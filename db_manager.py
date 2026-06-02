"""
db_manager.py — SQLite 数据库管理 & 去重工具
==============================================
功能：
- 初始化数据库表（jobs、applications、messages、task_log）
- 岗位去重（按 job_id / link）
- 存储/查询岗位、投递记录、消息、任务日志
- 用作 LangChain Tool: DataDeduplicator
"""

import sqlite3
import json
import logging
from datetime import datetime, date
from typing import Optional
from langchain.tools import tool

from config import DB_CONFIG

logger = logging.getLogger(__name__)


# ============================================================
# 数据库连接管理
# ============================================================

class DatabaseManager:
    """SQLite 数据库管理类"""

    def __init__(self, db_path: str = DB_CONFIG["path"]):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # 支持按列名访问
        conn.execute("PRAGMA journal_mode=WAL")  # 提升并发写入性能
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self):
        """初始化数据库表结构"""
        conn = self._get_conn()
        cursor = conn.cursor()

        # 岗位表：存储所有采集到的岗位信息
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT UNIQUE NOT NULL,         -- 51job岗位 ID
                keyword TEXT NOT NULL,               -- 搜索关键词
                job_name TEXT,                       -- 岗位名称
                company_name TEXT,                   -- 公司名称
                salary TEXT,                         -- 薪资范围
                city TEXT,                           -- 工作城市
                experience TEXT,                     -- 经验要求
                degree TEXT,                         -- 学历要求
                jd_text TEXT,                        -- 岗位描述（JD）
                job_link TEXT,                       -- 岗位详情链接
                publish_time TEXT,                   -- 发布时间
                match_score REAL DEFAULT 0,          -- 匹配度评分（0-10）
                match_reason TEXT,                   -- 匹配理由
                status TEXT DEFAULT 'new',           -- 状态: new/matched/applied/interview/rejected
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)

        # 投递记录表（预留：后续可扩展自动投递）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                status TEXT DEFAULT 'sent',          -- sent/viewed/replied/interview/offer/rejected
                note TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (job_id) REFERENCES jobs(job_id)
            )
        """)

        # 消息表：记录 HR 发来的消息
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                msg_id TEXT UNIQUE,                  -- 消息 ID
                sender_name TEXT,                    -- 发送者（HR 姓名）
                company_name TEXT,                   -- 公司名称
                content TEXT,                        -- 消息内容
                msg_type TEXT DEFAULT 'text',        -- 消息类型: text/interview_invite/system
                is_read INTEGER DEFAULT 0,           -- 是否已读
                has_replied INTEGER DEFAULT 0,       -- 是否已回复
                raw_data TEXT,                       -- 原始 JSON 数据
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)

        # 任务执行日志表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,             -- morning/evening
                status TEXT DEFAULT 'running',       -- running/success/failed
                summary TEXT,                        -- 执行摘要 JSON
                error_msg TEXT,                      -- 错误信息
                started_at TEXT DEFAULT (datetime('now', 'localtime')),
                finished_at TEXT
            )
        """)

        # 每日请求计数表（用于限制每日请求量）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daily_request_count (
                date TEXT PRIMARY KEY,
                count INTEGER DEFAULT 0
            )
        """)

        # 状态快照表（支持中断后继续）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS workflow_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_type TEXT NOT NULL,         -- morning/evening
                state_key TEXT NOT NULL,             -- 状态键
                state_value TEXT,                    -- 状态值（JSON）
                updated_at TEXT DEFAULT (datetime('now', 'localtime')),
                UNIQUE(workflow_type, state_key)
            )
        """)

        conn.commit()
        conn.close()
        logger.info("数据库初始化完成: %s", self.db_path)

    # ============================================================
    # 岗位 CRUD
    # ============================================================

    def is_duplicate(self, job_id: str) -> bool:
        """检查岗位是否已存在（去重）"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM jobs WHERE job_id = ?", (job_id,))
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0

    def insert_job(self, job: dict) -> bool:
        """
        插入一条岗位记录。
        返回 True 表示插入成功，False 表示已存在（去重）。
        """
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO jobs
                    (job_id, keyword, job_name, company_name, salary, city,
                     experience, degree, jd_text, job_link, publish_time, match_score, match_reason, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job["job_id"],
                job.get("keyword", ""),
                job.get("job_name", ""),
                job.get("company_name", ""),
                job.get("salary", ""),
                job.get("city", ""),
                job.get("experience", ""),
                job.get("degree", ""),
                job.get("jd_text", ""),
                job.get("job_link", ""),
                job.get("publish_time", ""),
                job.get("match_score", 0),
                job.get("match_reason", ""),
                job.get("status", "new"),
            ))
            conn.commit()
            inserted = cursor.rowcount > 0
            if inserted:
                logger.info("新岗位入库: %s - %s", job.get("job_name"), job.get("company_name"))
            return inserted
        except sqlite3.Error as e:
            logger.error("插入岗位失败: %s", e)
            return False
        finally:
            conn.close()

    def update_match_score(self, job_id: str, score: float, reason: str = ""):
        """更新岗位的匹配分数"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE jobs SET match_score = ?, match_reason = ?, status = 'matched',
                            updated_at = datetime('now', 'localtime')
            WHERE job_id = ?
        """, (score, reason, job_id))
        conn.commit()
        conn.close()

    def get_jobs_by_date(self, target_date: str = None) -> list:
        """获取指定日期的岗位列表"""
        if target_date is None:
            target_date = date.today().isoformat()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM jobs
            WHERE date(created_at) = ?
            ORDER BY match_score DESC, created_at DESC
        """, (target_date,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_matched_jobs(self, min_score: float = 7.0) -> list:
        """获取匹配度达标（≥ min_score）的岗位"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM jobs
            WHERE match_score >= ? AND date(created_at) = date('now', 'localtime')
            ORDER BY match_score DESC
        """, (min_score,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # ============================================================
    # 消息 CRUD
    # ============================================================

    def insert_message(self, msg: dict) -> bool:
        """插入一条消息记录"""
        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO messages
                    (msg_id, sender_name, company_name, content, msg_type, is_read, has_replied, raw_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                msg.get("msg_id", ""),
                msg.get("sender_name", ""),
                msg.get("company_name", ""),
                msg.get("content", ""),
                msg.get("msg_type", "text"),
                msg.get("is_read", 0),
                msg.get("has_replied", 0),
                json.dumps(msg, ensure_ascii=False),
            ))
            conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            logger.error("插入消息失败: %s", e)
            return False
        finally:
            conn.close()

    def get_messages_by_date(self, target_date: str = None) -> list:
        """获取指定日期的消息列表"""
        if target_date is None:
            target_date = date.today().isoformat()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM messages
            WHERE date(created_at) = ?
            ORDER BY created_at DESC
        """, (target_date,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_message_stats(self, target_date: str = None) -> dict:
        """获取今日消息统计"""
        if target_date is None:
            target_date = date.today().isoformat()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                COUNT(*) as total_new_replies,
                SUM(CASE WHEN is_read = 1 THEN 1 ELSE 0 END) as read_count,
                SUM(CASE WHEN msg_type = 'interview_invite' THEN 1 ELSE 0 END) as interview_count,
                SUM(CASE WHEN has_replied = 1 THEN 1 ELSE 0 END) as replied_count
            FROM messages
            WHERE date(created_at) = ?
        """, (target_date,))
        row = cursor.fetchone()
        conn.close()
        return {
            "total_new_replies": row["total_new_replies"] or 0,
            "read_count": row["read_count"] or 0,
            "interview_count": row["interview_count"] or 0,
            "replied_count": row["replied_count"] or 0,
        }

    # ============================================================
    # 任务日志
    # ============================================================

    def log_task_start(self, task_type: str) -> int:
        """记录任务开始，返回日志 ID"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO task_log (task_type, status) VALUES (?, 'running')",
            (task_type,),
        )
        conn.commit()
        log_id = cursor.lastrowid
        conn.close()
        return log_id

    def log_task_end(self, log_id: int, status: str, summary: dict = None, error_msg: str = None):
        """更新任务结束状态"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE task_log
            SET status = ?, summary = ?, error_msg = ?, finished_at = datetime('now', 'localtime')
            WHERE id = ?
        """, (status, json.dumps(summary, ensure_ascii=False) if summary else None, error_msg, log_id))
        conn.commit()
        conn.close()

    # ============================================================
    # 请求计数（每日上限）
    # ============================================================

    def increment_request_count(self) -> int:
        """增加今日请求计数，返回当前值"""
        today = date.today().isoformat()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO daily_request_count (date, count) VALUES (?, 1)
            ON CONFLICT(date) DO UPDATE SET count = count + 1
        """, (today,))
        conn.commit()
        cursor.execute("SELECT count FROM daily_request_count WHERE date = ?", (today,))
        count = cursor.fetchone()[0]
        conn.close()
        return count

    def get_today_request_count(self) -> int:
        """获取今日已发起的请求数"""
        today = date.today().isoformat()
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT count FROM daily_request_count WHERE date = ?", (today,))
        row = cursor.fetchone()
        conn.close()
        return row["count"] if row else 0

    # ============================================================
    # 工作流状态（中断恢复）
    # ============================================================

    def save_workflow_state(self, workflow_type: str, state_key: str, state_value: dict):
        """保存工作流状态快照"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO workflow_state (workflow_type, state_key, state_value, updated_at)
            VALUES (?, ?, ?, datetime('now', 'localtime'))
            ON CONFLICT(workflow_type, state_key)
            DO UPDATE SET state_value = ?, updated_at = datetime('now', 'localtime')
        """, (workflow_type, state_key, json.dumps(state_value, ensure_ascii=False),
              json.dumps(state_value, ensure_ascii=False)))
        conn.commit()
        conn.close()

    def get_workflow_state(self, workflow_type: str, state_key: str) -> Optional[dict]:
        """获取工作流状态快照"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT state_value FROM workflow_state WHERE workflow_type = ? AND state_key = ?",
            (workflow_type, state_key),
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return json.loads(row["state_value"])
        return None


# ============================================================
# 全局数据库实例
# ============================================================

db = DatabaseManager()


# ============================================================
# LangChain Tool: DataDeduplicator（去重工具）
# ============================================================

@tool
def data_deduplicator(job_id: str) -> bool:
    """
    检查指定 job_id 的岗位是否已经存在于数据库中。
    参数:
        job_id: 51job岗位 ID 字符串
    返回:
        True 表示已存在（重复），False 表示新岗位
    """
    return db.is_duplicate(job_id)


# ============================================================
# 快捷函数（供工作流节点调用）
# ============================================================

def deduplicate_jobs(jobs: list) -> list:
    """
    对岗位列表去重，返回仅包含新岗位的列表。
    同时将新岗位入库（尚未打分，score=0）。
    """
    new_jobs = []
    for job in jobs:
        if not db.is_duplicate(job["job_id"]):
            db.insert_job(job)
            new_jobs.append(job)
        else:
            logger.debug("岗位已存在，跳过: %s - %s", job.get("job_name"), job.get("company_name"))
    logger.info("去重完成: 总计 %d 个, 新岗位 %d 个", len(jobs), len(new_jobs))
    return new_jobs


def save_matched_score(job_id: str, score: float, reason: str = ""):
    """保存 AI 匹配打分结果"""
    db.update_match_score(job_id, score, reason)
