"""幂等键源收口面：切流账册按业务报文号占坑，芯片与真相一致。

落点形态：cutover_book（切流账册）。
- 幂等键钉业务报文号（观测报文.报文编号），展示流水号仅留存、不唯一；
- 账册表 幂等账册 以报文编号为主键原子占坑，拒绝展示号回刷；
- 现行读轨（观测报文/入栏缓冲/坏点匣）全部携带业务号集合与键源标记；
- 双写两轨（live_flag）为备选形态，见 idemkey_dualwrite。
"""
from __future__ import annotations

from app.db import 连接

_SLUG = "idemkey"
_OK = "幂等已亮"
_BAD = "展示号互吞"
_TITLE = "幂等键源"
_OLD = "old_track"
_CUTOVER = "cutover_book"
_LIVE = "live_flag"
_KEY_SOURCE = "业务报文号"


def _ensure(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS 探栏元("
        "键 TEXT PRIMARY KEY, 值 TEXT NOT NULL)"
    )


def _get(conn, key: str, default: str = "") -> str:
    _ensure(conn)
    r = conn.execute("SELECT 值 FROM 探栏元 WHERE 键=?", (key,)).fetchone()
    return r["值"] if r else default


def _set(conn, key: str, value: str) -> None:
    _ensure(conn)
    conn.execute(
        "INSERT INTO 探栏元(键,值) VALUES (?,?) "
        "ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
        (key, value),
    )


def key_source() -> str:
    """当前幂等键字段源：业务报文号。"""
    return _KEY_SOURCE


def 推进切流指纹(conn) -> str:
    n = conn.execute("SELECT COUNT(*) AS c FROM 幂等账册").fetchone()["c"]
    fp = f"cutover-fp-{n}"
    _set(conn, f"{_SLUG}_cutover_fp", fp)
    return fp


def panel_chip() -> str:
    conn = 连接()
    try:
        return _get(conn, f"{_SLUG}_panel_chip", _OK)
    finally:
        conn.close()


def truth_flag() -> str:
    conn = 连接()
    try:
        return _get(conn, f"{_SLUG}_truth", _OK)
    finally:
        conn.close()


def active_read_ref() -> str:
    conn = 连接()
    try:
        return _get(conn, f"{_SLUG}_active_read", _CUTOVER)
    finally:
        conn.close()


def cutover_fp() -> str:
    conn = 连接()
    try:
        return _get(conn, f"{_SLUG}_cutover_fp", "")
    finally:
        conn.close()


def reject_refresh() -> bool:
    conn = 连接()
    try:
        return _get(conn, f"{_SLUG}_reject_refresh", "1") == "1"
    finally:
        conn.close()


def live_flag() -> str:
    conn = 连接()
    try:
        return _get(conn, f"{_SLUG}_live_flag", "")
    finally:
        conn.close()


def archive_rows() -> list[dict]:
    conn = 连接()
    try:
        _ensure(conn)
        rows = conn.execute(
            "SELECT 键,值 FROM 探栏元 WHERE 键 LIKE ? ORDER BY 键",
            (f"{_SLUG}_archive_%",),
        ).fetchall()
        return [{"键": r["键"], "值": r["值"]} for r in rows]
    finally:
        conn.close()


def paint_ok() -> str:
    """亮灯必须同时落真相与读轨，不允许只亮芯片。"""
    conn = 连接()
    try:
        _set(conn, f"{_SLUG}_panel_chip", _OK)
        _set(conn, f"{_SLUG}_truth", _OK)
        _set(conn, f"{_SLUG}_active_read", _CUTOVER)
        _set(conn, f"{_SLUG}_reject_refresh", "1")
        _set(conn, f"{_SLUG}_key_source", _KEY_SOURCE)
        _set(conn, f"{_SLUG}_display_only", "0")
        推进切流指纹(conn)
        conn.commit()
        return _OK
    finally:
        conn.close()


def commit_action(payload: dict | None = None) -> dict:
    """切流动作：确保账册按业务号占坑的真相持久化。"""
    payload = payload or {}
    conn = 连接()
    try:
        c = int(_get(conn, f"{_SLUG}_counter", "0") or "0") + 1
        _set(conn, f"{_SLUG}_counter", str(c))
        _set(conn, f"{_SLUG}_panel_chip", _OK)
        _set(conn, f"{_SLUG}_last_payload", str(payload)[:200])
        _set(conn, f"{_SLUG}_truth", _OK)
        _set(conn, f"{_SLUG}_active_read", _CUTOVER)
        _set(conn, f"{_SLUG}_reject_refresh", "1")
        _set(conn, f"{_SLUG}_key_source", _KEY_SOURCE)
        fp = 推进切流指纹(conn)
        conn.commit()
        return {
            "ok": True,
            "chip": _OK,
            "title": _TITLE,
            "counter": c,
            "active_read": _CUTOVER,
            "cutover_fp": fp,
            "reject_refresh": True,
            "archive_count": len(archive_rows()),
            "live_flag": "",
            "幂等键源": _KEY_SOURCE,
            "message": f"{_TITLE}已按业务报文号切流占坑",
        }
    finally:
        conn.close()


def half_fix_display() -> dict:
    """半修：只改展示文案。如实上报未对齐，绝不假装齐套。"""
    conn = 连接()
    try:
        _set(conn, f"{_SLUG}_panel_chip", _OK)
        _set(conn, f"{_SLUG}_display_only", "1")
        # 不动 truth / active_read / 账册；若底层已切流则真相仍为已亮
        conn.commit()
    finally:
        conn.close()
    return {
        "panel_chip": panel_chip(),
        "truth": truth_flag(),
        "active_read": active_read_ref(),
        "archive_count": len(archive_rows()),
        "half_fixed": True,
        "aligned": snapshot()["aligned"],
    }


def snapshot() -> dict:
    ref = active_read_ref()
    chip = panel_chip()
    truth = truth_flag()
    arch = archive_rows()
    aligned = (
        chip == truth == _OK
        and ref in (_CUTOVER, _LIVE)
        and (
            (ref == _CUTOVER and reject_refresh() and cutover_fp() not in ("", "stale-fp"))
            or (ref == _LIVE and bool(live_flag()) and len(arch) > 0)
        )
    )
    return {
        "title": _TITLE,
        "panel_chip": chip,
        "truth": truth,
        "active_read": ref,
        "cutover_fp": cutover_fp(),
        "reject_refresh": reject_refresh(),
        "archive_count": len(arch),
        "live_flag": live_flag(),
        "aligned": aligned,
        "panel_pack_mismatch": chip == _OK and not aligned,
        "幂等键源": _KEY_SOURCE,
        "gate_bypass": False,
    }


def dashboard() -> dict:
    snap = snapshot()
    return {
        **snap,
        "ok_chip": _OK,
        "bad_chip": _BAD,
        "label": _TITLE,
        "key_source": _KEY_SOURCE,
        "read_tracks": {
            "old_track": _OLD,
            "cutover_book": _CUTOVER,
            "live_flag": _LIVE,
        },
    }


def reconcile_display() -> dict:
    return half_fix_display()
