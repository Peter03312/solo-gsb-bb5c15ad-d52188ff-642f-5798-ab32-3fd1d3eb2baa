"""求解器与 Web 业务规则测试。直接 `python3 tests/test_all.py` 运行。"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
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


def _run_all():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print(f"\n全部 {len(fns)} 个测试通过")


if __name__ == "__main__":
    _run_all()
