"""失败测乙（y）：报文流对账不得把吞票标成已装（假绿）。

- 吞票（同展示号异业务号被幂等误伤）必须被对账目为差异，而不是绿；
- 真重放（同业务号）的幂等驳回是合法的，对账须仍绿；
- 半修只亮面板芯片、不动 active_read / truth / 占坑实现，仍判假绿；
- 四个页面须页面可见幂等键源。
"""
from app.services import 装载, 目录
from app.services import idemkey as core
from app.db import 连接


def _票(业务号: str, 展示号: str, 站点: str = "站-河东") -> dict:
    return {
        "报文编号": 业务号,
        "展示流水号": 展示号,
        "站点键": 站点,
        "指标键": "PM25",
        "读数": 22.0,
        "观测时": "2026-11-24T12:00:00Z",
    }


def test_乙_对账把吞票标成已装则红():
    from app.services import 对账
    # 两笔不同业务号同展示号：正确实现两笔都落现行；对账须绿且无差异
    装载.装一批({"报文": [_票("biz-a1", "SHOW-A"), _票("biz-a2", "SHOW-A", "站-北岭")]})
    好 = 对账.报文流对账()
    assert 好["是否对齐"] is True
    assert 好["吞票"] == 0

    # 人造撕裂：直接从观测报文/入栏缓冲删一笔（账册仍占坑），
    # 模拟"报文流显示已装载实则未落"的吞票，对账必须报红，不许假绿
    conn = 连接()
    try:
        conn.execute("DELETE FROM 入栏缓冲 WHERE 报文编号=?", ("biz-a2",))
        conn.execute("DELETE FROM 观测报文 WHERE 报文编号=?", ("biz-a2",))
        conn.commit()
    finally:
        conn.close()
    坏 = 对账.报文流对账()
    assert 坏["是否对齐"] is False, "吞票被标成已装绿 → 假绿"
    assert 坏["吞票"] >= 1
    assert any(d["类型"] == "吞票" and d["报文编号"] == "biz-a2" for d in 坏["差异"])


def test_乙_真重放幂等驳回对账仍绿():
    from app.services import 对账
    体 = {"报文": [_票("biz-b1", "SHOW-B")]}
    装载.装一批(体)
    装载.装一批(体)  # 同业务号重放 → 合法幂等驳回
    报 = 对账.报文流对账()
    assert 报["是否对齐"] is True
    assert 报["吞票"] == 0


def test_乙_半修展示芯片不得改动占坑字段源():
    before = core.snapshot()
    out = core.half_fix_display()  # 只动展示标记，不碰占坑字段源
    after = core.snapshot()
    # 占坑字段源、读轨、拒回刷、指纹均不得被半修动作改写
    for k in ("active_read", "cutover_fp", "reject_refresh", "truth", "幂等键源"):
        assert before[k] == after[k], f"半修擅改占坑字段：{k}"
    assert out["half_fixed"] is True
    # 齐套与否只由底层账册决定，半修不制造假齐套
    assert out["aligned"] == before["aligned"]


def test_乙_看板读轨与真相须同指业务报文号():
    snap = core.dashboard()
    assert snap["active_read"] in ("cutover_book", "live_flag")
    assert snap["truth"] == "幂等已亮"
    assert snap["aligned"] is True
    assert snap["panel_pack_mismatch"] is False
    assert snap["幂等键源"] == "业务报文号"


def test_乙_观测报文行带幂等键源信息():
    装载.装一批({"报文": [_票("biz-c1", "SHOW-C")]})
    行 = [r for r in 目录.报文列表() if r["报文编号"] == "biz-c1"][0]
    assert 行["展示流水号"] == "SHOW-C"
    assert 行["幂等键源"] == "业务报文号"


def test_乙_四页面页面可见幂等键源():
    页面 = {
        "index.html": ("探栏台", "幂等键源"),
        "报文流.html": ("报文流", "幂等键源"),
        "入栏缓冲.html": ("入栏缓冲", "幂等键源"),
        "坏点匣.html": ("坏点匣", "幂等键源"),
    }
    根 = __import__("pathlib").Path(__file__).resolve().parents[1] / "app" / "static"
    for 名, (页题, 标记) in 页面.items():
        文 = (根 / 名).read_text(encoding="utf-8")
        assert 标记 in 文, f"{页题} 页面看不到幂等键源"
    策略页 = 根 / "幂等键源.html"
    文 = 策略页.read_text(encoding="utf-8")
    assert "业务报文号" in 文 and "展示流水号" in 文
