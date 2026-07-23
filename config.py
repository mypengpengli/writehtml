"""配置：从 .env 读取。所有项都可在 .env 里覆盖。"""
import os
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

# SQLite 路径（Docker 里挂到数据卷）
DB_PATH = os.getenv("DB_PATH", "writehtml.db")

# 本机 Skill / launcher 运行时（可选，默认关闭）。
# 仅服务管理员配置；用户上传的 SKILL.md 不会获得执行服务器命令的权限。
# AGENT_SKILL_DIR 下必须存在 meta-memory/SKILL.md；每一个 Agent 回合都会重新读取它。
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

# Pi Agent Core is the production writing-agent runtime. Set PI_AGENT_ENABLED=false
# only for a controlled rollback while diagnosing a deployment.
PI_AGENT_ENABLED = os.getenv("PI_AGENT_ENABLED", "true").lower() not in {"0", "false", "no"}
PI_AGENT_NODE = os.getenv("PI_AGENT_NODE", "node")
_pi_bridge = os.getenv("PI_AGENT_BRIDGE", os.path.join("pi_runtime", "bridge.mjs"))
PI_AGENT_BRIDGE = _pi_bridge if os.path.isabs(_pi_bridge) else os.path.join(ROOT_DIR, _pi_bridge)
PI_AGENT_TIMEOUT_SECONDS = float(os.getenv("PI_AGENT_TIMEOUT_SECONDS", "240"))
PI_AGENT_MAX_HISTORY_MESSAGES = int(os.getenv("PI_AGENT_MAX_HISTORY_MESSAGES", "80"))

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

# agent 对话上下文压缩：对话累计字符超过 AGENT_COMPACT_CHARS 时触发，
# 保留最近 AGENT_PRESERVE_RECENT 条（切在 user 消息边界，不切断工具对），
# 更早的轮次交给 LLM 压成不超过 AGENT_SUMMARY_MAX 字的摘要替代。
AGENT_COMPACT_CHARS = int(os.getenv("AGENT_COMPACT_CHARS", "12000"))
AGENT_PRESERVE_RECENT = int(os.getenv("AGENT_PRESERVE_RECENT", "8"))
AGENT_SUMMARY_MAX = int(os.getenv("AGENT_SUMMARY_MAX", "300"))
