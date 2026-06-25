"""OpenAI 兼容的大模型调用。润色 / 扩写 / 续写 三种模式的提示词。"""
from openai import OpenAI
import config

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY)
    return _client


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


def process(mode, text, context=""):
    """按模式调用 LLM，返回生成文本。"""
    messages = []
    if context:
        messages.append({
            "role": "system",
            "content": "这是当前文章已有的前文，请保持风格、人称和语气一致：\n" + context,
        })
    messages.append({"role": "system", "content": PROMPTS[mode]})
    messages.append({"role": "user", "content": text})

    resp = _get_client().chat.completions.create(
        model=config.LLM_MODEL,
        messages=messages,
        temperature=0.7,
    )
    return (resp.choices[0].message.content or "").strip()
