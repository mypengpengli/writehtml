"""OpenAI 兼容的大模型调用。润色 / 扩写 / 续写 三种模式的提示词。"""
import json
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
    "找回": (
        "你是中文写作助手。下面「当前正文」是作者现在的版本，"
        "「历史草稿」是作者之前写过的旧版本。请从历史草稿里找出写得好的、"
        "当前正文里没有或被删掉的内容，整理成可以补回当前正文的段落。"
        "只输出要补充的段落，不要重复当前正文已有的内容，不要解释、不要标题。"
    ),
    "摘要": (
        "你是中文写作助手。用1-3句话概括下面章节的剧情梗概，"
        "便于作为后续写作的上下文。只输出摘要，不要解释、不要标题。"
    ),
    "校验": (
        "你是中文写作的设定校验员。下面是作者写的正文，请与作品设定、本章备注比对，"
        "找出矛盾之处：人物性格/身份崩坏、时间线或地点前后不一致、"
        "人物状态冲突（如已断手却在用该手）、违反设定。"
        "用要点逐条列出问题并指明大致位置；没有矛盾就只回「未发现矛盾」。"
        "不要改写正文，只列问题。"
    ),
    "缩写": (
        "你是中文写作助手。把下面的段落缩写到约一半篇幅，"
        "保留关键情节、人物动作与对话要点，保持原风格和语气，"
        "只输出缩写后的正文，不要解释、不要标题。"
    ),
    "改写": (
        "你是中文写作助手。把下面的段落改写成「{style}」的风格，"
        "保持原意、人物设定与情节不变，只输出改写后的正文，"
        "不要解释、不要标题。"
    ),
}


def chat(messages, *, base_url=None, api_key=None, model=None):
    """多轮对话（头脑风暴）。messages 已含系统上下文 + 历史，直接发给模型。"""
    base_url = base_url or config.LLM_BASE_URL
    api_key = api_key or config.LLM_API_KEY
    model = model or config.LLM_MODEL
    resp = _get_client(base_url, api_key).chat.completions.create(
        model=model, messages=messages, temperature=0.7,
    )
    return (resp.choices[0].message.content or "").strip()


def agent_chat(messages, tools, *, base_url=None, api_key=None, model=None):
    """Agent 循环的单步调用：带 tools（function calling）发出去，返回完整 message。
    调用方根据 message.tool_calls 决定是分派工具还是收尾。"""
    base_url = base_url or config.LLM_BASE_URL
    api_key = api_key or config.LLM_API_KEY
    model = model or config.LLM_MODEL
    resp = _get_client(base_url, api_key).chat.completions.create(
        model=model, messages=messages, tools=tools, tool_choice="auto",
        temperature=0.6,
    )
    return resp.choices[0].message


def summarize(messages, prev="", *, base_url=None, api_key=None, model=None):
    """把多轮对话压成一段摘要（用于 agent 上下文压缩）。
    失败时抛异常，由调用方兜底（不阻断主流程）。"""
    base_url = base_url or config.LLM_BASE_URL
    api_key = api_key or config.LLM_API_KEY
    model = model or config.LLM_MODEL
    lines = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content") or ""
        if role == "tool":
            # 工具结果一般是 JSON，取 summary/error 字段更精炼
            try:
                r = json.loads(content)
                content = r.get("summary") or r.get("error") or "已执行操作"
            except Exception:
                pass
            tag = "操作"
        elif role == "user":
            tag = "用户"
        elif role == "assistant":
            tag = "助手"
            if m.get("tool_calls"):
                names = ",".join(tc.get("function", {}).get("name", "") for tc in m["tool_calls"])
                content = f"调用工具：{names}" + (f"；{content}" if content else "")
        else:
            tag = role
        if content:
            lines.append(f"{tag}：{content}")
    transcript = "\n".join(lines)
    sys = (
        "你是对话摘要器。把下面的多轮写作助手对话（用户指令、AI 回复、工具操作）"
        f"压缩成一段不超过 {config.AGENT_SUMMARY_MAX} 字的摘要，"
        "保留：已执行的关键操作、用户的核心意图与偏好、尚未完成的事项。"
        "不要编造、不要解释，直接输出摘要正文。"
    )
    user_content = (f"已有摘要：\n{prev}\n\n" if prev else "") + f"对话内容：\n{transcript}"
    resp = _get_client(base_url, api_key).chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": sys}, {"role": "user", "content": user_content}],
        temperature=0.3,
    )
    return (resp.choices[0].message.content or "").strip()


def process(mode, text, context="", notes="", *, base_url=None, api_key=None, model=None, bible=None, style=None):
    """按模式调用 LLM，返回生成文本。
    notes 为本章备注；bible 为作品级设定（人物/世界观/大纲），全文记忆。
    base_url/api_key/model 优先用调用方传入的（来自用户设置），缺省回落到 .env。"""
    base_url = base_url or config.LLM_BASE_URL
    api_key = api_key or config.LLM_API_KEY
    model = model or config.LLM_MODEL
    messages = []
    if bible:
        messages.append({
            "role": "system",
            "content": "这是作品设定（人物/世界观/大纲），全文请遵循保持一致：\n" + bible,
        })
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
    # 改写带风格参数；其余模式直接查表
    prompt = PROMPTS["改写"].format(style=style or "更生动") if mode == "改写" else PROMPTS[mode]
    messages.append({"role": "system", "content": prompt})
    messages.append({"role": "user", "content": text})

    resp = _get_client(base_url, api_key).chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.7,
    )
    return (resp.choices[0].message.content or "").strip()
