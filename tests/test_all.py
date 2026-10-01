"""求解器与 Web 业务规则测试。直接 `python3 tests/test_all.py` 运行。"""

import os
import sys
import tempfile
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, render_cutting_sheet
from app.solver import solve
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


# ---------------- 库存模式：多规格 + 成本最优 ----------------

def simulate_stock(result, stocks, k, prods):
    """按 result["sheet_specs"] 取每张母纸尺寸，校验贯穿刀与落位、库存上限。"""
    for si in range(result["sheets_used"]):
        spec_idx = result["sheet_specs"][si]
        L, W = stocks[spec_idx]["l"], stocks[spec_idx]["w"]
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
    usage = Counter(result["sheet_specs"])
    for i, s in enumerate(stocks):
        assert usage[i] <= s["qty"]
    assert result["sheets_used"] == len(result["sheet_specs"])
    assert result["total_cost"] == sum(
        stocks[i]["cost"] * n for i, n in usage.items())


def test_stock_picks_cheapest():
    prods = [{"l": 50, "w": 50, "qty": 4, "rotatable": True}]
    stocks = [{"l": 100, "w": 100, "qty": 2, "cost": 100},
              {"l": 50, "w": 50, "qty": 4, "cost": 20}]
    r = solve(0, 0, 0, 10, 10 ** 7, prods, stocks=stocks)
    assert r["status"] == "optimal" and r["optimal"] is True
    # 4 张小纸 80 < 1 张大纸 100
    assert r["total_cost"] == 80 and r["sheets_used"] == 4
    assert r["sheet_specs"] == [1, 1, 1, 1]
    assert r["counts"] == [4]
    simulate_stock(r, stocks, 0, prods)


def test_stock_fewer_sheets_then_earlier_index():
    prods = [{"l": 50, "w": 50, "qty": 4, "rotatable": True}]
    # 同价：张数少者优先 => 1 张大纸
    stocks = [{"l": 100, "w": 100, "qty": 2, "cost": 100},
              {"l": 50, "w": 50, "qty": 4, "cost": 100}]
    r = solve(0, 0, 0, 10, 10 ** 7, prods, stocks=stocks)
    assert r["status"] == "optimal"
    assert r["total_cost"] == 100 and r["sheet_specs"] == [0]

    # 同价同张数：录入靠前规格优先
    stocks = [{"l": 100, "w": 100, "qty": 2, "cost": 100},
              {"l": 101, "w": 101, "qty": 2, "cost": 100}]
    r = solve(0, 0, 0, 10, 10 ** 7,
              [{"l": 50, "w": 50, "qty": 8, "rotatable": True}],
              stocks=stocks)
    assert r["status"] == "optimal"
    assert r["total_cost"] == 200 and r["sheet_specs"] == [0, 0]


def test_stock_inventory_and_sheet_cap():
    prods = [{"l": 60, "w": 60, "qty": 3, "rotatable": True}]
    # 每张只能放 1 件，库存 2 张 => 库存不足，穷尽无解
    r = solve(0, 0, 0, 10, 10 ** 7, prods,
              stocks=[{"l": 100, "w": 100, "qty": 2, "cost": 100}])
    assert r["status"] == "infeasible"
    # 库存够但总张数上限不够
    r = solve(0, 0, 0, 2, 10 ** 7,
              [{"l": 50, "w": 50, "qty": 3, "rotatable": True}],
              stocks=[{"l": 50, "w": 50, "qty": 10, "cost": 1}])
    assert r["status"] == "infeasible"


def test_stock_rotation_and_kerf():
    # 不可旋转 => 便宜的 50x100 放不下 60x40（原姿态 60>50），必须用贵纸
    prods = [{"l": 60, "w": 40, "qty": 1, "rotatable": False}]
    stocks = [{"l": 50, "w": 100, "qty": 5, "cost": 1},
              {"l": 100, "w": 100, "qty": 5, "cost": 50}]
    r = solve(0, 0, 0, 10, 10 ** 7, prods, stocks=stocks)
    assert r["status"] == "optimal" and r["sheet_specs"] == [1]
    # 允许旋转 => 便宜纸可用
    prods[0]["rotatable"] = True
    r = solve(0, 0, 0, 10, 10 ** 7, prods, stocks=stocks)
    assert r["status"] == "optimal" and r["sheet_specs"] == [0]
    # 刀缝：49+3+49=101，一张 100x100 放不了两件；一张 101 纸 150 < 两张 100 纸 200
    prods2 = [{"l": 49, "w": 49, "qty": 2, "rotatable": True}]
    stocks2 = [{"l": 100, "w": 100, "qty": 5, "cost": 100},
               {"l": 101, "w": 101, "qty": 5, "cost": 150}]
    r = solve(0, 0, 3, 10, 10 ** 7, prods2, stocks=stocks2)
    assert r["status"] == "optimal"
    assert r["total_cost"] == 150 and r["sheets_used"] == 1
    simulate_stock(r, stocks2, 3, prods2)


def test_stock_mixed_geometry():
    prods = [{"l": 80, "w": 60, "qty": 6, "rotatable": True},
             {"l": 45, "w": 45, "qty": 10, "rotatable": False}]
    stocks = [{"l": 200, "w": 150, "qty": 3, "cost": 200},
              {"l": 120, "w": 100, "qty": 5, "cost": 90},
              {"l": 100, "w": 100, "qty": 5, "cost": 60}]
    r = solve(0, 0, 3, 10, 2_000_000, prods, stocks=stocks)
    assert r["status"] == "optimal"
    assert r["counts"] == [6, 10]
    simulate_stock(r, stocks, 3, prods)


def test_stock_budget_statuses():
    # 预算小但能找到可行解 => found + optimal=False（最优性未证实）
    r = solve(0, 0, 0, 30, 50_000,
              [{"l": 31, "w": 29, "qty": 20, "rotatable": True}],
              stocks=[{"l": 100, "w": 100, "qty": 30, "cost": 1}])
    assert r["status"] in ("found", "optimal")
    if r["status"] == "found":
        assert r["optimal"] is False and r["sheets_used"] > 0
        simulate_stock(r, [{"l": 100, "w": 100, "qty": 30, "cost": 1}],
                       0, [{"l": 31, "w": 29, "rotatable": True}])
    # 预算极小且无方案 => budget（未判定，不是 infeasible）
    r = solve(0, 0, 2, 30, 1,
              [{"l": 31, "w": 29, "qty": 50, "rotatable": True}],
              stocks=[{"l": 100, "w": 100, "qty": 30, "cost": 1}])
    assert r["status"] == "budget" and r["optimal"] is False


def test_stock_zero_cost():
    prods = [{"l": 50, "w": 50, "qty": 4, "rotatable": True}]
    stocks = [{"l": 100, "w": 100, "qty": 5, "cost": 0},
              {"l": 50, "w": 50, "qty": 5, "cost": 0}]
    r = solve(0, 0, 0, 10, 10 ** 7, prods, stocks=stocks)
    assert r["status"] == "optimal"
    assert r["total_cost"] == 0 and r["sheets_used"] == 1
    assert r["sheet_specs"] == [0]


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


def _stock_base():
    return {"kerf": 0, "max_sheets": 6, "max_states": 100_000,
            "stocks": [{"name": "大纸", "l": 100, "w": 100,
                        "qty": 4, "cost": 100},
                       {"name": "小纸", "l": 50, "w": 50,
                        "qty": 20, "cost": 20}],
            "products": [{"name": "A", "l": 50, "w": 50,
                          "qty": 4, "rotatable": True}]}


def test_validation_stock_mode():
    cfg = validate_input(_stock_base())
    assert cfg["stocks"][0]["cost"] == 100
    assert "paper_l" not in cfg
    # 库存模式下给顶层尺寸 => 400（防止误以为尺寸生效）
    bad = _stock_base()
    bad["paper_l"] = 100
    try:
        validate_input(bad)
        raise AssertionError("库存模式不应接受 paper_l")
    except ValidationError as e:
        assert e.field == "paper_l"
    # 空库存、负数成本、零库存、布尔伪装数字均拒绝
    for patch, field in [
        ({"stocks": []}, "stocks"),
        ({"stocks": [{"l": 100, "w": 100, "qty": 1, "cost": -1}]},
         "stocks[0].cost"),
        ({"stocks": [{"l": 100, "w": 100, "qty": 0, "cost": 1}]},
         "stocks[0].qty"),
        ({"stocks": [{"l": 100.5, "w": 100, "qty": 1, "cost": 1}]},
         "stocks[0].l"),
    ]:
        body = _stock_base()
        body.update(patch)
        try:
            validate_input(body)
            raise AssertionError(f"应当拒绝 {field}")
        except ValidationError as e:
            assert e.field == field


def test_stock_hash_semantics():
    a = validate_input(_stock_base())
    # 成本变化改变版本（旧方案不得按新输入导出）
    b = _stock_base()
    b["stocks"][0]["cost"] = 120
    assert input_hash(a) != input_hash(validate_input(b))
    # 库存张数、规格尺寸、录入顺序变化都改变版本
    b = _stock_base(); b["stocks"][1]["qty"] = 19
    assert input_hash(a) != input_hash(validate_input(b))
    b = _stock_base(); b["stocks"][0], b["stocks"][1] = (
        b["stocks"][1], b["stocks"][0])
    assert input_hash(a) != input_hash(validate_input(b))
    # 仅规格名称变化不改变版本
    c = _stock_base(); c["stocks"][0]["name"] = "改名"
    assert input_hash(a) == input_hash(validate_input(c))
    # 单规格与库存模式的指纹不冲突
    assert input_hash(a) != input_hash(validate_input(_base()))


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


def test_web_stock_lifecycle():
    c = _client()
    body = _stock_base()
    r = c.put("/api/inputs", json=body)
    assert r.status_code == 200
    st = r.get_json()["state"]
    assert st["cfg"]["mode"] == "stock"
    assert "paper_l" not in st["cfg"] and len(st["cfg"]["stocks"]) == 2

    s = c.post("/api/solve").get_json()["state"]["solution"]
    # 4 个 50x50：4 张小纸 80 < 1 张大纸 100
    assert s["status"] == "optimal" and s["fresh"]
    assert s["result"]["total_cost"] == 80
    assert s["result"]["sheet_specs"] == [1, 1, 1, 1]

    # optimal 方案可导出，裁切单包含成本与最优性说明
    r = c.get("/api/export")
    assert r.status_code == 200
    text = r.data.decode()
    assert "总纸价: 80" in text and "已证实" in text

    # 改成本后旧方案失效，不得按新输入导出
    body["stocks"][1]["cost"] = 5
    c.put("/api/inputs", json=body)
    assert c.get("/api/state").get_json()["solution"]["fresh"] is False
    r = c.get("/api/export")
    assert r.status_code == 409 and r.get_json()["stale"]

    # 重新排样：此时 4 张小纸仅 20，仍便宜 => optimal
    s = c.post("/api/solve").get_json()["state"]["solution"]
    assert s["fresh"] and s["status"] == "optimal"
    assert s["result"]["total_cost"] == 20
    assert c.get("/api/export").status_code == 200


def test_web_stock_budget_found_export_marks_unproven():
    c = _client()
    c.put("/api/inputs", json={
        "kerf": 0, "max_sheets": 30, "max_states": 50_000,
        "stocks": [{"l": 100, "w": 100, "qty": 30, "cost": 1}],
        "products": [{"l": 31, "w": 29, "qty": 20, "rotatable": True}]})
    s = c.post("/api/solve").get_json()["state"]["solution"]
    assert s["status"] in ("found", "optimal")
    if s["status"] == "found":
        # 有可行方案但最优性未证实：允许导出，且裁切单必须标明未证实
        r = c.get("/api/export")
        assert r.status_code == 200
        assert "未证实" in r.data.decode()


def test_web_stock_budget_no_plan_not_exportable():
    c = _client()
    c.put("/api/inputs", json={
        "kerf": 2, "max_sheets": 30, "max_states": 1,
        "stocks": [{"l": 120, "w": 90, "qty": 30, "cost": 1}],
        "products": [{"l": 37, "w": 29, "qty": 50, "rotatable": True},
                     {"l": 23, "w": 31, "qty": 40, "rotatable": False}]})
    s = c.post("/api/solve").get_json()["state"]["solution"]
    assert s["status"] == "budget"
    assert c.get("/api/export").status_code == 409


def test_legacy_db_migration_keeps_semantics(tmp_path=None):
    """手工构造旧版库结构与数据，迁移后既有单规格输入仍按原语义读取。"""
    import sqlite3
    d = tempfile.mkdtemp()
    path = os.path.join(d, "legacy.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE job ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), "
        "paper_l INTEGER NOT NULL, paper_w INTEGER NOT NULL, "
        "kerf INTEGER NOT NULL, max_sheets INTEGER NOT NULL, "
        "max_states INTEGER NOT NULL, input_hash TEXT NOT NULL, "
        "updated_at TEXT NOT NULL DEFAULT (datetime('now')));"
        "CREATE TABLE product ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, position INTEGER NOT NULL, "
        "name TEXT NOT NULL, l INTEGER NOT NULL, w INTEGER NOT NULL, "
        "qty INTEGER NOT NULL, "
        "rotatable INTEGER NOT NULL CHECK (rotatable IN (0,1)));"
        "CREATE TABLE solution ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), status TEXT NOT NULL, "
        "input_hash TEXT NOT NULL, result_json TEXT NOT NULL, "
        "states_searched INTEGER NOT NULL, "
        "sheets_used INTEGER NOT NULL DEFAULT 0, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now')));"
        "INSERT INTO job(id,paper_l,paper_w,kerf,max_sheets,max_states,"
        "input_hash) VALUES (1,100,100,0,4,100000,'deadbeef');"
        "INSERT INTO product(position,name,l,w,qty,rotatable) "
        "VALUES (0,'A',50,50,4,1);")
    conn.commit()
    conn.close()

    app = create_app(path)
    c = app.test_client()
    st = c.get("/api/state").get_json()
    assert st["has_input"] and st["cfg"]["mode"] == "single"
    assert st["cfg"]["paper_l"] == 100 and st["cfg"]["stocks"] == []
    # 没有迁移前方案行（旧库未插入 solution），排样仍走旧语义 found
    s = c.post("/api/solve").get_json()["state"]["solution"]
    assert s["status"] == "found"
    # 旧模式结果不带成本语义
    assert s["result"]["total_cost"] == 0


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"\n全部 {len(fns)} 个测试通过")


if __name__ == "__main__":
    _run_all()
