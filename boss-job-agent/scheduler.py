"""
scheduler.py — 定时任务调度器
================================
功能：
- 每天 8:00 自动执行早间搜岗 Agent
- 每天 20:00 自动执行晚间回复监控 Agent
- 支持一键启动、停止
- 带有日志输出和运行状态显示

用法：
    # 启动定时任务（前台运行，Ctrl+C 停止）
    python scheduler.py

    # 立即执行一次早间搜岗（不等待定时）
    python scheduler.py --now morning

    # 立即执行一次晚间监控
    python scheduler.py --now evening

    # 立即执行全部（先早间后晚间）
    python scheduler.py --now all
"""

import asyncio
import logging
import signal
import sys
import time
from datetime import datetime

import schedule

from config import SCHEDULE_CONFIG, LOG_CONFIG
from morning_agent import run_morning_job
from evening_agent import run_evening_job

# 配置日志
logging.basicConfig(
    level=getattr(logging, LOG_CONFIG.get("level", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_CONFIG["file"], encoding="utf-8"),
    ],
)
logger = logging.getLogger("scheduler")

# 全局停止标志
_running = True


def signal_handler(signum, frame):
    """处理 Ctrl+C 信号，优雅退出"""
    global _running
    logger.info("收到停止信号，正在退出...")
    _running = False


def setup_schedule():
    """配置定时任务"""
    morning_time = SCHEDULE_CONFIG["morning_time"]  # 默认 "08:00"
    evening_time = SCHEDULE_CONFIG["evening_time"]  # 默认 "20:00"

    # 每天 8:00 执行早间搜岗
    schedule.every().day.at(morning_time).do(run_morning_job)
    logger.info("已配置定时任务: 每天 %s 执行早间搜岗", morning_time)

    # 每天 20:00 执行晚间回复监控
    schedule.every().day.at(evening_time).do(run_evening_job)
    logger.info("已配置定时任务: 每天 %s 执行晚间回复监控", evening_time)


def print_status():
    """打印当前调度状态"""
    now = datetime.now()
    next_run = schedule.next_run()
    jobs = schedule.get_jobs()

    logger.info("=" * 50)
    logger.info("📋 当前调度状态")
    logger.info(f"   当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"   下次执行: {next_run.strftime('%Y-%m-%d %H:%M:%S') if next_run else '无'}")
    logger.info(f"   已注册任务数: {len(jobs)}")
    for job in jobs:
        logger.info(f"   - {job}")
    logger.info("=" * 50)


def run_scheduler():
    """
    启动定时任务循环。
    这是一个阻塞函数，使用 Ctrl+C 停止。
    """
    global _running

    # 注册信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    setup_schedule()

    logger.info("=" * 60)
    logger.info("🚀 51job求职 Agent 定时调度器已启动")
    logger.info(f"   早间搜岗: 每天 {SCHEDULE_CONFIG['morning_time']}")
    logger.info(f"   晚间监控: 每天 {SCHEDULE_CONFIG['evening_time']}")
    logger.info("   按 Ctrl+C 停止")
    logger.info("=" * 60)

    print_status()

    # 主循环
    while _running:
        schedule.run_pending()
        time.sleep(30)  # 每 30 秒检查一次是否有待执行任务

        # 每次循环检查时输出心跳日志（仅在 INFO 级别以上）
        if int(time.time()) % 3600 < 30:  # 每小时打印一次状态
            print_status()

    logger.info("调度器已停止")


def run_now(task_type: str):
    """
    立即执行指定任务。

    参数:
        task_type: "morning" / "evening" / "all"
    """
    if task_type == "morning":
        logger.info("立即执行: 早间搜岗")
        run_morning_job()
    elif task_type == "evening":
        logger.info("立即执行: 晚间回复监控")
        run_evening_job()
    elif task_type == "all":
        logger.info("立即执行: 早间搜岗 + 晚间回复监控")
        run_morning_job()
        logger.info("稍等片刻...")
        time.sleep(3)
        run_evening_job()
    else:
        logger.error("未知任务类型: %s (可选: morning/evening/all)", task_type)


# ============================================================
# 命令行入口
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="51job求职 Agent 定时调度器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scheduler.py              # 启动定时调度（前台运行）
  python scheduler.py --now morning # 立即执行早间搜岗
  python scheduler.py --now evening # 立即执行晚间监控
  python scheduler.py --now all     # 立即执行全部流程
        """,
    )
    parser.add_argument(
        "--now",
        type=str,
        choices=["morning", "evening", "all"],
        help="立即执行指定任务（不启动定时调度）",
    )

    args = parser.parse_args()

    if args.now:
        run_now(args.now)
    else:
        run_scheduler()
