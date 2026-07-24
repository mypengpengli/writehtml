"""FastAPI 后端：多用户鉴权 + 作品/章节 CRUD + AI 处理 + 拆分/排序/修订/导出。"""
import json
import secrets
import difflib
import base64
import io
import re
import zipfile
from urllib.parse import quote

import yaml

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

import config, db, llm, pi_agent, skill_runtime

app = FastAPI(title="写作")
db.init_db()


@app.middleware("http")
async def add_browser_permission_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("Permissions-Policy", "microphone=(self)")
    return response


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


def _mask_key(key):
    return ("****" + key[-4:]) if key else ""


def _asr_config(settings):
    """转写可使用独立服务；未填写独立项时回退到文字模型配置。"""
    settings = settings or {}
    return {
        "base_url": (
            settings.get("asr_base_url") or config.ASR_BASE_URL
            or settings.get("llm_base_url") or config.LLM_BASE_URL
        ),
        "api_key": (
            settings.get("asr_api_key") or config.ASR_API_KEY
            or settings.get("llm_api_key") or config.LLM_API_KEY
        ),
        "model": settings.get("asr_model") or config.ASR_MODEL,
    }


def _provider_error(exc, limit=260):
    """保留可诊断的信息，但不把供应商的整段 JSON 直接塞进手机 toast。"""
    text = " ".join(str(exc).split())
    return text[:limit] + ("…" if len(text) > limit else "")


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
    # 设置页只展示“显式配置”的转写项；留空即代表沿用文字模型，不把回退值写死。
    asr_key = s.get("asr_api_key") or config.ASR_API_KEY
    # 明文 key 不回传，只给掩码提示是否已填
    masked = ("****" + key[-4:]) if key else ""
    return {
        "base_url": s.get("llm_base_url") or config.LLM_BASE_URL,
        "api_key_masked": masked,
        "has_key": bool(key),
        "model": s.get("llm_model") or config.LLM_MODEL,
        "asr_base_url": s.get("asr_base_url") or config.ASR_BASE_URL,
        "asr_api_key_masked": _mask_key(asr_key),
        "asr_has_key": bool(asr_key),
        "asr_model": s.get("asr_model") or config.ASR_MODEL,
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
        (body.get("asr_model") or "").strip(),
        (body.get("asr_base_url") or "").strip(),
        (body.get("asr_api_key") or "").strip(),
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
    uid = _auth(request)
    at_chapter_id = _qparam_int(request, "chapter_id")
    if at_chapter_id is not None:
        chapter = db.get_chapter_meta(at_chapter_id, uid)
        if not chapter or chapter["work_id"] != wid:
            raise HTTPException(404, "章节不存在")
    r = db.list_entities(wid, uid, at_chapter_id)
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


# ---------- 人物动态卡（基础设定 + 按章节生效的状态/成长记录）----------

def _state_error(result):
    if result is None:
        raise HTTPException(404, "人物卡不存在")
    if result.get("invalid_chapter"):
        raise HTTPException(404, "章节不存在")
    if result.get("not_character"):
        raise HTTPException(400, "只有人物卡可以记录剧情状态")
    if result.get("empty_state"):
        raise HTTPException(400, "请至少填写一项人物状态")
    if result.get("resolved"):
        raise HTTPException(409, "该 AI 提议已经处理")
    return result


@app.get("/api/entities/{eid}/state-history")
async def get_entity_state_history(eid: int, request: Request):
    uid = _auth(request)
    chapter_id = _qparam_int(request, "chapter_id")
    result = _state_error(db.get_entity_state_overview(eid, uid, chapter_id))
    return result


@app.post("/api/entities/{eid}/state-versions")
async def save_entity_state_version(eid: int, request: Request):
    uid = _auth(request)
    body = await request.json()
    chapter_id = body.get("chapter_id")
    if not isinstance(chapter_id, int):
        raise HTTPException(400, "请选择状态生效章节")
    state = body.get("state")
    if not isinstance(state, dict):
        raise HTTPException(400, "人物状态格式不正确")
    result = _state_error(db.create_character_state_version(
        eid, uid, chapter_id, state, body.get("change_summary", ""), body.get("evidence", ""),
    ))
    return {"ok": True, "version": result}


@app.get("/api/chapters/{cid}/character-state-proposals")
async def get_character_state_proposals(cid: int, request: Request):
    result = db.list_character_state_proposals(cid, _auth(request))
    if result is None:
        raise HTTPException(404, "章节不存在")
    return result


@app.post("/api/chapters/{cid}/character-state-proposals/analyze")
async def analyze_character_state_proposals(cid: int, request: Request):
    uid = _auth(request)
    proposals = _generate_character_state_proposals(uid, cid, raise_on_error=True)
    return {"proposals": proposals}


@app.post("/api/character-state-proposals/{pid}/accept")
async def accept_character_state_proposal(pid: int, request: Request):
    uid = _auth(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    state = body.get("state") if "state" in body else None
    if state is not None and not isinstance(state, dict):
        raise HTTPException(400, "人物状态格式不正确")
    result = _state_error(db.accept_character_state_proposal(
        pid, uid, state, body.get("change_summary") if "change_summary" in body else None,
        body.get("evidence") if "evidence" in body else None,
    ))
    return {"ok": True, **result}


@app.post("/api/character-state-proposals/{pid}/reject")
async def reject_character_state_proposal(pid: int, request: Request):
    result = _state_error(db.reject_character_state_proposal(pid, _auth(request)))
    return result


# ---------- AI Skills（Agent Skills / SKILL.md）----------

_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SKILL_RESOURCE_EXTS = {".md", ".txt", ".json", ".csv", ".yaml", ".yml"}


def _skill_scope_payload(body):
    work_id = body.get("work_id")
    if work_id is not None and (not isinstance(work_id, int) or isinstance(work_id, bool)):
        raise HTTPException(400, "Skill 的作品范围无效")
    enabled = body.get("enabled", True)
    if not isinstance(enabled, bool):
        raise HTTPException(400, "Skill 启用状态无效")
    return work_id, enabled

def _skill_payload(body):
    if not isinstance(body, dict):
        raise HTTPException(400, "Skill 数据格式无效")
    name = (body.get("name") or "").strip()
    description = (body.get("description") or "").strip()
    instruction = (body.get("instruction") or "").strip()
    work_id, enabled = _skill_scope_payload(body)
    if not name:
        raise HTTPException(400, "Skill 名称不能为空")
    if not instruction:
        raise HTTPException(400, "Skill 规则不能为空")
    if len(name) > 64:
        raise HTTPException(400, "Skill 名称不能超过 64 字")
    if len(description) > 1024:
        raise HTTPException(400, "Skill 说明不能超过 1024 字")
    if len(instruction) > 8000:
        raise HTTPException(400, "Skill 规则不能超过 8000 字")
    return name, description, instruction, work_id, enabled


def _parse_skill_md(markdown):
    """解析 Agent Skills 标准的 YAML frontmatter + Markdown 正文。"""
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > 80_000:
        raise HTTPException(400, "SKILL.md 过大，请控制在 80,000 字以内")
    if not text.startswith("---\n"):
        raise HTTPException(400, "SKILL.md 缺少 YAML frontmatter（以 --- 开始）")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise HTTPException(400, "SKILL.md 的 YAML frontmatter 没有结束标记 ---")
    try:
        meta = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError as e:
        raise HTTPException(400, f"SKILL.md frontmatter 无法解析：{e}")
    if not isinstance(meta, dict):
        raise HTTPException(400, "SKILL.md frontmatter 必须是键值对象")
    name = meta.get("name")
    description = meta.get("description")
    if not isinstance(name, str) or not _SKILL_NAME_RE.fullmatch(name) or len(name) > 64:
        raise HTTPException(400, "SKILL.md 的 name 必须是 1-64 位小写字母、数字或连字符")
    if not isinstance(description, str) or not description.strip() or len(description.strip()) > 1024:
        raise HTTPException(400, "SKILL.md 的 description 必填，且不能超过 1024 字")
    instruction = text[end + 5:].strip()
    if len(instruction) > 8000:
        raise HTTPException(400, "SKILL.md 正文过长，请拆分到 references/ 后再导入")
    return name, description.strip(), instruction


def _decode_skill_import(body):
    """解码单个 SKILL.md 或 ZIP 包；只保存可安全按需读取的文本资源。"""
    filename = (body.get("filename") or "").strip().lower()
    data_b64 = body.get("data")
    if not filename or not isinstance(data_b64, str):
        raise HTTPException(400, "请选择 SKILL.md 或 Skill ZIP 包")
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except Exception:
        raise HTTPException(400, "Skill 文件数据无效")
    if not raw or len(raw) > 2 * 1024 * 1024:
        raise HTTPException(400, "Skill 文件不能为空，且压缩包不能超过 2MB")
    if not filename.endswith(".zip"):
        if not filename.endswith(".md"):
            raise HTTPException(400, "只支持 SKILL.md 或包含它的 ZIP 包")
        try:
            return raw.decode("utf-8-sig"), [], []
        except UnicodeDecodeError:
            raise HTTPException(400, "SKILL.md 必须为 UTF-8 编码")

    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise HTTPException(400, "Skill ZIP 包无效")
    files = [info for info in archive.infolist() if not info.is_dir()]
    if len(files) > 80 or sum(info.file_size for info in files) > 6 * 1024 * 1024:
        raise HTTPException(400, "Skill ZIP 包文件过多或解压后过大")
    if any(".." in info.filename.replace("\\", "/").split("/") for info in files):
        raise HTTPException(400, "Skill ZIP 包包含不安全路径")
    skill_files = [info for info in files if info.filename.replace("\\", "/").rstrip("/").endswith("/SKILL.md")
                   or info.filename.replace("\\", "/") == "SKILL.md"]
    if len(skill_files) != 1:
        raise HTTPException(400, "ZIP 包中必须且只能有一个 SKILL.md")
    skill_info = skill_files[0]
    root = skill_info.filename.replace("\\", "/").rsplit("/", 1)[0] if "/" in skill_info.filename.replace("\\", "/") else ""
    root_prefix = root + "/" if root else ""
    try:
        markdown = archive.read(skill_info).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "SKILL.md 必须为 UTF-8 编码")

    resources, skipped, total = [], [], 0
    for info in files:
        path = info.filename.replace("\\", "/")
        if info == skill_info or not path.startswith(root_prefix):
            continue
        relative = path[len(root_prefix):]
        suffix = "." + relative.rsplit(".", 1)[-1].lower() if "." in relative else ""
        if suffix not in _SKILL_RESOURCE_EXTS or info.file_size > 256 * 1024:
            skipped.append(relative)
            continue
        try:
            content = archive.read(info).decode("utf-8-sig")
        except UnicodeDecodeError:
            skipped.append(relative)
            continue
        if total + len(content) > 600_000:
            skipped.append(relative)
            continue
        total += len(content)
        resources.append({"path": relative, "content": content})
    return markdown, resources, skipped


@app.get("/api/agent/skills")
async def get_agent_skills(request: Request):
    uid = _auth(request)
    try:
        work_id = _qparam_int(request, "work_id")
    except ValueError:
        raise HTTPException(400, "作品范围无效")
    r = db.list_agent_skills(uid, work_id)
    if r is None:
        raise HTTPException(404, "作品不存在")
    return r


@app.post("/api/agent/skills")
async def new_agent_skill(request: Request):
    uid = _auth(request)
    name, description, instruction, work_id, enabled = _skill_payload(await request.json())
    r = db.create_agent_skill(uid, work_id, name, description, instruction, enabled)
    if r is None:
        raise HTTPException(404, "作品不存在")
    return r


@app.post("/api/agent/skills/import")
async def import_agent_skill(request: Request):
    """导入标准 SKILL.md，或包含该目录结构的 ZIP 包。"""
    uid = _auth(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "Skill 导入数据无效")
    work_id, enabled = _skill_scope_payload(body)
    markdown, resources, skipped = _decode_skill_import(body)
    name, description, instruction = _parse_skill_md(markdown)
    r = db.create_agent_skill(
        uid, work_id, name, description, instruction, enabled,
        source_kind="skill_md", source_markdown=markdown, resources=resources,
    )
    if r is None:
        raise HTTPException(404, "作品不存在")
    return {**r, "skipped_files": skipped}


@app.put("/api/agent/skills/{skill_id}")
async def save_agent_skill(skill_id: int, request: Request):
    uid = _auth(request)
    name, description, instruction, work_id, enabled = _skill_payload(await request.json())
    if not db.update_agent_skill(skill_id, uid, work_id, name, description, instruction, enabled):
        raise HTTPException(404, "Skill 或作品不存在")
    return {"ok": True}


@app.delete("/api/agent/skills/{skill_id}")
async def del_agent_skill(skill_id: int, request: Request):
    if not db.delete_agent_skill(skill_id, _auth(request)):
        raise HTTPException(404, "Skill 不存在")
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
        # bible 只在真的要调 LLM 时才拼；人物状态按当前章节时点生效。
        bible = ""
        if cid:
            bible = _agent_bible(chap["work_id"], uid, cid)
        result = llm.process(mode, text, context, notes, bible=bible,
                             base_url=base_url, api_key=api_key, model=model, style=style)
    else:
        raise HTTPException(400, "未知模式")

    # 只有这些模式把结果写进正文；校验/摘要不污染正文
    seg = None
    if cid and mode in ("转写", "润色", "扩写", "续写", "找回"):
        seg = db.add_segment(cid, uid, seg_raw, result, mode)
    proposals = []
    if seg and mode in ("润色", "扩写", "续写", "找回"):
        proposals = _generate_character_state_proposals(
            uid, cid, base_url=base_url, api_key=api_key, model=model,
        )
    return {"result": result, "raw": seg_raw, "mode": mode, "content": seg["content"] if seg else None,
            "character_state_proposals": proposals}


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
            bible = _agent_bible(chap["work_id"], uid, cid)
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


@app.post("/api/asr")
async def transcribe_audio(request: Request):
    """智能体语音入口：浏览器录音上传，后端走 OpenAI 兼容音频转写。"""
    uid = _auth(request)
    audio = await request.body()
    if not audio:
        raise HTTPException(400, "没有收到录音")
    if len(audio) > 15 * 1024 * 1024:
        raise HTTPException(413, "录音太长，请控制在一分钟内")
    asr = _asr_config(db.get_settings(uid))
    if not asr["api_key"]:
        raise HTTPException(500, "未配置转写服务，请在「设置」里填语音转写的 Base URL、Key 和模型")
    mime = (request.headers.get("content-type") or "audio/webm").split(";")[0]
    ext = "webm"
    if "mp4" in mime:
        ext = "mp4"
    elif "mpeg" in mime or "mp3" in mime:
        ext = "mp3"
    elif "wav" in mime:
        ext = "wav"
    try:
        text = llm.transcribe(audio, filename=f"speech.{ext}", mime_type=mime,
                              base_url=asr["base_url"], api_key=asr["api_key"], model=asr["model"])
    except Exception as e:
        raise HTTPException(502, "语音转写服务不可用。请检查转写专用 Base URL、Key、模型；"
                            "当前文字模型不一定提供 /audio/transcriptions。"
                            f" 详情：{_provider_error(e)}")
    if not text:
        raise HTTPException(500, "语音转写没有返回文字")
    return {"text": text}


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
    {"type": "function", "function": {
        "name": "activate_skill",
        "description": "按当前用户已安装 Skill 目录中的 id，加载一个与本次任务匹配的 Skill 完整规则。仅在用户明确提到某 Skill，或任务与其说明高度匹配时调用。",
        "parameters": {"type": "object", "properties": {
            "skill_id": {"type": "integer", "description": "Skill 目录里列出的 id"}},
            "required": ["skill_id"]}}},
    {"type": "function", "function": {
        "name": "read_skill_resource",
        "description": "读取已导入数据库的 Skill 引用文本资源（如 references/STYLE.md）。仅返回已导入的文本资料。",
        "parameters": {"type": "object", "properties": {
            "skill_id": {"type": "integer", "description": "已激活的 Skill id"},
            "path": {"type": "string", "description": "Skill 包内相对路径，例如 references/STYLE.md"}},
            "required": ["skill_id", "path"]}}},
]


def _agent_err(msg):
    return {"error": msg}


def _agent_bible(wid, uid, cid=None):
    bible = db.get_work_notes(wid, uid) or ""
    digest = db.get_entity_digest(wid, uid, cid)
    if digest:
        bible = (bible + "\n\n" + digest) if bible else digest
    return bible


def _parse_json_from_model(raw):
    """兼容模型偶尔附带的 Markdown 围栏，只接受第一个可解析 JSON 值。"""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text).strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return value
    return None


def _short_character_state(state):
    return {field: (state or {}).get(field, "") for field in db.CHARACTER_STATE_FIELDS}


def _generate_character_state_proposals(uid, cid, *, base_url=None, api_key=None, model=None,
                                        raise_on_error=False):
    """根据当前章正文提取人物变化。失败不阻断主写作，且永远只生成待确认提议。"""
    chapter = db.get_chapter_meta(cid, uid) if cid else None
    if not chapter:
        if raise_on_error:
            raise HTTPException(404, "章节不存在")
        return []
    # 同一章已经被作者确认过的状态也算当前事实；连续多次写作不能退回到章前状态。
    characters = db.list_character_cards(chapter["work_id"], uid, cid) or []
    if not characters or not (chapter.get("content") or "").strip():
        return []

    settings = db.get_settings(uid) or {}
    base_url = base_url or settings.get("llm_base_url") or config.LLM_BASE_URL
    api_key = api_key or settings.get("llm_api_key") or config.LLM_API_KEY
    model = model or settings.get("llm_model") or config.LLM_MODEL
    if not api_key:
        if raise_on_error:
            raise HTTPException(500, "未配置 API Key，无法提取人物状态")
        return []

    cards = []
    known = {}
    for character in characters:
        known[character["id"]] = character
        cards.append({
            "entity_id": character["id"],
            "name": character["name"],
            "summary": (character.get("summary") or "")[:900],
            "detail": (character.get("detail") or "")[:1800],
            "confirmed_state_at_this_point": _short_character_state(character.get("current_state")),
        })
    prompt = {
        "task": "阅读当前章节，只提取文本明确支持的人物动态变化；不要编造，也不要重写人物基础设定。",
        "output": {
            "updates": [{
                "entity_id": 1,
                "state": {
                    "location": "", "goal": "", "emotion": "", "physical": "",
                    "information": "", "relationships": "", "assets": "", "secrets": "", "notes": "",
                },
                "change_summary": "一句话说明本章发生的变化",
                "evidence": "来自本章的简短事实依据，不要长引文",
            }],
        },
        "rules": [
            "只返回 JSON 对象，不要 Markdown、不要解释。",
            "没有明确、重要变化时，返回 {\"updates\": []}。",
            "state 必须是截至本章结束的完整当前状态：保留当前已确认状态中仍然成立的信息；未知字段用空字符串。",
            "只能使用给定 entity_id，且只给本章实际涉及、状态发生实质变化的人物生成 update。",
        ],
        "characters": cards,
        "chapter": {
            "id": chapter["id"], "title": chapter["title"], "notes": chapter.get("notes") or "",
            "content": (chapter.get("content") or "")[-14000:],
        },
    }
    messages = [
        {"role": "system", "content": "你是小说人物连续性记录员。输出必须可被 JSON 解析，不能添加任何额外文字。"},
        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
    ]
    try:
        parsed = _parse_json_from_model(llm.chat(messages, base_url=base_url, api_key=api_key, model=model))
    except HTTPException:
        raise
    except Exception as exc:
        if raise_on_error:
            raise HTTPException(502, "人物状态提取失败：" + _provider_error(exc))
        return []

    updates = parsed.get("updates") if isinstance(parsed, dict) else parsed if isinstance(parsed, list) else []
    if not isinstance(updates, list):
        if raise_on_error:
            raise HTTPException(502, "人物状态提取没有返回有效 JSON")
        return []
    proposals = []
    seen = set()
    for update in updates:
        if not isinstance(update, dict):
            continue
        entity_id = update.get("entity_id")
        if isinstance(entity_id, str) and entity_id.isdigit():
            entity_id = int(entity_id)
        if not isinstance(entity_id, int) or isinstance(entity_id, bool) or entity_id not in known or entity_id in seen:
            continue
        raw_state = update.get("state")
        if not isinstance(raw_state, dict):
            raw_state = {field: update.get(field, "") for field in db.CHARACTER_STATE_FIELDS}
        before = _short_character_state(known[entity_id].get("current_state"))
        state = db.normalize_character_state(raw_state, before)
        if state == before or not db.character_state_has_content(state):
            continue
        summary = update.get("change_summary") if isinstance(update.get("change_summary"), str) else ""
        evidence = update.get("evidence") if isinstance(update.get("evidence"), str) else ""
        if not summary.strip():
            continue
        saved = db.upsert_character_state_proposal(entity_id, uid, cid, state, summary, evidence)
        if saved and not saved.get("not_character") and not saved.get("empty_state"):
            saved["entity_name"] = known[entity_id]["name"]
            proposals.append(saved)
            seen.add(entity_id)
    return proposals


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
        "3) 不要替作者下不可逆的决定；4) 通过写作工具改动正文后，系统会生成待作者确认的人物状态提议，"
        "未确认前不改变人物卡；5) 回答简洁，做完事说一句即可。"
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
            bible = _agent_bible(c["work_id"], uid, cid)
            if bible:
                parts.append("作品设定（人物/世界观/大纲），操作时请保持一致：\n" + bible)
    return {"role": "system", "content": "\n\n".join(parts)}


def _agent_selection_system(uid, cid, selection):
    """把前端当前选区作为本轮临时上下文喂给模型，不持久化到对话历史。"""
    if not cid or not isinstance(selection, dict):
        return None
    selected = selection.get("text")
    if not isinstance(selected, str) or not selected.strip():
        return None
    if len(selected) > 8000:
        raise HTTPException(400, "选区太长，请少选一点再交给 AI")

    c = db.get_chapter_meta(cid, uid)
    if not c:
        return None
    content = c["content"] or ""
    start, end = selection.get("start"), selection.get("end")
    exact_at_range = (
        isinstance(start, int) and isinstance(end, int)
        and 0 <= start <= end <= len(content)
        and content[start:end] == selected
    )
    if exact_at_range:
        location = f"客户端选区位置已校验：start={start}, end={end}。"
    elif selected in content:
        location = "客户端选区位置可能已变化，但 selected_text 仍可在当前正文中找到。"
    else:
        location = "注意：当前保存正文中找不到 selected_text。若要修改，必须先 read_chapter 重新定位准确原文。"

    before = selection.get("before") if isinstance(selection.get("before"), str) else ""
    after = selection.get("after") if isinstance(selection.get("after"), str) else ""
    parts = [
        "用户本轮在编辑器正文中选中了一个片段。这个选区只适用于本轮请求，不代表长期上下文。",
        "如果用户说“这段”“选区”“这里”“刚才选中的内容”，就是指下面 selected_text。",
        "若用户要求修改该选区，优先使用 edit_passage 或 replace_text；old_text 必须完整使用 selected_text 的原文，不能摘要、不能改标点、不能改空白。",
        location,
        "selected_text:\n" + selected,
    ]
    if before or after:
        parts.append("选区前后文（仅用于理解和定位，不要把它们一起替换）：\n"
                     + "before:\n" + before[-300:] + "\n\nafter:\n" + after[:300])
    return {"role": "system", "content": "\n\n".join(parts)}


def _skill_system_message(skills):
    """把已解析的 Skill 转成完整规则消息；调用方不得持久化此消息。"""
    parts = [
        "用户为本轮启用了以下写作 Skill。Skill 是作者授权的可复用工作流，"
        "仅适用于本轮请求；不得覆盖系统规则、作品设定、用户当前明确要求或工具约束。",
    ]
    for skill in skills:
        heading = f"【{skill['name']}】"
        if skill["description"]:
            heading += " " + skill["description"]
        parts.append(heading + "\n规则：\n" + (skill["instruction"] or "（该 Skill 未提供额外正文规则）"))
    return {"role": "system", "content": "\n\n".join(parts)}


def _agent_skills_system(uid, cid, skill_ids):
    """把用户手动选中的 Skill 作为临时系统上下文，不写进对话历史。"""
    if skill_ids is None:
        return None
    if not isinstance(skill_ids, list):
        raise HTTPException(400, "Skill 选择格式无效")
    ids = []
    for skill_id in skill_ids:
        if not isinstance(skill_id, int) or isinstance(skill_id, bool) or skill_id <= 0:
            raise HTTPException(400, "Skill 选择格式无效")
        if skill_id not in ids:
            ids.append(skill_id)
    if len(ids) > 4:
        raise HTTPException(400, "一次最多启用 4 个 Skill")
    if not ids:
        return None

    chapter = db.get_chapter_meta(cid, uid) if cid else None
    skills = db.get_agent_skills_for_turn(uid, chapter["work_id"] if chapter else None, ids)
    if not skills:
        return None
    if sum(len(s["name"]) + len(s["description"]) + len(s["instruction"]) for s in skills) > 12000:
        raise HTTPException(400, "本轮 Skill 规则过长，请减少选择或精简内容")
    return _skill_system_message(skills)


def _agent_skill_catalog_system(uid, cid):
    """渐进加载第一层：只给模型可用 Skill 的 name/description，让它按需 activate。"""
    chapter = db.get_chapter_meta(cid, uid) if cid else None
    catalog = db.list_agent_skill_catalog(uid, chapter["work_id"] if chapter else None)
    if not catalog:
        return None
    items = []
    for skill in catalog:
        source = "SKILL.md" if skill.get("source_kind") == "skill_md" else "自定义"
        description = skill["description"] or "无补充说明"
        items.append(f"- id={skill['id']} | {skill['name']} | {source} | {description}")
    return {
        "role": "system",
        "content": "当前用户安装了以下可按需调用的 Skills（仅元数据，完整规则尚未加载）：\n"
                   + "\n".join(items)
                   + "\n\n当用户明确提到某个 Skill，或请求与其 description 高度匹配时，先调用 activate_skill 加载完整规则；"
                     "不匹配时不要调用。网页导入到数据库的 Skill 包只保存文本资料，可在激活后用 "
                     "read_skill_resource 按需读取。",
    }


def _tool_read_chapter(uid, cid, cfg, args):
    if not cid:
        return _agent_err("当前没有选中章节")
    c = db.get_chapter_meta(cid, uid)
    if not c:
        return _agent_err("章节不存在")
    return {"changed": False, "title": c["title"], "notes": c.get("notes") or "",
            "content": c["content"] or "", "chars": len(c["content"] or "")}


def _tool_activate_skill(uid, cid, cfg, args):
    skill_id = args.get("skill_id")
    if not isinstance(skill_id, int) or isinstance(skill_id, bool) or skill_id <= 0:
        return _agent_err("Skill id 无效")
    if skill_id in cfg.get("active_skill_ids", set()):
        return {"changed": False, "summary": f"Skill #{skill_id} 已在本轮启用"}
    chapter = db.get_chapter_meta(cid, uid) if cid else None
    skills = db.get_agent_skills_for_turn(uid, chapter["work_id"] if chapter else None, [skill_id])
    if not skills:
        return _agent_err("Skill 不存在、已停用，或不属于当前作品范围")
    skill_msg = _skill_system_message(skills)
    existing = cfg.get("skill_instructions") or ""
    if len(existing) + len(skill_msg["content"]) > 12000:
        return _agent_err("本轮 Skill 规则过长，请只启用必要的 Skill")
    return {
        "changed": False, "summary": f"已启用 Skill「{skills[0]['name']}」",
        "_skill_system": skill_msg["content"], "_skill_id": skill_id,
    }


def _tool_read_skill_resource(uid, cid, cfg, args):
    skill_id, path = args.get("skill_id"), args.get("path")
    if not isinstance(skill_id, int) or isinstance(skill_id, bool) or skill_id not in cfg.get("active_skill_ids", set()):
        return _agent_err("只能读取本轮已启用 Skill 的资源")
    if not isinstance(path, str) or not path or len(path) > 240 or path.startswith(("/", "\\")) or ".." in path.replace("\\", "/").split("/"):
        return _agent_err("Skill 资源路径无效")
    resource = db.get_agent_skill_resource(uid, skill_id, path.replace("\\", "/"))
    if not resource:
        return _agent_err("找不到该 Skill 文本资源；脚本、二进制资产和未导入文件不能读取")
    content = resource["content"]
    truncated = len(content) > 6000
    if truncated:
        content = content[:6000] + "\n\n（该资源过长，已截取前 6000 字）"
    return {
        "changed": False, "summary": f"已读取 Skill 资料 {resource['path']}",
        "_skill_resource_system": f"已读取已激活 Skill 的资料 {resource['path']}：\n{content}",
        "_resource_truncated": truncated,
    }


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
            "undo_rid": snap["id"] if snap else None, "_character_state_dirty": True}


def _tool_append_text(uid, cid, cfg, args):
    if not cid:
        return _agent_err("当前没有选中章节")
    text = args.get("text", "")
    snap = db.add_revision(cid, uid)
    seg = db.add_segment(cid, uid, "（agent 追加）", text, "续写")
    if seg is None:
        return _agent_err("追加失败")
    return {"changed": True, "summary": "已在末尾追加段落",
            "undo_rid": snap["id"] if snap else None, "_character_state_dirty": True}


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
                            notes=c.get("notes") or "", bible=_agent_bible(c["work_id"], uid, cid),
                            base_url=cfg["base_url"], api_key=cfg["api_key"], model=cfg["model"],
                            style=(style or instruction), skill_instructions=cfg.get("skill_instructions"))
    snap = db.add_revision(cid, uid)
    if db.replace_text_in_chapter(cid, uid, old, rewritten) is None:
        return _agent_err("改写完成但在正文里找不到原文定位，请重新 read_chapter 取准确原文")
    return {"changed": True, "summary": f"已按「{instruction}」重写并替换该段",
            "undo_rid": snap["id"] if snap else None, "new_text": rewritten,
            "_character_state_dirty": True}


def _tool_continue_writing(uid, cid, cfg, args):
    if not cid:
        return _agent_err("当前没有选中章节")
    instruction = args.get("instruction") or "继续往下写"
    c = db.get_chapter_meta(cid, uid)
    if not c:
        return _agent_err("章节不存在")
    tail = (c["content"] or "")[-2000:]
    text = llm.process("续写", instruction, context=tail, notes=c.get("notes") or "",
                       bible=_agent_bible(c["work_id"], uid, cid),
                       base_url=cfg["base_url"], api_key=cfg["api_key"], model=cfg["model"],
                       skill_instructions=cfg.get("skill_instructions"))
    snap = db.add_revision(cid, uid)
    if db.add_segment(cid, uid, "（agent 续写）", text, "续写") is None:
        return _agent_err("续写失败")
    return {"changed": True, "summary": "已续写并追加到末尾",
            "undo_rid": snap["id"] if snap else None, "new_text": text,
            "_character_state_dirty": True}


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
    s = llm.process("摘要", c["content"], bible=_agent_bible(c["work_id"], uid, cid),
                    base_url=cfg["base_url"], api_key=cfg["api_key"], model=cfg["model"],
                    skill_instructions=cfg.get("skill_instructions"))
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
                    bible=_agent_bible(c["work_id"], uid, cid),
                    base_url=cfg["base_url"], api_key=cfg["api_key"], model=cfg["model"],
                    skill_instructions=cfg.get("skill_instructions"))
    return {"changed": False, "issues": s}


_AGENT_TOOLS = {
    "read_chapter": _tool_read_chapter, "list_chapters": _tool_list_chapters,
    "activate_skill": _tool_activate_skill, "read_skill_resource": _tool_read_skill_resource,
    "list_revisions": _tool_list_revisions, "replace_text": _tool_replace_text,
    "append_text": _tool_append_text, "edit_passage": _tool_edit_passage,
    "continue_writing": _tool_continue_writing, "set_title": _tool_set_title,
    "set_notes": _tool_set_notes, "create_chapter": _tool_create_chapter,
    "save_revision": _tool_save_revision, "restore_revision": _tool_restore_revision,
    "summarize": _tool_summarize, "check_consistency": _tool_check_consistency,
}


def _pi_text(content):
    """Extract visible text from a Pi content array or a legacy string."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        part.get("text", "") for part in content
        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
    )


def _pi_timestamp():
    import time
    return int(time.time() * 1000)


def _pi_tool_name_map(messages):
    names = {}
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function") or {}
            call_id = call.get("id")
            if isinstance(call_id, str) and isinstance(function.get("name"), str):
                names[call_id] = function["name"]
    return names


def _is_pi_transcript(messages):
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "toolResult":
            return True
        if message.get("role") in {"user", "assistant"} and isinstance(message.get("content"), list):
            return True
    return False


def _legacy_messages_to_pi(messages, model):
    """Migrate old OpenAI-shaped persisted messages lazily on the next Pi turn."""
    if _is_pi_transcript(messages):
        return list(messages)
    call_names = _pi_tool_name_map(messages)
    result = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        timestamp = _pi_timestamp()
        if role == "user":
            text = _pi_text(message.get("content"))
            result.append({"role": "user", "content": [{"type": "text", "text": text}], "timestamp": timestamp})
        elif role == "assistant":
            content = []
            text = _pi_text(message.get("content"))
            if text:
                content.append({"type": "text", "text": text})
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") or {}
                call_id = call.get("id")
                name = function.get("name")
                raw_args = function.get("arguments") or "{}"
                try:
                    arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except Exception:
                    arguments = {}
                if isinstance(call_id, str) and isinstance(name, str) and isinstance(arguments, dict):
                    content.append({"type": "toolCall", "id": call_id, "name": name, "arguments": arguments})
            result.append({
                "role": "assistant", "content": content, "api": "openai-completions", "provider": "openai",
                "model": model, "usage": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0,
                                           "totalTokens": 0, "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0}},
                "stopReason": "stop", "timestamp": timestamp,
            })
        elif role == "tool":
            raw = message.get("content") or "{}"
            try:
                details = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                details = {"summary": str(raw)}
            call_id = message.get("tool_call_id") or "legacy-tool-result"
            result.append({
                "role": "toolResult", "toolCallId": call_id, "toolName": call_names.get(call_id, "writing_tool"),
                "content": [{"type": "text", "text": json.dumps(details, ensure_ascii=False)}],
                "details": details, "isError": bool(isinstance(details, dict) and details.get("error")), "timestamp": timestamp,
            })
    return result


def _pi_messages_for_frontend(messages):
    """Translate Pi transcript messages into the stable structure used by static/app.js."""
    if not _is_pi_transcript(messages):
        return list(messages)
    result = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "user":
            result.append({"role": "user", "content": _pi_text(message.get("content"))})
        elif role == "assistant":
            item = {"role": "assistant", "content": _pi_text(message.get("content"))}
            calls = []
            for part in message.get("content") or []:
                if not isinstance(part, dict) or part.get("type") != "toolCall":
                    continue
                calls.append({"id": part.get("id"), "type": "function", "function": {
                    "name": part.get("name"), "arguments": json.dumps(part.get("arguments") or {}, ensure_ascii=False),
                }})
            if calls:
                item["tool_calls"] = calls
            result.append(item)
        elif role == "toolResult":
            details = message.get("details")
            if not isinstance(details, (dict, list)):
                raw = _pi_text(message.get("content"))
                try:
                    details = json.loads(raw)
                except Exception:
                    details = {"summary": raw}
            result.append({
                "role": "tool", "tool_call_id": message.get("toolCallId"), "name": message.get("toolName"),
                "content": json.dumps(details, ensure_ascii=False),
            })
    return result


def _pi_message_size(message):
    if not isinstance(message, dict):
        return 0
    return len(_pi_text(message.get("content"))) + len(json.dumps(message.get("details") or {}, ensure_ascii=False))


def _pi_recent_history(messages):
    limit = max(0, config.PI_AGENT_MAX_HISTORY_MESSAGES)
    if not limit or len(messages) <= limit:
        return list(messages)
    start = _compact_split(messages, limit)
    # Do not sever an assistant/tool-result pair just to meet a soft context cap.
    return list(messages[start:]) if start else list(messages)


def _pi_system_prompt(uid, cid, selection, skill_ids, runtime_system, summary, cfg):
    system_messages = [_agent_system(uid, cid)]
    if runtime_system:
        system_messages.append(runtime_system)
    skill_catalog = _agent_skill_catalog_system(uid, cid)
    if skill_catalog:
        system_messages.append(skill_catalog)
    skill_msg = _agent_skills_system(uid, cid, skill_ids)
    if skill_msg:
        system_messages.append(skill_msg)
        cfg["skill_instructions"] = skill_msg["content"]
        cfg["active_skill_ids"] = {
            skill_id for skill_id in skill_ids if isinstance(skill_id, int) and not isinstance(skill_id, bool)
        }
    selection_msg = _agent_selection_system(uid, cid, selection)
    if selection_msg:
        system_messages.append(selection_msg)
    if summary:
        system_messages.append({"role": "system", "content": "[此前对话摘要]\n" + summary})
    return "\n\n".join(message["content"] for message in system_messages if message.get("content"))


def _pi_tools():
    return [{
        "name": item["function"]["name"],
        "label": item["function"]["name"],
        "description": item["function"]["description"],
        "parameters": item["function"]["parameters"],
    } for item in AGENT_TOOLS]


def _run_pi_agent(uid, cid, history_text, selection=None, skill_ids=None, model_turn=None,
                  runtime_system=None, persist=True):
    """Run the writing agent through the official Pi Coding Agent process."""
    audio = None
    if model_turn is not None:
        content = model_turn.get("content") if isinstance(model_turn, dict) else None
        if not isinstance(content, list):
            raise HTTPException(400, "语音请求格式无效")
        instruction = ""
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                instruction += part["text"]
            if part.get("type") == "input_audio":
                raw_audio = part.get("input_audio") or {}
                data, format_ = raw_audio.get("data"), raw_audio.get("format")
                if isinstance(data, str) and isinstance(format_, str):
                    audio = {"data": data, "format": format_, "instruction": instruction}
        if not audio:
            raise HTTPException(400, "语音请求缺少可用音频")
    st = db.get_settings(uid) or {}
    base_url = st.get("llm_base_url") or config.LLM_BASE_URL
    api_key = st.get("llm_api_key") or config.LLM_API_KEY
    model = st.get("llm_model") or config.LLM_MODEL
    if not api_key:
        raise HTTPException(500, "未配置 API Key，请在设置里填写 base_url / key / 模型")
    cfg = {
        "base_url": base_url, "api_key": api_key, "model": model, "active_skill_ids": set(),
        "character_state_dirty": False,
    }
    conv = db.get_conversation(uid, cid) or {"messages": [], "summary": ""}
    summary = conv["summary"] or ""
    history = _legacy_messages_to_pi(conv["messages"], model)
    system_prompt = _pi_system_prompt(uid, cid, selection, skill_ids, runtime_system, summary, cfg)

    def execute_tool(name, args):
        fn = _AGENT_TOOLS.get(name)
        try:
            result = fn(uid, cid, cfg, args) if fn else {"error": f"未知工具 {name}"}
        except Exception as exc:
            result = {"error": f"工具执行出错：{exc}"}
        if not isinstance(result, dict):
            return {"summary": str(result)}
        result = dict(result)
        additions = []
        skill_system = result.pop("_skill_system", None)
        activated_skill_id = result.pop("_skill_id", None)
        resource_system = result.pop("_skill_resource_system", None)
        result.pop("_resource_truncated", None)
        if result.pop("_character_state_dirty", False):
            cfg["character_state_dirty"] = True
        if skill_system:
            additions.append(skill_system)
            cfg["skill_instructions"] = ((cfg.get("skill_instructions") or "") + "\n\n" + skill_system).strip()
            if isinstance(activated_skill_id, int):
                cfg["active_skill_ids"].add(activated_skill_id)
        if resource_system:
            additions.append(resource_system)
            combined = ((cfg.get("skill_instructions") or "") + "\n\n" + resource_system).strip()
            if len(combined) <= 18000:
                cfg["skill_instructions"] = combined
        if additions:
            result["_pi_system"] = "\n\n".join(additions)
        return result

    request = {
        "systemPrompt": system_prompt,
        "messages": _pi_recent_history(history),
        "prompt": history_text,
        "tools": _pi_tools(),
        "baseUrl": base_url,
        "apiKey": api_key,
        "model": model,
        "audio": audio,
        "sessionId": f"{config.AGENT_SKILL_AGENT_ID}:user-{uid}:chapter-{cid if cid is not None else 'global'}",
        "cwd": config.PI_AGENT_WORKSPACE_DIR,
        "skillDirs": [config.PI_AGENT_SKILL_DIR] if config.PI_AGENT_SKILL_DIR else [],
    }
    raw_messages = pi_agent.run_turn(request, execute_tool)
    reply = ""
    for message in reversed(raw_messages):
        if isinstance(message, dict) and message.get("role") == "assistant":
            reply = _pi_text(message.get("content")).strip()
            if reply:
                break
    if not reply:
        reply = "已完成本次操作。"

    compacted = False
    keep_from = _compact_split(raw_messages, config.AGENT_PRESERVE_RECENT)
    if keep_from > 0 and sum(_pi_message_size(message) for message in raw_messages) > config.AGENT_COMPACT_CHARS:
        try:
            summary = llm.summarize(_pi_messages_for_frontend(raw_messages[:keep_from]), prev=summary,
                                    base_url=base_url, api_key=api_key, model=model)
            raw_messages = raw_messages[keep_from:]
            compacted = True
        except Exception:
            pass

    display_messages = _pi_messages_for_frontend(raw_messages)
    state_request = (
        {"chapter_id": cid, "base_url": base_url, "api_key": api_key, "model": model}
        if cid and cfg.get("character_state_dirty") else None
    )
    if persist:
        db.save_conversation(uid, cid, raw_messages, summary)
        result = {"reply": reply, "messages": display_messages, "compacted": compacted}
        if state_request:
            result["_character_state_request"] = state_request
        return result
    return {
        "reply": reply, "messages": display_messages, "compacted": compacted,
        "_pending_conversation": {"messages": raw_messages, "summary": summary},
        "_character_state_request": state_request,
    }


def _run_legacy_agent(uid, cid, history_text, selection=None, skill_ids=None, model_turn=None,
               runtime_system=None, persist=True):
    """执行一轮 agent。

    history_text 是持久化到 SQLite 的安全文本；model_turn 可在本轮替换成音频等
    多模态内容，避免把 Base64 音频塞进后续每一轮对话上下文。
    """
    st = db.get_settings(uid) or {}
    base_url = st.get("llm_base_url") or config.LLM_BASE_URL
    api_key = st.get("llm_api_key") or config.LLM_API_KEY
    model = st.get("llm_model") or config.LLM_MODEL
    if not api_key:
        raise HTTPException(500, "未配置 API Key，请在「设置」里填 base_url / key / 模型")
    cfg = {
        "base_url": base_url, "api_key": api_key, "model": model, "active_skill_ids": set(),
        "character_state_dirty": False,
    }

    # 加载持久化对话（服务端权威）。音频只进入本轮模型消息，不保存原始内容。
    conv = db.get_conversation(uid, cid) or {"messages": [], "summary": ""}
    msgs = list(conv["messages"])
    summary = conv["summary"] or ""
    stored_turn = {"role": "user", "content": history_text}

    # 发给模型的数组：系统提示 + 早期摘要(若有) + 当前对话
    messages = [_agent_system(uid, cid)]
    if runtime_system:
        messages.append(runtime_system)
    skill_catalog = _agent_skill_catalog_system(uid, cid)
    if skill_catalog:
        messages.append(skill_catalog)
    skill_msg = _agent_skills_system(uid, cid, skill_ids)
    if skill_msg:
        messages.append(skill_msg)
        # 工具内会再次调用文本模型生成正文，必须带上同一组 Skill 规则。
        cfg["skill_instructions"] = skill_msg["content"]
        cfg["active_skill_ids"] = set(skill_id for skill_id in skill_ids if isinstance(skill_id, int) and not isinstance(skill_id, bool))
    selection_msg = _agent_selection_system(uid, cid, selection)
    if selection_msg:
        messages.append(selection_msg)
    if summary:
        messages.append({"role": "user", "content": "[此前对话摘要]\n" + summary})
    messages.extend(msgs)
    messages.append(model_turn or stored_turn)
    msgs.append(stored_turn)

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
            # activate_skill 的完整规则只留在当前模型上下文，不写进历史或工具记录。
            skill_system = result.pop("_skill_system", None) if isinstance(result, dict) else None
            activated_skill_id = result.pop("_skill_id", None) if isinstance(result, dict) else None
            resource_system = result.pop("_skill_resource_system", None) if isinstance(result, dict) else None
            result.pop("_resource_truncated", None) if isinstance(result, dict) else None
            if isinstance(result, dict) and result.pop("_character_state_dirty", False):
                cfg["character_state_dirty"] = True
            tm = {"role": "tool", "tool_call_id": tc.id,
                  "content": json.dumps(result, ensure_ascii=False)}
            messages.append(tm)
            msgs.append(tm)
            if skill_system:
                messages.append({"role": "system", "content": skill_system})
                cfg["skill_instructions"] = ((cfg.get("skill_instructions") or "") + "\n\n" + skill_system).strip()
                cfg["active_skill_ids"].add(activated_skill_id)
            if resource_system:
                messages.append({"role": "system", "content": resource_system})
                combined = ((cfg.get("skill_instructions") or "") + "\n\n" + resource_system).strip()
                if len(combined) <= 18000:
                    cfg["skill_instructions"] = combined
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

    state_request = (
        {"chapter_id": cid, "base_url": base_url, "api_key": api_key, "model": model}
        if cid and cfg.get("character_state_dirty") else None
    )
    if persist:
        db.save_conversation(uid, cid, msgs, summary)
        result = {"reply": reply, "messages": msgs, "compacted": compacted}
        if state_request:
            result["_character_state_request"] = state_request
        return result
    return {
        "reply": reply, "messages": msgs, "compacted": compacted,
        "_pending_conversation": {"messages": msgs, "summary": summary},
        "_character_state_request": state_request,
    }


def _runtime_request_payload(uid, cid, history_text, selection):
    """写入本机 launcher 的请求文件；音频 Base64 永远不落盘。"""
    return {
        "user_id": uid,
        "chapter_id": cid,
        "input": history_text,
        "selection": selection if isinstance(selection, dict) else None,
    }


def _run_agent_turn(uid, cid, history_text, selection=None, skill_ids=None, model_turn=None):
    """执行 Agent，并在启用本机运行时时先缓冲回答、等 after/recovery 确认。"""
    turn = None
    try:
        turn = skill_runtime.start_turn(_runtime_request_payload(uid, cid, history_text, selection))
        engine = _run_pi_agent if config.PI_AGENT_ENABLED else _run_legacy_agent
        result = engine(
            uid, cid, history_text, selection, skill_ids, model_turn=model_turn,
            runtime_system=turn.system_message if turn else None,
            persist=turn is None,
        )
    except skill_runtime.SkillRuntimeError as e:
        raise HTTPException(502, {"message": str(e), "turn_id": e.turn_id})
    except pi_agent.PiAgentError as e:
        if turn:
            turn.fail_before_answer(_provider_error(e))
        raise HTTPException(502, f"Pi Coding Agent 执行失败：{_provider_error(e)}")
    except Exception as e:
        if turn:
            turn.fail_before_answer(_provider_error(e))
        raise

    # 人物状态提取要等主回复/本机回合已确认后再落库，避免失败回合留下幽灵状态。
    state_request = result.pop("_character_state_request", None)
    if not turn:
        if state_request:
            result["character_state_proposals"] = _generate_character_state_proposals(
                uid, state_request["chapter_id"], base_url=state_request["base_url"],
                api_key=state_request["api_key"], model=state_request["model"],
            )
        return result

    pending = result.pop("_pending_conversation")
    try:
        runtime_state = turn.complete({
            "reply": result["reply"], "messages": result["messages"],
            "compacted": result["compacted"], "chapter_id": cid, "pending_conversation": pending,
        })
    except skill_runtime.SkillRuntimeError as e:
        raise HTTPException(502, {"message": str(e), "turn_id": e.turn_id})
    db.save_conversation(uid, cid, pending["messages"], pending["summary"])
    skill_runtime.mark_conversation_saved(turn.turn_id, uid)
    result["skill_runtime"] = runtime_state
    if state_request:
        result["character_state_proposals"] = _generate_character_state_proposals(
            uid, state_request["chapter_id"], base_url=state_request["base_url"],
            api_key=state_request["api_key"], model=state_request["model"],
        )
    return result


@app.post("/api/agent")
async def agent(request: Request):
    """文字指令进入 agent。"""
    uid = _auth(request)
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "没有对话内容")
    return _run_agent_turn(uid, body.get("chapter_id"), text, body.get("selection"), body.get("skill_ids"))


@app.post("/api/agent/audio")
async def agent_audio(request: Request):
    """语音直发模式：把 WAV/MP3 作为多模态用户消息交给当前 Agent 模型。"""
    uid = _auth(request)
    body = await request.json()
    audio_b64 = body.get("audio")
    audio_format = (body.get("format") or "").lower().strip()
    if not isinstance(audio_b64, str) or not audio_b64:
        raise HTTPException(400, "没有收到录音")
    if audio_format not in {"wav", "mp3"}:
        raise HTTPException(400, "语音直发仅接受 WAV 或 MP3 录音")
    try:
        audio = base64.b64decode(audio_b64, validate=True)
    except Exception:
        raise HTTPException(400, "录音数据无效")
    if not audio:
        raise HTTPException(400, "没有收到录音")
    if len(audio) > 5 * 1024 * 1024:
        raise HTTPException(413, "录音太长，请控制在一分钟内")

    # OpenAI Chat Completions 的音频内容格式；不兼容的网关会返回明确的直发失败提示。
    model_turn = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "以下是一条作者的语音指令。请直接理解音频内容并按要求执行；"
                        "若语义不清楚，简短追问。不要要求用户先转写。",
            },
            {
                "type": "input_audio",
                "input_audio": {"data": audio_b64, "format": audio_format},
            },
        ],
    }
    try:
        return _run_agent_turn(uid, body.get("chapter_id"), "[voice] 语音指令",
                               body.get("selection"), body.get("skill_ids"), model_turn=model_turn)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, "语音已直接发送给模型，但当前模型或网关不支持音频输入/工具调用。"
                            "可关闭「直接发送语音给 AI」后改用转写模式。"
                            f" 详情：{_provider_error(e)}")


@app.post("/api/agent/runtime/recover/{turn_id}")
async def recover_agent_runtime(turn_id: str, request: Request):
    """重放一个未确认的本机 launcher 回合，不重新调用模型。"""
    uid = _auth(request)
    try:
        answer, runtime_state = skill_runtime.recover_turn(turn_id, uid)
    except skill_runtime.SkillRuntimeError as e:
        raise HTTPException(502, {"message": str(e), "turn_id": e.turn_id})
    if not isinstance(answer, dict):
        raise HTTPException(409, "该回合没有可恢复的模型回答")
    pending = answer.get("pending_conversation")
    if not isinstance(pending, dict) or not isinstance(pending.get("messages"), list):
        raise HTTPException(409, "该回合没有可保存的对话状态")
    chapter_id = answer.get("chapter_id")
    if chapter_id is not None and (not isinstance(chapter_id, int) or isinstance(chapter_id, bool)):
        raise HTTPException(409, "该回合的章节标识无效")
    if not runtime_state.get("conversation_saved"):
        db.save_conversation(uid, chapter_id, pending["messages"], pending.get("summary") or "")
        skill_runtime.mark_conversation_saved(turn_id, uid)
        runtime_state["conversation_saved"] = True
    return {
        "reply": answer.get("reply") or "", "messages": _pi_messages_for_frontend(pending["messages"]),
        "compacted": bool(answer.get("compacted")), "skill_runtime": runtime_state,
    }


@app.get("/api/agent/conversation")
async def get_agent_conversation(request: Request):
    """取当前 用户×章节 的持久化对话（切章/刷新后恢复上下文用）。"""
    uid = _auth(request)
    cid = _qparam_int(request, "chapter_id")
    conv = db.get_conversation(uid, cid) or {"messages": [], "summary": ""}
    return {"messages": _pi_messages_for_frontend(conv["messages"]), "summary": conv["summary"]}


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
    """用户列表 + 占用统计（作品/章节/对话数/对话字节数），便于管理员清理。"""
    _admin_auth(request)
    return {"users": db.admin_user_stats()}


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


@app.delete("/api/admin/users/{target_uid}")
async def admin_delete_user(request: Request, target_uid: int):
    """彻底删除一个用户账号及其全部数据。
    不允许删除管理员账号、不允许管理员删除自己（避免误锁后台）。"""
    me = _admin_auth(request)
    if target_uid == me:
        raise HTTPException(400, "不能删除自己")
    if db.is_admin(target_uid):
        raise HTTPException(400, "不能删除管理员账号")
    if not db.admin_delete_user(target_uid):
        raise HTTPException(404, "用户不存在")
    return {"ok": True}


# ---------- 静态前端（放最后，避免盖住 /api） ----------

app.mount("/", StaticFiles(directory="static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=config.PORT, reload=False)
