"""配置：从 .env 读取。所有项都可在 .env 里覆盖。"""
import os
from dotenv import load_dotenv

load_dotenv()

# 单用户登录密码
APP_PASSWORD = os.getenv("APP_PASSWORD", "changeme")

# 登录态签名密钥（改成随机串更安全）
SESSION_SECRET = os.getenv("SESSION_SECRET", "please-change-this-secret")

# OpenAI 兼容的大模型配置——你自己填 base_url / key / model
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

# SQLite 路径（Docker 里挂到数据卷）
DB_PATH = os.getenv("DB_PATH", "writehtml.db")

# 服务端口（选了个基本不用的 9123，可自行改）
PORT = int(os.getenv("PORT", "9123"))
