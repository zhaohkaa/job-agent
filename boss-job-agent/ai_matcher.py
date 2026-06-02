"""
ai_matcher.py — AI 匹配打分工具（LangChain Tool）+ 规则引擎
=============================================================
功能：
- 调用 LLM 对 JD 与简历进行匹配打分
- 支持多简历 profile（根据关键词自动切换）
- 自定义评分权重（tech/experience/salary/education/company）
- 智能关键词加分（JD 中出现特定技术栈加分）
- 公司黑白名单过滤
- 薪资预筛选（在调用 LLM 前用规则过滤）
- 用作 LangChain Tool: AIMatcher

变更说明 (v3.0): 新增多简历、评分权重、关键词加分、预筛选
"""

import json
import logging
import re
from typing import Optional

from langchain.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage

from config import (
    AI_CONFIG,
    RESUME_PROFILES,
    KEYWORD_RESUME_MAP,
    SCORE_WEIGHTS,
    SMART_KEYWORD_BONUS,
    COMPANY_FILTER,
    SALARY_PRE_FILTER,
)

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


_llm_instance = None


def get_llm():
    """获取 LLM 实例（单例）"""
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = _get_llm()
    return _llm_instance


# ============================================================
# 简历选择
# ============================================================

def get_resume_for_keyword(keyword: str = "") -> str:
    """
    根据搜索关键词选择合适的简历 profile。
    未匹配时返回 general profile。
    """
    # 精确匹配
    if keyword in KEYWORD_RESUME_MAP:
        profile_name = KEYWORD_RESUME_MAP[keyword]
    else:
        # 模糊匹配：检查关键词是否包含映射中的 key
        matched = None
        for map_key, profile in KEYWORD_RESUME_MAP.items():
            if map_key.lower() in keyword.lower() or keyword.lower() in map_key.lower():
                matched = profile
                break
        profile_name = matched or "general"

    resume = RESUME_PROFILES.get(profile_name, RESUME_PROFILES.get("general", ""))
    logger.debug("关键词 '%s' → 简历 profile: %s", keyword, profile_name)
    return resume


# ============================================================
# 规则引擎：薪资预筛选
# ============================================================

def parse_salary_range(salary_text: str) -> Optional[dict]:
    """
    从薪资文本中解析数值范围。
    支持的格式:
      - "15K-25K" / "1.5-2.5万" / "15K-25K·13薪"
      - "200元/天" / "300元/天"
      - "20-25万/年"
    返回 {"min": float, "max": float, "unit": "monthly"|"daily"|"yearly", "multiplier": float}
    """
    if not salary_text:
        return None

    s = salary_text.strip().replace(" ", "")

    # 日薪: "200元/天" / "160元/天"
    daily_match = re.search(r'([\d.]+)\s*元?/天', s)
    if daily_match:
        val = float(daily_match.group(1))
        return {"min": val, "max": val, "unit": "daily", "multiplier": 1}

    # 年薪: "20-25万/年"
    yearly_match = re.search(r'([\d.]+)[-~]([\d.]+)万/年', s)
    if yearly_match:
        lo = float(yearly_match.group(1))
        hi = float(yearly_match.group(2))
        return {"min": lo / 12, "max": hi / 12, "unit": "monthly", "multiplier": 10000}

    # 月薪（万）: "1.5-2.5万" / "1-2万"
    wan_match = re.search(r'([\d.]+)[-~]([\d.]+)万', s)
    if wan_match:
        lo = float(wan_match.group(1)) * 10000
        hi = float(wan_match.group(2)) * 10000
        return {"min": lo, "max": hi, "unit": "monthly", "multiplier": 10000}

    # 月薪（K/千/万混合）: "15K-25K" / "7千-1.4万" / "8千-1万"
    k_match = re.search(r'([\d.]+)\s*[Kk千][-~]\s*([\d.]+)\s*[Kk千万]?', s)
    if k_match:
        lo_val = float(k_match.group(1))
        hi_val = float(k_match.group(2))
        # 如果有"万"，需要将对应部分乘以 10
        if '万' in s:
            # 判断"万"出现在低值还是高值
            parts = s.split('-') if '-' in s else s.split('~')
            if len(parts) >= 2 and '万' in parts[1]:
                hi_val = hi_val * 10
            if len(parts) >= 1 and '万' in parts[0]:
                lo_val = lo_val * 10
        return {"min": lo_val * 1000, "max": hi_val * 1000,
                "unit": "monthly", "multiplier": 1000}

    # 通用数字格式: 数字-数字 (默认视为 K)
    generic_match = re.search(r'([\d.]+)[-~]([\d.]+)', s)
    if generic_match:
        lo = float(generic_match.group(1))
        hi = float(generic_match.group(2))
        # 判断单位
        if lo < 10 and hi < 50:
            # 可能是"万"（如 1.5-2.5）
            return {"min": lo * 10000, "max": hi * 10000, "unit": "monthly", "multiplier": 10000}
        else:
            # 可能是 K（如 15-25）
            return {"min": lo * 1000, "max": hi * 1000, "unit": "monthly", "multiplier": 1000}

    # 单个数字
    single_match = re.search(r'([\d.]+)', s)
    if single_match:
        val = float(single_match.group(1))
        if val < 10:
            return {"min": val * 10000, "max": val * 10000, "unit": "monthly", "multiplier": 10000}
        else:
            return {"min": val * 1000, "max": val * 1000, "unit": "monthly", "multiplier": 1000}

    return None


def pre_filter_salary(job: dict) -> bool:
    """
    薪资预筛选。返回 True 表示通过，False 表示过滤掉。
    """
    if not SALARY_PRE_FILTER.get("enabled", False):
        return True

    salary_text = job.get("salary", "")
    parsed = parse_salary_range(salary_text)

    if parsed is None:
        return SALARY_PRE_FILTER.get("keep_if_unparseable", True)

    # 日薪过滤
    if parsed["unit"] == "daily":
        min_daily = SALARY_PRE_FILTER.get("min_daily_salary", 150)
        if parsed["min"] < min_daily:
            logger.info("  [预筛选] 日薪 %.0f < 最低 %d 元/天，过滤", parsed["min"], min_daily)
            return False
        return True

    # 月薪过滤
    if parsed["unit"] == "monthly":
        min_k = SALARY_PRE_FILTER.get("min_monthly_salary_k", 8)
        # parsed 的 min/max 已经是元，转换为 K 比较
        salary_min_k = parsed["min"] / 1000
        # 取中位数判断（有些岗位写一个宽泛范围，如 7千-1.4万）
        salary_mid_k = (parsed["min"] + parsed["max"]) / 2000
        if salary_mid_k < min_k * 0.7:  # 中位数低于期望的 70%
            logger.info("  [预筛选] 薪资中位 %.1fK < 最低 %.1fK，过滤",
                        salary_mid_k, min_k * 0.7)
            return False
        return True

    return True


# ============================================================
# 规则引擎：公司黑白名单
# ============================================================

def check_company_filter(job: dict) -> tuple:
    """
    检查公司黑白名单。
    返回 (should_skip: bool, score_bonus: float)
    """
    company = job.get("company_name", "")
    company_lower = company.lower()

    # 黑名单检查
    for keyword in COMPANY_FILTER.get("blacklist", []):
        if keyword.lower() in company_lower:
            logger.info("  [公司过滤] 黑名单匹配 '%s'，跳过 %s", keyword, company)
            return True, 0.0

    # 实习岗位检查
    job_name = job.get("job_name", "")
    if COMPANY_FILTER.get("skip_intern", False) and any(
        kw in job_name for kw in ["实习", "实习生", "intern"]
    ):
        logger.info("  [公司过滤] 实习岗位，跳过 %s", job_name)
        return True, 0.0

    # 无 JD 检查
    jd_text = job.get("jd_text", "")
    if COMPANY_FILTER.get("skip_no_jd", False) and (
        not jd_text or len(jd_text) < 50 or "提取失败" in jd_text
    ):
        logger.info("  [公司过滤] JD 缺失或过短，跳过 %s", job_name)
        return True, 0.0

    # 白名单加分
    bonus = 0.0
    for keyword in COMPANY_FILTER.get("whitelist", []):
        if keyword.lower() in company_lower:
            bonus = 2.0
            logger.info("  [公司加分] 白名单匹配 '%s'，%s +%.1f", keyword, company, bonus)
            break

    return False, bonus


# ============================================================
# 规则引擎：关键词加分
# ============================================================

def apply_keyword_bonus(jd_text: str, job_name: str = "") -> float:
    """
    在 JD 文本和岗位名中检测关键词，返回加分值。
    正数为加分，负数为扣分。
    """
    if not jd_text:
        return 0.0

    combined_text = (jd_text + " " + job_name).lower()
    total_bonus = 0.0
    matched = []

    for keyword, bonus in SMART_KEYWORD_BONUS.items():
        if keyword.lower() in combined_text:
            total_bonus += bonus
            matched.append(f"{keyword}({bonus:+.1f})")

    if matched:
        logger.debug("  [关键词加分] %s → 合计 %+.1f", ", ".join(matched[:5]), total_bonus)

    # 限制总加分范围 [-5, +5]
    return max(-5.0, min(5.0, total_bonus))


# ============================================================
# 核心 AI 匹配逻辑
# ============================================================

MATCH_SYSTEM_PROMPT = """你是一位专业的 HR 和技术面试官，擅长评估求职者与岗位的匹配程度。

你的任务是：
1. 仔细阅读求职者简历和岗位 JD
2. 从以下维度进行匹配度评估：
   - 技术栈匹配度（技能是否匹配 JD 要求）
   - 经验匹配度（工作年限、项目经验是否匹配）
   - 学历匹配度（学历是否满足要求）
   - 城市匹配度（期望城市是否匹配）
   - 薪资匹配度（期望薪资是否在 JD 范围内）
   - 公司质量（公司规模/行业/发展阶段）

3. 输出严格的 JSON 格式，不要输出任何 JSON 之外的内容：
{
    "score": 8.5,                   // 综合匹配度（0-10 的浮点数）
    "tech_score": 8,               // 技术栈匹配度（0-10）
    "experience_score": 9,         // 经验匹配度（0-10）
    "education_score": 10,         // 学历匹配度（0-10）
    "salary_score": 7,             // 薪资匹配度（0-10）
    "company_score": 8,            // 公司质量评分（0-10）
    "city_match": true,            // 城市是否匹配
    "salary_match": true,          // 薪资是否在期望范围内
    "reason": "技术栈高度匹配，Python/FastAPI/PostgreSQL 均为候选人核心技术栈...",  // 匹配理由（100字以内）
    "suggestion": "建议重点投递，该岗位与候选人背景高度契合"  // 投递建议
}

注意：
- 如果 JD 是空或过短，score 给 0 分
- 要有区分度，不要所有岗位都给高分
- 实事求是，不要刻意迎合
- 对于与求职者背景明显不相关的岗位（如不同行业/方向），给低分"""


def match_jd_to_resume(job: dict, resume: str = None) -> dict:
    """
    使用 LLM 对单个岗位 JD 与简历进行匹配打分。

    参数:
        job: 岗位字典，至少包含 job_name, company_name, jd_text, salary, city 等字段
        resume: 简历文本（可选，默认使用 RESUME_TEXT）

    返回:
        {"score": float, "reason": str, ...} 匹配结果字典
    """
    jd_text = job.get("jd_text", "")
    job_name = job.get("job_name", "未知岗位")
    company_name = job.get("company_name", "未知公司")
    salary = job.get("salary", "未知")
    city = job.get("city", "未知")
    keyword = job.get("keyword", "")

    # 选择简历
    if resume is None:
        resume = get_resume_for_keyword(keyword)

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
{resume[:3000]}

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
        json_match = re.search(r'\{[\s\S]*\}', result_text)
        if json_match:
            result_text = json_match.group(0)

        result = json.loads(result_text)

        # 确保 score 在 0-10 范围内
        result["score"] = max(0.0, min(10.0, float(result.get("score", 0))))
        result["jd_text"] = jd_text

        # ---- 应用评分权重 ----
        weights = SCORE_WEIGHTS
        weighted_score = 0.0
        weighted_score += result.get("tech_score", 5) * weights.get("tech_match", 0.35)
        weighted_score += result.get("experience_score", 5) * weights.get("experience_match", 0.20)
        weighted_score += result.get("salary_score", 5) * weights.get("salary_match", 0.20)
        weighted_score += result.get("education_score", 5) * weights.get("education_match", 0.10)
        weighted_score += result.get("company_score", 5) * weights.get("company_quality", 0.15)

        # 将加权分数映射回 0-10 范围（乘 2 因为权重总和为 1.0，每个维度满分 10）
        # 实际公式: weighted_score 已经是 0-10 范围
        result["ai_raw_score"] = result["score"]  # 保留 LLM 原始分数
        result["weighted_score"] = round(weighted_score, 1)  # 规则加权分数
        result["score"] = round(weighted_score, 1)  # 使用加权分数作为主分数
        result["jd_text"] = jd_text

        logger.info("匹配完成: %s - %s (LLM原始:%.1f, 加权:%.1f)",
                     job_name[:30], company_name[:20], result["ai_raw_score"], result["score"])
        return result

    except json.JSONDecodeError as e:
        logger.warning("LLM 返回非 JSON 格式，尝试容错解析: %s", str(e)[:100])
        score_match = re.search(r'"score"\s*:\s*([\d.]+)', result_text)
        score = float(score_match.group(1)) if score_match else 5.0
        return {
            "score": max(0.0, min(10.0, score)),
            "ai_raw_score": score,
            "weighted_score": score,
            "reason": result_text[:200],
            "tech_score": 5,
            "experience_score": 5,
            "education_score": 5,
            "salary_score": 5,
            "company_score": 5,
            "city_match": True,
            "salary_match": True,
            "suggestion": "（LLM 解析异常，使用默认分数）",
        }

    except Exception as e:
        logger.error("AI 匹配请求失败: %s", e)
        return {
            "score": 0,
            "ai_raw_score": 0,
            "weighted_score": 0,
            "reason": f"匹配请求失败: {str(e)[:100]}",
            "tech_score": 0,
            "experience_score": 0,
            "education_score": 0,
            "salary_score": 0,
            "company_score": 0,
            "city_match": False,
            "salary_match": False,
            "suggestion": "请求异常，请重试",
        }


# ============================================================
# 完整匹配流程（规则预筛选 + AI 打分 + 关键词加分 + 公司加分）
# ============================================================

def full_match_pipeline(job: dict, resume: str = None, min_score: float = None) -> Optional[dict]:
    """
    完整的岗位匹配流水线。
    返回带 match_score 和 match_reason 的 job 字典，如果被预筛选过滤则返回 None。
    """
    if min_score is None:
        min_score = AI_CONFIG["min_match_score"]

    job_name = job.get("job_name", "?")[:30]

    # 步骤 1: 公司黑白名单检查
    should_skip, company_bonus = check_company_filter(job)
    if should_skip:
        return None

    # 步骤 2: 薪资预筛选
    if not pre_filter_salary(job):
        return None

    # 步骤 3: AI 匹配打分
    match_result = match_jd_to_resume(job, resume)

    # 步骤 4: 关键词加分
    jd_text = job.get("jd_text", "")
    job_name_full = job.get("job_name", "")
    keyword_bonus = apply_keyword_bonus(jd_text, job_name_full)

    # 步骤 5: 合并最终分数
    base_score = match_result.get("score", 0)
    final_score = base_score + keyword_bonus + company_bonus
    final_score = max(0.0, min(10.0, final_score))

    job["match_score"] = round(final_score, 1)
    job["match_reason"] = match_result.get("reason", "")
    job["match_detail"] = match_result
    job["keyword_bonus"] = round(keyword_bonus, 1)
    job["company_bonus"] = round(company_bonus, 1)
    job["ai_raw_score"] = match_result.get("ai_raw_score", 0)

    if final_score >= min_score:
        logger.info("  >> 通过筛选 (%.1f >= %.1f) [AI:%.1f + 关键词:%+.1f + 公司:%+.1f]",
                     final_score, min_score, base_score, keyword_bonus, company_bonus)
    else:
        logger.info("  >> 未达阈值 (%.1f < %.1f)", final_score, min_score)

    return job


# ============================================================
# 批量匹配
# ============================================================

def batch_match_jobs(jobs: list, min_score: float = None) -> list:
    """
    批量对岗位进行完整匹配流水线（预筛选 + AI 打分 + 关键词加分 + 公司加分）。

    参数:
        jobs: 岗位字典列表
        min_score: 最低分数阈值（默认使用配置中的值）

    返回:
        带 match_score 和 match_reason 字段的岗位列表（已过滤低分和预筛选掉的岗位）
    """
    if min_score is None:
        min_score = AI_CONFIG["min_match_score"]

    results = []
    filtered_count = 0

    for i, job in enumerate(jobs):
        job_name = job.get("job_name", "?")[:30]
        logger.info("AI 匹配中 [%d/%d]: %s", i + 1, len(jobs), job_name)

        # 根据岗位的关键词选择简历
        keyword = job.get("keyword", "")
        resume = get_resume_for_keyword(keyword)

        result = full_match_pipeline(job, resume, min_score)
        if result is None:
            filtered_count += 1
            continue

        if result.get("match_score", 0) >= min_score:
            results.append(result)

    logger.info("批量匹配完成: %d 个岗位 → %d 达标, %d 预筛选过滤",
                 len(jobs), len(results), filtered_count)

    # 按分数降序排列
    results.sort(key=lambda x: x.get("match_score", 0), reverse=True)
    return results


# ============================================================
# LangChain Tool: AIMatcher
# ============================================================

@tool
def ai_matcher(job_json: str) -> str:
    """
    对岗位 JD 与求职者简历进行 AI 匹配打分（含预筛选+关键词加分）。
    参数:
        job_json: 岗位信息的 JSON 字符串，必须包含字段:
                  job_name(岗位名称), company_name(公司), jd_text(JD描述),
                  salary(薪资), city(城市), keyword(搜索关键词，可选)
    返回:
        JSON 字符串，包含 score(0-10分) 和 reason(匹配理由)
    """
    try:
        job = json.loads(job_json)
    except json.JSONDecodeError:
        return json.dumps({"score": 0, "reason": "输入 JSON 格式错误"}, ensure_ascii=False)

    result = full_match_pipeline(job)
    if result is None:
        return json.dumps({"score": 0, "reason": "被预筛选过滤", "filtered": True}, ensure_ascii=False)

    return json.dumps({
        "score": result.get("match_score", 0),
        "reason": result.get("match_reason", ""),
        "ai_raw_score": result.get("ai_raw_score", 0),
        "keyword_bonus": result.get("keyword_bonus", 0),
        "company_bonus": result.get("company_bonus", 0),
        "filtered": False,
    }, ensure_ascii=False)
