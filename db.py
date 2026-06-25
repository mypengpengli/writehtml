"""SQLite 数据访问。作品 → 章节 → 段落历史。"""
import sqlite3
import os
import time
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


def init_db():
    # 确保数据目录存在（Docker 挂卷时）
    parent = os.path.dirname(DB_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS works (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            """
        )


# ---------- 作品 ----------

def list_works():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM works ORDER BY updated_at DESC"
        )]


def create_work(title):
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO works(title, created_at, updated_at) VALUES(?,?,?)",
            (title, now, now),
        )
        wid = cur.lastrowid
    return {"id": wid, "title": title}


# ---------- 章节 ----------

def list_chapters(work_id):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, work_id, title, ord, created_at FROM chapters "
            "WHERE work_id=? ORDER BY ord", (work_id,)
        )]


def create_chapter(work_id, title):
    now = time.time()
    with get_conn() as conn:
        ord_ = conn.execute(
            "SELECT COALESCE(MAX(ord),0)+1 FROM chapters WHERE work_id=?",
            (work_id,),
        ).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO chapters(work_id,title,ord,content,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (work_id, title, ord_, "", now, now),
        )
        cid = cur.lastrowid
    return {"id": cid, "work_id": work_id, "title": title, "ord": ord_}


def get_chapter(cid):
    with get_conn() as conn:
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


def update_chapter(cid, title, content):
    now = time.time()
    with get_conn() as conn:
        conn.execute(
            "UPDATE chapters SET title=COALESCE(?,title), content=COALESCE(?,content), "
            "updated_at=? WHERE id=?",
            (title, content, now, cid),
        )
        if cid:
            conn.execute("UPDATE works SET updated_at=? WHERE id=(
                "SELECT work_id FROM chapters WHERE id=?", (cid,)
            ), (now,))
    return get_chapter(cid)


def add_segment(chapter_id, raw, result, mode):
    """记录一段历史，并把结果追加到章节正文。"""
    now = time.time()
    with get_conn() as conn:
        chap = conn.execute(
            "SELECT content FROM chapters WHERE id=?", (chapter_id,)
        ).fetchone()
        if not chap:
            return None
        content = chap["content"] or ""
        # 追加时保证前面有换行分隔
        if content and not content.endswith("\n"):
            content += "\n"
        content += result
        conn.execute(
            "UPDATE chapters SET content=?, updated_at=? WHERE id=?",
            (content, now, chapter_id),
        )
        cur = conn.execute(
            "INSERT INTO segments(chapter_id, raw, result, mode, created_at) "
            "VALUES(?,?,?,?,?)",
            (chapter_id, raw, result, mode, now),
        )
        sid = cur.lastrowid
        conn.execute(
            "UPDATE works SET updated_at=? WHERE id=(
                "SELECT work_id FROM chapters WHERE id=?", (chapter_id,)
            ), (now,))
    return {"segment_id": sid, "content": content}


def undo_last_segment(chapter_id):
    """撤销最近一段：删除历史记录，并从正文末尾去掉它的结果。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, result FROM segments WHERE chapter_id=? "
            "ORDER BY id DESC LIMIT 1", (chapter_id,)
        ).fetchone()
        if not row:
            return get_chapter(chapter_id)
        chap = conn.execute(
            "SELECT content FROM chapters WHERE id=?", (chapter_id,)
        ).fetchone()
        content = chap["content"] or ""
        # 仅当正文确实以该结果结尾时才裁掉，避免误删手动编辑
        if row["result"] and content.endswith(row["result"]):
            content = content[: -len(row["result"])].rstrip("\n")
        conn.execute(
            "UPDATE chapters SET content=?, updated_at=? WHERE id=?",
            (content, time.time(), chapter_id),
        )
        conn.execute("DELETE FROM segments WHERE id=?", (row["id"],))
    return get_chapter(chapter_id)
