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


# 人物的基础设定仍存放在 entities；下面这些字段只描述“截至某一章”的动态状态。
# 统一为短文本，既便于作者编辑，也避免把瞬时状态再拆成难以维护的多张表。
CHARACTER_STATE_FIELDS = (
    "location", "goal", "emotion", "physical", "information",
    "relationships", "assets", "secrets", "notes",
)
CHARACTER_STATE_LABELS = {
    "location": "地点", "goal": "目标", "emotion": "情绪", "physical": "身体",
    "information": "已知信息", "relationships": "关系", "assets": "能力/物品",
    "secrets": "秘密/承诺", "notes": "补充",
}

# 剧情状态与人物状态一样按章节生效，但描述的是故事整体而不是单个角色。
PLOT_STATE_FIELDS = (
    "mainline", "current_event", "timeline", "locations", "conflicts",
    "open_threads", "next_goal", "notes",
)
PLOT_STATE_LABELS = {
    "mainline": "主线进度", "current_event": "当前事件", "timeline": "时间线",
    "locations": "地点", "conflicts": "核心冲突", "open_threads": "未回收伏笔",
    "next_goal": "下一章目标", "notes": "补充",
}
WORKFLOW_STATUSES = ("planning", "drafting", "review", "final")


def normalize_character_state(state, base=None):
    """把 API/模型输入规整为可持久化的完整人物状态快照。"""
    state = state if isinstance(state, dict) else {}
    base = base if isinstance(base, dict) else {}
    result = {}
    for field in CHARACTER_STATE_FIELDS:
        value = state.get(field, base.get(field, ""))
        if value is None:
            value = ""
        elif isinstance(value, (list, tuple)):
            value = "；".join(str(x).strip() for x in value if str(x).strip())
        elif isinstance(value, dict):
            value = "；".join(f"{k}：{v}" for k, v in value.items() if str(v).strip())
        elif not isinstance(value, str):
            value = str(value)
        result[field] = value.strip()[:3000]
    return result


def _decode_character_state(raw):
    try:
        value = json.loads(raw or "{}")
    except Exception:
        value = {}
    return normalize_character_state(value)


def character_state_has_content(state):
    return any((state or {}).get(field) for field in CHARACTER_STATE_FIELDS)


def normalize_plot_state(state, base=None):
    """把剧情状态规整为完整快照，保留作者可编辑的短文本结构。"""
    state = state if isinstance(state, dict) else {}
    base = base if isinstance(base, dict) else {}
    result = {}
    for field in PLOT_STATE_FIELDS:
        value = state.get(field, base.get(field, ""))
        if value is None:
            value = ""
        elif isinstance(value, (list, tuple)):
            value = "；".join(str(x).strip() for x in value if str(x).strip())
        elif isinstance(value, dict):
            value = "；".join(f"{k}：{v}" for k, v in value.items() if str(v).strip())
        elif not isinstance(value, str):
            value = str(value)
        result[field] = value.strip()[:4000]
    return result


def _decode_plot_state(raw):
    try:
        value = json.loads(raw or "{}")
    except Exception:
        value = {}
    return normalize_plot_state(value)


def plot_state_has_content(state):
    return any((state or {}).get(field) for field in PLOT_STATE_FIELDS)


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
            CREATE TABLE IF NOT EXISTS entity_state_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id INTEGER NOT NULL,
                chapter_id INTEGER NOT NULL,
                state_json TEXT NOT NULL DEFAULT '{}',
                change_summary TEXT DEFAULT '',
                evidence TEXT DEFAULT '',
                source TEXT DEFAULT 'manual',
                proposal_id INTEGER,
                created_at REAL,
                FOREIGN KEY(entity_id) REFERENCES entities(id),
                FOREIGN KEY(chapter_id) REFERENCES chapters(id)
            );
            CREATE TABLE IF NOT EXISTS entity_state_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id INTEGER NOT NULL,
                chapter_id INTEGER NOT NULL,
                state_json TEXT NOT NULL DEFAULT '{}',
                change_summary TEXT DEFAULT '',
                evidence TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at REAL,
                updated_at REAL,
                resolved_at REAL,
                FOREIGN KEY(entity_id) REFERENCES entities(id),
                FOREIGN KEY(chapter_id) REFERENCES chapters(id)
            );
            CREATE TABLE IF NOT EXISTS plot_state_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_id INTEGER NOT NULL,
                chapter_id INTEGER NOT NULL,
                state_json TEXT NOT NULL DEFAULT '{}',
                change_summary TEXT DEFAULT '',
                evidence TEXT DEFAULT '',
                source TEXT DEFAULT 'manual',
                proposal_id INTEGER,
                created_at REAL,
                FOREIGN KEY(work_id) REFERENCES works(id),
                FOREIGN KEY(chapter_id) REFERENCES chapters(id)
            );
            CREATE TABLE IF NOT EXISTS plot_state_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_id INTEGER NOT NULL,
                chapter_id INTEGER NOT NULL,
                state_json TEXT NOT NULL DEFAULT '{}',
                change_summary TEXT DEFAULT '',
                evidence TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at REAL,
                updated_at REAL,
                resolved_at REAL,
                FOREIGN KEY(work_id) REFERENCES works(id),
                FOREIGN KEY(chapter_id) REFERENCES chapters(id)
            );
            CREATE TABLE IF NOT EXISTS entity_relations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_id INTEGER NOT NULL,
                from_entity_id INTEGER NOT NULL,
                to_entity_id INTEGER NOT NULL,
                relation TEXT NOT NULL,
                detail TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                created_at REAL,
                updated_at REAL,
                FOREIGN KEY(work_id) REFERENCES works(id),
                FOREIGN KEY(from_entity_id) REFERENCES entities(id),
                FOREIGN KEY(to_entity_id) REFERENCES entities(id)
            );
            CREATE TABLE IF NOT EXISTS chapter_consistency_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'notice',
                title TEXT NOT NULL,
                detail TEXT DEFAULT '',
                evidence TEXT DEFAULT '',
                suggestion TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                created_at REAL,
                updated_at REAL,
                resolved_at REAL,
                FOREIGN KEY(chapter_id) REFERENCES chapters(id)
            );
            CREATE TABLE IF NOT EXISTS work_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_id INTEGER NOT NULL,
                label TEXT DEFAULT '',
                snapshot_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL,
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
            CREATE TABLE IF NOT EXISTS agent_skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                work_id INTEGER,             -- NULL 表示可用于所有作品
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                instruction TEXT NOT NULL,
                source_kind TEXT DEFAULT 'manual',
                source_markdown TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1,
                created_at REAL,
                updated_at REAL,
                FOREIGN KEY(work_id) REFERENCES works(id)
            );
            CREATE TABLE IF NOT EXISTS agent_skill_resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_id INTEGER NOT NULL,
                path TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at REAL,
                UNIQUE(skill_id, path),
                FOREIGN KEY(skill_id) REFERENCES agent_skills(id)
            );
            CREATE INDEX IF NOT EXISTS idx_works_user ON works(user_id, updated_at);
            CREATE INDEX IF NOT EXISTS idx_chapters_work ON chapters(work_id, ord);
            CREATE INDEX IF NOT EXISTS idx_segments_chapter ON segments(chapter_id);
            CREATE INDEX IF NOT EXISTS idx_revisions_chapter ON chapter_revisions(chapter_id);
            CREATE INDEX IF NOT EXISTS idx_entities_work ON entities(work_id);
            CREATE INDEX IF NOT EXISTS idx_entity_state_versions_entity_chapter
                ON entity_state_versions(entity_id, chapter_id, id);
            CREATE INDEX IF NOT EXISTS idx_entity_state_proposals_chapter_status
                ON entity_state_proposals(chapter_id, status, entity_id);
            CREATE INDEX IF NOT EXISTS idx_plot_state_versions_work_chapter
                ON plot_state_versions(work_id, chapter_id, id);
            CREATE INDEX IF NOT EXISTS idx_plot_state_proposals_chapter_status
                ON plot_state_proposals(chapter_id, status, work_id);
            CREATE INDEX IF NOT EXISTS idx_entity_relations_work ON entity_relations(work_id, from_entity_id, to_entity_id);
            CREATE INDEX IF NOT EXISTS idx_consistency_alerts_chapter ON chapter_consistency_alerts(chapter_id, status, id);
            CREATE INDEX IF NOT EXISTS idx_work_revisions_work ON work_revisions(work_id, id);
            CREATE INDEX IF NOT EXISTS idx_conv_user ON agent_conversations(user_id, updated_at);
            CREATE INDEX IF NOT EXISTS idx_skills_user_work ON agent_skills(user_id, work_id, updated_at);
            CREATE INDEX IF NOT EXISTS idx_skill_resources_skill ON agent_skill_resources(skill_id, path);
            """
        )
        _add_col(conn, "chapters", "notes", "TEXT DEFAULT ''")
        _add_col(conn, "chapters", "deleted_at", "REAL")  # 软删时间戳；NULL=正常在册
        _add_col(conn, "user_settings", "asr_model", "TEXT")
        _add_col(conn, "user_settings", "asr_base_url", "TEXT")
        _add_col(conn, "user_settings", "asr_api_key", "TEXT")
        _add_col(conn, "works", "user_id", "INTEGER DEFAULT 0")
        _add_col(conn, "works", "notes", "TEXT DEFAULT ''")  # 作品设定(人物/世界观/大纲)
        _add_col(conn, "users", "is_admin", "INTEGER DEFAULT 0")  # 后台管理员标记
        _add_col(conn, "agent_skills", "source_kind", "TEXT DEFAULT 'manual'")
        _add_col(conn, "agent_skills", "source_markdown", "TEXT DEFAULT ''")
        _add_col(conn, "chapters", "workflow_status", "TEXT DEFAULT 'drafting'")
        _add_col(conn, "chapters", "workflow_goal", "TEXT DEFAULT ''")
        _add_col(conn, "chapters", "workflow_summary", "TEXT DEFAULT ''")
        _add_col(conn, "chapters", "workflow_checked_at", "REAL")
        _add_col(conn, "chapters", "branch_of_chapter_id", "INTEGER")
        _add_col(conn, "chapters", "branch_from_revision_id", "INTEGER")
        _add_col(conn, "chapter_revisions", "label", "TEXT DEFAULT ''")
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


def admin_user_stats():
    """每个用户的占用统计：作品数 / 章节数 / 对话数 / 对话存储字节数。
    分多句聚合而非一次大 JOIN，避免 章节×对话 笛卡尔积把字节数算重。"""
    with get_conn() as conn:
        users = [dict(r) for r in conn.execute(
            "SELECT id, username, is_admin, created_at FROM users ORDER BY id")]
        wcnt = {r["user_id"]: r["n"] for r in conn.execute(
            "SELECT user_id, COUNT(*) AS n FROM works GROUP BY user_id")}
        ccnt = {r["user_id"]: r["n"] for r in conn.execute(
            "SELECT w.user_id, COUNT(*) AS n FROM chapters c "
            "JOIN works w ON c.work_id=w.id GROUP BY w.user_id")}
        conv = {r["user_id"]: (r["n"], r["bytes"]) for r in conn.execute(
            "SELECT user_id, COUNT(*) AS n, "
            "COALESCE(SUM(LENGTH(messages)+LENGTH(summary)),0) AS bytes "
            "FROM agent_conversations GROUP BY user_id")}
        for u in users:
            uid = u["id"]
            c = conv.get(uid, (0, 0))
            u["works"] = wcnt.get(uid, 0)
            u["chapters"] = ccnt.get(uid, 0)
            u["convs"] = c[0]
            u["conv_bytes"] = c[1]
        return users


def admin_delete_user(user_id):
    """彻底删除一个用户及其全部数据（作品/章节/段落/版本/实体/对话/设置）。
    事务内执行；用户不存在返回 False。"""
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone():
            return False
        # 该用户各章节下的段落/版本/对话
        cids = [r["id"] for r in conn.execute(
            "SELECT c.id FROM chapters c JOIN works w ON c.work_id=w.id WHERE w.user_id=?",
            (user_id,))]
        for cid in cids:
            conn.execute("DELETE FROM segments WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM chapter_revisions WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM agent_conversations WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM entity_state_versions WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM entity_state_proposals WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM plot_state_versions WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM plot_state_proposals WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM chapter_consistency_alerts WHERE chapter_id=?", (cid,))
        # 章节以上的作品级数据 + 该用户的无章节对话 + 设置 + 账号本身
        conn.execute("DELETE FROM chapters WHERE work_id IN (SELECT id FROM works WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM entity_state_versions WHERE entity_id IN "
                     "(SELECT id FROM entities WHERE work_id IN (SELECT id FROM works WHERE user_id=?))", (user_id,))
        conn.execute("DELETE FROM entity_state_proposals WHERE entity_id IN "
                     "(SELECT id FROM entities WHERE work_id IN (SELECT id FROM works WHERE user_id=?))", (user_id,))
        conn.execute("DELETE FROM plot_state_versions WHERE work_id IN (SELECT id FROM works WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM plot_state_proposals WHERE work_id IN (SELECT id FROM works WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM entity_relations WHERE work_id IN (SELECT id FROM works WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM work_revisions WHERE work_id IN (SELECT id FROM works WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM entities WHERE work_id IN (SELECT id FROM works WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM agent_skill_resources WHERE skill_id IN (SELECT id FROM agent_skills WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM agent_skills WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM works WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM agent_conversations WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM user_settings WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        return True


def list_conversations_admin():
    """列出所有用户的对话，带用户名/章节标题/占用字节数（便于 admin 辨识后删除）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ac.id, ac.user_id, u.username, ac.chapter_id, "
            "c.title AS chapter_title, ac.msg_count, "
            "CASE WHEN ac.summary!='' THEN 1 ELSE 0 END AS has_summary, "
            "LENGTH(ac.messages)+LENGTH(ac.summary) AS bytes, "
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
            "SELECT llm_base_url, llm_api_key, llm_model, asr_base_url, asr_api_key, asr_model "
            "FROM user_settings WHERE user_id=?",
            (user_id,),
        ).fetchone()
        return dict(r) if r else None


def save_settings(user_id, base_url, api_key, model, asr_model=None,
                  asr_base_url=None, asr_api_key=None):
    """保存设置。api_key 为空或为掩码占位时保留旧值，避免清空已填的 key。"""
    now = time.time()
    with get_conn() as conn:
        old = conn.execute(
            "SELECT llm_api_key, asr_api_key FROM user_settings WHERE user_id=?", (user_id,)
        ).fetchone()
        if not api_key or api_key.startswith("****"):
            api_key = old["llm_api_key"] if old else ""
        if not asr_api_key or asr_api_key.startswith("****"):
            asr_api_key = old["asr_api_key"] if old else ""
        conn.execute(
            "INSERT INTO user_settings(user_id, llm_base_url, llm_api_key, llm_model, "
            "asr_base_url, asr_api_key, asr_model, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "llm_base_url=excluded.llm_base_url, llm_api_key=excluded.llm_api_key, "
            "llm_model=excluded.llm_model, asr_base_url=excluded.asr_base_url, "
            "asr_api_key=excluded.asr_api_key, asr_model=excluded.asr_model, updated_at=excluded.updated_at",
            (user_id, base_url, api_key, model, asr_base_url or "", asr_api_key,
             asr_model, now),
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
            conn.execute("DELETE FROM entity_state_versions WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM entity_state_proposals WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM plot_state_versions WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM plot_state_proposals WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM chapter_consistency_alerts WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM chapters WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM entity_state_versions WHERE entity_id IN (SELECT id FROM entities WHERE work_id=?)", (wid,))
        conn.execute("DELETE FROM entity_state_proposals WHERE entity_id IN (SELECT id FROM entities WHERE work_id=?)", (wid,))
        conn.execute("DELETE FROM plot_state_versions WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM plot_state_proposals WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM entity_relations WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM work_revisions WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM entities WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM agent_skill_resources WHERE skill_id IN (SELECT id FROM agent_skills WHERE work_id=?)", (wid,))
        conn.execute("DELETE FROM agent_skills WHERE work_id=?", (wid,))
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

def _state_version_payload(row):
    if not row:
        return None
    item = dict(row)
    item["state"] = _decode_character_state(item.pop("state_json", "{}"))
    return item


def _state_proposal_payload(row):
    if not row:
        return None
    item = dict(row)
    item["state"] = _decode_character_state(item.pop("state_json", "{}"))
    return item


def _entity_row(conn, eid, user_id):
    row = conn.execute(
        "SELECT e.id, e.work_id, e.name, e.kind, e.summary, e.detail, e.created_at, e.updated_at "
        "FROM entities e JOIN works w ON e.work_id=w.id WHERE e.id=? AND w.user_id=?",
        (eid, user_id),
    ).fetchone()
    return dict(row) if row else None


def get_entity(eid, user_id):
    with get_conn() as conn:
        return _entity_row(conn, eid, user_id)


def _chapter_for_work(conn, cid, wid):
    if cid is None:
        return None
    row = conn.execute(
        "SELECT id, work_id, title, ord FROM chapters "
        "WHERE id=? AND work_id=? AND deleted_at IS NULL",
        (cid, wid),
    ).fetchone()
    return dict(row) if row else None


# ---------- 剧情状态（作品级、按章节生效）----------

def _plot_state_version_payload(row):
    if not row:
        return None
    item = dict(row)
    item["state"] = _decode_plot_state(item.pop("state_json", "{}"))
    return item


def _plot_state_proposal_payload(row):
    if not row:
        return None
    item = dict(row)
    item["state"] = _decode_plot_state(item.pop("state_json", "{}"))
    return item


def _plot_state_version_at(conn, wid, target_chapter_id, before=False):
    if target_chapter_id is None:
        return None
    op = "<" if before else "<="
    row = conn.execute(
        "SELECT v.id, v.work_id, v.chapter_id, v.state_json, v.change_summary, v.evidence, "
        "v.source, v.proposal_id, v.created_at, c.title AS chapter_title, c.ord AS chapter_ord "
        "FROM plot_state_versions v "
        "JOIN chapters c ON c.id=v.chapter_id "
        "JOIN chapters target ON target.id=? "
        f"WHERE v.work_id=? AND target.work_id=? AND c.deleted_at IS NULL AND target.deleted_at IS NULL "
        f"AND c.work_id=target.work_id AND c.ord {op} target.ord "
        "ORDER BY c.ord DESC, v.id DESC LIMIT 1",
        (target_chapter_id, wid, wid),
    ).fetchone()
    return _plot_state_version_payload(row)


def get_plot_state_overview(wid, user_id, at_chapter_id=None):
    """剧情卡在指定章节时点的当前状态、待确认提议和历史。"""
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        target = _chapter_for_work(conn, at_chapter_id, wid) if at_chapter_id is not None else None
        if at_chapter_id is not None and not target:
            return {"invalid_chapter": True}
        version = _plot_state_version_at(conn, wid, at_chapter_id)
        history_rows = conn.execute(
            "SELECT v.id, v.work_id, v.chapter_id, v.state_json, v.change_summary, v.evidence, "
            "v.source, v.proposal_id, v.created_at, c.title AS chapter_title, c.ord AS chapter_ord "
            "FROM plot_state_versions v JOIN chapters c ON c.id=v.chapter_id "
            "WHERE v.work_id=? AND c.deleted_at IS NULL ORDER BY c.ord DESC, v.id DESC",
            (wid,),
        ).fetchall()
        proposal_rows = []
        if at_chapter_id is not None:
            proposal_rows = conn.execute(
                "SELECT id, work_id, chapter_id, state_json, change_summary, evidence, status, "
                "created_at, updated_at, resolved_at FROM plot_state_proposals "
                "WHERE work_id=? AND chapter_id=? "
                "ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, id DESC",
                (wid, at_chapter_id),
            ).fetchall()
        work = conn.execute("SELECT id, title FROM works WHERE id=?", (wid,)).fetchone()
        return {
            "work": dict(work) if work else {"id": wid},
            "target_chapter": target,
            "current_state": version["state"] if version else normalize_plot_state({}),
            "state_version": version,
            "history": [_plot_state_version_payload(row) for row in history_rows],
            "proposals": [_plot_state_proposal_payload(row) for row in proposal_rows],
        }


def create_plot_state_version(wid, user_id, chapter_id, state, change_summary="", evidence="", source="manual", proposal_id=None):
    now = time.time()
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        if not _chapter_for_work(conn, chapter_id, wid):
            return {"invalid_chapter": True}
        normalized = normalize_plot_state(state)
        if not plot_state_has_content(normalized):
            return {"empty_state": True}
        cur = conn.execute(
            "INSERT INTO plot_state_versions(work_id,chapter_id,state_json,change_summary,evidence,source,proposal_id,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (wid, chapter_id, json.dumps(normalized, ensure_ascii=False), (change_summary or "").strip()[:1400],
             (evidence or "").strip()[:4000], source, proposal_id, now),
        )
        row = conn.execute(
            "SELECT v.id, v.work_id, v.chapter_id, v.state_json, v.change_summary, v.evidence, "
            "v.source, v.proposal_id, v.created_at, c.title AS chapter_title, c.ord AS chapter_ord "
            "FROM plot_state_versions v JOIN chapters c ON c.id=v.chapter_id WHERE v.id=?",
            (cur.lastrowid,),
        ).fetchone()
        return _plot_state_version_payload(row)


def upsert_plot_state_proposal(wid, user_id, chapter_id, state, change_summary="", evidence=""):
    """同一作品×章节只保留一条待确认剧情更新，重复分析会覆盖陈旧建议。"""
    now = time.time()
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        if not _chapter_for_work(conn, chapter_id, wid):
            return {"invalid_chapter": True}
        normalized = normalize_plot_state(state)
        if not plot_state_has_content(normalized):
            return {"empty_state": True}
        payload = (json.dumps(normalized, ensure_ascii=False), (change_summary or "").strip()[:1400],
                   (evidence or "").strip()[:4000], now)
        existing = conn.execute(
            "SELECT id FROM plot_state_proposals WHERE work_id=? AND chapter_id=? AND status='pending' "
            "ORDER BY id DESC LIMIT 1", (wid, chapter_id),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE plot_state_proposals SET state_json=?, change_summary=?, evidence=?, updated_at=? WHERE id=?",
                (*payload, existing["id"]),
            )
            pid = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO plot_state_proposals(work_id,chapter_id,state_json,change_summary,evidence,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,'pending',?,?)",
                (wid, chapter_id, payload[0], payload[1], payload[2], now, now),
            )
            pid = cur.lastrowid
        row = conn.execute(
            "SELECT id, work_id, chapter_id, state_json, change_summary, evidence, status, "
            "created_at, updated_at, resolved_at FROM plot_state_proposals WHERE id=?", (pid,)
        ).fetchone()
        return _plot_state_proposal_payload(row)


def accept_plot_state_proposal(pid, user_id, state=None, change_summary=None, evidence=None):
    now = time.time()
    with get_conn() as conn:
        proposal = conn.execute(
            "SELECT p.id, p.work_id, p.chapter_id, p.state_json, p.change_summary, p.evidence, p.status "
            "FROM plot_state_proposals p JOIN works w ON w.id=p.work_id WHERE p.id=? AND w.user_id=?",
            (pid, user_id),
        ).fetchone()
        if not proposal:
            return None
        if proposal["status"] != "pending":
            return {"resolved": True}
        previous = _decode_plot_state(proposal["state_json"])
        normalized = normalize_plot_state(state, previous)
        if not plot_state_has_content(normalized):
            return {"empty_state": True}
        summary = proposal["change_summary"] if change_summary is None else (change_summary or "").strip()[:1400]
        proof = proposal["evidence"] if evidence is None else (evidence or "").strip()[:4000]
        edited = state is not None or change_summary is not None or evidence is not None
        cur = conn.execute(
            "INSERT INTO plot_state_versions(work_id,chapter_id,state_json,change_summary,evidence,source,proposal_id,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (proposal["work_id"], proposal["chapter_id"], json.dumps(normalized, ensure_ascii=False),
             summary, proof, "ai_edited" if edited else "ai_confirmed", pid, now),
        )
        conn.execute(
            "UPDATE plot_state_proposals SET status='accepted', updated_at=?, resolved_at=? WHERE id=?",
            (now, now, pid),
        )
        version = conn.execute(
            "SELECT v.id, v.work_id, v.chapter_id, v.state_json, v.change_summary, v.evidence, "
            "v.source, v.proposal_id, v.created_at, c.title AS chapter_title, c.ord AS chapter_ord "
            "FROM plot_state_versions v JOIN chapters c ON c.id=v.chapter_id WHERE v.id=?",
            (cur.lastrowid,),
        ).fetchone()
        return {"version": _plot_state_version_payload(version)}


def reject_plot_state_proposal(pid, user_id):
    now = time.time()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT p.id, p.status FROM plot_state_proposals p JOIN works w ON w.id=p.work_id "
            "WHERE p.id=? AND w.user_id=?", (pid, user_id),
        ).fetchone()
        if not row:
            return None
        if row["status"] != "pending":
            return {"resolved": True}
        conn.execute(
            "UPDATE plot_state_proposals SET status='rejected', updated_at=?, resolved_at=? WHERE id=?",
            (now, now, pid),
        )
        return {"ok": True}


def get_plot_digest(wid, user_id, at_chapter_id=None):
    overview = get_plot_state_overview(wid, user_id, at_chapter_id)
    if not overview or overview.get("invalid_chapter"):
        return ""
    state = overview.get("current_state") or {}
    facts = [f"{PLOT_STATE_LABELS[field]}={state[field]}" for field in PLOT_STATE_FIELDS if state.get(field)]
    if not facts:
        return ""
    target = overview.get("target_chapter") or {}
    point = f"第{target.get('ord')}章《{target.get('title') or '无标题'}》" if target else "当前时点"
    return "剧情状态（截至" + point + "，写作时必须遵循）：\n" + "；".join(facts)


def _state_version_at(conn, eid, target_chapter_id, before=False):
    if target_chapter_id is None:
        return None
    op = "<" if before else "<="
    row = conn.execute(
        "SELECT v.id, v.entity_id, v.chapter_id, v.state_json, v.change_summary, v.evidence, "
        "v.source, v.proposal_id, v.created_at, c.title AS chapter_title, c.ord AS chapter_ord "
        "FROM entity_state_versions v "
        "JOIN chapters c ON c.id=v.chapter_id "
        "JOIN chapters target ON target.id=? "
        f"WHERE v.entity_id=? AND c.deleted_at IS NULL AND target.deleted_at IS NULL "
        f"AND c.work_id=target.work_id AND c.ord {op} target.ord "
        "ORDER BY c.ord DESC, v.id DESC LIMIT 1",
        (target_chapter_id, eid),
    ).fetchone()
    return _state_version_payload(row)


def list_entities(wid, user_id, at_chapter_id=None):
    """作品级基础卡；传入章节时附带该时点有效的动态人物状态。"""
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        if at_chapter_id is not None and not _chapter_for_work(conn, at_chapter_id, wid):
            return None
        rows = [dict(r) for r in conn.execute(
            "SELECT id, name, kind, summary, detail, created_at, updated_at "
            "FROM entities WHERE work_id=? ORDER BY kind, id", (wid,)
        )]
        if at_chapter_id is None:
            return rows
        for entity in rows:
            entity["current_state"] = None
            entity["state_version"] = None
            entity["pending_count"] = 0
            if entity["kind"] != "人物":
                continue
            version = _state_version_at(conn, entity["id"], at_chapter_id)
            if version:
                entity["current_state"] = version["state"]
                entity["state_version"] = version
            entity["pending_count"] = conn.execute(
                "SELECT COUNT(*) FROM entity_state_proposals "
                "WHERE entity_id=? AND chapter_id=? AND status='pending'",
                (entity["id"], at_chapter_id),
            ).fetchone()[0]
        return rows


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
        conn.execute("DELETE FROM entity_state_versions WHERE entity_id=?", (eid,))
        conn.execute("DELETE FROM entity_state_proposals WHERE entity_id=?", (eid,))
        conn.execute("DELETE FROM entity_relations WHERE from_entity_id=? OR to_entity_id=?", (eid, eid))
        conn.execute("DELETE FROM entities WHERE id=?", (eid,))
        return True


def list_character_cards(wid, user_id, at_chapter_id=None, before=False):
    """供状态提取和 AI 上下文使用的人物基础卡 + 指定章节前/截至该章的状态。"""
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        if at_chapter_id is not None and not _chapter_for_work(conn, at_chapter_id, wid):
            return None
        rows = [dict(r) for r in conn.execute(
            "SELECT id, work_id, name, kind, summary, detail FROM entities "
            "WHERE work_id=? AND kind='人物' ORDER BY id", (wid,)
        )]
        for entity in rows:
            version = _state_version_at(conn, entity["id"], at_chapter_id, before=before)
            entity["current_state"] = version["state"] if version else normalize_character_state({})
            entity["state_version"] = version
        return rows


def get_entity_state_overview(eid, user_id, at_chapter_id=None):
    """单个人物在一个章节时点的卡片、待确认提议和成长历史。"""
    with get_conn() as conn:
        entity = _entity_row(conn, eid, user_id)
        if not entity:
            return None
        target = None
        if at_chapter_id is not None:
            target = _chapter_for_work(conn, at_chapter_id, entity["work_id"])
            if not target:
                return {"invalid_chapter": True}
        version = _state_version_at(conn, eid, at_chapter_id)
        history_rows = conn.execute(
            "SELECT v.id, v.entity_id, v.chapter_id, v.state_json, v.change_summary, v.evidence, "
            "v.source, v.proposal_id, v.created_at, c.title AS chapter_title, c.ord AS chapter_ord, "
            "c.deleted_at AS chapter_deleted_at "
            "FROM entity_state_versions v JOIN chapters c ON c.id=v.chapter_id "
            "WHERE v.entity_id=? ORDER BY c.ord DESC, v.id DESC",
            (eid,),
        ).fetchall()
        proposal_rows = []
        if at_chapter_id is not None:
            proposal_rows = conn.execute(
                "SELECT p.id, p.entity_id, p.chapter_id, p.state_json, p.change_summary, p.evidence, "
                "p.status, p.created_at, p.updated_at, p.resolved_at "
                "FROM entity_state_proposals p WHERE p.entity_id=? AND p.chapter_id=? "
                "ORDER BY CASE p.status WHEN 'pending' THEN 0 ELSE 1 END, p.id DESC",
                (eid, at_chapter_id),
            ).fetchall()
        return {
            "entity": entity,
            "target_chapter": target,
            "current_state": version["state"] if version else normalize_character_state({}),
            "state_version": version,
            "history": [_state_version_payload(row) for row in history_rows],
            "proposals": [_state_proposal_payload(row) for row in proposal_rows],
        }


def create_character_state_version(eid, user_id, chapter_id, state, change_summary="", evidence="", source="manual", proposal_id=None):
    """人工保存一个完整快照；基础卡与状态卡始终分开。"""
    now = time.time()
    with get_conn() as conn:
        entity = _entity_row(conn, eid, user_id)
        if not entity:
            return None
        if entity["kind"] != "人物":
            return {"not_character": True}
        if not _chapter_for_work(conn, chapter_id, entity["work_id"]):
            return {"invalid_chapter": True}
        normalized = normalize_character_state(state)
        if not character_state_has_content(normalized):
            return {"empty_state": True}
        cur = conn.execute(
            "INSERT INTO entity_state_versions(entity_id,chapter_id,state_json,change_summary,evidence,source,proposal_id,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (eid, chapter_id, json.dumps(normalized, ensure_ascii=False), (change_summary or "").strip()[:1000],
             (evidence or "").strip()[:3000], source, proposal_id, now),
        )
        row = conn.execute(
            "SELECT v.id, v.entity_id, v.chapter_id, v.state_json, v.change_summary, v.evidence, "
            "v.source, v.proposal_id, v.created_at, c.title AS chapter_title, c.ord AS chapter_ord "
            "FROM entity_state_versions v JOIN chapters c ON c.id=v.chapter_id WHERE v.id=?",
            (cur.lastrowid,),
        ).fetchone()
        return _state_version_payload(row)


def upsert_character_state_proposal(eid, user_id, chapter_id, state, change_summary="", evidence=""):
    """同一人物×章节只保留一条待确认 AI 提议，重新分析会刷新它而不是堆积噪音。"""
    now = time.time()
    with get_conn() as conn:
        entity = _entity_row(conn, eid, user_id)
        if not entity:
            return None
        if entity["kind"] != "人物":
            return {"not_character": True}
        if not _chapter_for_work(conn, chapter_id, entity["work_id"]):
            return {"invalid_chapter": True}
        normalized = normalize_character_state(state)
        if not character_state_has_content(normalized):
            return {"empty_state": True}
        payload = (json.dumps(normalized, ensure_ascii=False), (change_summary or "").strip()[:1000],
                   (evidence or "").strip()[:3000], now)
        existing = conn.execute(
            "SELECT id FROM entity_state_proposals WHERE entity_id=? AND chapter_id=? AND status='pending' "
            "ORDER BY id DESC LIMIT 1", (eid, chapter_id),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE entity_state_proposals SET state_json=?, change_summary=?, evidence=?, updated_at=? WHERE id=?",
                (*payload, existing["id"]),
            )
            pid = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO entity_state_proposals(entity_id,chapter_id,state_json,change_summary,evidence,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,'pending',?,?)",
                (eid, chapter_id, payload[0], payload[1], payload[2], now, now),
            )
            pid = cur.lastrowid
        row = conn.execute(
            "SELECT id, entity_id, chapter_id, state_json, change_summary, evidence, status, "
            "created_at, updated_at, resolved_at FROM entity_state_proposals WHERE id=?", (pid,)
        ).fetchone()
        return _state_proposal_payload(row)


def list_character_state_proposals(chapter_id, user_id):
    with get_conn() as conn:
        chapter = conn.execute(
            "SELECT c.id, c.work_id, c.title, c.ord FROM chapters c JOIN works w ON c.work_id=w.id "
            "WHERE c.id=? AND c.deleted_at IS NULL AND w.user_id=?", (chapter_id, user_id),
        ).fetchone()
        if not chapter:
            return None
        rows = conn.execute(
            "SELECT p.id, p.entity_id, p.chapter_id, p.state_json, p.change_summary, p.evidence, "
            "p.status, p.created_at, p.updated_at, p.resolved_at, e.name AS entity_name "
            "FROM entity_state_proposals p JOIN entities e ON e.id=p.entity_id "
            "WHERE p.chapter_id=? ORDER BY CASE p.status WHEN 'pending' THEN 0 ELSE 1 END, p.id DESC",
            (chapter_id,),
        ).fetchall()
        return {"chapter": dict(chapter), "proposals": [_state_proposal_payload(row) for row in rows]}


def accept_character_state_proposal(pid, user_id, state=None, change_summary=None, evidence=None):
    now = time.time()
    with get_conn() as conn:
        proposal = conn.execute(
            "SELECT p.id, p.entity_id, p.chapter_id, p.state_json, p.change_summary, p.evidence, p.status "
            "FROM entity_state_proposals p JOIN entities e ON e.id=p.entity_id "
            "JOIN works w ON w.id=e.work_id WHERE p.id=? AND w.user_id=?", (pid, user_id),
        ).fetchone()
        if not proposal:
            return None
        if proposal["status"] != "pending":
            return {"resolved": True}
        previous = _decode_character_state(proposal["state_json"])
        normalized = normalize_character_state(state, previous)
        if not character_state_has_content(normalized):
            return {"empty_state": True}
        summary = proposal["change_summary"] if change_summary is None else (change_summary or "").strip()[:1000]
        proof = proposal["evidence"] if evidence is None else (evidence or "").strip()[:3000]
        edited = state is not None or change_summary is not None or evidence is not None
        cur = conn.execute(
            "INSERT INTO entity_state_versions(entity_id,chapter_id,state_json,change_summary,evidence,source,proposal_id,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (proposal["entity_id"], proposal["chapter_id"], json.dumps(normalized, ensure_ascii=False),
             summary, proof, "ai_edited" if edited else "ai_confirmed", pid, now),
        )
        conn.execute(
            "UPDATE entity_state_proposals SET status='accepted', updated_at=?, resolved_at=? WHERE id=?",
            (now, now, pid),
        )
        version = conn.execute(
            "SELECT v.id, v.entity_id, v.chapter_id, v.state_json, v.change_summary, v.evidence, "
            "v.source, v.proposal_id, v.created_at, c.title AS chapter_title, c.ord AS chapter_ord "
            "FROM entity_state_versions v JOIN chapters c ON c.id=v.chapter_id WHERE v.id=?",
            (cur.lastrowid,),
        ).fetchone()
        return {"version": _state_version_payload(version)}


def reject_character_state_proposal(pid, user_id):
    now = time.time()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT p.id, p.status FROM entity_state_proposals p JOIN entities e ON e.id=p.entity_id "
            "JOIN works w ON w.id=e.work_id WHERE p.id=? AND w.user_id=?", (pid, user_id),
        ).fetchone()
        if not row:
            return None
        if row["status"] != "pending":
            return {"resolved": True}
        conn.execute(
            "UPDATE entity_state_proposals SET status='rejected', updated_at=?, resolved_at=? WHERE id=?",
            (now, now, pid),
        )
        return {"ok": True}


def get_entity_digest(wid, user_id, at_chapter_id=None):
    """基础实体 + 指定章节生效的人物状态，拼进 AI 的写作上下文。"""
    rows = list_entities(wid, user_id, at_chapter_id)
    if not rows:
        return ""
    lines = []
    for row in rows:
        line = f"[{row['kind']}] {row['name']}" + (f"：{row['summary']}" if row["summary"] else "")
        detail = (row.get("detail") or "").strip()
        if detail:
            line += "\n  基础设定：" + detail[:1600]
        state = row.get("current_state") or {}
        details = [f"{CHARACTER_STATE_LABELS[field]}={state[field]}" for field in CHARACTER_STATE_FIELDS if state.get(field)]
        if details:
            version = row.get("state_version") or {}
            chapter_name = version.get("chapter_title") or "当前时点"
            line += f"\n  动态状态（截至《{chapter_name}》）：" + "；".join(details)
        lines.append(line)
    relation_digest = get_relationship_digest(wid, user_id)
    digest = "作品实体（写作时保持一致）：\n" + "\n".join(lines)
    return digest + ("\n\n" + relation_digest if relation_digest else "")


# ---------- 人物关系（作品级，可视化关系图和 AI 连续性上下文）----------

def _relation_payload(row):
    return dict(row) if row else None


def list_entity_relations(wid, user_id):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        rows = conn.execute(
            "SELECT r.id, r.work_id, r.from_entity_id, r.to_entity_id, r.relation, r.detail, r.status, "
            "r.created_at, r.updated_at, a.name AS from_name, b.name AS to_name "
            "FROM entity_relations r JOIN entities a ON a.id=r.from_entity_id "
            "JOIN entities b ON b.id=r.to_entity_id WHERE r.work_id=? "
            "ORDER BY r.updated_at DESC, r.id DESC",
            (wid,),
        ).fetchall()
        return [_relation_payload(row) for row in rows]


def _entity_in_work(conn, eid, wid):
    row = conn.execute("SELECT id FROM entities WHERE id=? AND work_id=?", (eid, wid)).fetchone()
    return row is not None


def create_entity_relation(wid, user_id, from_entity_id, to_entity_id, relation, detail="", status="active"):
    now = time.time()
    relation = (relation or "").strip()[:160]
    detail = (detail or "").strip()[:2000]
    status = (status or "active").strip()[:48] or "active"
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        if from_entity_id == to_entity_id or not _entity_in_work(conn, from_entity_id, wid) or not _entity_in_work(conn, to_entity_id, wid):
            return {"invalid_entity": True}
        if not relation:
            return {"invalid_relation": True}
        cur = conn.execute(
            "INSERT INTO entity_relations(work_id,from_entity_id,to_entity_id,relation,detail,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (wid, from_entity_id, to_entity_id, relation, detail, status, now, now),
        )
        row = conn.execute(
            "SELECT r.id, r.work_id, r.from_entity_id, r.to_entity_id, r.relation, r.detail, r.status, "
            "r.created_at, r.updated_at, a.name AS from_name, b.name AS to_name "
            "FROM entity_relations r JOIN entities a ON a.id=r.from_entity_id "
            "JOIN entities b ON b.id=r.to_entity_id WHERE r.id=?",
            (cur.lastrowid,),
        ).fetchone()
        return _relation_payload(row)


def update_entity_relation(rid, user_id, from_entity_id, to_entity_id, relation, detail="", status="active"):
    now = time.time()
    relation = (relation or "").strip()[:160]
    detail = (detail or "").strip()[:2000]
    status = (status or "active").strip()[:48] or "active"
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT r.id, r.work_id FROM entity_relations r JOIN works w ON w.id=r.work_id "
            "WHERE r.id=? AND w.user_id=?", (rid, user_id),
        ).fetchone()
        if not existing:
            return None
        if from_entity_id == to_entity_id or not _entity_in_work(conn, from_entity_id, existing["work_id"]) or not _entity_in_work(conn, to_entity_id, existing["work_id"]):
            return {"invalid_entity": True}
        if not relation:
            return {"invalid_relation": True}
        conn.execute(
            "UPDATE entity_relations SET from_entity_id=?, to_entity_id=?, relation=?, detail=?, status=?, updated_at=? WHERE id=?",
            (from_entity_id, to_entity_id, relation, detail, status, now, rid),
        )
        row = conn.execute(
            "SELECT r.id, r.work_id, r.from_entity_id, r.to_entity_id, r.relation, r.detail, r.status, "
            "r.created_at, r.updated_at, a.name AS from_name, b.name AS to_name "
            "FROM entity_relations r JOIN entities a ON a.id=r.from_entity_id "
            "JOIN entities b ON b.id=r.to_entity_id WHERE r.id=?",
            (rid,),
        ).fetchone()
        return _relation_payload(row)


def delete_entity_relation(rid, user_id):
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM entity_relations WHERE id=? AND work_id IN (SELECT id FROM works WHERE user_id=?)",
            (rid, user_id),
        )
        return cur.rowcount > 0


def get_relationship_digest(wid, user_id):
    relations = list_entity_relations(wid, user_id)
    if not relations:
        return ""
    lines = []
    for item in relations[:80]:
        status = f"（{item['status']}）" if item.get("status") and item.get("status") != "active" else ""
        detail = f"：{item['detail']}" if item.get("detail") else ""
        lines.append(f"{item['from_name']} → {item['to_name']}：{item['relation']}{status}{detail}")
    return "人物关系（写作时保持连续）：\n" + "\n".join(lines)


# ---------- AI Skills（用户可复用的 agent 指令模板）----------

def list_agent_skills(user_id, work_id=None):
    """取用户可见的 Skill：通用 Skill 加当前作品专用 Skill。"""
    with get_conn() as conn:
        if work_id is not None and not _work_owned(conn, work_id, user_id):
            return None
        if work_id is None:
            rows = conn.execute(
                "SELECT s.id, s.user_id, s.work_id, s.name, s.description, s.instruction, s.source_kind, s.enabled, s.created_at, s.updated_at, "
                "(SELECT COUNT(*) FROM agent_skill_resources r WHERE r.skill_id=s.id) AS resource_count "
                "FROM agent_skills s WHERE s.user_id=? AND s.work_id IS NULL ORDER BY s.updated_at DESC, s.id DESC",
                (user_id,),
            )
        else:
            rows = conn.execute(
                "SELECT s.id, s.user_id, s.work_id, s.name, s.description, s.instruction, s.source_kind, s.enabled, s.created_at, s.updated_at, "
                "(SELECT COUNT(*) FROM agent_skill_resources r WHERE r.skill_id=s.id) AS resource_count "
                "FROM agent_skills s WHERE s.user_id=? AND (s.work_id IS NULL OR s.work_id=?) "
                "ORDER BY CASE WHEN s.work_id IS NULL THEN 0 ELSE 1 END, s.updated_at DESC, s.id DESC",
                (user_id, work_id),
            )
        return [dict(r) for r in rows]


def create_agent_skill(user_id, work_id, name, description, instruction, enabled=True,
                       source_kind="manual", source_markdown="", resources=None):
    now = time.time()
    with get_conn() as conn:
        if work_id is not None and not _work_owned(conn, work_id, user_id):
            return None
        cur = conn.execute(
            "INSERT INTO agent_skills(user_id,work_id,name,description,instruction,source_kind,source_markdown,enabled,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (user_id, work_id, name, description or "", instruction, source_kind, source_markdown or "",
             int(bool(enabled)), now, now),
        )
        for resource in resources or []:
            conn.execute(
                "INSERT INTO agent_skill_resources(skill_id,path,content,created_at) VALUES(?,?,?,?)",
                (cur.lastrowid, resource["path"], resource["content"], now),
            )
        return {
            "id": cur.lastrowid, "user_id": user_id, "work_id": work_id, "name": name,
            "description": description or "", "instruction": instruction, "source_kind": source_kind,
            "resource_count": len(resources or []), "enabled": int(bool(enabled)), "created_at": now, "updated_at": now,
        }


def _agent_skill_owned(conn, skill_id, user_id):
    return conn.execute(
        "SELECT 1 FROM agent_skills WHERE id=? AND user_id=?", (skill_id, user_id)
    ).fetchone() is not None


def update_agent_skill(skill_id, user_id, work_id, name, description, instruction, enabled):
    now = time.time()
    with get_conn() as conn:
        if not _agent_skill_owned(conn, skill_id, user_id):
            return False
        if work_id is not None and not _work_owned(conn, work_id, user_id):
            return False
        conn.execute(
            "UPDATE agent_skills SET work_id=?, name=?, description=?, instruction=?, enabled=?, updated_at=? WHERE id=?",
            (work_id, name, description or "", instruction, int(bool(enabled)), now, skill_id),
        )
        return True


def delete_agent_skill(skill_id, user_id):
    with get_conn() as conn:
        if not _agent_skill_owned(conn, skill_id, user_id):
            return False
        conn.execute("DELETE FROM agent_skill_resources WHERE skill_id=?", (skill_id,))
        conn.execute("DELETE FROM agent_skills WHERE id=?", (skill_id,))
        return True


def get_agent_skills_for_turn(user_id, work_id, skill_ids):
    """为一次 Agent 请求取已启用且作用域合法的 Skill，保持调用方的选中顺序。"""
    if not skill_ids:
        return []
    ids = list(dict.fromkeys(skill_ids))
    placeholders = ",".join("?" for _ in ids)
    with get_conn() as conn:
        if work_id is None:
            rows = conn.execute(
                f"SELECT id, name, description, instruction FROM agent_skills "
                f"WHERE user_id=? AND enabled=1 AND work_id IS NULL AND id IN ({placeholders})",
                (user_id, *ids),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT id, name, description, instruction FROM agent_skills "
                f"WHERE user_id=? AND enabled=1 AND (work_id IS NULL OR work_id=?) AND id IN ({placeholders})",
                (user_id, work_id, *ids),
            ).fetchall()
    by_id = {r["id"]: dict(r) for r in rows}
    return [by_id[i] for i in ids if i in by_id]


def list_agent_skill_catalog(user_id, work_id, limit=30):
    """提供给 Agent 的轻量 Skill 目录，只含元数据，不提前塞入完整规则。"""
    with get_conn() as conn:
        if work_id is None:
            rows = conn.execute(
                "SELECT id, name, description, source_kind FROM agent_skills "
                "WHERE user_id=? AND enabled=1 AND work_id IS NULL "
                "ORDER BY updated_at DESC, id DESC LIMIT ?",
                (user_id, limit),
            )
        else:
            rows = conn.execute(
                "SELECT id, name, description, source_kind FROM agent_skills "
                "WHERE user_id=? AND enabled=1 AND (work_id IS NULL OR work_id=?) "
                "ORDER BY CASE WHEN work_id IS NULL THEN 0 ELSE 1 END, updated_at DESC, id DESC LIMIT ?",
                (user_id, work_id, limit),
            )
        return [dict(r) for r in rows]


def get_agent_skill_resource(user_id, skill_id, path):
    with get_conn() as conn:
        if not _agent_skill_owned(conn, skill_id, user_id):
            return None
        r = conn.execute(
            "SELECT path, content FROM agent_skill_resources WHERE skill_id=? AND path=?",
            (skill_id, path),
        ).fetchone()
        return dict(r) if r else None


# ---------- 章节 ----------

def list_chapters(wid, user_id):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        return [dict(r) for r in conn.execute(
            "SELECT id, work_id, title, ord, created_at, length(content) AS chars, "
            "workflow_status, workflow_goal, workflow_summary, workflow_checked_at, "
            "branch_of_chapter_id, branch_from_revision_id "
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
            "SELECT id, work_id, title, ord, content, notes, workflow_status, workflow_goal, "
            "workflow_summary, workflow_checked_at, branch_of_chapter_id, branch_from_revision_id "
            "FROM chapters WHERE id=? AND deleted_at IS NULL",
            (cid,),
        ).fetchone()
        return dict(r) if r else None


def get_chapter_workflow(cid, user_id):
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        row = conn.execute(
            "SELECT id, work_id, title, ord, workflow_status, workflow_goal, workflow_summary, "
            "workflow_checked_at, updated_at FROM chapters WHERE id=? AND deleted_at IS NULL",
            (cid,),
        ).fetchone()
        return dict(row) if row else None


def update_chapter_workflow(cid, user_id, status=None, goal=None, summary=None, checked=False):
    if status is not None and status not in WORKFLOW_STATUSES:
        return {"invalid_status": True}
    now = time.time()
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        conn.execute(
            "UPDATE chapters SET workflow_status=COALESCE(?, workflow_status), "
            "workflow_goal=COALESCE(?, workflow_goal), workflow_summary=COALESCE(?, workflow_summary), "
            "workflow_checked_at=CASE WHEN ? THEN ? ELSE workflow_checked_at END, updated_at=? WHERE id=?",
            (status, (goal or "").strip()[:2000] if goal is not None else None,
             (summary or "").strip()[:4000] if summary is not None else None,
             int(bool(checked)), now, now, cid),
        )
        conn.execute(
            "UPDATE works SET updated_at=? WHERE id=(SELECT work_id FROM chapters WHERE id=?)",
            (now, cid),
        )
    return get_chapter_workflow(cid, user_id)


# ---------- 一致性提醒（章节复核的结构化结果）----------

_ALERT_SEVERITIES = {"critical", "warning", "notice"}


def _alert_payload(row):
    return dict(row) if row else None


def list_chapter_consistency_alerts(cid, user_id):
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        rows = conn.execute(
            "SELECT id, chapter_id, category, severity, title, detail, evidence, suggestion, status, "
            "created_at, updated_at, resolved_at FROM chapter_consistency_alerts "
            "WHERE chapter_id=? ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, "
            "CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, id DESC",
            (cid,),
        ).fetchall()
        return [_alert_payload(row) for row in rows]


def replace_chapter_consistency_alerts(cid, user_id, alerts):
    """一轮复核覆盖旧的未处理提醒，已忽略的提醒留作审阅痕迹。"""
    now = time.time()
    alerts = alerts if isinstance(alerts, list) else []
    normalized = []
    for item in alerts[:20]:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()[:240]
        if not title:
            continue
        severity = (item.get("severity") or "notice").strip().lower()
        if severity not in _ALERT_SEVERITIES:
            severity = "notice"
        normalized.append({
            "category": (item.get("category") or "连续性").strip()[:80] or "连续性",
            "severity": severity,
            "title": title,
            "detail": (item.get("detail") or "").strip()[:2400],
            "evidence": (item.get("evidence") or "").strip()[:1200],
            "suggestion": (item.get("suggestion") or "").strip()[:1600],
        })
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        conn.execute("DELETE FROM chapter_consistency_alerts WHERE chapter_id=? AND status='open'", (cid,))
        for item in normalized:
            conn.execute(
                "INSERT INTO chapter_consistency_alerts(chapter_id,category,severity,title,detail,evidence,suggestion,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,'open',?,?)",
                (cid, item["category"], item["severity"], item["title"], item["detail"],
                 item["evidence"], item["suggestion"], now, now),
            )
    return list_chapter_consistency_alerts(cid, user_id)


def dismiss_chapter_consistency_alert(alert_id, user_id):
    now = time.time()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT a.id, a.status FROM chapter_consistency_alerts a "
            "JOIN chapters c ON c.id=a.chapter_id JOIN works w ON w.id=c.work_id "
            "WHERE a.id=? AND w.user_id=?", (alert_id, user_id),
        ).fetchone()
        if not row:
            return None
        if row["status"] != "open":
            return {"resolved": True}
        conn.execute(
            "UPDATE chapter_consistency_alerts SET status='dismissed', updated_at=?, resolved_at=? WHERE id=?",
            (now, now, alert_id),
        )
        return {"ok": True}


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


def apply_chapter_edit_proposal(cid, user_id, base_content, operation, result_text, mode,
                                old_text="", start=None, end=None):
    """确认 AI 预览后再原子写入正文，并在写入前保存可恢复快照。"""
    if not isinstance(base_content, str) or not isinstance(result_text, str):
        return {"invalid": True}
    if operation not in {"append", "replace"}:
        return {"invalid": True}
    now = time.time()
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        chapter = conn.execute(
            "SELECT title, content FROM chapters WHERE id=? AND deleted_at IS NULL", (cid,)
        ).fetchone()
        if not chapter:
            return None
        current = chapter["content"] or ""
        if current != base_content:
            return {"stale": True}
        if operation == "append":
            content = current + ("\n" if current and not current.endswith("\n") else "") + result_text
            raw = f"（AI {mode or '生成'} 预览确认）"
        else:
            if not old_text:
                return {"invalid": True}
            if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(current) and current[start:end] == old_text:
                content = current[:start] + result_text + current[end:]
            else:
                index = current.find(old_text)
                if index < 0:
                    return {"stale": True}
                content = current[:index] + result_text + current[index + len(old_text):]
            raw = old_text
        snapshot = _add_revision_snapshot(conn, cid)
        conn.execute(
            "UPDATE chapters SET content=?, updated_at=? WHERE id=?",
            (content, now, cid),
        )
        conn.execute(
            "INSERT INTO segments(chapter_id, raw, result, mode, created_at) VALUES(?,?,?,?,?)",
            (cid, raw, result_text, f"preview:{mode or 'edit'}", now),
        )
        conn.execute(
            "UPDATE works SET updated_at=? WHERE id=(SELECT work_id FROM chapters WHERE id=?)",
            (now, cid),
        )
        return {
            "content": content,
            "title": chapter["title"],
            "revision": snapshot,
        }


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
        conn.execute("DELETE FROM entity_state_versions WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM entity_state_proposals WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM plot_state_versions WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM plot_state_proposals WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM chapter_consistency_alerts WHERE chapter_id=?", (cid,))
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


# ---------- 修订版本 / 分支 ----------

def _trim_revision_history(conn, cid):
    # 仅清理未命名的自动快照；作者显式命名的版本始终保留。
    conn.execute(
        "DELETE FROM chapter_revisions WHERE chapter_id=? AND COALESCE(label,'')='' AND id NOT IN "
        "(SELECT id FROM chapter_revisions WHERE chapter_id=? AND COALESCE(label,'')='' ORDER BY id DESC LIMIT 20)",
        (cid, cid),
    )


def _add_revision_snapshot(conn, cid, label=""):
    now = time.time()
    chap = conn.execute("SELECT title, content FROM chapters WHERE id=?", (cid,)).fetchone()
    if not chap:
        return None
    label = (label or "").strip()[:120]
    cur = conn.execute(
        "INSERT INTO chapter_revisions(chapter_id, title, content, label, created_at) VALUES(?,?,?,?,?)",
        (cid, chap["title"], chap["content"], label, now),
    )
    _trim_revision_history(conn, cid)
    return {"id": cur.lastrowid, "label": label, "created_at": now}


def add_revision(cid, user_id, label=""):
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        return _add_revision_snapshot(conn, cid, label)


def list_revisions(cid, user_id):
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        return [dict(r) for r in conn.execute(
            "SELECT id, title, label, length(content) AS chars, created_at "
            "FROM chapter_revisions WHERE chapter_id=? ORDER BY id DESC", (cid,)
        )]


def rename_revision(cid, user_id, rid, label):
    label = (label or "").strip()[:120]
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        cur = conn.execute(
            "UPDATE chapter_revisions SET label=? WHERE id=? AND chapter_id=?",
            (label, rid, cid),
        )
        if not cur.rowcount:
            return False
        row = conn.execute(
            "SELECT id, title, label, length(content) AS chars, created_at FROM chapter_revisions WHERE id=?",
            (rid,),
        ).fetchone()
        return dict(row) if row else False


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
    """取单个历史版本的完整内容（供 AI 找回 / 预览 / 分支读取）。"""
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        r = conn.execute(
            "SELECT id, title, content, label, created_at FROM chapter_revisions WHERE id=? AND chapter_id=?",
            (rid, cid),
        ).fetchone()
        return dict(r) if r else None


def create_chapter_branch(cid, user_id, rid, title=""):
    """从某个章节快照复制出一章独立可编辑的分支稿，不污染主线正文。"""
    now = time.time()
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        source = conn.execute(
            "SELECT work_id, notes, workflow_goal FROM chapters WHERE id=? AND deleted_at IS NULL", (cid,)
        ).fetchone()
        rev = conn.execute(
            "SELECT title, content FROM chapter_revisions WHERE id=? AND chapter_id=?", (rid, cid)
        ).fetchone()
        if not source or not rev:
            return False
        title = (title or "").strip()[:200] or f"{rev['title'] or '章节'} · 分支"
        ord_ = conn.execute(
            "SELECT COALESCE(MAX(ord),0)+1 FROM chapters WHERE work_id=?", (source["work_id"],)
        ).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO chapters(work_id,title,ord,content,notes,workflow_status,workflow_goal,"
            "branch_of_chapter_id,branch_from_revision_id,created_at,updated_at) "
            "VALUES(?,?,?,?,?,'drafting',?,?,?,?,?)",
            (source["work_id"], title, ord_, rev["content"] or "", source["notes"] or "",
             source["workflow_goal"] or "", cid, rid, now, now),
        )
        conn.execute("UPDATE works SET updated_at=? WHERE id=?", (now, source["work_id"]))
        return {"id": cur.lastrowid, "work_id": source["work_id"], "title": title, "ord": ord_,
                "branch_of_chapter_id": cid, "branch_from_revision_id": rid}


# ---------- 整本版本 ----------

def _work_snapshot(conn, wid):
    work = conn.execute("SELECT title, notes FROM works WHERE id=?", (wid,)).fetchone()
    chapters = [dict(row) for row in conn.execute(
        "SELECT id, title, ord, content, notes, workflow_status, workflow_goal, workflow_summary, "
        "workflow_checked_at, branch_of_chapter_id, branch_from_revision_id "
        "FROM chapters WHERE work_id=? AND deleted_at IS NULL ORDER BY ord", (wid,)
    )]
    return {"work": dict(work) if work else {}, "chapters": chapters}


def save_work_revision(wid, user_id, label=""):
    now = time.time()
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        label = (label or "").strip()[:120]
        snapshot = _work_snapshot(conn, wid)
        cur = conn.execute(
            "INSERT INTO work_revisions(work_id,label,snapshot_json,created_at) VALUES(?,?,?,?)",
            (wid, label, json.dumps(snapshot, ensure_ascii=False), now),
        )
        conn.execute(
            "DELETE FROM work_revisions WHERE work_id=? AND id NOT IN "
            "(SELECT id FROM work_revisions WHERE work_id=? ORDER BY id DESC LIMIT 20)",
            (wid, wid),
        )
        return {"id": cur.lastrowid, "label": label, "created_at": now,
                "chapters": len(snapshot["chapters"])}


def list_work_revisions(wid, user_id):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        rows = conn.execute(
            "SELECT id, label, snapshot_json, created_at FROM work_revisions WHERE work_id=? ORDER BY id DESC", (wid,)
        ).fetchall()
        result = []
        for row in rows:
            try:
                snapshot = json.loads(row["snapshot_json"] or "{}")
            except Exception:
                snapshot = {}
            result.append({"id": row["id"], "label": row["label"] or "", "created_at": row["created_at"],
                           "chapters": len(snapshot.get("chapters") or [])})
        return result


def get_work_revision(wid, user_id, rid):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        row = conn.execute(
            "SELECT id, label, snapshot_json, created_at FROM work_revisions WHERE id=? AND work_id=?", (rid, wid)
        ).fetchone()
        if not row:
            return False
        try:
            snapshot = json.loads(row["snapshot_json"] or "{}")
        except Exception:
            snapshot = {}
        return {"id": row["id"], "label": row["label"] or "", "created_at": row["created_at"],
                "snapshot": snapshot if isinstance(snapshot, dict) else {}}


def diff_work_revision(wid, user_id, rid):
    revision = get_work_revision(wid, user_id, rid)
    if not revision:
        return revision
    with get_conn() as conn:
        current = _work_snapshot(conn, wid)
    previous = {item.get("id"): item for item in revision["snapshot"].get("chapters", []) if isinstance(item, dict) and isinstance(item.get("id"), int)}
    now = {item.get("id"): item for item in current.get("chapters", []) if isinstance(item, dict) and isinstance(item.get("id"), int)}
    changes = []
    for cid in sorted(set(previous) | set(now)):
        old, new = previous.get(cid), now.get(cid)
        if old is None:
            changes.append({"chapter_id": cid, "title": new.get("title") or "", "status": "added",
                            "chars_before": 0, "chars_now": len(new.get("content") or "")})
            continue
        if new is None:
            changes.append({"chapter_id": cid, "title": old.get("title") or "", "status": "removed",
                            "chars_before": len(old.get("content") or ""), "chars_now": 0})
            continue
        fields = [name for name in ("title", "content", "notes", "workflow_status", "workflow_goal", "workflow_summary")
                  if (old.get(name) or "") != (new.get(name) or "")]
        changes.append({"chapter_id": cid, "title": new.get("title") or old.get("title") or "",
                        "status": "changed" if fields else "same", "changed_fields": fields,
                        "chars_before": len(old.get("content") or ""), "chars_now": len(new.get("content") or "")})
    return {"revision": {k: revision[k] for k in ("id", "label", "created_at")}, "chapters": changes}


def restore_work_revision(wid, user_id, rid):
    revision = get_work_revision(wid, user_id, rid)
    if not revision:
        return revision
    snapshot = revision["snapshot"]
    chapters = snapshot.get("chapters") if isinstance(snapshot.get("chapters"), list) else []
    now = time.time()
    restored = 0
    created = 0
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        for item in chapters:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "新章节").strip()[:200]
            ord_ = item.get("ord") if isinstance(item.get("ord"), int) else restored + created + 1
            content = item.get("content") if isinstance(item.get("content"), str) else ""
            notes = item.get("notes") if isinstance(item.get("notes"), str) else ""
            workflow_status = item.get("workflow_status") if item.get("workflow_status") in WORKFLOW_STATUSES else "drafting"
            workflow_goal = item.get("workflow_goal") if isinstance(item.get("workflow_goal"), str) else ""
            workflow_summary = item.get("workflow_summary") if isinstance(item.get("workflow_summary"), str) else ""
            old_id = item.get("id")
            existing = conn.execute("SELECT id FROM chapters WHERE id=? AND work_id=?", (old_id, wid)).fetchone() if isinstance(old_id, int) else None
            if existing:
                conn.execute(
                    "UPDATE chapters SET title=?, ord=?, content=?, notes=?, workflow_status=?, workflow_goal=?, "
                    "workflow_summary=?, workflow_checked_at=?, deleted_at=NULL, updated_at=? WHERE id=?",
                    (title, ord_, content, notes, workflow_status, workflow_goal[:2000], workflow_summary[:4000],
                     item.get("workflow_checked_at"), now, old_id),
                )
                restored += 1
            else:
                conn.execute(
                    "INSERT INTO chapters(work_id,title,ord,content,notes,workflow_status,workflow_goal,workflow_summary,"
                    "workflow_checked_at,branch_of_chapter_id,branch_from_revision_id,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (wid, title, ord_, content, notes, workflow_status, workflow_goal[:2000], workflow_summary[:4000],
                     item.get("workflow_checked_at"), item.get("branch_of_chapter_id"), item.get("branch_from_revision_id"), now, now),
                )
                created += 1
        work_notes = snapshot.get("work", {}).get("notes") if isinstance(snapshot.get("work"), dict) else None
        if isinstance(work_notes, str):
            conn.execute("UPDATE works SET notes=?, updated_at=? WHERE id=?", (work_notes, now, wid))
        else:
            conn.execute("UPDATE works SET updated_at=? WHERE id=?", (now, wid))
    return {"restored": restored, "created": created, "retained_current_chapters": max(0, len(_work_snapshot_after_restore(wid, user_id)) - restored - created)}


def _work_snapshot_after_restore(wid, user_id):
    """供整本恢复结果统计，避免把连接对象传出事务。"""
    return list_chapters(wid, user_id) or []
