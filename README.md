# 🤖 51job（前程无忧）求职自动化 Agent

每天自动在 51job 搜索岗位、AI 筛选匹配度、生成投递报告、晚间自动检查投递反馈/面试邀请，并推送到个人微信。

> **v2.0 变更说明**：目标网站从 Boss 直聘改为 **51job（前程无忧）**，原因：
> - Boss 直聘的反爬机制极强（JS 加密、行为检测、滑块验证码），自动化成功率低
> - 51job 的反爬相对宽松，搜索参数明文，页面结构更稳定
> - 51job 支持短信/密码登录，无需 App 扫码，自动化更友好

## 功能概览

### 1. 早间搜岗 Agent（8:00）
- ✅ 自动登录 51job（Cookie 持久化，免重复登录）
- ✅ 根据配置关键词搜索岗位（支持：城市、薪资、经验、学历）
- ✅ 进入详情页读取完整 JD
- ✅ AI 对 JD 与简历做匹配度打分（0-10分）
- ✅ 自动去重（不重复处理同一岗位）
- ✅ 筛选匹配度 ≥ 7 分的岗位
- ✅ 生成 Markdown 日报
- ✅ 推送到个人微信

### 2. 晚间投递反馈监控 Agent（20:00）
- ✅ 自动登录 51job
- ✅ 抓取投递反馈（HR 查看/感兴趣/面试邀请等）
- ✅ 抓取面试邀请页
- ✅ 统计分析：今日反馈数、面试数
- ✅ 生成晚间报告推送微信
- ✅ 数据保存到数据库

## 技术栈

| 技术 | 用途 |
|------|------|
| **LangChain + LangGraph** | 工作流编排、工具调用、状态管理 |
| **Playwright (CDP模式)** | 连接已有 Chrome，100% 绕过反爬 |
| **通义千问 / OpenAI / 本地LLM** | AI 匹配打分 |
| **schedule** | 定时任务调度 |
| **SQLite3** | 数据存储（岗位、反馈、去重、日志） |
| **Server酱 / 企业微信 Webhook** | 消息推送到个人微信 |

## 快速开始

### 1. 环境准备

```bash
# 进入项目目录
cd boss-job-agent

# 创建虚拟环境（推荐）
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 一键安装依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器
playwright install chromium
```

### 2. 配置

编辑 `config.py` 文件，修改以下配置：

#### a. 简历配置
```python
# 将你的个人简历摘要写在这里
RESUME_TEXT = """
【个人简介】
- 姓名：你的名字
- 学历：本科 / 计算机科学与技术
...
"""
```

#### b. 搜索条件
```python
SEARCH_CONFIG = {
    "keywords": ["Python 后端开发", "Python 工程师"],  # 搜索关键词
    "city": "北京",              # 目标城市
    "min_salary": 15,           # 最低薪资（K）
    "experience": "02",         # 经验要求: 01=应届生, 02=1-3年, 03=3-5年
    "degree": "03",             # 学历: 01=高中, 02=大专, 03=本科, 04=硕士
}
```

#### c. AI 配置（三选一）

**方式1：通义千问（推荐）**
```bash
# 设置环境变量
export DASHSCOPE_API_KEY="sk-your-api-key"
```
获取 API Key: https://dashscope.aliyun.com/

**方式2：OpenAI 兼容 API**
```python
AI_CONFIG = {
    "provider": "openai",
    "openai_api_key": "sk-xxx",
    "openai_base_url": "https://api.openai.com/v1",
}
```

**方式3：本地 LLM（Ollama）**
```bash
# 先安装 Ollama 并拉取模型
ollama pull qwen2.5:7b
```

#### d. 微信推送（Server酱）

1. 注册 Server酱: https://sct.ftqq.com/
2. 在 "发送消息" 页面获取 SENDKEY
3. 配置环境变量：
```bash
export SCT_SENDKEY="SCTxxxxx"
```

### 3. 登录 51job

**首次使用需要手动登录一次，之后 Cookie 会自动保持，无需重复登录。**

```bash
# 终端 1：启动 Chrome 调试模式
open -a 'Google Chrome' --args --remote-debugging-port=9222

# 在 Chrome 中打开 51job.com 并登录（支持短信/密码登录）

# 终端 2：立即执行一次早间搜岗
python morning_agent.py
```

51job 登录流程：
1. Chrome 中打开 https://www.51job.com/
2. 点击「登录/注册」
3. 使用 **手机号 + 短信验证码** 或 **密码** 登录
4. 登录成功后保持 Chrome 运行，程序自动通过 CDP 连接

> ⚠️ **注意**：程序依赖 CDP 连接你的 Chrome，请保持 Chrome 在运行状态。51job Cookie 有效期较长，通常无需每天重复登录。

### 4. 启动定时任务

```bash
# 启动定时调度器（8:00 搜岗 + 20:00 监控）
python scheduler.py

# 立即执行搜岗（不等待定时）
python scheduler.py --now morning

# 立即执行晚间监控
python scheduler.py --now evening
```

按 `Ctrl+C` 停止调度器。

## 命令参考

```bash
# 立即执行早间搜岗
python morning_agent.py

# 指定关键词执行
python morning_agent.py --keywords "Python 后端,Java 开发"

# 指定最低匹配分数
python morning_agent.py --min-score 6

# 空跑模式（不推送）
python morning_agent.py --dry-run

# 立即执行晚间监控
python evening_agent.py

# 定时调度
python scheduler.py

# 立即执行全部流程
python scheduler.py --now all
```

## 项目结构

```
boss-job-agent/
├── config.py              # 全局配置（关键词、Cookie、推送、城市、薪资等）
├── browser_tool.py        # Playwright 浏览器自动化工具（LangChain Tool）
├── ai_matcher.py          # LLM 匹配打分工具（LangChain Tool）
├── db_manager.py          # SQLite 数据库、去重、存储
├── notifier.py            # 微信推送工具（Server酱 + LangChain Tool）
├── langgraph_workflow.py  # LangGraph 状态图、工作流节点定义
├── morning_agent.py       # 早间搜岗主流程
├── evening_agent.py       # 晚间投递反馈监控主流程
├── scheduler.py           # 定时任务调度器（8:00 / 20:00）
├── requirements.txt       # Python 依赖清单
├── README.md              # 使用说明（本文件）
└── data/                  # 运行时数据目录（自动创建）
    ├── job51_jobs.db      # SQLite 数据库
    ├── agent.log          # 运行日志
```

## LangGraph 工作流状态图

### 早间搜岗工作流
```
START → login → search → collect_details → ai_match
         → deduplicate → generate_report → push → END
```
异常处理：
```
任意节点出错 → handle_error（重新登录） → 重试当前节点
```

### 晚间监控工作流
```
START → login → fetch_delivery + fetch_interviews
         → stats → generate_report → push → END
```

### LangChain Tools
```
工具1: Job51Browser  — 登录、搜岗、爬详情、查投递反馈/面试邀请
工具2: AIMatcher       — 简历 + JD 匹配打分
工具3: DataDeduplicator — 去重检查
工具4: WechatNotifier  — 微信推送
```

## 防反爬规则

本工具内置以下防护措施：

- ✅ **CDP 模式** — 连接真实 Chrome，非 webdriver
- ✅ **随机等待**（2~4 秒）— 模拟真人浏览节奏
- ✅ **随机鼠标移动** — 模拟真人行为
- ✅ **真实 Chrome Profile** — 使用你自己的 Cookie/登录态
- ✅ **每日请求限流**（≤100 次）— 防止触发风控

> ⚠️ **重要提醒**：请合理使用本工具。51job 对爬虫有频率检测。建议：
> - 不要频繁手动触发（每天 1-2 次为宜）
> - 不要设置过高的翻页数（默认 5 页）
> - 保持有头模式（CDP 模式下自然满足）

