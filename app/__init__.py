"""小型印刷作坊矩形纸张裁切排样 Web 应用。

API：
  GET  /                 浏览器界面
  GET  /api/state        当前输入 + 方案及有效性（cfg.mode: legacy/stock）
  PUT  /api/inputs       校验并保存输入（非法输入 400，不写库；旧方案失效）
                         请求体含 stocks 时为多规格库存模式，否则为
                         单一母纸模式（历史语义）
  POST /api/solve        基于已保存输入运行排样搜索
  GET  /api/export       导出文本裁切单
                         legacy：仅有效 found 方案可导出
                         stock ：optimal 可导出；budget 但已有可行方案可
                         导出（标注最优性未证实）；infeasible/无方案拒绝
"""

from __future__ import annotations

import io
import os
import sqlite3

from flask import Flask, jsonify, render_template, request, send_file

from . import db
from .solver import solve, solve_inventory
from .validation import (ValidationError, input_hash, input_version,
                         validate_input)


def create_app(db_path: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path or os.environ.get(
        "CUTTING_DB", os.path.join(os.path.dirname(__file__), "data",
                                   "cutting.db"))
    db.init_db(app.config["DB_PATH"])

    def conn() -> sqlite3.Connection:
        return db.get_conn(app.config["DB_PATH"])

    # ---------- 工具 ----------
    def _state_payload(c: sqlite3.Connection):
        job = db.get_job(c)
        sol = db.get_solution(c)
        if job is None:
            return {"has_input": False, "cfg": None, "solution": None}
        fresh = sol is not None and sol["input_hash"] == job["input_hash"]
        version = input_version(job["input_hash"])
        out_sol = None
        if sol is not None:
            out_sol = {
                "status": sol["status"],
                "fresh": fresh,
                "input_version": input_version(sol["input_hash"]),
                "created_at": sol["created_at"],
                "result": sol["result"],
            }
        return {
            "has_input": True,
            "cfg": job["cfg"],
            "input_version": version,
            "updated_at": job["updated_at"],
            "solution": out_sol,
        }

    # ---------- 页面 ----------
    @app.get("/")
    def index():
        return render_template("index.html")

    # ---------- 状态 ----------
    @app.get("/api/state")
    def api_state():
        with conn() as c:
            return jsonify(_state_payload(c))

    # ---------- 保存输入 ----------
    @app.put("/api/inputs")
    def api_save_inputs():
        try:
            cfg = validate_input(request.get_json(silent=True))
        except ValidationError as e:
            return jsonify({"ok": False, "error": e.message,
                            "field": e.field}), 400
        h = input_hash(cfg)
        with conn() as c:
            db.save_job(c, cfg, h)
            c.commit()
            return jsonify({"ok": True, "input_version": input_version(h),
                            "state": _state_payload(c)})

    # ---------- 运行排样 ----------
    @app.post("/api/solve")
    def api_solve():
        with conn() as c:
            job = db.get_job(c)
            if job is None:
                return jsonify({"ok": False,
                                "error": "请先保存输入参数"}), 400
            cfg, h = job["cfg"], job["input_hash"]
        if cfg.get("mode") == "stock":
            result = solve_inventory(
                [(s["l"], s["w"], s["qty"], s["cost"])
                 for s in cfg["stocks"]],
                cfg["kerf"], cfg["max_sheets"], cfg["max_states"],
                [{"l": p["l"], "w": p["w"], "qty": p["qty"],
                  "rotatable": p["rotatable"]} for p in cfg["products"]],
            )
        else:
            result = solve(
                cfg["paper_l"], cfg["paper_w"], cfg["kerf"],
                cfg["max_sheets"], cfg["max_states"],
                [{"l": p["l"], "w": p["w"], "qty": p["qty"],
                  "rotatable": p["rotatable"]} for p in cfg["products"]],
            )
        with conn() as c:
            db.save_solution(c, result["status"], h, result)
            c.commit()
            return jsonify({"ok": True, "state": _state_payload(c)})

    # ---------- 导出裁切单 ----------
    @app.get("/api/export")
    def api_export():
        with conn() as c:
            job = db.get_job(c)
            if job is None:
                return jsonify({"ok": False, "error": "尚未保存输入"}), 400
            sol = db.get_solution(c)
            if sol is None:
                return jsonify({"ok": False, "error": "尚无方案，请先排样"}), 409
            if sol["input_hash"] != job["input_hash"]:
                return jsonify({
                    "ok": False,
                    "error": "方案已失效：输入参数在方案保存后被修改，请重新排样",
                    "stale": True,
                }), 409
            if job["cfg"].get("mode") == "stock":
                # 库存模式：最优(optimal)或预算耗尽但已有可行方案可导出；
                # 无解 / 预算耗尽且无方案均不可导出。
                if sol["status"] == "infeasible":
                    return jsonify({
                        "ok": False,
                        "error": "已穷尽搜索且无解，没有可导出的方案",
                    }), 409
                if sol["status"] != "optimal" and \
                        sol["result"].get("sheets_used", 0) <= 0:
                    return jsonify({
                        "ok": False,
                        "error": "搜索预算耗尽且尚未找到可行排样，"
                                 "结果未判定，不可导出；请调大搜索预算重试",
                    }), 409
            elif sol["status"] != "found":
                return jsonify({
                    "ok": False,
                    "error": ("搜索预算耗尽，未获得可导出的可行方案"
                              if sol["status"] == "budget"
                              else "已穷尽搜索且无解，没有可导出的方案"),
                }), 409
            text = render_cutting_sheet(job["cfg"], job["input_hash"],
                                        sol["result"])
        buf = io.BytesIO(text.encode("utf-8"))
        return send_file(buf, mimetype="text/plain; charset=utf-8",
                         as_attachment=True,
                         download_name="cutting-sheet.txt")

    return app


# ---------- 裁切单文本 ----------
def render_cutting_sheet(cfg: dict, h: str, result: dict) -> str:
    stock_mode = cfg.get("mode") == "stock"
    lines = []
    lines.append("================ 裁切作业单（贯通式裁切） ================")
    lines.append(f"输入版本: {input_version(h)}")
    lines.append(f"指纹(sha256): {h}")
    if stock_mode:
        lines.append("母纸库存（规格顺序即录入顺序）：")
        for i, s in enumerate(cfg["stocks"]):
            lines.append(
                f"  规格{i}: {s['name']}  {s['l']}x{s['w']} mm  "
                f"库存 {s['qty']} 张  单张成本 {s['cost']}")
    else:
        lines.append(f"母纸尺寸: {cfg['paper_l']} mm x {cfg['paper_w']} mm")
    lines.append(f"刀缝宽度: {cfg['kerf']} mm")
    lines.append(f"张数上限: {cfg['max_sheets']}    "
                 f"搜索状态数上限: {cfg['max_states']}")
    lines.append("")
    lines.append("成品需求：")
    for i, p in enumerate(cfg["products"]):
        lines.append(
            f"  类型{i}: {p['name']}  {p['l']}x{p['w']} mm  "
            f"需求 {p['qty']} 份  可旋转: {'是' if p['rotatable'] else '否'}")
    lines.append("")
    lines.append(f"实际使用母纸: {result['sheets_used']} 张")
    lines.append(f"搜索状态数: {result['states_searched']}")
    if stock_mode:
        if result.get("optimal"):
            lines.append("最优性: 已完整穷尽搜索，证实本方案总纸价最低"
                         "（同价取用纸张数最少；再同则优先录入靠前的规格）。")
        else:
            lines.append("最优性: 未证实！搜索预算在完整穷尽前耗尽，本方案"
                         "仅为已找到的可行排样，不能声称总纸价最低；"
                         "请调大搜索状态数上限后重新排样。")
        lines.append(f"总纸价: {result['total_cost']}")
        lines.append("各规格用纸：")
        used_by_spec = {}
        for sh in result["sheets"]:
            used_by_spec[sh["spec"]] = used_by_spec.get(sh["spec"], 0) + 1
        for i, s in enumerate(cfg["stocks"]):
            n = used_by_spec.get(i, 0)
            lines.append(f"  规格{i}: {s['name']}  {s['l']}x{s['w']}  "
                         f"用 {n} / 库存 {s['qty']} 张  "
                         f"小计 {n * s['cost']}")
    lines.append("")
    lines.append("实际成品数量：")
    for i, p in enumerate(cfg["products"]):
        got = result["counts"][i]
        mark = "OK" if got >= p["qty"] else "短缺"
        lines.append(f"  类型{i}: {p['name']}  {p['l']}x{p['w']}  "
                     f"需求 {p['qty']} / 实得 {got}  [{mark}]")
    lines.append("")
    lines.append("裁切步骤（按实际执行顺序；坐标原点为母纸左上角，X 向右、"
                 "Y 向下，单位 mm）：")
    lines.append("刀缝起点坐标为 [坐标, 坐标+刀缝)；V=竖刀(沿Y方向贯穿)，"
                 "H=横刀(沿X方向贯穿)。")
    for c in result["cuts"]:
        if stock_mode:
            s = cfg["stocks"][c["spec"]]
            sheet_desc = (f"第{c['sheet'] + 1}张[规格{c['spec']} "
                          f"{s['l']}x{s['w']}]")
        else:
            sheet_desc = f"第{c['sheet'] + 1}张"
        if c["orientation"] == "V":
            lines.append(
                f"  #{c['seq']:>3} {sheet_desc} 竖刀 "
                f"x={c['x']}  y={c['y']}  刀长={c['length']}")
        else:
            lines.append(
                f"  #{c['seq']:>3} {sheet_desc} 横刀 "
                f"x={c['x']}  y={c['y']}  刀长={c['length']}")
    lines.append("")
    lines.append("成品落位（每张母纸）：")
    by_sheet = {}
    for p in result["pieces"]:
        by_sheet.setdefault(p["sheet"], []).append(p)
    for si in sorted(by_sheet):
        if stock_mode:
            sh = result["sheets"][si]
            s = cfg["stocks"][sh["spec"]]
            head = (f"-- 第 {si + 1} 张（规格{sh['spec']}: {s['name']} "
                    f"{s['l']}x{s['w']} mm，单张成本 {s['cost']}）--")
        else:
            head = f"-- 第 {si + 1} 张 --"
        lines.append(head)
        for p in by_sheet[si]:
            t = cfg["products"][p["product"]]
            rot = " [旋转]" if p["rotated"] else ""
            lines.append(
                f"  {t['name']} {p['w']}x{p['h']}  左下角(x={p['x']}, "
                f"y={p['y']}){rot}")
    lines.append("=" * 58)
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
