"""
langgraph_workflow.py — LangGraph 状态图 & 工作流定义
========================================================
功能：
- 定义早间搜岗 Agent 的状态图（StateGraph）
- 定义晚间投递反馈监控 Agent 的状态图
- 包含异常处理节点（登录失效 → 重新登录 → 继续）
- 状态管理与持久化，支持中断后继续

变更说明 (v2.0): 目标网站从 Boss 直聘改为 51job。
晚间流程从"消息中心抓取"改为"投递反馈+面试邀请"。

状态流转图：

早间流程:
  START → login → search → collect_details → ai_match → deduplicate → generate_report → push → END

晚间流程:
  START → login → fetch_delivery+interviews → stats → generate_report → push → END
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import TypedDict, Optional, Annotated, Sequence
import operator

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from config import SEARCH_CONFIG, AI_CONFIG, JOB51_CONFIG
from browser_tool import Job51Browser
from ai_matcher import match_jd_to_resume, batch_match_jobs
from db_manager import db, deduplicate_jobs, save_matched_score
from notifier import push_morning_report, push_evening_report

logger = logging.getLogger(__name__)


# ============================================================
# 状态定义
# ============================================================

class MorningState(TypedDict):
    """早间搜岗工作流状态"""
    # 工作流标识
    workflow_type: str  # "morning"
    # 登录状态
    is_logged_in: bool
    login_retry_count: int
    # 搜索相关
    keywords: list
    current_keyword_index: int
    raw_jobs: Annotated[list, operator.add]  # 列表合并使用追加
    # 采集状态
    jobs_with_jd: list
    # AI 匹配状态
    matched_jobs: list
    min_match_score: float
    # 去重状态
    new_jobs: list
    # 报告状态
    report_content: str
    # 推送状态
    push_result: dict
    # 异常状态
    error: Optional[str]
    error_count: int
    # 任务日志 ID
    task_log_id: Optional[int]


class EveningState(TypedDict):
    """晚间回复监控工作流状态"""
    # 工作流标识
    workflow_type: str  # "evening"
    # 登录状态
    is_logged_in: bool
    login_retry_count: int
    # 消息数据
    messages: list
    # 统计数据
    stats: dict
    # 报告状态
    report_content: str
    # 推送状态
    push_result: dict
    # 异常状态
    error: Optional[str]
    error_count: int
    # 任务日志 ID
    task_log_id: Optional[int]


# ============================================================
# 浏览器实例（全局单例，工作流内复用）
# ============================================================

_browser_instance: Optional[Job51Browser] = None


async def get_browser() -> Job51Browser:
    """获取或创建浏览器实例"""
    global _browser_instance
    if _browser_instance is None:
        _browser_instance = Job51Browser()
        await _browser_instance.start()
    return _browser_instance


async def close_browser():
    """关闭浏览器实例"""
    global _browser_instance
    if _browser_instance:
        await _browser_instance.close()
        _browser_instance = None


# ============================================================
# 早间工作流节点
# ============================================================

async def morning_login_node(state: MorningState) -> MorningState:
    """
    节点1: 登录
    加载 Cookie 自动登录，如失效则引导用户手动扫码。
    """
    logger.info("[早间流程] 节点: 登录")
    state["error"] = None

    try:
        browser = await get_browser()
        success = await browser.login()

        if success:
            state["is_logged_in"] = True
            state["login_retry_count"] = 0
            logger.info("[早间流程] 登录成功")
        else:
            state["is_logged_in"] = False
            state["login_retry_count"] += 1
            state["error"] = "登录失败：Cookie 失效且手动登录超时"
            logger.error("[早间流程] 登录失败")

    except Exception as e:
        state["is_logged_in"] = False
        state["error"] = f"登录异常: {str(e)}"
        state["error_count"] += 1
        logger.error("[早间流程] 登录异常: %s", e)

    return state


async def morning_search_node(state: MorningState) -> MorningState:
    """
    节点2: 搜索岗位
    根据配置的关键词逐个搜索，采集列表页基本信息。
    """
    logger.info("[早间流程] 节点: 搜索岗位")

    if not state.get("is_logged_in"):
        state["error"] = "未登录，无法搜索"
        return state

    if not state.get("raw_jobs"):
        state["raw_jobs"] = []

    try:
        browser = await get_browser()
        keywords = state.get("keywords", SEARCH_CONFIG["keywords"])
        all_jobs = []
        max_total = JOB51_CONFIG.get("max_total_jobs", 60)

        for kw in keywords:
            if len(all_jobs) >= max_total:
                logger.info("[早间流程] 总岗位已达上限 %d，停止搜索", max_total)
                break
            logger.info("[早间流程] 搜索关键词: %s", kw)
            jobs = await browser.search_jobs(kw)
            all_jobs.extend(jobs)
            logger.info("[早间流程] 关键词 '%s' 找到 %d 个岗位", kw, len(jobs))

        # 截断到上限
        if len(all_jobs) > max_total:
            all_jobs = all_jobs[:max_total]

        state["raw_jobs"] = all_jobs
        logger.info("[早间流程] 搜索完成: 共 %d 个岗位", len(all_jobs))

    except Exception as e:
        state["error"] = f"搜索异常: {str(e)}"
        state["error_count"] += 1
        logger.error("[早间流程] 搜索异常: %s", e)

    return state


async def morning_collect_details_node(state: MorningState) -> MorningState:
    """
    节点3: 采集详情页 JD
    进入每个岗位的详情页，读取完整 JD 描述。
    """
    logger.info("[早间流程] 节点: 采集详情页 JD")

    if state.get("error"):
        return state

    try:
        browser = await get_browser()
        raw_jobs = state.get("raw_jobs", [])
        jobs_with_jd = await browser.fetch_all_jd_details(raw_jobs)
        state["jobs_with_jd"] = jobs_with_jd
        logger.info("[早间流程] 详情采集完成: %d 个岗位", len(jobs_with_jd))

    except Exception as e:
        state["error"] = f"采集详情异常: {str(e)}"
        state["error_count"] += 1
        logger.error("[早间流程] 采集详情异常: %s", e)

    return state


async def morning_ai_match_node(state: MorningState) -> MorningState:
    """
    节点4: AI 筛选
    使用 LLM 对每个岗位的 JD 与简历进行匹配打分。
    """
    logger.info("[早间流程] 节点: AI 筛选匹配")

    if state.get("error"):
        return state

    try:
        jobs = state.get("jobs_with_jd", [])
        min_score = state.get("min_match_score", AI_CONFIG["min_match_score"])

        matched = batch_match_jobs(jobs, min_score)

        # 保存匹配分数到数据库
        for job in jobs:
            score = job.get("match_score", 0)
            reason = job.get("match_reason", "")
            if score > 0:
                save_matched_score(job["job_id"], score, reason)

        state["matched_jobs"] = matched
        logger.info("[早间流程] AI 匹配完成: %d 个岗位达标 (≥%d分)", len(matched), min_score)

    except Exception as e:
        state["error"] = f"AI 匹配异常: {str(e)}"
        state["error_count"] += 1
        logger.error("[早间流程] AI 匹配异常: %s", e)

    return state


async def morning_deduplicate_node(state: MorningState) -> MorningState:
    """
    节点5: 去重
    过滤掉数据库中已存在的岗位。
    """
    logger.info("[早间流程] 节点: 去重")

    if state.get("error"):
        return state

    try:
        matched = state.get("matched_jobs", [])
        new_jobs = deduplicate_jobs(matched)
        state["new_jobs"] = new_jobs
        logger.info("[早间流程] 去重完成: %d 个新岗位", len(new_jobs))

    except Exception as e:
        state["error"] = f"去重异常: {str(e)}"
        state["error_count"] += 1
        logger.error("[早间流程] 去重异常: %s", e)

    return state


async def morning_generate_report_node(state: MorningState) -> MorningState:
    """
    节点6: 生成日报
    将筛选后的岗位生成 Markdown 日报。
    """
    logger.info("[早间流程] 节点: 生成日报")

    if state.get("error"):
        return state

    try:
        from notifier import generate_morning_report

        matched = state.get("new_jobs", state.get("matched_jobs", []))
        keywords = state.get("keywords", SEARCH_CONFIG["keywords"])
        report = generate_morning_report(matched, keywords)
        state["report_content"] = report
        logger.info("[早间流程] 日报生成完成")

    except Exception as e:
        state["error"] = f"生成报告异常: {str(e)}"
        state["error_count"] += 1
        logger.error("[早间流程] 生成报告异常: %s", e)

    return state


async def morning_push_node(state: MorningState) -> MorningState:
    """
    节点7: 推送日报到企业微信
    """
    logger.info("[早间流程] 节点: 推送日报")

    if state.get("error"):
        return state

    try:
        matched = state.get("new_jobs", state.get("matched_jobs", []))
        keywords = state.get("keywords", SEARCH_CONFIG["keywords"])
        result = push_morning_report(matched, keywords)
        state["push_result"] = result

        if result.get("wechat_markdown") or result.get("wechat_text"):
            logger.info("[早间流程] 推送成功")
        else:
            logger.warning("[早间流程] 推送失败（企业微信可能未配置）")

    except Exception as e:
        state["error"] = f"推送异常: {str(e)}"
        state["error_count"] += 1
        logger.error("[早间流程] 推送异常: %s", e)

    return state


async def morning_handle_error_node(state: MorningState) -> MorningState:
    """
    异常处理节点: 登录失效时重新尝试登录+保存 Cookie。
    """
    logger.info("[早间流程] 异常处理节点")
    state["error_count"] += 1

    if state["error_count"] > 3:
        logger.error("[早间流程] 错误次数过多，终止执行")
        state["error"] = f"错误次数过多 (>{3})，终止执行"
        return state

    try:
        browser = await get_browser()
        # 清除旧 Cookie，重新登录
        logger.info("[早间流程] 重新尝试登录...")
        success = await browser.login()

        if success:
            state["is_logged_in"] = True
            state["error"] = None
            logger.info("[早间流程] 重新登录成功")
        else:
            state["is_logged_in"] = False
            state["error"] = "重新登录仍然失败"
            logger.error("[早间流程] 重新登录失败")

    except Exception as e:
        state["error"] = f"异常处理失败: {str(e)}"
        logger.error("[早间流程] 异常处理失败: %s", e)

    return state


# ============================================================
# 晚间工作流节点
# ============================================================

async def evening_login_node(state: EveningState) -> EveningState:
    """节点1: 登录"""
    logger.info("[晚间流程] 节点: 登录 51job")
    state["error"] = None

    try:
        browser = await get_browser()
        success = await browser.login()

        if success:
            state["is_logged_in"] = True
            state["login_retry_count"] = 0
            logger.info("[晚间流程] 登录 51job 成功")
        else:
            state["is_logged_in"] = False
            state["login_retry_count"] += 1
            state["error"] = "登录 51job 失败"

    except Exception as e:
        state["is_logged_in"] = False
        state["error"] = f"登录异常: {str(e)}"
        state["error_count"] += 1

    return state


async def evening_fetch_messages_node(state: EveningState) -> EveningState:
    """
    节点2: 抓取投递反馈 + 面试邀请。
    51job 没有 Boss 那样的聊天消息，取而代之的是：
    - 投递记录页面（被查看/感兴趣/不合适/面试邀请）
    - 面试邀请页面
    """
    logger.info("[晚间流程] 节点: 抓取投递反馈与面试邀请")

    if not state.get("is_logged_in"):
        state["error"] = "未登录，无法抓取"
        return state

    try:
        browser = await get_browser()
        messages = []

        # 抓取投递反馈
        logger.info("[晚间流程] 抓取投递反馈...")
        delivery_items = await browser.fetch_delivery_status()
        messages.extend(delivery_items)
        logger.info("[晚间流程] 投递反馈: %d 条", len(delivery_items))

        # 抓取面试邀请
        logger.info("[晚间流程] 抓取面试邀请...")
        interview_items = await browser.fetch_interview_invites()
        messages.extend(interview_items)
        logger.info("[晚间流程] 面试邀请: %d 条", len(interview_items))

        state["messages"] = messages

        # 将消息保存到数据库
        for msg in messages:
            db.insert_message(msg)

        logger.info("[晚间流程] 抓取完成: 共 %d 条", len(messages))

    except Exception as e:
        state["error"] = f"抓取异常: {str(e)}"
        state["error_count"] += 1

    return state


async def evening_stats_node(state: EveningState) -> EveningState:
    """节点3: 统计消息"""
    logger.info("[晚间流程] 节点: 统计分析")

    if state.get("error"):
        return state

    try:
        stats = db.get_message_stats()
        # 合并运行时抓取的数据和数据库历史数据
        messages = state.get("messages", [])
        interview_count = sum(1 for m in messages if m.get("msg_type") == "interview_invite")
        read_count = sum(1 for m in messages if m.get("is_read"))

        stats["interview_count"] = max(stats.get("interview_count", 0), interview_count)
        stats["read_count"] = max(stats.get("read_count", 0), read_count)

        state["stats"] = stats
        logger.info("[晚间流程] 统计: %s", json.dumps(stats, ensure_ascii=False))

    except Exception as e:
        state["error"] = f"统计分析异常: {str(e)}"
        state["error_count"] += 1

    return state


async def evening_generate_report_node(state: EveningState) -> EveningState:
    """节点4: 生成晚报"""
    logger.info("[晚间流程] 节点: 生成晚报")

    if state.get("error"):
        return state

    try:
        from notifier import generate_evening_report

        stats = state.get("stats", {})
        messages = state.get("messages", [])
        report = generate_evening_report(stats, messages)
        state["report_content"] = report

    except Exception as e:
        state["error"] = f"生成报告异常: {str(e)}"
        state["error_count"] += 1

    return state


async def evening_push_node(state: EveningState) -> EveningState:
    """节点5: 推送晚报"""
    logger.info("[晚间流程] 节点: 推送晚报")

    if state.get("error"):
        return state

    try:
        stats = state.get("stats", {})
        messages = state.get("messages", [])
        result = push_evening_report(stats, messages)
        state["push_result"] = result

        if result.get("wechat_markdown") or result.get("wechat_text"):
            logger.info("[晚间流程] 推送成功")

    except Exception as e:
        state["error"] = f"推送异常: {str(e)}"
        state["error_count"] += 1

    return state


async def evening_handle_error_node(state: EveningState) -> EveningState:
    """晚间异常处理"""
    logger.info("[晚间流程] 异常处理节点")
    state["error_count"] += 1

    if state["error_count"] > 3:
        state["error"] = f"错误次数过多 (>{3})，终止执行"
        return state

    try:
        browser = await get_browser()
        success = await browser.login()
        state["is_logged_in"] = success
        state["error"] = None if success else "重新登录仍然失败"
    except Exception as e:
        state["error"] = f"异常处理失败: {str(e)}"

    return state


# ============================================================
# 路由函数（条件边）
# ============================================================

def should_handle_error(state: MorningState | EveningState) -> str:
    """判断是否需要进入异常处理节点"""
    if state.get("error"):
        return "handle_error"
    return "continue"


def should_continue_after_error(state: MorningState | EveningState) -> str:
    """异常处理后判断是否继续"""
    # 错误已解决
    if state.get("is_logged_in") and not state.get("error"):
        return "continue"
    # 错误次数过多
    if state.get("error_count", 0) > 3:
        return "end"
    # 仍有错误，重试登录
    if state.get("login_retry_count", 1) <= 2:
        return "retry_login"
    return "end"


# ============================================================
# 构建 LangGraph 状态图
# ============================================================

def build_morning_graph() -> StateGraph:
    """
    构建早间搜岗工作流状态图。

    状态流转:
    START → login → search → collect_details → ai_match → deduplicate
           → generate_report → push → END

    异常路径:
    任意节点出错 → handle_error → (重试登录成功) → 回到出错节点的下一个节点
    任意节点出错 → handle_error → (重试失败) → END
    """
    workflow = StateGraph(MorningState)

    # 添加节点
    workflow.add_node("login", morning_login_node)
    workflow.add_node("search", morning_search_node)
    workflow.add_node("collect_details", morning_collect_details_node)
    workflow.add_node("ai_match", morning_ai_match_node)
    workflow.add_node("deduplicate", morning_deduplicate_node)
    workflow.add_node("generate_report", morning_generate_report_node)
    workflow.add_node("push", morning_push_node)
    workflow.add_node("handle_error", morning_handle_error_node)

    # 正常流程边
    workflow.add_edge("login", "search")
    workflow.add_edge("search", "collect_details")
    workflow.add_edge("collect_details", "ai_match")
    workflow.add_edge("ai_match", "deduplicate")
    workflow.add_edge("deduplicate", "generate_report")
    workflow.add_edge("generate_report", "push")
    workflow.add_edge("push", END)

    # 设置入口
    workflow.set_entry_point("login")

    return workflow


def build_evening_graph() -> StateGraph:
    """
    构建晚间回复监控工作流状态图。

    状态流转:
    START → login → fetch_messages → stats → generate_report → push → END
    """
    workflow = StateGraph(EveningState)

    # 添加节点
    workflow.add_node("login", evening_login_node)
    workflow.add_node("fetch_messages", evening_fetch_messages_node)
    workflow.add_node("stats", evening_stats_node)
    workflow.add_node("generate_report", evening_generate_report_node)
    workflow.add_node("push", evening_push_node)
    workflow.add_node("handle_error", evening_handle_error_node)

    # 正常流程边
    workflow.add_edge("login", "fetch_messages")
    workflow.add_edge("fetch_messages", "stats")
    workflow.add_edge("stats", "generate_report")
    workflow.add_edge("generate_report", "push")
    workflow.add_edge("push", END)

    # 设置入口
    workflow.set_entry_point("login")

    return workflow


# ============================================================
# 工作流执行器
# ============================================================

async def run_morning_workflow(
    keywords: list = None,
    min_match_score: float = None,
) -> dict:
    """
    执行早间搜岗工作流。
    返回执行结果摘要。
    """
    logger.info("=" * 60)
    logger.info("开始执行早间搜岗工作流")
    logger.info("=" * 60)

    # 记录任务开始
    task_log_id = db.log_task_start("morning")

    # 构建初始状态
    initial_state: MorningState = {
        "workflow_type": "morning",
        "is_logged_in": False,
        "login_retry_count": 0,
        "keywords": keywords or SEARCH_CONFIG["keywords"],
        "current_keyword_index": 0,
        "raw_jobs": [],
        "jobs_with_jd": [],
        "matched_jobs": [],
        "min_match_score": min_match_score or AI_CONFIG["min_match_score"],
        "new_jobs": [],
        "report_content": "",
        "push_result": {},
        "error": None,
        "error_count": 0,
        "task_log_id": task_log_id,
    }

    try:
        graph = build_morning_graph()
        compiled = graph.compile()

        # 执行工作流（按节点顺序执行）
        state = initial_state

        # 节点执行顺序
        nodes = ["login", "search", "collect_details", "ai_match", "deduplicate", "generate_report", "push"]

        for node_name in nodes:
            logger.info("--- 执行节点: %s ---", node_name)
            node_func = {
                "login": morning_login_node,
                "search": morning_search_node,
                "collect_details": morning_collect_details_node,
                "ai_match": morning_ai_match_node,
                "deduplicate": morning_deduplicate_node,
                "generate_report": morning_generate_report_node,
                "push": morning_push_node,
            }[node_name]

            state = await node_func(state)

            # 如果出错，尝试异常处理
            if state.get("error"):
                logger.warning("节点 %s 出错: %s", node_name, state["error"])
                state = await morning_handle_error_node(state)

                # 异常处理后仍失败，终止
                if state.get("error") and not state.get("is_logged_in"):
                    logger.error("异常处理失败，终止工作流")
                    break
                # 重试当前节点
                logger.info("异常已处理，重试节点 %s", node_name)
                state = await node_func(state)

        # 保存工作流状态到数据库（用于中断恢复）
        db.save_workflow_state("morning", "last_state", {
            "matched_count": len(state.get("new_jobs", state.get("matched_jobs", []))),
            "raw_count": len(state.get("raw_jobs", [])),
            "finished_at": datetime.now().isoformat(),
            "error": state.get("error"),
        })

        summary = {
            "status": "success" if not state.get("error") else "failed",
            "matched_jobs": len(state.get("new_jobs", state.get("matched_jobs", []))),
            "raw_jobs": len(state.get("raw_jobs", [])),
            "report": state.get("report_content", ""),
            "push_result": state.get("push_result", {}),
            "error": state.get("error"),
        }

        db.log_task_end(task_log_id, summary["status"], summary)
        logger.info("早间工作流执行完成: %s", summary["status"])

        return summary

    except Exception as e:
        logger.error("早间工作流异常: %s", e, exc_info=True)
        db.log_task_end(task_log_id, "failed", None, str(e))
        return {"status": "failed", "error": str(e)}

    finally:
        await close_browser()


async def run_evening_workflow() -> dict:
    """
    执行晚间回复监控工作流。
    返回执行结果摘要。
    """
    logger.info("=" * 60)
    logger.info("开始执行晚间回复监控工作流")
    logger.info("=" * 60)

    # 记录任务开始
    task_log_id = db.log_task_start("evening")

    # 构建初始状态
    initial_state: EveningState = {
        "workflow_type": "evening",
        "is_logged_in": False,
        "login_retry_count": 0,
        "messages": [],
        "stats": {},
        "report_content": "",
        "push_result": {},
        "error": None,
        "error_count": 0,
        "task_log_id": task_log_id,
    }

    try:
        # 节点执行顺序
        state = initial_state

        nodes = ["login", "fetch_messages", "stats", "generate_report", "push"]

        for node_name in nodes:
            logger.info("--- 执行节点: %s ---", node_name)
            node_func = {
                "login": evening_login_node,
                "fetch_messages": evening_fetch_messages_node,
                "stats": evening_stats_node,
                "generate_report": evening_generate_report_node,
                "push": evening_push_node,
            }[node_name]

            state = await node_func(state)

            # 如果出错，尝试异常处理
            if state.get("error"):
                logger.warning("节点 %s 出错: %s", node_name, state["error"])
                state = await evening_handle_error_node(state)

                if state.get("error") and not state.get("is_logged_in"):
                    logger.error("异常处理失败，终止工作流")
                    break
                state = await node_func(state)

        # 保存状态
        db.save_workflow_state("evening", "last_state", {
            "message_count": len(state.get("messages", [])),
            "stats": state.get("stats", {}),
            "finished_at": datetime.now().isoformat(),
            "error": state.get("error"),
        })

        summary = {
            "status": "success" if not state.get("error") else "failed",
            "message_count": len(state.get("messages", [])),
            "stats": state.get("stats", {}),
            "report": state.get("report_content", ""),
            "push_result": state.get("push_result", {}),
            "error": state.get("error"),
        }

        db.log_task_end(task_log_id, summary["status"], summary)
        logger.info("晚间工作流执行完成: %s", summary["status"])

        return summary

    except Exception as e:
        logger.error("晚间工作流异常: %s", e, exc_info=True)
        db.log_task_end(task_log_id, "failed", None, str(e))
        return {"status": "failed", "error": str(e)}

    finally:
        await close_browser()


# ============================================================
# 命令行入口（单独运行工作流）
# ============================================================

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if len(sys.argv) > 1 and sys.argv[1] == "evening":
        asyncio.run(run_evening_workflow())
    else:
        asyncio.run(run_morning_workflow())
