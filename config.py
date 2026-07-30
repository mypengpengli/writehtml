"""配置：从 .env 读取。所有项都可在 .env 里覆盖。"""
import os
import re
from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

# OpenAI 兼容的大模型配置——你自己填 base_url / key / model
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
# 转写服务可单独配置；留空时自动沿用文字模型的 base_url / key。
ASR_BASE_URL = os.getenv("ASR_BASE_URL", "")
ASR_API_KEY = os.getenv("ASR_API_KEY", "")
ASR_MODEL = os.getenv("ASR_MODEL", "whisper-1")

# Tavily 联网搜索只由服务端持有密钥。支持逗号、分号或换行分隔的 Key 池；
# TAVILY_API_KEY 保留为单 Key 兼容项。多个同账号 Key 仍共享账号套餐总额度。
_tavily_keys_raw = os.getenv("TAVILY_API_KEYS", "")
_tavily_single_key = os.getenv("TAVILY_API_KEY", "")
TAVILY_API_KEYS = tuple(dict.fromkeys(
    value.strip().strip("\"'")
    for value in re.split(r"[,;\r\n]+", ",".join(filter(None, [_tavily_keys_raw, _tavily_single_key])))
    if value.strip().strip("\"'")
))
TAVILY_PROJECT_ID = os.getenv("TAVILY_PROJECT_ID", "writehtml")
TAVILY_SEARCH_TIMEOUT_SECONDS = float(os.getenv("TAVILY_SEARCH_TIMEOUT_SECONDS", "20"))
TAVILY_SEARCH_MAX_RESULTS = int(os.getenv("TAVILY_SEARCH_MAX_RESULTS", "5"))
TAVILY_SEARCH_DEPTH = os.getenv("TAVILY_SEARCH_DEPTH", "basic").lower()
TAVILY_SEARCH_SAFE_SEARCH = os.getenv("TAVILY_SEARCH_SAFE_SEARCH", "false").lower() not in {
    "0", "false", "no",
}

# SQLite 路径（Docker 里挂到数据卷）
DB_PATH = os.getenv("DB_PATH", "writehtml.db")

# 灵感原始素材与数据库放在同一持久卷中。Docker 默认落到 /data/inspirations，
# 本地开发则落到数据库旁边，升级容器不会丢图片、录音或音乐。
INSPIRATION_STORAGE_DIR = os.getenv(
    "INSPIRATION_STORAGE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "inspirations"),
)
INSPIRATION_IMAGE_MAX_BYTES = int(os.getenv("INSPIRATION_IMAGE_MAX_BYTES", str(20 * 1024 * 1024)))
INSPIRATION_AUDIO_MAX_BYTES = int(os.getenv("INSPIRATION_AUDIO_MAX_BYTES", str(100 * 1024 * 1024)))
INSPIRATION_VIDEO_MAX_BYTES = int(os.getenv("INSPIRATION_VIDEO_MAX_BYTES", str(300 * 1024 * 1024)))
INSPIRATION_USER_STORAGE_LIMIT_BYTES = int(os.getenv(
    "INSPIRATION_USER_STORAGE_LIMIT_BYTES", str(5 * 1024 * 1024 * 1024)
))
INSPIRATION_WORKER_ENABLED = os.getenv("INSPIRATION_WORKER_ENABLED", "true").lower() not in {
    "0", "false", "no",
}
INSPIRATION_WORKER_POLL_SECONDS = float(os.getenv("INSPIRATION_WORKER_POLL_SECONDS", "1.5"))
INSPIRATION_TEMP_RETENTION_HOURS = int(os.getenv("INSPIRATION_TEMP_RETENTION_HOURS", "24"))

# 本机 Skill / launcher 运行时（可选）。
# 仅服务管理员配置；网页上传的 SKILL.md 不会获得执行服务器命令的权限。
# AGENT_SKILL_DIR 下的 meta-memory/SKILL.md 会在启用 launcher 生命周期时每回合重新读取。
AGENT_SKILL_DIR = os.getenv("AGENT_SKILL_DIR", "")
# 安装器生成的 launcher 路径或命令，例如 /opt/meta-memory/bin/launcher。
AGENT_SKILL_LAUNCHER = os.getenv("AGENT_SKILL_LAUNCHER", "")
# launcher 的工作目录；为空时使用当前项目目录。执行时同时传入 --cwd。
AGENT_SKILL_CWD = os.getenv("AGENT_SKILL_CWD", "")
# 请求、回答和 manifest 的持久化目录。回复经 launcher 确认前绝不删除。
AGENT_SKILL_RUNTIME_DIR = os.getenv(
    "AGENT_SKILL_RUNTIME_DIR", os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "skill-turns")
)
# 每种 Agent 固定、唯一的身份。变更此值会形成一个新的 launcher 身份。
AGENT_SKILL_AGENT_ID = os.getenv("AGENT_SKILL_AGENT_ID", "writehtml-writing-agent-v1")
AGENT_SKILL_COMMAND_TIMEOUT = float(os.getenv("AGENT_SKILL_COMMAND_TIMEOUT", "30"))
AGENT_SKILL_TOUCH_SECONDS = float(os.getenv("AGENT_SKILL_TOUCH_SECONDS", "20"))

# Pi Coding Agent is the production writing-agent runtime. Set PI_AGENT_ENABLED=false
# only for a controlled rollback while diagnosing a deployment.
PI_AGENT_ENABLED = os.getenv("PI_AGENT_ENABLED", "true").lower() not in {"0", "false", "no"}
PI_AGENT_NODE = os.getenv("PI_AGENT_NODE", "node")
_pi_bridge = os.getenv("PI_AGENT_BRIDGE", os.path.join("pi_runtime", "bridge.mjs"))
PI_AGENT_BRIDGE = _pi_bridge if os.path.isabs(_pi_bridge) else os.path.join(ROOT_DIR, _pi_bridge)
PI_AGENT_TIMEOUT_SECONDS = float(os.getenv("PI_AGENT_TIMEOUT_SECONDS", "240"))
# 0 means the token budget below, rather than an arbitrary message count, controls history.
PI_AGENT_MAX_HISTORY_MESSAGES = int(os.getenv("PI_AGENT_MAX_HISTORY_MESSAGES", "0"))
# Pi Coding Agent 的本机 Skill 目录。留空时沿用 AGENT_SKILL_DIR；它会按 Pi 的
# Agent Skills 规范扫描目录中的 SKILL.md，并由 Pi 的 read/bash 工具按需使用。
PI_AGENT_SKILL_DIR = os.getenv("PI_AGENT_SKILL_DIR", AGENT_SKILL_DIR)
# Pi 的 read/grep/find/ls/bash 工具的默认工作目录。相对路径相对于项目根目录解析。
_pi_workspace = os.getenv("PI_AGENT_WORKSPACE_DIR", AGENT_SKILL_CWD or ROOT_DIR)
PI_AGENT_WORKSPACE_DIR = _pi_workspace if os.path.isabs(_pi_workspace) else os.path.join(ROOT_DIR, _pi_workspace)
# 服务端口（选了个基本不用的 9123，可自行改）
PORT = int(os.getenv("PORT", "9123"))

# 注册控制（公网部署用）：
#   SIGNUP_CODE 非空 → 注册需提供此码（你把码发给谁，谁才能注册）
#   ALLOW_SIGNUP=true → 任何人可注册（不推荐公网开）
#   两者皆空 → 完全禁止注册，只能用已存在的账号登录
ALLOW_SIGNUP = os.getenv("ALLOW_SIGNUP", "false").lower() == "true"
SIGNUP_CODE = os.getenv("SIGNUP_CODE", "")

# 后台管理员账户（首次启动时自动引导创建一个 is_admin=1 的用户）：
#   ADMIN_USER 非空才创建；ADMIN_PASSWORD 为空时随机生成 12 位并打印到服务端日志，
#   你在 docker logs 里读一次即可，正式部署请用 env 固定一个强密码。
ADMIN_USER = os.getenv("WRITEHTML_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("WRITEHTML_ADMIN_PASSWORD", "")

# Agent context is budgeted in tokens. Compression starts when the estimated complete
# request (system/story context + tools + chat + reserved answer) reaches 90% of the
# configured model window. The default matches the project's current 200K-class models.
AGENT_CONTEXT_WINDOW_TOKENS = int(os.getenv("AGENT_CONTEXT_WINDOW_TOKENS", "200000"))
AGENT_CONTEXT_TRIGGER_RATIO = float(os.getenv("AGENT_CONTEXT_TRIGGER_RATIO", "0.90"))
AGENT_MAX_OUTPUT_TOKENS = int(os.getenv("AGENT_MAX_OUTPUT_TOKENS", "8192"))
AGENT_PRESERVE_RECENT = int(os.getenv("AGENT_PRESERVE_RECENT", "24"))
AGENT_SUMMARY_MAX = int(os.getenv("AGENT_SUMMARY_MAX", "2000"))
# Deprecated emergency override. A positive value keeps the old character threshold,
# mainly for controlled tests or unusually small compatibility models.
AGENT_COMPACT_CHARS = int(os.getenv("AGENT_COMPACT_CHARS", "0"))
AGENT_DOCUMENT_MAX_BYTES = int(os.getenv("AGENT_DOCUMENT_MAX_BYTES", str(25 * 1024 * 1024)))
AGENT_DOCUMENT_MAX_CHARS = int(os.getenv("AGENT_DOCUMENT_MAX_CHARS", "120000"))
AGENT_DOCUMENT_TOTAL_MAX_CHARS = int(os.getenv("AGENT_DOCUMENT_TOTAL_MAX_CHARS", "160000"))
AGENT_DOCUMENT_MAX_FILES = int(os.getenv("AGENT_DOCUMENT_MAX_FILES", "8"))
