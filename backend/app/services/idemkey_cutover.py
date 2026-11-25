"""幂等键源切流账册：业务报文号占坑、耐久指纹、拒展示号回刷。"""
from __future__ import annotations

from app.db import 连接
from app.services import idemkey as core

_SLUG = "idemkey"
_TITLE = "幂等键源"
_OK = "幂等已亮"


def 执行切流(conn=None) -> dict:
    """把读轨切到账册并推进耐久指纹；可复用外部连接/事务。"""
    自有 = conn is None
    conn = conn or 连接()
    try:
        core._ensure(conn)
        core._set(conn, f"{_SLUG}_panel_chip", _OK)
        core._set(conn, f"{_SLUG}_truth", _OK)
        core._set(conn, f"{_SLUG}_active_read", core._CUTOVER)
        core._set(conn, f"{_SLUG}_reject_refresh", "1")
        core._set(conn, f"{_SLUG}_key_source", core._KEY_SOURCE)
        fp = core.推进切流指纹(conn)
        if 自有:
            conn.commit()
        return {
            "panel": _OK,
            "truth": core._OK,
            "active_read": core._CUTOVER,
            "cutover_fp": fp,
            "reject_refresh": True,
            "幂等键源": core._KEY_SOURCE,
        }
    finally:
        if 自有:
            conn.close()


def append_cutover(ref_id: int, note: str = "") -> dict:
    conn = 连接()
    try:
        out = 执行切流(conn)
        core._set(conn, f"{_SLUG}_cutover_note_{ref_id}", note or _TITLE)
        conn.commit()
    finally:
        conn.close()
    return {"ref_id": ref_id, "note": note, **out}


def board() -> dict:
    dash = core.dashboard()
    dash["fate"] = "cutover_book"
    return dash
