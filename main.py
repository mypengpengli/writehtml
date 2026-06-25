"""FastAPI 后端：多用户鉴权 + 作品/章节 CRUD + AI 处理 + 拆分/排序/修订/导出。"""
import secrets
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
    return {"username": db.get_username(uid)}


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


# ---------- AI 处理 ----------

@app.post("/api/process")
async def do_process(request: Request):
    uid = _auth(request)
    body = await request.json()
    mode = body.get("mode")
    text = (body.get("text") or "").strip()
    context = body.get("context") or ""
    cid = body.get("chapter_id")

    if not text:
        raise HTTPException(400, "内容为空")

    notes = ""
    if cid:
        chap = db.get_chapter(cid, uid)
        if not chap:
            raise HTTPException(404, "章节不存在")
        notes = chap.get("notes") or ""

    if mode == "转写":
        result = text
    elif mode in ("润色", "扩写", "续写"):
        s = db.get_settings(uid) or {}
        base_url = s.get("llm_base_url") or config.LLM_BASE_URL
        api_key = s.get("llm_api_key") or config.LLM_API_KEY
        model = s.get("llm_model") or config.LLM_MODEL
        if not api_key:
            raise HTTPException(500, "未配置 API Key，请在「设置」里填 base_url / key / 模型")
        result = llm.process(mode, text, context, notes,
                             base_url=base_url, api_key=api_key, model=model)
    else:
        raise HTTPException(400, "未知模式")

    seg = db.add_segment(cid, uid, text, result, mode) if cid else None
    return {"result": result, "raw": text, "mode": mode, "content": seg["content"] if seg else None}


# ---------- 静态前端（放最后，避免盖住 /api） ----------

app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=config.PORT, reload=False)
