"""OpenAI 兼容的大模型调用。润色 / 扩写 / 续写 三种模式的提示词。"""
from openai import OpenAI
import config

# 按配置缓存客户端：(base_url, api_key) -> OpenAI
_clients = {}


def _get_client(base_url, api_key):
    k = (base_url, api_key)
    c = _clients.get(k)
    if c is None:
        c = OpenAI(base_url=base_url, api_key=api_key)
        _clients[k] = c
    return c


# 每种模式的系统指令
PROMPTS = {
    "润色": (
        "你是中文写作助手。把下面的口述文字润色整理成通顺的书面语："
        "修正口语化表达、补全标点、合理分段，但忠于原意，不要增加新内容、不要扩写。"
        "只输出整理后的文字，不要解释。"
    ),
    "扩写": (
        "你是中文写作助手。根据下面的口述大意，扩写成一段生动、具体的正文，"
        "保持作者的风格与语气。只输出正文，不要解释、不要标题。"
    ),
    "续写": (
        "你是中文写作助手。请阅读前文，然后根据口述的方向继续往下写，"
        "保持风格、人称和语气一致，自然衔接。只输出续写部分，不要重复前文，不要解释。"
    ),
}


def process(mode, text, context="", notes="", *, base_url=None, api_key=None, model=None):
    """按模式调用 LLM，返回生成文本。notes 为本章备注/设定，喂给 AI 保持一致。
    base_url/api_key/model 优先用调用方传入的（来自用户设置），缺省回落到 .env。"""
    base_url = base_url or config.LLM_BASE_URL
    api_key = api_key or config.LLM_API_KEY
    model = model or config.LLM_MODEL
    messages = []
    if notes:
        messages.append({
            "role": "system",
            "content": "这是作者给本章的备注/设定，写作时请遵循：\n" + notes,
        })
    if context:
        messages.append({
            "role": "system",
            "content": "这是当前文章已有的前文，请保持风格、人称和语气一致：\n" + context,
        })
    messages.append({"role": "system", "content": PROMPTS[mode]})
    messages.append({"role": "user", "content": text})

    resp = _get_client(base_url, api_key).chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.7,
    )
    return (resp.choices[0].message.content or "").strip()
