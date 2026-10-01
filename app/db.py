"""SQLite 持久化：单行工单、成品行、库存行、最近一次方案与失效判定。"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def init_db(db_path: str) -> None:
    """幂等建表；并把旧版单规格库结构就地迁移到支持库存模式的结构。"""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()
        _migrate_job(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate_job(conn: sqlite3.Connection) -> None:
    """旧库的 job 表没有 mode 列、paper_l/paper_w 为 NOT NULL：重建迁移。

    迁移后的既有工单仍按单规格模式（mode='single'）解释，保持原语义。
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(job)").fetchall()}
    if "mode" in cols:
        return
    conn.execute("ALTER TABLE job RENAME TO job_old")
    conn.execute(
        "CREATE TABLE job ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), "
        "mode TEXT NOT NULL DEFAULT 'single', "
        "paper_l INTEGER, paper_w INTEGER, "
        "kerf INTEGER NOT NULL, max_sheets INTEGER NOT NULL, "
        "max_states INTEGER NOT NULL, input_hash TEXT NOT NULL, "
        "updated_at TEXT NOT NULL DEFAULT (datetime('now')))")
    conn.execute(
        "INSERT INTO job(id, mode, paper_l, paper_w, kerf, max_sheets, "
        "max_states, input_hash, updated_at) "
        "SELECT id, 'single', paper_l, paper_w, kerf, max_sheets, "
        "max_states, input_hash, updated_at FROM job_old")
    conn.execute("DROP TABLE job_old")


def get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_job(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM job WHERE id = 1").fetchone()
    if row is None:
        return None
    products = [
        dict(r) for r in conn.execute(
            "SELECT position, name, l, w, qty, rotatable FROM product "
            "ORDER BY position").fetchall()
    ]
    for p in products:
        p["rotatable"] = bool(p["rotatable"])
    stocks = [
        dict(r) for r in conn.execute(
            "SELECT position, name, l, w, qty, cost FROM stock "
            "ORDER BY position").fetchall()
    ]
    mode = row["mode"] if "mode" in row.keys() else "single"
    cfg: Dict[str, Any] = {
        "mode": mode,
        "kerf": row["kerf"],
        "max_sheets": row["max_sheets"],
        "max_states": row["max_states"],
        "products": products,
        "stocks": stocks,
    }
    if mode == "single":
        cfg["paper_l"] = row["paper_l"]
        cfg["paper_w"] = row["paper_w"]
    return {"cfg": cfg, "input_hash": row["input_hash"],
            "updated_at": row["updated_at"]}


def save_job(conn: sqlite3.Connection, cfg: Dict[str, Any],
             input_hash: str) -> None:
    """整体替换工单输入（事务由调用方管理）。

    任何修改输入的保存都会令旧方案失效：方案是否有效以其 input_hash
    是否等于工单当前 input_hash 判定。
    """
    stock_mode = bool(cfg.get("stocks"))
    conn.execute(
        "INSERT INTO job(id, mode, paper_l, paper_w, kerf, max_sheets, "
        "max_states, input_hash, updated_at) "
        "VALUES (1, ?, ?, ?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(id) DO UPDATE SET "
        "mode=excluded.mode, paper_l=excluded.paper_l, "
        "paper_w=excluded.paper_w, kerf=excluded.kerf, "
        "max_sheets=excluded.max_sheets, "
        "max_states=excluded.max_states, input_hash=excluded.input_hash, "
        "updated_at=datetime('now')",
        ("stock" if stock_mode else "single",
         None if stock_mode else cfg["paper_l"],
         None if stock_mode else cfg["paper_w"],
         cfg["kerf"], cfg["max_sheets"], cfg["max_states"], input_hash),
    )
    conn.execute("DELETE FROM product")
    for pos, p in enumerate(cfg["products"]):
        conn.execute(
            "INSERT INTO product(position, name, l, w, qty, rotatable) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (pos, p["name"], p["l"], p["w"], p["qty"],
             1 if p["rotatable"] else 0),
        )
    conn.execute("DELETE FROM stock")
    for pos, s in enumerate(cfg.get("stocks", [])):
        conn.execute(
            "INSERT INTO stock(position, name, l, w, qty, cost) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (pos, s["name"], s["l"], s["w"], s["qty"], s["cost"]),
        )


def get_solution(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM solution WHERE id = 1").fetchone()
    if row is None:
        return None
    return {
        "status": row["status"],
        "input_hash": row["input_hash"],
        "result": json.loads(row["result_json"]),
        "states_searched": row["states_searched"],
        "sheets_used": row["sheets_used"],
        "created_at": row["created_at"],
    }


def save_solution(conn: sqlite3.Connection, status: str, input_hash: str,
                  result: Dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO solution(id, status, input_hash, result_json, "
        "states_searched, sheets_used, created_at) "
        "VALUES (1, ?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(id) DO UPDATE SET "
        "status=excluded.status, input_hash=excluded.input_hash, "
        "result_json=excluded.result_json, "
        "states_searched=excluded.states_searched, "
        "sheets_used=excluded.sheets_used, created_at=datetime('now')",
        (status, input_hash, json.dumps(result, ensure_ascii=False),
         result.get("states_searched", 0), result.get("sheets_used", 0)),
    )


def clear_solution(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM solution WHERE id = 1")


def solution_is_fresh(conn: sqlite3.Connection) -> bool:
    """方案是否对应当前输入版本（否则即为失效方案，拒绝导出）。"""
    job = conn.execute("SELECT input_hash FROM job WHERE id = 1").fetchone()
    sol = conn.execute(
        "SELECT input_hash FROM solution WHERE id = 1").fetchone()
    return job is not None and sol is not None and \
        job["input_hash"] == sol["input_hash"]
