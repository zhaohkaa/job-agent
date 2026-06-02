"""
config.py — 全局配置文件
============================
包含搜索关键词、Cookie 路径、企业微信 Webhook、城市/薪资筛选条件等。
使用前请根据自身情况修改下方配置项。

变更说明 (v2.0): 目标网站从 Boss 直聘改为 51job（前程无忧）。
"""

import os
from pathlib import Path

# ============================================================
# 项目根目录
# ============================================================
BASE_DIR = Path(__file__).resolve().parent

# ============================================================
# 简历配置（用于 AI 匹配打分时的参考文本）
# ============================================================
# 将你的个人简历摘要写在这里，AI 会用它与 JD 进行匹配打分
RESUME_TEXT = """
【个人简介】
- 姓名：张三
- 学历：本科 / 计算机科学与技术
- 工作经验：3 年 Python 后端开发
- 期望职位：Python 开发工程师、后端开发工程师
- 期望城市：北京、上海、深圳
- 期望薪资：15K-25K

【技能栈】
- 语言：Python、Go、JavaScript
- 框架：Django、FastAPI、Flask
- 数据库：MySQL、PostgreSQL、Redis、MongoDB
- 云服务：AWS、阿里云、Docker、K8s
- 其他：Git、CI/CD、Linux

【工作经历】
1. XX 科技有限公司（2022-至今）— Python 后端开发
   - 负责公司核心 API 服务的设计与开发，日均请求量 500W+
   - 使用 FastAPI + PostgreSQL 搭建微服务架构
   - 引入 Redis 缓存，接口响应时间降低 60%

2. YY 互联网公司（2021-2022）— Python 开发
   - 参与内部运维平台的开发，使用 Django + Vue.js
   - 编写自动化部署脚本，减少人工操作 80%
"""

# ============================================================
# 搜索配置
# ============================================================
SEARCH_CONFIG = {
    # 搜索关键词列表（可多个，每个关键词独立搜索）
    "keywords": [
        "Python 后端开发",
        "Python 工程师",
        "Go 后端开发",
        "后端开发工程师",
    ],
    # 目标城市（51job 城市名）
    "city": "北京",
    # 薪资下限（单位：K）
    "min_salary": 15,
    # 经验要求: 01=应届生, 02=1-3年, 03=3-5年, 04=5-10年, 05=10年以上
    "experience": "02",
    # 学历要求: 01=高中, 02=大专, 03=本科, 04=硕士, 05=博士
    "degree": "03",
    # 职位类型（留空=不限）
    "job_type": "",
    # 薪资单位：0=月薪, 1=日薪, 2=时薪
    "salary_type": "0",
    # 公司规模（留空=不限）
    # 51job: 01=少于50人, 02=50-150人, 03=150-500人, 04=500-1000人, 05=1000人以上
    "scale": "",
}

# ============================================================
# AI 匹配配置
# ============================================================
AI_CONFIG = {
    # 可选: "tongyi" (通义千问), "openai" (兼容 OpenAI API), "local" (本地 LLM)
    "provider": "tongyi",
    # 最低匹配分数（0-10 分），达到此分数才推送
    "min_match_score": 7,
    # 通义千问 API Key（兼容 DashScope）
    "tongyi_api_key": os.getenv("DASHSCOPE_API_KEY", "sk-your-tongyi-api-key-here"),
    # OpenAI 兼容 API（如果用 vLLM / Ollama / 其他代理）
    "openai_api_key": os.getenv("OPENAI_API_KEY", "sk-your-openai-api-key-here"),
    "openai_base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    "openai_model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
    # 本地 LLM 地址（如 Ollama）
    "local_llm_url": os.getenv("LOCAL_LLM_URL", "http://localhost:11434"),
    "local_llm_model": os.getenv("LOCAL_LLM_MODEL", "qwen2.5:7b"),
    # 通义千问模型名
    "tongyi_model": "qwen-plus",
}

# ============================================================
# 51job（前程无忧）站点配置
# ============================================================
JOB51_CONFIG = {
    # 51job 首页
    "base_url": "https://www.51job.com",
    # 搜索页域名（51job 搜索使用的子域名）
    "search_domain": "https://we.51job.com",
    # 登录页
    "login_url": "https://login.51job.com/",
    # 用户中心（用于查看投递反馈/面试邀请）
    "user_center_url": "https://i.51job.com/",
    # 投递记录页（查看 HR 反馈、面试邀请等）
    "delivery_url": "https://i.51job.com/delivery/delivery.php",
    # Cookie 保存路径（CDP 模式下不需要，保留兼容）
    "cookie_file": str(BASE_DIR / "data" / "job51_cookies.json"),
    # CDP 连接地址（连接到你手动打开的 Chrome）
    "cdp_url": os.getenv("CDP_URL", "http://localhost:9222"),
    # 是否使用有头浏览器（CDP 模式下忽略此设置）
    "headless": False,
    # 浏览器窗口大小
    "viewport_width": 1366,
    "viewport_height": 768,
    # 每页操作后等待时间范围（秒），模拟真人浏览节奏
    "min_wait": 2.0,
    "max_wait": 4.0,
    # 每日最大请求量
    "max_daily_requests": 100,
    # 只采集近期发布的新岗位（天数，0=不限）
    "only_recent_days": 3,
    # 搜索 URL 模板
    # keyword: 搜索关键词
    # city_code: 城市编码（如 010000）
    # 51job 搜索页会自动处理分页参数
    "search_url_template": (
        "https://we.51job.com/pc/search"
        "?keyword={keyword}"
        "&searchType=2"
        "&sortType=0"
        "&metro={city_code}"
    ),
    # 搜索最大翻页数（建议 2-3 页，避免耗时过长）
    "max_pages": 3,
    # 每页岗位数（51job 默认 20）
    "page_size": 20,
    # 最大采集岗位总数（超过则截断，避免单次运行耗时过长）
    "max_total_jobs": 60,
}

# ============================================================
# 城市编码对照表（51job 城市编码为 6 位数字）
# ============================================================
CITY_CODE_MAP = {
    "北京": "010000",
    "上海": "020000",
    "广州": "030200",
    "深圳": "040000",
    "杭州": "080200",
    "成都": "090200",
    "南京": "070200",
    "武汉": "180200",
    "西安": "200200",
    "厦门": "110200",
    "苏州": "070300",
    "长沙": "190200",
    "重庆": "060000",
    "天津": "050000",
    "郑州": "170200",
    "合肥": "150200",
    "济南": "120200",
    "青岛": "120300",
    "大连": "230200",
    "福州": "110100",
    "无锡": "070400",
    "佛山": "030600",
    "东莞": "030800",
    "珠海": "031000",
}


def get_city_code(city_name: str) -> str:
    """根据中文城市名获取 51job 城市编码"""
    return CITY_CODE_MAP.get(city_name, "010000")  # 默认北京


# ============================================================
# 个人微信推送配置（Server酱 ServerChan）
# ============================================================
# 使用 Server酱 将消息推送到个人微信。
# 注册地址: https://sct.ftqq.com/
# 注册后在 "发送消息" 页面获取 SENDKEY。
# 支持 Markdown 格式推送。
PUSH_CONFIG = {
    # 推送方式: "serverchan" (Server酱), "wechat_work" (企业微信机器人)
    "method": "serverchan",
    # Server酱 SENDKEY（从 https://sct.ftqq.com/ 获取）
    # 环境变量: export SCT_SENDKEY="SCTxxxxx"
    "serverchan_key": os.getenv("SCT_SENDKEY", "your-sendkey-here"),
    # Server酱 API 地址
    "serverchan_url": "https://sctapi.ftqq.com/{sendkey}.send",
    # 是否启用推送
    "enabled": True,
}

# 兼容旧的企业微信配置（如果 method 设为 wechat_work 时生效）
WECHAT_CONFIG = {
    "webhook_url": os.getenv(
        "WECHAT_WEBHOOK_URL",
        "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=your-key-here",
    ),
}

# ============================================================
# 邮件推送配置（可选，备用通道）
# ============================================================
EMAIL_CONFIG = {
    "enabled": False,
    "smtp_host": os.getenv("SMTP_HOST", "smtp.qq.com"),
    "smtp_port": int(os.getenv("SMTP_PORT", "587")),
    "smtp_user": os.getenv("SMTP_USER", ""),
    "smtp_pass": os.getenv("SMTP_PASS", ""),
    "to_email": os.getenv("NOTIFY_EMAIL", ""),
}

# ============================================================
# 定时任务配置
# ============================================================
SCHEDULE_CONFIG = {
    # 早间搜岗执行时间（24小时制）
    "morning_time": "08:00",
    # 晚间回复监控执行时间
    "evening_time": "20:00",
}

# ============================================================
# 数据库配置
# ============================================================
DB_CONFIG = {
    "path": str(BASE_DIR / "data" / "job51_jobs.db"),
}

# ============================================================
# 日志配置
# ============================================================
LOG_CONFIG = {
    "level": "INFO",
    "file": str(BASE_DIR / "data" / "agent.log"),
    # 日志保留天数
    "backup_count": 7,
}

# ============================================================
# 多简历配置 — 针对不同搜索方向使用不同简历表述
# ============================================================
# 每个 profile 是一个独立的简历摘要，AI 会用它匹配对应方向的岗位
# keyword 映射见下方 KEYWORD_RESUME_MAP
RESUME_PROFILES = {
    "python_backend": """
【个人简介】
- 学历：本科 / 计算机科学与技术
- 工作经验：3 年 Python 后端开发
- 期望职位：Python 开发工程师、后端开发工程师
- 期望薪资：15K-25K

【技能栈】
- 语言：Python、Go、SQL
- 框架：Django、FastAPI、Flask、Celery
- 数据库：MySQL、PostgreSQL、Redis、MongoDB、Elasticsearch
- 云服务：AWS(EC2/S3/RDS)、阿里云、Docker、K8s、Terraform
- 其他：Git、CI/CD(Jenkins/GitHub Actions)、Linux、Nginx、gRPC

【项目经验】
1. 微服务 API 网关（FastAPI + Redis + PostgreSQL）
   - 设计并实现日均 500W+ 请求的 API 网关
   - 引入 Redis 缓存，P95 延迟从 200ms 降至 45ms
   - 使用 Docker + K8s 部署，支持自动扩缩容

2. 内部 DevOps 平台（Django + Vue.js）
   - 开发自动化部署流水线，减少人工操作 80%
   - 集成监控告警（Prometheus + Grafana）
""",
    "go_backend": """
【个人简介】
- 学历：本科 / 计算机科学与技术
- 工作经验：3 年后端开发（含 Go 相关经验）
- 期望职位：Go 后端开发工程师、后端开发工程师
- 期望薪资：15K-25K

【技能栈】
- 语言：Go、Python、SQL
- 框架：Gin、Echo、Go-Micro、gRPC
- 数据库：MySQL、PostgreSQL、Redis、MongoDB
- 云原生：Docker、Kubernetes、Helm、Istio
- 其他：Git、CI/CD、Linux、消息队列(Kafka/RabbitMQ)

【项目经验】
1. 分布式任务调度系统（Go + Redis + Kafka）
   - 使用 Go-Micro 微服务框架，支持每秒处理 10W+ 任务
   - 引入 Kafka 做异步消息解耦，系统可用性 99.95%

2. API 网关服务（Gin + gRPC）
   - 重构 Python 版网关为 Go，性能提升 3x
""",
    "general": """
【个人简介】
- 学历：本科 / 计算机科学与技术
- 工作经验：3 年后端开发
- 期望职位：后端开发工程师、软件开发工程师
- 期望薪资：15K-25K

【技能栈】
- 语言：Python、Go、JavaScript
- 框架：Django、FastAPI、Flask、Gin
- 数据库：MySQL、PostgreSQL、Redis、MongoDB
- 云服务：AWS、阿里云、Docker、K8s
- 其他：Git、CI/CD、Linux

【工作经历】
1. 后端开发工程师（2022-至今）
   - 负责核心 API 服务的设计与开发
   - 使用 FastAPI + PostgreSQL 搭建微服务架构

2. Python 开发工程师（2021-2022）
   - 参与内部系统的开发与维护
""",
}

# 关键词 → 简历 profile 映射（未映射的关键词默认使用 general）
KEYWORD_RESUME_MAP = {
    "Python 后端开发": "python_backend",
    "Python 工程师": "python_backend",
    "Python": "python_backend",
    "Go 后端开发": "go_backend",
    "Go 开发": "go_backend",
    "Golang": "go_backend",
    "后端开发工程师": "general",
    "Java 后端": "general",
}

# ============================================================
# AI 评分权重配置
# ============================================================
# 定义各维度的评分权重，总分 = 加权求和
SCORE_WEIGHTS = {
    "tech_match": 0.35,        # 技术栈匹配度
    "experience_match": 0.20,  # 经验匹配度
    "salary_match": 0.20,      # 薪资匹配度
    "education_match": 0.10,   # 学历匹配度
    "company_quality": 0.15,   # 公司质量（规模/行业/融资阶段）
}

# ============================================================
# 智能关键词加分规则
# ============================================================
# JD 中出现以下关键词自动加分（在 LLM 评分基础上叠加）
# 格式: {"关键词": 加分值（0-2 分）}
SMART_KEYWORD_BONUS = {
    # 高价值技术栈
    "Kubernetes": 1.0,
    "K8s": 1.0,
    "Docker": 0.5,
    "微服务": 0.5,
    "gRPC": 0.5,
    "Elasticsearch": 0.5,
    "Kafka": 0.5,
    "RabbitMQ": 0.5,
    "Redis": 0.3,
    "CI/CD": 0.3,
    "Terraform": 0.5,
    # 架构能力
    "分布式": 0.5,
    "高并发": 0.5,
    "系统设计": 0.5,
    # 公司类型加分
    "自研": 0.3,
    "技术驱动": 0.5,
    # 福利加分（暗示公司不错）
    "年终奖": 0.2,
    "15薪": 0.2,
    "16薪": 0.3,
    "六险一金": 0.2,
    "补充公积金": 0.3,
    # 扣分项
    "外包": -1.0,
    "培训贷": -5.0,
    "996": -1.0,
}

# ============================================================
# 公司黑白名单
# ============================================================
COMPANY_FILTER = {
    # 黑名单公司：包含以下关键字的公司自动跳过（不区分大小写）
    "blacklist": [
        # "XX外包公司",
        # "XX培训机构",
    ],
    # 白名单公司：包含以下关键字的公司额外 +2 分
    "whitelist": [
        # "字节跳动",
        # "腾讯",
        # "阿里巴巴",
    ],
    # 跳过没有明确JD描述的岗位
    "skip_no_jd": True,
    # 跳过实习岗位
    "skip_intern": False,
}

# ============================================================
# 薪资预筛选 — 在 AI 打分前用规则过滤明显不合适的岗位
# ============================================================
SALARY_PRE_FILTER = {
    # 启用预筛选
    "enabled": True,
    # 期望最低月薪（单位：K），低于此值的岗位直接过滤
    "min_monthly_salary_k": 8,
    # 如果无法从文本中解析出数字，是否保留（True=保留）
    "keep_if_unparseable": True,
    # 跳过低薪实习（日薪 < N 元/天）
    "min_daily_salary": 150,
}

# ============================================================
# 自动投递配置
# ============================================================
AUTO_APPLY = {
    # 是否启用自动投递（需谨慎开启）
    "enabled": False,
    # 自动投递最低分数阈值（建议 ≥ 8.5）
    "min_score": 8.5,
    # 每天最多自动投递数
    "max_daily": 5,
    # 投递前是否需要确认（True=每次弹出确认，False=直接投递）
    "confirm_before_apply": True,
    # 投递间隔（秒），避免操作过快
    "apply_interval": 5.0,
}

# ============================================================
# 交互式审阅模式
# ============================================================
INTERACTIVE_MODE = {
    # 是否启用交互模式（True=跑完后在终端让你勾选，False=直接推送）
    "enabled": True,
    # 展示的候选岗位数（AI 评分排序 top N）
    "candidate_count": 20,
    # 是否自动推送未审阅的高分岗位（超时未操作时）
    "auto_push_timeout_minutes": 0,  # 0=不自动推送
}

# ============================================================
# 确保数据目录存在
# ============================================================
(BASE_DIR / "data").mkdir(parents=True, exist_ok=True)
