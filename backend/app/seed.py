from datetime import datetime, timezone
from app.db import 连接

建表 = """
CREATE TABLE IF NOT EXISTS 栏规 (
  键 TEXT PRIMARY KEY,
  值 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 站点源 (
  源键 TEXT PRIMARY KEY,
  名称 TEXT NOT NULL,
  介质 TEXT NOT NULL DEFAULT '大气',
  区域 TEXT NOT NULL DEFAULT '',
  说明 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 观测报文 (
  报文编号 TEXT PRIMARY KEY,
  展示流水号 TEXT NOT NULL DEFAULT '',
  站点键 TEXT NOT NULL,
  指标键 TEXT NOT NULL,
  读数 REAL NOT NULL,
  观测时 TEXT NOT NULL,
  处理时 TEXT NOT NULL,
  命运 TEXT NOT NULL DEFAULT '已装载',
  批次编号 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 入栏缓冲 (
  报文编号 TEXT PRIMARY KEY,
  展示流水号 TEXT NOT NULL DEFAULT '',
  站点键 TEXT NOT NULL,
  指标键 TEXT NOT NULL,
  读数 REAL NOT NULL,
  观测时 TEXT NOT NULL,
  栏号 TEXT NOT NULL,
  命运 TEXT NOT NULL DEFAULT '在栏',
  入栏时 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 幂等账册 (
  报文编号 TEXT PRIMARY KEY,
  展示流水号 TEXT NOT NULL DEFAULT '',
  指纹 TEXT NOT NULL,
  占坑时 TEXT NOT NULL,
  批次编号 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 站点汇总账 (
  汇总编号 TEXT PRIMARY KEY,
  栏号 TEXT NOT NULL,
  站点键 TEXT NOT NULL,
  指标键 TEXT NOT NULL,
  报文数 INTEGER NOT NULL,
  均值 REAL NOT NULL,
  物化时 TEXT NOT NULL,
  命运 TEXT NOT NULL DEFAULT '已汇总'
);
CREATE TABLE IF NOT EXISTS 坏点匣 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  报文编号 TEXT NOT NULL,
  展示流水号 TEXT NOT NULL DEFAULT '',
  站点键 TEXT NOT NULL,
  原因 TEXT NOT NULL,
  读数 REAL NOT NULL,
  观测时 TEXT NOT NULL,
  入匣时 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 栏贯验 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  时刻 TEXT NOT NULL,
  在栏笔数 INTEGER NOT NULL,
  汇总报文数 INTEGER NOT NULL,
  坏点笔数 INTEGER NOT NULL,
  是否对齐 INTEGER NOT NULL,
  说明 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 装载批次 (
  编号 TEXT PRIMARY KEY,
  时刻 TEXT NOT NULL,
  入流 INTEGER NOT NULL DEFAULT 0,
  入栏 INTEGER NOT NULL DEFAULT 0,
  坏点 INTEGER NOT NULL DEFAULT 0,
  报告 TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS 探栏元 (
  键 TEXT PRIMARY KEY,
  值 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 结栏批次 (
  编号 TEXT PRIMARY KEY,
  栏号 TEXT NOT NULL,
  时刻 TEXT NOT NULL,
  汇总数 INTEGER NOT NULL DEFAULT 0,
  报告 TEXT NOT NULL DEFAULT '{}'
);
"""


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _列名(conn, 表: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({表})").fetchall()}


def _补列(conn, 表: str, 列: str, 声明: str) -> None:
    if 列 not in _列名(conn, 表):
        conn.execute(f"ALTER TABLE {表} ADD COLUMN {列} {声明}")


def _迁移补展示号(conn) -> None:
    """老库平滑加列：展示号降级为非唯一留存列，幂等不再依赖它。"""
    _补列(conn, "观测报文", "展示流水号", "TEXT NOT NULL DEFAULT ''")
    _补列(conn, "入栏缓冲", "展示流水号", "TEXT NOT NULL DEFAULT ''")
    _补列(conn, "坏点匣", "展示流水号", "TEXT NOT NULL DEFAULT ''")


def _建账册回填(conn) -> None:
    """切流账册：以业务报文号占坑；老数据按业务号回填，展示号仅留存。"""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS 幂等账册("
        "报文编号 TEXT PRIMARY KEY,"
        "展示流水号 TEXT NOT NULL DEFAULT '',"
        "指纹 TEXT NOT NULL,"
        "占坑时 TEXT NOT NULL,"
        "批次编号 TEXT NOT NULL DEFAULT '')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO 幂等账册(报文编号,展示流水号,指纹,占坑时,批次编号) "
        "SELECT 报文编号, COALESCE(展示流水号,''), 'backfill:'||报文编号, "
        "COALESCE(处理时, ''), COALESCE(批次编号,'') FROM 观测报文"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_幂等账册_展示号 ON 幂等账册(展示流水号)"
    )


def 初始化库() -> None:
    conn = 连接()
    conn.executescript(建表)
    _迁移补展示号(conn)
    _建账册回填(conn)
    _seed_idemkey_chip(conn)
    if conn.execute("SELECT COUNT(*) AS c FROM 栏规").fetchone()["c"] == 0:
        for k, v in {
            "当前栏号": "B-demo",
            "PM25上限": "75",
            "PM25下限": "0",
            "标定偏移上限": "5",
            "栏策略": "按站点指标汇总均值",
        }.items():
            conn.execute("INSERT INTO 栏规(键,值) VALUES (?,?)", (k, v))
    if conn.execute("SELECT COUNT(*) AS c FROM 站点源").fetchone()["c"] == 0:
        for row in [
            ("站-河东", "河东大气站", "大气", "城区", "示范站"),
            ("站-湖西", "湖西水质站", "水质", "湖区", "示范站"),
            ("站-北岭", "北岭大气站", "大气", "近郊", "示范站"),
            ("站-江口", "江口大气站", "大气", "河口", "示范站"),
            ("站-林场", "林场微站", "大气", "林区", "示范站"),
        ]:
            conn.execute(
                "INSERT INTO 站点源(源键,名称,介质,区域,说明) VALUES (?,?,?,?,?)",
                row,
            )
    conn.commit()
    conn.close()



def _seed_idemkey_chip(conn) -> None:
    """初始化幂等键源真相：切流账册已按业务报文号占坑。

    只在键缺失时落默认值，运行期由 idemkey_cutover 推进指纹等字段。
    """
    默认 = {
        "idemkey_panel_chip": "幂等已亮",
        "idemkey_truth": "幂等已亮",
        "idemkey_active_read": "cutover_book",
        "idemkey_reject_refresh": "1",
        "idemkey_live_flag": "",
        "idemkey_key_source": "业务报文号",
    }
    for k, v in 默认.items():
        conn.execute(
            "INSERT INTO 探栏元(键,值) VALUES (?,?) ON CONFLICT(键) DO NOTHING",
            (k, v),
        )
    # 切流指纹：随账册占坑条数推进，stale-fp 不再是合法值
    条数 = conn.execute("SELECT COUNT(*) AS c FROM 幂等账册").fetchone()["c"]
    fp = f"cutover-fp-{条数}"
    conn.execute(
        "INSERT INTO 探栏元(键,值) VALUES (?,?) ON CONFLICT(键) DO NOTHING",
        ("idemkey_cutover_fp", fp),
    )
