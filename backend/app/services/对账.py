"""报文流对账：账册占坑 vs 现行（观测报文/入栏缓冲/坏点匣）。

假绿防线：
- 账册已占坑但现行缺行 = 吞票（报文流可能显示已装、实则未落），判红；
- 同展示流水号挂多个业务号是合法的，不算差异；
- 同业务号重放被幂等驳回（账册与现行都在）是合法幂等，保持绿。
"""
from __future__ import annotations

from app.db import 连接
from app.services import idemkey as core


def 报文流对账() -> dict:
    conn = 连接()
    差异: list[dict] = []
    账册: dict[str, dict] = {}
    现行: dict[str, dict] = {}
    缓冲号: set[str] = set()
    坏点号: set[str] = set()
    try:
        账册 = {
            r["报文编号"]: dict(r)
            for r in conn.execute("SELECT * FROM 幂等账册").fetchall()
        }
        现行 = {
            r["报文编号"]: dict(r)
            for r in conn.execute("SELECT 报文编号,展示流水号 FROM 观测报文").fetchall()
        }
        缓冲号 = {r["报文编号"] for r in conn.execute("SELECT 报文编号 FROM 入栏缓冲").fetchall()}
        坏点号 = {r["报文编号"] for r in conn.execute("SELECT 报文编号 FROM 坏点匣").fetchall()}

        # 1) 吞票：账册占了坑，现行却没有
        for 业务号, 占 in 账册.items():
            if 业务号 not in 现行:
                差异.append({
                    "类型": "吞票",
                    "报文编号": 业务号,
                    "展示流水号": 占.get("展示流水号", ""),
                    "说明": "账册已按业务报文号占坑，但观测报文无此行",
                })
                continue
            # 2) 现行在，但既没入栏也没进坏点匣（命运落不了地）
            if 业务号 not in 缓冲号 and 业务号 not in 坏点号:
                差异.append({
                    "类型": "悬空",
                    "报文编号": 业务号,
                    "展示流水号": 现行[业务号].get("展示流水号", ""),
                    "说明": "报文已装载但未入栏也未入坏点匣",
                })
            # 3) 展示号回刷：现行展示号与账册占坑不一致
            if 现行[业务号].get("展示流水号", "") != 占.get("展示流水号", ""):
                差异.append({
                    "类型": "展示号回刷",
                    "报文编号": 业务号,
                    "说明": "账册留存展示号与现行不一致，疑似回刷",
                })

        # 4) 现行有行但账册没占坑（绕过账册的野行）
        for 业务号 in 现行:
            if 业务号 not in 账册:
                差异.append({
                    "类型": "无占坑",
                    "报文编号": 业务号,
                    "说明": "现行有报文但幂等账册无占坑记录",
                })
    finally:
        conn.close()

    snap = core.snapshot()
    return {
        "是否对齐": not 差异,
        "幂等键源": snap.get("幂等键源", core.key_source()),
        "active_read": snap.get("active_read"),
        "账册笔数": len(账册),
        "现行笔数": len(现行),
        "吞票": sum(1 for d in 差异 if d["类型"] == "吞票"),
        "差异": 差异,
    }
