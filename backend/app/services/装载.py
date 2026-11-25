"""报文装载 → 入栏或坏点。

幂等落点（切流账册形态）：
- 幂等键钉业务报文号 ev["报文编号"]，在 幂等账册 以主键原子占坑；
- 展示流水号仅作非唯一留存列，任何同业务号的展示号回刷一律拒绝；
- 观测报文/入栏缓冲/坏点匣均携带展示流水号与幂等键源，现行读业务号集合。
"""
from __future__ import annotations
import hashlib
import json
import uuid
from app.db import 连接
from app.seed import 此刻
from app.services import 校验, 目录, 时间

幂等键源 = "业务报文号"


def _批次号(体: dict) -> str:
    前 = str(体.get("批次编号") or "探批").strip() or "探批"
    return f"{前}-{uuid.uuid4().hex[:8]}"


def _指纹(业务号: str, ev: dict) -> str:
    料 = "|".join(str(ev.get(k, "")) for k in ("报文编号", "站点键", "指标键", "读数", "观测时"))
    return hashlib.sha1(料.encode("utf-8")).hexdigest()[:16]


def 装一批(体: dict) -> dict:
    批次 = _批次号(体 or {})
    报文 = (体 or {}).get("报文") or []
    报告 = {"批次编号": 批次, "入流": 0, "入栏": 0, "坏点": 0, "驳回": 0, "明细": [],
            "幂等键源": 幂等键源}
    预检 = 校验.校验批次(体 or {})
    if 预检:
        报告["明细"].append({"命运": "批次预检", "说明": 校验.摘要错误(预检)})
    规 = 目录.栏规()
    栏号 = 规.get("当前栏号", "B-demo")
    站集合 = {s["源键"] for s in 目录.站点列表()}
    仓时 = 此刻()
    conn = 连接()
    try:
        for ev in 报文:
            行错 = 校验.校验报文(ev)
            if 行错:
                报告["驳回"] += 1
                报告["明细"].append({"命运": "驳回-校验", "说明": 校验.摘要错误(行错)})
                continue
            try:
                时间.解析时(ev["观测时"])
            except Exception:
                报告["驳回"] += 1
                报告["明细"].append({"命运": "驳回-观测时", "说明": ev.get("观测时")})
                continue
            # 幂等键钉业务报文号；展示流水号仅留存
            业务号 = str(ev["报文编号"])
            展示号 = str(ev.get("展示流水号") or "")
            if ev["站点键"] not in 站集合:
                报告["驳回"] += 1
                报告["明细"].append({"命运": "驳回-未知站点", "说明": ev["站点键"],
                                      "报文编号": 业务号, "幂等键源": 幂等键源})
                continue
            # 原子占坑：业务号为主键，INSERT OR IGNORE 并发下只有一笔赢
            指纹 = _指纹(业务号, ev)
            占 = conn.execute(
                "INSERT OR IGNORE INTO 幂等账册(报文编号,展示流水号,指纹,占坑时,批次编号) "
                "VALUES (?,?,?,?,?)",
                (业务号, 展示号, 指纹, 仓时, 批次),
            )
            if 占.rowcount == 0:
                报告["驳回"] += 1
                报告["明细"].append({
                    "命运": "驳回-幂等", "说明": 业务号,
                    "报文编号": 业务号, "展示流水号": 展示号, "幂等键源": 幂等键源,
                })
                continue
            读数 = float(ev["读数"])
            漂 = 时间.漂移秒(ev["观测时"], 仓时)
            conn.execute(
                """INSERT INTO 观测报文(报文编号,展示流水号,站点键,指标键,读数,观测时,处理时,命运,批次编号)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (业务号, 展示号, ev["站点键"], ev["指标键"], 读数,
                 ev["观测时"], 仓时, "已装载", 批次),
            )
            报告["入流"] += 1
            ok, 原因 = 校验.过门槛(读数, 规, ev["指标键"])
            if ok:
                conn.execute(
                    """INSERT INTO 入栏缓冲(报文编号,展示流水号,站点键,指标键,读数,观测时,栏号,命运,入栏时)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (业务号, 展示号, ev["站点键"], ev["指标键"], 读数,
                     ev["观测时"], 栏号, "在栏", 仓时),
                )
                conn.execute("UPDATE 观测报文 SET 命运=? WHERE 报文编号=?", ("已入栏", 业务号))
                报告["入栏"] += 1
                报告["明细"].append({
                    "命运": "入栏",
                    "报文编号": 业务号,
                    "展示流水号": 展示号,
                    "幂等键源": 幂等键源,
                    "栏号": 栏号,
                    "漂移秒": 漂,
                    "受控": 校验.指标是否受控(ev["指标键"]),
                })
            else:
                conn.execute(
                    """INSERT INTO 坏点匣(报文编号,展示流水号,站点键,原因,读数,观测时,入匣时)
                       VALUES (?,?,?,?,?,?,?)""",
                    (业务号, 展示号, ev["站点键"], 原因, 读数, ev["观测时"], 仓时),
                )
                conn.execute("UPDATE 观测报文 SET 命运=? WHERE 报文编号=?", ("已坏点", 业务号))
                报告["坏点"] += 1
                报告["明细"].append({"命运": "坏点", "报文编号": 业务号,
                                      "展示流水号": 展示号, "幂等键源": 幂等键源,
                                      "原因": 原因})
        conn.execute(
            """INSERT INTO 装载批次(编号,时刻,入流,入栏,坏点,报告) VALUES (?,?,?,?,?,?)""",
            (批次, 仓时, 报告["入流"], 报告["入栏"], 报告["坏点"], json.dumps(报告, ensure_ascii=False)),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return 报告
