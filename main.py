"""FastAPI 后端：鉴权 + 作品/章节 CRUD + AI 处理 + 静态前端。"""
import secrets

from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles

import config, db, llm

app = FastAPI(title="写作")
db.init_db()

# 内存里的登录态（单用户、重启即失效，够用）
_sessions = set()


def _auth(request: Request):
    tok = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    if tok not in _sessions:
        raise HTTPException(401, "未登录")


# ---------- 鉴权 ----------

@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    if body.get("password") != config.APP_PASSWORD:
        raise HTTPException(403, "密码错误")
    tok = secrets.token_hex(24)
    _sessions.add(tok)
    return {"token": tok}


@app.post("/api/logout")
async def logout(request: Request):
    _auth(request)
    tok = request.headers.get("Authorization", "").replace("Bearer ", "").strip()
    _sessions.discard(tok)
    return {"ok": True}


# ---------- 作品 / 章节 ----------

@app.get("/api/works")
async def get_works(request: Request):
    _auth(request)
    return db.list_works()


@app.post("/api/works")
async def new_work(request: Request):
    _auth(request)
    body = await request.json()
    return db.create_work(body.get("title", "未命名"))


@app.get("/api/works/{wid}/chapters")
async def get_chapters(wid: int, request: Request):
    _auth(request)
    return db.list_chapters(wid)


@app.post("/api/works/{wid}/chapters")
async def new_chapter(wid: int, request: Request):
    _auth(request)
    body = await request.json()
    return db.create_chapter(wid, body.get("title", "新章节"))


@app.get("/api/chapters/{cid}")
async def get_chapter(cid: int, request: Request):
    _auth(request)
    chap = db.get_chapter(cid)
    if not chap:
        raise HTTPException(404, "章节不存在")
    return chap


@app.put("/api/chapters/{cid}")
async def save_chapter(cid: int, request: Request):
    _auth(request)
    body = await request.json()
    return db.update_chapter(cid, body.get("title"), body.get("content"))


@app.post("/api/chapters/{cid}/undo")
async def undo(cid: int, request: Request):
    _auth(request)
    return db.undo_last_segment(cid)


# ---------- AI 处理 ----------

@app.post("/api/process")
async def do_process(request: Request):
    _auth(request)
    body = await request.json()
    mode = body.get("mode")
    text = (body.get("text") or "").strip()
    context = body.get("context") or ""
    chapter_id = body.get("chapter_id")

    if not text:
        raise HTTPException(400, "内容为空")

    if mode == "转写":
        # 不调 LLM，直接用识别原文
        result = text
    elif mode in ("润色", "扩写", "续写"):
        if not config.LLM_API_KEY:
            raise HTTPException(500, "未配置 LLM_API_KEY，请在 .env 填 base_url/key/model")
        result = llm.process(mode, text, context)
    else:
        raise HTTPException(400, "未知模式")

    seg = None
    if chapter_id:
        seg = db.add_segment(chapter_id, text, result, mode)

    return {"result": result, "raw": text, "mode": mode, "content": seg["content"] if seg else None}


# ---------- 静态前端（放最后，避免盖住 /api） ----------

app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=config.PORT, reload=False)
