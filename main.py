"""FastAPI 后端：多用户鉴权 + 作品/章节 CRUD + AI 处理 + 拆分/排序/修订/导出。"""
import json
import secrets
import difflib
from urllib.parse import quote

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

import config, db, llm

app = FastAPI(title="写作")
db.init_db()

# 内存里的登录态：token -> user_id（单进程、重启即失效，需重登）
_sessions = {}


def _auth(request: Request):
    tok = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    uid = _sessions.get(tok)
    if not uid:
        raise HTTPException(401, "未登录")
    return uid


def _admin_auth(request: Request):
    """鉴权 + 管理员校验。非管理员 403。"""
    uid = _auth(request)
    if not db.is_admin(uid):
        raise HTTPException(403, "需要管理员权限")
    return uid


def _qparam_int(request: Request, name):
    """从 query string 取一个可选整数，缺省/空返回 None。"""
    v = request.query_params.get(name)
    return int(v) if v else None


# ---------- 鉴权 / 注册 ----------

@app.get("/api/signup-status")
async def signup_status():
    enabled = config.ALLOW_SIGNUP or bool(config.SIGNUP_CODE)
    return {"enabled": enabled, "needs_code": bool(config.SIGNUP_CODE)}


@app.post("/api/register")
async def register(request: Request):
    body = await request.json()
    if not (config.ALLOW_SIGNUP or config.SIGNUP_CODE):
        raise HTTPException(403, "未开放注册")
    if config.SIGNUP_CODE and body.get("code") != config.SIGNUP_CODE:
        raise HTTPException(403, "注册码错误")
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if len(username) < 2 or len(password) < 4:
        raise HTTPException(400, "用户名至少2位，密码至少4位")
    u = db.create_user(username, password)
    if u is None:
        raise HTTPException(409, "用户名已存在")
    tok = secrets.token_hex(24)
    _sessions[tok] = u["id"]
    return {"token": tok, "username": username}


@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    u = db.verify_user(body.get("username", ""), body.get("password", ""))
    if not u:
        raise HTTPException(403, "用户名或密码错误")
    tok = secrets.token_hex(24)
    _sessions[tok] = u["id"]
    return {"token": tok, "username": u["username"]}


@app.post("/api/logout")
async def logout(request: Request):
    _auth(request)
    tok = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    _sessions.pop(tok, None)
    return {"ok": True}


@app.get("/api/me")
async def me(request: Request):
    uid = _auth(request)
    return {"username": db.get_username(uid), "is_admin": db.is_admin(uid)}


# ---------- 每用户大模型设置 ----------

@app.get("/api/settings")
async def get_settings(request: Request):
    uid = _auth(request)
    s = db.get_settings(uid) or {}
    key = s.get("llm_api_key") or ""
    # 明文 key 不回传，只给掩码提示是否已填
    masked = ("****" + key[-4:]) if key else ""
    return {
        "base_url": s.get("llm_base_url") or config.LLM_BASE_URL,
        "api_key_masked": masked,
        "has_key": bool(key),
        "model": s.get("llm_model") or config.LLM_MODEL,
    }


@app.post("/api/settings")
async def save_settings(request: Request):
    uid = _auth(request)
    body = await request.json()
    db.save_settings(
        uid,
        (body.get("base_url") or "").strip(),
        (body.get("api_key") or "").strip(),
        (body.get("model") or "").strip(),
    )
    return {"ok": True}


# ---------- 作品 ----------

@app.get("/api/works")
async def get_works(request: Request):
    return db.list_works(_auth(request))


@app.post("/api/works")
async def new_work(request: Request):
    body = await request.json()
    return db.create_work(_auth(request), body.get("title", "未命名"))


@app.delete("/api/works/{wid}")
async def del_work(wid: int, request: Request):
    if not db.delete_work(wid, _auth(request)):
        raise HTTPException(404, "作品不存在")
    return {"ok": True}


@app.get("/api/works/{wid}/notes")
async def get_work_notes_api(wid: int, request: Request):
    n = db.get_work_notes(wid, _auth(request))
    if n is None:
        raise HTTPException(404, "作品不存在")
    return {"notes": n or ""}


@app.put("/api/works/{wid}/notes")
async def save_work_notes(wid: int, request: Request):
    body = await request.json()
    if not db.update_work_notes(wid, _auth(request), body.get("notes", "")):
        raise HTTPException(404, "作品不存在")
    return {"ok": True}


# ---------- 实体卡片（作品级 wiki）----------

@app.get("/api/works/{wid}/entities")
async def get_entities(wid: int, request: Request):
    r = db.list_entities(wid, _auth(request))
    if r is None:
        raise HTTPException(404, "作品不存在")
    return r


@app.post("/api/works/{wid}/entities")
async def new_entity(wid: int, request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "实体名不能为空")
    r = db.create_entity(wid, _auth(request), name, body.get("kind", "人物"),
                         body.get("summary", ""), body.get("detail", ""))
    if r is None:
        raise HTTPException(404, "作品不存在")
    return r


@app.put("/api/entities/{eid}")
async def save_entity(eid: int, request: Request):
    body = await request.json()
    if not db.update_entity(eid, _auth(request), body.get("name"), body.get("kind"),
                            body.get("summary"), body.get("detail")):
        raise HTTPException(404, "实体不存在")
    return {"ok": True}


@app.delete("/api/entities/{eid}")
async def del_entity(eid: int, request: Request):
    if not db.delete_entity(eid, _auth(request)):
        raise HTTPException(404, "实体不存在")
    return {"ok": True}


# ---------- 章节 ----------

@app.get("/api/works/{wid}/chapters")
async def get_chapters(wid: int, request: Request):
    r = db.list_chapters(wid, _auth(request))
    if r is None:
        raise HTTPException(404, "作品不存在")
    return r


@app.post("/api/works/{wid}/chapters")
async def new_chapter(wid: int, request: Request):
    body = await request.json()
    r = db.create_chapter(wid, _auth(request), body.get("title", "新章节"))
    if r is None:
        raise HTTPException(404, "作品不存在")
    return r


@app.post("/api/works/{wid}/reorder")
async def reorder(wid: int, request: Request):
    body = await request.json()
    if not db.reorder_chapters(wid, _auth(request), body.get("ids", [])):
        raise HTTPException(404, "作品不存在")
    return {"ok": True}


@app.get("/api/chapters/{cid}")
async def get_chapter(cid: int, request: Request):
    chap = db.get_chapter(cid, _auth(request))
    if not chap:
        raise HTTPException(404, "章节不存在")
    return chap


@app.put("/api/chapters/{cid}")
async def save_chapter(cid: int, request: Request):
    body = await request.json()
    if not db.update_chapter(cid, _auth(request), body.get("title"), body.get("content"), body.get("notes")):
        raise HTTPException(404, "章节不存在")
    return {"ok": True}


@app.delete("/api/chapters/{cid}")
async def del_chapter(cid: int, request: Request):
    if not db.delete_chapter(cid, _auth(request)):
        raise HTTPException(404, "章节不存在")
    return {"ok": True}


@app.post("/api/chapters/{cid}/restore")
async def restore_chapter(cid: int, request: Request):
    if not db.restore_chapter(cid, _auth(request)):
        raise HTTPException(404, "章节不存在")
    return {"ok": True}


@app.post("/api/chapters/{cid}/purge")
async def purge_chapter(cid: int, request: Request):
    if not db.purge_chapter(cid, _auth(request)):
        raise HTTPException(404, "章节不存在")
    return {"ok": True}


@app.post("/api/chapters/{cid}/split")
async def split(cid: int, request: Request):
    body = await request.json()
    r = db.split_chapter(cid, _auth(request), int(body.get("at", 0)), body.get("title", "新章节"))
    if r is None:
        raise HTTPException(404, "章节不存在")
    return r


@app.post("/api/chapters/{cid}/undo")
async def undo(cid: int, request: Request):
    r = db.undo_last_segment(cid, _auth(request))
    if r is None:
        raise HTTPException(404, "章节不存在")
    return r


@app.get("/api/works/{wid}/trash")
async def get_trash(wid: int, request: Request):
    r = db.list_trashed(wid, _auth(request))
    if r is None:
        raise HTTPException(404, "作品不存在")
    return r


# ---------- 修订版本 ----------

@app.get("/api/chapters/{cid}/revisions")
async def get_revisions(cid: int, request: Request):
    r = db.list_revisions(cid, _auth(request))
    if r is None:
        raise HTTPException(404, "章节不存在")
    return r


@app.post("/api/chapters/{cid}/revisions")
async def save_revision(cid: int, request: Request):
    r = db.add_revision(cid, _auth(request))
    if r is None:
        raise HTTPException(404, "章节不存在")
    return r


@app.post("/api/chapters/{cid}/revisions/{rid}/restore")
async def restore(cid: int, rid: int, request: Request):
    r = db.restore_revision(cid, _auth(request), rid)
    if r is None:
        raise HTTPException(404, "不存在")
    return r


@app.get("/api/chapters/{cid}/revisions/{rid}/diff")
async def diff_revision(cid: int, rid: int, request: Request):
    """对比某历史版本与当前正文，按行给出增删块。鉴权复用 get_revision/get_chapter。"""
    uid = _auth(request)
    rev = db.get_revision(cid, uid, rid)
    if not rev:
        raise HTTPException(404, "历史版本不存在")
    cur = db.get_chapter(cid, uid)
    if not cur:
        raise HTTPException(404, "章节不存在")
    a = (rev["content"] or "").splitlines()   # 旧（历史版本）
    b = (cur["content"] or "").splitlines()   # 新（当前正文）
    ops = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        ops.append({"op": tag, "old": "\n".join(a[i1:i2]), "new": "\n".join(b[j1:j2])})
    return {"ops": ops, "rev_title": rev["title"], "cur_title": cur["title"], "rev_at": rev["created_at"]}


# ---------- 导出 ----------

@app.get("/api/chapters/{cid}/export")
async def export(cid: int, request: Request, format: str = "txt"):
    chap = db.get_chapter(cid, _auth(request))
    if not chap:
        raise HTTPException(404, "章节不存在")
    title = chap["title"] or "chapter"
    content = chap["content"] or ""
    # 文件名可能含中文，HTTP 头只能 latin-1，按 RFC 5987 百分号编码并给 ASCII 兜底名
    q = quote(title)
    def _disp(ext):
        return f"attachment; filename=chapter.{ext}; filename*=UTF-8''{q}.{ext}"
    if format == "docx":
        from io import BytesIO
        from docx import Document
        doc = Document()
        doc.add_heading(title, 0)
        for para in content.split("\n"):
            if para.strip():
                doc.add_paragraph(para)
        buf = BytesIO()
        doc.save(buf)
        return Response(
            buf.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": _disp("docx")},
        )
    return Response(
        content.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": _disp("txt")},
    )


@app.get("/api/works/{wid}/export")
async def export_work(wid: int, request: Request, format: str = "txt"):
    uid = _auth(request)
    work = db.get_work(wid, uid)
    if not work:
        raise HTTPException(404, "作品不存在")
    chaps = db.list_chapters_full(wid, uid)
    title = work["title"] or "writehtml"
    q = quote(title)
    def _disp(ext):
        return f"attachment; filename=work.{ext}; filename*=UTF-8''{q}.{ext}"
    if format == "docx":
        from io import BytesIO
        from docx import Document
        doc = Document()
        doc.add_heading(title, 0)
        for c in chaps:
            doc.add_heading(c["title"] or "(无标题)", level=1)
            for para in (c["content"] or "").split("\n"):
                if para.strip():
                    doc.add_paragraph(para)
        buf = BytesIO()
        doc.save(buf)
        return Response(
            buf.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": _disp("docx")},
        )
    # txt：每章标题 + 正文，空行分隔
    content = "\n\n".join(
        f"{'　' * 2}{c['title'] or '(无标题)'}\n\n{c['content'] or ''}" for c in chaps
    )
    return Response(
        content.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": _disp("txt")},
    )


# ---------- AI 处理 ----------

@app.post("/api/process")
async def do_process(request: Request):
    uid = _auth(request)
    body = await request.json()
    mode = body.get("mode")
    text = (body.get("text") or "").strip()
    context = body.get("context") or ""
    cid = body.get("chapter_id")
    style = body.get("style")  # 改写风格预设（更生动/更精炼/文艺风/…），仅改写模式用

    notes = ""
    chap = None
    if cid:
        chap = db.get_chapter_meta(cid, uid)  # 轻量取元数据，不拉段落历史（转写热路径）
        if not chap:
            raise HTTPException(404, "章节不存在")
        notes = chap.get("notes") or ""

    seg_raw = text  # 段落历史里记录的"原始输入"
    # 找回：从指定历史版本里恢复内容，主输入=旧草稿，上下文=当前正文全文
    if mode == "找回":
        rid = body.get("revision_id")
        rev = db.get_revision(cid, uid, rid) if (cid and rid) else None
        if not rev:
            raise HTTPException(404, "历史版本不存在")
        rev_content = (rev["content"] or "").strip()
        if not rev_content:
            raise HTTPException(400, "该历史版本为空")
        context = (chap["content"] if chap else "") or ""
        text = rev_content
        seg_raw = f"（找回自历史版本 #{rid}）"
    elif mode in ("校验", "摘要"):
        # 作用对象是整章正文，不需要用户额外输入；结果不写进正文
        if not cid:
            raise HTTPException(400, "请先选择章节")
        text = (chap["content"] if chap else "") or ""
        if not text:
            raise HTTPException(400, "本章为空")
        seg_raw = f"（{mode}）"

    if not text:
        raise HTTPException(400, "内容为空")

    if mode == "转写":
        result = text
    elif mode in ("润色", "扩写", "续写", "找回", "校验", "摘要", "缩写", "改写"):
        s = db.get_settings(uid) or {}
        base_url = s.get("llm_base_url") or config.LLM_BASE_URL
        api_key = s.get("llm_api_key") or config.LLM_API_KEY
        model = s.get("llm_model") or config.LLM_MODEL
        if not api_key:
            raise HTTPException(500, "未配置 API Key，请在「设置」里填 base_url / key / 模型")
        # bible 只在真的要调 LLM 时才拼（作品设定 + 实体 digest）；转写/无key 不浪费这两次查询
        bible = ""
        if cid:
            bible = db.get_work_notes(chap["work_id"], uid) or ""
            digest = db.get_entity_digest(chap["work_id"], uid)
            if digest:
                bible = (bible + "\n\n" + digest) if bible else digest
        result = llm.process(mode, text, context, notes, bible=bible,
                             base_url=base_url, api_key=api_key, model=model, style=style)
    else:
        raise HTTPException(400, "未知模式")

    # 只有这些模式把结果写进正文；校验/摘要不污染正文
    seg = None
    if cid and mode in ("转写", "润色", "扩写", "续写", "找回"):
        seg = db.add_segment(cid, uid, seg_raw, result, mode)
    return {"result": result, "raw": seg_raw, "mode": mode, "content": seg["content"] if seg else None}


@app.post("/api/chat")
async def chat(request: Request):
    """头脑风暴：多轮对话，不碰正文。带作品设定+本章备注+正文末尾作上下文。"""
    uid = _auth(request)
    body = await request.json()
    msgs = body.get("messages")
    if not isinstance(msgs, list) or not msgs:
        raise HTTPException(400, "没有对话内容")
    s = db.get_settings(uid) or {}
    base_url = s.get("llm_base_url") or config.LLM_BASE_URL
    api_key = s.get("llm_api_key") or config.LLM_API_KEY
    model = s.get("llm_model") or config.LLM_MODEL
    if not api_key:
        raise HTTPException(500, "未配置 API Key，请在「设置」里填 base_url / key / 模型")

    sys_ctx = [{"role": "system", "content":
        "你是作者的联合创作者，帮其推敲剧情、查逻辑漏洞、探讨走向。"
        "回答简洁有建设性，给选项和建议，不要替作者下最终决定。"}]
    cid = body.get("chapter_id")
    if cid:
        chap = db.get_chapter_meta(cid, uid)  # 轻量取元数据，不拉段落历史
        if chap:
            bible = db.get_work_notes(chap["work_id"], uid) or ""
            digest = db.get_entity_digest(chap["work_id"], uid)
            if digest:
                bible = (bible + "\n\n" + digest) if bible else digest
            if bible:
                sys_ctx.append({"role": "system", "content": "作品设定（人物/世界观/大纲），探讨时请遵循：\n" + bible})
            notes = chap.get("notes") or ""
            if notes:
                sys_ctx.append({"role": "system", "content": "本章备注：\n" + notes})
            tail = (chap["content"] or "")[-2000:]
            if tail:
                sys_ctx.append({"role": "system", "content":
                    "当前正文末尾（供理解上下文，不要重复或改写）：\n" + tail})
    reply = llm.chat(sys_ctx + msgs, base_url=base_url, api_key=api_key, model=model)
    return {"reply": reply}


# ---------- AI agent（对话即操作） ----------

# 工具的 JSON schema（喂给模型 function calling）
AGENT_TOOLS = [
    {"type": "function", "function": {
        "name": "read_chapter",
        "description": "读取当前章节的标题、备注和正文全文。要修改某段文字前先调它取准确原文。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "list_chapters",
        "description": "列出当前作品的所有章节（id、标题、字数）。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "list_revisions",
        "description": "列出当前章节的历史版本（id、标题、字数），供回退选择。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "replace_text",
        "description": "在当前章节正文里找到 old_text 的第一处出现，替换为 new_text。old_text 必须与正文逐字一致；找不到会报错，请先 read_chapter 取准确原文。",
        "parameters": {"type": "object", "properties": {
            "old_text": {"type": "string", "description": "要被替换的原文，须与正文逐字一致"},
            "new_text": {"type": "string", "description": "替换后的新文字"}},
            "required": ["old_text", "new_text"]}}},
    {"type": "function", "function": {
        "name": "append_text",
        "description": "在当前章节正文末尾追加一段文字（补段落、贴成品用）。",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "要追加的正文"}},
            "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "edit_passage",
        "description": "把指定段落按 instruction 重写后替换回正文（一步完成：AI 改写 + 原地替换）。old_text 须与正文逐字一致。",
        "parameters": {"type": "object", "properties": {
            "old_text": {"type": "string", "description": "要重写的原文段落，须与正文逐字一致"},
            "instruction": {"type": "string", "description": "重写指令，如“更紧张”“更精炼”“改成口语化”"},
            "style": {"type": "string", "description": "可选风格预设：更生动/更精炼/文艺风/口语化/悬疑感"}},
            "required": ["old_text", "instruction"]}}},
    {"type": "function", "function": {
        "name": "continue_writing",
        "description": "根据指令续写正文，接在当前章节末尾。无需提供原文，自动取正文末尾作前文。",
        "parameters": {"type": "object", "properties": {
            "instruction": {"type": "string", "description": "续写方向/要求，可空"}}}}},
    {"type": "function", "function": {
        "name": "set_title",
        "description": "修改当前章节标题。",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"}}, "required": ["title"]}}},
    {"type": "function", "function": {
        "name": "set_notes",
        "description": "修改当前章节的备注（作者给自己/AI 的本章设定/梗概）。",
        "parameters": {"type": "object", "properties": {
            "notes": {"type": "string"}}, "required": ["notes"]}}},
    {"type": "function", "function": {
        "name": "create_chapter",
        "description": "在当前作品新建一章（空正文），返回新章节 id。",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "新章节标题"}},
            "required": ["title"]}}},
    {"type": "function", "function": {
        "name": "save_revision",
        "description": "把当前章节存为一个历史版本快照，返回版本 id。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "restore_revision",
        "description": "把当前章节回退到指定历史版本（用 list_revisions 取 rid）。回退前会自动存当前为快照，可撤销。",
        "parameters": {"type": "object", "properties": {
            "rid": {"type": "integer", "description": "要回退到的历史版本 id"}},
            "required": ["rid"]}}},
    {"type": "function", "function": {
        "name": "summarize",
        "description": "生成当前章节的 1-3 句剧情摘要（不改正文）。要保存可用 set_notes 写进备注。",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "check_consistency",
        "description": "对照作品设定校验当前正文，列出矛盾（人物/时间线/设定冲突）。不改正文。",
        "parameters": {"type": "object", "properties": {}}}},
]


def _agent_err(msg):
    return {"error": msg}


def _agent_bible(wid, uid):
    bible = db.get_work_notes(wid, uid) or ""
    digest = db.get_entity_digest(wid, uid)
    if digest:
        bible = (bible + "\n\n" + digest) if bible else digest
    return bible


def _compact_split(msgs, preserve):
    """计算可压缩前缀的起点索引：保留最近 preserve 条，且 recent 从一条 user
    消息开始（确保不切断 assistant(tool_calls)→tool 的工具对，避免悬空 tool_call_id）。
    返回 0 表示无可压缩前缀。"""
    n = len(msgs)
    if n <= preserve:
        return 0
    keep_from = n - preserve
    # 前移到首个 role=='user' 的边界
    while keep_from < n and msgs[keep_from].get("role") != "user":
        keep_from += 1
    return keep_from if keep_from < n else 0


def _agent_system(uid, cid):
    parts = [
        "你是作者的写作 agent。你可以通过工具直接操作作者的作品：改正文、续写、"
        "回退到历史版本、改章节标题/备注、新建章节、存版本、摘要、设定校验。"
        "原则：1) 要改某段文字前，先 read_chapter 读准确原文，再用 replace_text 或 edit_passage，"
        "old_text 必须与正文逐字一致；2) 每个写操作都会自动存版本，用户可一键撤销，所以放心改；"
        "3) 不要替作者下不可逆的决定；4) 回答简洁，做完事说一句即可。"
    ]
    if cid:
        c = db.get_chapter_meta(cid, uid)
        if c:
            parts.append(f"当前章节：#{cid}《{c['title']}》")
            notes = c.get("notes") or ""
            if notes:
                parts.append("本章备注：\n" + notes)
            content = c["content"] or ""
            parts.append(("当前正文全文（修改时请从中逐字复制 old_text）：\n" + content)
                         if content else "（正文为空）")
            bible = _agent_bible(c["work_id"], uid)
            if bible:
                parts.append("作品设定（人物/世界观/大纲），操作时请保持一致：\n" + bible)
    return {"role": "system", "content": "\n\n".join(parts)}


def _tool_read_chapter(uid, cid, cfg, args):
    if not cid:
        return _agent_err("当前没有选中章节")
    c = db.get_chapter_meta(cid, uid)
    if not c:
        return _agent_err("章节不存在")
    return {"changed": False, "title": c["title"], "notes": c.get("notes") or "",
            "content": c["content"] or "", "chars": len(c["content"] or "")}


def _tool_list_chapters(uid, cid, cfg, args):
    if not cid:
        return _agent_err("当前没有选中章节")
    c = db.get_chapter_meta(cid, uid)
    if not c:
        return _agent_err("章节不存在")
    lst = db.list_chapters(c["work_id"], uid) or []
    return {"changed": False, "chapters": [
        {"id": x["id"], "title": x["title"], "chars": x["chars"]} for x in lst]}


def _tool_list_revisions(uid, cid, cfg, args):
    if not cid:
        return _agent_err("当前没有选中章节")
    lst = db.list_revisions(cid, uid)
    if lst is None:
        return _agent_err("章节不存在")
    return {"changed": False, "revisions": [
        {"id": x["id"], "title": x["title"], "chars": x["chars"]} for x in lst]}


def _tool_replace_text(uid, cid, cfg, args):
    if not cid:
        return _agent_err("当前没有选中章节")
    old, new = args.get("old_text", ""), args.get("new_text", "")
    snap = db.add_revision(cid, uid)
    new_content = db.replace_text_in_chapter(cid, uid, old, new)
    if new_content is None:
        return _agent_err("在正文里找不到这段原文，请先 read_chapter 取准确原文再试")
    return {"changed": True, "summary": "已替换一处正文",
            "undo_rid": snap["id"] if snap else None}


def _tool_append_text(uid, cid, cfg, args):
    if not cid:
        return _agent_err("当前没有选中章节")
    text = args.get("text", "")
    snap = db.add_revision(cid, uid)
    seg = db.add_segment(cid, uid, "（agent 追加）", text, "续写")
    if seg is None:
        return _agent_err("追加失败")
    return {"changed": True, "summary": "已在末尾追加段落",
            "undo_rid": snap["id"] if snap else None}


def _tool_edit_passage(uid, cid, cfg, args):
    if not cid:
        return _agent_err("当前没有选中章节")
    old = args.get("old_text", "")
    instruction = args.get("instruction", "")
    style = args.get("style")
    c = db.get_chapter_meta(cid, uid)
    if not c:
        return _agent_err("章节不存在")
    rewritten = llm.process("改写", old, context=(c["content"] or "")[-1500:],
                            notes=c.get("notes") or "", bible=_agent_bible(c["work_id"], uid),
                            base_url=cfg["base_url"], api_key=cfg["api_key"], model=cfg["model"],
                            style=(style or instruction))
    snap = db.add_revision(cid, uid)
    if db.replace_text_in_chapter(cid, uid, old, rewritten) is None:
        return _agent_err("改写完成但在正文里找不到原文定位，请重新 read_chapter 取准确原文")
    return {"changed": True, "summary": f"已按「{instruction}」重写并替换该段",
            "undo_rid": snap["id"] if snap else None, "new_text": rewritten}


def _tool_continue_writing(uid, cid, cfg, args):
    if not cid:
        return _agent_err("当前没有选中章节")
    instruction = args.get("instruction") or "继续往下写"
    c = db.get_chapter_meta(cid, uid)
    if not c:
        return _agent_err("章节不存在")
    tail = (c["content"] or "")[-2000:]
    text = llm.process("续写", instruction, context=tail, notes=c.get("notes") or "",
                       bible=_agent_bible(c["work_id"], uid),
                       base_url=cfg["base_url"], api_key=cfg["api_key"], model=cfg["model"])
    snap = db.add_revision(cid, uid)
    if db.add_segment(cid, uid, "（agent 续写）", text, "续写") is None:
        return _agent_err("续写失败")
    return {"changed": True, "summary": "已续写并追加到末尾",
            "undo_rid": snap["id"] if snap else None, "new_text": text}


def _tool_set_title(uid, cid, cfg, args):
    if not cid:
        return _agent_err("当前没有选中章节")
    title = args.get("title", "")
    snap = db.add_revision(cid, uid)
    db.update_chapter(cid, uid, title, None, None)
    return {"changed": True, "summary": f"已改标题为「{title}」",
            "undo_rid": snap["id"] if snap else None}


def _tool_set_notes(uid, cid, cfg, args):
    if not cid:
        return _agent_err("当前没有选中章节")
    notes = args.get("notes", "")
    snap = db.add_revision(cid, uid)
    db.update_chapter(cid, uid, None, None, notes)
    return {"changed": True, "summary": "已更新本章备注",
            "undo_rid": snap["id"] if snap else None}


def _tool_create_chapter(uid, cid, cfg, args):
    if not cid:
        return _agent_err("请先在当前作品下选中任一章节（用于确定作品）")
    c = db.get_chapter_meta(cid, uid)
    if not c:
        return _agent_err("章节不存在")
    title = args.get("title", "新章节")
    r = db.create_chapter(c["work_id"], uid, title)
    if not r:
        return _agent_err("新建失败")
    return {"changed": False, "sidebar_dirty": True,
            "summary": f"已新建章节「{title}」（id={r['id']}）", "new_chapter_id": r["id"]}


def _tool_save_revision(uid, cid, cfg, args):
    if not cid:
        return _agent_err("当前没有选中章节")
    r = db.add_revision(cid, uid)
    if not r:
        return _agent_err("存版本失败")
    return {"changed": False, "summary": f"已存为版本 #{r['id']}", "revision_id": r["id"]}


def _tool_restore_revision(uid, cid, cfg, args):
    if not cid:
        return _agent_err("当前没有选中章节")
    rid = args.get("rid")
    snap = db.add_revision(cid, uid)  # 回退前先存当前为快照，可再撤销
    r = db.restore_revision(cid, uid, rid)
    if r is None:
        return _agent_err("该历史版本不存在")
    return {"changed": True, "summary": f"已回退到版本 #{rid}",
            "undo_rid": snap["id"] if snap else None}


def _tool_summarize(uid, cid, cfg, args):
    if not cid:
        return _agent_err("当前没有选中章节")
    c = db.get_chapter_meta(cid, uid)
    if not c:
        return _agent_err("章节不存在")
    if not (c["content"] or "").strip():
        return _agent_err("本章为空")
    s = llm.process("摘要", c["content"], bible=_agent_bible(c["work_id"], uid),
                    base_url=cfg["base_url"], api_key=cfg["api_key"], model=cfg["model"])
    return {"changed": False, "summary_text": s}


def _tool_check_consistency(uid, cid, cfg, args):
    if not cid:
        return _agent_err("当前没有选中章节")
    c = db.get_chapter_meta(cid, uid)
    if not c:
        return _agent_err("章节不存在")
    if not (c["content"] or "").strip():
        return _agent_err("本章为空")
    s = llm.process("校验", c["content"], notes=c.get("notes") or "",
                    bible=_agent_bible(c["work_id"], uid),
                    base_url=cfg["base_url"], api_key=cfg["api_key"], model=cfg["model"])
    return {"changed": False, "issues": s}


_AGENT_TOOLS = {
    "read_chapter": _tool_read_chapter, "list_chapters": _tool_list_chapters,
    "list_revisions": _tool_list_revisions, "replace_text": _tool_replace_text,
    "append_text": _tool_append_text, "edit_passage": _tool_edit_passage,
    "continue_writing": _tool_continue_writing, "set_title": _tool_set_title,
    "set_notes": _tool_set_notes, "create_chapter": _tool_create_chapter,
    "save_revision": _tool_save_revision, "restore_revision": _tool_restore_revision,
    "summarize": _tool_summarize, "check_consistency": _tool_check_consistency,
}


@app.post("/api/agent")
async def agent(request: Request):
    """AI agent：对话即操作。模型经 function calling 调工具改稿/回退/加章，
    每个写操作前自动存版本快照，返回 undo_rid 供前端撤销。
    服务端按 用户×章节 持久化对话；超长时把早期轮次 LLM 摘要压缩，保留最近几轮。"""
    uid = _auth(request)
    body = await request.json()
    text = (body.get("text") or "").strip()
    cid = body.get("chapter_id")
    if not text:
        raise HTTPException(400, "没有对话内容")
    st = db.get_settings(uid) or {}
    base_url = st.get("llm_base_url") or config.LLM_BASE_URL
    api_key = st.get("llm_api_key") or config.LLM_API_KEY
    model = st.get("llm_model") or config.LLM_MODEL
    if not api_key:
        raise HTTPException(500, "未配置 API Key，请在「设置」里填 base_url / key / 模型")
    cfg = {"base_url": base_url, "api_key": api_key, "model": model}

    # 加载持久化对话（服务端权威），追加本轮用户消息
    conv = db.get_conversation(uid, cid) or {"messages": [], "summary": ""}
    msgs = list(conv["messages"])
    summary = conv["summary"] or ""
    msgs.append({"role": "user", "content": text})

    # 发给模型的数组：系统提示 + 早期摘要(若有) + 当前对话
    messages = [_agent_system(uid, cid)]
    if summary:
        messages.append({"role": "user", "content": "[此前对话摘要]\n" + summary})
    messages.extend(msgs)

    reply = ""
    for _ in range(6):
        msg = llm.agent_chat(messages, AGENT_TOOLS, base_url=base_url, api_key=api_key, model=model)
        m = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            m["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {
                    "name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                for tc in msg.tool_calls
            ]
        messages.append(m)
        msgs.append(m)  # 同步写入持久化用的非系统消息列表
        if not msg.tool_calls:
            reply = (msg.content or "").strip()
            break
        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            fn = _AGENT_TOOLS.get(name)
            try:
                result = fn(uid, cid, cfg, args) if fn else {"error": f"未知工具 {name}"}
            except Exception as e:
                result = {"error": f"工具执行出错：{e}"}
            tm = {"role": "tool", "tool_call_id": tc.id,
                  "content": json.dumps(result, ensure_ascii=False)}
            messages.append(tm)
            msgs.append(tm)
    else:
        reply = "操作较多，已暂停。已执行的动作见对话记录，可逐条撤销。"

    # 超长压缩：把早期轮次交给 LLM 压成摘要，保留最近几轮（切在 user 边界，不切断工具对）
    compacted = False
    keep_from = _compact_split(msgs, config.AGENT_PRESERVE_RECENT)
    if keep_from > 0:
        total = sum(len(m.get("content") or "") for m in msgs)
        if total > config.AGENT_COMPACT_CHARS:
            try:
                summary = llm.summarize(msgs[:keep_from], prev=summary,
                                        base_url=base_url, api_key=api_key, model=model)
                msgs = msgs[keep_from:]
                compacted = True
            except Exception:
                # 摘要失败则保留原样，不阻断本轮
                pass

    db.save_conversation(uid, cid, msgs, summary)
    return {"reply": reply, "messages": msgs, "compacted": compacted}


@app.get("/api/agent/conversation")
async def get_agent_conversation(request: Request):
    """取当前 用户×章节 的持久化对话（切章/刷新后恢复上下文用）。"""
    uid = _auth(request)
    cid = _qparam_int(request, "chapter_id")
    conv = db.get_conversation(uid, cid) or {"messages": [], "summary": ""}
    return conv


@app.delete("/api/agent/conversation")
async def delete_agent_conversation(request: Request):
    """清空当前 用户×章节 的持久化对话（前端「清空」按钮用）。"""
    uid = _auth(request)
    cid = _qparam_int(request, "chapter_id")
    db.delete_conversation(uid, cid)
    return {"ok": True}


# ---------- 后台管理（admin） ----------

@app.get("/api/admin/users")
async def admin_users(request: Request):
    _admin_auth(request)
    return {"users": db.list_users_admin()}


@app.get("/api/admin/conversations")
async def admin_conversations(request: Request):
    """列出所有用户的对话（带用户名/章节标题），供管理员辨识后删除。"""
    _admin_auth(request)
    return {"conversations": db.list_conversations_admin()}


@app.delete("/api/admin/conversations/{conv_id}")
async def admin_delete_conversation(request: Request, conv_id: int):
    _admin_auth(request)
    if not db.admin_delete_conversation(conv_id):
        raise HTTPException(404, "对话不存在")
    return {"ok": True}


@app.delete("/api/admin/users/{target_uid}/conversations")
async def admin_clear_user_conversations(request: Request, target_uid: int):
    """清空指定用户的全部对话。"""
    _admin_auth(request)
    n = db.admin_clear_user_conversations(target_uid)
    return {"ok": True, "deleted": n}


# ---------- 静态前端（放最后，避免盖住 /api） ----------

app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=config.PORT, reload=False)
