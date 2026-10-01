"""严格输入校验与输入版本指纹。

规则：
- 母纸长/宽：正整数毫米（1..MAX_DIM）
- 刀缝：非负整数毫米（0..MAX_KERF）
- 张数上限、搜索状态数上限：正整数（1..对应上限）
- 成品：至少 1 种；长/宽正整数，份数正整数，rotatable 为布尔
- 布尔值不接受 int 伪装（Python 中 True 是 int 的子类）

校验失败抛出 ValidationError(字段名, 原因)，任何调用方都不得把非法
输入写入数据库。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Tuple

MAX_DIM = 10_000          # 毫米，单边长上限（小作坊足够）
MAX_KERF = 100
MAX_SHEETS = 10_000
MAX_STATES = 100_000_000
MAX_PRODUCT_KINDS = 50
MAX_NAME = 40
MAX_QTY = 1_000_000


class ValidationError(ValueError):
    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _check_int(value: Any, field: str, lo: int, hi: int) -> int:
    if not _is_int(value):
        raise ValidationError(field, "必须是整数")
    if value < lo or value > hi:
        raise ValidationError(field, f"必须在 {lo}..{hi} 之间")
    return value


def validate_input(payload: Dict[str, Any]) -> Dict[str, Any]:
    """校验并返回规范化后的输入字典；非法即抛 ValidationError。"""
    if not isinstance(payload, dict):
        raise ValidationError("body", "请求体必须是 JSON 对象")

    paper_l = _check_int(payload.get("paper_l"), "paper_l", 1, MAX_DIM)
    paper_w = _check_int(payload.get("paper_w"), "paper_w", 1, MAX_DIM)
    kerf = _check_int(payload.get("kerf"), "kerf", 0, MAX_KERF)
    max_sheets = _check_int(payload.get("max_sheets"), "max_sheets",
                            1, MAX_SHEETS)
    max_states = _check_int(payload.get("max_states"), "max_states",
                            1, MAX_STATES)

    raw_products = payload.get("products")
    if not isinstance(raw_products, list) or not raw_products:
        raise ValidationError("products", "至少需要一种成品")
    if len(raw_products) > MAX_PRODUCT_KINDS:
        raise ValidationError("products", f"成品种类至多 {MAX_PRODUCT_KINDS} 种")

    products: List[dict] = []
    for idx, p in enumerate(raw_products):
        fp = f"products[{idx}]"
        if not isinstance(p, dict):
            raise ValidationError(fp, "必须是对象")
        l = _check_int(p.get("l"), f"{fp}.l", 1, MAX_DIM)
        w = _check_int(p.get("w"), f"{fp}.w", 1, MAX_DIM)
        qty = _check_int(p.get("qty"), f"{fp}.qty", 1, MAX_QTY)
        rot = p.get("rotatable")
        if not isinstance(rot, bool):
            raise ValidationError(f"{fp}.rotatable", "必须是 true/false")
        name = p.get("name") or f"成品{idx + 1}"
        if not isinstance(name, str):
            raise ValidationError(f"{fp}.name", "名称必须是字符串")
        name = name.strip()[:MAX_NAME] or f"成品{idx + 1}"
        products.append({"name": name, "l": l, "w": w,
                         "qty": qty, "rotatable": rot})

    return {
        "paper_l": paper_l,
        "paper_w": paper_w,
        "kerf": kerf,
        "max_sheets": max_sheets,
        "max_states": max_states,
        "products": products,
    }


def canonical_inputs(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """去掉名称等不影响排样的字段，得到决定方案有效性的规范输入。"""
    return {
        "paper_l": cfg["paper_l"],
        "paper_w": cfg["paper_w"],
        "kerf": cfg["kerf"],
        "max_sheets": cfg["max_sheets"],
        "max_states": cfg["max_states"],
        "products": [
            [p["l"], p["w"], p["qty"], 1 if p["rotatable"] else 0]
            for p in cfg["products"]
        ],
    }


def input_hash(cfg: Dict[str, Any]) -> str:
    """对影响排样的输入做 SHA-256，作为方案所对应的输入版本指纹。"""
    blob = json.dumps(canonical_inputs(cfg), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def input_version(h: str) -> str:
    """对外展示的短版本号（指纹前 12 位）。"""
    return h[:12]
