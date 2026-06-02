"""
notifier.py — 个人微信推送工具（Server酱）+ LangChain Tool
=============================================================
功能：
- 通过 Server酱 (ServerChan) 推送 Markdown 消息到个人微信
- 兼容企业微信 Webhook（可切换）
- 生成日报/晚报
- 可选邮件推送（备用通道）
- 用作 LangChain Tool: WechatNotifier

Server酱注册地址: https://sct.ftqq.com/
注册后在 "发送消息" 页面获取 SENDKEY。
"""

import json
import logging
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

import requests
from langchain.tools import tool

from config import PUSH_CONFIG, WECHAT_CONFIG, EMAIL_CONFIG

logger = logging.getLogger(__name__)


# ============================================================
# 推送器基类
# ============================================================

class PersonalWechatNotifier:
    """个人微信推送类（默认使用 Server酱）"""

    def __init__(self, sendkey: str = None):
        """
        初始化推送器。
        参数:
            sendkey: Server酱 SENDKEY（可从 https://sct.ftqq.com/ 获取）
        """
        self.sendkey = sendkey or PUSH_CONFIG["serverchan_key"]
        self.method = PUSH_CONFIG["method"]
        self.enabled = PUSH_CONFIG["enabled"] and "your-sendkey-here" not in self.sendkey

        if not self.enabled:
            logger.warning("推送未启用或 SENDKEY 未配置，消息不会推送到微信")

    def send(self, title: str, content: str) -> bool:
        """
        推送消息到个人微信。

        参数:
            title: 消息标题（纯文本，最长 256 字符）
            content: 消息正文（支持 Markdown，最长 64KB）

        返回:
            True 表示发送成功
        """
        if not self.enabled:
            logger.info("推送未启用，跳过。内容预览:\n标题: %s\n正文前100字: %s",
                         title, content[:100])
            return False

        if self.method == "wechat_work":
            return self._send_via_wechat_work(title, content)
        else:
            return self._send_via_serverchan(title, content)

    def _send_via_serverchan(self, title: str, content: str) -> bool:
        """
        通过 Server酱 推送到个人微信。

        Server酱 API:
          POST https://sctapi.ftqq.com/{SENDKEY}.send
          Body: title=标题&desp=Markdown正文
        """
        url = PUSH_CONFIG["serverchan_url"].format(sendkey=self.sendkey)

        # Server酱限制: title 最长 256 字符, desp 最长 64KB
        payload = {
            "title": title[:256],
            "desp": content[:60000],
        }

        try:
            resp = requests.post(url, data=payload, timeout=15)
            result = resp.json()

            if result.get("code") == 0 or result.get("errno") == 0:
                logger.info("Server酱推送成功 → 个人微信")
                return True
            else:
                logger.error("Server酱推送失败: %s", result.get("message", result.get("msg", "未知错误")))
                return False
        except Exception as e:
            logger.error("Server酱推送异常: %s", e)
            return False

    def _send_via_wechat_work(self, title: str, content: str) -> bool:
        """
        通过企业微信机器人 Webhook 推送（兼容旧方案）。
        """
        webhook_url = WECHAT_CONFIG.get("webhook_url", "")
        if "your-key-here" in webhook_url:
            logger.warning("企业微信 Webhook 未配置，跳过推送")
            return False

        # 企业微信 Markdown 消息格式
        full_content = f"## {title}\n\n{content}"
        payload = {
            "msgtype": "markdown",
            "markdown": {"content": full_content[:4000]},
        }

        try:
            resp = requests.post(webhook_url, json=payload, timeout=10)
            result = resp.json()
            if result.get("errcode") == 0:
                logger.info("企业微信推送成功")
                return True
            else:
                logger.error("企业微信推送失败: %s", result.get("errmsg", "未知错误"))
                return False
        except Exception as e:
            logger.error("企业微信推送异常: %s", e)
            return False


# ============================================================
# 日报生成
# ============================================================

def generate_morning_report(matched_jobs: list, search_keywords: list) -> str:
    """
    生成早间搜岗日报（Markdown 格式）。
    参数:
        matched_jobs: 匹配度 ≥ 7 分的岗位列表
        search_keywords: 搜索关键词列表
    返回:
        Markdown 格式的日报文本
    """
    today_str = datetime.now().strftime("%Y-%m-%d")

    if not matched_jobs:
        return (
            f"## 🤖 51job 早间搜岗日报\n"
            f"> 日期：{today_str}\n\n"
            f"**今日搜岗结果：未找到高匹配度岗位**\n\n"
            f"搜索关键词：{'、'.join(search_keywords)}\n\n"
            f"---\n"
            f"*建议：可放宽搜索条件或调整关键词重试*"
        )

    lines = [
        f"## 🤖 51job 早间搜岗日报",
        f"> 日期：{today_str}",
        f"> 搜索关键词：{'、'.join(search_keywords)}",
        f"> 高匹配岗位数：**{len(matched_jobs)} 个**",
        "",
        "---",
        "",
    ]

    for i, job in enumerate(matched_jobs[:15], 1):  # 最多展示 15 个
        score = job.get("match_score", 0)
        star = "⭐" * min(5, int(score / 2))  # 星级评分
        job_name = job.get("job_name", "未知岗位")
        company = job.get("company_name", "未知公司")
        salary = job.get("salary", "未知")
        city = job.get("city", "未知")
        reason = job.get("match_reason", "")[:100]
        link = job.get("job_link", "")

        lines.append(f"### {i}. {job_name} | {star} {score}分")
        lines.append(f"")
        lines.append(f"- 🏢 **公司**：{company}")
        lines.append(f"- 💰 **薪资**：{salary}")
        lines.append(f"- 📍 **城市**：{city}")
        lines.append(f"- 💡 **匹配理由**：{reason}")
        if link:
            lines.append(f"- 🔗 [查看详情]({link})")
        lines.append(f"")

    lines.append("---")
    lines.append(f"*🤖 本报告由求职 Agent 自动生成 | {today_str}*")

    return "\n".join(lines)


def generate_morning_report_text(matched_jobs: list, search_keywords: list) -> str:
    """
    生成纯文本版日报（用于文本消息推送）。
    """
    today_str = datetime.now().strftime("%Y-%m-%d")

    if not matched_jobs:
        return (
            f"【51job早间日报】{today_str}\n"
            f"今日搜岗结果：未找到高匹配度岗位\n"
            f"搜索关键词：{'、'.join(search_keywords)}"
        )

    lines = [
        f"【51job早间日报】{today_str}",
        f"搜索关键词：{'、'.join(search_keywords)}",
        f"高匹配岗位数：{len(matched_jobs)} 个",
        "",
    ]

    for i, job in enumerate(matched_jobs[:10], 1):
        score = job.get("match_score", 0)
        lines.append(
            f"{i}. [{score}分] {job.get('job_name', '?')} | "
            f"{job.get('company_name', '?')} | {job.get('salary', '?')} | {job.get('city', '?')}"
        )

    return "\n".join(lines)


# ============================================================
# 晚报生成
# ============================================================

def generate_evening_report(stats: dict, messages: list) -> str:
    """
    生成晚间回复监控报告（Markdown 格式）。
    参数:
        stats: 消息统计字典
        messages: 消息列表
    返回:
        Markdown 格式的晚报文本
    """
    today_str = datetime.now().strftime("%Y-%m-%d")

    total = stats.get("total_new_replies", 0)
    read = stats.get("read_count", 0)
    interview = stats.get("interview_count", 0)
    replied = stats.get("replied_count", 0)

    lines = [
        f"## 🌙 51job晚间回复监控报告",
        f"> 日期：{today_str}",
        "",
        "### 📊 今日数据概览",
        "",
        f"| 指标 | 数量 |",
        f"|------|------|",
        f"| 今日新消息 | **{total}** |",
        f"| 已读消息 | {read} |",
        f"| 面试邀请 | 🎉 **{interview}** |",
        f"| 已回复消息 | {replied} |",
        "",
    ]

    if interview > 0:
        lines.append("### 🎉 面试邀请!")
        lines.append("")
        for msg in messages:
            if msg.get("msg_type") == "interview_invite":
                lines.append(f"- **{msg.get('company_name', '?')}** — {msg.get('content', '')[:100]}")
        lines.append("")

    if messages:
        lines.append("### 📬 消息列表")
        lines.append("")
        for i, msg in enumerate(messages[:10], 1):
            lines.append(
                f"{i}. **{msg.get('sender_name', '?')}** "
                f"({msg.get('company_name', '?')}) — {msg.get('content', '')[:80]}"
            )

    lines.append("")
    lines.append("---")
    lines.append(f"*🤖 本报告由求职 Agent 自动生成 | {today_str}*")

    return "\n".join(lines)


def generate_evening_report_text(stats: dict, messages: list) -> str:
    """
    生成纯文本版晚报（用于文本消息推送）。
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    total = stats.get("total_new_replies", 0)
    interview = stats.get("interview_count", 0)

    lines = [
        f"【51job晚间报告】{today_str}",
        f"今日新消息：{total} 条",
        f"面试邀请：{interview} 个",
        "",
    ]

    if interview > 0:
        lines.append("🎉 有面试邀请！请登录 51job 查看详情。")

    if messages:
        for i, msg in enumerate(messages[:5], 1):
            lines.append(
                f"{i}. {msg.get('sender_name', '?')}({msg.get('company_name', '?')}): "
                f"{msg.get('content', '')[:60]}"
            )

    return "\n".join(lines)


# ============================================================
# 邮件推送（备用通道）
# ============================================================

def send_email(subject: str, body: str, to_email: str = None) -> bool:
    """
    发送邮件通知（备用通道）。
    返回 True 表示发送成功。
    """
    if not EMAIL_CONFIG["enabled"]:
        logger.debug("邮件推送未启用")
        return False

    to_email = to_email or EMAIL_CONFIG["to_email"]
    if not to_email:
        logger.warning("未配置收件邮箱")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = EMAIL_CONFIG["smtp_user"]
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "html", "utf-8"))

        with smtplib.SMTP(EMAIL_CONFIG["smtp_host"], EMAIL_CONFIG["smtp_port"]) as server:
            server.starttls()
            server.login(EMAIL_CONFIG["smtp_user"], EMAIL_CONFIG["smtp_pass"])
            server.sendmail(EMAIL_CONFIG["smtp_user"], to_email, msg.as_string())

        logger.info("邮件发送成功: %s", to_email)
        return True

    except Exception as e:
        logger.error("邮件发送失败: %s", e)
        return False


# ============================================================
# LangChain Tool: WechatNotifier
# ============================================================

@tool
def wechat_notifier(message: str) -> str:
    """
    通过 Server酱 推送消息到个人微信。
    参数:
        message: 要推送的消息内容（支持 Markdown 格式）
    返回:
        推送结果的 JSON 字符串 {"success": true/false}
    """
    notifier = PersonalWechatNotifier()
    # 从消息中提取第一行作为标题
    lines = message.strip().split("\n")
    title = lines[0].lstrip("# ").strip() if lines else "求职 Agent 通知"
    content = "\n".join(lines) if len(lines) > 1 else message
    success = notifier.send(title=title, content=content)
    return json.dumps({"success": success}, ensure_ascii=False)


# ============================================================
# 快捷函数
# ============================================================

def push_morning_report(matched_jobs: list, search_keywords: list) -> dict:
    """推送早间日报到个人微信"""
    notifier = PersonalWechatNotifier()
    result = {"serverchan": False, "email": False}

    # 生成报告内容
    md_report = generate_morning_report(matched_jobs, search_keywords)
    today_str = datetime.now().strftime("%Y-%m-%d")
    title = f"🤖 51job 早间日报 - {today_str}"

    # Server酱 推送（内容直接走 Markdown，Server酱会渲染）
    result["serverchan"] = notifier.send(title=title, content=md_report)

    # 邮件备份（可选）
    if EMAIL_CONFIG["enabled"]:
        html_body = md_report.replace("\n", "<br>\n")
        result["email"] = send_email(
            f"51job早间日报 - {today_str}",
            html_body,
        )

    return result


def push_evening_report(stats: dict, messages: list) -> dict:
    """推送晚间报告到个人微信"""
    notifier = PersonalWechatNotifier()
    result = {"serverchan": False, "email": False}

    md_report = generate_evening_report(stats, messages)
    today_str = datetime.now().strftime("%Y-%m-%d")
    title = f"🌙 51job晚间报告 - {today_str}"

    result["serverchan"] = notifier.send(title=title, content=md_report)

    return result
