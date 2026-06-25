"""SQLite 数据访问。用户 → 作品 → 章节 → 段落历史 / 修订版本。"""
import sqlite3
import os
import time
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
            """
        )
        _add_col(conn, "chapters", "notes", "TEXT DEFAULT ''")
        _add_col(conn, "works", "user_id", "INTEGER DEFAULT 0")


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
            "SELECT * FROM works WHERE user_id=? ORDER BY updated_at DESC", (user_id,)
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
        conn.execute("DELETE FROM chapters WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM works WHERE id=?", (wid,))
        return True


# ---------- 章节 ----------

def list_chapters(wid, user_id):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        return [dict(r) for r in conn.execute(
            "SELECT id, work_id, title, ord, created_at, length(content) AS chars "
            "FROM chapters WHERE work_id=? ORDER BY ord", (wid,)
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
        row = conn.execute("SELECT * FROM chapters WHERE id=?", (cid,)).fetchone()
        if not row:
            return None
        chap = dict(row)
        segs = conn.execute(
            "SELECT id, raw, result, mode, created_at FROM segments "
            "WHERE chapter_id=? ORDER BY id", (cid,)
        ).fetchall()
        chap["segments"] = [dict(s) for s in segs]
        return chap


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


def delete_chapter(cid, user_id):
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return False
        conn.execute("DELETE FROM segments WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM chapter_revisions WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM chapters WHERE id=?", (cid,))
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
