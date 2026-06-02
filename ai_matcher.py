"""
ai_matcher.py — AI 匹配打分工具（LangChain Tool）
==================================================
功能：
- 调用 LLM（通义千问 / OpenAI / 本地 LLM）对 JD 与简历进行匹配打分
- 输出 0-10 分 + 匹配理由
- 用作 LangChain Tool: AIMatcher
"""

import json
import logging
import re
from typing import Optional
from langchain.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage

from config import AI_CONFIG, RESUME_TEXT

logger = logging.getLogger(__name__)


# ============================================================
# LLM 客户端初始化
# ============================================================

def _get_llm():
    """根据配置返回 LangChain ChatModel 实例"""
    provider = AI_CONFIG["provider"]

    if provider == "tongyi":
        try:
            from langchain_community.chat_models import ChatTongyi
            return ChatTongyi(
                model=AI_CONFIG["tongyi_model"],
                dashscope_api_key=AI_CONFIG["tongyi_api_key"],
                temperature=0.3,
            )
        except ImportError:
            logger.warning("langchain_community 未安装，尝试使用 OpenAI 兼容模式")
            provider = "openai"

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=AI_CONFIG["openai_model"],
            api_key=AI_CONFIG["openai_api_key"],
            base_url=AI_CONFIG["openai_base_url"],
            temperature=0.3,
        )

    if provider == "local":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=AI_CONFIG["local_llm_model"],
            api_key="not-needed",
            base_url=AI_CONFIG["local_llm_url"] + "/v1",
            temperature=0.3,
        )

    raise ValueError(f"不支持的 AI provider: {provider}")


# 延迟初始化 LLM（避免导入时就连接 API）
_llm_instance = None


def get_llm():
    """获取 LLM 实例（单例）"""
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = _get_llm()
    return _llm_instance


# ============================================================
# AI 匹配核心逻辑
# ============================================================

# 匹配打分的 System Prompt
MATCH_SYSTEM_PROMPT = """你是一位专业的 HR 和技术面试官，擅长评估求职者与岗位的匹配程度。

你的任务是：
1. 仔细阅读求职者简历和岗位 JD
2. 从以下维度进行匹配度评估：
   - 技术栈匹配度（技能是否匹配 JD 要求）
   - 经验匹配度（工作年限、项目经验是否匹配）
   - 学历匹配度（学历是否满足要求）
   - 城市匹配度（期望城市是否匹配）
   - 薪资匹配度（期望薪资是否在 JD 范围内）
   - 综合匹配度

3. 输出严格的 JSON 格式，不要输出任何 JSON 之外的内容：
{
    "score": 8.5,                   // 综合匹配度（0-10 的浮点数）
    "tech_score": 8,               // 技术栈匹配度（0-10）
    "experience_score": 9,         // 经验匹配度（0-10）
    "education_score": 10,         // 学历匹配度（0-10）
    "city_match": true,            // 城市是否匹配
    "salary_match": true,          // 薪资是否在期望范围内
    "reason": "技术栈高度匹配，Python/FastAPI/PostgreSQL 均为候选人核心技术栈...",  // 匹配理由（100字以内）
    "suggestion": "建议重点投递，该岗位与候选人背景高度契合"  // 投递建议
}

注意：
- 如果 JD 是空或过短，score 给 0 分
- 要有区分度，不要所有岗位都给高分
- 实事求是，不要刻意迎合"""


def match_jd_to_resume(job: dict) -> dict:
    """
    使用 LLM 对单个岗位 JD 与简历进行匹配打分。

    参数:
        job: 岗位字典，至少包含 job_name, company_name, jd_text, salary, city 等字段

    返回:
        {"score": float, "reason": str, ...} 匹配结果字典
    """
    jd_text = job.get("jd_text", "")
    job_name = job.get("job_name", "未知岗位")
    company_name = job.get("company_name", "未知公司")
    salary = job.get("salary", "未知")
    city = job.get("city", "未知")

    # 构建 Human Message
    user_prompt = f"""请评估以下岗位与求职者的匹配度：

【岗位信息】
- 岗位名称：{job_name}
- 公司：{company_name}
- 薪资：{salary}
- 城市：{city}
- JD 描述：
{jd_text[:3000]}

【求职者简历】
{RESUME_TEXT[:3000]}

请严格按照 JSON 格式输出匹配结果。"""

    llm = get_llm()
    messages = [
        SystemMessage(content=MATCH_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    try:
        response = llm.invoke(messages)
        result_text = response.content.strip()

        # 尝试从响应中提取 JSON
        # 有些模型会在 JSON 外面加 ```json ... ``` 标记
        json_match = re.search(r'\{[\s\S]*\}', result_text)
        if json_match:
            result_text = json_match.group(0)

        result = json.loads(result_text)

        # 确保 score 在 0-10 范围内
        result["score"] = max(0.0, min(10.0, float(result.get("score", 0))))
        result["jd_text"] = jd_text  # 保留原始 JD 供参考

        logger.info("匹配完成: %s - %s (分数: %.1f)", job_name, company_name, result["score"])
        return result

    except json.JSONDecodeError as e:
        logger.warning("LLM 返回非 JSON 格式，尝试容错解析: %s", str(e)[:100])
        # 容错：尝试从文本中提取分数
        score_match = re.search(r'"score"\s*:\s*([\d.]+)', result_text)
        score = float(score_match.group(1)) if score_match else 5.0
        return {
            "score": max(0.0, min(10.0, score)),
            "reason": result_text[:200],
            "tech_score": 5,
            "experience_score": 5,
            "education_score": 5,
            "city_match": True,
            "salary_match": True,
            "suggestion": "（LLM 解析异常，使用默认分数）",
        }

    except Exception as e:
        logger.error("AI 匹配请求失败: %s", e)
        return {
            "score": 0,
            "reason": f"匹配请求失败: {str(e)[:100]}",
            "tech_score": 0,
            "experience_score": 0,
            "education_score": 0,
            "city_match": False,
            "salary_match": False,
            "suggestion": "请求异常，请重试",
        }


# ============================================================
# LangChain Tool: AIMatcher
# ============================================================

@tool
def ai_matcher(job_json: str) -> str:
    """
    对岗位 JD 与求职者简历进行 AI 匹配打分。
    参数:
        job_json: 岗位信息的 JSON 字符串，必须包含字段:
                  job_name(岗位名称), company_name(公司), jd_text(JD描述),
                  salary(薪资), city(城市)
    返回:
        JSON 字符串，包含 score(0-10分) 和 reason(匹配理由)
    """
    try:
        job = json.loads(job_json)
    except json.JSONDecodeError:
        return json.dumps({"score": 0, "reason": "输入 JSON 格式错误"}, ensure_ascii=False)

    result = match_jd_to_resume(job)
    return json.dumps(result, ensure_ascii=False)


# ============================================================
# 批量匹配快捷函数
# ============================================================

def batch_match_jobs(jobs: list, min_score: float = None) -> list:
    """
    批量对岗位进行 AI 匹配打分。

    参数:
        jobs: 岗位字典列表
        min_score: 最低分数阈值（默认使用配置中的值）

    返回:
        带 match_score 和 match_reason 字段的岗位列表
    """
    if min_score is None:
        min_score = AI_CONFIG["min_match_score"]

    results = []
    for i, job in enumerate(jobs):
        logger.info("AI 匹配中 [%d/%d]: %s", i + 1, len(jobs), job.get("job_name", "?"))
        match_result = match_jd_to_resume(job)

        # 将匹配结果写回到 job 字典
        job["match_score"] = match_result.get("score", 0)
        job["match_reason"] = match_result.get("reason", "")
        job["match_detail"] = match_result

        if job["match_score"] >= min_score:
            results.append(job)
            logger.info("  >> 通过筛选 (%.1f >= %.1f)", job["match_score"], min_score)
        else:
            logger.info("  >> 未达阈值 (%.1f < %.1f)", job["match_score"], min_score)

    # 按分数降序排列
    results.sort(key=lambda x: x.get("match_score", 0), reverse=True)
    return results
