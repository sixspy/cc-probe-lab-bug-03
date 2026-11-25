"""失败测甲（x）：同展示号、异业务号连灌，后票不得被静默吞。

硬推理约束：
- 幂等键须钉业务报文号，展示流水号只是留存列；
- 两笔不同业务号即便同展示号也必须各自成行，现行行数须可区分；
- 半修只改展示文案不改幂等字段时，本文件仍红。
"""
from app.services import 装载, 目录, 校验, 夹具
from app.db import 连接


def _票(业务号: str, 展示号: str, 站点: str = "站-河东") -> dict:
    return {
        "报文编号": 业务号,
        "展示流水号": 展示号,
        "站点键": 站点,
        "指标键": "PM25",
        "读数": 22.0,
        "观测时": "2026-11-24T11:00:00Z",
    }


def _批(*票) -> dict:
    return {"批次编号": "测甲批", "报文": list(票)}


def test_甲_同展示号异业务号连灌现行两行():
    报告 = 装载.装一批(_批(_票("biz-x-1", "SHOW-X"), _票("biz-x-2", "SHOW-X", "站-北岭")))
    assert 报告["驳回"] == 0, f"后票被吞：{报告}"
    assert 报告["入流"] == 2
    assert 报告["入栏"] == 2

    conn = 连接()
    try:
        现行 = conn.execute(
            "SELECT 报文编号, 展示流水号 FROM 观测报文 WHERE 展示流水号=? ORDER BY 报文编号",
            ("SHOW-X",),
        ).fetchall()
        assert len(现行) == 2, "现行只一行 → 后票被静默吞"
        assert {r["报文编号"] for r in 现行} == {"biz-x-1", "biz-x-2"}
        缓冲 = conn.execute(
            "SELECT 报文编号 FROM 入栏缓冲 WHERE 展示流水号=?", ("SHOW-X",)
        ).fetchall()
        assert {r["报文编号"] for r in 缓冲} == {"biz-x-1", "biz-x-2"}
    finally:
        conn.close()


def test_甲_夹具同展示号异业务号灌完两行可区分():
    报告 = 装载.装一批(夹具.读取("同展示号异业务号样例"))
    assert 报告["入流"] == 2
    报文 = {r["报文编号"]: r for r in 目录.报文列表()}
    assert set(报文) == {"biz-dup-a", "biz-dup-b"}
    assert all(r["展示流水号"] == "SHOW-DUP-1" for r in 报文.values())
    assert 报文["biz-dup-a"]["站点键"] != 报文["biz-dup-b"]["站点键"]


def test_甲_同业务号重灌才驳回且幂等键源标注业务号():
    体 = _批(_票("biz-y-1", "SHOW-Y"))
    assert 装载.装一批(体)["入栏"] == 1
    再 = 装载.装一批(体)
    assert 再["驳回"] == 1
    幂等明细 = [m for m in 再["明细"] if m["命运"] == "驳回-幂等"]
    assert 幂等明细 and 幂等明细[0]["幂等键源"] == "业务报文号"
    assert 幂等明细[0]["报文编号"] == "biz-y-1"


def test_甲_同业务号换展示号不得回刷占坑():
    装载.装一批(_批(_票("biz-y-2", "SHOW-OLD")))
    回刷 = 装载.装一批(_批(_票("biz-y-2", "SHOW-NEW")))
    assert 回刷["驳回"] == 1
    conn = 连接()
    try:
        r = conn.execute(
            "SELECT 展示流水号 FROM 观测报文 WHERE 报文编号=?", ("biz-y-2",)
        ).fetchone()
        assert r["展示流水号"] == "SHOW-OLD", "展示号回刷了已占坑业务号"
    finally:
        conn.close()


def test_甲_同展示号异业务号含坏点时两轨各得其所():
    好 = _票("biz-z-1", "SHOW-Z")
    坏 = _票("biz-z-2", "SHOW-Z", "站-北岭")
    坏["读数"] = 999.0  # PM25 越界 → 坏点匣
    报告 = 装载.装一批(_批(好, 坏))
    assert 报告["入栏"] == 1
    assert 报告["坏点"] == 1
    conn = 连接()
    try:
        匣 = conn.execute(
            "SELECT 报文编号, 展示流水号 FROM 坏点匣 WHERE 展示流水号=?", ("SHOW-Z",)
        ).fetchall()
        assert [r["报文编号"] for r in 匣] == ["biz-z-2"]
        总 = conn.execute("SELECT COUNT(*) AS c FROM 观测报文").fetchone()["c"]
        assert 总 == 2
    finally:
        conn.close()


def test_甲_批次校验仍按业务报文号查重():
    体 = _批(_票("biz-same", "SHOW-1"), _票("biz-same", "SHOW-2"))
    错 = 校验.校验批次(体)
    assert any("报文编号重复" in e for e in 错)
