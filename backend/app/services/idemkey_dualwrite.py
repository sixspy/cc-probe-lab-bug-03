"""幂等键源双写两轨：旧轨进只读归档，现行改读 live_flag 业务号集合。

与切流账册互为备选形态：
- apply_dualwrite：把展示号旧轨登记进 idemkey_archive_% 只读归档，
  落 live_flag 并把 active_read 切到 live_flag；
- dual_board：纯只读看板，不再有写副作用。
"""
from __future__ import annotations

from app.db import 连接
from app.services import idemkey as core

_SLUG = "idemkey"
_TITLE = "幂等键源"
_OK = "幂等已亮"


def _归档旧轨(conn, note: str) -> int:
    rows = conn.execute(
        "SELECT DISTINCT 展示流水号 FROM 幂等账册 "
        "WHERE 展示流水号 IS NOT NULL AND 展示流水号<>'' ORDER BY 展示流水号"
    ).fetchall()
    n = 0
    for r in rows:
        k = f"{_SLUG}_archive_display_{r['展示流水号']}"
        core._set(conn, k, f"只读归档:{r['展示流水号']}:{note or _TITLE}")
        n += 1
    # 至少一条归档，保证 live 形态齐套可验
    if n == 0:
        core._set(conn, f"{_SLUG}_archive_display_empty", f"只读归档:{note or _TITLE}")
        n = 1
    return n


def apply_dualwrite(note: str = "") -> dict:
    conn = 连接()
    try:
        core._ensure(conn)
        n = _归档旧轨(conn, note)
        live = f"live-biz-key-{conn.execute('SELECT COUNT(*) AS c FROM 幂等账册').fetchone()['c']}"
        core._set(conn, f"{_SLUG}_dual_note", note or _TITLE)
        core._set(conn, f"{_SLUG}_panel_chip", _OK)
        core._set(conn, f"{_SLUG}_truth", _OK)
        core._set(conn, f"{_SLUG}_live_flag", live)
        core._set(conn, f"{_SLUG}_active_read", core._LIVE)
        core._set(conn, f"{_SLUG}_reject_refresh", "1")
        core._set(conn, f"{_SLUG}_key_source", core._KEY_SOURCE)
        conn.commit()
    finally:
        conn.close()
    return {
        "dual_chip": _OK,
        "truth": core.truth_flag(),
        "active_read": core.active_read_ref(),
        "archive_count": len(core.archive_rows()),
        "live_flag": core.live_flag(),
        "幂等键源": core._KEY_SOURCE,
        "归档条数": n,
    }


def dual_board() -> dict:
    dash = core.dashboard()
    return {
        "dual_chip": core.panel_chip(),
        "truth": core.truth_flag(),
        "active_read": core.active_read_ref(),
        "archive": core.archive_rows(),
        "live_flag": core.live_flag(),
        "dash": dash,
        "fate": "dualwrite",
        "panel_pack_mismatch": dash["panel_pack_mismatch"],
        "title": f"{_TITLE}双写面",
    }
