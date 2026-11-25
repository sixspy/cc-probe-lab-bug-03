"""幂等键源读轨审计。"""
from __future__ import annotations

from app.services import idemkey as core
from app.services import idemkey_cutover as cutover
from app.services import idemkey_dualwrite as dual


def audit_summary() -> dict:
    return {
        "title": "幂等键源审计",
        "core": core.dashboard(),
        "cutover": cutover.board(),
        "dualwrite": dual.dual_board(),
        "half": core.half_fix_display(),
    }
