"""SQLite 数据访问。用户 → 作品 → 章节 → 段落历史 / 修订版本。"""
import sqlite3
import os
import time
import json
import secrets
import hashlib
from contextlib import contextmanager

import config

DB_PATH = config.DB_PATH


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _add_col(conn, table, col, decl):
    """幂等加列，兼容旧库升级。"""
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def init_db():
    parent = os.path.dirname(DB_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                salt TEXT NOT NULL,
                hash TEXT NOT NULL,
                created_at REAL
            );
            CREATE TABLE IF NOT EXISTS works (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                created_at REAL,
                updated_at REAL
            );
            CREATE TABLE IF NOT EXISTS chapters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                ord INTEGER NOT NULL,
                content TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                created_at REAL,
                updated_at REAL,
                FOREIGN KEY(work_id) REFERENCES works(id)
            );
            CREATE TABLE IF NOT EXISTS segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter_id INTEGER NOT NULL,
                raw TEXT,
                result TEXT,
                mode TEXT,
                created_at REAL,
                FOREIGN KEY(chapter_id) REFERENCES chapters(id)
            );
            CREATE TABLE IF NOT EXISTS chapter_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter_id INTEGER NOT NULL,
                title TEXT,
                content TEXT,
                created_at REAL,
                FOREIGN KEY(chapter_id) REFERENCES chapters(id)
            );
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                llm_base_url TEXT,
                llm_api_key TEXT,
                llm_model TEXT,
                updated_at REAL
            );
            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                summary TEXT DEFAULT '',
                detail TEXT DEFAULT '',
                created_at REAL,
                updated_at REAL,
                FOREIGN KEY(work_id) REFERENCES works(id)
            );
            CREATE TABLE IF NOT EXISTS agent_conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chapter_id INTEGER,      -- NULL 表示未选章节时的通用对话
                messages TEXT DEFAULT '[]',  -- 非系统对话消息的 JSON 数组
                summary TEXT DEFAULT '',     -- 已压缩掉的早期对话的滚动摘要
                msg_count INTEGER DEFAULT 0,
                created_at REAL,
                updated_at REAL,
                UNIQUE(user_id, chapter_id)  -- 一个用户一个章节一行
            );
            CREATE INDEX IF NOT EXISTS idx_works_user ON works(user_id, updated_at);
            CREATE INDEX IF NOT EXISTS idx_chapters_work ON chapters(work_id, ord);
            CREATE INDEX IF NOT EXISTS idx_segments_chapter ON segments(chapter_id);
            CREATE INDEX IF NOT EXISTS idx_revisions_chapter ON chapter_revisions(chapter_id);
            CREATE INDEX IF NOT EXISTS idx_entities_work ON entities(work_id);
            CREATE INDEX IF NOT EXISTS idx_conv_user ON agent_conversations(user_id, updated_at);
            """
        )
        _add_col(conn, "chapters", "notes", "TEXT DEFAULT ''")
        _add_col(conn, "chapters", "deleted_at", "REAL")  # 软删时间戳；NULL=正常在册
        _add_col(conn, "works", "user_id", "INTEGER DEFAULT 0")
        _add_col(conn, "works", "notes", "TEXT DEFAULT ''")  # 作品设定(人物/世界观/大纲)
        _add_col(conn, "users", "is_admin", "INTEGER DEFAULT 0")  # 后台管理员标记
        _bootstrap_admin(conn)


def _bootstrap_admin(conn):
    """首次启动若无任何管理员，按 config.ADMIN_USER 引导创建一个 is_admin=1 账户。
    密码用 config.ADMIN_PASSWORD；为空则随机生成并打印到日志，请尽快用 env 固定。"""
    if conn.execute("SELECT 1 FROM users WHERE is_admin=1 LIMIT 1").fetchone():
        return
    name = (config.ADMIN_USER or "").strip()
    if not name:
        return
    pwd = config.ADMIN_PASSWORD or ""
    generated = False
    if not pwd:
        pwd = secrets.token_urlsafe(9)
        generated = True
    now = time.time()
    salt = secrets.token_bytes(16)
    h = _hash_pw(pwd, salt)
    existing = conn.execute("SELECT id FROM users WHERE username=?", (name,)).fetchone()
    if existing:
        # 同名用户已存在（非管理员）：提升为管理员，不改其密码
        conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (existing["id"],))
        print(f"[writehtml] 已将已有用户 {name!r} 提升为管理员。", flush=True)
        return
    try:
        conn.execute(
            "INSERT INTO users(username, salt, hash, is_admin, created_at) VALUES(?,?,?,?,?)",
            (name, salt.hex(), h, 1, now),
        )
    except sqlite3.IntegrityError:
        return
    if generated:
        print(f"[writehtml] 已创建管理员账户 用户名={name!r} 初始密码={pwd!r}（请尽快登录后在 .env 用 WRITEHTML_ADMIN_PASSWORD 固定强密码）", flush=True)
    else:
        print(f"[writehtml] 已创建管理员账户 用户名={name!r}（密码来自 WRITEHTML_ADMIN_PASSWORD）", flush=True)


# ---------- 用户 / 鉴权 ----------

def _hash_pw(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120000).hex()


def create_user(username, password):
    salt = secrets.token_bytes(16)
    h = _hash_pw(password, salt)
    now = time.time()
    with get_conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users(username, salt, hash, created_at) VALUES(?,?,?,?)",
                (username, salt.hex(), h, now),
            )
        except sqlite3.IntegrityError:
            return None
        return {"id": cur.lastrowid, "username": username}


def verify_user(username, password):
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not r:
        return None
    salt = bytes.fromhex(r["salt"])
    if not secrets.compare_digest(_hash_pw(password, salt), r["hash"]):
        return None
    return {"id": r["id"], "username": r["username"]}


def get_username(user_id):
    with get_conn() as conn:
        r = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
        return r["username"] if r else ""


def is_admin(user_id):
    with get_conn() as conn:
        r = conn.execute("SELECT is_admin FROM users WHERE id=?", (user_id,)).fetchone()
        return bool(r and r["is_admin"])


# ---------- agent 对话持久化（按 用户 × 章节 存一行） ----------

def get_conversation(user_id, chapter_id):
    with get_conn() as conn:
        if chapter_id is None:
            r = conn.execute(
                "SELECT messages, summary FROM agent_conversations WHERE user_id=? AND chapter_id IS NULL",
                (user_id,)).fetchone()
        else:
            r = conn.execute(
                "SELECT messages, summary FROM agent_conversations WHERE user_id=? AND chapter_id=?",
                (user_id, chapter_id)).fetchone()
        if not r:
            return None
        try:
            msgs = json.loads(r["messages"] or "[]")
        except Exception:
            msgs = []
        if not isinstance(msgs, list):
            msgs = []
        return {"messages": msgs, "summary": r["summary"] or ""}


def save_conversation(user_id, chapter_id, messages, summary):
    """upsert 一条对话。chapter_id 为 None 时走应用层查重（SQLite NULL 不唯一）。"""
    now = time.time()
    msgs_json = json.dumps(messages, ensure_ascii=False)
    cnt = len(messages)
    with get_conn() as conn:
        if chapter_id is None:
            row = conn.execute(
                "SELECT id FROM agent_conversations WHERE user_id=? AND chapter_id IS NULL",
                (user_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM agent_conversations WHERE user_id=? AND chapter_id=?",
                (user_id, chapter_id)).fetchone()
        if row:
            conn.execute(
                "UPDATE agent_conversations SET messages=?, summary=?, msg_count=?, updated_at=? WHERE id=?",
                (msgs_json, summary, cnt, now, row["id"]))
        else:
            conn.execute(
                "INSERT INTO agent_conversations(user_id, chapter_id, messages, summary, msg_count, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (user_id, chapter_id, msgs_json, summary, cnt, now, now))
        return True


def delete_conversation(user_id, chapter_id):
    with get_conn() as conn:
        if chapter_id is None:
            cur = conn.execute(
                "DELETE FROM agent_conversations WHERE user_id=? AND chapter_id IS NULL",
                (user_id,))
        else:
            cur = conn.execute(
                "DELETE FROM agent_conversations WHERE user_id=? AND chapter_id=?",
                (user_id, chapter_id))
        return cur.rowcount > 0


# ---------- 后台管理（admin）查询 ----------

def list_users_admin():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, username, is_admin, created_at FROM users ORDER BY id")]


def list_conversations_admin():
    """列出所有用户的对话，带用户名与章节标题（便于 admin 辨识后删除）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ac.id, ac.user_id, u.username, ac.chapter_id, "
            "c.title AS chapter_title, ac.msg_count, "
            "CASE WHEN ac.summary!='' THEN 1 ELSE 0 END AS has_summary, "
            "ac.created_at, ac.updated_at "
            "FROM agent_conversations ac "
            "LEFT JOIN users u ON u.id=ac.user_id "
            "LEFT JOIN chapters c ON c.id=ac.chapter_id "
            "ORDER BY ac.updated_at DESC")
        return [dict(r) for r in rows]


def admin_delete_conversation(conv_id):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM agent_conversations WHERE id=?", (conv_id,))
        return cur.rowcount > 0


def admin_clear_user_conversations(user_id):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM agent_conversations WHERE user_id=?", (user_id,))
        return cur.rowcount


# ---------- 每个用户自己的大模型设置 ----------

def get_settings(user_id):
    """返回该用户的 LLM 设置；没存过返回 None（调用方用 .env 兜底）。"""
    with get_conn() as conn:
        r = conn.execute(
            "SELECT llm_base_url, llm_api_key, llm_model FROM user_settings WHERE user_id=?",
            (user_id,),
        ).fetchone()
        return dict(r) if r else None


def save_settings(user_id, base_url, api_key, model):
    """保存设置。api_key 为空或为掩码占位时保留旧值，避免清空已填的 key。"""
    now = time.time()
    with get_conn() as conn:
        old = conn.execute(
            "SELECT llm_api_key FROM user_settings WHERE user_id=?", (user_id,)
        ).fetchone()
        if not api_key or api_key.startswith("****"):
            api_key = old["llm_api_key"] if old else ""
        conn.execute(
            "INSERT INTO user_settings(user_id, llm_base_url, llm_api_key, llm_model, updated_at) "
            "VALUES(?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "llm_base_url=excluded.llm_base_url, llm_api_key=excluded.llm_api_key, "
            "llm_model=excluded.llm_model, updated_at=excluded.updated_at",
            (user_id, base_url, api_key, model, now),
        )
        return True


# ---------- 归属校验 ----------

def _work_owned(conn, wid, user_id):
    return conn.execute(
        "SELECT 1 FROM works WHERE id=? AND user_id=?", (wid, user_id)
    ).fetchone() is not None


def _chapter_owned(conn, cid, user_id):
    r = conn.execute(
        "SELECT w.user_id FROM chapters c JOIN works w ON c.work_id=w.id WHERE c.id=?",
        (cid,),
    ).fetchone()
    return r is not None and r["user_id"] == user_id


# ---------- 作品 ----------

def list_works(user_id):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, user_id, title, created_at, updated_at FROM works WHERE user_id=? ORDER BY updated_at DESC", (user_id,)
        )]


def create_work(user_id, title):
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO works(user_id, title, created_at, updated_at) VALUES(?,?,?,?)",
            (user_id, title, now, now),
        )
        return {"id": cur.lastrowid, "title": title}


def delete_work(wid, user_id):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return False
        cids = [r["id"] for r in conn.execute(
            "SELECT id FROM chapters WHERE work_id=?", (wid,)
        )]
        for cid in cids:
            conn.execute("DELETE FROM segments WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM chapter_revisions WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM agent_conversations WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM chapters WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM entities WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM works WHERE id=?", (wid,))
        return True


def get_work(wid, user_id):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        r = conn.execute("SELECT * FROM works WHERE id=?", (wid,)).fetchone()
        return dict(r) if r else None


def list_chapters_full(wid, user_id):
    """带正文的章节列表，按 ord 排序，用于整本导出。"""
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        return [dict(r) for r in conn.execute(
            "SELECT id, title, ord, content FROM chapters WHERE work_id=? AND deleted_at IS NULL ORDER BY ord", (wid,)
        )]


def get_work_notes(wid, user_id):
    """作品设定（人物/世界观/大纲），喂给 AI 当全文记忆。"""
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        r = conn.execute("SELECT notes FROM works WHERE id=?", (wid,)).fetchone()
        return r["notes"] if r else None


def update_work_notes(wid, user_id, notes):
    now = time.time()
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return False
        conn.execute("UPDATE works SET notes=?, updated_at=? WHERE id=?", (notes, now, wid))
        return True


# ---------- 实体卡片（作品级 wiki）----------

def list_entities(wid, user_id):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        return [dict(r) for r in conn.execute(
            "SELECT id, name, kind, summary, detail, created_at, updated_at "
            "FROM entities WHERE work_id=? ORDER BY kind, id", (wid,)
        )]


def create_entity(wid, user_id, name, kind, summary, detail):
    now = time.time()
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        cur = conn.execute(
            "INSERT INTO entities(work_id,name,kind,summary,detail,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (wid, name, kind, summary or "", detail or "", now, now),
        )
        return {"id": cur.lastrowid, "work_id": wid, "name": name, "kind": kind,
                "summary": summary or "", "detail": detail or ""}


def _entity_owned(conn, eid, user_id):
    r = conn.execute(
        "SELECT w.user_id FROM entities e JOIN works w ON e.work_id=w.id WHERE e.id=?",
        (eid,),
    ).fetchone()
    return r is not None and r["user_id"] == user_id


def update_entity(eid, user_id, name, kind, summary, detail):
    now = time.time()
    with get_conn() as conn:
        if not _entity_owned(conn, eid, user_id):
            return False
        conn.execute(
            "UPDATE entities SET name=COALESCE(?,name), kind=COALESCE(?,kind), "
            "summary=COALESCE(?,summary), detail=COALESCE(?,detail), updated_at=? WHERE id=?",
            (name, kind, summary, detail, now, eid),
        )
        return True


def delete_entity(eid, user_id):
    with get_conn() as conn:
        if not _entity_owned(conn, eid, user_id):
            return False
        conn.execute("DELETE FROM entities WHERE id=?", (eid,))
        return True


def get_entity_digest(wid, user_id):
    """把作品实体格式化成一行一条的摘要，拼进 bible 喂给 AI 当结构化设定。"""
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return ""
        rows = conn.execute(
            "SELECT name, kind, summary FROM entities WHERE work_id=? ORDER BY kind, id",
            (wid,)
        ).fetchall()
    if not rows:
        return ""
    return "作品实体（写作时保持一致）：\n" + "\n".join(
        f"[{r['kind']}] {r['name']}" + (f"：{r['summary']}" if r['summary'] else "") for r in rows
    )


# ---------- 章节 ----------

def list_chapters(wid, user_id):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        return [dict(r) for r in conn.execute(
            "SELECT id, work_id, title, ord, created_at, length(content) AS chars "
            "FROM chapters WHERE work_id=? AND deleted_at IS NULL ORDER BY ord", (wid,)
        )]


def create_chapter(wid, user_id, title):
    now = time.time()
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        ord_ = conn.execute(
            "SELECT COALESCE(MAX(ord),0)+1 FROM chapters WHERE work_id=?", (wid,)
        ).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO chapters(work_id,title,ord,content,notes,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (wid, title, ord_, "", "", now, now),
        )
        return {"id": cur.lastrowid, "work_id": wid, "title": title, "ord": ord_}


def reorder_chapters(wid, user_id, ids):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return False
        for i, cid in enumerate(ids):
            conn.execute(
                "UPDATE chapters SET ord=? WHERE id=? AND work_id=?",
                (i + 1, cid, wid),
            )
        return True


def get_chapter(cid, user_id):
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        row = conn.execute("SELECT * FROM chapters WHERE id=? AND deleted_at IS NULL", (cid,)).fetchone()
        if not row:
            return None
        chap = dict(row)
        segs = conn.execute(
            "SELECT id, raw, result, mode, created_at FROM segments "
            "WHERE chapter_id=? ORDER BY id", (cid,)
        ).fetchall()
        chap["segments"] = [dict(s) for s in segs]
        return chap


def get_chapter_meta(cid, user_id):
    """轻量取章节元数据（title/content/notes/work_id），不拉段落历史。
    do_process / chat 等只需元数据与归属校验的热路径用这个，避免随段落增长放大开销。"""
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        r = conn.execute(
            "SELECT id, work_id, title, content, notes FROM chapters WHERE id=? AND deleted_at IS NULL",
            (cid,),
        ).fetchone()
        return dict(r) if r else None


def update_chapter(cid, user_id, title, content, notes):
    now = time.time()
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return False
        conn.execute(
            "UPDATE chapters SET title=COALESCE(?,title), content=COALESCE(?,content), "
            "notes=COALESCE(?,notes), updated_at=? WHERE id=?",
            (title, content, notes, now, cid),
        )
        conn.execute(
            "UPDATE works SET updated_at=? WHERE id="
            "(SELECT work_id FROM chapters WHERE id=?)",
            (now, cid),
        )
        return True


def replace_text_in_chapter(cid, user_id, old, new):
    """在正文里定位 old 的第一处出现并替换为 new，整章回写。
    供 AI agent 的 replace_text 工具用——现在只有整章覆盖/末尾追加，缺"定位替换"。
    找不到 old 返回 None（让上层提示 AI 重新读取正文再试）。"""
    if not old:
        return None
    now = time.time()
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        chap = conn.execute("SELECT content FROM chapters WHERE id=?", (cid,)).fetchone()
        if not chap:
            return None
        content = chap["content"] or ""
        if old not in content:
            return None
        content = content.replace(old, new, 1)
        conn.execute("UPDATE chapters SET content=?, updated_at=? WHERE id=?", (content, now, cid))
        conn.execute(
            "UPDATE works SET updated_at=? WHERE id="
            "(SELECT work_id FROM chapters WHERE id=?)",
            (now, cid),
        )
        return content


def delete_chapter(cid, user_id):
    """软删（移入回收站），可恢复。"""
    now = time.time()
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return False
        conn.execute("UPDATE chapters SET deleted_at=? WHERE id=?", (now, cid))
        return True


def purge_chapter(cid, user_id):
    """彻底删除（从回收站清空），不可恢复。"""
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return False
        conn.execute("DELETE FROM segments WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM chapter_revisions WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM agent_conversations WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM chapters WHERE id=?", (cid,))
        return True


def list_trashed(wid, user_id):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        return [dict(r) for r in conn.execute(
            "SELECT id, title, ord, length(content) AS chars, deleted_at "
            "FROM chapters WHERE work_id=? AND deleted_at IS NOT NULL "
            "ORDER BY deleted_at DESC", (wid,)
        )]


def restore_chapter(cid, user_id):
    """从回收站恢复；放到章节列表末尾，避免 ord 冲突。"""
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return False
        new_ord = conn.execute(
            "SELECT COALESCE(MAX(ord),0)+1 FROM chapters WHERE work_id="
            "(SELECT work_id FROM chapters WHERE id=?)", (cid,)
        ).fetchone()[0]
        conn.execute("UPDATE chapters SET deleted_at=NULL, ord=? WHERE id=?", (new_ord, cid))
        return True


def split_chapter(cid, user_id, at, title):
    """在 at 处把当前章拆成两章：左半留在原章，右半进新建的下一章。"""
    now = time.time()
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        chap = conn.execute(
            "SELECT work_id, content, ord FROM chapters WHERE id=?", (cid,)
        ).fetchone()
        if not chap:
            return None
        content = chap["content"] or ""
        at = max(0, min(at, len(content)))
        left, right = content[:at], content[at:]
        conn.execute(
            "UPDATE chapters SET content=?, updated_at=? WHERE id=?", (left, now, cid)
        )
        conn.execute(
            "UPDATE chapters SET ord=ord+1 WHERE work_id=? AND ord>?",
            (chap["work_id"], chap["ord"]),
        )
        cur = conn.execute(
            "INSERT INTO chapters(work_id,title,ord,content,notes,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (chap["work_id"], title, chap["ord"] + 1, right, "", now, now),
        )
        return {"new_chapter_id": cur.lastrowid}


# ---------- 段落（语音/AI 落稿） ----------

def add_segment(cid, user_id, raw, result, mode):
    """记录一段历史，并把结果追加到章节正文。"""
    now = time.time()
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        chap = conn.execute("SELECT content FROM chapters WHERE id=?", (cid,)).fetchone()
        if not chap:
            return None
        content = chap["content"] or ""
        if content and not content.endswith("\n"):
            content += "\n"
        content += result
        conn.execute(
            "UPDATE chapters SET content=?, updated_at=? WHERE id=?",
            (content, now, cid),
        )
        cur = conn.execute(
            "INSERT INTO segments(chapter_id, raw, result, mode, created_at) VALUES(?,?,?,?,?)",
            (cid, raw, result, mode, now),
        )
        conn.execute(
            "UPDATE works SET updated_at=? WHERE id="
            "(SELECT work_id FROM chapters WHERE id=?)",
            (now, cid),
        )
        return {"segment_id": cur.lastrowid, "content": content}


def undo_last_segment(cid, user_id):
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        row = conn.execute(
            "SELECT id, result FROM segments WHERE chapter_id=? "
            "ORDER BY id DESC LIMIT 1", (cid,)
        ).fetchone()
        chap = conn.execute(
            "SELECT content FROM chapters WHERE id=?", (cid,)
        ).fetchone()
        content = (chap["content"] or "") if chap else ""
        if row and row["result"] and content.endswith(row["result"]):
            content = content[: -len(row["result"])].rstrip("\n")
        if row:
            conn.execute("DELETE FROM segments WHERE id=?", (row["id"],))
        conn.execute(
            "UPDATE chapters SET content=?, updated_at=? WHERE id=?",
            (content, time.time(), cid),
        )
    return get_chapter(cid, user_id)


# ---------- 修订版本 ----------

def add_revision(cid, user_id):
    now = time.time()
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        chap = conn.execute("SELECT title, content FROM chapters WHERE id=?", (cid,)).fetchone()
        if not chap:
            return None
        cur = conn.execute(
            "INSERT INTO chapter_revisions(chapter_id, title, content, created_at) VALUES(?,?,?,?)",
            (cid, chap["title"], chap["content"], now),
        )
        # 每章只保留最近 20 个版本，老的自动清掉，省盘
        conn.execute(
            "DELETE FROM chapter_revisions WHERE chapter_id=? AND id NOT IN "
            "(SELECT id FROM chapter_revisions WHERE chapter_id=? ORDER BY id DESC LIMIT 20)",
            (cid, cid),
        )
        return {"id": cur.lastrowid, "created_at": now}


def list_revisions(cid, user_id):
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        return [dict(r) for r in conn.execute(
            "SELECT id, title, length(content) AS chars, created_at "
            "FROM chapter_revisions WHERE chapter_id=? ORDER BY id DESC", (cid,)
        )]


def restore_revision(cid, user_id, rid):
    now = time.time()
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        rev = conn.execute(
            "SELECT title, content FROM chapter_revisions WHERE id=? AND chapter_id=?",
            (rid, cid),
        ).fetchone()
        if not rev:
            return None
        conn.execute(
            "UPDATE chapters SET title=?, content=?, updated_at=? WHERE id=?",
            (rev["title"], rev["content"], now, cid),
        )
    return get_chapter(cid, user_id)


def get_revision(cid, user_id, rid):
    """取单个历史版本的完整内容（供 AI 找回读取）。"""
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        r = conn.execute(
            "SELECT id, title, content, created_at FROM chapter_revisions WHERE id=? AND chapter_id=?",
            (rid, cid),
        ).fetchone()
        return dict(r) if r else None
