"""求解器与 Web 业务规则测试。直接 `python3 tests/test_all.py` 运行。"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.solver import solve, solve_inventory
from app.validation import (ValidationError, input_hash, validate_input)


# ---------------- 求解器：几何与 guillotine 约束 ----------------

def simulate_cuts(result, L, W, k, prods):
    """按顺序模拟裁切：每刀必须贯穿某个当前矩形净余料，并校验成品落位。"""
    for si in range(result["sheets_used"]):
        cuts = [c for c in result["cuts"] if c["sheet"] == si]
        rects = [(0, 0, L, W)]
        for c in cuts:
            x, y, length = c["x"], c["y"], c["length"]
            found = None
            for idx, (rx, ry, rw, rh) in enumerate(rects):
                if c["orientation"] == "V":
                    if rx <= x < rx + rw and ry == y and rh == length:
                        found = idx
                        break
                else:
                    if ry <= y < ry + rh and rx == x and rw == length:
                        found = idx
                        break
            assert found is not None, ("刀未贯穿当前余料", c, rects)
            rx, ry, rw, rh = rects.pop(found)
            if c["orientation"] == "V":
                a, b = x - rx, rx + rw - (x + k)
                if a > 0:
                    rects.append((rx, ry, a, rh))
                if b > 0:
                    rects.append((x + k, ry, b, rh))
            else:
                a, b = y - ry, ry + rh - (y + k)
                if a > 0:
                    rects.append((rx, ry, rw, a))
                if b > 0:
                    rects.append((rx, y + k, rw, b))
        remaining = rects[:]
        for p in (p for p in result["pieces"] if p["sheet"] == si):
            box = (p["x"], p["y"], p["w"], p["h"])
            assert box in remaining, ("成品与最终余料不吻合", box, remaining)
            remaining.remove(box)
            t = prods[p["product"]]
            if p["rotated"]:
                assert t["rotatable"]
                assert (p["w"], p["h"]) == (t["w"], t["l"])
            else:
                assert (p["w"], p["h"]) == (t["l"], t["w"])


def check_found(r, L, W, k, prods, qtys):
    assert r["status"] == "found"
    assert r["counts"] == qtys
    assert len(r["pieces"]) == sum(qtys)
    simulate_cuts(r, L, W, k, prods)


def test_basic_layouts():
    r = solve(100, 100, 0, 5, 10 ** 6,
              [{"l": 50, "w": 50, "qty": 4, "rotatable": True}])
    check_found(r, 100, 100, 0,
                [{"l": 50, "w": 50, "rotatable": True}], [4])
    assert r["sheets_used"] == 1

    r = solve(100, 100, 0, 5, 10 ** 6,
              [{"l": 60, "w": 60, "qty": 3, "rotatable": True}])
    check_found(r, 100, 100, 0,
                [{"l": 60, "w": 60, "rotatable": True}], [3])
    assert r["sheets_used"] == 3


def test_kerf_deducted():
    # 49+2+49=100：一张两件可行
    r = solve(100, 100, 2, 5, 10 ** 6,
              [{"l": 49, "w": 49, "qty": 2, "rotatable": True}])
    check_found(r, 100, 100, 2,
                [{"l": 49, "w": 49, "rotatable": True}], [2])
    assert r["sheets_used"] == 1
    # 49+3+49=101：一张放不下两件
    r = solve(100, 100, 3, 5, 10 ** 6,
              [{"l": 49, "w": 49, "qty": 2, "rotatable": True}])
    check_found(r, 100, 100, 3,
                [{"l": 49, "w": 49, "rotatable": True}], [2])
    assert r["sheets_used"] == 2


def test_edge_trim():
    # 100 宽、成品48、刀缝3：48+3+48=99，两件可行（外缘各余 1mm 废边）
    r = solve(100, 100, 3, 5, 10 ** 6,
              [{"l": 48, "w": 48, "qty": 2, "rotatable": True}])
    check_found(r, 100, 100, 3,
                [{"l": 48, "w": 48, "rotatable": True}], [2])
    assert r["sheets_used"] == 1


def test_rotation_respected():
    prods = [{"l": 60, "w": 40, "qty": 1, "rotatable": False}]
    r = solve(50, 100, 0, 1, 10 ** 7, prods)
    assert r["status"] == "infeasible"
    prods[0]["rotatable"] = True
    r = solve(50, 100, 0, 1, 10 ** 7, prods)
    check_found(r, 50, 100, 0, prods, [1])


def test_mixed_types():
    prods = [{"l": 100, "w": 60, "qty": 4, "rotatable": True},
             {"l": 50, "w": 50, "qty": 6, "rotatable": True}]
    r = solve(200, 150, 3, 6, 500_000, prods)
    check_found(r, 200, 150, 3, prods, [4, 6])


def test_infeasible_vs_budget():
    # 几何上摆不下：给足预算 => 已穷尽，无解
    r = solve(100, 100, 0, 1, 10 ** 7,
              [{"l": 60, "w": 60, "qty": 2, "rotatable": True}])
    assert r["status"] == "infeasible"
    # 张数上限收紧
    r = solve(100, 100, 0, 2, 10 ** 7,
              [{"l": 60, "w": 60, "qty": 3, "rotatable": True}])
    assert r["status"] == "infeasible"
    # 极小预算 => budget，不得声称无解
    r = solve(100, 100, 0, 8, 5,
              [{"l": 31, "w": 29, "qty": 30, "rotatable": True}])
    assert r["status"] in ("budget", "found")
    # 同一实例放宽预算可转为确定结论
    r = solve(120, 90, 2, 10, 10,
              [{"l": 37, "w": 29, "qty": 12, "rotatable": True},
               {"l": 23, "w": 31, "qty": 10, "rotatable": False},
               {"l": 41, "w": 19, "qty": 8, "rotatable": True}])
    assert r["status"] == "budget"


# ---------------- 输入校验 ----------------

def _base():
    return {"paper_l": 100, "paper_w": 100, "kerf": 0,
            "max_sheets": 2, "max_states": 1000,
            "products": [{"l": 50, "w": 50, "qty": 1, "rotatable": True}]}


def test_validation_accepts_good():
    cfg = validate_input(_base())
    assert cfg["paper_l"] == 100 and cfg["products"][0]["name"]


def test_validation_rejects_bad():
    bad_fields = [
        ({"paper_l": 0}, "paper_l"),
        ({"paper_l": -5}, "paper_l"),
        ({"paper_l": 1.5}, "paper_l"),
        ({"paper_l": "100"}, "paper_l"),
        ({"paper_l": True}, "paper_l"),
        ({"kerf": -1}, "kerf"),
        ({"kerf": False}, "kerf"),
        ({"max_sheets": 0}, "max_sheets"),
        ({"max_states": 0}, "max_states"),
        ({"products": []}, "products"),
    ]
    for patch, field in bad_fields:
        body = _base()
        body.update(patch)
        try:
            validate_input(body)
            raise AssertionError(f"应当拒绝 {field}: {patch}")
        except ValidationError as e:
            assert e.field == field
    # 成品行内字段
    for patch, field in [
        ({"l": 0}, "products[0].l"),
        ({"w": -1}, "products[0].w"),
        ({"qty": 0}, "products[0].qty"),
        ({"rotatable": 1}, "products[0].rotatable"),
    ]:
        body = _base()
        body["products"][0].update(patch)
        try:
            validate_input(body)
            raise AssertionError(field)
        except ValidationError as e:
            assert e.field == field


def test_input_hash_changes_on_any_input_change():
    a = validate_input(_base())
    b = validate_input({**_base(), "kerf": 1})
    assert input_hash(a) != input_hash(b)
    # 仅名称变化不影响排样版本
    c = validate_input(_base())
    c["products"][0]["name"] = "改名"
    assert input_hash(a) == input_hash(c)


# ---------------- Web 业务规则 ----------------

def _client():
    tmp = tempfile.mkdtemp()
    app = create_app(os.path.join(tmp, "t.db"))
    return app.test_client()


def test_web_full_lifecycle():
    c = _client()
    assert c.get("/api/state").get_json()["has_input"] is False

    # 非法输入 400 且不写库
    r = c.put("/api/inputs", json={**_base(), "paper_l": 0})
    assert r.status_code == 400
    assert c.get("/api/state").get_json()["has_input"] is False

    cfg = {"paper_l": 100, "paper_w": 100, "kerf": 0,
           "max_sheets": 4, "max_states": 100_000,
           "products": [{"name": "A", "l": 50, "w": 50,
                         "qty": 4, "rotatable": True}]}
    v1 = c.put("/api/inputs", json=cfg).get_json()["input_version"]
    s = c.post("/api/solve").get_json()["state"]["solution"]
    assert s["status"] == "found" and s["fresh"]

    r = c.get("/api/export")
    assert r.status_code == 200 and v1 in r.data.decode()

    # 修改输入 => 旧方案失效，导出被拒
    cfg["kerf"] = 2
    v2 = c.put("/api/inputs", json=cfg).get_json()["input_version"]
    assert v1 != v2
    assert c.get("/api/state").get_json()["solution"]["fresh"] is False
    r = c.get("/api/export")
    assert r.status_code == 409 and r.get_json()["stale"]

    # 重新排样 => 恢复
    s = c.post("/api/solve").get_json()["state"]["solution"]
    assert s["fresh"] and s["status"] == "found"
    assert c.get("/api/export").status_code == 200


def test_web_budget_and_infeasible_not_exportable():
    c = _client()
    c.put("/api/inputs", json={
        "paper_l": 120, "paper_w": 90, "kerf": 2,
        "max_sheets": 10, "max_states": 10,
        "products": [
            {"l": 37, "w": 29, "qty": 12, "rotatable": True},
            {"l": 23, "w": 31, "qty": 10, "rotatable": False},
            {"l": 41, "w": 19, "qty": 8, "rotatable": True}]})
    s = c.post("/api/solve").get_json()["state"]["solution"]
    assert s["status"] == "budget"
    assert c.get("/api/export").status_code == 409

    c.put("/api/inputs", json={
        "paper_l": 100, "paper_w": 100, "kerf": 0,
        "max_sheets": 1, "max_states": 10 ** 7,
        "products": [{"l": 60, "w": 60, "qty": 2,
                      "rotatable": True}]})
    s = c.post("/api/solve").get_json()["state"]["solution"]
    assert s["status"] == "infeasible"
    assert c.get("/api/export").status_code == 409


# ---------------- 多规格库存：最低纸价 + 三级次序 ----------------

_stock_kerf = 0


def sim_stock(result, stocks, prods):
    """对库存方案逐张按其实际规格尺寸做贯通裁切与落位校验。"""
    for si in range(result["sheets_used"]):
        L, W = stocks[result["sheets"][si]["spec"]][:2]
        k = _stock_kerf
        cuts = [c for c in result["cuts"] if c["sheet"] == si]
        rects = [(0, 0, L, W)]
        for c in cuts:
            x, y, length = c["x"], c["y"], c["length"]
            found = None
            for idx, (rx, ry, rw, rh) in enumerate(rects):
                if c["orientation"] == "V":
                    if rx <= x < rx + rw and ry == y and rh == length:
                        found = idx
                        break
                else:
                    if ry <= y < ry + rh and rx == x and rw == length:
                        found = idx
                        break
            assert found is not None, ("刀未贯穿当前余料", c, rects)
            rx, ry, rw, rh = rects.pop(found)
            if c["orientation"] == "V":
                a, b = x - rx, rx + rw - (x + k)
                if a > 0:
                    rects.append((rx, ry, a, rh))
                if b > 0:
                    rects.append((x + k, ry, b, rh))
            else:
                a, b = y - ry, ry + rh - (y + k)
                if a > 0:
                    rects.append((rx, ry, rw, a))
                if b > 0:
                    rects.append((rx, y + k, rw, b))
        for p in (p for p in result["pieces"] if p["sheet"] == si):
            box = (p["x"], p["y"], p["w"], p["h"])
            assert box in rects, ("成品与最终余料不吻合", box, rects)
            rects.remove(box)
            t = prods[p["product"]]
            if p["rotated"]:
                assert t["rotatable"]
                assert (p["w"], p["h"]) == (t["w"], t["l"])
            else:
                assert (p["w"], p["h"]) == (t["l"], t["w"])


_stock_kerf = 0


def run_stock(stocks, k, prods, qtys, max_sheets=12, budget=10 ** 7):
    global _stock_kerf
    _stock_kerf = k
    r = solve_inventory(stocks, k, max_sheets, budget, prods)
    if r["status"] != "infeasible":
        sim_stock(r, stocks, prods)
        assert r["counts"] == qtys
    return r


def test_stock_cost_optimization():
    # 3 件 60x60：小纸 3 张 *90=270 < 大纸 1 张 *300
    prods = [{"l": 60, "w": 60, "qty": 3, "rotatable": True}]
    stocks = [(100, 100, 5, 90), (200, 200, 5, 300)]
    r = run_stock(stocks, 0, prods, [3])
    assert r["status"] == "optimal" and r["total_cost"] == 270
    assert all(sh["spec"] == 0 for sh in r["sheets"])

    # 价格反过来 => 大纸 1 张更省
    stocks = [(100, 100, 5, 100), (200, 200, 5, 250)]
    r = run_stock(stocks, 0, prods, [3])
    assert r["status"] == "optimal" and r["total_cost"] == 250
    assert all(sh["spec"] == 1 for sh in r["sheets"])


def test_stock_tie_sheet_count():
    # 9 件 50x50：100 纸每纸 4 件需 3 张；220 纸 1 张。同单价取张数少者。
    prods = [{"l": 50, "w": 50, "qty": 9, "rotatable": True}]
    stocks = [(100, 100, 5, 100), (220, 220, 5, 100)]
    r = run_stock(stocks, 0, prods, [9])
    assert r["status"] == "optimal" and r["total_cost"] == 100
    assert r["sheets_used"] == 1 and r["sheets"][0]["spec"] == 1


def test_stock_tie_early_spec():
    # 同价同张数：优先录入靠前（下标 0）的规格。
    prods = [{"l": 50, "w": 50, "qty": 1, "rotatable": True}]
    stocks = [(100, 100, 5, 100), (100, 100, 5, 100)]
    r = run_stock(stocks, 0, prods, [1])
    assert r["status"] == "optimal" and r["sheets"][0]["spec"] == 0
    # 调换录入顺序 => 结论跟着顺序走
    stocks = [(100, 100, 5, 100,), (200, 200, 5, 100)]
    r = run_stock(stocks, 0, prods, [1])
    assert r["sheets"][0]["spec"] == 0


def test_stock_zero_cost_and_caps():
    # 单张成本 0 合法：此时比张数（少用纸张）。
    prods = [{"l": 60, "w": 60, "qty": 3, "rotatable": True}]
    stocks = [(100, 100, 5, 0), (200, 200, 5, 0)]
    r = run_stock(stocks, 0, prods, [3])
    assert r["status"] == "optimal" and r["total_cost"] == 0
    assert r["sheets_used"] == 1 and r["sheets"][0]["spec"] == 1

    # 库存张数不足 => 无解（同规格 2 张库存、总张数上限再大也无法突破）
    stocks = [(100, 100, 2, 1)]
    r = run_stock(stocks, 0, prods, [3], max_sheets=10)
    assert r["status"] == "infeasible"
    # 放开库存即可
    stocks = [(100, 100, 3, 1)]
    r = run_stock(stocks, 0, prods, [3], max_sheets=10)
    assert r["status"] == "optimal" and r["sheets_used"] == 3


def test_stock_kerf_rotation_mixed():
    # 刀缝：k=3 时 100 纸放 49x49 每件一张（49+3+49>100）
    prods = [{"l": 49, "w": 49, "qty": 2, "rotatable": True}]
    r = run_stock([(100, 100, 5, 100)], 3, prods, [2])
    assert r["sheets_used"] == 2
    r = run_stock([(100, 100, 5, 100)], 2, prods, [2])
    assert r["sheets_used"] == 1

    # 旋转限制：60x40 不可旋转，50x100 放不下
    prods = [{"l": 60, "w": 40, "qty": 1, "rotatable": False}]
    r = run_stock([(50, 100, 2, 100)], 0, prods, [1])
    assert r["status"] == "infeasible"
    prods[0]["rotatable"] = True
    r = run_stock([(50, 100, 2, 100)], 0, prods, [1])
    assert r["status"] == "optimal"

    # 混合成品 + 多规格：可行最优解费用不超过「3 张大纸」的上界
    prods = [{"l": 100, "w": 60, "qty": 4, "rotatable": True},
             {"l": 50, "w": 50, "qty": 6, "rotatable": True}]
    stocks = [(200, 150, 6, 100), (300, 220, 3, 220)]
    r = run_stock(stocks, 3, prods, [4, 6])
    assert r["status"] == "optimal"
    assert r["total_cost"] <= 660  # 至多 3 张大纸
    assert r["sheets_used"] <= 6


def test_stock_budget_statuses():
    # 小预算拿到可行方案：budget + optimal=False，但方案可用
    prods = [{"l": 31, "w": 29, "qty": 30, "rotatable": True}]
    r = solve_inventory([(100, 100, 8, 100)], 0, 8, 30, prods)
    assert r["status"] == "budget" and r["optimal"] is False
    if r["sheets_used"] > 0:
        _stock_kerf = 0
        sim_stock(r, [(100, 100, 8, 100)], prods)

    # 极小预算、未找到方案：budget，不允许表示为无解
    r = solve_inventory([(100, 100, 8, 100)], 0, 8, 1, prods)
    assert r["status"] in ("budget", "optimal", "infeasible")
    if r["status"] == "budget":
        assert r["optimal"] is False

    # 几何不可能且预算充足 => infeasible（可报告无解）
    prods = [{"l": 60, "w": 60, "qty": 2, "rotatable": True}]
    r = solve_inventory([(100, 100, 1, 100)], 0, 1, 10 ** 7, prods)
    assert r["status"] == "infeasible"


# ---------------- 库存输入校验与指纹 ----------------

def test_stock_validation():
    good = {"kerf": 0, "max_sheets": 2, "max_states": 100,
            "stocks": [{"l": 100, "w": 100, "qty": 5, "cost": 0},
                       {"l": 200, "w": 200, "qty": 1, "cost": 10 ** 9}],
            "products": [{"l": 50, "w": 50, "qty": 1, "rotatable": True}]}
    cfg = validate_input(good)
    assert cfg["mode"] == "stock" and len(cfg["stocks"]) == 2

    for patch, field in [
        ({"stocks": []}, "stocks"),
        ({"stocks": [{"l": 0, "w": 100, "qty": 1, "cost": 1}]},
         "stocks[0].l"),
        ({"stocks": [{"l": 100, "w": 100, "qty": 0, "cost": 1}]},
         "stocks[0].qty"),
        ({"stocks": [{"l": 100, "w": 100, "qty": 1, "cost": -1}]},
         "stocks[0].cost"),
        ({"stocks": [{"l": 100, "w": 100, "qty": 1, "cost": 1.5}]},
         "stocks[0].cost"),
        ({"stocks": [{"l": 100, "w": 100, "qty": 1, "cost": True}]},
         "stocks[0].cost"),
        ({"stocks": [{"l": "100", "w": 100, "qty": 1, "cost": 1}]},
         "stocks[0].l"),
    ]:
        body = {k: v for k, v in good.items()}
        body.update(patch)
        try:
            validate_input(body)
            raise AssertionError(f"应拒绝 {field}")
        except ValidationError as e:
            assert e.field == field, (e.field, field)


def test_stock_hash_and_mode():
    stock_body = {"kerf": 0, "max_sheets": 2, "max_states": 100,
                  "stocks": [{"l": 100, "w": 100, "qty": 5, "cost": 10}],
                  "products": [{"l": 50, "w": 50, "qty": 1,
                                "rotatable": True}]}
    a = validate_input(stock_body)
    b = validate_input({**stock_body,
                        "stocks": [dict(stock_body["stocks"][0], cost=11)]})
    assert input_hash(a) != input_hash(b)
    # 规格顺序变化影响指纹（次序是优化维度）
    c = validate_input({**stock_body,
                        "stocks": list(reversed(
                            stock_body["stocks"])) +
                        [{"l": 200, "w": 200, "qty": 1, "cost": 5}]})
    assert input_hash(a) != input_hash(c)
    # 仅改名不影响指纹
    d = validate_input(stock_body)
    d["stocks"][0]["name"] = "新名"
    assert input_hash(a) == input_hash(d)
    # legacy 与 stock 指纹空间分离
    legacy = validate_input({"paper_l": 100, "paper_w": 100, "kerf": 0,
                            "max_sheets": 2, "max_states": 100,
                            "products": [{"l": 50, "w": 50, "qty": 1,
                                          "rotatable": True}]})
    assert input_hash(legacy) != input_hash(a)


# ---------------- 库存模式 Web 业务规则 ----------------

def _stock_body():
    return {"kerf": 0, "max_sheets": 10, "max_states": 2_000_000,
            "stocks": [{"name": "小", "l": 100, "w": 100,
                        "qty": 5, "cost": 30},
                       {"name": "大", "l": 220, "w": 220,
                        "qty": 5, "cost": 100}],
            "products": [{"name": "A", "l": 50, "w": 50,
                          "qty": 9, "rotatable": True}]}


def test_web_stock_lifecycle():
    c = _client()
    body = _stock_body()
    r = c.put("/api/inputs", json=body)
    assert r.status_code == 200
    st = c.get("/api/state").get_json()
    assert st["cfg"]["mode"] == "stock"
    assert len(st["cfg"]["stocks"]) == 2

    s = c.post("/api/solve").get_json()["state"]["solution"]
    assert s["fresh"] and s["status"] == "optimal"
    assert s["result"]["total_cost"] == 90  # 3 张小纸
    # 导出成功且标注「已证实最优」
    ex = c.get("/api/export")
    assert ex.status_code == 200
    assert "证实本方案总纸价最低" in ex.data.decode()
    assert "总纸价: 90" in ex.data.decode()

    # 修改库存成本 => 旧方案失效，禁止按新输入导出
    body["stocks"][0]["cost"] = 31
    c.put("/api/inputs", json=body)
    assert c.get("/api/state").get_json()["solution"]["fresh"] is False
    r = c.get("/api/export")
    assert r.status_code == 409 and r.get_json()["stale"]

    # 重新排样后恢复
    s = c.post("/api/solve").get_json()["state"]["solution"]
    assert s["fresh"] and s["status"] == "optimal"
    assert c.get("/api/export").status_code == 200

    # 改回 legacy 模式后状态与求解语义随之切换
    c.put("/api/inputs", json={
        "paper_l": 100, "paper_w": 100, "kerf": 0,
        "max_sheets": 5, "max_states": 10 ** 6,
        "products": [{"name": "A", "l": 50, "w": 50,
                      "qty": 4, "rotatable": True}]})
    st = c.get("/api/state").get_json()
    assert st["cfg"]["mode"] == "legacy" and "stocks" not in st["cfg"]
    s = c.post("/api/solve").get_json()["state"]["solution"]
    assert s["status"] == "found"


def test_web_stock_budget_export_rules():
    c = _client()
    # 预算耗尽但已得可行方案：可导出，且裁切单标注最优性未证实
    c.put("/api/inputs", json={
        "kerf": 0, "max_sheets": 8, "max_states": 10,
        "stocks": [{"l": 100, "w": 100, "qty": 8, "cost": 1}],
        "products": [{"l": 31, "w": 29, "qty": 30, "rotatable": True}]})
    s = c.post("/api/solve").get_json()["state"]["solution"]
    assert s["status"] == "budget" and s["result"]["optimal"] is False
    if s["result"]["sheets_used"] > 0:
        ex = c.get("/api/export")
        assert ex.status_code == 200
        assert "最优性: 未证实" in ex.data.decode()
    else:
        assert c.get("/api/export").status_code == 409

    # 已穷尽无解：不可导出
    c.put("/api/inputs", json={
        "kerf": 0, "max_sheets": 1, "max_states": 10 ** 7,
        "stocks": [{"l": 100, "w": 100, "qty": 2, "cost": 1}],
        "products": [{"l": 60, "w": 60, "qty": 2, "rotatable": True}]})
    s = c.post("/api/solve").get_json()["state"]["solution"]
    assert s["status"] == "infeasible"
    assert c.get("/api/export").status_code == 409


def test_legacy_calls_keep_semantics_without_stocks():
    # 旧调用体（无 stocks）走单规格 found 语义
    r = solve(100, 100, 0, 5, 10 ** 6,
              [{"l": 50, "w": 50, "qty": 4, "rotatable": True}])
    assert r["status"] == "found" and "total_cost" not in r
    cfg = validate_input({"paper_l": 100, "paper_w": 100, "kerf": 0,
                          "max_sheets": 2, "max_states": 100,
                          "products": [{"l": 50, "w": 50, "qty": 1,
                                        "rotatable": True}]})
    assert cfg["mode"] == "legacy" and "stocks" not in cfg


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"\n全部 {len(fns)} 个测试通过")


if __name__ == "__main__":
    _run_all()
