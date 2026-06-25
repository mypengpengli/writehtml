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
