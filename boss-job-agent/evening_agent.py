"""
evening_agent.py — 晚间回复监控主流程
========================================
功能：
- 每天 20:00 自动执行
- 自动登录 51job，进入消息中心
- 抓取所有新消息、HR 回复、面试邀请
- 统计：今日新回复、已读、面试邀请数量
- 生成晚间报告
- 推送到企业微信提醒
- 保存状态到数据库

用法：
    # 手动执行一次
    python evening_agent.py
"""

import asyncio
import logging
import sys
from datetime import datetime

from langgraph_workflow import run_evening_workflow
from config import LOG_CONFIG

# 配置日志
logging.basicConfig(
    level=getattr(logging, LOG_CONFIG.get("level", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_CONFIG["file"], encoding="utf-8"),
    ],
)
logger = logging.getLogger("evening_agent")


# ============================================================
# 主流程
# ============================================================

async def run():
    """
    执行晚间回复监控完整流程。

    流程概览:
    1. 启动 Playwright 浏览器
    2. 加载 Cookie 自动登录 51job
    3. 进入消息中心
    4. 抓取所有新消息（HR 回复、面试邀请、系统通知等）
    5. 统计分析：今日新回复数、已读数、面试邀请数
    6. 生成晚间报告
    7. 推送到企业微信
    8. 保存数据到 SQLite
    """
    logger.info("=" * 60)
    logger.info("🌙 51job晚间回复监控 Agent 启动")
    logger.info(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    result = await run_evening_workflow()

    # 输出结果摘要
    logger.info("=" * 60)
    logger.info("📊 执行结果摘要")
    logger.info(f"   状态: {result.get('status', 'unknown')}")
    logger.info(f"   消息数: {result.get('message_count', 0)}")

    stats = result.get("stats", {})
    logger.info(f"   新回复: {stats.get('total_new_replies', 0)}")
    logger.info(f"   面试邀请: {stats.get('interview_count', 0)}")
    logger.info(f"   已读: {stats.get('read_count', 0)}")
    logger.info(f"   推送状态: {result.get('push_result', {})}")

    if result.get("error"):
        logger.info(f"   错误信息: {result['error']}")

    # 如果有面试邀请，特别提醒
    if stats.get("interview_count", 0) > 0:
        logger.info("")
        logger.info("🎉🎉🎉 注意：有面试邀请！请及时登录 51job 查看和回复！🎉🎉🎉")

    logger.info("=" * 60)

    return result


# ============================================================
# 一次性执行函数（供 scheduler 调用）
# ============================================================

def run_evening_job():
    """
    供 schedule 定时任务调用的同步包装函数。
    """
    logger.info("⏰ 定时任务触发: 晚间回复监控")
    asyncio.run(run())


# ============================================================
# 命令行入口
# ============================================================

if __name__ == "__main__":
    asyncio.run(run())
