"""争用失败测（z）：双会话同展示号异业务号并发，不得互吞。

- SQLite 须开 WAL + busy_timeout，两个独立连接并发装载；
- 占坑以业务报文号为唯一键，同展示号异业务号两笔都要落库；
- 同业务号并发只允许一笔赢，另一笔合法幂等驳回（不允许双写或异常炸穿）。
"""
import sqlite3
import threading

from app.services import 装载, 目录
from app.db import 连接


def _票(业务号: str, 展示号: str, 站点: str = "站-河东") -> dict:
    return {
        "报文编号": 业务号,
        "展示流水号": 展示号,
        "站点键": 站点,
        "指标键": "PM25",
        "读数": 22.0,
        "观测时": "2026-11-24T13:00:00Z",
    }


def test_争_双会话同展示号异业务号并发不互吞():
    结果: list[dict] = []
    错: list[BaseException] = []
    闸 = threading.Barrier(2)

    def 灌(业务号: str, 站点: str) -> None:
        try:
            闸.wait()
            结果.append(装载.装一批({"报文": [_票(业务号, "SHOW-RACE", 站点)]}))
        except BaseException as e:  # 锁错误也算红
            错.append(e)

    t1 = threading.Thread(target=灌, args=("biz-race-1", "站-河东"))
    t2 = threading.Thread(target=灌, args=("biz-race-2", "站-北岭"))
    t1.start(); t2.start(); t1.join(10); t2.join(10)
    assert not 错, f"并发装载炸穿：{错}"
    assert sum(r["入流"] for r in 结果) == 2, f"并发互吞：{结果}"
    assert sum(r["驳回"] for r in 结果) == 0
    报文 = {r["报文编号"] for r in 目录.报文列表()}
    assert 报文 == {"biz-race-1", "biz-race-2"}
    缓冲 = {r["报文编号"] for r in 目录.缓冲列表()}
    assert 缓冲 == {"biz-race-1", "biz-race-2"}


def test_争_同业务号并发只有一笔赢():
    结果: list[dict] = []
    错: list[BaseException] = []
    闸 = threading.Barrier(2)

    def 灌(展示号: str) -> None:
        try:
            闸.wait()
            结果.append(装载.装一批({"报文": [_票("biz-race-same", 展示号)]}))
        except BaseException as e:
            错.append(e)

    t1 = threading.Thread(target=灌, args=("SHOW-S1",))
    t2 = threading.Thread(target=灌, args=("SHOW-S2",))
    t1.start(); t2.start(); t1.join(10); t2.join(10)
    assert not 错, f"并发同业务号炸穿：{错}"
    assert sum(r["入流"] for r in 结果) == 1
    assert sum(r["驳回"] for r in 结果) == 1
    conn = 连接()
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM 观测报文 WHERE 报文编号=?", ("biz-race-same",)
        ).fetchone()["c"]
        assert n == 1
    finally:
        conn.close()


def test_争_账册表以业务报文号为唯一占坑键():
    装载.装一批({"报文": [_票("biz-led-1", "SHOW-LED"),
                          _票("biz-led-2", "SHOW-LED", "站-北岭")]})
    conn = 连接()
    try:
        # 同展示号在账册里应有两条占坑 → 展示号不是唯一键
        同展示 = conn.execute(
            "SELECT COUNT(*) AS c FROM 幂等账册 WHERE 展示流水号=?", ("SHOW-LED",)
        ).fetchone()["c"]
        assert 同展示 == 2
        业务 = conn.execute(
            "SELECT COUNT(*) AS c FROM 幂等账册 WHERE 报文编号 IN (?,?)",
            ("biz-led-1", "biz-led-2"),
        ).fetchone()["c"]
        assert 业务 == 2
        # 表约束层：业务号唯一，展示号不唯一
        约束 = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='幂等账册'"
        ).fetchone()["sql"]
        assert "报文编号" in 约束 and "PRIMARY KEY" in 约束
        再占 = conn.execute(
            "INSERT OR IGNORE INTO 幂等账册(报文编号,展示流水号,指纹,占坑时) "
            "VALUES (?,?,?,?)",
            ("biz-led-1", "SHOW-回刷", "fp-x", "t"),
        )
        assert 再占.rowcount == 0, "展示号回刷应被账册拒绝"
    finally:
        conn.close()


def test_争_连接开启WAL与忙等():
    conn = 连接()
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        assert int(conn.execute("PRAGMA busy_timeout").fetchone()[0]) >= 3000
    finally:
        conn.close()
