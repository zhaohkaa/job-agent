"""
morning_agent.py — 早间搜岗主流程
====================================
功能：
- 每天 8:00 自动执行
- 调用 LangGraph 早间工作流
- 支持手动触发和定时触发两种模式
- 提供命令行入口和可独立运行的 main 函数

用法：
    # 手动执行一次
    python morning_agent.py

    # 指定关键词执行
    python morning_agent.py --keywords "Python 后端,Java 开发"

    # 指定最低匹配分数
    python morning_agent.py --min-score 6
"""

import asyncio
import logging
import sys
import argparse
from datetime import datetime

from langgraph_workflow import run_morning_workflow
from config import LOG_CONFIG, SEARCH_CONFIG, AI_CONFIG

# 配置日志
logging.basicConfig(
    level=getattr(logging, LOG_CONFIG.get("level", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_CONFIG["file"], encoding="utf-8"),
    ],
)
logger = logging.getLogger("morning_agent")


# ============================================================
# 主流程
# ============================================================

async def run(keywords: list = None, min_score: float = None):
    """
    执行早间搜岗完整流程。

    流程概览:
    1. 启动 Playwright 浏览器
    2. 加载 Cookie 自动登录 51job
    3. 根据配置关键词搜索岗位
    4. 进入详情页读取完整 JD
    5. 调用 LLM 对 JD 与简历做匹配打分
    6. 去重（过滤已处理的岗位）
    7. 筛选匹配度 ≥ 7 分的岗位
    8. 生成日报
    9. 推送到企业微信
    10. 保存数据到 SQLite

    参数:
        keywords: 搜索关键词列表（可选，默认使用 config.py 中的配置）
        min_score: 最低匹配分数（可选，默认使用 config.py 中的配置）
    """
    logger.info("=" * 60)
    logger.info("🤖 51job早间搜岗 Agent 启动")
    logger.info(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"   关键词: {keywords or SEARCH_CONFIG['keywords']}")
    logger.info(f"   最低分数: {min_score or AI_CONFIG['min_match_score']}")
    logger.info("=" * 60)

    result = await run_morning_workflow(
        keywords=keywords,
        min_match_score=min_score,
    )

    # 输出结果摘要
    logger.info("=" * 60)
    logger.info("📊 执行结果摘要")
    logger.info(f"   状态: {result.get('status', 'unknown')}")
    logger.info(f"   采集岗位数: {result.get('raw_jobs', 0)}")
    logger.info(f"   高匹配岗位数: {result.get('matched_jobs', 0)}")
    logger.info(f"   推送状态: {result.get('push_result', {})}")
    if result.get("error"):
        logger.info(f"   错误信息: {result['error']}")
    logger.info("=" * 60)

    return result


# ============================================================
# 一次性执行函数（供 scheduler 调用）
# ============================================================

def run_morning_job():
    """
    供 schedule 定时任务调用的同步包装函数。
    scheduler 中的定时任务需要一个无参数的同步函数。
    """
    logger.info("⏰ 定时任务触发: 早间搜岗")
    asyncio.run(run())


# ============================================================
# 命令行入口
# ============================================================

def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="51job早间搜岗 Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--keywords", "-k",
        type=str,
        help="搜索关键词，多个关键词用逗号分隔（例如: 'Python 后端,Java 开发'）",
    )
    parser.add_argument(
        "--min-score", "-s",
        type=float,
        default=None,
        help=f"最低匹配分数（0-10），默认 {AI_CONFIG['min_match_score']}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="空跑模式：只搜索不推送",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    # 解析关键词
    keywords = None
    if args.keywords:
        keywords = [kw.strip() for kw in args.keywords.split(",") if kw.strip()]

    # 空跑模式说明
    if args.dry_run:
        logger.info("⚠️  空跑模式：将跳过推送环节")
        # 临时禁用推送（通过修改配置的方式）
        from config import PUSH_CONFIG
        PUSH_CONFIG["enabled"] = False

    asyncio.run(run(keywords=keywords, min_score=args.min_score))
