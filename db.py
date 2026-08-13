"""SQLite 数据访问。用户 → 作品 → 章节 → 段落历史 / 修订版本。"""
import sqlite3
import os
import time
import json
import secrets
import hashlib
import re
from datetime import datetime
from contextlib import contextmanager

import config

DB_PATH = config.DB_PATH


def _content_fingerprint(content):
    """Stable source identifier for analysis outputs created from chapter prose."""
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


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
STORY_MEMORY_TYPES = (
    "event", "fact", "knowledge", "relationship_change", "item_change",
    "location_change", "ability_change", "world_rule", "promise", "secret",
)
STORY_MEMORY_STATUSES = ("proposed", "confirmed", "rejected", "stale")
STORY_MEMORY_TYPE_LABELS = {
    "event": "重要事件", "fact": "明确事实", "knowledge": "知情变化",
    "relationship_change": "关系变化", "item_change": "物品变化",
    "location_change": "地点变化", "ability_change": "能力变化",
    "world_rule": "世界规则", "promise": "承诺/任务", "secret": "重要秘密",
}
MAX_LLM_MODELS = 20
MAX_LLM_MODEL_ID_LENGTH = 160
MAX_TAVILY_API_KEYS = 20
MAX_TAVILY_API_KEY_LENGTH = 500


def _normalize_llm_models(models, active_model=""):
    """Keep a small ordered, de-duplicated list of user-selectable model IDs."""
    if isinstance(models, str):
        models = models.splitlines()
    if not isinstance(models, (list, tuple)):
        models = []
    result = []
    for value in list(models) + [active_model]:
        if not isinstance(value, str):
            continue
        model = value.strip()[:MAX_LLM_MODEL_ID_LENGTH]
        if model and model not in result:
            result.append(model)
        if len(result) >= MAX_LLM_MODELS:
            break
    return result


def _decode_llm_models(raw, active_model=""):
    try:
        models = json.loads(raw or "[]")
    except Exception:
        models = []
    return _normalize_llm_models(models, active_model)


def normalize_tavily_api_keys(values):
    """Normalize a small ordered key list without logging or returning raw errors."""
    if isinstance(values, str):
        values = re.split(r"[,;\r\n]+", values)
    if not isinstance(values, (list, tuple)):
        return []
    result = []
    for value in values:
        if not isinstance(value, str):
            continue
        key = value.strip().strip("\"'")[:MAX_TAVILY_API_KEY_LENGTH]
        if key and not key.startswith("****") and key not in result:
            result.append(key)
        if len(result) >= MAX_TAVILY_API_KEYS:
            break
    return result


def _decode_tavily_api_keys(raw):
    try:
        values = json.loads(raw or "[]")
    except Exception:
        values = []
    return normalize_tavily_api_keys(values)


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


def _migration_story_memory_and_provenance(conn):
    """V2 foundation: source-aware chapter analysis and confirmed story memories."""
    _add_col(conn, "chapters", "content_hash", "TEXT DEFAULT ''")
    _add_col(conn, "chapters", "content_revision", "INTEGER DEFAULT 1")
    _add_col(conn, "chapters", "analysis_status", "TEXT DEFAULT 'fresh'")
    _add_col(conn, "chapters", "analysis_reason", "TEXT DEFAULT ''")
    _add_col(conn, "chapters", "analysis_checked_at", "REAL")
    _add_col(conn, "entity_state_versions", "source_content_hash", "TEXT DEFAULT ''")
    _add_col(conn, "entity_state_versions", "stale", "INTEGER DEFAULT 0")
    _add_col(conn, "entity_state_proposals", "source_content_hash", "TEXT DEFAULT ''")
    _add_col(conn, "plot_state_versions", "source_content_hash", "TEXT DEFAULT ''")
    _add_col(conn, "plot_state_versions", "stale", "INTEGER DEFAULT 0")
    _add_col(conn, "plot_state_proposals", "source_content_hash", "TEXT DEFAULT ''")
    _add_col(conn, "chapter_consistency_alerts", "source_content_hash", "TEXT DEFAULT ''")
    _add_col(conn, "chapter_consistency_alerts", "stale", "INTEGER DEFAULT 0")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS story_memory_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            memory_type TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            evidence TEXT DEFAULT '',
            importance INTEGER NOT NULL DEFAULT 3,
            status TEXT NOT NULL DEFAULT 'proposed',
            source_content_hash TEXT DEFAULT '',
            source_content_revision INTEGER DEFAULT 0,
            stale INTEGER NOT NULL DEFAULT 0,
            confirmed_at REAL,
            resolved_at REAL,
            created_at REAL,
            updated_at REAL,
            FOREIGN KEY(work_id) REFERENCES works(id),
            FOREIGN KEY(chapter_id) REFERENCES chapters(id)
        );
        CREATE TABLE IF NOT EXISTS story_memory_entity_refs (
            memory_id INTEGER NOT NULL,
            entity_id INTEGER NOT NULL,
            PRIMARY KEY(memory_id, entity_id),
            FOREIGN KEY(memory_id) REFERENCES story_memory_items(id),
            FOREIGN KEY(entity_id) REFERENCES entities(id)
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS story_memory_fts USING fts5(
            title,
            content,
            evidence,
            keywords,
            tokenize='trigram'
        );
        CREATE INDEX IF NOT EXISTS idx_story_memory_work_chapter_status
            ON story_memory_items(work_id, chapter_id, status, stale, importance DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_story_memory_refs_entity
            ON story_memory_entity_refs(entity_id, memory_id);
        """
    )
    rows = conn.execute(
        "SELECT id, content, content_hash, content_revision, analysis_status FROM chapters"
    ).fetchall()
    for row in rows:
        content = row["content"] or ""
        fingerprint = row["content_hash"] or _content_fingerprint(content)
        revision = row["content_revision"] or 1
        status = row["analysis_status"] or ("needs_review" if content.strip() else "fresh")
        conn.execute(
            "UPDATE chapters SET content_hash=?, content_revision=?, analysis_status=? WHERE id=?",
            (fingerprint, revision, status, row["id"]),
        )


def _migration_model_presets(conn):
    """Persist selectable model IDs without changing the existing active-model contract."""
    _add_col(conn, "user_settings", "llm_models_json", "TEXT DEFAULT '[]'")
    rows = conn.execute("SELECT user_id, llm_model, llm_models_json FROM user_settings").fetchall()
    for row in rows:
        models = _decode_llm_models(row["llm_models_json"], row["llm_model"])
        conn.execute(
            "UPDATE user_settings SET llm_models_json=? WHERE user_id=?",
            (json.dumps(models, ensure_ascii=False), row["user_id"]),
        )


def _migration_creative_inspirations(conn):
    """Durable, source-preserving inspiration library kept separate from story facts."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS creative_inspirations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            work_id INTEGER,
            title TEXT NOT NULL,
            title_locked INTEGER NOT NULL DEFAULT 0,
            raw_text TEXT DEFAULT '',
            user_impression TEXT DEFAULT '',
            source_type TEXT NOT NULL DEFAULT 'text',
            primary_category TEXT NOT NULL DEFAULT 'general',
            library_status TEXT NOT NULL DEFAULT 'inbox',
            reuse_mode TEXT NOT NULL DEFAULT 'adaptable',
            use_policy TEXT NOT NULL DEFAULT 'generate_candidate',
            core_mechanism TEXT DEFAULT '',
            creative_summary TEXT DEFAULT '',
            suitable_context TEXT DEFAULT '',
            adaptation_notes TEXT DEFAULT '',
            production_notes TEXT DEFAULT '',
            constraints_text TEXT DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            mood_tags_json TEXT NOT NULL DEFAULT '[]',
            usage_tags_json TEXT NOT NULL DEFAULT '[]',
            search_keywords_json TEXT NOT NULL DEFAULT '[]',
            search_tags TEXT NOT NULL DEFAULT '',
            importance INTEGER NOT NULL DEFAULT 3,
            favorite INTEGER NOT NULL DEFAULT 0,
            analysis_status TEXT NOT NULL DEFAULT 'pending',
            analysis_error TEXT DEFAULT '',
            current_analysis_id INTEGER,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(work_id) REFERENCES works(id)
        );

        CREATE TABLE IF NOT EXISTS inspiration_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inspiration_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            asset_type TEXT NOT NULL,
            original_name TEXT DEFAULT '',
            mime_type TEXT DEFAULT '',
            storage_path TEXT DEFAULT '',
            source_url TEXT DEFAULT '',
            file_size INTEGER NOT NULL DEFAULT 0,
            content_hash TEXT DEFAULT '',
            duration_ms INTEGER,
            width INTEGER,
            height INTEGER,
            transcript TEXT DEFAULT '',
            description TEXT DEFAULT '',
            copyright_status TEXT NOT NULL DEFAULT 'unknown',
            reference_only INTEGER NOT NULL DEFAULT 1,
            processing_status TEXT NOT NULL DEFAULT 'uploaded',
            processing_error TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(inspiration_id) REFERENCES creative_inspirations(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS inspiration_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inspiration_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            analysis_type TEXT NOT NULL DEFAULT 'general',
            model TEXT DEFAULT '',
            prompt_version TEXT DEFAULT '',
            result_json TEXT NOT NULL DEFAULT '{}',
            result_text TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'completed',
            error TEXT DEFAULT '',
            created_at REAL NOT NULL,
            FOREIGN KEY(inspiration_id) REFERENCES creative_inspirations(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS inspiration_usages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inspiration_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            work_id INTEGER,
            chapter_id INTEGER,
            usage_target_type TEXT NOT NULL DEFAULT 'chapter',
            usage_target_id TEXT DEFAULT '',
            usage_type TEXT NOT NULL DEFAULT 'referenced',
            usage_status TEXT NOT NULL DEFAULT 'applied',
            adaptation_summary TEXT DEFAULT '',
            generated_candidate TEXT DEFAULT '',
            applied_excerpt TEXT DEFAULT '',
            user_feedback TEXT DEFAULT '',
            score INTEGER,
            context_snapshot_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(inspiration_id) REFERENCES creative_inspirations(id),
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(work_id) REFERENCES works(id),
            FOREIGN KEY(chapter_id) REFERENCES chapters(id)
        );

        CREATE TABLE IF NOT EXISTS inspiration_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inspiration_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            job_type TEXT NOT NULL DEFAULT 'analyze',
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            progress INTEGER NOT NULL DEFAULT 0,
            error TEXT DEFAULT '',
            created_at REAL NOT NULL,
            started_at REAL,
            finished_at REAL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(inspiration_id) REFERENCES creative_inspirations(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS inspiration_fts USING fts5(
            title,
            raw_text,
            user_impression,
            core_mechanism,
            creative_summary,
            suitable_context,
            search_tags,
            content='creative_inspirations',
            content_rowid='id',
            tokenize='trigram'
        );

        CREATE TRIGGER IF NOT EXISTS creative_inspirations_ai
        AFTER INSERT ON creative_inspirations BEGIN
            INSERT INTO inspiration_fts(
                rowid, title, raw_text, user_impression, core_mechanism,
                creative_summary, suitable_context, search_tags
            ) VALUES (
                new.id, new.title, new.raw_text, new.user_impression, new.core_mechanism,
                new.creative_summary, new.suitable_context, new.search_tags
            );
        END;

        CREATE TRIGGER IF NOT EXISTS creative_inspirations_ad
        AFTER DELETE ON creative_inspirations BEGIN
            INSERT INTO inspiration_fts(
                inspiration_fts, rowid, title, raw_text, user_impression,
                core_mechanism, creative_summary, suitable_context, search_tags
            ) VALUES (
                'delete', old.id, old.title, old.raw_text, old.user_impression,
                old.core_mechanism, old.creative_summary, old.suitable_context, old.search_tags
            );
        END;

        CREATE TRIGGER IF NOT EXISTS creative_inspirations_au
        AFTER UPDATE ON creative_inspirations BEGIN
            INSERT INTO inspiration_fts(
                inspiration_fts, rowid, title, raw_text, user_impression,
                core_mechanism, creative_summary, suitable_context, search_tags
            ) VALUES (
                'delete', old.id, old.title, old.raw_text, old.user_impression,
                old.core_mechanism, old.creative_summary, old.suitable_context, old.search_tags
            );
            INSERT INTO inspiration_fts(
                rowid, title, raw_text, user_impression, core_mechanism,
                creative_summary, suitable_context, search_tags
            ) VALUES (
                new.id, new.title, new.raw_text, new.user_impression, new.core_mechanism,
                new.creative_summary, new.suitable_context, new.search_tags
            );
        END;

        CREATE INDEX IF NOT EXISTS idx_inspirations_user_status
            ON creative_inspirations(user_id, library_status, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_inspirations_work
            ON creative_inspirations(work_id, library_status, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_inspirations_type
            ON creative_inspirations(user_id, source_type, primary_category);
        CREATE INDEX IF NOT EXISTS idx_inspiration_assets_parent
            ON inspiration_assets(inspiration_id, id);
        CREATE INDEX IF NOT EXISTS idx_inspiration_assets_hash
            ON inspiration_assets(user_id, content_hash);
        CREATE INDEX IF NOT EXISTS idx_inspiration_analyses_parent
            ON inspiration_analyses(inspiration_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_inspiration_usages_parent
            ON inspiration_usages(inspiration_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_inspiration_usages_chapter
            ON inspiration_usages(chapter_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_inspiration_jobs_status
            ON inspiration_jobs(status, updated_at, id);
        """
    )


def _conversation_title_from_messages(messages, fallback="旧会话"):
    """Build a readable session title without spending another model call."""
    if not isinstance(messages, list):
        return fallback
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            parts = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            content = " ".join(parts)
        if not isinstance(content, str):
            continue
        title = " ".join(content.replace("[voice] 语音指令", "语音会话").split()).strip()
        if title:
            return title[:32] + ("…" if len(title) > 32 else "")
    return fallback


def _migration_agent_sessions(conn):
    """Upgrade one-conversation-per-chapter storage to durable multi-session storage."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS agent_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            work_id INTEGER,
            chapter_id INTEGER,
            scope_key TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '新会话',
            messages TEXT NOT NULL DEFAULT '[]',
            summary TEXT NOT NULL DEFAULT '',
            msg_count INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 0,
            archived_at REAL,
            last_model TEXT DEFAULT '',
            legacy_conversation_id INTEGER UNIQUE,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(work_id) REFERENCES works(id),
            FOREIGN KEY(chapter_id) REFERENCES chapters(id)
        );
        CREATE INDEX IF NOT EXISTS idx_agent_sessions_scope
            ON agent_sessions(user_id, scope_key, archived_at, updated_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_sessions_active_scope
            ON agent_sessions(user_id, scope_key) WHERE is_active=1;
        """
    )
    rows = conn.execute(
        "SELECT ac.*, c.work_id AS chapter_work_id "
        "FROM agent_conversations ac "
        "LEFT JOIN chapters c ON c.id=ac.chapter_id "
        "ORDER BY ac.updated_at DESC, ac.id DESC"
    ).fetchall()
    active_scopes = set()
    for row in rows:
        chapter_id = row["chapter_id"]
        work_id = row["chapter_work_id"]
        scope_key = f"chapter:{chapter_id}" if chapter_id is not None else "global"
        scope = (row["user_id"], scope_key)
        is_active = 0 if scope in active_scopes else 1
        active_scopes.add(scope)
        try:
            messages = json.loads(row["messages"] or "[]")
        except Exception:
            messages = []
        if not isinstance(messages, list):
            messages = []
        created_at = row["created_at"] or row["updated_at"] or time.time()
        updated_at = row["updated_at"] or created_at
        conn.execute(
            "INSERT OR IGNORE INTO agent_sessions("
            "user_id,work_id,chapter_id,scope_key,title,messages,summary,msg_count,is_active,"
            "legacy_conversation_id,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row["user_id"], work_id, chapter_id, scope_key,
                _conversation_title_from_messages(messages),
                json.dumps(messages, ensure_ascii=False), row["summary"] or "",
                row["msg_count"] or len(messages), is_active, row["id"], created_at, updated_at,
            ),
        )


def _migration_tavily_search_settings(conn):
    """Allow each user to keep a private, ordered Tavily key pool."""
    _add_col(conn, "user_settings", "tavily_api_keys_json", "TEXT DEFAULT '[]'")


def _migration_character_images(conn):
    """Configurable image providers and persisted entity portraits."""
    _add_col(conn, "user_settings", "image_base_url", "TEXT DEFAULT ''")
    _add_col(conn, "user_settings", "image_api_key", "TEXT DEFAULT ''")
    _add_col(conn, "user_settings", "image_model", "TEXT DEFAULT ''")
    _add_col(conn, "user_settings", "image_size", "TEXT DEFAULT '1024x1024'")
    _add_col(conn, "entities", "image_path", "TEXT DEFAULT ''")
    _add_col(conn, "entities", "image_prompt", "TEXT DEFAULT ''")
    _add_col(conn, "entities", "image_updated_at", "REAL")


def _migration_story_sandboxes(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS story_sandboxes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_id INTEGER NOT NULL,
            name TEXT NOT NULL DEFAULT '主线推演',
            data_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(work_id) REFERENCES works(id)
        );
        CREATE INDEX IF NOT EXISTS idx_story_sandboxes_work
            ON story_sandboxes(work_id, updated_at DESC, id DESC);
        """
    )


def _migration_book_disassembly(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS book_disassembly_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            target_work_id INTEGER NOT NULL,
            source_name TEXT NOT NULL,
            strategy TEXT NOT NULL DEFAULT 'close_reading',
            status TEXT NOT NULL DEFAULT 'ready',
            total_chapters INTEGER NOT NULL DEFAULT 0,
            processed_chapters INTEGER NOT NULL DEFAULT 0,
            failed_chapters INTEGER NOT NULL DEFAULT 0,
            stats_json TEXT NOT NULL DEFAULT '{}',
            error TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            finished_at REAL,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(target_work_id) REFERENCES works(id)
        );
        CREATE TABLE IF NOT EXISTS book_disassembly_chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            ord INTEGER NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            target_chapter_id INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            result_json TEXT NOT NULL DEFAULT '{}',
            error TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(job_id) REFERENCES book_disassembly_jobs(id),
            FOREIGN KEY(target_chapter_id) REFERENCES chapters(id)
        );
        CREATE INDEX IF NOT EXISTS idx_disassembly_jobs_user
            ON book_disassembly_jobs(user_id, updated_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_disassembly_chapters_job
            ON book_disassembly_chapters(job_id, ord, id);
        """
    )


_MIGRATIONS = (
    (1, "baseline_schema", lambda conn: None),
    (2, "story_memory_and_provenance", _migration_story_memory_and_provenance),
    (3, "model_presets", _migration_model_presets),
    (4, "creative_inspiration_library", _migration_creative_inspirations),
    (5, "agent_multi_session", _migration_agent_sessions),
    (6, "tavily_search_settings", _migration_tavily_search_settings),
    (7, "character_images", _migration_character_images),
    (8, "story_sandboxes", _migration_story_sandboxes),
    (9, "book_disassembly", _migration_book_disassembly),
)


def _backup_before_migration(conn):
    """Use SQLite's backup API so a pending schema migration never touches the only copy."""
    base_dir = os.path.dirname(os.path.abspath(DB_PATH))
    backup_dir = os.path.join(base_dir, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = os.path.join(backup_dir, f"writehtml-before-migration-{stamp}.db")
    suffix = 1
    while os.path.exists(target):
        target = os.path.join(backup_dir, f"writehtml-before-migration-{stamp}-{suffix}.db")
        suffix += 1
    backup = sqlite3.connect(target)
    try:
        conn.backup(backup)
    finally:
        backup.close()
    return target


def _pending_migrations(conn):
    try:
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    except sqlite3.OperationalError:
        applied = set()
    return [migration for migration in _MIGRATIONS if migration[0] not in applied]


def _run_migrations(conn, had_database, backup_done=False):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at REAL NOT NULL)"
    )
    pending = _pending_migrations(conn)
    if pending and had_database and not backup_done:
        _backup_before_migration(conn)
    for version, name, apply in pending:
        apply(conn)
        conn.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES(?,?,?)",
            (version, name, time.time()),
        )


def init_db():
    parent = os.path.dirname(DB_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    had_database = os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 0
    # A legacy database can need old additive columns before the formal V2 migration
    # runs. Back it up first, before any CREATE/ALTER statement touches that file.
    backup_done = False
    if had_database:
        preflight = sqlite3.connect(DB_PATH)
        try:
            if _pending_migrations(preflight):
                _backup_before_migration(preflight)
                backup_done = True
        finally:
            preflight.close()
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
                image_base_url TEXT DEFAULT '',
                image_api_key TEXT DEFAULT '',
                image_model TEXT DEFAULT '',
                image_size TEXT DEFAULT '1024x1024',
                updated_at REAL
            );
            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                summary TEXT DEFAULT '',
                detail TEXT DEFAULT '',
                image_path TEXT DEFAULT '',
                image_prompt TEXT DEFAULT '',
                image_updated_at REAL,
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
            CREATE TABLE IF NOT EXISTS story_sandboxes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_id INTEGER NOT NULL,
                name TEXT NOT NULL DEFAULT '主线推演',
                data_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY(work_id) REFERENCES works(id)
            );
            CREATE TABLE IF NOT EXISTS book_disassembly_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                target_work_id INTEGER NOT NULL,
                source_name TEXT NOT NULL,
                strategy TEXT NOT NULL DEFAULT 'close_reading',
                status TEXT NOT NULL DEFAULT 'ready',
                total_chapters INTEGER NOT NULL DEFAULT 0,
                processed_chapters INTEGER NOT NULL DEFAULT 0,
                failed_chapters INTEGER NOT NULL DEFAULT 0,
                stats_json TEXT NOT NULL DEFAULT '{}',
                error TEXT DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                finished_at REAL
            );
            CREATE TABLE IF NOT EXISTS book_disassembly_chapters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                ord INTEGER NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                target_chapter_id INTEGER,
                status TEXT NOT NULL DEFAULT 'pending',
                result_json TEXT NOT NULL DEFAULT '{}',
                error TEXT DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
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
        _run_migrations(conn, had_database, backup_done=backup_done)
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


# ---------- agent 多会话持久化 ----------

def _agent_scope(conn, user_id, chapter_id=None, work_id=None):
    if chapter_id is not None:
        row = conn.execute(
            "SELECT c.id,c.work_id FROM chapters c JOIN works w ON w.id=c.work_id "
            "WHERE c.id=? AND w.user_id=?",
            (chapter_id, user_id),
        ).fetchone()
        if not row:
            return None
        return {
            "scope_key": f"chapter:{chapter_id}",
            "work_id": row["work_id"],
            "chapter_id": chapter_id,
        }
    if work_id is not None:
        if not _work_owned(conn, work_id, user_id):
            return None
        return {"scope_key": f"work:{work_id}", "work_id": work_id, "chapter_id": None}
    return {"scope_key": "global", "work_id": None, "chapter_id": None}


def resolve_agent_scope(user_id, chapter_id=None, work_id=None):
    with get_conn() as conn:
        return _agent_scope(conn, user_id, chapter_id, work_id)


def _agent_session_dict(row, include_messages=False):
    if not row:
        return None
    result = dict(row)
    result["is_active"] = bool(result.get("is_active"))
    result["archived"] = result.get("archived_at") is not None
    result["has_summary"] = bool(result.get("summary"))
    result["summary_preview"] = (result.get("summary") or "")[:180]
    if include_messages:
        try:
            messages = json.loads(result.get("messages") or "[]")
        except Exception:
            messages = []
        result["messages"] = messages if isinstance(messages, list) else []
    else:
        result.pop("messages", None)
        result.pop("summary", None)
    return result


def list_agent_sessions(user_id, chapter_id=None, work_id=None, include_archived=True):
    with get_conn() as conn:
        scope = _agent_scope(conn, user_id, chapter_id, work_id)
        if not scope:
            return None
        sql = (
            "SELECT id,user_id,work_id,chapter_id,scope_key,title,msg_count,is_active,"
            "archived_at,last_model,summary,created_at,updated_at "
            "FROM agent_sessions WHERE user_id=? AND scope_key=?"
        )
        params = [user_id, scope["scope_key"]]
        if not include_archived:
            sql += " AND archived_at IS NULL"
        sql += " ORDER BY is_active DESC, archived_at IS NOT NULL, updated_at DESC, id DESC"
        return [_agent_session_dict(row) for row in conn.execute(sql, params)]


def get_agent_session(user_id, session_id, include_messages=True):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM agent_sessions WHERE id=? AND user_id=?",
            (session_id, user_id),
        ).fetchone()
        return _agent_session_dict(row, include_messages=include_messages)


def _activate_latest_session(conn, user_id, scope_key):
    row = conn.execute(
        "SELECT id FROM agent_sessions WHERE user_id=? AND scope_key=? AND archived_at IS NULL "
        "ORDER BY updated_at DESC,id DESC LIMIT 1",
        (user_id, scope_key),
    ).fetchone()
    if row:
        conn.execute("UPDATE agent_sessions SET is_active=1 WHERE id=?", (row["id"],))
        return row["id"]
    return None


def create_agent_session(user_id, chapter_id=None, work_id=None, title="新会话"):
    now = time.time()
    with get_conn() as conn:
        scope = _agent_scope(conn, user_id, chapter_id, work_id)
        if not scope:
            return None
        conn.execute(
            "UPDATE agent_sessions SET is_active=0 WHERE user_id=? AND scope_key=? AND is_active=1",
            (user_id, scope["scope_key"]),
        )
        clean_title = " ".join((title or "新会话").split())[:48] or "新会话"
        cur = conn.execute(
            "INSERT INTO agent_sessions("
            "user_id,work_id,chapter_id,scope_key,title,messages,summary,msg_count,is_active,created_at,updated_at"
            ") VALUES(?,?,?,?,?,'[]','',0,1,?,?)",
            (
                user_id, scope["work_id"], scope["chapter_id"], scope["scope_key"],
                clean_title, now, now,
            ),
        )
        return get_agent_session_from_conn(conn, user_id, cur.lastrowid, include_messages=True)


def get_agent_session_from_conn(conn, user_id, session_id, include_messages=True):
    row = conn.execute(
        "SELECT * FROM agent_sessions WHERE id=? AND user_id=?",
        (session_id, user_id),
    ).fetchone()
    return _agent_session_dict(row, include_messages=include_messages)


def activate_agent_session(user_id, session_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id,scope_key,archived_at FROM agent_sessions WHERE id=? AND user_id=?",
            (session_id, user_id),
        ).fetchone()
        if not row or row["archived_at"] is not None:
            return None
        conn.execute(
            "UPDATE agent_sessions SET is_active=0 WHERE user_id=? AND scope_key=? AND is_active=1",
            (user_id, row["scope_key"]),
        )
        conn.execute(
            "UPDATE agent_sessions SET is_active=1,updated_at=? WHERE id=?",
            (time.time(), session_id),
        )
        return get_agent_session_from_conn(conn, user_id, session_id, include_messages=False)


def update_agent_session(user_id, session_id, *, title=None, archived=None):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id,scope_key,is_active,archived_at FROM agent_sessions WHERE id=? AND user_id=?",
            (session_id, user_id),
        ).fetchone()
        if not row:
            return None
        updates, params = [], []
        if title is not None:
            clean_title = " ".join(str(title).split())[:48]
            if not clean_title:
                return False
            updates.append("title=?")
            params.append(clean_title)
        if archived is not None:
            updates.append("archived_at=?")
            params.append(time.time() if archived else None)
            if archived:
                updates.append("is_active=0")
        updates.append("updated_at=?")
        params.append(time.time())
        params.append(session_id)
        conn.execute(f"UPDATE agent_sessions SET {','.join(updates)} WHERE id=?", params)
        if archived and row["is_active"]:
            _activate_latest_session(conn, user_id, row["scope_key"])
        if archived is False:
            conn.execute(
                "UPDATE agent_sessions SET is_active=0 WHERE user_id=? AND scope_key=? AND id!=?",
                (user_id, row["scope_key"], session_id),
            )
            conn.execute("UPDATE agent_sessions SET is_active=1 WHERE id=?", (session_id,))
        return get_agent_session_from_conn(conn, user_id, session_id, include_messages=False)


def delete_agent_session(user_id, session_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT scope_key,is_active FROM agent_sessions WHERE id=? AND user_id=?",
            (session_id, user_id),
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM agent_sessions WHERE id=? AND user_id=?", (session_id, user_id))
        if row["is_active"]:
            _activate_latest_session(conn, user_id, row["scope_key"])
        return True


def get_conversation(user_id, chapter_id, session_id=None, work_id=None):
    with get_conn() as conn:
        if session_id is not None:
            scope = _agent_scope(conn, user_id, chapter_id, work_id)
            if not scope:
                return None
            row = conn.execute(
                "SELECT * FROM agent_sessions WHERE id=? AND user_id=? AND scope_key=?",
                (session_id, user_id, scope["scope_key"]),
            ).fetchone()
        else:
            scope = _agent_scope(conn, user_id, chapter_id, work_id)
            if not scope:
                return None
            row = conn.execute(
                "SELECT * FROM agent_sessions WHERE user_id=? AND scope_key=? "
                "AND is_active=1 AND archived_at IS NULL LIMIT 1",
                (user_id, scope["scope_key"]),
            ).fetchone()
        return _agent_session_dict(row, include_messages=True)


def save_conversation(user_id, chapter_id, messages, summary, session_id=None, work_id=None,
                      title_hint=None, model=None):
    """Save one durable session; callers may retain the legacy chapter-only contract."""
    now = time.time()
    with get_conn() as conn:
        row = None
        if session_id is not None:
            scope = _agent_scope(conn, user_id, chapter_id, work_id)
            if not scope:
                return None
            row = conn.execute(
                "SELECT * FROM agent_sessions "
                "WHERE id=? AND user_id=? AND scope_key=? AND archived_at IS NULL",
                (session_id, user_id, scope["scope_key"]),
            ).fetchone()
            if not row:
                return None
        else:
            scope = _agent_scope(conn, user_id, chapter_id, work_id)
            if not scope:
                return None
            row = conn.execute(
                "SELECT * FROM agent_sessions WHERE user_id=? AND scope_key=? "
                "AND is_active=1 AND archived_at IS NULL LIMIT 1",
                (user_id, scope["scope_key"]),
            ).fetchone()
            if not row:
                conn.execute(
                    "UPDATE agent_sessions SET is_active=0 WHERE user_id=? AND scope_key=?",
                    (user_id, scope["scope_key"]),
                )
                cur = conn.execute(
                    "INSERT INTO agent_sessions("
                    "user_id,work_id,chapter_id,scope_key,title,messages,summary,msg_count,is_active,"
                    "last_model,created_at,updated_at"
                    ") VALUES(?,?,?,?,?,'[]','',0,1,?,?,?)",
                    (
                        user_id, scope["work_id"], scope["chapter_id"], scope["scope_key"],
                        _conversation_title_from_messages(
                            [{"role": "user", "content": title_hint or ""}], "新会话"
                        ),
                        model or "", now, now,
                    ),
                )
                row = conn.execute("SELECT * FROM agent_sessions WHERE id=?", (cur.lastrowid,)).fetchone()
        title = row["title"]
        if title == "新会话" and title_hint:
            title = _conversation_title_from_messages(
                [{"role": "user", "content": title_hint}], title
            )
        conn.execute(
            "UPDATE agent_sessions SET messages=?,summary=?,msg_count=?,title=?,last_model=?,updated_at=? "
            "WHERE id=?",
            (
                json.dumps(messages, ensure_ascii=False), summary or "", len(messages), title,
                model or row["last_model"] or "", now, row["id"],
            ),
        )
        return get_agent_session_from_conn(conn, user_id, row["id"], include_messages=True)


def delete_conversation(user_id, chapter_id, session_id=None, work_id=None):
    """Legacy clear endpoint now removes only the resolved active session."""
    conv = get_conversation(user_id, chapter_id, session_id=session_id, work_id=work_id)
    return bool(conv and delete_agent_session(user_id, conv["id"]))


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
            "FROM agent_sessions GROUP BY user_id")}
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
            conn.execute("DELETE FROM agent_sessions WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM agent_conversations WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM entity_state_versions WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM entity_state_proposals WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM plot_state_versions WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM plot_state_proposals WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM chapter_consistency_alerts WHERE chapter_id=?", (cid,))
            _delete_story_memories_for_chapter(conn, cid)
        # 章节以上的作品级数据 + 该用户的无章节对话 + 设置 + 账号本身
        conn.execute("DELETE FROM chapters WHERE work_id IN (SELECT id FROM works WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM entity_state_versions WHERE entity_id IN "
                     "(SELECT id FROM entities WHERE work_id IN (SELECT id FROM works WHERE user_id=?))", (user_id,))
        conn.execute("DELETE FROM entity_state_proposals WHERE entity_id IN "
                     "(SELECT id FROM entities WHERE work_id IN (SELECT id FROM works WHERE user_id=?))", (user_id,))
        conn.execute("DELETE FROM plot_state_versions WHERE work_id IN (SELECT id FROM works WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM plot_state_proposals WHERE work_id IN (SELECT id FROM works WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM entity_relations WHERE work_id IN (SELECT id FROM works WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM story_sandboxes WHERE work_id IN (SELECT id FROM works WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM book_disassembly_chapters WHERE job_id IN (SELECT id FROM book_disassembly_jobs WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM book_disassembly_jobs WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM work_revisions WHERE work_id IN (SELECT id FROM works WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM entities WHERE work_id IN (SELECT id FROM works WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM agent_skill_resources WHERE skill_id IN (SELECT id FROM agent_skills WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM agent_skills WHERE user_id=?", (user_id,))
        # 灵感与故事事实分离，但仍属于用户账号。物理素材目录由 API 层随后清理。
        conn.execute("DELETE FROM inspiration_jobs WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM inspiration_usages WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM inspiration_analyses WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM inspiration_assets WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM creative_inspirations WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM works WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM agent_sessions WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM agent_conversations WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM user_settings WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        return True


def list_conversations_admin():
    """列出所有用户的对话，带用户名/章节标题/占用字节数（便于 admin 辨识后删除）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT s.id, s.user_id, u.username, s.work_id, s.chapter_id, s.title, "
            "c.title AS chapter_title, s.msg_count, s.is_active, s.archived_at, "
            "CASE WHEN s.summary!='' THEN 1 ELSE 0 END AS has_summary, "
            "LENGTH(s.messages)+LENGTH(s.summary) AS bytes, "
            "s.created_at, s.updated_at "
            "FROM agent_sessions s "
            "LEFT JOIN users u ON u.id=s.user_id "
            "LEFT JOIN chapters c ON c.id=s.chapter_id "
            "ORDER BY s.updated_at DESC")
        return [dict(r) for r in rows]


def admin_delete_conversation(conv_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id,scope_key,is_active FROM agent_sessions WHERE id=?",
            (conv_id,),
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM agent_sessions WHERE id=?", (conv_id,))
        if row["is_active"]:
            _activate_latest_session(conn, row["user_id"], row["scope_key"])
        return True


def admin_clear_user_conversations(user_id):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM agent_sessions WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM agent_conversations WHERE user_id=?", (user_id,))
        return cur.rowcount


# ---------- 每个用户自己的大模型设置 ----------

def get_settings(user_id):
    """返回该用户的 LLM 设置；没存过返回 None（调用方用 .env 兜底）。"""
    with get_conn() as conn:
        r = conn.execute(
            "SELECT llm_base_url, llm_api_key, llm_model, llm_models_json, "
            "asr_base_url, asr_api_key, asr_model, tavily_api_keys_json, "
            "image_base_url, image_api_key, image_model, image_size "
            "FROM user_settings WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if not r:
            return None
        settings = dict(r)
        settings["llm_models"] = _decode_llm_models(
            settings.pop("llm_models_json", "[]"), settings.get("llm_model") or "",
        )
        settings["tavily_api_keys"] = _decode_tavily_api_keys(
            settings.pop("tavily_api_keys_json", "[]")
        )
        return settings


def save_settings(user_id, base_url, api_key, model, asr_model=None,
                  asr_base_url=None, asr_api_key=None, models=None,
                  tavily_api_keys=None, image_base_url=None,
                  image_api_key=None, image_model=None, image_size=None):
    """保存设置。api_key 为空或为掩码占位时保留旧值，避免清空已填的 key。"""
    now = time.time()
    with get_conn() as conn:
        old = conn.execute(
            "SELECT llm_api_key, asr_api_key, llm_model, llm_models_json, "
            "tavily_api_keys_json, image_base_url, image_api_key, image_model, image_size "
            "FROM user_settings WHERE user_id=?", (user_id,)
        ).fetchone()
        if not api_key or api_key.startswith("****"):
            api_key = old["llm_api_key"] if old else ""
        if not asr_api_key or asr_api_key.startswith("****"):
            asr_api_key = old["asr_api_key"] if old else ""
        if not image_api_key or image_api_key.startswith("****"):
            image_api_key = old["image_api_key"] if old else ""
        if image_base_url is None:
            image_base_url = old["image_base_url"] if old else ""
        if image_model is None:
            image_model = old["image_model"] if old else ""
        if image_size is None:
            image_size = old["image_size"] if old else "1024x1024"
        image_size = (image_size or "1024x1024").strip()[:32]
        if image_size != "auto" and not re.fullmatch(r"\d{2,4}x\d{2,4}", image_size):
            image_size = "1024x1024"
        model = (model or "").strip()[:MAX_LLM_MODEL_ID_LENGTH]
        old_models = _decode_llm_models(old["llm_models_json"], old["llm_model"] or "") if old else []
        model_list = _normalize_llm_models(old_models if models is None else models, model)
        if not model and model_list:
            model = model_list[0]
        if tavily_api_keys is None:
            tavily_keys = _decode_tavily_api_keys(old["tavily_api_keys_json"]) if old else []
        else:
            tavily_keys = normalize_tavily_api_keys(tavily_api_keys)
        conn.execute(
            "INSERT INTO user_settings(user_id, llm_base_url, llm_api_key, llm_model, "
            "llm_models_json, asr_base_url, asr_api_key, asr_model, tavily_api_keys_json, "
            "image_base_url, image_api_key, image_model, image_size, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "llm_base_url=excluded.llm_base_url, llm_api_key=excluded.llm_api_key, "
            "llm_model=excluded.llm_model, llm_models_json=excluded.llm_models_json, asr_base_url=excluded.asr_base_url, "
            "asr_api_key=excluded.asr_api_key, asr_model=excluded.asr_model, "
            "tavily_api_keys_json=excluded.tavily_api_keys_json, image_base_url=excluded.image_base_url, "
            "image_api_key=excluded.image_api_key, image_model=excluded.image_model, "
            "image_size=excluded.image_size, updated_at=excluded.updated_at",
            (user_id, base_url, api_key, model, json.dumps(model_list, ensure_ascii=False),
             asr_base_url or "", asr_api_key, asr_model,
             json.dumps(tavily_keys, ensure_ascii=False), image_base_url or "", image_api_key,
             (image_model or "").strip()[:MAX_LLM_MODEL_ID_LENGTH], image_size, now),
        )
        return {"model": model, "models": model_list, "tavily_user_key_count": len(tavily_keys),
                "image_model": (image_model or "").strip()[:MAX_LLM_MODEL_ID_LENGTH],
                "image_size": image_size}


def set_active_llm_model(user_id, model, fallback_models=None):
    """Switch only the active model while preserving API credentials and other settings."""
    model = (model or "").strip()[:MAX_LLM_MODEL_ID_LENGTH]
    if not model:
        return {"invalid_model": True}
    now = time.time()
    with get_conn() as conn:
        old = conn.execute(
            "SELECT llm_model, llm_models_json FROM user_settings WHERE user_id=?", (user_id,)
        ).fetchone()
        old_models = _decode_llm_models(old["llm_models_json"], old["llm_model"] or "") if old else []
        model_list = _normalize_llm_models(old_models + list(fallback_models or []))
        if model not in model_list:
            return {"unknown_model": True, "models": model_list}
        if old:
            conn.execute(
                "UPDATE user_settings SET llm_model=?, llm_models_json=?, updated_at=? WHERE user_id=?",
                (model, json.dumps(model_list, ensure_ascii=False), now, user_id),
            )
        else:
            conn.execute(
                "INSERT INTO user_settings(user_id, llm_model, llm_models_json, updated_at) VALUES(?,?,?,?)",
                (user_id, model, json.dumps(model_list, ensure_ascii=False), now),
            )
        return {"model": model, "models": model_list}


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
        # 删除作品不应顺带销毁作者收集的原始创意资产。解除章节引用并归档，
        # 作者仍可在灵感库的“已归档”中查看或重新设为通用。
        conn.execute(
            "UPDATE inspiration_usages SET chapter_id=NULL WHERE chapter_id IN "
            "(SELECT id FROM chapters WHERE work_id=?) AND user_id=?",
            (wid, user_id),
        )
        conn.execute(
            "UPDATE inspiration_usages SET work_id=NULL WHERE work_id=? AND user_id=?",
            (wid, user_id),
        )
        conn.execute(
            "UPDATE creative_inspirations SET work_id=NULL,library_status='archived',updated_at=? "
            "WHERE work_id=? AND user_id=?",
            (time.time(), wid, user_id),
        )
        cids = [r["id"] for r in conn.execute(
            "SELECT id FROM chapters WHERE work_id=?", (wid,)
        )]
        for cid in cids:
            conn.execute("DELETE FROM segments WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM chapter_revisions WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM agent_sessions WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM agent_conversations WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM entity_state_versions WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM entity_state_proposals WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM plot_state_versions WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM plot_state_proposals WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM chapter_consistency_alerts WHERE chapter_id=?", (cid,))
            _delete_story_memories_for_chapter(conn, cid)
        conn.execute("DELETE FROM chapters WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM entity_state_versions WHERE entity_id IN (SELECT id FROM entities WHERE work_id=?)", (wid,))
        conn.execute("DELETE FROM entity_state_proposals WHERE entity_id IN (SELECT id FROM entities WHERE work_id=?)", (wid,))
        conn.execute("DELETE FROM plot_state_versions WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM plot_state_proposals WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM entity_relations WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM story_sandboxes WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM book_disassembly_chapters WHERE job_id IN (SELECT id FROM book_disassembly_jobs WHERE target_work_id=?)", (wid,))
        conn.execute("DELETE FROM book_disassembly_jobs WHERE target_work_id=?", (wid,))
        conn.execute("DELETE FROM work_revisions WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM entities WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM agent_skill_resources WHERE skill_id IN (SELECT id FROM agent_skills WHERE work_id=?)", (wid,))
        conn.execute("DELETE FROM agent_skills WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM agent_sessions WHERE work_id=?", (wid,))
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


# ---------- 可视化大纲 / 情节分支沙盘 ----------

def _sandbox_payload(row, include_data=True):
    if not row:
        return None
    item = dict(row)
    raw = item.pop("data_json", "{}")
    if include_data:
        try:
            data = json.loads(raw or "{}")
        except Exception:
            data = {}
        item["data"] = data if isinstance(data, dict) else {"nodes": [], "edges": []}
    return item


def list_story_sandboxes(wid, user_id):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        rows = conn.execute(
            "SELECT id,work_id,name,data_json,created_at,updated_at FROM story_sandboxes "
            "WHERE work_id=? ORDER BY updated_at DESC,id DESC", (wid,),
        ).fetchall()
        result = []
        for row in rows:
            item = _sandbox_payload(row)
            data = item.pop("data")
            item["node_count"] = len(data.get("nodes") or [])
            item["edge_count"] = len(data.get("edges") or [])
            result.append(item)
        return result


def get_story_sandbox(sid, user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT s.id,s.work_id,s.name,s.data_json,s.created_at,s.updated_at "
            "FROM story_sandboxes s JOIN works w ON w.id=s.work_id WHERE s.id=? AND w.user_id=?",
            (sid, user_id),
        ).fetchone()
        return _sandbox_payload(row)


def create_story_sandbox(wid, user_id, name="主线推演", data=None):
    now = time.time()
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        clean_name = " ".join((name or "主线推演").split())[:80] or "主线推演"
        payload = data if isinstance(data, dict) else {"nodes": [], "edges": []}
        cur = conn.execute(
            "INSERT INTO story_sandboxes(work_id,name,data_json,created_at,updated_at) VALUES(?,?,?,?,?)",
            (wid, clean_name, json.dumps(payload, ensure_ascii=False), now, now),
        )
        row = conn.execute(
            "SELECT id,work_id,name,data_json,created_at,updated_at FROM story_sandboxes WHERE id=?",
            (cur.lastrowid,),
        ).fetchone()
        return _sandbox_payload(row)


def update_story_sandbox(sid, user_id, name=None, data=None):
    now = time.time()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT s.id FROM story_sandboxes s JOIN works w ON w.id=s.work_id "
            "WHERE s.id=? AND w.user_id=?", (sid, user_id),
        ).fetchone()
        if not row:
            return None
        clean_name = None if name is None else (" ".join((name or "").split())[:80] or "未命名沙盘")
        raw = None if data is None else json.dumps(data, ensure_ascii=False)
        conn.execute(
            "UPDATE story_sandboxes SET name=COALESCE(?,name),data_json=COALESCE(?,data_json),updated_at=? WHERE id=?",
            (clean_name, raw, now, sid),
        )
        result = conn.execute(
            "SELECT id,work_id,name,data_json,created_at,updated_at FROM story_sandboxes WHERE id=?", (sid,),
        ).fetchone()
        return _sandbox_payload(result)


def delete_story_sandbox(sid, user_id):
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM story_sandboxes WHERE id=? AND work_id IN (SELECT id FROM works WHERE user_id=?)",
            (sid, user_id),
        )
        return cur.rowcount > 0


# ---------- 拆书任务（逐章落盘，可暂停、重试和续跑） ----------

def _decode_json_object(raw):
    try:
        value = json.loads(raw or "{}")
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


def _disassembly_stats(conn, job_id):
    found = {"characters": set(), "locations": set(), "items": set(), "organizations": set(), "relations": set()}
    rows = conn.execute(
        "SELECT result_json FROM book_disassembly_chapters WHERE job_id=? AND status='done'", (job_id,),
    )
    for row in rows:
        result = _decode_json_object(row["result_json"])
        for key in ("characters", "locations", "items", "organizations"):
            for item in result.get(key) or []:
                if isinstance(item, dict) and item.get("name"):
                    found[key].add(str(item["name"]).strip())
        for item in result.get("relations") or []:
            if isinstance(item, dict):
                signature = (str(item.get("from") or "").strip(), str(item.get("to") or "").strip(),
                             str(item.get("relation") or "").strip())
                if all(signature):
                    found["relations"].add(signature)
    return {key: len(values) for key, values in found.items()}


def _disassembly_job_payload(conn, row, include_chapters=False):
    if not row:
        return None
    item = dict(row)
    item["stats"] = _decode_json_object(item.pop("stats_json", "{}"))
    if include_chapters:
        chapters = conn.execute(
            "SELECT id,ord,title,target_chapter_id,status,result_json,error,length(content) AS chars,"
            "substr(content,1,180) AS excerpt,updated_at FROM book_disassembly_chapters "
            "WHERE job_id=? ORDER BY ord,id", (item["id"],),
        ).fetchall()
        item["chapters"] = []
        for row_chapter in chapters:
            chapter = dict(row_chapter)
            chapter["result"] = _decode_json_object(chapter.pop("result_json", "{}"))
            item["chapters"].append(chapter)
    return item


def create_disassembly_job(user_id, target_work_id, source_name, strategy, chapters):
    now = time.time()
    with get_conn() as conn:
        if not _work_owned(conn, target_work_id, user_id):
            return None
        cur = conn.execute(
            "INSERT INTO book_disassembly_jobs(user_id,target_work_id,source_name,strategy,status,total_chapters,"
            "processed_chapters,failed_chapters,stats_json,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, target_work_id, (source_name or "导入书稿")[:240], strategy, "ready", len(chapters),
             0, 0, "{}", "", now, now),
        )
        job_id = cur.lastrowid
        next_ord = conn.execute("SELECT COALESCE(MAX(ord),0)+1 FROM chapters WHERE work_id=?", (target_work_id,)).fetchone()[0]
        for index, item in enumerate(chapters):
            title = (item.get("title") or f"第{index + 1}章").strip()[:200]
            content = item.get("content") or ""
            chapter_cur = conn.execute(
                "INSERT INTO chapters(work_id,title,ord,content,notes,content_hash,content_revision,analysis_status,"
                "analysis_reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (target_work_id, title, next_ord + index, content, "拆书导入，等待逐章分析", _content_fingerprint(content),
                 1, "fresh", "", now, now),
            )
            conn.execute(
                "INSERT INTO book_disassembly_chapters(job_id,ord,title,content,target_chapter_id,status,result_json,error,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (job_id, index + 1, title, content, chapter_cur.lastrowid, "pending", "{}", "", now, now),
            )
        conn.execute("UPDATE works SET updated_at=? WHERE id=?", (now, target_work_id))
        row = conn.execute("SELECT * FROM book_disassembly_jobs WHERE id=?", (job_id,)).fetchone()
        return _disassembly_job_payload(conn, row, True)


def list_disassembly_jobs(user_id, target_work_id=None):
    with get_conn() as conn:
        params = [user_id]
        where = "WHERE user_id=?"
        if target_work_id is not None:
            if not _work_owned(conn, target_work_id, user_id):
                return None
            where += " AND target_work_id=?"
            params.append(target_work_id)
        rows = conn.execute(
            "SELECT * FROM book_disassembly_jobs " + where + " ORDER BY updated_at DESC,id DESC LIMIT 30", params,
        ).fetchall()
        return [_disassembly_job_payload(conn, row) for row in rows]


def get_disassembly_job(job_id, user_id, include_chapters=True):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM book_disassembly_jobs WHERE id=? AND user_id=?", (job_id, user_id)).fetchone()
        return _disassembly_job_payload(conn, row, include_chapters)


def next_disassembly_chapter(job_id, user_id):
    with get_conn() as conn:
        job = conn.execute("SELECT * FROM book_disassembly_jobs WHERE id=? AND user_id=?", (job_id, user_id)).fetchone()
        if not job:
            return None
        row = conn.execute(
            "SELECT * FROM book_disassembly_chapters WHERE job_id=? AND status='pending' ORDER BY ord,id LIMIT 1",
            (job_id,),
        ).fetchone()
        return {"job": _disassembly_job_payload(conn, job), "chapter": dict(row) if row else None}


def set_disassembly_job_status(job_id, user_id, status, error=""):
    if status not in {"ready", "running", "paused", "partial", "completed", "cancelled"}:
        return {"invalid_status": True}
    now = time.time()
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM book_disassembly_jobs WHERE id=? AND user_id=?", (job_id, user_id)).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE book_disassembly_jobs SET status=?,error=?,updated_at=?,finished_at=CASE WHEN ? IN ('partial','completed','cancelled') THEN ? ELSE finished_at END WHERE id=?",
            (status, (error or "")[:1000], now, status, now, job_id),
        )
        result = conn.execute("SELECT * FROM book_disassembly_jobs WHERE id=?", (job_id,)).fetchone()
        return _disassembly_job_payload(conn, result, True)


def _upsert_disassembled_entity(conn, wid, kind, item, now):
    if not isinstance(item, dict):
        return None
    name = str(item.get("name") or "").strip()[:160]
    if not name:
        return None
    summary = str(item.get("summary") or item.get("role") or "").strip()[:1000]
    detail = str(item.get("detail") or item.get("description") or "").strip()[:5000]
    row = conn.execute("SELECT id,summary,detail FROM entities WHERE work_id=? AND name=? AND kind=?", (wid, name, kind)).fetchone()
    if row:
        conn.execute(
            "UPDATE entities SET summary=CASE WHEN summary='' THEN ? ELSE summary END,"
            "detail=CASE WHEN detail='' THEN ? ELSE detail END,updated_at=? WHERE id=?",
            (summary, detail, now, row["id"]),
        )
        return row["id"]
    cur = conn.execute(
        "INSERT INTO entities(work_id,name,kind,summary,detail,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (wid, name, kind, summary, detail, now, now),
    )
    return cur.lastrowid


def complete_disassembly_chapter(job_id, user_id, chapter_row_id, result):
    now = time.time()
    result = result if isinstance(result, dict) else {}
    with get_conn() as conn:
        job = conn.execute("SELECT * FROM book_disassembly_jobs WHERE id=? AND user_id=?", (job_id, user_id)).fetchone()
        chapter = conn.execute(
            "SELECT * FROM book_disassembly_chapters WHERE id=? AND job_id=?", (chapter_row_id, job_id),
        ).fetchone()
        if not job or not chapter:
            return None
        wid = job["target_work_id"]
        kinds = (("characters", "人物"), ("locations", "地点"), ("items", "物品"), ("organizations", "组织"))
        for key, kind in kinds:
            for entity in result.get(key) or []:
                _upsert_disassembled_entity(conn, wid, kind, entity, now)
        for relation in result.get("relations") or []:
            if not isinstance(relation, dict):
                continue
            from_name, to_name = str(relation.get("from") or "").strip(), str(relation.get("to") or "").strip()
            rel = str(relation.get("relation") or "").strip()[:160]
            if not from_name or not to_name or not rel:
                continue
            a = conn.execute("SELECT id FROM entities WHERE work_id=? AND name=? ORDER BY kind='人物' DESC,id LIMIT 1", (wid, from_name)).fetchone()
            b = conn.execute("SELECT id FROM entities WHERE work_id=? AND name=? ORDER BY kind='人物' DESC,id LIMIT 1", (wid, to_name)).fetchone()
            if not a or not b or a["id"] == b["id"]:
                continue
            exists = conn.execute(
                "SELECT id FROM entity_relations WHERE work_id=? AND from_entity_id=? AND to_entity_id=? AND relation=?",
                (wid, a["id"], b["id"], rel),
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO entity_relations(work_id,from_entity_id,to_entity_id,relation,detail,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (wid, a["id"], b["id"], rel, str(relation.get("detail") or "")[:2000], "active", now, now),
                )
        summary = str(result.get("summary") or "").strip()[:4000]
        if chapter["target_chapter_id"]:
            conn.execute(
                "UPDATE chapters SET workflow_summary=?,notes=CASE WHEN notes LIKE '拆书导入%' THEN ? ELSE notes END,updated_at=? WHERE id=?",
                (summary, f"拆书分析摘要：\n{summary}" if summary else "拆书导入", now, chapter["target_chapter_id"]),
            )
        conn.execute(
            "UPDATE book_disassembly_chapters SET status='done',result_json=?,error='',updated_at=? WHERE id=?",
            (json.dumps(result, ensure_ascii=False), now, chapter_row_id),
        )
        processed = conn.execute("SELECT COUNT(*) FROM book_disassembly_chapters WHERE job_id=? AND status='done'", (job_id,)).fetchone()[0]
        failed = conn.execute("SELECT COUNT(*) FROM book_disassembly_chapters WHERE job_id=? AND status='error'", (job_id,)).fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM book_disassembly_chapters WHERE job_id=? AND status='pending'", (job_id,)).fetchone()[0]
        stats = _disassembly_stats(conn, job_id)
        status = job["status"] if job["status"] in {"partial", "cancelled"} else (
            "completed" if pending == 0 and failed == 0 else "running"
        )
        conn.execute(
            "UPDATE book_disassembly_jobs SET status=?,processed_chapters=?,failed_chapters=?,stats_json=?,error='',updated_at=?,"
            "finished_at=CASE WHEN ?='completed' THEN ? ELSE finished_at END WHERE id=?",
            (status, processed, failed, json.dumps(stats, ensure_ascii=False), now, status, now, job_id),
        )
        result_row = conn.execute("SELECT * FROM book_disassembly_jobs WHERE id=?", (job_id,)).fetchone()
        return _disassembly_job_payload(conn, result_row, True)


def fail_disassembly_chapter(job_id, user_id, chapter_row_id, error):
    now = time.time()
    with get_conn() as conn:
        job = conn.execute("SELECT id FROM book_disassembly_jobs WHERE id=? AND user_id=?", (job_id, user_id)).fetchone()
        if not job:
            return None
        conn.execute(
            "UPDATE book_disassembly_chapters SET status='error',error=?,updated_at=? WHERE id=? AND job_id=?",
            ((error or "")[:1000], now, chapter_row_id, job_id),
        )
        failed = conn.execute("SELECT COUNT(*) FROM book_disassembly_chapters WHERE job_id=? AND status='error'", (job_id,)).fetchone()[0]
        status = "partial" if conn.execute(
            "SELECT status FROM book_disassembly_jobs WHERE id=?", (job_id,),
        ).fetchone()["status"] == "partial" else "paused"
        conn.execute("UPDATE book_disassembly_jobs SET status=?,failed_chapters=?,error=?,updated_at=? WHERE id=?",
                     (status, failed, (error or "")[:1000], now, job_id))
        row = conn.execute("SELECT * FROM book_disassembly_jobs WHERE id=?", (job_id,)).fetchone()
        return _disassembly_job_payload(conn, row, True)


def retry_disassembly_chapter(job_id, user_id, chapter_row_id):
    now = time.time()
    with get_conn() as conn:
        if not conn.execute("SELECT id FROM book_disassembly_jobs WHERE id=? AND user_id=?", (job_id, user_id)).fetchone():
            return None
        cur = conn.execute(
            "UPDATE book_disassembly_chapters SET status='pending',error='',updated_at=? WHERE id=? AND job_id=? AND status='error'",
            (now, chapter_row_id, job_id),
        )
        if not cur.rowcount:
            return {"invalid": True}
        conn.execute("UPDATE book_disassembly_jobs SET status='ready',error='',updated_at=? WHERE id=?", (now, job_id))
        row = conn.execute("SELECT * FROM book_disassembly_jobs WHERE id=?", (job_id,)).fetchone()
        return _disassembly_job_payload(conn, row, True)


# ---------- 实体卡片（作品级 wiki）----------

def _state_version_payload(row):
    if not row:
        return None
    item = dict(row)
    item["state"] = _decode_character_state(item.pop("state_json", "{}"))
    item["source_current"] = not bool(item.get("source_content_hash")) or bool(item.get("source_hash_matches", 1))
    item["is_stale"] = bool(item.get("stale")) or not item["source_current"]
    item.pop("source_hash_matches", None)
    return item


def _state_proposal_payload(row):
    if not row:
        return None
    item = dict(row)
    item["state"] = _decode_character_state(item.pop("state_json", "{}"))
    item["source_current"] = not bool(item.get("source_content_hash")) or bool(item.get("source_hash_matches", 1))
    item["is_stale"] = not item["source_current"]
    item.pop("source_hash_matches", None)
    return item


def _entity_row(conn, eid, user_id):
    row = conn.execute(
        "SELECT e.id, e.work_id, e.name, e.kind, e.summary, e.detail, e.image_prompt, "
        "CASE WHEN e.image_path<>'' THEN 1 ELSE 0 END AS has_image, e.image_updated_at, "
        "e.created_at, e.updated_at "
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


# ---------- 故事记忆（正文派生、作者确认、可追溯）----------

def _chapter_memory_source(conn, cid, wid):
    row = conn.execute(
        "SELECT id, work_id, title, ord, content_hash, content_revision, analysis_status, "
        "analysis_reason, analysis_checked_at FROM chapters "
        "WHERE id=? AND work_id=? AND deleted_at IS NULL",
        (cid, wid),
    ).fetchone()
    return dict(row) if row else None


def _memory_payload(conn, row):
    if not row:
        return None
    item = dict(row)
    refs = conn.execute(
        "SELECT e.id, e.name FROM story_memory_entity_refs r "
        "JOIN entities e ON e.id=r.entity_id WHERE r.memory_id=? ORDER BY e.name, e.id",
        (item["id"],),
    ).fetchall()
    item["entity_ids"] = [ref["id"] for ref in refs]
    item["entity_names"] = [ref["name"] for ref in refs]
    item["source_current"] = not bool(item.get("source_content_hash")) or bool(item.get("source_hash_matches", 1))
    item["is_stale"] = bool(item.get("stale")) or not item["source_current"]
    item.pop("source_hash_matches", None)
    return item


def _normalize_story_memory(item):
    item = item if isinstance(item, dict) else {}
    memory_type = (item.get("memory_type") or "fact").strip().lower()
    if memory_type not in STORY_MEMORY_TYPES:
        return {"invalid_type": True}
    title = (item.get("title") or "").strip()[:240]
    content = (item.get("content") or "").strip()[:4000]
    if not title or not content:
        return {"invalid": True}
    try:
        importance = int(item.get("importance", 3))
    except (TypeError, ValueError):
        importance = 3
    entity_ids = item.get("entity_ids")
    if entity_ids is None and item.get("entity_id") is not None:
        entity_ids = [item.get("entity_id")]
    if not isinstance(entity_ids, list):
        entity_ids = []
    normalized_ids = []
    for value in entity_ids[:12]:
        if isinstance(value, int) and not isinstance(value, bool) and value > 0 and value not in normalized_ids:
            normalized_ids.append(value)
    entity_names = item.get("entity_names")
    if entity_names is None and item.get("entity_name"):
        entity_names = [item.get("entity_name")]
    if not isinstance(entity_names, list):
        entity_names = []
    normalized_names = []
    for value in entity_names[:12]:
        name = str(value or "").strip()[:160]
        if name and name not in normalized_names:
            normalized_names.append(name)
    return {
        "memory_type": memory_type,
        "title": title,
        "content": content,
        "evidence": (item.get("evidence") or "").strip()[:1600],
        "importance": max(1, min(5, importance)),
        "entity_ids": normalized_ids,
        "entity_names": normalized_names,
    }


def _resolve_memory_entities(conn, wid, entity_ids, entity_names):
    resolved = []
    for eid in entity_ids:
        row = conn.execute("SELECT id FROM entities WHERE id=? AND work_id=?", (eid, wid)).fetchone()
        if row and row["id"] not in resolved:
            resolved.append(row["id"])
    for name in entity_names:
        row = conn.execute(
            "SELECT id FROM entities WHERE work_id=? AND name=? ORDER BY id LIMIT 1", (wid, name)
        ).fetchone()
        if row and row["id"] not in resolved:
            resolved.append(row["id"])
    return resolved


def _sync_story_memory_fts(conn, memory_id):
    row = conn.execute(
        "SELECT m.title, m.content, m.evidence, GROUP_CONCAT(e.name, ' ') AS entity_names "
        "FROM story_memory_items m "
        "LEFT JOIN story_memory_entity_refs r ON r.memory_id=m.id "
        "LEFT JOIN entities e ON e.id=r.entity_id WHERE m.id=? GROUP BY m.id",
        (memory_id,),
    ).fetchone()
    conn.execute("DELETE FROM story_memory_fts WHERE rowid=?", (memory_id,))
    if row:
        conn.execute(
            "INSERT INTO story_memory_fts(rowid,title,content,evidence,keywords) VALUES(?,?,?,?,?)",
            (memory_id, row["title"], row["content"], row["evidence"], row["entity_names"] or ""),
        )


def _set_story_memory_entities(conn, memory_id, entity_ids):
    conn.execute("DELETE FROM story_memory_entity_refs WHERE memory_id=?", (memory_id,))
    for eid in entity_ids:
        conn.execute(
            "INSERT OR IGNORE INTO story_memory_entity_refs(memory_id,entity_id) VALUES(?,?)",
            (memory_id, eid),
        )
    _sync_story_memory_fts(conn, memory_id)


def _delete_story_memories_for_chapter(conn, cid):
    memory_ids = [row["id"] for row in conn.execute(
        "SELECT id FROM story_memory_items WHERE chapter_id=?", (cid,)
    )]
    if not memory_ids:
        return
    marks = ",".join("?" for _ in memory_ids)
    conn.execute("DELETE FROM story_memory_fts WHERE rowid IN (" + marks + ")", memory_ids)
    conn.execute("DELETE FROM story_memory_entity_refs WHERE memory_id IN (" + marks + ")", memory_ids)
    conn.execute("DELETE FROM story_memory_items WHERE id IN (" + marks + ")", memory_ids)


def _delete_story_memories_for_work(conn, wid):
    memory_ids = [row["id"] for row in conn.execute(
        "SELECT id FROM story_memory_items WHERE work_id=?", (wid,)
    )]
    if not memory_ids:
        return
    marks = ",".join("?" for _ in memory_ids)
    conn.execute("DELETE FROM story_memory_fts WHERE rowid IN (" + marks + ")", memory_ids)
    conn.execute("DELETE FROM story_memory_entity_refs WHERE memory_id IN (" + marks + ")", memory_ids)
    conn.execute("DELETE FROM story_memory_items WHERE id IN (" + marks + ")", memory_ids)


_STORY_MEMORY_SELECT = (
    "SELECT m.id, m.work_id, m.chapter_id, m.memory_type, m.title, m.content, m.evidence, "
    "m.importance, m.status, m.source_content_hash, m.source_content_revision, m.stale, "
    "m.confirmed_at, m.resolved_at, m.created_at, m.updated_at, c.title AS chapter_title, "
    "c.ord AS chapter_ord, CASE WHEN m.source_content_hash='' OR m.source_content_hash=c.content_hash "
    "THEN 1 ELSE 0 END AS source_hash_matches "
    "FROM story_memory_items m JOIN chapters c ON c.id=m.chapter_id "
)


def _story_memory_where(wid, at_chapter_id=None, statuses=None, memory_types=None, include_stale=True):
    clauses = ["m.work_id=?", "c.deleted_at IS NULL"]
    params = [wid]
    if at_chapter_id is not None:
        clauses.append("c.ord <= (SELECT ord FROM chapters WHERE id=? AND work_id=? AND deleted_at IS NULL)")
        params.extend([at_chapter_id, wid])
    if statuses:
        values = [value for value in statuses if value in STORY_MEMORY_STATUSES]
        if values:
            clauses.append("m.status IN (" + ",".join("?" for _ in values) + ")")
            params.extend(values)
    if memory_types:
        values = [value for value in memory_types if value in STORY_MEMORY_TYPES]
        if values:
            clauses.append("m.memory_type IN (" + ",".join("?" for _ in values) + ")")
            params.extend(values)
    if not include_stale:
        clauses.extend(["m.stale=0", "(m.source_content_hash='' OR m.source_content_hash=c.content_hash)"])
    return " WHERE " + " AND ".join(clauses), params


def list_story_memories(wid, user_id, at_chapter_id=None, statuses=None, memory_types=None,
                        include_stale=True, limit=200):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        if at_chapter_id is not None and not _chapter_for_work(conn, at_chapter_id, wid):
            return {"invalid_chapter": True}
        where, params = _story_memory_where(wid, at_chapter_id, statuses, memory_types, include_stale)
        rows = conn.execute(
            _STORY_MEMORY_SELECT + where + " ORDER BY c.ord DESC, m.importance DESC, m.id DESC LIMIT ?",
            (*params, max(1, min(int(limit or 200), 500))),
        ).fetchall()
        return [_memory_payload(conn, row) for row in rows]


def get_story_memory_overview(wid, user_id, at_chapter_id=None):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        target = _chapter_memory_source(conn, at_chapter_id, wid) if at_chapter_id is not None else None
        if at_chapter_id is not None and not target:
            return {"invalid_chapter": True}
        where, params = _story_memory_where(wid, at_chapter_id, include_stale=True)
        rows = conn.execute(
            _STORY_MEMORY_SELECT + where + " ORDER BY c.ord DESC, m.importance DESC, m.id DESC LIMIT 300",
            params,
        ).fetchall()
        memories = [_memory_payload(conn, row) for row in rows]
        chapter_memories = [item for item in memories if target and item["chapter_id"] == target["id"]]
        return {
            "target_chapter": target,
            "proposals": [item for item in chapter_memories if item["status"] == "proposed" and not item["is_stale"]],
            "confirmed": [item for item in memories if item["status"] == "confirmed" and not item["is_stale"]],
            "stale": [item for item in memories if item["is_stale"] or item["status"] == "stale"],
            "chapter_memories": chapter_memories,
            "counts": {
                "proposed": sum(1 for item in chapter_memories if item["status"] == "proposed" and not item["is_stale"]),
                "confirmed": sum(1 for item in memories if item["status"] == "confirmed" and not item["is_stale"]),
                "stale": sum(1 for item in memories if item["is_stale"] or item["status"] == "stale"),
            },
        }


def upsert_story_memory_proposal(wid, user_id, chapter_id, item):
    now = time.time()
    normalized = _normalize_story_memory(item)
    if normalized.get("invalid") or normalized.get("invalid_type"):
        return normalized
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        chapter_source = _chapter_memory_source(conn, chapter_id, wid)
        if not chapter_source:
            return {"invalid_chapter": True}
        entity_ids = _resolve_memory_entities(
            conn, wid, normalized["entity_ids"], normalized["entity_names"]
        )
        existing = conn.execute(
            "SELECT id FROM story_memory_items WHERE work_id=? AND chapter_id=? AND memory_type=? "
            "AND title=? AND status='proposed' ORDER BY id DESC LIMIT 1",
            (wid, chapter_id, normalized["memory_type"], normalized["title"]),
        ).fetchone()
        values = (
            normalized["content"], normalized["evidence"], normalized["importance"],
            chapter_source["content_hash"] or _content_fingerprint(""), chapter_source["content_revision"] or 1, now,
        )
        if existing:
            conn.execute(
                "UPDATE story_memory_items SET content=?, evidence=?, importance=?, source_content_hash=?, "
                "source_content_revision=?, stale=0, status='proposed', updated_at=?, resolved_at=NULL WHERE id=?",
                (*values, existing["id"]),
            )
            memory_id = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO story_memory_items(work_id,chapter_id,memory_type,title,content,evidence,importance,"
                "status,source_content_hash,source_content_revision,stale,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,'proposed',?,?,0,?,?)",
                (wid, chapter_id, normalized["memory_type"], normalized["title"], *values[:-1], now, now),
            )
            memory_id = cur.lastrowid
        _set_story_memory_entities(conn, memory_id, entity_ids)
        row = conn.execute(_STORY_MEMORY_SELECT + " WHERE m.id=?", (memory_id,)).fetchone()
        return _memory_payload(conn, row)


def accept_story_memory(memory_id, user_id, changes=None):
    now = time.time()
    with get_conn() as conn:
        row = conn.execute(
            _STORY_MEMORY_SELECT + " JOIN works w ON w.id=m.work_id WHERE m.id=? AND w.user_id=?",
            (memory_id, user_id),
        ).fetchone()
        if not row:
            return None
        item = _memory_payload(conn, row)
        if item["status"] != "proposed":
            return {"resolved": True}
        if item["is_stale"]:
            conn.execute(
                "UPDATE story_memory_items SET status='stale', stale=1, updated_at=?, resolved_at=? WHERE id=?",
                (now, now, memory_id),
            )
            return {"stale": True}
        normalized = _normalize_story_memory({**item, **(changes or {})}) if changes else None
        if normalized and (normalized.get("invalid") or normalized.get("invalid_type")):
            return normalized
        if normalized:
            entity_ids = _resolve_memory_entities(
                conn, item["work_id"], normalized["entity_ids"], normalized["entity_names"]
            )
            conn.execute(
                "UPDATE story_memory_items SET memory_type=?, title=?, content=?, evidence=?, importance=?, "
                "status='confirmed', stale=0, confirmed_at=?, resolved_at=?, updated_at=? WHERE id=?",
                (normalized["memory_type"], normalized["title"], normalized["content"], normalized["evidence"],
                 normalized["importance"], now, now, now, memory_id),
            )
            _set_story_memory_entities(conn, memory_id, entity_ids)
        else:
            conn.execute(
                "UPDATE story_memory_items SET status='confirmed', stale=0, confirmed_at=?, resolved_at=?, updated_at=? WHERE id=?",
                (now, now, now, memory_id),
            )
            _sync_story_memory_fts(conn, memory_id)
        result = conn.execute(_STORY_MEMORY_SELECT + " WHERE m.id=?", (memory_id,)).fetchone()
        return _memory_payload(conn, result)


def reject_story_memory(memory_id, user_id):
    now = time.time()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT m.id, m.status FROM story_memory_items m JOIN works w ON w.id=m.work_id "
            "WHERE m.id=? AND w.user_id=?", (memory_id, user_id),
        ).fetchone()
        if not row:
            return None
        if row["status"] != "proposed":
            return {"resolved": True}
        conn.execute(
            "UPDATE story_memory_items SET status='rejected', resolved_at=?, updated_at=? WHERE id=?",
            (now, now, memory_id),
        )
        return {"ok": True}


def update_story_memory(memory_id, user_id, changes):
    normalized = _normalize_story_memory(changes)
    if normalized.get("invalid") or normalized.get("invalid_type"):
        return normalized
    now = time.time()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT m.id, m.work_id, m.status FROM story_memory_items m JOIN works w ON w.id=m.work_id "
            "WHERE m.id=? AND w.user_id=?", (memory_id, user_id),
        ).fetchone()
        if not row:
            return None
        if row["status"] != "confirmed":
            return {"not_confirmed": True}
        entity_ids = _resolve_memory_entities(
            conn, row["work_id"], normalized["entity_ids"], normalized["entity_names"]
        )
        conn.execute(
            "UPDATE story_memory_items SET memory_type=?, title=?, content=?, evidence=?, importance=?, updated_at=? WHERE id=?",
            (normalized["memory_type"], normalized["title"], normalized["content"], normalized["evidence"],
             normalized["importance"], now, memory_id),
        )
        _set_story_memory_entities(conn, memory_id, entity_ids)
        result = conn.execute(_STORY_MEMORY_SELECT + " WHERE m.id=?", (memory_id,)).fetchone()
        return _memory_payload(conn, result)


def _invalidate_chapter_derived_state(conn, cid, reason="正文已修改"):
    now = time.time()
    conn.execute(
        "UPDATE story_memory_items SET stale=1, status=CASE WHEN status='proposed' THEN 'stale' ELSE status END, "
        "updated_at=?, resolved_at=CASE WHEN status='proposed' THEN ? ELSE resolved_at END WHERE chapter_id=?",
        (now, now, cid),
    )
    conn.execute(
        "UPDATE entity_state_proposals SET status='stale', updated_at=?, resolved_at=? "
        "WHERE chapter_id=? AND status='pending'", (now, now, cid),
    )
    conn.execute(
        "UPDATE plot_state_proposals SET status='stale', updated_at=?, resolved_at=? "
        "WHERE chapter_id=? AND status='pending'", (now, now, cid),
    )
    conn.execute(
        "UPDATE entity_state_versions SET stale=1 WHERE chapter_id=? AND source_content_hash<>''", (cid,))
    conn.execute(
        "UPDATE plot_state_versions SET stale=1 WHERE chapter_id=? AND source_content_hash<>''", (cid,))
    conn.execute(
        "UPDATE chapter_consistency_alerts SET stale=1, status=CASE WHEN status='open' THEN 'stale' ELSE status END, "
        "updated_at=? WHERE chapter_id=?", (now, cid),
    )
    conn.execute(
        "UPDATE chapters SET analysis_status='needs_review', analysis_reason=?, analysis_checked_at=NULL WHERE id=?",
        ((reason or "正文已修改")[:240], cid),
    )


def mark_chapter_story_memory_stale(cid, user_id, reason="作者标记正文发生重大修改"):
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        chapter = conn.execute("SELECT work_id, ord FROM chapters WHERE id=?", (cid,)).fetchone()
        _invalidate_chapter_derived_state(conn, cid, reason)
        affected = conn.execute(
            "SELECT COUNT(*) FROM chapters WHERE work_id=? AND deleted_at IS NULL AND ord>?",
            (chapter["work_id"], chapter["ord"]),
        ).fetchone()[0]
        return {"ok": True, "later_chapters": affected}


def mark_chapter_analysis_reviewed(cid, user_id):
    now = time.time()
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        conn.execute(
            "UPDATE chapters SET analysis_status='fresh', analysis_reason='', analysis_checked_at=? WHERE id=?",
            (now, cid),
        )
        return {"ok": True, "checked_at": now}


def _memory_fts_terms(query):
    runs = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{2,}", query or "")
    terms = []
    for run in runs:
        if run not in terms:
            terms.append(run[:80])
        if len(run) > 6 and re.fullmatch(r"[\u4e00-\u9fff]+", run):
            for index in range(len(run) - 2):
                gram = run[index:index + 3]
                if gram not in terms:
                    terms.append(gram)
    return terms[:12]


def _memory_literal_terms(query):
    """Terms FTS5's trigram tokenizer cannot reliably find (notably two CJK chars)."""
    terms = []
    for run in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{2,}", query or ""):
        if re.fullmatch(r"[\u4e00-\u9fff]+", run):
            candidates = [run[index:index + 2] for index in range(max(1, len(run) - 1))]
        else:
            candidates = [run]
        for term in candidates:
            if term not in terms:
                terms.append(term[:80])
    return terms[:16]


def search_story_memories(wid, user_id, query="", entity_ids=None, memory_types=None,
                          before_chapter_id=None, limit=15):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        if before_chapter_id is not None and not _chapter_for_work(conn, before_chapter_id, wid):
            return {"invalid_chapter": True}
        clauses = ["m.work_id=?", "m.status='confirmed'", "m.stale=0", "c.deleted_at IS NULL",
                   "(m.source_content_hash='' OR m.source_content_hash=c.content_hash)"]
        params = [wid]
        if before_chapter_id is not None:
            clauses.append("c.ord <= (SELECT ord FROM chapters WHERE id=? AND work_id=? AND deleted_at IS NULL)")
            params.extend([before_chapter_id, wid])
        if memory_types:
            values = [value for value in memory_types if value in STORY_MEMORY_TYPES]
            if values:
                clauses.append("m.memory_type IN (" + ",".join("?" for _ in values) + ")")
                params.extend(values)
        normalized_ids = [value for value in (entity_ids or []) if isinstance(value, int) and value > 0]
        if normalized_ids:
            clauses.append(
                "EXISTS (SELECT 1 FROM story_memory_entity_refs r WHERE r.memory_id=m.id "
                "AND r.entity_id IN (" + ",".join("?" for _ in normalized_ids) + "))"
            )
            params.extend(normalized_ids)
        terms = _memory_fts_terms(query)
        literal_terms = _memory_literal_terms(query)
        from_sql = "story_memory_items m JOIN chapters c ON c.id=m.chapter_id"
        text_matches = []
        if terms:
            fts_query = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
            # MATCH cannot be nested directly inside an OR in SQLite. Keep it in a
            # subquery so FTS and the two-character CJK fallback can be combined.
            text_matches.append("m.id IN (SELECT rowid FROM story_memory_fts WHERE story_memory_fts MATCH ?)")
            params.append(fts_query)
        if literal_terms:
            text_matches.append("(" + " OR ".join(
                "instr(m.title || ' ' || m.content || ' ' || m.evidence, ?) > 0"
                for _ in literal_terms
            ) + ")")
            params.extend(literal_terms)
        if text_matches:
            clauses.append("(" + " OR ".join(text_matches) + ")")
        rows = conn.execute(
            _STORY_MEMORY_SELECT.replace("FROM story_memory_items m JOIN chapters c ON c.id=m.chapter_id", "FROM " + from_sql)
            + " WHERE " + " AND ".join(clauses)
            + " ORDER BY m.importance DESC, c.ord DESC, m.id DESC LIMIT ?",
            (*params, max(1, min(int(limit or 15), 40))),
        ).fetchall()
        return [_memory_payload(conn, row) for row in rows]


def list_recent_story_memories(wid, user_id, before_chapter_id=None, limit=12):
    return search_story_memories(wid, user_id, "", before_chapter_id=before_chapter_id, limit=limit)


def get_story_memory_source(memory_id, user_id):
    with get_conn() as conn:
        row = conn.execute(
            _STORY_MEMORY_SELECT + " JOIN works w ON w.id=m.work_id WHERE m.id=? AND w.user_id=?",
            (memory_id, user_id),
        ).fetchone()
        if not row:
            return None
        item = _memory_payload(conn, row)
        chapter = conn.execute(
            "SELECT id, title, ord, content, content_hash, content_revision FROM chapters WHERE id=?",
            (item["chapter_id"],),
        ).fetchone()
        return {"memory": item, "chapter": dict(chapter) if chapter else None}


def list_recent_chapter_summaries(wid, user_id, before_chapter_id=None, limit=5):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        clauses = ["work_id=?", "deleted_at IS NULL", "TRIM(COALESCE(workflow_summary, ''))<>''"]
        params = [wid]
        if before_chapter_id is not None:
            if not _chapter_for_work(conn, before_chapter_id, wid):
                return {"invalid_chapter": True}
            clauses.append("ord <= (SELECT ord FROM chapters WHERE id=? AND work_id=? AND deleted_at IS NULL)")
            params.extend([before_chapter_id, wid])
        rows = conn.execute(
            "SELECT id, title, ord, workflow_summary, content_hash, content_revision FROM chapters WHERE "
            + " AND ".join(clauses) + " ORDER BY ord DESC LIMIT ?",
            (*params, max(1, min(int(limit or 5), 20))),
        ).fetchall()
        return [dict(row) for row in rows]


# ---------- 剧情状态（作品级、按章节生效）----------

def _plot_state_version_payload(row):
    if not row:
        return None
    item = dict(row)
    item["state"] = _decode_plot_state(item.pop("state_json", "{}"))
    item["source_current"] = not bool(item.get("source_content_hash")) or bool(item.get("source_hash_matches", 1))
    item["is_stale"] = bool(item.get("stale")) or not item["source_current"]
    item.pop("source_hash_matches", None)
    return item


def _plot_state_proposal_payload(row):
    if not row:
        return None
    item = dict(row)
    item["state"] = _decode_plot_state(item.pop("state_json", "{}"))
    item["source_current"] = not bool(item.get("source_content_hash")) or bool(item.get("source_hash_matches", 1))
    item["is_stale"] = not item["source_current"]
    item.pop("source_hash_matches", None)
    return item


def _plot_state_version_at(conn, wid, target_chapter_id, before=False):
    if target_chapter_id is None:
        return None
    op = "<" if before else "<="
    row = conn.execute(
        "SELECT v.id, v.work_id, v.chapter_id, v.state_json, v.change_summary, v.evidence, "
        "v.source, v.proposal_id, v.source_content_hash, v.stale, v.created_at, "
        "c.title AS chapter_title, c.ord AS chapter_ord, "
        "CASE WHEN v.source_content_hash='' OR v.source_content_hash=c.content_hash THEN 1 ELSE 0 END AS source_hash_matches "
        "FROM plot_state_versions v "
        "JOIN chapters c ON c.id=v.chapter_id "
        "JOIN chapters target ON target.id=? "
        f"WHERE v.work_id=? AND target.work_id=? AND c.deleted_at IS NULL AND target.deleted_at IS NULL "
        f"AND c.work_id=target.work_id AND c.ord {op} target.ord AND v.stale=0 "
        "AND (v.source_content_hash='' OR v.source_content_hash=c.content_hash) "
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
            "v.source, v.proposal_id, v.source_content_hash, v.stale, v.created_at, "
            "c.title AS chapter_title, c.ord AS chapter_ord, "
            "CASE WHEN v.source_content_hash='' OR v.source_content_hash=c.content_hash THEN 1 ELSE 0 END AS source_hash_matches "
            "FROM plot_state_versions v JOIN chapters c ON c.id=v.chapter_id "
            "WHERE v.work_id=? AND c.deleted_at IS NULL ORDER BY c.ord DESC, v.id DESC",
            (wid,),
        ).fetchall()
        proposal_rows = []
        if at_chapter_id is not None:
            proposal_rows = conn.execute(
                "SELECT p.id, p.work_id, p.chapter_id, p.state_json, p.change_summary, p.evidence, p.status, "
                "p.source_content_hash, p.created_at, p.updated_at, p.resolved_at, "
                "CASE WHEN p.source_content_hash='' OR p.source_content_hash=c.content_hash THEN 1 ELSE 0 END AS source_hash_matches "
                "FROM plot_state_proposals p JOIN chapters c ON c.id=p.chapter_id "
                "WHERE p.work_id=? AND p.chapter_id=? "
                "ORDER BY CASE p.status WHEN 'pending' THEN 0 ELSE 1 END, p.id DESC",
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
        chapter_source = _chapter_memory_source(conn, chapter_id, wid)
        if not chapter_source:
            return {"invalid_chapter": True}
        normalized = normalize_plot_state(state)
        if not plot_state_has_content(normalized):
            return {"empty_state": True}
        cur = conn.execute(
            "INSERT INTO plot_state_versions(work_id,chapter_id,state_json,change_summary,evidence,source,proposal_id,"
            "source_content_hash,stale,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (wid, chapter_id, json.dumps(normalized, ensure_ascii=False), (change_summary or "").strip()[:1400],
             (evidence or "").strip()[:4000], source, proposal_id, "", 0, now),
        )
        row = conn.execute(
            "SELECT v.id, v.work_id, v.chapter_id, v.state_json, v.change_summary, v.evidence, "
            "v.source, v.proposal_id, v.source_content_hash, v.stale, v.created_at, "
            "c.title AS chapter_title, c.ord AS chapter_ord, "
            "CASE WHEN v.source_content_hash='' OR v.source_content_hash=c.content_hash THEN 1 ELSE 0 END AS source_hash_matches "
            "FROM plot_state_versions v JOIN chapters c ON c.id=v.chapter_id WHERE v.id=?",
            (cur.lastrowid,),
        ).fetchone()
        return _plot_state_version_payload(row)


def autosave_plot_state_version(wid, user_id, chapter_id, state, change_summary="", evidence=""):
    """自动保存当前章节的作者剧情卡，复用当前自动草稿而不覆盖已确认历史。"""
    now = time.time()
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        chapter_source = _chapter_memory_source(conn, chapter_id, wid)
        if not chapter_source:
            return {"invalid_chapter": True}
        normalized = normalize_plot_state(state)
        if not plot_state_has_content(normalized):
            return {"empty_state": True}
        summary = (change_summary or "").strip()[:1400]
        proof = (evidence or "").strip()[:4000]
        # 手动保存和 AI 采纳都是可追溯的节点。只更新最后一个确认节点之后
        # 新建出的自动草稿，避免自动输入篡改历史版本。
        existing = conn.execute(
            "SELECT id FROM plot_state_versions WHERE work_id=? AND chapter_id=? "
            "AND proposal_id IS NULL AND source='autosave' AND id > COALESCE(("
            "SELECT MAX(id) FROM plot_state_versions WHERE work_id=? AND chapter_id=? "
            "AND proposal_id IS NULL AND source<>'autosave'"
            "), 0) ORDER BY id DESC LIMIT 1",
            (wid, chapter_id, wid, chapter_id),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE plot_state_versions SET state_json=?, change_summary=?, evidence=?, source=?, created_at=? WHERE id=?",
                (json.dumps(normalized, ensure_ascii=False), summary, proof, "autosave", now, existing["id"]),
            )
            vid = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO plot_state_versions(work_id,chapter_id,state_json,change_summary,evidence,source,proposal_id,"
                "source_content_hash,stale,created_at) VALUES(?,?,?,?,?,'autosave',NULL,'',0,?)",
                (wid, chapter_id, json.dumps(normalized, ensure_ascii=False), summary, proof, now),
            )
            vid = cur.lastrowid
        row = conn.execute(
            "SELECT v.id, v.work_id, v.chapter_id, v.state_json, v.change_summary, v.evidence, "
            "v.source, v.proposal_id, v.source_content_hash, v.stale, v.created_at, "
            "c.title AS chapter_title, c.ord AS chapter_ord, "
            "CASE WHEN v.source_content_hash='' OR v.source_content_hash=c.content_hash THEN 1 ELSE 0 END AS source_hash_matches "
            "FROM plot_state_versions v JOIN chapters c ON c.id=v.chapter_id WHERE v.id=?",
            (vid,),
        ).fetchone()
        return _plot_state_version_payload(row)


def upsert_plot_state_proposal(wid, user_id, chapter_id, state, change_summary="", evidence=""):
    """同一作品×章节只保留一条待确认剧情更新，重复分析会覆盖陈旧建议。"""
    now = time.time()
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        chapter_source = _chapter_memory_source(conn, chapter_id, wid)
        if not chapter_source:
            return {"invalid_chapter": True}
        normalized = normalize_plot_state(state)
        if not plot_state_has_content(normalized):
            return {"empty_state": True}
        payload = (json.dumps(normalized, ensure_ascii=False), (change_summary or "").strip()[:1400],
                   (evidence or "").strip()[:4000], chapter_source["content_hash"] or _content_fingerprint(""), now)
        existing = conn.execute(
            "SELECT id FROM plot_state_proposals WHERE work_id=? AND chapter_id=? AND status='pending' "
            "ORDER BY id DESC LIMIT 1", (wid, chapter_id),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE plot_state_proposals SET state_json=?, change_summary=?, evidence=?, source_content_hash=?, "
                "status='pending', updated_at=?, resolved_at=NULL WHERE id=?",
                (*payload, existing["id"]),
            )
            pid = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO plot_state_proposals(work_id,chapter_id,state_json,change_summary,evidence,status,source_content_hash,created_at,updated_at) "
                "VALUES(?,?,?,?,?,'pending',?,?,?)",
                (wid, chapter_id, payload[0], payload[1], payload[2], payload[3], now, now),
            )
            pid = cur.lastrowid
        row = conn.execute(
            "SELECT p.id, p.work_id, p.chapter_id, p.state_json, p.change_summary, p.evidence, p.status, "
            "p.source_content_hash, p.created_at, p.updated_at, p.resolved_at, "
            "CASE WHEN p.source_content_hash='' OR p.source_content_hash=c.content_hash THEN 1 ELSE 0 END AS source_hash_matches "
            "FROM plot_state_proposals p JOIN chapters c ON c.id=p.chapter_id WHERE p.id=?", (pid,)
        ).fetchone()
        return _plot_state_proposal_payload(row)


def accept_plot_state_proposal(pid, user_id, state=None, change_summary=None, evidence=None):
    now = time.time()
    with get_conn() as conn:
        proposal = conn.execute(
            "SELECT p.id, p.work_id, p.chapter_id, p.state_json, p.change_summary, p.evidence, p.status, "
            "p.source_content_hash, c.content_hash FROM plot_state_proposals p "
            "JOIN works w ON w.id=p.work_id JOIN chapters c ON c.id=p.chapter_id "
            "WHERE p.id=? AND w.user_id=?",
            (pid, user_id),
        ).fetchone()
        if not proposal:
            return None
        if proposal["status"] != "pending":
            return {"resolved": True}
        if proposal["source_content_hash"] and proposal["source_content_hash"] != proposal["content_hash"]:
            conn.execute(
                "UPDATE plot_state_proposals SET status='stale', updated_at=?, resolved_at=? WHERE id=?",
                (now, now, pid),
            )
            return {"stale": True}
        previous = _decode_plot_state(proposal["state_json"])
        normalized = normalize_plot_state(state, previous)
        if not plot_state_has_content(normalized):
            return {"empty_state": True}
        summary = proposal["change_summary"] if change_summary is None else (change_summary or "").strip()[:1400]
        proof = proposal["evidence"] if evidence is None else (evidence or "").strip()[:4000]
        edited = state is not None or change_summary is not None or evidence is not None
        cur = conn.execute(
            "INSERT INTO plot_state_versions(work_id,chapter_id,state_json,change_summary,evidence,source,proposal_id,"
            "source_content_hash,stale,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (proposal["work_id"], proposal["chapter_id"], json.dumps(normalized, ensure_ascii=False),
             summary, proof, "ai_edited" if edited else "ai_confirmed", pid,
             # Acceptance is an author decision. Keep proposal_id for provenance, but do not
             # make the confirmed story state disappear after the chapter is edited.
             "", 0, now),
        )
        conn.execute(
            "UPDATE plot_state_proposals SET status='accepted', updated_at=?, resolved_at=? WHERE id=?",
            (now, now, pid),
        )
        version = conn.execute(
            "SELECT v.id, v.work_id, v.chapter_id, v.state_json, v.change_summary, v.evidence, "
            "v.source, v.proposal_id, v.source_content_hash, v.stale, v.created_at, "
            "c.title AS chapter_title, c.ord AS chapter_ord, "
            "CASE WHEN v.source_content_hash='' OR v.source_content_hash=c.content_hash THEN 1 ELSE 0 END AS source_hash_matches "
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
        "v.source, v.proposal_id, v.source_content_hash, v.stale, v.created_at, "
        "c.title AS chapter_title, c.ord AS chapter_ord, "
        "CASE WHEN v.source_content_hash='' OR v.source_content_hash=c.content_hash THEN 1 ELSE 0 END AS source_hash_matches "
        "FROM entity_state_versions v "
        "JOIN chapters c ON c.id=v.chapter_id "
        "JOIN chapters target ON target.id=? "
        f"WHERE v.entity_id=? AND c.deleted_at IS NULL AND target.deleted_at IS NULL "
        f"AND c.work_id=target.work_id AND c.ord {op} target.ord AND v.stale=0 "
        "AND (v.source_content_hash='' OR v.source_content_hash=c.content_hash) "
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
            "SELECT id, name, kind, summary, detail, image_prompt, "
            "CASE WHEN image_path<>'' THEN 1 ELSE 0 END AS has_image, image_updated_at, "
            "created_at, updated_at "
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


def get_entity_image_record(eid, user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT e.id, e.work_id, e.name, e.kind, e.summary, e.detail, e.image_path, "
            "e.image_prompt, e.image_updated_at FROM entities e JOIN works w ON w.id=e.work_id "
            "WHERE e.id=? AND w.user_id=?", (eid, user_id),
        ).fetchone()
        return dict(row) if row else None


def save_entity_image(eid, user_id, image_path, prompt):
    now = time.time()
    with get_conn() as conn:
        if not _entity_owned(conn, eid, user_id):
            return None
        old = conn.execute("SELECT image_path FROM entities WHERE id=?", (eid,)).fetchone()
        conn.execute(
            "UPDATE entities SET image_path=?, image_prompt=?, image_updated_at=?, updated_at=? WHERE id=?",
            (image_path or "", (prompt or "").strip()[:8000], now, now, eid),
        )
        return {"old_path": old["image_path"] if old else "", "image_updated_at": now}


def clear_entity_image(eid, user_id):
    now = time.time()
    with get_conn() as conn:
        if not _entity_owned(conn, eid, user_id):
            return None
        old = conn.execute("SELECT image_path FROM entities WHERE id=?", (eid,)).fetchone()
        conn.execute(
            "UPDATE entities SET image_path='', image_updated_at=NULL, updated_at=? WHERE id=?",
            (now, eid),
        )
        return old["image_path"] if old else ""


def list_work_entity_image_paths(wid, user_id):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        return [row["image_path"] for row in conn.execute(
            "SELECT image_path FROM entities WHERE work_id=? AND image_path<>''", (wid,),
        )]


def delete_entity(eid, user_id):
    with get_conn() as conn:
        if not _entity_owned(conn, eid, user_id):
            return False
        memory_ids = [row["memory_id"] for row in conn.execute(
            "SELECT memory_id FROM story_memory_entity_refs WHERE entity_id=?", (eid,)
        )]
        conn.execute("DELETE FROM entity_state_versions WHERE entity_id=?", (eid,))
        conn.execute("DELETE FROM entity_state_proposals WHERE entity_id=?", (eid,))
        conn.execute("DELETE FROM entity_relations WHERE from_entity_id=? OR to_entity_id=?", (eid, eid))
        conn.execute("DELETE FROM story_memory_entity_refs WHERE entity_id=?", (eid,))
        conn.execute("DELETE FROM entities WHERE id=?", (eid,))
        for memory_id in memory_ids:
            _sync_story_memory_fts(conn, memory_id)
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
            "v.source, v.proposal_id, v.source_content_hash, v.stale, v.created_at, "
            "c.title AS chapter_title, c.ord AS chapter_ord, c.deleted_at AS chapter_deleted_at, "
            "CASE WHEN v.source_content_hash='' OR v.source_content_hash=c.content_hash THEN 1 ELSE 0 END AS source_hash_matches "
            "FROM entity_state_versions v JOIN chapters c ON c.id=v.chapter_id "
            "WHERE v.entity_id=? ORDER BY c.ord DESC, v.id DESC",
            (eid,),
        ).fetchall()
        proposal_rows = []
        if at_chapter_id is not None:
            proposal_rows = conn.execute(
                "SELECT p.id, p.entity_id, p.chapter_id, p.state_json, p.change_summary, p.evidence, "
                "p.status, p.source_content_hash, p.created_at, p.updated_at, p.resolved_at, "
                "CASE WHEN p.source_content_hash='' OR p.source_content_hash=c.content_hash THEN 1 ELSE 0 END AS source_hash_matches "
                "FROM entity_state_proposals p JOIN chapters c ON c.id=p.chapter_id "
                "WHERE p.entity_id=? AND p.chapter_id=? "
                "ORDER BY CASE p.status WHEN 'pending' THEN 0 ELSE 1 END, p.id DESC",
                (eid, at_chapter_id),
            ).fetchall()
        relation_rows = conn.execute(
            "SELECT r.id, r.from_entity_id, r.to_entity_id, r.relation, r.detail, r.status, "
            "a.name AS from_name, b.name AS to_name FROM entity_relations r "
            "JOIN entities a ON a.id=r.from_entity_id JOIN entities b ON b.id=r.to_entity_id "
            "WHERE r.work_id=? AND (r.from_entity_id=? OR r.to_entity_id=?) "
            "ORDER BY r.updated_at DESC, r.id DESC",
            (entity["work_id"], eid, eid),
        ).fetchall()
        return {
            "entity": entity,
            "target_chapter": target,
            "current_state": version["state"] if version else normalize_character_state({}),
            "state_version": version,
            "history": [_state_version_payload(row) for row in history_rows],
            "proposals": [_state_proposal_payload(row) for row in proposal_rows],
            "relations": [dict(row) for row in relation_rows],
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
        chapter_source = _chapter_memory_source(conn, chapter_id, entity["work_id"])
        if not chapter_source:
            return {"invalid_chapter": True}
        normalized = normalize_character_state(state)
        if not character_state_has_content(normalized):
            return {"empty_state": True}
        cur = conn.execute(
            "INSERT INTO entity_state_versions(entity_id,chapter_id,state_json,change_summary,evidence,source,proposal_id,"
            "source_content_hash,stale,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (eid, chapter_id, json.dumps(normalized, ensure_ascii=False), (change_summary or "").strip()[:1000],
             (evidence or "").strip()[:3000], source, proposal_id, "", 0, now),
        )
        row = conn.execute(
            "SELECT v.id, v.entity_id, v.chapter_id, v.state_json, v.change_summary, v.evidence, "
            "v.source, v.proposal_id, v.source_content_hash, v.stale, v.created_at, "
            "c.title AS chapter_title, c.ord AS chapter_ord, "
            "CASE WHEN v.source_content_hash='' OR v.source_content_hash=c.content_hash THEN 1 ELSE 0 END AS source_hash_matches "
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
        chapter_source = _chapter_memory_source(conn, chapter_id, entity["work_id"])
        if not chapter_source:
            return {"invalid_chapter": True}
        normalized = normalize_character_state(state)
        if not character_state_has_content(normalized):
            return {"empty_state": True}
        payload = (json.dumps(normalized, ensure_ascii=False), (change_summary or "").strip()[:1000],
                   (evidence or "").strip()[:3000], chapter_source["content_hash"] or _content_fingerprint(""), now)
        existing = conn.execute(
            "SELECT id FROM entity_state_proposals WHERE entity_id=? AND chapter_id=? AND status='pending' "
            "ORDER BY id DESC LIMIT 1", (eid, chapter_id),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE entity_state_proposals SET state_json=?, change_summary=?, evidence=?, source_content_hash=?, "
                "status='pending', updated_at=?, resolved_at=NULL WHERE id=?",
                (*payload, existing["id"]),
            )
            pid = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO entity_state_proposals(entity_id,chapter_id,state_json,change_summary,evidence,status,source_content_hash,created_at,updated_at) "
                "VALUES(?,?,?,?,?,'pending',?,?,?)",
                (eid, chapter_id, payload[0], payload[1], payload[2], payload[3], now, now),
            )
            pid = cur.lastrowid
        row = conn.execute(
            "SELECT p.id, p.entity_id, p.chapter_id, p.state_json, p.change_summary, p.evidence, p.status, "
            "p.source_content_hash, p.created_at, p.updated_at, p.resolved_at, "
            "CASE WHEN p.source_content_hash='' OR p.source_content_hash=c.content_hash THEN 1 ELSE 0 END AS source_hash_matches "
            "FROM entity_state_proposals p JOIN chapters c ON c.id=p.chapter_id WHERE p.id=?", (pid,)
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
            "p.status, p.source_content_hash, p.created_at, p.updated_at, p.resolved_at, e.name AS entity_name, "
            "CASE WHEN p.source_content_hash='' OR p.source_content_hash=c.content_hash THEN 1 ELSE 0 END AS source_hash_matches "
            "FROM entity_state_proposals p JOIN entities e ON e.id=p.entity_id JOIN chapters c ON c.id=p.chapter_id "
            "WHERE p.chapter_id=? ORDER BY CASE p.status WHEN 'pending' THEN 0 ELSE 1 END, p.id DESC",
            (chapter_id,),
        ).fetchall()
        return {"chapter": dict(chapter), "proposals": [_state_proposal_payload(row) for row in rows]}


def accept_character_state_proposal(pid, user_id, state=None, change_summary=None, evidence=None):
    now = time.time()
    with get_conn() as conn:
        proposal = conn.execute(
            "SELECT p.id, p.entity_id, p.chapter_id, p.state_json, p.change_summary, p.evidence, p.status, "
            "p.source_content_hash, c.content_hash FROM entity_state_proposals p JOIN entities e ON e.id=p.entity_id "
            "JOIN works w ON w.id=e.work_id JOIN chapters c ON c.id=p.chapter_id WHERE p.id=? AND w.user_id=?", (pid, user_id),
        ).fetchone()
        if not proposal:
            return None
        if proposal["status"] != "pending":
            return {"resolved": True}
        if proposal["source_content_hash"] and proposal["source_content_hash"] != proposal["content_hash"]:
            conn.execute(
                "UPDATE entity_state_proposals SET status='stale', updated_at=?, resolved_at=? WHERE id=?",
                (now, now, pid),
            )
            return {"stale": True}
        previous = _decode_character_state(proposal["state_json"])
        normalized = normalize_character_state(state, previous)
        if not character_state_has_content(normalized):
            return {"empty_state": True}
        summary = proposal["change_summary"] if change_summary is None else (change_summary or "").strip()[:1000]
        proof = proposal["evidence"] if evidence is None else (evidence or "").strip()[:3000]
        edited = state is not None or change_summary is not None or evidence is not None
        cur = conn.execute(
            "INSERT INTO entity_state_versions(entity_id,chapter_id,state_json,change_summary,evidence,source,proposal_id,"
            "source_content_hash,stale,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (proposal["entity_id"], proposal["chapter_id"], json.dumps(normalized, ensure_ascii=False),
             summary, proof, "ai_edited" if edited else "ai_confirmed", pid,
             # Once the author accepts it, this becomes an authored state snapshot rather
             # than a disposable extraction tied to one exact source revision.
             "", 0, now),
        )
        conn.execute(
            "UPDATE entity_state_proposals SET status='accepted', updated_at=?, resolved_at=? WHERE id=?",
            (now, now, pid),
        )
        version = conn.execute(
            "SELECT v.id, v.entity_id, v.chapter_id, v.state_json, v.change_summary, v.evidence, "
            "v.source, v.proposal_id, v.source_content_hash, v.stale, v.created_at, "
            "c.title AS chapter_title, c.ord AS chapter_ord, "
            "CASE WHEN v.source_content_hash='' OR v.source_content_hash=c.content_hash THEN 1 ELSE 0 END AS source_hash_matches "
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
            "branch_of_chapter_id, branch_from_revision_id, content_revision, analysis_status, "
            "analysis_reason, analysis_checked_at, "
            "(SELECT COUNT(*) FROM story_memory_items m WHERE m.chapter_id=chapters.id "
            "AND m.status='confirmed' AND m.stale=0) AS confirmed_memory_count "
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
            "INSERT INTO chapters(work_id,title,ord,content,notes,content_hash,content_revision,analysis_status,"
            "analysis_reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (wid, title, ord_, "", "", _content_fingerprint(""), 1, "fresh", "", now, now),
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
            "workflow_summary, workflow_checked_at, branch_of_chapter_id, branch_from_revision_id, "
            "content_hash, content_revision, analysis_status, analysis_reason, analysis_checked_at "
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
            "workflow_checked_at, updated_at, content_revision, analysis_status, analysis_reason, analysis_checked_at "
            "FROM chapters WHERE id=? AND deleted_at IS NULL",
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
    if not row:
        return None
    item = dict(row)
    item["source_current"] = not bool(item.get("source_content_hash")) or bool(item.get("source_hash_matches", 1))
    item["is_stale"] = bool(item.get("stale")) or not item["source_current"]
    item.pop("source_hash_matches", None)
    return item


def list_chapter_consistency_alerts(cid, user_id):
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        rows = conn.execute(
            "SELECT a.id, a.chapter_id, a.category, a.severity, a.title, a.detail, a.evidence, a.suggestion, a.status, "
            "a.source_content_hash, a.stale, a.created_at, a.updated_at, a.resolved_at, "
            "CASE WHEN a.source_content_hash='' OR a.source_content_hash=c.content_hash THEN 1 ELSE 0 END AS source_hash_matches "
            "FROM chapter_consistency_alerts a JOIN chapters c ON c.id=a.chapter_id "
            "WHERE a.chapter_id=? ORDER BY CASE a.status WHEN 'open' THEN 0 ELSE 1 END, "
            "CASE a.severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, a.id DESC",
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
        chapter = conn.execute("SELECT content_hash FROM chapters WHERE id=?", (cid,)).fetchone()
        source_hash = (chapter["content_hash"] if chapter else "") or _content_fingerprint("")
        conn.execute("DELETE FROM chapter_consistency_alerts WHERE chapter_id=? AND status='open'", (cid,))
        for item in normalized:
            conn.execute(
                "INSERT INTO chapter_consistency_alerts(chapter_id,category,severity,title,detail,evidence,suggestion,status,"
                "source_content_hash,stale,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'open',?,0,?,?)",
                (cid, item["category"], item["severity"], item["title"], item["detail"],
                 item["evidence"], item["suggestion"], source_hash, now, now),
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


def _persist_chapter_content(conn, cid, content, now=None, reason="正文已修改，需重新分析", invalidate=False):
    """唯一的正文写入点：同步源版本，并让派生资料不再误当作当前事实。"""
    now = now or time.time()
    row = conn.execute(
        "SELECT content, content_hash, content_revision FROM chapters WHERE id=?", (cid,)
    ).fetchone()
    if not row:
        return None
    content = content if isinstance(content, str) else ""
    old_content = row["content"] or ""
    old_hash = row["content_hash"] or _content_fingerprint(old_content)
    new_hash = _content_fingerprint(content)
    if content == old_content and new_hash == old_hash:
        return {"changed": False, "content_hash": old_hash, "content_revision": row["content_revision"] or 1}
    revision = max(1, int(row["content_revision"] or 1)) + 1
    conn.execute(
        "UPDATE chapters SET content=?, content_hash=?, content_revision=?, analysis_status='needs_review', "
        "analysis_reason=?, analysis_checked_at=NULL, updated_at=? WHERE id=?",
        (content, new_hash, revision, (reason or "正文已修改，需重新分析")[:240], now, cid),
    )
    if invalidate:
        _invalidate_chapter_derived_state(conn, cid, reason)
    return {"changed": True, "content_hash": new_hash, "content_revision": revision}


def update_chapter(cid, user_id, title, content, notes):
    now = time.time()
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return False
        if content is not None:
            _persist_chapter_content(conn, cid, content, now, "正文已修改，需重新分析")
        conn.execute(
            "UPDATE chapters SET title=COALESCE(?,title), notes=COALESCE(?,notes), updated_at=? WHERE id=?",
            (title, notes, now, cid),
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
        _persist_chapter_content(conn, cid, content, now, "AI 替换正文，需要重新分析", invalidate=True)
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
        _persist_chapter_content(conn, cid, content, now, "AI 修改正文，需要重新分析", invalidate=True)
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
        conn.execute("DELETE FROM agent_sessions WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM agent_conversations WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM entity_state_versions WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM entity_state_proposals WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM plot_state_versions WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM plot_state_proposals WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM chapter_consistency_alerts WHERE chapter_id=?", (cid,))
        _delete_story_memories_for_chapter(conn, cid)
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
        _persist_chapter_content(conn, cid, left, now, "拆分章节，需要重新分析", invalidate=True)
        conn.execute(
            "UPDATE chapters SET ord=ord+1 WHERE work_id=? AND ord>?",
            (chap["work_id"], chap["ord"]),
        )
        cur = conn.execute(
            "INSERT INTO chapters(work_id,title,ord,content,notes,content_hash,content_revision,analysis_status,"
            "analysis_reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (chap["work_id"], title, chap["ord"] + 1, right, "", _content_fingerprint(right), 1,
             "needs_review" if right.strip() else "fresh", "章节由拆分创建，需要重新分析" if right.strip() else "", now, now),
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
        _persist_chapter_content(conn, cid, content, now, "正文新增内容，需要重新分析")
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
        _persist_chapter_content(conn, cid, content, time.time(), "撤销段落，需要重新分析")
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
        _persist_chapter_content(conn, cid, rev["content"] or "", now, "从历史版本恢复，需要重新分析", invalidate=True)
        conn.execute("UPDATE chapters SET title=?, updated_at=? WHERE id=?", (rev["title"], now, cid))
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
        branch_content = rev["content"] or ""
        cur = conn.execute(
            "INSERT INTO chapters(work_id,title,ord,content,notes,workflow_status,workflow_goal,"
            "branch_of_chapter_id,branch_from_revision_id,content_hash,content_revision,analysis_status,analysis_reason,"
            "created_at,updated_at) VALUES(?,?,?,?,?,'drafting',?,?,?,?,?,?,?,?,?)",
            (source["work_id"], title, ord_, branch_content, source["notes"] or "",
             source["workflow_goal"] or "", cid, rid, _content_fingerprint(branch_content), 1,
             "needs_review" if branch_content.strip() else "fresh",
             "分支稿需要独立分析" if branch_content.strip() else "", now, now),
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
                _persist_chapter_content(
                    conn, old_id, content, now, "从整本历史版本恢复，需要重新分析", invalidate=True
                )
                conn.execute(
                    "UPDATE chapters SET title=?, ord=?, notes=?, workflow_status=?, workflow_goal=?, "
                    "workflow_summary=?, workflow_checked_at=?, deleted_at=NULL, updated_at=? WHERE id=?",
                    (title, ord_, notes, workflow_status, workflow_goal[:2000], workflow_summary[:4000],
                     item.get("workflow_checked_at"), now, old_id),
                )
                restored += 1
            else:
                conn.execute(
                    "INSERT INTO chapters(work_id,title,ord,content,notes,workflow_status,workflow_goal,workflow_summary,"
                    "workflow_checked_at,branch_of_chapter_id,branch_from_revision_id,content_hash,content_revision,analysis_status,"
                    "analysis_reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (wid, title, ord_, content, notes, workflow_status, workflow_goal[:2000], workflow_summary[:4000],
                     item.get("workflow_checked_at"), item.get("branch_of_chapter_id"), item.get("branch_from_revision_id"),
                     _content_fingerprint(content), 1, "needs_review" if content.strip() else "fresh",
                     "从整本历史版本恢复，需要重新分析" if content.strip() else "", now, now),
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
