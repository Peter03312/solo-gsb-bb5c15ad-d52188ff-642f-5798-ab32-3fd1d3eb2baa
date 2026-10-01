"""Guillotine（贯通式）矩形排样搜索。

业务约束：
- 每一刀必须贯穿当前正在处理的矩形余料；
- 刀缝宽度 kerf 从余料中扣除；
- 成品不得重叠、越界；旋转须符合该类成品的 rotatable 声明。

搜索：角点放置 + 两类首刀归一形式（V-first / H-first）的带记忆 DFS，
归一化枚举所有 guillotine 可行排样，并显式枚举「余料作废」分支，
因此在预算耗尽前穷尽整棵搜索树时给出的 infeasible 是可信的。

返回 status：
- found      找到可行方案（找到即返回，不追求最优）
- infeasible 已完整穷尽，判定无解
- budget     搜索状态数预算耗尽，不能判定无解
"""

from __future__ import annotations

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

    def to_json(self) -> dict:
        return {
            "status": self.status,
            "states_searched": self.states_searched,
            "sheets_used": self.sheets_used,
            "pieces": self.pieces,
            "cuts": self.cuts,
            "counts": self.counts,
        }


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
