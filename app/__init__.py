"""小型印刷作坊矩形纸张裁切排样 Web 应用。

API：
  GET  /                 浏览器界面
  GET  /api/state        当前输入 + 方案及有效性
  PUT  /api/inputs       校验并保存输入（非法输入 400，不写库；旧方案失效）
  POST /api/solve        基于已保存输入运行排样搜索
  GET  /api/export       导出文本裁切单（仅有效 found 方案）
"""

from __future__ import annotations

import io
import os
import sqlite3

from flask import Flask, jsonify, render_template, request, send_file

from . import db
from .solver import solve
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
            if sol["status"] != "found":
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
    L, W, k = cfg["paper_l"], cfg["paper_w"], cfg["kerf"]
    lines = []
    lines.append("================ 裁切作业单（贯通式裁切） ================")
    lines.append(f"输入版本: {input_version(h)}")
    lines.append(f"指纹(sha256): {h}")
    lines.append(f"母纸尺寸: {L} mm x {W} mm")
    lines.append(f"刀缝宽度: {k} mm")
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
        if c["orientation"] == "V":
            lines.append(
                f"  #{c['seq']:>3} 第{c['sheet'] + 1}张 竖刀 "
                f"x={c['x']}  y={c['y']}  刀长={c['length']}")
        else:
            lines.append(
                f"  #{c['seq']:>3} 第{c['sheet'] + 1}张 横刀 "
                f"x={c['x']}  y={c['y']}  刀长={c['length']}")
    lines.append("")
    lines.append("成品落位（每张母纸）：")
    by_sheet = {}
    for p in result["pieces"]:
        by_sheet.setdefault(p["sheet"], []).append(p)
    for si in sorted(by_sheet):
        lines.append(f"-- 第 {si + 1} 张 --")
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
