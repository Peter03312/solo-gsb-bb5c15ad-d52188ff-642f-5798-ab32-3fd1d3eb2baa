"""Guillotine（贯通式）矩形排样搜索。

业务约束：
- 每一刀必须贯穿当前正在处理的矩形余料；
- 刀缝宽度 kerf 从余料中扣除；
- 成品不得重叠、越界；旋转须符合该类成品的 rotatable 声明。

两种入口：
- solve(...)           单规格母纸（历史语义）：找到任意可行方案即返回，
                       状态 found / infeasible / budget。
- solve_inventory(...) 多规格库存：带 incumbent 的分支限界 DFS，完整穷尽后
                       返回总纸价最低的贯通式排样；同价先比用纸张数，再比
                       规格录入顺序（靠前的优先）。状态：
                         optimal    已完整穷尽，证实最优
                         infeasible 已完整穷尽，判定无解
                         budget     预算耗尽；若已得可行方案则一并返回，
                                    并显式标记最优性未证实（optimal=False）

两类搜索共用同一套「角点放置 + 首刀归一化（V/H/VH/HV）+ 余料作废」枚举，
因此预算内完整遍历给出的 infeasible / optimal 是可信的。
"""

from __future__ import annotations

import bisect
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

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

    def to_json(self) -> dict:
        return {
            "status": self.status,
            "states_searched": self.states_searched,
            "sheets_used": self.sheets_used,
            "pieces": self.pieces,
            "cuts": self.cuts,
            "counts": self.counts,
        }


# 自由余料元组：(x, y, w, h, nid)
Free = Tuple[int, int, int, int, int]


# =====================================================================
# 共享几何枚举：首刀归一化形式、裁切树展开/回退、成品落位收集
# =====================================================================

def plans_for(w: int, h: int, pw: int, ph: int, kerf: int) -> List[tuple]:
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
    k = kerf
    rw = w - pw - k
    th = h - ph - k
    exact_w = (w == pw)
    exact_h = (h == ph)
    plans: List[tuple] = []
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


def expand(nodes: Dict[int, Node], alloc: Callable[[], int], kerf: int,
           nid: int, region: Tuple[int, int, int, int], plan: tuple,
           ti: int, rotated: bool, pw: int, ph: int) -> List[Free]:
    """展开自由叶子 nid；返回新产生的自由余料元组列表。

    所有坐标均为净料坐标；刀缝起点偏移 = 成品边位置，第二块净料从
    偏移+kerf 开始。第二块净宽/净高 <= 0 时为外缘修切，对应子节点
    是退化废边叶子（不下刀、不出成品）。
    """
    x, y, w, h = region
    node = nodes[nid]
    node.is_free = False
    k = kerf

    kind = plan[0]
    if kind == "none":
        node.item, node.rotated = ti, rotated
        return []

    leaf = alloc()
    nodes[leaf] = Node(leaf, (x, y, pw, ph), item=ti, rotated=rotated)

    def make_free(box):
        bx, by, bw_, bh_ = box
        fn = alloc()
        nodes[fn] = Node(fn, box, is_free=True)
        return (bx, by, bw_, bh_, fn)

    def make_waste(box):
        fn = alloc()
        nodes[fn] = Node(fn, box, is_free=False, is_waste=True)
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
        inner = alloc()
        nodes[inner] = Node(inner, (x, y, pw, h),
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
    inner = alloc()
    nodes[inner] = Node(inner, (x, y, w, ph),
                        cut="V", at=pw,
                        children=[leaf, side_nid])
    node.cut, node.at, node.children = "H", ph, [inner, below_nid]
    return new_frees


def restore(nodes: Dict[int, Node], nid: int,
            region: Tuple[int, int, int, int], id_lo: int,
            id_hi: int) -> None:
    """删除 [id_lo, id_hi) 内新建节点，把 nid 还原为 region 上的自由叶子。"""
    for dead in range(id_lo, id_hi):
        nodes.pop(dead, None)
    node = nodes[nid]
    node.cut, node.at, node.children = None, 0, []
    node.item, node.rotated = None, False
    node.is_free, node.is_waste = True, False
    node.region = region


def walk_sheet(nodes: Dict[int, Node], rid: int, L: int, W: int,
               kerf: int, si: int, spec: Optional[int],
               pieces: List[dict], cuts: List[dict],
               counts: List[int]) -> None:
    """遍历一张母纸的裁切树，收集成品落位与按执行顺序排列的裁切步骤。

    深度优先前序遍历。执行顺序为「先完整切完第一子区域（含成品的
    一侧），再切第二子区域（兄弟余料）」：第一刀把当前余料分开后，
    第一子区域的后续刀只贯穿第一子区域（兄弟区域的存在不影响），
    其全部刀完成后再对兄弟余料下刀。
    """
    step = 0
    stack = [(rid, 0, 0, L, W)]
    while stack:
        nid, bx, by, bw, bh = stack.pop()
        n = nodes[nid]
        if n.cut == "V":
            cuts.append({
                "sheet": si, "order": step,
                "orientation": "V",
                "x": bx + n.at, "y": by, "length": bh,
                **({"spec": spec} if spec is not None else {}),
            })
            step += 1
            first_w = n.at                    # 刀缝起点前净宽
            second_x = bx + n.at + kerf
            second_w = bw - n.at - kerf
            first, second = n.children
            if second_w > 0:
                stack.append((second, second_x, by, second_w, bh))
            stack.append((first, bx, by, first_w, bh))
        elif n.cut == "H":
            cuts.append({
                "sheet": si, "order": step,
                "orientation": "H",
                "x": bx, "y": by + n.at, "length": bw,
                **({"spec": spec} if spec is not None else {}),
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
            piece = {
                "sheet": si, "x": px, "y": py,
                "w": pw_, "h": ph_,
                "product": n.item, "rotated": n.rotated,
            }
            if spec is not None:
                piece["spec"] = spec
            pieces.append(piece)
        # 其余：作废余料，忽略。


# =====================================================================
# 单规格母纸（历史语义：找到任意可行方案即返回）
# =====================================================================

def solve(paper_l: int, paper_w: int, kerf: int, max_sheets: int,
          max_states: int, products: List[dict]) -> dict:
    """products: [{"l","w","qty","rotatable"}]，列表下标即成品类型 id。"""
    types = [(int(p["l"]), int(p["w"]), bool(p["rotatable"])) for p in products]
    demand = [int(p["qty"]) for p in products]

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
                for plan in plans_for(w, h, pw, ph, self.kerf):
                    snapshot = self._next_nid
                    new_frees = expand(self.nodes, self._alloc, self.kerf,
                                       nid, (x, y, w, h), plan,
                                       ti, rotated, pw, ph)
                    new_dem = list(dem)
                    new_dem[ti] -= 1
                    if self._dfs(rest + new_frees, tuple(new_dem)):
                        return True
                    restore(self.nodes, nid, (x, y, w, h),
                            snapshot, self._next_nid)
                    self._next_nid = snapshot

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
            walk_sheet(self.nodes, rid, self.L, self.W, self.kerf,
                       used - 1, None, pieces, cuts, counts)

        # 裁切步骤全局排序：按张、再按张内顺序。
        cuts.sort(key=lambda c: (c["sheet"], c["order"]))
        for i, c in enumerate(cuts):
            c["seq"] = i + 1
        return SolveResult("found", self.states, used, pieces, cuts, counts)


# =====================================================================
# 多规格库存：总纸价最低（同价比张数，再比规格录入顺序）
# =====================================================================

# 库存规格：(长, 宽, 可用张数, 单张成本)，元组顺序即录入顺序（0 基）。
Stock = Tuple[int, int, int, int]


def solve_inventory(stocks: List[Stock], kerf: int, max_sheets: int,
                    max_states: int, products: List[dict]) -> dict:
    """多规格库存排样。

    stocks 顺序即录入顺序，参与同价同张数时的次序优先比较。
    返回 dict：status=optimal/infeasible/budget，optimal 为 bool；
    budget 且已找到可行方案时附带该方案并置 optimal=False。
    """
    types = [(int(p["l"]), int(p["w"]), bool(p["rotatable"])) for p in products]
    demand = [int(p["qty"]) for p in products]
    specs = [(int(s[0]), int(s[1]), int(s[2]), int(s[3])) for s in stocks]

    def empty(status: str, states: int) -> dict:
        return {
            "status": status,
            "optimal": status == "optimal",
            "states_searched": states,
            "sheets_used": 0,
            "total_cost": 0,
            "counts": [0] * len(types),
            "pieces": [],
            "cuts": [],
            "sheets": [],
        }

    # 快速预检 1：任一成品必须存在某规格能以其允许姿态容纳。
    for il, iw, rot in types:
        if not any((il <= L and iw <= W) or (rot and iw <= L and il <= W)
                   for L, W, _, _ in specs):
            return empty("infeasible", 0)

    # 快速预检 2：成品总面积超过全部库存面积 => 无解。
    total_area = sum(t[0] * t[1] * demand[i]
                     for i, t in enumerate(types))
    stock_area = sum(L * W * qty for L, W, qty, _ in specs)
    if total_area > stock_area:
        return empty("infeasible", 0)

    planner = _InventoryPlanner(specs, kerf, max_sheets, max_states,
                                types, demand)
    return planner.run()


@dataclass
class _SheetCtx:
    """一张正在排样中的母纸：独立裁切树、自由余料与已落位成品计数。"""

    nid: int
    spec: int
    L: int
    W: int
    nodes: Dict[int, Node] = field(default_factory=dict)
    frees: List[Free] = field(default_factory=list)
    placed: int = 0
    _next: int = 0

    def alloc(self) -> int:
        nid = self._next
        self._next += 1
        return nid


class _InventoryPlanner:
    def __init__(self, specs, kerf, max_sheets, max_states, types, demand):
        self.specs = specs
        self.n_specs = len(specs)
        self.kerf = kerf
        self.max_sheets = max_sheets
        self.max_states = max_states
        self.types = types
        self.n_types = len(types)
        self.demand0 = demand

        # 开新纸时的尝试顺序：单价升序，同价按录入顺序。
        self.open_order = sorted(range(self.n_specs),
                                 key=lambda s: (specs[s][3], s))
        # 各规格对各成品类型是否存在允许姿态可容纳。
        self.spec_fits = [
            [(il <= L and iw <= W) or (rot and iw <= L and il <= W)
             for il, iw, rot in types]
            for L, W, _, _ in specs
        ]
        self.spec_areas = [L * W for L, W, _, _ in specs]
        # 成品类型按面积降序（与单规格搜索一致），只算一次。
        self.type_order = sorted(
            range(self.n_types),
            key=lambda i: (-types[i][0] * types[i][1], -types[i][0], i))
        self.type_areas = [t[0] * t[1] for t in types]
        self.type_orients = [
            ([(t[0], t[1], False)]
             + ([(t[1], t[0], True)]
                if t[2] and (t[1], t[0]) != (t[0], t[1]) else []))
            for t in types
        ]
        # 各规格按单价升序的可用索引（费用下界用）。
        self.cost_order = sorted(range(self.n_specs),
                                 key=lambda s: (specs[s][3], s))

        self.committed: List[_SheetCtx] = []   # 已排定、不再改动的母纸
        self.ctx: Optional[_SheetCtx] = None   # 当前正在排样的母纸
        self.used = [0] * self.n_specs
        self.states = 0
        self.memo: set = set()
        self.incumbent: Optional[dict] = None
        # 未来尚可启用的各空白纸面积多重集（随开纸/回溯维护），降序排列。
        self._caps: List[int] = []
        self._caps_prefix: List[int] = []
        self._reset_caps()

    @staticmethod
    def _free_sort_key(f: Free):
        return (-f[2] * f[3], -f[2], -f[3], f[4])

    def _refresh_caps(self) -> None:
        total = 0
        self._caps_prefix = []
        for a in self._caps:
            total += a
            self._caps_prefix.append(total)

    def _reset_caps(self) -> None:
        self._caps = []
        for L, W, qty, _ in self.specs:
            self._caps.extend([L * W] * qty)
        self._caps.sort(reverse=True)
        self._refresh_caps()

    def _pop_cap(self, area: int) -> None:
        # 从降序多重集中删除一张面积为 area 的纸。
        lo, hi = 0, len(self._caps)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._caps[mid] > area:
                lo = mid + 1
            else:
                hi = mid
        self._caps.pop(lo)
        self._refresh_caps()

    def _push_cap(self, area: int) -> None:
        # 降序插入。
        lo, hi = 0, len(self._caps)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._caps[mid] > area:
                lo = mid + 1
            else:
                hi = mid
        self._caps.insert(lo, area)
        self._refresh_caps()

    # ---------- 目标键：(总纸价, 总张数, 录入次序优先向量)，取最小 ----------
    def _early_tuple(self, used) -> tuple:
        # used[0] 越大越优先 => 取负号后整体字典序最小。
        return tuple(-used[s] for s in range(self.n_specs))

    def _cost(self, used) -> int:
        return sum(used[s] * self.specs[s][3]
                   for s in range(self.n_specs))

    def _key(self) -> tuple:
        return (self._cost(self.used), sum(self.used),
                self._early_tuple(self.used))

    # ---------- 主流程 ----------
    def run(self) -> dict:
        # 先做一次贪心搜索，尽快得到一份可行 incumbent，使完整搜索一开始
        # 就能以「纸价/张数/次序」三层界限剪枝；贪心结果只用于剪枝，
        # 最优性仍由随后的完整遍历保证。
        self._greedy_seed(tuple(self.demand0))
        # 贪心会改写 used/committed/caps；_collect 只读取快照数据，
        # 种子结束后恢复正式搜索所需的初始状态。
        self.committed = []
        self.ctx = None
        self.used = [0] * self.n_specs
        self.memo = set()
        self._reset_caps()
        try:
            self._open_state(tuple(self.demand0))
        except BudgetExhausted:
            status = "budget"
            if self.incumbent is None:
                return self._empty_result("budget")
            data = self.incumbent["data"]
        else:
            if self.incumbent is None:
                return self._empty_result("infeasible")
            status = "optimal"
            data = self.incumbent["data"]
        out = dict(data)
        out["status"] = status
        out["optimal"] = status == "optimal"
        # 状态统计以搜索结束时为准（incumbent 快照可能在更早处生成）。
        out["states_searched"] = self.states
        return out

    # ---------- 贪心种子：每种选择只取第一可行分支，失败即回溯到上一层 ----
    def _greedy_seed(self, dem0: tuple) -> None:
        # 贪心只取首可行分支、失败才回溯；为避免在困难无解实例上退化成
        # 无记忆穷举，限制其展开步数。找不到种子也无妨——正式搜索照常进行，
        # 只是初始剪枝界限较弱。
        self._greedy_steps = 0
        self._greedy_cap = min(max(self.max_states, 1) * 2, 200_000)
        saved_max = self.max_states
        self.max_states = 10 ** 18
        try:
            self._greedy_open(dem0)
        except BudgetExhausted:
            pass
        self.max_states = saved_max

    def _greedy_open(self, dem: tuple) -> bool:
        if sum(dem) == 0:
            self._accept()
            return True
        cur_sheets = len(self.committed) + (1 if self.ctx is not None else 0)
        if cur_sheets >= self.max_sheets:
            return False
        part_cost = self._cost(self.used)
        best = self.incumbent["key"] if self.incumbent else None
        for s in self.open_order:
            L, W, qty, cost = self.specs[s]
            if self.used[s] >= qty:
                continue
            if best is not None:
                new_cost = part_cost + cost
                new_sheets = sum(self.used) + 1
                if new_cost > best[0]:
                    continue
                if new_cost == best[0] and new_sheets > best[1]:
                    continue
                if new_cost == best[0] and new_sheets == best[1]:
                    tentative = list(self.used)
                    tentative[s] += 1
                    if (new_cost, new_sheets,
                            self._early_tuple(tentative)) >= best:
                        continue
            ctx = _SheetCtx(nid=0, spec=s, L=L, W=W)
            ctx.nodes[0] = Node(0, (0, 0, L, W), is_free=True)
            ctx._next = 1  # 根节点已占用 id 0
            ctx.frees = [(0, 0, L, W, 0)]
            self.ctx = ctx
            self.used[s] += 1
            ok = self._greedy_active(ctx.frees, dem)
            self.used[s] -= 1
            self.ctx = None
            if ok:
                return True
        return False

    def _greedy_active_done(self, dem: tuple) -> bool:
        ctx = self.ctx
        self.committed.append(ctx)
        self.ctx = None
        ok = self._greedy_open(dem)
        self.ctx = self.committed.pop()
        return ok

    def _greedy_active(self, frees: List[Free], dem: tuple) -> bool:
        ctx = self.ctx
        if not frees:
            return self._greedy_active_done(dem)
        self._greedy_steps += 1
        if self._greedy_steps > self._greedy_cap:
            raise BudgetExhausted()
        frees = sorted(frees, key=lambda f: (-f[2] * f[3], -f[2], -f[3], f[4]))
        x, y, w, h, nid = frees[0]
        rest = frees[1:]
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
                for plan in plans_for(w, h, pw, ph, self.kerf):
                    snapshot = ctx._next
                    new_frees = expand(ctx.nodes, ctx.alloc, self.kerf,
                                       nid, (x, y, w, h), plan,
                                       ti, rotated, pw, ph)
                    ctx.placed += 1
                    new_dem = list(dem)
                    new_dem[ti] -= 1
                    new_dem_t = tuple(new_dem)
                    if sum(new_dem) == 0:
                        self._accept()
                        ok = True
                    else:
                        ok = self._greedy_active(rest + new_frees, new_dem_t)
                    restore(ctx.nodes, nid, (x, y, w, h),
                            snapshot, ctx._next)
                    ctx._next = snapshot
                    ctx.placed -= 1
                    if ok:
                        return True
        if ctx.placed > 0:
            node = ctx.nodes[nid]
            node.is_free = False
            node.is_waste = True
            ok = self._greedy_active(rest, dem)
            node.is_free = True
            node.is_waste = False
            return ok
        return False

    def _empty_result(self, status: str) -> dict:
        return {
            "status": status,
            "optimal": False,
            "states_searched": self.states,
            "sheets_used": 0,
            "total_cost": 0,
            "counts": [0] * self.n_types,
            "pieces": [],
            "cuts": [],
            "sheets": [],
        }

    # ---------- 开启下一张母纸 ----------
    def _open_state(self, dem: tuple) -> None:
        if sum(dem) == 0:
            self._accept()
            return
        cur_sheets = len(self.committed) + (1 if self.ctx is not None else 0)
        if cur_sheets >= self.max_sheets:
            return

        best = self.incumbent["key"] if self.incumbent else None
        part_cost = self._cost(self.used)   # 不含即将新开的这一张
        for s in self.open_order:
            L, W, qty, cost = self.specs[s]
            if self.used[s] >= qty:
                continue
            # 相对当前最优解的分支限界：新纸落定后总纸价只增不减。
            if best is not None:
                new_cost = part_cost + cost
                new_sheets = sum(self.used) + 1
                if new_cost > best[0]:
                    continue
                if new_cost == best[0] and new_sheets > best[1]:
                    continue
                if new_cost == best[0] and new_sheets == best[1]:
                    # 此后不允许再加纸（否则张数超出），用量向量即此试探值。
                    tentative = list(self.used)
                    tentative[s] += 1
                    if (new_cost, new_sheets,
                            self._early_tuple(tentative)) >= best:
                        continue
            ctx = _SheetCtx(nid=0, spec=s, L=L, W=W)
            ctx.nodes[0] = Node(0, (0, 0, L, W), is_free=True)
            ctx._next = 1  # 根节点已占用 id 0
            ctx.frees = [(0, 0, L, W, 0)]
            self.ctx = ctx
            self.used[s] += 1
            self._pop_cap(self.spec_areas[s])
            self._dfs_active(ctx.frees, dem)
            self._push_cap(self.spec_areas[s])
            self.used[s] -= 1
            self.ctx = None

    # 当前活动母纸已全部切成叶子 => 冻结并进入「是否再开一张」。
    def _active_done(self, dem: tuple) -> None:
        ctx = self.ctx
        self.committed.append(ctx)
        self.ctx = None
        self._open_state(dem)
        self.ctx = self.committed.pop()

    # ---------- 活动母纸内的 DFS ----------
    def _dfs_active(self, frees: List[Free], dem: tuple,
                    need_area: int = -1) -> None:
        ctx = self.ctx
        if not frees:
            self._active_done(dem)
            return

        # 固定选取最大余料；其余尺寸相同的余料对称。
        frees.sort(key=self._free_sort_key)
        if need_area < 0:
            need_area = sum(a * dem[i]
                            for i, a in enumerate(self.type_areas))

        # 记忆（同规格、同余料形状多重集、同需求、同已用纸量向量）；
        # 状态计数先于剪枝，与单规格搜索口径一致。
        key = (ctx.spec,
               tuple(sorted((f[2], f[3]) for f in frees)),
               dem, tuple(self.used))
        if key in self.memo:
            return
        self.states += 1
        if self.states > self.max_states:
            raise BudgetExhausted()
        self.memo.add(key)

        x, y, w, h, nid = frees[0]
        rest = frees[1:]

        free_area = sum(f[2] * f[3] for f in frees)
        future_area = self._caps_prefix[-1] if self._caps_prefix else 0
        cur_sheets = len(self.committed) + 1

        # 廉价硬剪枝：面积总量、张数上限。
        if need_area > free_area + future_area:
            return
        if need_area > free_area and cur_sheets >= self.max_sheets:
            return

        if self.incumbent is not None:
            best_cost, best_sheets, _ = self.incumbent["key"]
            unpaid = need_area - free_area
            if unpaid > 0:
                need_extra = self._extra_sheets_lb(unpaid)
                if need_extra > self.max_sheets - cur_sheets:
                    return
                add_cost = 0
                picked = 0
                for s in self.cost_order:
                    if picked >= need_extra:
                        break
                    take = min(self.specs[s][2] - self.used[s],
                               need_extra - picked)
                    if take > 0:
                        add_cost += take * self.specs[s][3]
                        picked += take
                lb = self._cost(self.used) + add_cost
                if lb > best_cost or (
                        lb == best_cost and
                        cur_sheets + need_extra > best_sheets):
                    return

        # 容纳性剪枝。
        for i in self.type_order:
            if dem[i] <= 0:
                continue
            il, iw, _rot = self.types[i]
            fits_any = any(
                (il <= cw and iw <= ch)
                or (_rot and iw <= cw and il <= ch)
                for _, _, cw, ch, _ in frees)
            if not fits_any:
                for s in range(self.n_specs):
                    if self.used[s] < self.specs[s][2] and \
                            self.spec_fits[s][i]:
                        fits_any = True
                        break
            if not fits_any:
                return

        for ti in self.type_order:
            if dem[ti] <= 0:
                continue
            il, iw, _rotatable = self.types[ti]
            for pw, ph, rotated in self.type_orients[ti]:
                if pw > w or ph > h:
                    continue
                piece_area = pw * ph
                for plan in plans_for(w, h, pw, ph, self.kerf):
                    snapshot = ctx._next
                    new_frees = expand(ctx.nodes, ctx.alloc, self.kerf,
                                       nid, (x, y, w, h), plan,
                                       ti, rotated, pw, ph)
                    ctx.placed += 1
                    new_dem = list(dem)
                    new_dem[ti] -= 1
                    new_need = need_area - piece_area
                    if new_need == 0:
                        self._accept()
                    else:
                        self._dfs_active(rest + new_frees,
                                         tuple(new_dem), new_need)
                    restore(ctx.nodes, nid, (x, y, w, h),
                            snapshot, ctx._next)
                    ctx._next = snapshot
                    ctx.placed -= 1

        # 余料作废分支。该纸尚未放入任何成品时整纸作废等价于「不开这张
        # 纸」，属被支配分支（开纸选择已在上层枚举），故跳过。
        if ctx.placed > 0:
            node = ctx.nodes[nid]
            node.is_free = False
            node.is_waste = True
            self._dfs_active(rest, dem, need_area)
            node.is_free = True
            node.is_waste = False

    def _extra_sheets_lb(self, unpaid_area: int) -> int:
        """剩余 unpaid_area 平方毫米至少还要开几张新纸的面积下界。

        未来可用空白纸按面积降序排列（self._caps）：每开一张至多贡献其
        纸面面积，故装满 unpaid_area 所需张数是任何真实排样用纸数下界。
        """
        if not self._caps_prefix:
            return 10 ** 18
        # 首个前缀和 >= unpaid_area
        idx = bisect.bisect_left(self._caps_prefix, unpaid_area)
        return idx + 1 if idx < len(self._caps_prefix) \
            else len(self._caps_prefix) + 1

    # ---------- 命中完整方案：按 (纸价, 张数, 次序) 决定是否刷新最优 ----
    def _accept(self) -> None:
        key = self._key()
        if self.incumbent is not None and key >= self.incumbent["key"]:
            return
        self.incumbent = {"key": key, "data": self._collect(key)}

    def _collect(self, key: tuple) -> dict:
        pieces: List[dict] = []
        cuts: List[dict] = []
        counts = [0] * self.n_types
        sheets_meta: List[dict] = []

        ctxs = self.committed + ([self.ctx] if self.ctx is not None else [])
        for si, ctx in enumerate(ctxs):
            L, W = ctx.L, ctx.W
            s = ctx.spec
            sheets_meta.append({
                "spec": s,
                "l": L,
                "w": W,
                "cost": self.specs[s][3],
            })
            walk_sheet(ctx.nodes, ctx.nid, L, W, self.kerf,
                       si, s, pieces, cuts, counts)

        cuts.sort(key=lambda c: (c["sheet"], c["order"]))
        for i, c in enumerate(cuts):
            c["seq"] = i + 1

        return {
            "states_searched": self.states,
            "sheets_used": len(sheets_meta),
            "total_cost": key[0],
            "counts": counts,
            "pieces": pieces,
            "cuts": cuts,
            "sheets": sheets_meta,
        }
