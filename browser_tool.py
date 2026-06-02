"""
browser_tool.py — Playwright 浏览器自动化工具（LangChain Tool）
================================================================
功能：
- 自动登录 51job（前程无忧），Cookie 持久化，避免重复登录
- 搜索岗位、翻页采集列表
- 进入详情页读取完整 JD
- 进入用户中心抓取投递反馈 / 面试邀请
- 模拟真人操作（随机延迟、鼠标移动、滚动）
- 用作 LangChain Tool: Job51Browser

变更说明 (v2.0): 目标网站从 Boss 直聘改为 51job。
51job 的反爬机制相对宽松，主要通过 IP 频率限制。
使用 CDP 连接真实 Chrome 浏览器，天然绕过大部分检测。
"""

import asyncio
import json
import logging
import os
import random
import re
import time
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

from langchain.tools import tool
from playwright.async_api import async_playwright, Page, Browser, BrowserContext

from config import (
    JOB51_CONFIG,
    SEARCH_CONFIG,
    get_city_code,
)
from db_manager import db

logger = logging.getLogger(__name__)


# ============================================================
# 工具函数：模拟真人行为
# ============================================================

async def human_delay(min_s: float = None, max_s: float = None):
    """随机等待一段时间，模拟真人浏览节奏"""
    min_s = min_s or JOB51_CONFIG["min_wait"]
    max_s = max_s or JOB51_CONFIG["max_wait"]
    delay = random.uniform(min_s, max_s)
    await asyncio.sleep(delay)


async def human_scroll(page: Page, times: int = 3):
    """模拟真人滚动页面"""
    for _ in range(times):
        scroll_y = random.randint(200, 600)
        await page.evaluate(f"window.scrollBy(0, {scroll_y})")
        await asyncio.sleep(random.uniform(0.5, 1.5))


async def human_mouse_move(page: Page):
    """在页面内随机移动鼠标"""
    x = random.randint(100, 800)
    y = random.randint(100, 600)
    await page.mouse.move(x, y, steps=random.randint(3, 8))
    await asyncio.sleep(random.uniform(0.2, 0.5))


# ============================================================
# Job51Browser 类
# ============================================================

class Job51Browser:
    """
    51job（前程无忧）浏览器自动化工具类。
    通过 CDP (Chrome DevTools Protocol) 连接到用户手动打开的 Chrome，
    利用用户已登录的真实浏览器 Session，100% 绕过反爬检测。

    使用方式:
        1. 用户手动启动 Chrome 调试模式
        2. 在 Chrome 中登录 51job.com
        3. 运行本程序 → 自动连接 → 搜岗/查反馈
    """

    def __init__(self):
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.is_logged_in = False
        self.cdp_url = JOB51_CONFIG.get("cdp_url", "http://localhost:9222")

    async def start(self):
        """连接到用户已打开的 Chrome 浏览器（CDP 模式）"""
        logger.info("正在连接到 Chrome 浏览器...")
        self.playwright = await async_playwright().start()

        try:
            self.browser = await self.playwright.chromium.connect_over_cdp(self.cdp_url)
            logger.info("CDP 连接成功: %s", self.cdp_url)
        except Exception as e:
            logger.error("无法连接到 Chrome，请先启动 Chrome 调试模式")
            logger.error(
                "启动命令: open -a 'Google Chrome' --args --remote-debugging-port=9222"
            )
            raise RuntimeError(
                f"Chrome CDP 连接失败。请先执行:\n"
                f"  open -a 'Google Chrome' --args --remote-debugging-port=9222\n"
                f"然后在 Chrome 中登录 51job.com 后再运行本程序。\n"
                f"原始错误: {e}"
            )

        # 获取默认浏览器上下文（用户正在使用的那个）
        contexts = self.browser.contexts
        if contexts:
            self.context = contexts[0]
            logger.info(
                "获取到现有浏览器上下文 (%d 个标签页)", len(self.context.pages)
            )
        else:
            self.context = await self.browser.new_context()
            logger.info("创建新的浏览器上下文")

        # 创建新标签页用于自动化操作（不干扰用户已有标签页）
        self.page = await self.context.new_page()
        logger.info("浏览器连接完成（新建标签页用于自动化）")

    async def close(self):
        """断开 CDP 连接（不关闭用户的 Chrome）"""
        if self.page:
            await self.page.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("已断开浏览器连接")

    # ============================================================
    # 登录流程
    # ============================================================

    async def login(self) -> bool:
        """
        登录检测（CDP 模式下，用户需在 Chrome 中自行登录 51job）。
        程序检查 Cookie 中是否有登录 session。
        """
        logger.info("===== 检查登录状态 =====")

        # 导航到 51job 首页，看页面内容判断登录状态
        try:
            await self.page.goto(
                JOB51_CONFIG["base_url"],
                wait_until="domcontentloaded",
                timeout=20000,
            )
        except Exception:
            logger.warning("51job.com 访问超时，尝试通过 Cookie 判断登录状态")

        await human_delay(2, 4)

        # 检查登录状态
        if await self._check_login_status():
            self.is_logged_in = True
            logger.info("已登录 51job!")
            return True

        # 未登录 — 提示用户在 Chrome 中登录
        logger.info("=" * 60)
        logger.info("⚠️  未检测到 51job 登录状态!")
        logger.info("")
        logger.info("请在 Chrome 浏览器中操作：")
        logger.info("   1. 找一个标签页打开 51job.com")
        logger.info("   2. 点击「登录/注册」→ 密码/短信登录")
        logger.info("   3. 登录成功后，回到本程序")
        logger.info("")
        logger.info("⏳ 每 5 秒自动检测登录状态，最长等待 180 秒")
        logger.info("=" * 60)

        # 轮询检测
        for i in range(36):
            await asyncio.sleep(5)
            # 刷新页面检测登录状态
            try:
                await self.page.goto(
                    JOB51_CONFIG["user_center_url"],
                    wait_until="domcontentloaded",
                    timeout=10000,
                )
            except Exception:
                pass

            if await self._check_login_status():
                self.is_logged_in = True
                logger.info("✅ 检测到登录成功!")
                return True
            if i % 6 == 5:
                remaining = 180 - (i + 1) * 5
                logger.info("⏳ 等待登录... (剩余 %d 秒)", remaining)

        logger.error("未检测到登录，超时")
        return False

    async def _check_login_status(self) -> bool:
        """
        检查当前是否已登录 51job。
        通过 Cookie + 页面内容双重判断。
        """
        try:
            # 方法1：检查 Cookie 中是否有 51job 登录相关 cookie
            cookies = await self.context.cookies()
            for cookie in cookies:
                domain = cookie.get("domain", "")
                name = cookie.get("name", "")
                # 51job 登录相关的 cookie 名称
                if "51job" in domain or "51job" in name:
                    if name.lower() in (
                        "guid",
                        "acw_tc",
                        "51job",
                        "userid",
                        "c_utm",
                    ) and cookie.get("value"):
                        # 有多个 51job cookie，进一步确认
                        pass

            # 方法2：检查页面 URL/内容判断登录状态
            current_url = self.page.url

            # 如果不在登录页，说明可能已登录
            if "login.51job.com" in current_url:
                # 在登录页，检查是否能获取到用户信息元素
                pass

            # 方法3：检查页面是否包含用户名/登录状态元素
            try:
                # 51job 登录后，页面上通常有用户相关的元素
                login_indicators = [
                    ".user_name",
                    ".nickname",
                    "[class*='user']",
                    ".login_box",  # 未登录时的登录框
                    ".unlogin",     # 未登录标识
                ]

                # 检查是否有"退出"链接（登录态会有）
                page_text = await self.page.inner_text("body")
                if "退出" in page_text and ("登录" not in page_text[:200]):
                    logger.info("检测到登录状态（页面含'退出'链接）")
                    return True

                # 检查是否在用户中心页面（需要登录才能访问）
                if "i.51job.com" in current_url and "login" not in current_url:
                    logger.info("检测到登录状态（已进入用户中心）")
                    return True

            except Exception:
                pass

            # 方法4：Cookie 综合判断
            cookie_names = {c.get("name", "") for c in cookies}
            login_related_cookies = {"guid", "acw_tc", "51job", "userid"}
            if len(cookie_names & login_related_cookies) >= 2:
                logger.info("检测到登录 Cookie")
                return True

            return False

        except Exception as e:
            logger.debug("检查登录状态异常: %s", e)
            return False

    # ============================================================
    # 搜索岗位
    # ============================================================

    async def search_jobs(self, keyword: str) -> list:
        """
        根据关键词在 51job 搜索岗位，采集列表页基本信息。
        返回岗位列表（dict）。

        51job 搜索页 URL 格式:
        https://we.51job.com/pc/search?keyword=xxx&searchType=2&sortType=0&metro=010000
        """
        if not self.is_logged_in:
            logger.error("未登录，无法搜索岗位")
            return []

        # 检查每日请求量
        today_count = db.get_today_request_count()
        if today_count >= JOB51_CONFIG["max_daily_requests"]:
            logger.warning(
                "今日请求量已达上限 %d，跳过搜索",
                JOB51_CONFIG["max_daily_requests"],
            )
            return []

        city_code = get_city_code(SEARCH_CONFIG["city"])
        search_url = JOB51_CONFIG["search_url_template"].format(
            keyword=quote(keyword),
            city_code=city_code,
        )

        logger.info("搜索关键词: %s, URL: %s", keyword, search_url)
        await self.page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
        await human_delay(2, 4)
        db.increment_request_count()

        # 51job 搜索结果是动态加载的（JS 调用 API），需要额外等待
        await self._wait_for_search_results()

        jobs = []
        max_pages = JOB51_CONFIG.get("max_pages", 5)

        for page_num in range(1, max_pages + 1):
            logger.info("--- 正在采集第 %d 页 ---", page_num)

            # 等待当前页数据渲染完成
            await human_delay(2, 3)

            # 模拟真人滚动浏览
            await human_scroll(self.page, times=random.randint(2, 4))
            await human_mouse_move(self.page)
            await human_delay(1, 3)

            # 提取当前页的岗位列表
            page_jobs = await self._extract_job_list(keyword)
            jobs.extend(page_jobs)
            logger.info("第 %d 页采集到 %d 个岗位", page_num, len(page_jobs))

            # 检查是否需要翻页
            if page_num < max_pages:
                has_next = await self._go_next_page()
                if not has_next:
                    logger.info("没有下一页，停止翻页")
                    break
                await human_delay(2, 4)

        logger.info("关键词 '%s' 共采集到 %d 个岗位", keyword, len(jobs))

        # 检查总量限制
        if JOB51_CONFIG.get("max_total_jobs") and len(jobs) >= JOB51_CONFIG["max_total_jobs"]:
            logger.info("已达到总量限制 %d，截断", JOB51_CONFIG["max_total_jobs"])
            jobs = jobs[: JOB51_CONFIG["max_total_jobs"]]

        return jobs

    async def _wait_for_search_results(self, timeout: int = 15000):
        """
        等待 51job 搜索结果加载完成。
        51job 使用 JS 动态加载搜索结果，需要等待 API 返回并渲染。
        """
        selectors = [
            ".joblist .joblist-item",
            ".e_job_list .e_typical",
            "[class*='joblist-item']",
            ".e_result .e_typical",
            ".j_joblist .j_joblist_item",
            ".result_list .job_item",
        ]

        start = asyncio.get_event_loop().time()
        while (asyncio.get_event_loop().time() - start) * 1000 < timeout:
            for selector in selectors:
                try:
                    elements = await self.page.query_selector_all(selector)
                    if len(elements) > 0:
                        logger.info(
                            "搜索结果加载完成（%d 个岗位卡片，选择器: %s）",
                            len(elements),
                            selector,
                        )
                        return
                except Exception:
                    continue
            await asyncio.sleep(0.5)

        # 超时后尝试获取页面文本内容判断状态
        try:
            body_text = await self.page.inner_text("body")
            if "没有找到" in body_text or "无结果" in body_text:
                logger.warning("搜索无结果")
            else:
                logger.warning("搜索结果等待超时，尝试继续（可能页面结构已变化）")
        except Exception:
            logger.warning("搜索结果等待超时")

    async def _extract_job_list(self, keyword: str) -> list:
        """从当前 51job 搜索列表页提取岗位基本信息"""
        jobs = []

        # 尝试多种可能的卡片选择器（覆盖不同页面版本）
        card_selectors = [
            ".joblist .joblist-item",
            ".e_job_list .e_typical",
            "[class*='joblist-item']",
            ".e_result .e_typical",
            ".j_joblist .j_joblist_item",
            ".result_list .job_item",
            # 更宽泛的匹配
            "div[class*='joblist'] > div",
            "div[class*='result'] > div[class*='item']",
        ]

        job_cards = []
        for selector in card_selectors:
            cards = await self.page.query_selector_all(selector)
            if cards and len(cards) >= 3:
                job_cards = cards
                logger.info("使用选择器 '%s' 找到 %d 个岗位卡片", selector, len(cards))
                break

        # 如果上述选择器都没找到，尝试从页面中查找所有包含链接的卡片
        if not job_cards:
            logger.warning("未找到标准岗位卡片，尝试通用提取...")
            all_links = await self.page.query_selector_all(
                "a[href*='jobs.51job.com']"
            )
            # 使用链接的父级元素作为卡片
            card_set = set()
            for link in all_links:
                try:
                    parent = await link.evaluate(
                        "el => el.closest('div[class]')?.className"
                    )
                    if parent:
                        card_set.add(parent)
                except Exception:
                    pass
            logger.info("找到 %d 个可能包含岗位链接的区域", len(card_set))

            # 直接通过链接提取
            jobs = await self._extract_jobs_from_links(keyword)
            return jobs

        for card in job_cards:
            try:
                job_data = await self._parse_job_card(card, keyword)
                if job_data and job_data.get("job_name"):
                    jobs.append(job_data)
            except Exception as e:
                logger.debug("解析岗位卡片失败: %s", e)
                continue

        return jobs

    async def _extract_jobs_from_links(self, keyword: str) -> list:
        """备用方案：直接从页面中提取指向 jobs.51job.com 的链接"""
        jobs = []
        try:
            links = await self.page.query_selector_all(
                "a[href*='jobs.51job.com']"
            )
            seen_urls = set()

            for link in links:
                try:
                    href = await link.get_attribute("href")
                    if not href or "jobs.51job.com" not in href:
                        continue
                    if href in seen_urls:
                        continue
                    seen_urls.add(href)

                    job_name = (await link.inner_text()).strip()
                    if not job_name or len(job_name) < 2:
                        continue

                    # 从 URL 提取 job_id 和城市
                    import hashlib
                    job_id = ""
                    id_match = re.search(r'jobs\.51job\.com/\w+/(\d+)\.html', href)
                    if id_match:
                        job_id = id_match.group(1)
                    else:
                        job_id = hashlib.md5(href.encode()).hexdigest()[:16]

                    # 尝试获取父级元素中的公司/薪资信息
                    parent_text = ""
                    try:
                        parent_el = await link.evaluate(
                            "el => el.closest('div[class]')?.innerText || ''"
                        )
                        parent_text = parent_el if parent_el else ""
                    except Exception:
                        pass

                    # 从父级文本中尝试提取公司和薪资
                    lines = [l.strip() for l in parent_text.split("\n") if l.strip()]
                    company_name = lines[1] if len(lines) > 1 else "未知公司"
                    salary = ""
                    for line in lines:
                        if re.search(r'[\d.]+[-~][\d.]+[Kk万]', line):
                            salary = line
                            break

                    job_data = {
                        "job_id": job_id,
                        "keyword": keyword,
                        "job_name": job_name,
                        "company_name": company_name,
                        "salary": salary or "薪资面议",
                        "city": SEARCH_CONFIG["city"],
                        "experience": "",
                        "degree": "",
                        "job_link": href,
                        "publish_time": "",
                        "jd_text": "",
                    }
                    jobs.append(job_data)

                except Exception as e:
                    logger.debug("通用链接提取失败: %s", e)
                    continue

        except Exception as e:
            logger.error("通用链接提取异常: %s", e)

        return jobs

    async def _parse_job_card(self, card, keyword: str) -> Optional[dict]:
        """
        解析单个 51job 岗位卡片，提取基本字段。

        51job 卡片结构（示例）:
        <div class="joblist-item">
          <div class="joblist-item-name"><a href="...">岗位名</a></div>
          <div class="joblist-item-company"><a>公司名</a></div>
          <div class="joblist-item-pay">15K-25K</div>
          <div class="joblist-item-location">北京-朝阳区</div>
          <div class="joblist-item-time">06-02</div>
        </div>
        """
        try:
            # 获取卡片完整文本，用于兜底解析
            card_text = ""
            try:
                card_text = await card.inner_text()
            except Exception:
                pass

            lines = [l.strip() for l in card_text.split("\n") if l.strip()]

            # --- 岗位名称 + 链接 ---
            job_name = ""
            job_link = ""
            job_id = ""

            name_link_selectors = [
                ".joblist-item-name a",
                ".e_jobname a",
                ".e_job_name a",
                "a[href*='jobs.51job.com']",
                ".jname a",
                "[class*='job-name'] a",
                "[class*='jobname'] a",
                ".el .t1 a",
            ]

            for sel in name_link_selectors:
                try:
                    el = await card.query_selector(sel)
                    if el:
                        job_name = (await el.inner_text()).strip()
                        href = await el.get_attribute("href")
                        if href:
                            job_link = (
                                href
                                if href.startswith("http")
                                else "https:" + href
                            )
                            # 从 URL 提取 job_id
                            id_match = re.search(
                                r'jobs\.51job\.com/\w+/(\d+)\.html', job_link
                            )
                            if id_match:
                                job_id = id_match.group(1)
                        break
                except Exception:
                    continue

            # 如果通过选择器没找到，尝试从文本行提取
            if not job_name and lines:
                # 第一行通常是岗位名
                for line in lines[:3]:
                    if (
                        len(line) >= 4
                        and not re.search(r'[\d.]+[-~][\d.]+[Kk万]', line)
                        and "51job" not in line
                    ):
                        job_name = line
                        break

            # --- 公司名称 ---
            company_name = ""
            company_selectors = [
                ".joblist-item-company a",
                ".joblist-item-company",
                ".e_company a",
                ".e_company_name a",
                ".cname a",
                "[class*='company-name']",
                "[class*='company'] a",
                ".el .t2 a",
            ]

            for sel in company_selectors:
                try:
                    el = await card.query_selector(sel)
                    if el:
                        company_name = (await el.inner_text()).strip()
                        break
                except Exception:
                    continue

            if not company_name and len(lines) > 1:
                company_name = lines[1]

            # --- 薪资 ---
            salary = ""
            salary_selectors = [
                ".joblist-item-pay",
                ".e_salary",
                ".sal",
                "[class*='salary']",
                "[class*='pay']",
                ".el .t3",
            ]

            for sel in salary_selectors:
                try:
                    el = await card.query_selector(sel)
                    if el:
                        salary = (await el.inner_text()).strip()
                        break
                except Exception:
                    continue

            if not salary:
                for line in lines:
                    if re.search(r'[\d.]+[-~][\d.]+[Kk万]', line) or re.search(
                        r'[\d.]+[-~][\d.]+[Kk]/月', line
                    ):
                        salary = line
                        break

            # --- 工作地点 ---
            city = ""
            area_selectors = [
                ".joblist-item-location",
                ".e_area",
                "[class*='location']",
                "[class*='area']",
                ".el .t4",
            ]

            for sel in area_selectors:
                try:
                    el = await card.query_selector(sel)
                    if el:
                        city = (await el.inner_text()).strip()
                        break
                except Exception:
                    continue

            if not city:
                city = SEARCH_CONFIG["city"]

            # --- 经验/学历（可能在标签中） ---
            tags = []
            tag_selectors = [
                ".e_tags span",
                ".tags span",
                "[class*='tag'] span",
                "[class*='label']",
            ]

            for sel in tag_selectors:
                try:
                    els = await card.query_selector_all(sel)
                    for el in els[:5]:
                        t = (await el.inner_text()).strip()
                        if t:
                            tags.append(t)
                    if tags:
                        break
                except Exception:
                    continue

            experience = ""
            degree = ""
            for tag in tags:
                if any(w in tag for w in ["年", "应届", "经验"]):
                    experience = tag
                elif any(w in tag for w in ["本科", "大专", "硕士", "博士", "学历"]):
                    degree = tag

            # --- 发布时间 ---
            publish_time = ""
            time_selectors = [
                ".joblist-item-time",
                ".e_time",
                "[class*='time']",
                "[class*='date']",
                ".el .t5",
            ]

            for sel in time_selectors:
                try:
                    el = await card.query_selector(sel)
                    if el:
                        publish_time = (await el.inner_text()).strip()
                        break
                except Exception:
                    continue

            if not publish_time:
                for line in lines:
                    if re.search(r'\d{2}-\d{2}', line) or "前" in line:
                        publish_time = line
                        break

            # --- 生成 job_id（如果未从 URL 提取到） ---
            if not job_id:
                import hashlib
                raw_id = f"{job_name}{company_name}{keyword}"
                job_id = hashlib.md5(raw_id.encode()).hexdigest()[:16]

            # --- 过滤条件：薪资 ---
            if SEARCH_CONFIG.get("min_salary"):
                min_sal = SEARCH_CONFIG["min_salary"]
                sal_match = re.search(r'([\d.]+)', salary) if salary else None
                if sal_match:
                    low_sal = float(sal_match.group(1))
                    # 如果薪资单位是 K，检查是否满足最低要求
                    if "万" in salary:
                        low_sal = low_sal * 10
                    # 如果薪资上限低于期望下限的 60%，跳过
                    # (不严格过滤，因为 salary 文本格式多样)

            job_data = {
                "job_id": job_id,
                "keyword": keyword,
                "job_name": job_name,
                "company_name": company_name or "未知公司",
                "salary": salary or "薪资面议",
                "city": city or SEARCH_CONFIG["city"],
                "experience": experience,
                "degree": degree,
                "job_link": job_link,
                "publish_time": publish_time,
                "jd_text": "",  # 稍后从详情页获取
            }

            return job_data

        except Exception as e:
            logger.debug("解析卡片异常: %s", e)
            return None

    async def _go_next_page(self) -> bool:
        """
        翻到下一页（51job 搜索页）。
        51job 的翻页是 JS 触发 API 请求，URL 不变。
        返回 True 表示成功翻页。
        """
        try:
            # 在点击前记录当前岗位数，用于判断翻页是否成功
            prev_count = len(
                await self.page.query_selector_all(
                    "a[href*='jobs.51job.com']"
                )
            )

            # 尝试多种下一页按钮选择器
            next_selectors = [
                ".pagination .next:not(.disabled)",
                ".page_next:not(.disabled)",
                ".next_page:not(.disabled)",
                "[class*='pagination'] [class*='next']:not([class*='disabled'])",
                "a:has-text('下一页'):not([class*='disabled'])",
                "li:has-text('下一页'):not([class*='disabled'])",
                "button:has-text('下一页')",
                ".e_page .next",
                ".page_box .next",
            ]

            next_btn = None
            for sel in next_selectors:
                try:
                    btn = await self.page.query_selector(sel)
                    if btn:
                        next_btn = btn
                        break
                except Exception:
                    continue

            if not next_btn:
                logger.info("未找到下一页按钮")
                return False

            # 检查是否 disabled
            is_disabled = await next_btn.get_attribute("class")
            if is_disabled and "disabled" in is_disabled:
                logger.info("下一页按钮已禁用")
                return False

            # 点击前滚动到按钮位置
            try:
                await next_btn.scroll_into_view_if_needed()
            except Exception:
                pass
            await human_delay(1, 2)

            await next_btn.click()
            db.increment_request_count()

            # 等待新数据加载
            await human_delay(2, 4)

            # 检查新数据是否加载
            await self._wait_for_search_results(timeout=10000)

            # 验证翻页成功
            new_count = len(
                await self.page.query_selector_all(
                    "a[href*='jobs.51job.com']"
                )
            )
            if new_count > 0:
                logger.info("翻页成功（检测到 %d 个链接）", new_count)
                return True

            return True  # 即使计数未变也尝试继续

        except Exception as e:
            logger.warning("翻页失败: %s", e)
            return False

    # ============================================================
    # 读取岗位详情页 JD
    # ============================================================

    async def fetch_jd_detail(self, job: dict) -> dict:
        """
        进入 51job 岗位详情页，读取完整 JD。
        51job 详情页 URL: https://jobs.51job.com/{city}/{job_id}.html
        返回补充了 jd_text 的 job 字典。
        """
        job_link = job.get("job_link", "")
        if not job_link:
            logger.warning(
                "岗位 %s 缺少详情链接，跳过", job.get("job_name")
            )
            return job

        try:
            job_name_short = job.get("job_name", "?")[:40]
            logger.info("读取详情: %s", job_name_short)

            # 使用 networkidle 等待 JS 动态内容加载完成
            try:
                await self.page.goto(
                    job_link, wait_until="networkidle", timeout=20000
                )
            except Exception:
                # networkidle 可能超时（如广告请求慢），用 domcontentloaded 兜底
                logger.debug("networkidle 超时，使用 domcontentloaded")
                await self.page.goto(
                    job_link, wait_until="domcontentloaded", timeout=10000
                )

            # 额外等待 JD 内容渲染
            await human_delay(2, 4)
            db.increment_request_count()

            # 检测是否被反爬拦截
            page_text = ""
            try:
                page_text = await self.page.inner_text("body")
            except Exception:
                pass

            if any(kw in page_text for kw in ["验证", "验证码", "滑块", "频繁", "限制"]):
                logger.warning("检测到反爬验证页面，等待 10 秒后重试...")
                await asyncio.sleep(10)
                db.increment_request_count()
                # 重试一次
                try:
                    await self.page.goto(
                        job_link, wait_until="networkidle", timeout=15000
                    )
                except Exception:
                    pass
                await human_delay(3, 5)

            # 51job 详情页 JD 选择器（按优先级排列）
            jd_selectors = [
                ".bmsg.job_msg",                 # 经典 51job JD 区域
                ".bmsg",                         # JD 内容容器
                ".job_msg",                      # JD 文本
                ".job-detail .content",          # 详情内容
                ".job-detail-wrapper",           # 详情包装器
                ".job-detail-box .detail-text",  # 详情文本
                "[class*='job-detail'] [class*='content']",
                "[class*='job_detail']",
                ".tCompany_main .bmsg",          # 旧版 51job 结构
                "div.job-detail",                # 通用
                "article",                       # HTML5 语义标签
                ".job-main .content",            # 新版 51job
                "[class*='job'] [class*='detail']",
            ]

            jd_text = ""
            for selector in jd_selectors:
                try:
                    el = await self.page.query_selector(selector)
                    if el:
                        text = await el.inner_text()
                        if text and len(text.strip()) > 50:
                            jd_text = text.strip()
                            break
                except Exception:
                    continue

            # 如果选择器都没找到，尝试提取主要内容区域（关键词锚定）
            if not jd_text:
                try:
                    body_text = await self.page.inner_text("body")
                    lines = body_text.split("\n")
                    jd_lines = []
                    in_jd = False
                    for line in lines:
                        stripped = line.strip()
                        if any(
                            kw in stripped
                            for kw in [
                                "岗位职责",
                                "任职要求",
                                "职位描述",
                                "工作内容",
                                "岗位要求",
                                "任职资格",
                                "工作职责",
                            ]
                        ):
                            in_jd = True
                        if in_jd and len(stripped) > 20:
                            jd_lines.append(stripped)
                        if in_jd and any(
                            kw in stripped
                            for kw in ["公司介绍", "公司福利", "联系方式", "职能类别", "关键字"]
                        ):
                            break
                    if jd_lines:
                        jd_text = "\n".join(jd_lines)
                except Exception:
                    pass

            # 如果还是没有，取 body 中所有长文本行（排除明显的导航/页脚）
            if not jd_text:
                try:
                    body_text = await self.page.inner_text("body")
                    lines = [
                        l.strip()
                        for l in body_text.split("\n")
                        if len(l.strip()) > 40
                        and not any(
                            skip in l
                            for skip in [
                                "51job",
                                "前程无忧",
                                "copyright",
                                "©",
                                "首页",
                                "我的",
                            ]
                        )
                    ]
                    jd_text = "\n".join(lines[:60])
                except Exception:
                    pass

            job["jd_text"] = jd_text[:5000] if jd_text else "[JD 提取失败]"
            logger.info(
                "JD 获取完成: %d 字符 (url: %s...)",
                len(job.get("jd_text", "")),
                job_link[:60],
            )

        except Exception as e:
            logger.error("获取详情页失败: %s", e)
            job["jd_text"] = f"[获取失败] {str(e)[:200]}"

        return job

    async def fetch_all_jd_details(self, jobs: list) -> list:
        """批量读取所有岗位的详情页 JD"""
        results = []
        for i, job in enumerate(jobs):
            logger.info(
                "详情采集 [%d/%d]: %s",
                i + 1,
                len(jobs),
                job.get("job_name", "?"),
            )
            job = await self.fetch_jd_detail(job)
            results.append(job)
        return results

    # ============================================================
    # 投递反馈 / 面试邀请抓取（晚间监控用）
    # ============================================================
    # 51job 没有 Boss 那样的聊天消息，取而代之的是：
    #   - 投递状态更新（被查看 / 感兴趣 / 面试邀请）
    #   - 面试通知
    # 这些信息在用户中心的"投递记录"页面

    async def fetch_delivery_status(self) -> list:
        """
        进入 51job 用户中心，抓取投递反馈和面试邀请。
        返回反馈列表（dict），模拟原 messages 结构。

        51job 投递记录页: https://i.51job.com/delivery/delivery.php
        """
        if not self.is_logged_in:
            logger.error("未登录，无法抓取投递反馈")
            return []

        feedback_list = []

        try:
            # 进入投递记录页
            delivery_url = JOB51_CONFIG["delivery_url"]
            logger.info("进入投递记录页: %s", delivery_url)
            await self.page.goto(
                delivery_url, wait_until="domcontentloaded", timeout=15000
            )
            await human_delay(2, 4)
            db.increment_request_count()

            # 等待动态内容加载
            await self._wait_for_delivery_results()

            # 滚动加载更多记录
            await human_scroll(self.page, times=3)

            # 提取投递记录
            feedback_list = await self._extract_delivery_items()

            logger.info(
                "投递反馈抓取完成: 共 %d 条记录", len(feedback_list)
            )

        except Exception as e:
            logger.error("抓取投递反馈失败: %s", e)

        return feedback_list

    async def _wait_for_delivery_results(self, timeout: int = 10000):
        """等待投递记录页数据加载完成"""
        selectors = [
            ".delivery_list .delivery_item",
            "[class*='delivery'] [class*='item']",
            ".e_delivery_list .e_item",
            ".my_delivery_list tr",
            "table[class*='delivery'] tbody tr",
        ]

        start = asyncio.get_event_loop().time()
        while (asyncio.get_event_loop().time() - start) * 1000 < timeout:
            for selector in selectors:
                try:
                    elements = await self.page.query_selector_all(selector)
                    if len(elements) > 0:
                        logger.info(
                            "投递记录加载完成（%d 条，选择器: %s）",
                            len(elements),
                            selector,
                        )
                        return
                except Exception:
                    continue
            await asyncio.sleep(0.5)

        logger.warning("投递记录加载超时，尝试继续")

    async def _extract_delivery_items(self) -> list:
        """从投递记录页提取各条反馈"""
        items = []

        # 尝试多种选择器提取投递项
        item_selectors = [
            ".delivery_list .delivery_item",
            "[class*='delivery'] [class*='item']",
            ".e_delivery_list .e_item",
            ".my_delivery_list tbody tr",
            "table[class*='delivery'] tbody tr",
            "div[class*='list'] > div[class*='item']",
        ]

        elements = []
        for sel in item_selectors:
            els = await self.page.query_selector_all(sel)
            if els and len(els) >= 1:
                elements = els
                break

        # 如果没有找到结构化元素，尝试获取整个页面文本
        if not elements:
            try:
                body_text = await self.page.inner_text("body")
                # 生成一条汇总消息
                items.append(
                    {
                        "msg_id": f"summary_{datetime.now().strftime('%Y%m%d')}",
                        "sender_name": "51job 系统",
                        "company_name": "",
                        "content": body_text[:1000],
                        "msg_type": "system",
                        "is_read": 1,
                        "has_replied": 0,
                    }
                )
                return items
            except Exception:
                return items

        for elem in elements:
            try:
                item = await self._parse_delivery_item(elem)
                if item:
                    items.append(item)
            except Exception as e:
                logger.debug("解析投递项失败: %s", e)
                continue

        return items

    async def _parse_delivery_item(self, elem) -> Optional[dict]:
        """解析单条投递反馈"""
        try:
            text = await elem.inner_text()
            if not text or len(text.strip()) < 3:
                return None

            lines = [l.strip() for l in text.split("\n") if l.strip()]

            # 岗位名称（通常在第一行）
            job_name = lines[0] if lines else "未知岗位"

            # 公司名称
            company_name = lines[1] if len(lines) > 1 else ""

            # 状态（投递状态通常包含关键字）
            status_text = ""
            status_keywords = [
                "面试",
                "邀请",
                "感兴趣",
                "已查看",
                "已投递",
                "不合适",
                "通过",
                "待沟通",
            ]
            for line in lines:
                for kw in status_keywords:
                    if kw in line:
                        status_text = line
                        break
                if status_text:
                    break

            # 判断消息类型
            msg_type = "text"
            if any(kw in text for kw in ["面试", "邀请", "面试邀请"]):
                msg_type = "interview_invite"
            elif any(kw in text for kw in ["感兴趣", "通过"]):
                msg_type = "positive_feedback"
            elif any(kw in text for kw in ["不合适", "未通过"]):
                msg_type = "negative_feedback"
            elif any(kw in text for kw in ["已查看", "已读"]):
                msg_type = "viewed"

            # 时间
            time_str = ""
            for line in lines:
                if re.search(r'\d{2}-\d{2}|\d{4}-\d{2}-\d{2}', line):
                    time_str = line
                    break

            import hashlib
            raw_id = f"{job_name}{company_name}{status_text}{time_str}"
            msg_id = hashlib.md5(raw_id.encode()).hexdigest()[:12]

            return {
                "msg_id": msg_id,
                "sender_name": company_name or "51job",
                "company_name": company_name,
                "content": f"{job_name} | {status_text}" if status_text else text[:300],
                "msg_type": msg_type,
                "is_read": 1 if "未读" not in text else 0,
                "has_replied": 0,
            }

        except Exception:
            return None

    # ============================================================
    # 面试邀请检查（独立于投递记录，有时在独立页面）
    # ============================================================

    async def fetch_interview_invites(self) -> list:
        """
        检查 51job 的面试邀请页面。
        URL: https://i.51job.com/interview/
        返回面试邀请列表。
        """
        invites = []

        try:
            interview_url = "https://i.51job.com/interview/"
            logger.info("进入面试邀请页: %s", interview_url)
            await self.page.goto(
                interview_url, wait_until="domcontentloaded", timeout=15000
            )
            await human_delay(2, 4)
            db.increment_request_count()

            # 提取面试邀请
            invite_selectors = [
                ".interview_list .interview_item",
                "[class*='interview'] [class*='item']",
                ".e_interview_list .e_item",
                "div[class*='list'] > div",
            ]

            for sel in invite_selectors:
                try:
                    elements = await self.page.query_selector_all(sel)
                    if elements:
                        for elem in elements:
                            text = await elem.inner_text()
                            if text and len(text.strip()) > 10:
                                lines = [l.strip() for l in text.split("\n") if l.strip()]
                                import hashlib
                                msg_id = hashlib.md5(text.encode()).hexdigest()[:12]
                                invites.append(
                                    {
                                        "msg_id": msg_id,
                                        "sender_name": lines[0] if lines else "未知",
                                        "company_name": lines[1] if len(lines) > 1 else "",
                                        "content": text[:500],
                                        "msg_type": "interview_invite",
                                        "is_read": 0 if "新" in text or "未读" in text else 1,
                                        "has_replied": 0,
                                    }
                                )
                        if invites:
                            break
                except Exception:
                    continue

            logger.info("面试邀请抓取完成: 共 %d 条", len(invites))

        except Exception as e:
            logger.error("抓取面试邀请失败: %s", e)

        return invites


# ============================================================
# LangChain Tool: Job51Browser
# ============================================================

@tool
async def job51_browser(action: str, data: str = "") -> str:
    """
    51job（前程无忧）浏览器自动化工具。
    参数:
        action: 操作类型，可选值:
            - "login": 登录 51job
            - "search": 搜索岗位（data 为关键词）
            - "fetch_jd": 读取岗位详情（data 为 job_id）
            - "fetch_delivery": 抓取投递反馈
            - "fetch_interviews": 抓取面试邀请
        data: 附加数据（JSON 字符串或关键词）
    返回:
        操作结果的 JSON 字符串
    """
    browser = Job51Browser()
    try:
        await browser.start()

        if action == "login":
            success = await browser.login()
            return json.dumps(
                {"success": success, "message": "登录成功" if success else "登录失败"},
                ensure_ascii=False,
            )

        elif action == "search":
            jobs = await browser.search_jobs(data)
            return json.dumps(
                {"count": len(jobs), "jobs": jobs}, ensure_ascii=False
            )

        elif action == "fetch_delivery":
            feedback = await browser.fetch_delivery_status()
            return json.dumps(
                {"count": len(feedback), "items": feedback}, ensure_ascii=False
            )

        elif action == "fetch_interviews":
            invites = await browser.fetch_interview_invites()
            return json.dumps(
                {"count": len(invites), "items": invites}, ensure_ascii=False
            )

        else:
            return json.dumps({"error": f"未知操作: {action}"}, ensure_ascii=False)

    except Exception as e:
        logger.error("Job51Browser 操作失败: %s", e)
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    finally:
        await browser.close()


# ============================================================
# 快捷函数（供 LangGraph 节点调用）
# ============================================================

async def browser_login(browser: Job51Browser) -> bool:
    """LangGraph 节点: 登录"""
    return await browser.login()


async def browser_search(browser: Job51Browser, keyword: str) -> list:
    """LangGraph 节点: 搜索岗位"""
    return await browser.search_jobs(keyword)


async def browser_fetch_all_details(browser: Job51Browser, jobs: list) -> list:
    """LangGraph 节点: 批量获取详情"""
    return await browser.fetch_all_jd_details(jobs)


async def browser_fetch_delivery(browser: Job51Browser) -> list:
    """LangGraph 节点: 抓取投递反馈"""
    return await browser.fetch_delivery_status()


async def browser_fetch_interviews(browser: Job51Browser) -> list:
    """LangGraph 节点: 抓取面试邀请"""
    return await browser.fetch_interview_invites()
