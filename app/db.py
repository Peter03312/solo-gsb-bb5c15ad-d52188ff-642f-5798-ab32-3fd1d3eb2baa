"""SQLite 持久化：单行工单、成品行、最近一次方案与失效标记。"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")


def init_db(db_path: str) -> None:
    """幂等建表。"""
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()
    finally:
        conn.close()


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
    cfg = {
        "paper_l": row["paper_l"],
        "paper_w": row["paper_w"],
        "kerf": row["kerf"],
        "max_sheets": row["max_sheets"],
        "max_states": row["max_states"],
        "products": products,
    }
    return {"cfg": cfg, "input_hash": row["input_hash"],
            "updated_at": row["updated_at"]}


def save_job(conn: sqlite3.Connection, cfg: Dict[str, Any],
             input_hash: str) -> None:
    """整体替换工单输入（事务由调用方管理）。

    任何修改输入的保存都会令旧方案失效：方案是否有效以其 input_hash
    是否等于工单当前 input_hash 判定。
    """
    conn.execute(
        "INSERT INTO job(id, paper_l, paper_w, kerf, max_sheets, "
        "max_states, input_hash, updated_at) "
        "VALUES (1, ?, ?, ?, ?, ?, ?, datetime('now')) "
        "ON CONFLICT(id) DO UPDATE SET "
        "paper_l=excluded.paper_l, paper_w=excluded.paper_w, "
        "kerf=excluded.kerf, max_sheets=excluded.max_sheets, "
        "max_states=excluded.max_states, input_hash=excluded.input_hash, "
        "updated_at=datetime('now')",
        (cfg["paper_l"], cfg["paper_w"], cfg["kerf"],
         cfg["max_sheets"], cfg["max_states"], input_hash),
    )
    conn.execute("DELETE FROM product")
    for pos, p in enumerate(cfg["products"]):
        conn.execute(
            "INSERT INTO product(position, name, l, w, qty, rotatable) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (pos, p["name"], p["l"], p["w"], p["qty"],
             1 if p["rotatable"] else 0),
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
