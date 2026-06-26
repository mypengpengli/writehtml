"""配置：从 .env 读取。所有项都可在 .env 里覆盖。"""
import os
from dotenv import load_dotenv

load_dotenv()

# OpenAI 兼容的大模型配置——你自己填 base_url / key / model
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

# SQLite 路径（Docker 里挂到数据卷）
DB_PATH = os.getenv("DB_PATH", "writehtml.db")

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
