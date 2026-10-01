"""Guillotine（贯通式）矩形排样搜索。

两种模式：

1. 旧语义（单规格母纸，``solve(...)`` 不传 ``stocks``）：
   角点放置 + 两类首刀归一形式（V-first / H-first）的带记忆 DFS，
   找到任意可行方案即返回（不追求最优）。状态：
   - found      找到可行方案（找到即返回，不追求最优）
   - infeasible 已完整穷尽，判定无解
   - budget     搜索状态数预算耗尽，不能判定无解

2. 库存模式（传入 ``stocks``，多规格母纸）：
   每种规格记录长宽、可用张数与非负整数单张成本。在贯通式排样约束、
   旋转限制、各规格库存上限与总张数上限内，搜索总纸价最低的可行排样；
   同价先比较用纸张数（少者优先），再优先使用录入靠前的规格
   （用量向量按规格下标字典序靠前优先）。状态：
   - optimal     完整穷尽，所给方案成本最优（平局亦按上述次序决胜）
   - found       已有可行方案，但预算耗尽，最优性未证实
   - infeasible  完整穷尽，确认无解
   - budget      预算耗尽且未找到任何可行方案（未判定，不表示无解）
"""

from __future__ import annotations

import copy
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

sys.setrecursionlimit(200_000)


class BudgetExhausted(Exception):
    """搜索状态数超过上限。"""


@dataclass
class Node:
    """裁切树节点（DFS 回溯时就地增删/改写）。"""

    nid: int
    region: Tuple[int, int, int, int]   # (x, y, w, h)，母纸左上角坐标系
    cut: Optional[str] = None           # None(叶子) / 'V' / 'H'
    at: int = 0                         # 刀线相对 region 原点的偏移
    children: List[int] = field(default_factory=list)
    item: Optional[int] = None          # 成品叶子：类型下标；否则 None
    rotated: bool = False
    is_free: bool = False               # 尚未处理的自由余料叶子
    is_waste: bool = False              # 已作废余料叶子


@dataclass
class SolveResult:
    status: str
    states_searched: int
    sheets_used: int = 0
    pieces: List[dict] = field(default_factory=list)
    cuts: List[dict] = field(default_factory=list)
    counts: List[int] = field(default_factory=list)
    # 库存模式专用字段（旧模式下保持默认值，语义不变）
    optimal: bool = False               # 成本最优性是否已证实
    total_cost: int = 0
    # 每张已使用母纸的规格下标（长度 = sheets_used）；旧模式为空
    sheet_specs: List[int] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "status": self.status,
            "states_searched": self.states_searched,
            "sheets_used": self.sheets_used,
            "pieces": self.pieces,
            "cuts": self.cuts,
            "counts": self.counts,
            "optimal": self.optimal,
            "total_cost": self.total_cost,
            "sheet_specs": self.sheet_specs,
        }


def solve(paper_l: int, paper_w: int, kerf: int, max_sheets: int,
          max_states: int, products: List[dict],
          stocks: Optional[List[dict]] = None) -> dict:
    """products: [{"l","w","qty","rotatable"}]，列表下标即成品类型 id。

    stocks 缺省/为空 => 旧单规格语义（paper_l×paper_w，库存不限，
    找到即返回）。否则 stocks=[{"l","w","qty","cost"}] 为多规格库存，
    启用成本最优搜索（qty 为该规格可用张数，cost 为非负整数单张成本）。
    """
    types = [(int(p["l"]), int(p["w"]), bool(p["rotatable"])) for p in products]
    demand = [int(p["qty"]) for p in products]

    if stocks:
        stock_list = [(int(s["l"]), int(s["w"]), int(s["qty"]),
                       int(s["cost"])) for s in stocks]
        return _solve_stock(stock_list, kerf, max_sheets, max_states,
                            types, demand)

    # 快速预检 1：任一成品任何允许姿态都放不进母纸 => 无解。
    for i, (l, w, rot) in enumerate(types):
        fits = (l <= paper_l and w <= paper_w) or (
            rot and w <= paper_l and l <= paper_w)
        if not fits:
            return SolveResult("infeasible", 0, counts=[0] * len(types)).to_json()

    # 快速预检 2：成品总面积（刀缝只会让情况更差）超过母纸总面积 => 无解。
    total_area = sum(l * w * demand[i] for i, (l, w, _) in enumerate(types))
    if total_area > max_sheets * paper_l * paper_w:
        return SolveResult("infeasible", 0, counts=[0] * len(types)).to_json()

    planner = _Planner(paper_l, paper_w, kerf, max_sheets, max_states,
                       types, demand)
    return planner.run().to_json()


# 自由余料元组：(x, y, w, h, nid)
Free = Tuple[int, int, int, int, int]


class _Planner:
    def __init__(self, paper_l, paper_w, kerf, max_sheets, max_states,
                 types, demand):
        self.L, self.W = paper_l, paper_w
        self.kerf = kerf
        self.max_sheets = max_sheets
        self.max_states = max_states
        self.types = types
        self.demand0 = demand
        self.n_types = len(types)

        self.nodes: Dict[int, Node] = {}
        self._next_nid = 0
        self.roots: List[int] = []
        self.states = 0
        self.memo: set = set()

    # ---------- 节点工具 ----------
    def _alloc(self) -> int:
        nid = self._next_nid
        self._next_nid += 1
        return nid

    def _free_leaf(self, box) -> int:
        nid = self._alloc()
        self.nodes[nid] = Node(nid, box, is_free=True)
        return nid

    # ---------- 主流程 ----------
    def run(self) -> SolveResult:
        root = self._free_leaf((0, 0, self.L, self.W))
        self.roots.append(root)
        frees = [(0, 0, self.L, self.W, root)]
        try:
            ok = self._dfs(frees, tuple(self.demand0))
        except BudgetExhausted:
            return SolveResult("budget", self.states,
                               counts=[0] * self.n_types)
        if not ok:
            return SolveResult("infeasible", self.states,
                               counts=[0] * self.n_types)
        return self._collect()

    # ---------- DFS ----------
    def _dfs(self, frees: List[Free], dem: tuple) -> bool:
        if sum(dem) == 0:
            return True

        added_root = None
        if not frees:
            # 已启用母纸用尽：在张数上限内再启用一张。
            if len(self.roots) >= self.max_sheets:
                return False
            nid = self._free_leaf((0, 0, self.L, self.W))
            self.roots.append(nid)
            added_root = nid
            frees = [(0, 0, self.L, self.W, nid)]

        # 固定选取最大余料；其余尺寸相同的余料对称。
        frees = sorted(frees, key=lambda f: (-f[2] * f[3], -f[2], -f[3], f[4]))
        # 记忆键含已启用母纸数：剩余「加纸额度」不同的状态不能合并。
        key = (tuple(sorted((w, h) for _, _, w, h, _ in frees)), dem,
               len(self.roots))
        if key in self.memo:
            self._rollback_added(added_root)
            return False
        self.states += 1
        if self.states > self.max_states:
            raise BudgetExhausted()
        self.memo.add(key)

        x, y, w, h, nid = frees[0]
        rest = frees[1:]

        # 面积剪枝：剩余成品面积超过「当前余料 + 尚可启用母纸」总面积 => 必败。
        need_area = sum(t[0] * t[1] * dem[i]
                        for i, t in enumerate(self.types))
        free_area = sum(f[2] * f[3] for f in frees)
        spare_sheets = self.max_sheets - len(self.roots)
        spare_area = spare_sheets * self.L * self.W
        if need_area > free_area + spare_area:
            self._rollback_added(added_root)
            return False

        # 容纳性剪枝：每个仍有需求的成品，必须存在某个当前余料或一张空白
        # 母纸能以其允许姿态整体放下；否则必败。
        if spare_sheets > 0:
            candidates = frees + [(0, 0, self.L, self.W, -1)]
        else:
            candidates = frees
        for i, (il, iw, rot) in enumerate(self.types):
            if dem[i] <= 0:
                continue
            fits_any = any(
                (il <= cw and iw <= ch)
                or (rot and iw <= cw and il <= ch)
                for _, _, cw, ch, _ in candidates)
            if not fits_any:
                self._rollback_added(added_root)
                return False

        # 成品类型按面积降序尝试。
        for ti in sorted((i for i in range(self.n_types) if dem[i] > 0),
                         key=lambda i: (-self.types[i][0] * self.types[i][1],
                                        -self.types[i][0], i)):
            il, iw, rotatable = self.types[ti]
            orients = [(il, iw, False)]
            if rotatable and (iw, il) != (il, iw):
                orients.append((iw, il, True))
            for pw, ph, rotated in orients:
                if pw > w or ph > h:
                    continue
                for plan in self._plans(w, h, pw, ph):
                    snapshot = self._next_nid
                    new_frees = self._build(nid, (x, y, w, h), plan,
                                            ti, rotated, pw, ph)
                    new_dem = list(dem)
                    new_dem[ti] -= 1
                    if self._dfs(rest + new_frees, tuple(new_dem)):
                        return True
                    self._teardown(nid, (x, y, w, h), snapshot)

        # 余料作废分支：该余料一张成品也不放（完备性所需）。
        node = self.nodes[nid]
        node.is_free = False
        node.is_waste = True
        if self._dfs(rest, dem):
            return True
        node.is_free = True
        node.is_waste = False

        self._rollback_added(added_root)
        return False

    def _rollback_added(self, added_root):
        if added_root is not None:
            self.roots.pop()
            self.nodes.pop(added_root, None)

    # ---------- 候选裁切形式 ----------
    def _plans(self, w, h, pw, ph):
        """把成品 P 放在余料左下角后，枚举 guillotine 首刀的全部归一形式。

        右净宽 rw = w-pw-kerf（>0 才是可再利用余料），上净高 th 同理。
        0 < 空档 <= kerf 时无法在该空档再下刀；若刀在余料外缘，刀缝可越出
        纸边（边缘修切），该侧成为废边。

        完备形式（切出左下角 P 的首刀必贴 P 边缘，归一化）：
          none  P 与余料严格等宽等高
          V     竖切，左=P（要求严格等高 h==ph），右整条自由(rw>0)
          H     横切，下=P（要求严格等宽 w==pw），上整条自由(th>0)
          VH    先竖切（右整条 rw>0 或外缘修切），再在 pw 宽左条横切
                出 P / 上条（th>0）
          HV    先横切（上整条 th>0 或外缘修切），再在 ph 高下条竖切
                出 P / 右侧条（rw>0）
        """
        k = self.kerf
        rw = w - pw - k
        th = h - ph - k
        exact_w = (w == pw)
        exact_h = (h == ph)
        plans = []
        if exact_w and exact_h:
            plans.append(("none",))
            return plans
        if rw > 0 and exact_h:
            plans.append(("V",))
        if exact_w and th > 0:
            plans.append(("H",))
        if th > 0:
            # 右条可能为外缘废边（rw<=0）
            plans.append(("VH",))
        if rw > 0:
            # 下条之后的上侧可能为外缘废边（th<=0）
            plans.append(("HV",))
        return plans

    # ---------- 裁切树构建/回溯 ----------
    def _build(self, nid, region, plan, ti, rotated, pw, ph):
        """展开自由叶子 nid；返回新产生的自由余料元组列表。

        所有坐标均为净料坐标；刀缝起点偏移 = 成品边位置，第二块净料从
        偏移+kerf 开始。第二块净宽/净高 <= 0 时为外缘修切，对应子节点
        是退化废边叶子（不下刀、不出成品）。
        """
        x, y, w, h = region
        node = self.nodes[nid]
        node.is_free = False
        k = self.kerf

        kind = plan[0]
        if kind == "none":
            node.item, node.rotated = ti, rotated
            return []

        leaf = self._alloc()
        self.nodes[leaf] = Node(leaf, (x, y, pw, ph), item=ti, rotated=rotated)

        def make_free(box):
            bx, by, bw_, bh_ = box
            fn = self._free_leaf(box)
            return (bx, by, bw_, bh_, fn)

        def make_waste(box):
            fn = self._alloc()
            self.nodes[fn] = Node(fn, box, is_free=False, is_waste=True)
            return fn

        if kind == "V":
            # h==ph（严格等高）；左=成品，右整条自由（rw>0）。
            at = pw
            right = make_free((x + at + k, y, w - at - k, h))
            node.cut, node.at, node.children = "V", at, [leaf, right[4]]
            return [right]

        if kind == "H":
            # w==pw（严格等宽）；下=成品，上整条自由（th>0）。
            at = ph
            top = make_free((x, y + at + k, w, h - at - k))
            node.cut, node.at, node.children = "H", at, [leaf, top[4]]
            return [top]

        if kind == "VH":
            # 先竖切：左条 pw 宽；右为自由条(rw>0)或外缘废边。
            # 再在左条横切：下=P，上自由(th>0)。
            right_w = w - pw - k
            if right_w > 0:
                right = make_free((x + pw + k, y, right_w, h))
                right_nid = right[4]
                new_frees = [right]
            else:
                right_nid = make_waste((x + pw + k, y, 0, h))
                new_frees = []
            top = make_free((x, y + ph + k, pw, h - ph - k))
            inner = self._alloc()
            self.nodes[inner] = Node(inner, (x, y, pw, h),
                                     cut="H", at=ph,
                                     children=[leaf, top[4]])
            node.cut, node.at, node.children = "V", pw, [inner, right_nid]
            return [top] + new_frees

        # HV：先横切（下条 ph 高，上自由或外缘废边），下条再竖切。
        top_h = h - ph - k
        if top_h > 0:
            below = make_free((x, y + ph + k, w, top_h))
            below_nid = below[4]
            new_frees = [below]
        else:
            below_nid = make_waste((x, y + ph + k, w, 0))
            new_frees = []
        side_w = w - pw - k
        if side_w > 0:
            side = make_free((x + pw + k, y, side_w, ph))
            side_nid = side[4]
            new_frees.append(side)
        else:
            side_nid = make_waste((x + pw + k, y, 0, ph))
        inner = self._alloc()
        self.nodes[inner] = Node(inner, (x, y, w, ph),
                                 cut="V", at=pw,
                                 children=[leaf, side_nid])
        node.cut, node.at, node.children = "H", ph, [inner, below_nid]
        return new_frees

    def _teardown(self, nid, region, snapshot):
        """删除本步新建的所有节点，把 nid 还原为自由余料叶子。"""
        for dead in range(snapshot, self._next_nid):
            self.nodes.pop(dead, None)
        self._next_nid = snapshot
        node = self.nodes[nid]
        node.cut, node.at, node.children = None, 0, []
        node.item, node.rotated = None, False
        node.is_free, node.is_waste = True, False
        node.region = region

    # ---------- 成功后收集排样与裁切步骤 ----------
    def _collect(self) -> SolveResult:
        pieces, cuts = [], []
        counts = [0] * self.n_types
        used = 0

        for si, rid in enumerate(self.roots):
            root = self.nodes[rid]
            # 从未动用的母纸：自由叶子且仍是完整母纸尺寸。
            if root.cut is None and root.item is None and root.is_free:
                continue
            used += 1
            step = 0
            # 深度优先前序遍历。执行顺序为「先完整切完第一子区域（含成品的
            # 一侧），再切第二子区域（兄弟余料）」：第一刀把当前余料分开后，
            # 第一子区域的后续刀只贯穿第一子区域（兄弟区域的存在不影响），
            # 其全部刀完成后再对兄弟余料下刀。
            stack = [(rid, 0, 0, self.L, self.W)]
            while stack:
                nid, bx, by, bw, bh = stack.pop()
                n = self.nodes[nid]
                if n.cut == "V":
                    cuts.append({
                        "sheet": si, "order": step,
                        "orientation": "V",
                        "x": bx + n.at, "y": by, "length": bh,
                    })
                    step += 1
                    first_w = n.at                    # 刀缝起点前净宽
                    second_x = bx + n.at + self.kerf
                    second_w = bw - n.at - self.kerf
                    first, second = n.children
                    if second_w > 0:
                        stack.append((second, second_x, by, second_w, bh))
                    stack.append((first, bx, by, first_w, bh))
                elif n.cut == "H":
                    cuts.append({
                        "sheet": si, "order": step,
                        "orientation": "H",
                        "x": bx, "y": by + n.at, "length": bw,
                    })
                    step += 1
                    first_h = n.at
                    second_y = by + n.at + self.kerf
                    second_h = bh - n.at - self.kerf
                    first, second = n.children
                    if second_h > 0:
                        stack.append((second, bx, second_y, bw, second_h))
                    stack.append((first, bx, by, bw, first_h))
                elif n.item is not None:
                    px, py, pw_, ph_ = n.region
                    counts[n.item] += 1
                    pieces.append({
                        "sheet": si, "x": px, "y": py,
                        "w": pw_, "h": ph_,
                        "product": n.item, "rotated": n.rotated,
                    })
                # 其余：作废余料，忽略。

        # 裁切步骤全局排序：按张、再按张内顺序。
        cuts.sort(key=lambda c: (c["sheet"], c["order"]))
        for i, c in enumerate(cuts):
            c["seq"] = i + 1
        return SolveResult("found", self.states, used, pieces, cuts, counts)


# ====================================================================
# 库存模式：多规格母纸 + 成本最优的分支限界搜索
# ====================================================================

def _solve_stock(stocks, kerf, max_sheets, max_states, types, demand):
    """stocks: [(l, w, qty_available, cost_per_sheet), ...]（录入顺序）。"""
    n_specs = len(stocks)
    zero_counts = [0] * len(types)

    # 快速预检 1：任一成品任何允许姿态都放不进任一（有库存的）规格 => 无解。
    for il, iw, rot in types:
        fits_any = False
        for sl, sw, qty, _cost in stocks:
            if qty <= 0:
                continue
            if (il <= sl and iw <= sw) or (rot and iw <= sl and il <= sw):
                fits_any = True
                break
        if not fits_any:
            return SolveResult("infeasible", 0, counts=zero_counts).to_json()

    # 快速预检 2：总面积超过「库存总面积 ∩ 张数上限」=> 无解。
    total_area = sum(l * w * demand[i] for i, (l, w, _) in enumerate(types))
    sheet_areas: List[int] = []
    for sl, sw, qty, _cost in stocks:
        sheet_areas.extend([sl * sw] * min(qty, max_sheets))
    cap_area = sum(sorted(sheet_areas, reverse=True)[:max_sheets])
    if total_area > cap_area:
        return SolveResult("infeasible", 0, counts=zero_counts).to_json()

    planner = _StockPlanner(stocks, kerf, max_sheets, max_states,
                            types, demand)
    return planner.run().to_json()


class _StockPlanner(_Planner):
    """多规格库存的成本最优搜索。

    复用单规格规划器的余料归一形式（_plans）与裁切树构建/回溯
    （_build/_teardown），但启用母纸时可选择规格，并用分支限界寻找：
      最优目标 (总成本, 总用纸张数, 用量向量按规格下标的字典序)
    其中字典序（前下标用得多者优先）对应「优先使用录入靠前的规格」。

    归一化：第 j+1 张启用的规格下标不小于第 j 张（非降序）。任意规格
    多重集合都可以唯一地按此顺序枚举，因此不遗漏可行排样，也不重复
    枚举同规格纸张的排列。

    实现要点：
    - 「开新纸」是同一决策状态下的规格分支，不计入状态数（与旧规划器
      对单一规格续纸的处理一致）；只有展开某个余料（放置/作废）才计数，
      从而 max_states 语义在两种模式下一致且终止有保证。
    - 带记忆 DFS 的键含 (余料尺寸多重集, 剩余需求, 各规格用量)。
    """

    def __init__(self, stocks, kerf, max_sheets, max_states, types, demand):
        super().__init__(stocks[0][0], stocks[0][1], kerf, max_sheets,
                         max_states, types, demand)
        self.stocks = stocks          # [(l,w,qty,cost)]
        self.n_specs = len(stocks)
        self.usage = [0] * self.n_specs
        self.root_specs: List[int] = []   # 与 self.roots 平行的规格下标
        self.best_obj: Optional[Tuple] = None
        self.best_snapshot: Optional[dict] = None

        # 静态预计算（搜索中不变）。
        self._s_l = [s[0] for s in stocks]
        self._s_w = [s[1] for s in stocks]
        self._s_area = [s[0] * s[1] for s in stocks]
        self._s_cost = [s[3] for s in stocks]
        self._s_qty = [s[2] for s in stocks]
        self._t_area = [t[0] * t[1] for t in types]
        # 仍有需求时成品的尝试顺序（面积降序）：按需求向量动态过滤。
        self._t_order_all = sorted(
            range(self.n_types),
            key=lambda i: (-self._t_area[i], -types[i][0], i))
        # cap[si][ti]：规格 si 一张纸最多能放成品 ti 的件数（含旋转姿态）。
        self._cap = []
        for si, sl in enumerate(self._s_l):
            sw = self._s_w[si]
            row = []
            for il, iw, rot in types:
                n0 = (sl // il) * (sw // iw)
                if rot:
                    n0 = max(n0, (sl // iw) * (sw // il))
                row.append(n0)
            self._cap.append(row)

    # ---------- 主流程 ----------
    def run(self) -> SolveResult:
        try:
            self._enter_sheet([], tuple(self.demand0))
        except BudgetExhausted:
            if self.best_snapshot is None:
                return SolveResult("budget", self.states,
                                   counts=[0] * self.n_types)
            return self._collect_best(optimal=False)
        if self.best_snapshot is None:
            return SolveResult("infeasible", self.states,
                               counts=[0] * self.n_types)
        return self._collect_best(optimal=True)

    def _current_obj(self):
        """(总成本, 总张数, 用量向量取负的元组) 最小 => 要求的优先序。"""
        total_cost = 0
        for i in range(self.n_specs):
            total_cost += self._s_cost[i] * self.usage[i]
        return (total_cost, sum(self.usage),
                tuple(-u for u in self.usage))

    # ---------- 分支限界 DFS ----------
    def _enter_sheet(self, frees: List[Free], dem: tuple) -> None:
        """frees 为「已启用纸张上」的剩余自由余料；为空时枚举下一规格。

        开新纸只是在同一决策点上选择规格（非降序归一），不单独计数；
        每张新纸进入 _place 展开其完整余料。
        """
        if sum(dem) == 0:
            # 需求已满足：残留余料一律可作废，直接记录完整可行排样。
            obj = self._current_obj()
            if self.best_obj is None or obj < self.best_obj:
                self.best_obj = obj
                self.best_snapshot = {
                    "nodes": copy.deepcopy(self.nodes),
                    "roots": list(self.roots),
                    "root_specs": list(self.root_specs),
                    "usage": list(self.usage),
                }
            return

        if frees:
            # 固定先处理最大余料；同尺寸余料对称。
            # 同一次排序既用于选最大余料，也用于记忆键（尺寸多重集）。
            frees = sorted(frees,
                           key=lambda f: (-f[2] * f[3], -f[2], -f[3], f[4]))
            signature = tuple((f[2], f[3]) for f in frees)
            self._place(frees, dem, signature)
            return

        lo = self.root_specs[-1] if self.root_specs else 0
        if sum(self.usage) >= self.max_sheets:
            return
        for si in range(lo, self.n_specs):
            if self.usage[si] >= self._s_qty[si]:
                continue
            sl, sw = self._s_l[si], self._s_w[si]
            nid = self._free_leaf((0, 0, sl, sw))
            self.roots.append(nid)
            self.root_specs.append(si)
            self.usage[si] += 1
            self._place([(0, 0, sl, sw, nid)], dem, ((sl, sw),))
            self.usage[si] -= 1
            self.root_specs.pop()
            self.roots.pop()
            self.nodes.pop(nid, None)

    def _place(self, frees: List[Free], dem: tuple,
               signature: Tuple[Tuple[int, int], ...]) -> None:
        """展开最大余料 frees[0]：记忆、剪枝、放置分支、作废分支。

        signature 为 frees 的 (宽, 高) 元组（按面积降序，等价于尺寸
        多重集的规范表示），由调用方随 frees 一起维护，避免每状态重排。
        """
        key = (signature, dem, tuple(self.usage))
        if key in self.memo:
            return
        self.states += 1
        if self.states > self.max_states:
            raise BudgetExhausted()
        self.memo.add(key)

        x, y, w, h, nid = frees[0]
        rest = frees[1:]
        lo = self.root_specs[-1]
        used_sheets = sum(self.usage)
        remain_slots = self.max_sheets - used_sheets

        need_area = 0
        for i in range(self.n_types):
            if dem[i]:
                need_area += self._t_area[i] * dem[i]
        free_area = 0
        for f in frees:
            free_area += f[2] * f[3]

        # 面积剪枝（各规格剩余库存 ∩ 总张数上限，取面积最大的若干张）。
        if need_area > free_area:
            spare = 0
            slots = remain_slots
            by_area = sorted(
                (si for si in range(lo, self.n_specs)
                 if self._s_qty[si] - self.usage[si] > 0),
                key=lambda si: -self._s_area[si])
            for si in by_area:
                take = min(self._s_qty[si] - self.usage[si], slots)
                spare += take * self._s_area[si]
                slots -= take
                if slots == 0:
                    break
            if need_area > free_area + spare:
                return

        # 容纳性剪枝：每个仍有需求的成品必须能放进某余料，或某张尚可
        # 启用的空白母纸。
        boxes = [(f[2], f[3]) for f in frees]
        if remain_slots > 0:
            for si in range(lo, self.n_specs):
                if self.usage[si] < self._s_qty[si]:
                    boxes.append((self._s_l[si], self._s_w[si]))
        for ti in range(self.n_types):
            if dem[ti] <= 0:
                continue
            il, iw, rot = self.types[ti]
            ok = False
            for cw, ch in boxes:
                if (il <= cw and iw <= ch) or (
                        rot and iw <= cw and il <= ch):
                    ok = True
                    break
            if not ok:
                return

        # 分支限界下界：剩余需求必须再开至少 lb_sheets 张纸（同时用面积
        # 与「每张纸可放件数」做乐观估计，取大者），每张至少还要花
        # cheapest 的成本。下界对成本与张数都乐观，因此剪枝安全。
        need_more = max(0, need_area - free_area)
        max_area = 1
        cheapest = None
        slots = remain_slots
        available = []   # 尚可启用的规格下标
        for si in range(lo, self.n_specs):
            left = self._s_qty[si] - self.usage[si]
            if left <= 0:
                continue
            available.append(si)
            if self._s_area[si] > max_area:
                max_area = self._s_area[si]
            c = self._s_cost[si]
            if cheapest is None or c < cheapest:
                cheapest = c
        lb_area = ((need_more + max_area - 1) // max_area
                   if need_more > 0 and available else 0)
        # 件数下界：对每种成品，取任一可用规格（或当前余料）一张最多
        # 可放件数，合计剩余件数所需的最少额外张数（分品种各自乐观）。
        lb_pieces = 0
        for ti in range(self.n_types):
            q = dem[ti]
            if q <= 0:
                continue
            il, iw, rot = self.types[ti]
            # 当前余料（含本决策正在处理的最大余料）的总容量。
            per_free = 0
            for _fx, _fy, fw, fh, _fn in frees:
                n0 = (fw // il) * (fh // iw)
                if rot:
                    n0 = max(n0, (fw // iw) * (fh // il))
                per_free += n0
            q_left = q - per_free
            if q_left <= 0:
                continue
            per_sheet = 0
            for si in available:
                n0 = self._cap[si][ti]
                if n0 > per_sheet:
                    per_sheet = n0
            if per_sheet == 0:
                # 新纸也放不下该成品：由容纳性剪枝处理/必败。
                lb_pieces = slots + 1
                break
            lb_pieces = max(lb_pieces,
                            (q_left + per_sheet - 1) // per_sheet)
        lb_sheets = max(lb_area, lb_pieces, 1 if need_more > 0 else 0)
        if lb_sheets > slots:
            return
        if self.best_obj is not None and cheapest is not None:
            cur_cost = 0
            for i in range(self.n_specs):
                cur_cost += self._s_cost[i] * self.usage[i]
            lb_cost = cur_cost + cheapest * lb_sheets
            if lb_cost > self.best_obj[0] or (
                    lb_cost == self.best_obj[0]
                    and used_sheets + lb_sheets > self.best_obj[1]):
                return

        # 放置分支：成品类型按面积降序尝试。
        new_dem = list(dem)
        for ti in self._t_order_all:
            if dem[ti] <= 0:
                continue
            il, iw, rotatable = self.types[ti]
            orients = [(il, iw, False)]
            if rotatable and (iw, il) != (il, iw):
                orients.append((iw, il, True))
            for pw, ph, rotated in orients:
                if pw > w or ph > h:
                    continue
                for plan in self._plans(w, h, pw, ph):
                    snapshot = self._next_nid
                    new_frees = self._build(nid, (x, y, w, h), plan,
                                            ti, rotated, pw, ph)
                    new_dem[ti] -= 1
                    self._enter_sheet(rest + new_frees, tuple(new_dem))
                    self._teardown(nid, (x, y, w, h), snapshot)
                    new_dem[ti] += 1

        # 余料作废分支（完备性所需）。
        node = self.nodes[nid]
        node.is_free = False
        node.is_waste = True
        self._enter_sheet(rest, dem)
        node.is_free = True
        node.is_waste = False

    # ---------- 成功后收集 ----------
    def _collect_best(self, optimal: bool) -> SolveResult:
        saved = self.best_snapshot
        nodes = saved["nodes"]
        roots = saved["roots"]
        root_specs = saved["root_specs"]
        usage = saved["usage"]
        kerf = self.kerf

        pieces: List[dict] = []
        cuts: List[dict] = []
        counts = [0] * self.n_types
        used = 0

        for rid, spec_idx in zip(roots, root_specs):
            root = nodes[rid]
            # 从未动用的母纸：自由叶子且仍是完整规格尺寸。
            sl, sw = self.stocks[spec_idx][0], self.stocks[spec_idx][1]
            if (root.cut is None and root.item is None and root.is_free
                    and root.region == (0, 0, sl, sw)):
                continue
            sheet_no = used
            used += 1
            step = 0
            stack = [(rid, 0, 0, sl, sw)]
            while stack:
                nid, bx, by, bw, bh = stack.pop()
                n = nodes[nid]
                if n.cut == "V":
                    cuts.append({
                        "sheet": sheet_no, "order": step,
                        "orientation": "V",
                        "x": bx + n.at, "y": by, "length": bh,
                    })
                    step += 1
                    first_w = n.at
                    second_x = bx + n.at + kerf
                    second_w = bw - n.at - kerf
                    first, second = n.children
                    if second_w > 0:
                        stack.append((second, second_x, by, second_w, bh))
                    stack.append((first, bx, by, first_w, bh))
                elif n.cut == "H":
                    cuts.append({
                        "sheet": sheet_no, "order": step,
                        "orientation": "H",
                        "x": bx, "y": by + n.at, "length": bw,
                    })
                    step += 1
                    first_h = n.at
                    second_y = by + n.at + kerf
                    second_h = bh - n.at - kerf
                    first, second = n.children
                    if second_h > 0:
                        stack.append((second, bx, second_y, bw, second_h))
                    stack.append((first, bx, by, bw, first_h))
                elif n.item is not None:
                    px, py, pw_, ph_ = n.region
                    counts[n.item] += 1
                    pieces.append({
                        "sheet": sheet_no, "x": px, "y": py,
                        "w": pw_, "h": ph_,
                        "product": n.item, "rotated": n.rotated,
                    })

        cuts.sort(key=lambda c: (c["sheet"], c["order"]))
        for i, c in enumerate(cuts):
            c["seq"] = i + 1
        total_cost = sum(self.stocks[i][3] * usage[i]
                         for i in range(self.n_specs))
        used_specs = []
        for rid, spec_idx in zip(roots, root_specs):
            root = nodes[rid]
            sl, sw = self.stocks[spec_idx][0], self.stocks[spec_idx][1]
            if (root.cut is None and root.item is None and root.is_free
                    and root.region == (0, 0, sl, sw)):
                continue
            used_specs.append(spec_idx)

        status = "optimal" if optimal else "found"
        return SolveResult(status, self.states, used, pieces, cuts, counts,
                           optimal=optimal, total_cost=total_cost,
                           sheet_specs=used_specs)
