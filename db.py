"""SQLite 数据访问。用户 → 作品 → 章节 → 段落历史 / 修订版本。"""
import sqlite3
import os
import time
import json
import secrets
import hashlib
import re
from difflib import SequenceMatcher
from datetime import datetime
from contextlib import contextmanager
from contextvars import ContextVar

import config
import builtin_skills

DB_PATH = config.DB_PATH
_active_transaction = ContextVar("writehtml_active_db_transaction", default=None)


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
PRODUCTION_CARD_CATEGORIES = ("rule", "location", "skill", "item", "organization")
PRODUCTION_CARD_CATEGORY_LABELS = {
    "rule": "规则", "location": "地点", "skill": "技能",
    "item": "道具", "organization": "组织",
}
PRODUCTION_SCOPE_TYPES = ("global", "chapter_range", "scene")
PRODUCTION_PROPOSAL_TYPES = ("new_card", "card_update", "card_state", "scene")
STORY_PLAN_NODE_TYPES = ("book", "volume", "arc", "chapter", "scene", "subplot", "foreshadow")
STORY_PLAN_STATUSES = ("planned", "committed", "abandoned")
STORY_PLAN_CONTEXT_POLICIES = ("auto", "planning_only", "writing_range", "never")
STORY_PLAN_REALIZATION_STATUSES = ("pending", "partial", "realized", "deviated")
MAX_LLM_MODELS = 20
MAX_LLM_MODEL_ID_LENGTH = 160
DEFAULT_MODEL_CONTEXT_WINDOW_TOKENS = max(1, int(config.AGENT_CONTEXT_WINDOW_TOKENS))
DEFAULT_WORLD_STATE_CONTENT_CHARS = max(1, int(config.WORLD_STATE_CONTENT_CHARS))
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


def _positive_model_limit(value, default):
    if isinstance(value, bool):
        return default
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return value if value > 0 else default


def _decode_model_runtime_options(raw):
    try:
        values = json.loads(raw or "{}")
    except Exception:
        values = {}
    if not isinstance(values, dict):
        return {}
    result = {}
    for raw_model, raw_options in values.items():
        if not isinstance(raw_model, str) or not isinstance(raw_options, dict):
            continue
        model = raw_model.strip()[:MAX_LLM_MODEL_ID_LENGTH]
        if not model:
            continue
        result[model] = {
            "context_window_tokens": _positive_model_limit(
                raw_options.get("context_window_tokens"), DEFAULT_MODEL_CONTEXT_WINDOW_TOKENS,
            ),
            "world_state_content_chars": _positive_model_limit(
                raw_options.get("world_state_content_chars"), DEFAULT_WORLD_STATE_CONTENT_CHARS,
            ),
        }
    return result


def model_runtime_limits(options, model):
    """Return effective limits for one model without guessing provider capabilities."""
    options = options if isinstance(options, dict) else {}
    configured = options.get((model or "").strip())
    configured = configured if isinstance(configured, dict) else {}
    return {
        "context_window_tokens": _positive_model_limit(
            configured.get("context_window_tokens"), DEFAULT_MODEL_CONTEXT_WINDOW_TOKENS,
        ),
        "world_state_content_chars": _positive_model_limit(
            configured.get("world_state_content_chars"), DEFAULT_WORLD_STATE_CONTENT_CHARS,
        ),
    }


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
    active = _active_transaction.get()
    if active is not None:
        yield active
        return
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def atomic_transaction(immediate=False):
    """Share one SQLite transaction across existing repository helpers."""
    active = _active_transaction.get()
    if active is not None:
        yield active
        return
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA synchronous=NORMAL")
    token = _active_transaction.set(conn)
    try:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _active_transaction.reset(token)
        conn.close()


class StaleWorldStateError(RuntimeError):
    pass


@contextmanager
def world_state_transaction(chapter_id, user_id, expected_content_hash):
    """Atomically validate and commit every derived view from one analysis."""
    with atomic_transaction(immediate=True) as conn:
        row = conn.execute(
            "SELECT c.content_hash FROM chapters c JOIN works w ON w.id=c.work_id "
            "WHERE c.id=? AND w.user_id=? AND c.deleted_at IS NULL", (chapter_id, user_id),
        ).fetchone()
        if not row or (row["content_hash"] or "") != (expected_content_hash or ""):
            raise StaleWorldStateError("chapter content changed")
        yield conn


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


def _migration_unified_materials(conn):
    """Persistent reference sources shared by writing, disassembly and Agent context."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS work_material_settings (
            work_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            use_story_memory INTEGER NOT NULL DEFAULT 1,
            use_reference_projects INTEGER NOT NULL DEFAULT 1,
            use_style_profile INTEGER NOT NULL DEFAULT 1,
            use_inspirations INTEGER NOT NULL DEFAULT 1,
            use_reference_documents INTEGER NOT NULL DEFAULT 1,
            style_strength TEXT NOT NULL DEFAULT 'balanced',
            updated_at REAL NOT NULL,
            FOREIGN KEY(work_id) REFERENCES works(id)
        );
        CREATE TABLE IF NOT EXISTS work_reference_mounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            work_id INTEGER NOT NULL,
            reference_work_id INTEGER NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            use_style INTEGER NOT NULL DEFAULT 1,
            use_plot INTEGER NOT NULL DEFAULT 1,
            use_world INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(work_id, reference_work_id),
            FOREIGN KEY(work_id) REFERENCES works(id),
            FOREIGN KEY(reference_work_id) REFERENCES works(id)
        );
        CREATE TABLE IF NOT EXISTS work_style_profiles (
            work_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            source_kind TEXT NOT NULL DEFAULT 'manual',
            source_label TEXT DEFAULT '',
            source_job_id INTEGER,
            profile_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(work_id) REFERENCES works(id)
        );
        CREATE TABLE IF NOT EXISTS work_reference_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            work_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            tags TEXT DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            pinned INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(work_id, content_hash),
            FOREIGN KEY(work_id) REFERENCES works(id)
        );
        CREATE TABLE IF NOT EXISTS disassembly_material_extractions (
            job_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            work_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            result_json TEXT NOT NULL DEFAULT '{}',
            inspiration_ids_json TEXT NOT NULL DEFAULT '[]',
            error TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(job_id) REFERENCES book_disassembly_jobs(id),
            FOREIGN KEY(work_id) REFERENCES works(id)
        );
        CREATE INDEX IF NOT EXISTS idx_material_settings_user
            ON work_material_settings(user_id, work_id);
        CREATE INDEX IF NOT EXISTS idx_reference_mounts_work
            ON work_reference_mounts(work_id, enabled, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_reference_mounts_source
            ON work_reference_mounts(reference_work_id, enabled);
        CREATE INDEX IF NOT EXISTS idx_reference_documents_work
            ON work_reference_documents(work_id, enabled, pinned DESC, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_material_extractions_user
            ON disassembly_material_extractions(user_id, updated_at DESC);
        """
    )


def _migration_character_image_library(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS entity_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            work_id INTEGER NOT NULL,
            entity_id INTEGER NOT NULL,
            category TEXT NOT NULL DEFAULT 'characters',
            image_path TEXT NOT NULL,
            prompt TEXT NOT NULL DEFAULT '',
            style TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            size TEXT NOT NULL DEFAULT '',
            selected INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(image_path),
            FOREIGN KEY(work_id) REFERENCES works(id),
            FOREIGN KEY(entity_id) REFERENCES entities(id)
        );
        CREATE INDEX IF NOT EXISTS idx_entity_images_entity
            ON entity_images(entity_id, selected DESC, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_entity_images_work
            ON entity_images(work_id, category, created_at DESC);
        """
    )
    now = time.time()
    conn.execute(
        "INSERT OR IGNORE INTO entity_images(user_id,work_id,entity_id,category,image_path,prompt,selected,created_at,updated_at) "
        "SELECT w.user_id,e.work_id,e.id,'characters',e.image_path,e.image_prompt,1,"
        "COALESCE(e.image_updated_at,?),COALESCE(e.image_updated_at,?) FROM entities e JOIN works w ON w.id=e.work_id "
        "WHERE e.image_path<>''",
        (now, now),
    )


def _migration_production_canvas(conn):
    """Chapter-aware production canvas: canonical cards, scenes and source-backed changes."""
    _add_col(conn, "chapters", "production_analysis_status", "TEXT DEFAULT 'unreviewed'")
    _add_col(conn, "chapters", "production_analysis_hash", "TEXT DEFAULT ''")
    _add_col(conn, "chapters", "production_analyzed_at", "REAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS production_work_settings (
            work_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            evidence_enabled INTEGER NOT NULL DEFAULT 1,
            auto_analyze_on_leave INTEGER NOT NULL DEFAULT 1,
            custom_fields_json TEXT NOT NULL DEFAULT '[]',
            updated_at REAL NOT NULL,
            FOREIGN KEY(work_id) REFERENCES works(id)
        );
        CREATE TABLE IF NOT EXISTS production_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            summary TEXT DEFAULT '',
            detail TEXT DEFAULT '',
            attributes_json TEXT NOT NULL DEFAULT '{}',
            truth_json TEXT NOT NULL DEFAULT '{}',
            reader_state TEXT DEFAULT '',
            scope_type TEXT NOT NULL DEFAULT 'global',
            scope_start_chapter_id INTEGER,
            scope_end_chapter_id INTEGER,
            scope_scene_id INTEGER,
            status TEXT NOT NULL DEFAULT 'confirmed',
            source_chapter_id INTEGER,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(work_id) REFERENCES works(id)
        );
        CREATE TABLE IF NOT EXISTS production_card_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            state_json TEXT NOT NULL DEFAULT '{}',
            change_summary TEXT DEFAULT '',
            evidence TEXT DEFAULT '',
            evidence_start INTEGER,
            evidence_end INTEGER,
            source_content_hash TEXT DEFAULT '',
            source TEXT NOT NULL DEFAULT 'manual',
            stale INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            FOREIGN KEY(card_id) REFERENCES production_cards(id),
            FOREIGN KEY(chapter_id) REFERENCES chapters(id)
        );
        CREATE TABLE IF NOT EXISTS production_scenes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter_id INTEGER NOT NULL,
            ord INTEGER NOT NULL,
            title TEXT NOT NULL,
            summary TEXT DEFAULT '',
            time_label TEXT DEFAULT '',
            location_card_id INTEGER,
            goal TEXT DEFAULT '',
            conflict TEXT DEFAULT '',
            outcome TEXT DEFAULT '',
            refs_json TEXT NOT NULL DEFAULT '{}',
            evidence TEXT DEFAULT '',
            evidence_start INTEGER,
            evidence_end INTEGER,
            source_content_hash TEXT DEFAULT '',
            source TEXT NOT NULL DEFAULT 'manual',
            stale INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(chapter_id) REFERENCES chapters(id)
        );
        CREATE TABLE IF NOT EXISTS production_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            scene_id INTEGER,
            proposal_type TEXT NOT NULL,
            target_id INTEGER,
            category TEXT DEFAULT '',
            name TEXT DEFAULT '',
            severity TEXT NOT NULL DEFAULT 'major',
            before_json TEXT NOT NULL DEFAULT '{}',
            after_json TEXT NOT NULL DEFAULT '{}',
            change_summary TEXT DEFAULT '',
            evidence TEXT DEFAULT '',
            evidence_start INTEGER,
            evidence_end INTEGER,
            confidence REAL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            source_content_hash TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            resolved_at REAL,
            FOREIGN KEY(work_id) REFERENCES works(id),
            FOREIGN KEY(chapter_id) REFERENCES chapters(id)
        );
        CREATE TABLE IF NOT EXISTS production_canvas_layouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_id INTEGER NOT NULL,
            chapter_id INTEGER,
            scope_key TEXT NOT NULL,
            data_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(work_id, scope_key),
            FOREIGN KEY(work_id) REFERENCES works(id)
        );
        CREATE TABLE IF NOT EXISTS production_impact_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_id INTEGER NOT NULL,
            source_chapter_id INTEGER NOT NULL,
            affected_chapter_id INTEGER NOT NULL,
            summary TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at REAL NOT NULL,
            resolved_at REAL,
            FOREIGN KEY(work_id) REFERENCES works(id)
        );
        CREATE INDEX IF NOT EXISTS idx_production_cards_work
            ON production_cards(work_id, status, category, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_production_versions_card_chapter
            ON production_card_versions(card_id, chapter_id, id);
        CREATE INDEX IF NOT EXISTS idx_production_scenes_chapter
            ON production_scenes(chapter_id, ord, id);
        CREATE INDEX IF NOT EXISTS idx_production_proposals_chapter
            ON production_proposals(chapter_id, status, id);
        CREATE INDEX IF NOT EXISTS idx_production_impacts_chapter
            ON production_impact_flags(affected_chapter_id, status, id);
        """
    )


def _migration_integrity_and_world_state_snapshots(conn):
    _add_col(conn, "production_cards", "introduced_at_ord", "INTEGER")
    _add_col(conn, "production_cards", "source_chapter_deleted", "INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "UPDATE production_cards SET introduced_at_ord=(SELECT ord FROM chapters WHERE id=source_chapter_id) "
        "WHERE introduced_at_ord IS NULL AND source_chapter_id IS NOT NULL"
    )
    # Repair orphan rows created by legacy delete order before stronger integrity checks are introduced.
    conn.execute("DELETE FROM production_scenes WHERE chapter_id NOT IN (SELECT id FROM chapters)")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS world_state_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter_id INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            analyzer_version TEXT NOT NULL,
            model TEXT NOT NULL DEFAULT '',
            result_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(chapter_id, content_hash, analyzer_version, model),
            FOREIGN KEY(chapter_id) REFERENCES chapters(id)
        );
        CREATE INDEX IF NOT EXISTS idx_world_state_analysis_chapter
            ON world_state_analyses(chapter_id, created_at DESC);
        """
    )


def _migration_reference_document_fts(conn):
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS reference_document_fts USING fts5("
        "name,tags,content,user_id UNINDEXED,work_id UNINDEXED,tokenize='trigram')"
    )
    conn.execute("DELETE FROM reference_document_fts")
    conn.execute(
        "INSERT INTO reference_document_fts(rowid,name,tags,content,user_id,work_id) "
        "SELECT id,name,tags,content,user_id,work_id FROM work_reference_documents"
    )


def _migration_world_state_analysis_inputs(conn):
    # These rows are disposable model-output caches. Legacy rows cannot be
    # trusted because their key omitted most of the analysis prompt inputs.
    conn.executescript(
        """
        DROP TABLE IF EXISTS world_state_analyses;
        CREATE TABLE world_state_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chapter_id INTEGER NOT NULL,
            input_hash TEXT NOT NULL,
            source_content_hash TEXT NOT NULL,
            analyzer_version TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            generation INTEGER NOT NULL DEFAULT 1,
            result_json TEXT NOT NULL,
            applied_at REAL,
            created_at REAL NOT NULL,
            UNIQUE(chapter_id, input_hash, analyzer_version, provider, model, generation),
            FOREIGN KEY(chapter_id) REFERENCES chapters(id)
        );
        CREATE INDEX idx_world_state_analysis_chapter
            ON world_state_analyses(chapter_id, created_at DESC);
        CREATE INDEX idx_world_state_analysis_lookup
            ON world_state_analyses(chapter_id, input_hash, analyzer_version, provider, model, generation DESC);
        """
    )


def _migration_model_runtime_options(conn):
    """Keep model-specific context settings separate from shared provider credentials."""
    _add_col(conn, "user_settings", "llm_model_options_json", "TEXT DEFAULT '{}'")


def _migration_story_plan(conn):
    """Separate author plans from canonical facts and prose-derived state."""
    _add_col(conn, "chapters", "outcome_summary", "TEXT DEFAULT ''")
    _add_col(conn, "chapters", "outcome_source_hash", "TEXT DEFAULT ''")
    _add_col(conn, "chapters", "outcome_source_revision", "INTEGER")
    _add_col(conn, "chapters", "outcome_updated_at", "REAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS story_plan_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_id INTEGER NOT NULL,
            parent_id INTEGER,
            node_type TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT DEFAULT '',
            detail TEXT DEFAULT '',
            goal TEXT DEFAULT '',
            conflict TEXT DEFAULT '',
            expected_outcome TEXT DEFAULT '',
            context_summary TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'planned',
            context_policy TEXT NOT NULL DEFAULT 'auto',
            chapter_id INTEGER,
            scope_start_chapter_id INTEGER,
            scope_end_chapter_id INTEGER,
            ord INTEGER NOT NULL DEFAULT 1,
            source_sandbox_id INTEGER,
            source_node_id TEXT DEFAULT '',
            revision INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            deleted_at REAL,
            FOREIGN KEY(work_id) REFERENCES works(id),
            FOREIGN KEY(parent_id) REFERENCES story_plan_nodes(id),
            FOREIGN KEY(chapter_id) REFERENCES chapters(id)
        );
        CREATE TABLE IF NOT EXISTS story_plan_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_node_id INTEGER NOT NULL,
            target_type TEXT NOT NULL,
            target_id INTEGER NOT NULL,
            label TEXT DEFAULT '',
            created_at REAL NOT NULL,
            UNIQUE(plan_node_id, target_type, target_id),
            FOREIGN KEY(plan_node_id) REFERENCES story_plan_nodes(id)
        );
        CREATE TABLE IF NOT EXISTS story_plan_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_node_id INTEGER NOT NULL,
            revision INTEGER NOT NULL,
            snapshot_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(plan_node_id, revision),
            FOREIGN KEY(plan_node_id) REFERENCES story_plan_nodes(id)
        );
        CREATE TABLE IF NOT EXISTS story_plan_realizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_node_id INTEGER NOT NULL,
            plan_revision INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            source_content_hash TEXT DEFAULT '',
            source_content_revision INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            evidence TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            source TEXT NOT NULL DEFAULT 'manual',
            stale INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(plan_node_id, chapter_id, source_content_hash),
            FOREIGN KEY(plan_node_id) REFERENCES story_plan_nodes(id),
            FOREIGN KEY(chapter_id) REFERENCES chapters(id)
        );
        CREATE INDEX IF NOT EXISTS idx_story_plan_work_parent
            ON story_plan_nodes(work_id, parent_id, status, ord, id);
        CREATE INDEX IF NOT EXISTS idx_story_plan_chapter
            ON story_plan_nodes(work_id, chapter_id, status);
        CREATE INDEX IF NOT EXISTS idx_story_plan_source
            ON story_plan_nodes(source_sandbox_id, source_node_id);
        CREATE INDEX IF NOT EXISTS idx_story_plan_realization_chapter
            ON story_plan_realizations(chapter_id, stale, status);
        """
    )


def _migration_story_plan_refinement(conn):
    _add_col(conn, "story_plan_realizations", "plan_content_hash", "TEXT NOT NULL DEFAULT ''")
    _add_col(conn, "story_plan_nodes", "archived_from_status", "TEXT")
    _add_col(conn, "story_plan_nodes", "archived_branch_id", "INTEGER")
    conn.execute("UPDATE story_plan_nodes SET status='committed' WHERE status='realized'")


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
    (10, "unified_reference_materials", _migration_unified_materials),
    (11, "character_image_library", _migration_character_image_library),
    (12, "production_canvas", _migration_production_canvas),
    (13, "integrity_and_world_state_snapshots", _migration_integrity_and_world_state_snapshots),
    (14, "reference_document_fts", _migration_reference_document_fts),
    (15, "world_state_analysis_inputs", _migration_world_state_analysis_inputs),
    (16, "model_runtime_options", _migration_model_runtime_options),
    (17, "story_plan", _migration_story_plan),
    (18, "story_plan_refinement", _migration_story_plan_refinement),
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
    # The deployment uses a local bind-mounted database. WAL lets readers keep
    # serving while autosave/agent workers serialize their writes.
    bootstrap = sqlite3.connect(DB_PATH, timeout=15)
    try:
        bootstrap.execute("PRAGMA busy_timeout=15000")
        bootstrap.execute("PRAGMA journal_mode=WAL")
        bootstrap.execute("PRAGMA synchronous=NORMAL")
    finally:
        bootstrap.close()
    had_database = os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 0
    # A legacy database can need old additive columns before the formal V2 migration
    # runs. Back it up first, before any CREATE/ALTER statement touches that file.
    backup_done = False
    if had_database:
        preflight = sqlite3.connect(DB_PATH, timeout=15)
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
                llm_model_options_json TEXT DEFAULT '{}',
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
            CREATE TABLE IF NOT EXISTS work_material_settings (
                work_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                use_story_memory INTEGER NOT NULL DEFAULT 1,
                use_reference_projects INTEGER NOT NULL DEFAULT 1,
                use_style_profile INTEGER NOT NULL DEFAULT 1,
                use_inspirations INTEGER NOT NULL DEFAULT 1,
                use_reference_documents INTEGER NOT NULL DEFAULT 1,
                style_strength TEXT NOT NULL DEFAULT 'balanced',
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS work_reference_mounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                work_id INTEGER NOT NULL,
                reference_work_id INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                use_style INTEGER NOT NULL DEFAULT 1,
                use_plot INTEGER NOT NULL DEFAULT 1,
                use_world INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(work_id, reference_work_id)
            );
            CREATE TABLE IF NOT EXISTS work_style_profiles (
                work_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                source_kind TEXT NOT NULL DEFAULT 'manual',
                source_label TEXT DEFAULT '',
                source_job_id INTEGER,
                profile_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS work_reference_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                work_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                tags TEXT DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                pinned INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(work_id, content_hash)
            );
            CREATE TABLE IF NOT EXISTS disassembly_material_extractions (
                job_id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                work_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                result_json TEXT NOT NULL DEFAULT '{}',
                inspiration_ids_json TEXT NOT NULL DEFAULT '[]',
                error TEXT DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS entity_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                work_id INTEGER NOT NULL,
                entity_id INTEGER NOT NULL,
                category TEXT NOT NULL DEFAULT 'characters',
                image_path TEXT NOT NULL,
                prompt TEXT NOT NULL DEFAULT '',
                style TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                size TEXT NOT NULL DEFAULT '',
                selected INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE(image_path)
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
                builtin_key TEXT DEFAULT '',
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
        _add_col(conn, "works", "notes", "TEXT DEFAULT ''")  # 创作总则（文风、视角、作者禁忌）
        _add_col(conn, "users", "is_admin", "INTEGER DEFAULT 0")  # 后台管理员标记
        _add_col(conn, "agent_skills", "source_kind", "TEXT DEFAULT 'manual'")
        _add_col(conn, "agent_skills", "source_markdown", "TEXT DEFAULT ''")
        _add_col(conn, "agent_skills", "builtin_key", "TEXT DEFAULT ''")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_skills_user_builtin "
            "ON agent_skills(user_id,builtin_key) WHERE builtin_key<>''"
        )
        _add_col(conn, "chapters", "workflow_status", "TEXT DEFAULT 'drafting'")
        _add_col(conn, "chapters", "workflow_goal", "TEXT DEFAULT ''")
        _add_col(conn, "chapters", "workflow_summary", "TEXT DEFAULT ''")
        _add_col(conn, "chapters", "workflow_checked_at", "REAL")
        _add_col(conn, "chapters", "branch_of_chapter_id", "INTEGER")
        _add_col(conn, "chapters", "branch_from_revision_id", "INTEGER")
        _add_col(conn, "chapter_revisions", "label", "TEXT DEFAULT ''")
        _run_migrations(conn, had_database, backup_done=backup_done)
        _bootstrap_admin(conn)
        _sync_builtin_agent_skills(conn)


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
        _sync_builtin_agent_skills(conn, cur.lastrowid)
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
            conn.execute("DELETE FROM production_proposals WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM production_scenes WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM production_card_versions WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM production_canvas_layouts WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM production_impact_flags WHERE source_chapter_id=? OR affected_chapter_id=?", (cid, cid))
            conn.execute("DELETE FROM world_state_analyses WHERE chapter_id=?", (cid,))
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
        conn.execute("DELETE FROM production_proposals WHERE work_id IN (SELECT id FROM works WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM production_card_versions WHERE card_id IN (SELECT p.id FROM production_cards p JOIN works w ON w.id=p.work_id WHERE w.user_id=?)", (user_id,))
        conn.execute("DELETE FROM production_cards WHERE work_id IN (SELECT id FROM works WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM production_canvas_layouts WHERE work_id IN (SELECT id FROM works WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM production_impact_flags WHERE work_id IN (SELECT id FROM works WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM production_work_settings WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM disassembly_material_extractions WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM book_disassembly_chapters WHERE job_id IN (SELECT id FROM book_disassembly_jobs WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM book_disassembly_jobs WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM reference_document_fts WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM work_reference_documents WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM work_style_profiles WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM work_reference_mounts WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM work_material_settings WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM work_revisions WHERE work_id IN (SELECT id FROM works WHERE user_id=?)", (user_id,))
        conn.execute("DELETE FROM entity_images WHERE user_id=?", (user_id,))
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
            "SELECT llm_base_url, llm_api_key, llm_model, llm_models_json, llm_model_options_json, "
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
        settings["llm_model_options"] = _decode_model_runtime_options(
            settings.pop("llm_model_options_json", "{}")
        )
        settings.update(model_runtime_limits(
            settings["llm_model_options"], settings.get("llm_model") or "",
        ))
        settings["tavily_api_keys"] = _decode_tavily_api_keys(
            settings.pop("tavily_api_keys_json", "[]")
        )
        return settings


def save_settings(user_id, base_url, api_key, model, asr_model=None,
                  asr_base_url=None, asr_api_key=None, models=None,
                  tavily_api_keys=None, image_base_url=None,
                  image_api_key=None, image_model=None, image_size=None,
                  context_window_tokens=None, world_state_content_chars=None):
    """保存设置。api_key 为空或为掩码占位时保留旧值，避免清空已填的 key。"""
    now = time.time()
    with get_conn() as conn:
        old = conn.execute(
            "SELECT llm_api_key, asr_api_key, llm_model, llm_models_json, llm_model_options_json, "
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
        model_options = _decode_model_runtime_options(old["llm_model_options_json"]) if old else {}
        current_limits = model_runtime_limits(model_options, model)
        if model:
            model_options[model] = {
                "context_window_tokens": _positive_model_limit(
                    context_window_tokens, current_limits["context_window_tokens"],
                ),
                "world_state_content_chars": _positive_model_limit(
                    world_state_content_chars, current_limits["world_state_content_chars"],
                ),
            }
        if tavily_api_keys is None:
            tavily_keys = _decode_tavily_api_keys(old["tavily_api_keys_json"]) if old else []
        else:
            tavily_keys = normalize_tavily_api_keys(tavily_api_keys)
        conn.execute(
            "INSERT INTO user_settings(user_id, llm_base_url, llm_api_key, llm_model, "
            "llm_models_json, llm_model_options_json, asr_base_url, asr_api_key, asr_model, tavily_api_keys_json, "
            "image_base_url, image_api_key, image_model, image_size, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "llm_base_url=excluded.llm_base_url, llm_api_key=excluded.llm_api_key, "
            "llm_model=excluded.llm_model, llm_models_json=excluded.llm_models_json, "
            "llm_model_options_json=excluded.llm_model_options_json, asr_base_url=excluded.asr_base_url, "
            "asr_api_key=excluded.asr_api_key, asr_model=excluded.asr_model, "
            "tavily_api_keys_json=excluded.tavily_api_keys_json, image_base_url=excluded.image_base_url, "
            "image_api_key=excluded.image_api_key, image_model=excluded.image_model, "
            "image_size=excluded.image_size, updated_at=excluded.updated_at",
            (user_id, base_url, api_key, model, json.dumps(model_list, ensure_ascii=False),
             json.dumps(model_options, ensure_ascii=False, sort_keys=True),
             asr_base_url or "", asr_api_key, asr_model,
             json.dumps(tavily_keys, ensure_ascii=False), image_base_url or "", image_api_key,
             (image_model or "").strip()[:MAX_LLM_MODEL_ID_LENGTH], image_size, now),
        )
        active_limits = model_runtime_limits(model_options, model)
        return {"model": model, "models": model_list, **active_limits,
                "tavily_user_key_count": len(tavily_keys),
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
            "SELECT llm_model, llm_models_json, llm_model_options_json "
            "FROM user_settings WHERE user_id=?", (user_id,)
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
        model_options = _decode_model_runtime_options(old["llm_model_options_json"]) if old else {}
        return {"model": model, "models": model_list, **model_runtime_limits(model_options, model)}


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
        conn.execute(
            "DELETE FROM story_plan_realizations WHERE plan_node_id IN "
            "(SELECT id FROM story_plan_nodes WHERE work_id=?)", (wid,),
        )
        conn.execute(
            "DELETE FROM story_plan_versions WHERE plan_node_id IN "
            "(SELECT id FROM story_plan_nodes WHERE work_id=?)", (wid,),
        )
        conn.execute(
            "DELETE FROM story_plan_links WHERE plan_node_id IN "
            "(SELECT id FROM story_plan_nodes WHERE work_id=?)", (wid,),
        )
        conn.execute("DELETE FROM story_plan_nodes WHERE work_id=?", (wid,))
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
            conn.execute("DELETE FROM production_proposals WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM production_scenes WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM production_card_versions WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM production_canvas_layouts WHERE chapter_id=?", (cid,))
            conn.execute("DELETE FROM production_impact_flags WHERE source_chapter_id=? OR affected_chapter_id=?", (cid, cid))
            conn.execute("DELETE FROM world_state_analyses WHERE chapter_id=?", (cid,))
            _delete_story_memories_for_chapter(conn, cid)
        conn.execute("DELETE FROM chapters WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM entity_state_versions WHERE entity_id IN (SELECT id FROM entities WHERE work_id=?)", (wid,))
        conn.execute("DELETE FROM entity_state_proposals WHERE entity_id IN (SELECT id FROM entities WHERE work_id=?)", (wid,))
        conn.execute("DELETE FROM plot_state_versions WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM plot_state_proposals WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM entity_relations WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM story_sandboxes WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM production_proposals WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM production_card_versions WHERE card_id IN (SELECT id FROM production_cards WHERE work_id=?)", (wid,))
        conn.execute("DELETE FROM production_cards WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM production_canvas_layouts WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM production_impact_flags WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM production_work_settings WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM disassembly_material_extractions WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM book_disassembly_chapters WHERE job_id IN (SELECT id FROM book_disassembly_jobs WHERE target_work_id=?)", (wid,))
        conn.execute("DELETE FROM book_disassembly_jobs WHERE target_work_id=?", (wid,))
        conn.execute("DELETE FROM reference_document_fts WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM work_reference_documents WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM work_style_profiles WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM work_reference_mounts WHERE work_id=? OR reference_work_id=?", (wid, wid))
        conn.execute("DELETE FROM work_material_settings WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM work_revisions WHERE work_id=?", (wid,))
        conn.execute("DELETE FROM entity_images WHERE work_id=?", (wid,))
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
    """Return the author's work-wide writing guidelines."""
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


def update_story_sandbox(sid, user_id, name=None, data=None, expected_updated_at=None):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT s.id,s.updated_at FROM story_sandboxes s JOIN works w ON w.id=s.work_id "
            "WHERE s.id=? AND w.user_id=?", (sid, user_id),
        ).fetchone()
        if not row:
            return None
        now = max(time.time(), row["updated_at"] + 0.000001)
        clean_name = None if name is None else (" ".join((name or "").split())[:80] or "未命名沙盘")
        raw = None if data is None else json.dumps(data, ensure_ascii=False)
        expected_clause = " AND updated_at=?" if expected_updated_at is not None else ""
        params = (clean_name, raw, now, sid)
        if expected_updated_at is not None:
            params += (expected_updated_at,)
        updated = conn.execute(
            "UPDATE story_sandboxes SET name=COALESCE(?,name),data_json=COALESCE(?,data_json),updated_at=? "
            "WHERE id=?" + expected_clause,
            params,
        )
        if not updated.rowcount:
            return {"conflict": True}
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


# ---------- 故事计划（作者决定的未来，不是已经发生的事实） ----------

_STORY_PLAN_TEXT_LIMITS = {
    "title": 240,
    "summary": 4000,
    "detail": 30000,
    "goal": 6000,
    "conflict": 6000,
    "expected_outcome": 6000,
    "context_summary": 4000,
}


def _clean_story_plan_text(field, value):
    limit = _STORY_PLAN_TEXT_LIMITS[field]
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    text = text.strip()
    return text[:limit]


def _story_plan_row(conn, plan_id, user_id=None, work_id=None):
    clauses = ["n.id=?", "n.deleted_at IS NULL"]
    params = [plan_id]
    if user_id is not None:
        clauses.append("w.user_id=?")
        params.append(user_id)
    if work_id is not None:
        clauses.append("n.work_id=?")
        params.append(work_id)
    return conn.execute(
        "SELECT n.*,c.title AS chapter_title,c.ord AS chapter_ord "
        "FROM story_plan_nodes n JOIN works w ON w.id=n.work_id "
        "LEFT JOIN chapters c ON c.id=n.chapter_id AND c.deleted_at IS NULL "
        "WHERE " + " AND ".join(clauses), params,
    ).fetchone()


def _story_plan_snapshot(conn, row):
    item = dict(row)
    allowed = (
        "id", "work_id", "parent_id", "node_type", "title", "summary", "detail", "goal",
        "conflict", "expected_outcome", "context_summary", "status", "context_policy",
        "chapter_id", "scope_start_chapter_id", "scope_end_chapter_id", "ord",
        "source_sandbox_id", "source_node_id", "revision", "created_at", "updated_at",
    )
    snapshot = {key: item.get(key) for key in allowed}
    snapshot["links"] = [dict(link) for link in conn.execute(
        "SELECT target_type,target_id,label FROM story_plan_links WHERE plan_node_id=? ORDER BY id",
        (item["id"],),
    ).fetchall()]
    return snapshot


def _record_story_plan_version(conn, row):
    snapshot = _story_plan_snapshot(conn, row)
    conn.execute(
        "INSERT OR REPLACE INTO story_plan_versions(plan_node_id,revision,snapshot_json,created_at) "
        "VALUES(?,?,?,?)",
        (row["id"], row["revision"], json.dumps(snapshot, ensure_ascii=False), time.time()),
    )


def _story_plan_realizations(conn, plan_id):
    plan = conn.execute("SELECT * FROM story_plan_nodes WHERE id=?", (plan_id,)).fetchone()
    plan_hash = _story_plan_content_hash(conn, plan)
    rows = conn.execute(
        "SELECT r.*,c.title AS chapter_title,c.ord AS chapter_ord,c.content_hash AS current_content_hash,"
        "c.content_revision AS current_content_revision,n.revision AS current_plan_revision "
        "FROM story_plan_realizations r JOIN chapters c ON c.id=r.chapter_id "
        "JOIN story_plan_nodes n ON n.id=r.plan_node_id "
        "WHERE r.plan_node_id=? AND c.deleted_at IS NULL ORDER BY c.ord,r.id DESC",
        (plan_id,),
    ).fetchall()
    return [_realization_payload(row, plan_hash) for row in rows]


_PLAN_CONTENT_FIELDS = (
    "node_type", "title", "summary", "detail", "goal", "conflict", "expected_outcome",
    "parent_id", "chapter_id", "scope_start_chapter_id", "scope_end_chapter_id",
)


def _story_plan_content_hash(conn, row, links=None):
    if not row:
        return ""
    if links is None:
        links = conn.execute(
            "SELECT target_type,target_id FROM story_plan_links WHERE plan_node_id=? "
            "ORDER BY target_type,target_id", (row["id"],),
        ).fetchall()
    payload = {field: row[field] for field in _PLAN_CONTENT_FIELDS}
    payload["links"] = sorted((link["target_type"], link["target_id"]) for link in links)
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _story_plan_links(conn, plan_id):
    return [dict(link) for link in conn.execute(
        "SELECT id,target_type,target_id,label,created_at FROM story_plan_links "
        "WHERE plan_node_id=? ORDER BY id", (plan_id,),
    )]


def _realization_payload(row, plan_hash):
    item = dict(row)
    item["source_current"] = (
        not bool(item.get("stale"))
        and (item.get("source_content_hash") or "") == (item.get("current_content_hash") or "")
        and (item.get("plan_content_hash") or "") == plan_hash
    )
    item["is_stale"] = not item["source_current"]
    return item


def _story_plan_payload(conn, row, include_related=True):
    if not row:
        return None
    item = dict(row)
    if include_related:
        item["links"] = _story_plan_links(conn, item["id"])
        item["realizations"] = _story_plan_realizations(conn, item["id"])
    return item


def _validate_story_plan_reference(conn, work_id, table, item_id):
    if item_id is None:
        return None
    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        return None
    if table == "chapter":
        return item_id if _chapter_for_work(conn, item_id, work_id) else None
    table_name = {
        "entity": "entities",
        "production_card": "production_cards",
        "plan_node": "story_plan_nodes",
    }.get(table)
    if not table_name:
        return None
    deleted = " AND deleted_at IS NULL" if table == "plan_node" else ""
    row = conn.execute(
        f"SELECT id FROM {table_name} WHERE id=? AND work_id=?{deleted}", (item_id, work_id),
    ).fetchone()
    return item_id if row else None


def _replace_story_plan_links(conn, plan_id, work_id, links):
    conn.execute("DELETE FROM story_plan_links WHERE plan_node_id=?", (plan_id,))
    seen = set()
    now = time.time()
    for raw in links if isinstance(links, list) else []:
        if not isinstance(raw, dict):
            continue
        target_type = (raw.get("target_type") or "").strip()
        target_id = _validate_story_plan_reference(conn, work_id, target_type, raw.get("target_id"))
        key = (target_type, target_id)
        if target_id is None or key in seen or (target_type == "plan_node" and target_id == plan_id):
            continue
        seen.add(key)
        conn.execute(
            "INSERT INTO story_plan_links(plan_node_id,target_type,target_id,label,created_at) VALUES(?,?,?,?,?)",
            (plan_id, target_type, target_id, _clean_story_plan_text("title", raw.get("label")), now),
        )


def _story_plan_link_values(conn, plan_id):
    return sorted((row["target_type"], row["target_id"], row["label"] or "") for row in conn.execute(
        "SELECT target_type,target_id,label FROM story_plan_links WHERE plan_node_id=?", (plan_id,),
    ))


def _requested_story_plan_links(conn, wid, plan_id, links):
    result = set()
    for raw in links if isinstance(links, list) else []:
        if not isinstance(raw, dict):
            continue
        kind = (raw.get("target_type") or "").strip()
        target = _validate_story_plan_reference(conn, wid, kind, raw.get("target_id"))
        if target is not None and not (kind == "plan_node" and target == plan_id):
            result.add((kind, target, _clean_story_plan_text("title", raw.get("label"))))
    return sorted(result)


def _story_plan_scope_error(conn, wid, policy, start_id, end_id):
    if policy != "writing_range":
        return None
    if start_id is None and end_id is None:
        return "invalid_range"
    if start_id and end_id:
        rows = conn.execute(
            "SELECT id,ord FROM chapters WHERE work_id=? AND deleted_at IS NULL AND id IN (?,?)",
            (wid, start_id, end_id),
        ).fetchall()
        orders = {row["id"]: row["ord"] for row in rows}
        if len(orders) != (1 if start_id == end_id else 2) or orders[start_id] > orders[end_id]:
            return "invalid_range"
    return None


def list_story_plan_nodes(wid, user_id, include_abandoned=False):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        clauses = ["n.work_id=?", "n.deleted_at IS NULL"]
        params = [wid]
        if not include_abandoned:
            clauses.append("n.status<>'abandoned'")
        rows = conn.execute(
            "SELECT n.*,c.title AS chapter_title,c.ord AS chapter_ord "
            "FROM story_plan_nodes n LEFT JOIN chapters c ON c.id=n.chapter_id AND c.deleted_at IS NULL "
            "WHERE " + " AND ".join(clauses) + " ORDER BY COALESCE(n.parent_id,0),n.ord,n.id",
            params,
        ).fetchall()
        if not rows:
            return []
        links_by_plan = {}
        for link in conn.execute(
            "SELECT l.* FROM story_plan_links l JOIN story_plan_nodes n ON n.id=l.plan_node_id "
            "WHERE n.work_id=? AND n.deleted_at IS NULL ORDER BY l.id", (wid,),
        ):
            links_by_plan.setdefault(link["plan_node_id"], []).append(dict(link))
        realizations_by_plan = {}
        for realization in conn.execute(
            "SELECT r.*,c.title AS chapter_title,c.ord AS chapter_ord,c.content_hash AS current_content_hash,"
            "c.content_revision AS current_content_revision,n.revision AS current_plan_revision "
            "FROM story_plan_realizations r JOIN story_plan_nodes n ON n.id=r.plan_node_id "
            "JOIN chapters c ON c.id=r.chapter_id AND c.deleted_at IS NULL "
            "WHERE n.work_id=? AND n.deleted_at IS NULL ORDER BY c.ord,r.id DESC", (wid,),
        ):
            realizations_by_plan.setdefault(realization["plan_node_id"], []).append(dict(realization))
        result = []
        for row in rows:
            item = dict(row)
            item["links"] = links_by_plan.get(item["id"], [])
            plan_hash = _story_plan_content_hash(conn, row, item["links"])
            item["realizations"] = [_realization_payload(r, plan_hash)
                                    for r in realizations_by_plan.get(item["id"], [])]
            result.append(item)
        return result


def get_story_plan_node(plan_id, user_id):
    with get_conn() as conn:
        return _story_plan_payload(conn, _story_plan_row(conn, plan_id, user_id=user_id))


def create_story_plan_node(wid, user_id, values):
    values = values if isinstance(values, dict) else {}
    node_type = (values.get("node_type") or "chapter").strip()
    status = (values.get("status") or "planned").strip()
    policy = (values.get("context_policy") or "auto").strip()
    if node_type not in STORY_PLAN_NODE_TYPES:
        return {"invalid_node_type": True}
    if status not in STORY_PLAN_STATUSES:
        return {"invalid_status": True}
    if policy not in STORY_PLAN_CONTEXT_POLICIES:
        return {"invalid_context_policy": True}
    title = _clean_story_plan_text("title", values.get("title"))
    if not title:
        return {"invalid_title": True}
    now = time.time()
    with atomic_transaction(immediate=True) as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        parent_id = _validate_story_plan_reference(conn, wid, "plan_node", values.get("parent_id"))
        chapter_id = _validate_story_plan_reference(conn, wid, "chapter", values.get("chapter_id"))
        start_id = _validate_story_plan_reference(conn, wid, "chapter", values.get("scope_start_chapter_id"))
        end_id = _validate_story_plan_reference(conn, wid, "chapter", values.get("scope_end_chapter_id"))
        for field, resolved in (("parent_id", parent_id), ("chapter_id", chapter_id),
                                ("scope_start_chapter_id", start_id), ("scope_end_chapter_id", end_id)):
            if values.get(field) not in (None, "") and resolved is None:
                return {f"invalid_{field}": True}
        scope_error = _story_plan_scope_error(conn, wid, policy, start_id, end_id)
        if scope_error:
            return {scope_error: True}
        source_sandbox_id = values.get("source_sandbox_id")
        source_node_id = _clean_story_plan_text("title", values.get("source_node_id"))
        if source_sandbox_id and source_node_id:
            existing = conn.execute(
                "SELECT id FROM story_plan_nodes WHERE work_id=? AND source_sandbox_id=? "
                "AND source_node_id=? AND deleted_at IS NULL ORDER BY id DESC LIMIT 1",
                (wid, source_sandbox_id, source_node_id),
            ).fetchone()
            if existing:
                return _story_plan_payload(conn, _story_plan_row(conn, existing["id"], work_id=wid))
        try:
            ord_value = int(values.get("ord"))
        except (TypeError, ValueError):
            ord_value = 0
        if ord_value <= 0:
            if parent_id is None:
                row = conn.execute(
                    "SELECT COALESCE(MAX(ord),0)+1 AS next_ord FROM story_plan_nodes "
                    "WHERE work_id=? AND parent_id IS NULL AND deleted_at IS NULL", (wid,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COALESCE(MAX(ord),0)+1 AS next_ord FROM story_plan_nodes "
                    "WHERE work_id=? AND parent_id=? AND deleted_at IS NULL", (wid, parent_id),
                ).fetchone()
            ord_value = row["next_ord"]
        cur = conn.execute(
            "INSERT INTO story_plan_nodes(work_id,parent_id,node_type,title,summary,detail,goal,conflict,"
            "expected_outcome,context_summary,status,context_policy,chapter_id,scope_start_chapter_id,"
            "scope_end_chapter_id,ord,source_sandbox_id,source_node_id,revision,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
            (
                wid, parent_id, node_type, title,
                *[_clean_story_plan_text(field, values.get(field)) for field in (
                    "summary", "detail", "goal", "conflict", "expected_outcome", "context_summary"
                )],
                status, policy, chapter_id, start_id, end_id, ord_value,
                source_sandbox_id, source_node_id, now, now,
            ),
        )
        plan_id = cur.lastrowid
        _replace_story_plan_links(conn, plan_id, wid, values.get("links"))
        row = _story_plan_row(conn, plan_id, work_id=wid)
        _record_story_plan_version(conn, row)
        conn.execute("UPDATE works SET updated_at=? WHERE id=?", (now, wid))
        return _story_plan_payload(conn, row)


def update_story_plan_node(plan_id, user_id, values, expected_revision=None):
    values = values if isinstance(values, dict) else {}
    now = time.time()
    with atomic_transaction(immediate=True) as conn:
        current = _story_plan_row(conn, plan_id, user_id=user_id)
        if not current:
            return None
        current = dict(current)
        revision = int(current.get("revision") or 1)
        if expected_revision is not None:
            try:
                expected_revision = int(expected_revision)
            except (TypeError, ValueError):
                return {"invalid_revision": True}
            if expected_revision != revision:
                return {"conflict": True, "current": _story_plan_payload(conn, current)}
        updates = {}
        for field in _STORY_PLAN_TEXT_LIMITS:
            if field in values:
                updates[field] = _clean_story_plan_text(field, values.get(field))
        if "title" in updates and not updates["title"]:
            return {"invalid_title": True}
        for field, allowed in (
            ("node_type", STORY_PLAN_NODE_TYPES),
            ("status", STORY_PLAN_STATUSES),
            ("context_policy", STORY_PLAN_CONTEXT_POLICIES),
        ):
            if field in values:
                value = (values.get(field) or "").strip()
                if value not in allowed:
                    return {f"invalid_{field}": True}
                updates[field] = value
        for field, target_type in (
            ("parent_id", "plan_node"), ("chapter_id", "chapter"),
            ("scope_start_chapter_id", "chapter"), ("scope_end_chapter_id", "chapter"),
        ):
            if field in values:
                raw = values.get(field)
                updates[field] = None if raw in (None, "") else _validate_story_plan_reference(
                    conn, current["work_id"], target_type, raw,
                )
                if raw not in (None, "") and updates[field] is None:
                    return {f"invalid_{field}": True}
        if updates.get("parent_id") == plan_id:
            return {"invalid_parent_id": True}
        if "parent_id" in updates and updates["parent_id"] is not None:
            ancestor = updates["parent_id"]
            visited = {plan_id}
            while ancestor is not None:
                if ancestor in visited:
                    return {"invalid_parent_id": True}
                visited.add(ancestor)
                parent = conn.execute(
                    "SELECT parent_id FROM story_plan_nodes WHERE id=? AND work_id=? AND deleted_at IS NULL",
                    (ancestor, current["work_id"]),
                ).fetchone()
                ancestor = parent["parent_id"] if parent else None
        if "ord" in values:
            try:
                updates["ord"] = max(1, int(values.get("ord")))
            except (TypeError, ValueError):
                return {"invalid_ord": True}
        policy = updates.get("context_policy", current["context_policy"])
        start_id = updates.get("scope_start_chapter_id", current["scope_start_chapter_id"])
        end_id = updates.get("scope_end_chapter_id", current["scope_end_chapter_id"])
        scope_error = _story_plan_scope_error(conn, current["work_id"], policy, start_id, end_id)
        if scope_error:
            return {scope_error: True}
        changed = any(updates.get(key) != current.get(key) for key in updates)
        links_changed = "links" in values and _requested_story_plan_links(
            conn, current["work_id"], plan_id, values["links"],
        ) != _story_plan_link_values(conn, plan_id)
        if not changed and not links_changed:
            return _story_plan_payload(conn, current)
        previous_content_hash = _story_plan_content_hash(conn, current)
        updates["revision"] = revision + 1
        updates["updated_at"] = now
        assignments = ",".join(f"{key}=?" for key in updates)
        cur = conn.execute(
            f"UPDATE story_plan_nodes SET {assignments} WHERE id=? AND revision=?",
            (*updates.values(), plan_id, revision),
        )
        if cur.rowcount != 1:
            latest = _story_plan_row(conn, plan_id, user_id=user_id)
            return {"conflict": True, "current": _story_plan_payload(conn, latest)}
        if links_changed:
            _replace_story_plan_links(conn, plan_id, current["work_id"], values.get("links"))
        row = _story_plan_row(conn, plan_id, work_id=current["work_id"])
        if previous_content_hash != _story_plan_content_hash(conn, row):
            conn.execute("UPDATE story_plan_realizations SET stale=1,updated_at=? WHERE plan_node_id=?", (now, plan_id))
        _record_story_plan_version(conn, row)
        conn.execute("UPDATE works SET updated_at=? WHERE id=?", (now, current["work_id"]))
        return _story_plan_payload(conn, row)


def list_story_plan_versions(plan_id, user_id):
    with get_conn() as conn:
        row = _story_plan_row(conn, plan_id, user_id=user_id)
        if not row:
            return None
        versions = conn.execute(
            "SELECT id,plan_node_id,revision,snapshot_json,created_at FROM story_plan_versions "
            "WHERE plan_node_id=? ORDER BY revision DESC", (plan_id,),
        ).fetchall()
        result = []
        for version in versions:
            item = dict(version)
            try:
                item["snapshot"] = json.loads(item.pop("snapshot_json") or "{}")
            except Exception:
                item["snapshot"] = {}
            result.append(item)
        return result


def restore_story_plan_version(plan_id, user_id, revision, expected_revision=None):
    with get_conn() as conn:
        row = _story_plan_row(conn, plan_id, user_id=user_id)
        if not row:
            return None
        version = conn.execute(
            "SELECT snapshot_json FROM story_plan_versions WHERE plan_node_id=? AND revision=?",
            (plan_id, revision),
        ).fetchone()
        if not version:
            return {"invalid_version": True}
        try:
            snapshot = json.loads(version["snapshot_json"] or "{}")
        except Exception:
            snapshot = {}
    allowed = set(_STORY_PLAN_TEXT_LIMITS) | {
        "node_type", "status", "context_policy", "parent_id", "chapter_id",
        "scope_start_chapter_id", "scope_end_chapter_id", "ord", "links",
    }
    return update_story_plan_node(
        plan_id, user_id, {key: value for key, value in snapshot.items() if key in allowed},
        expected_revision=expected_revision,
    )


def upsert_story_plan_realization(plan_id, user_id, chapter_id, status="pending", evidence="", notes="", source="manual",
                                  expected_plan_revision=None, expected_content_revision=None):
    status = (status or "pending").strip()
    if status not in STORY_PLAN_REALIZATION_STATUSES:
        return {"invalid_status": True}
    now = time.time()
    with atomic_transaction(immediate=True) as conn:
        plan = _story_plan_row(conn, plan_id, user_id=user_id)
        if not plan:
            return None
        chapter = conn.execute(
            "SELECT id,work_id,content_hash,content_revision FROM chapters WHERE id=? AND deleted_at IS NULL",
            (chapter_id,),
        ).fetchone()
        if not chapter or chapter["work_id"] != plan["work_id"]:
            return {"invalid_chapter": True}
        try:
            revisions_match = (
                (expected_plan_revision is None or int(expected_plan_revision) == plan["revision"])
                and (expected_content_revision is None or int(expected_content_revision) == chapter["content_revision"])
            )
        except (TypeError, ValueError):
            return {"invalid_revision": True}
        if not revisions_match:
            return {"conflict": True}
        if source == "agent" and status in {"partial", "realized", "deviated"}:
            content = conn.execute("SELECT content FROM chapters WHERE id=?", (chapter_id,)).fetchone()["content"] or ""
            if not evidence or evidence not in content:
                return {"invalid_evidence": True}
        source_hash = chapter["content_hash"] or _content_fingerprint("")
        plan_hash = _story_plan_content_hash(conn, plan)
        conn.execute(
            "UPDATE story_plan_realizations SET stale=1,updated_at=? WHERE plan_node_id=? AND chapter_id=? "
            "AND (source_content_hash<>? OR plan_content_hash<>?)",
            (now, plan_id, chapter_id, source_hash, plan_hash),
        )
        conn.execute(
            "INSERT INTO story_plan_realizations(plan_node_id,plan_revision,chapter_id,source_content_hash,"
            "source_content_revision,status,evidence,notes,source,plan_content_hash,stale,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?) ON CONFLICT(plan_node_id,chapter_id,source_content_hash) DO UPDATE SET "
            "plan_revision=excluded.plan_revision,source_content_revision=excluded.source_content_revision,"
            "status=excluded.status,evidence=excluded.evidence,notes=excluded.notes,source=excluded.source,"
            "plan_content_hash=excluded.plan_content_hash,stale=0,updated_at=excluded.updated_at",
            (
                plan_id, plan["revision"], chapter_id, source_hash, chapter["content_revision"], status,
                _clean_story_plan_text("detail", evidence), _clean_story_plan_text("summary", notes),
                (source or "manual")[:40], plan_hash, now, now,
            ),
        )
        return _story_plan_payload(conn, _story_plan_row(conn, plan_id, work_id=plan["work_id"]))


def get_or_create_story_plan_chapter(plan_id, user_id):
    with atomic_transaction(immediate=True) as conn:
        plan = _story_plan_row(conn, plan_id, user_id=user_id)
        if not plan:
            return None
        if plan["node_type"] != "chapter":
            return {"invalid_node_type": True}
        chapter = get_chapter_meta(plan["chapter_id"], user_id) if plan["chapter_id"] else None
        if not chapter:
            chapter = create_chapter(plan["work_id"], user_id, plan["title"])
            update_chapter_workflow(chapter["id"], user_id, status="planning",
                                    goal=plan["goal"] or plan["summary"] or plan["title"])
            plan = update_story_plan_node(plan_id, user_id, {"chapter_id": chapter["id"]},
                                          expected_revision=plan["revision"])
            if plan.get("conflict") is True:
                return plan
        return {"plan": _story_plan_payload(conn, _story_plan_row(conn, plan_id, user_id=user_id)),
                "chapter": chapter}


def move_story_plan_node(plan_id, user_id, direction, expected_revision=None):
    if direction not in {"up", "down"}:
        return {"invalid_order": True}
    with atomic_transaction(immediate=True) as conn:
        row = _story_plan_row(conn, plan_id, user_id=user_id)
        if not row:
            return None
        try:
            if expected_revision is not None and int(expected_revision) != row["revision"]:
                return {"conflict": True}
        except (TypeError, ValueError):
            return {"invalid_revision": True}
        if row["parent_id"] is None:
            siblings = conn.execute(
                "SELECT * FROM story_plan_nodes WHERE work_id=? AND parent_id IS NULL "
                "AND deleted_at IS NULL ORDER BY ord,id", (row["work_id"],),
            ).fetchall()
        else:
            siblings = conn.execute(
                "SELECT * FROM story_plan_nodes WHERE work_id=? AND parent_id=? "
                "AND deleted_at IS NULL ORDER BY ord,id", (row["work_id"], row["parent_id"]),
            ).fetchall()
        index = next(i for i, sibling in enumerate(siblings) if sibling["id"] == plan_id)
        other = index + (-1 if direction == "up" else 1)
        if other < 0 or other >= len(siblings):
            return _story_plan_payload(conn, row)
        siblings[index], siblings[other] = siblings[other], siblings[index]
        now = time.time()
        for ordinal, sibling in enumerate(siblings, 1):
            if sibling["ord"] == ordinal:
                continue
            conn.execute(
                "UPDATE story_plan_nodes SET ord=?,revision=revision+1,updated_at=? WHERE id=?",
                (ordinal, now, sibling["id"]),
            )
            _record_story_plan_version(conn, _story_plan_row(conn, sibling["id"], work_id=row["work_id"]))
        return _story_plan_payload(conn, _story_plan_row(conn, plan_id, work_id=row["work_id"]))


def archive_story_plan_branch(plan_id, user_id, restore=False, expected_revision=None):
    with atomic_transaction(immediate=True) as conn:
        root = _story_plan_row(conn, plan_id, user_id=user_id)
        if not root:
            return None
        try:
            if expected_revision is not None and int(expected_revision) != root["revision"]:
                return {"conflict": True}
        except (TypeError, ValueError):
            return {"invalid_revision": True}
        rows = conn.execute(
            "SELECT * FROM story_plan_nodes WHERE work_id=? AND deleted_at IS NULL", (root["work_id"],),
        ).fetchall()
        by_parent = {}
        for row in rows:
            by_parent.setdefault(row["parent_id"], []).append(row)
        stack = [root]
        changed = 0
        while stack:
            row = stack.pop()
            stack.extend(by_parent.get(row["id"], []))
            if restore and (row["status"] != "abandoned" or row["archived_branch_id"] != plan_id):
                continue
            if not restore and row["status"] == "abandoned":
                continue
            status = (row["archived_from_status"] or "planned") if restore else "abandoned"
            archived_from = None if restore else row["status"]
            conn.execute(
                "UPDATE story_plan_nodes SET status=?,archived_from_status=?,archived_branch_id=?,"
                "revision=revision+1,updated_at=? WHERE id=?",
                (status, archived_from, None if restore else plan_id, time.time(), row["id"]),
            )
            _record_story_plan_version(conn, _story_plan_row(conn, row["id"], work_id=root["work_id"]))
            changed += 1
        return {"changed": changed, "plan": _story_plan_payload(
            conn, _story_plan_row(conn, plan_id, work_id=root["work_id"]))}


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


def _memory_payloads(conn, rows):
    """Hydrate entity references for a memory result set with one query."""
    rows = list(rows or [])
    if not rows:
        return []
    memory_ids = [row["id"] for row in rows]
    marks = ",".join("?" for _ in memory_ids)
    refs_by_memory = {memory_id: [] for memory_id in memory_ids}
    for ref in conn.execute(
        "SELECT r.memory_id,e.id,e.name FROM story_memory_entity_refs r "
        "JOIN entities e ON e.id=r.entity_id WHERE r.memory_id IN (" + marks + ") "
        "ORDER BY r.memory_id,e.name,e.id", memory_ids,
    ):
        refs_by_memory.setdefault(ref["memory_id"], []).append(ref)
    result = []
    for row in rows:
        item = dict(row)
        refs = refs_by_memory.get(item["id"], [])
        item["entity_ids"] = [ref["id"] for ref in refs]
        item["entity_names"] = [ref["name"] for ref in refs]
        item["source_current"] = not bool(item.get("source_content_hash")) or bool(item.get("source_hash_matches", 1))
        item["is_stale"] = bool(item.get("stale")) or not item["source_current"]
        item.pop("source_hash_matches", None)
        result.append(item)
    return result


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
        return _memory_payloads(conn, rows)


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
        memories = _memory_payloads(conn, rows)
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
        return _memory_payloads(conn, rows)


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
        clauses = [
            "work_id=?", "deleted_at IS NULL", "TRIM(COALESCE(outcome_summary, ''))<>''",
            "outcome_source_hash=content_hash",
        ]
        params = [wid]
        if before_chapter_id is not None:
            if not _chapter_for_work(conn, before_chapter_id, wid):
                return {"invalid_chapter": True}
            clauses.append("ord < (SELECT ord FROM chapters WHERE id=? AND work_id=? AND deleted_at IS NULL)")
            params.extend([before_chapter_id, wid])
        rows = conn.execute(
            "SELECT id,title,ord,outcome_summary,outcome_source_hash,outcome_source_revision,"
            "content_hash,content_revision FROM chapters WHERE "
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


def get_plot_state_at(wid, user_id, at_chapter_id=None, before=False):
    """Lightweight plot-state read for model context; does not load UI history/proposals."""
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        if at_chapter_id is not None and not _chapter_for_work(conn, at_chapter_id, wid):
            return {"invalid_chapter": True}
        version = _plot_state_version_at(conn, wid, at_chapter_id, before=before)
        return {
            "current_state": version["state"] if version else normalize_plot_state({}),
            "state_version": version,
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
    overview = get_plot_state_at(wid, user_id, at_chapter_id)
    if not overview or overview.get("invalid_chapter"):
        return ""
    state = overview.get("current_state") or {}
    facts = [f"{PLOT_STATE_LABELS[field]}={state[field]}" for field in PLOT_STATE_FIELDS if state.get(field)]
    if not facts:
        return ""
    target = get_chapter_meta(at_chapter_id, user_id) if at_chapter_id else None
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


def _state_versions_for_work(conn, wid, target_chapter_id, before=False):
    if target_chapter_id is None:
        return {}
    op = "<" if before else "<="
    rows = conn.execute(
        "SELECT v.id,v.entity_id,v.chapter_id,v.state_json,v.change_summary,v.evidence,"
        "v.source,v.proposal_id,v.source_content_hash,v.stale,v.created_at,"
        "c.title AS chapter_title,c.ord AS chapter_ord,"
        "CASE WHEN v.source_content_hash='' OR v.source_content_hash=c.content_hash THEN 1 ELSE 0 END AS source_hash_matches "
        "FROM entity_state_versions v JOIN entities e ON e.id=v.entity_id "
        "JOIN chapters c ON c.id=v.chapter_id JOIN chapters target ON target.id=? "
        f"WHERE e.work_id=? AND c.deleted_at IS NULL AND target.deleted_at IS NULL "
        f"AND c.work_id=target.work_id AND c.ord {op} target.ord AND v.stale=0 "
        "AND (v.source_content_hash='' OR v.source_content_hash=c.content_hash) "
        "ORDER BY v.entity_id,c.ord DESC,v.id DESC",
        (target_chapter_id, wid),
    ).fetchall()
    latest = {}
    for row in rows:
        if row["entity_id"] not in latest:
            latest[row["entity_id"]] = _state_version_payload(row)
    return latest


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
        versions = _state_versions_for_work(conn, wid, at_chapter_id)
        pending_counts = {
            row["entity_id"]: row["count"] for row in conn.execute(
                "SELECT p.entity_id,COUNT(*) AS count FROM entity_state_proposals p "
                "JOIN entities e ON e.id=p.entity_id WHERE e.work_id=? AND p.chapter_id=? "
                "AND p.status='pending' GROUP BY p.entity_id", (wid, at_chapter_id),
            )
        }
        for entity in rows:
            entity["current_state"] = None
            entity["state_version"] = None
            entity["pending_count"] = 0
            if entity["kind"] != "人物":
                continue
            version = versions.get(entity["id"])
            if version:
                entity["current_state"] = version["state"]
                entity["state_version"] = version
            entity["pending_count"] = pending_counts.get(entity["id"], 0)
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


def _entity_image_payload(row):
    if not row:
        return None
    item = dict(row)
    item["selected"] = bool(item.get("selected"))
    item.pop("image_path", None)
    return item


def save_entity_image(eid, user_id, image_path, prompt, style="", model="", size=""):
    now = time.time()
    with get_conn() as conn:
        if not _entity_owned(conn, eid, user_id):
            return None
        entity = conn.execute("SELECT work_id FROM entities WHERE id=?", (eid,)).fetchone()
        conn.execute("UPDATE entity_images SET selected=0,updated_at=? WHERE entity_id=?", (now, eid))
        cur = conn.execute(
            "INSERT INTO entity_images(user_id,work_id,entity_id,category,image_path,prompt,style,model,size,"
            "selected,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, entity["work_id"], eid, "characters", image_path or "", (prompt or "").strip()[:8000],
             (style or "").strip()[:1000], (model or "").strip()[:200], (size or "").strip()[:32], 1, now, now),
        )
        conn.execute(
            "UPDATE entities SET image_path=?, image_prompt=?, image_updated_at=?, updated_at=? WHERE id=?",
            (image_path or "", (prompt or "").strip()[:8000], now, now, eid),
        )
        row = conn.execute(
            "SELECT i.*,e.name AS entity_name FROM entity_images i JOIN entities e ON e.id=i.entity_id WHERE i.id=?",
            (cur.lastrowid,),
        ).fetchone()
        return {"image": _entity_image_payload(row), "image_updated_at": now}


def clear_entity_image(eid, user_id):
    now = time.time()
    with get_conn() as conn:
        if not _entity_owned(conn, eid, user_id):
            return None
        old = conn.execute("SELECT image_path FROM entities WHERE id=?", (eid,)).fetchone()
        conn.execute("DELETE FROM entity_images WHERE entity_id=? AND image_path=?", (eid, old["image_path"] if old else ""))
        fallback = conn.execute(
            "SELECT id,image_path,prompt FROM entity_images WHERE entity_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
            (eid,),
        ).fetchone()
        if fallback:
            conn.execute("UPDATE entity_images SET selected=1,updated_at=? WHERE id=?", (now, fallback["id"]))
            conn.execute(
                "UPDATE entities SET image_path=?,image_prompt=?,image_updated_at=?,updated_at=? WHERE id=?",
                (fallback["image_path"], fallback["prompt"], now, now, eid),
            )
        else:
            conn.execute(
                "UPDATE entities SET image_path='', image_updated_at=NULL, updated_at=? WHERE id=?",
                (now, eid),
            )
        return old["image_path"] if old else ""


def list_entity_images(eid, user_id):
    with get_conn() as conn:
        if not _entity_owned(conn, eid, user_id):
            return None
        rows = conn.execute(
            "SELECT i.*,e.name AS entity_name FROM entity_images i JOIN entities e ON e.id=i.entity_id "
            "WHERE i.entity_id=? AND i.user_id=? ORDER BY i.selected DESC,i.created_at DESC,i.id DESC",
            (eid, user_id),
        ).fetchall()
        return [_entity_image_payload(row) for row in rows]


def list_entity_image_paths(eid, user_id):
    with get_conn() as conn:
        if not _entity_owned(conn, eid, user_id):
            return None
        return [row["image_path"] for row in conn.execute(
            "SELECT image_path FROM entity_images WHERE entity_id=? AND user_id=? AND image_path<>''",
            (eid, user_id),
        )]


def list_work_entity_images(wid, user_id, category="characters"):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        params = [wid, user_id]
        where = "WHERE i.work_id=? AND i.user_id=?"
        if category:
            where += " AND i.category=?"
            params.append(category)
        rows = conn.execute(
            "SELECT i.*,e.name AS entity_name FROM entity_images i JOIN entities e ON e.id=i.entity_id "
            + where + " ORDER BY i.created_at DESC,i.id DESC LIMIT 300", params,
        ).fetchall()
        return [_entity_image_payload(row) for row in rows]


def get_entity_image_asset(image_id, user_id, include_path=False):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT i.*,e.name AS entity_name FROM entity_images i JOIN entities e ON e.id=i.entity_id "
            "JOIN works w ON w.id=i.work_id WHERE i.id=? AND i.user_id=? AND w.user_id=?",
            (image_id, user_id, user_id),
        ).fetchone()
        if not row:
            return None
        return dict(row) if include_path else _entity_image_payload(row)


def select_entity_image(image_id, user_id):
    now = time.time()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT i.* FROM entity_images i JOIN works w ON w.id=i.work_id "
            "WHERE i.id=? AND i.user_id=? AND w.user_id=?", (image_id, user_id, user_id),
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE entity_images SET selected=0,updated_at=? WHERE entity_id=?", (now, row["entity_id"]))
        conn.execute("UPDATE entity_images SET selected=1,updated_at=? WHERE id=?", (now, image_id))
        conn.execute(
            "UPDATE entities SET image_path=?,image_prompt=?,image_updated_at=?,updated_at=? WHERE id=?",
            (row["image_path"], row["prompt"], now, now, row["entity_id"]),
        )
        result = conn.execute(
            "SELECT i.*,e.name AS entity_name FROM entity_images i JOIN entities e ON e.id=i.entity_id WHERE i.id=?",
            (image_id,),
        ).fetchone()
        return _entity_image_payload(result)


def delete_entity_image_asset(image_id, user_id):
    now = time.time()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT i.* FROM entity_images i JOIN works w ON w.id=i.work_id "
            "WHERE i.id=? AND i.user_id=? AND w.user_id=?", (image_id, user_id, user_id),
        ).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM entity_images WHERE id=?", (image_id,))
        fallback = None
        if row["selected"]:
            fallback = conn.execute(
                "SELECT * FROM entity_images WHERE entity_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                (row["entity_id"],),
            ).fetchone()
            if fallback:
                conn.execute("UPDATE entity_images SET selected=1,updated_at=? WHERE id=?", (now, fallback["id"]))
                conn.execute(
                    "UPDATE entities SET image_path=?,image_prompt=?,image_updated_at=?,updated_at=? WHERE id=?",
                    (fallback["image_path"], fallback["prompt"], now, now, row["entity_id"]),
                )
            else:
                conn.execute(
                    "UPDATE entities SET image_path='',image_updated_at=NULL,updated_at=? WHERE id=?",
                    (now, row["entity_id"]),
                )
        return {"image_path": row["image_path"], "entity_id": row["entity_id"],
                "selected_image_id": fallback["id"] if fallback else None,
                "has_image": bool(fallback) if row["selected"] else True}


def list_work_entity_image_paths(wid, user_id):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        return list(dict.fromkeys(row["image_path"] for row in conn.execute(
            "SELECT image_path FROM entity_images WHERE work_id=? AND image_path<>'' UNION ALL "
            "SELECT image_path FROM entities WHERE work_id=? AND image_path<>''", (wid, wid),
        )))


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
        conn.execute("DELETE FROM entity_images WHERE entity_id=?", (eid,))
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
        versions = _state_versions_for_work(conn, wid, at_chapter_id, before=before)
        for entity in rows:
            version = versions.get(entity["id"])
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
        payload = (
            json.dumps(normalized, ensure_ascii=False), (change_summary or "").strip()[:1000],
            (evidence or "").strip()[:3000], source, proposal_id,
            chapter_source["content_hash"] if source == "ai" else "", now,
        )
        # Automated analysis is derived data. Re-analysis refreshes the snapshot
        # instead of accumulating equivalent versions for the same chapter.
        existing = None
        if source == "ai":
            existing = conn.execute(
                "SELECT id FROM entity_state_versions WHERE entity_id=? AND chapter_id=? "
                "AND source='ai' ORDER BY id DESC LIMIT 1", (eid, chapter_id),
            ).fetchone()
        if existing:
            conn.execute(
                "UPDATE entity_state_versions SET state_json=?,change_summary=?,evidence=?,source=?,proposal_id=?,"
                "source_content_hash=?,stale=0,created_at=? WHERE id=?", (*payload, existing["id"]),
            )
            version_id = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO entity_state_versions(entity_id,chapter_id,state_json,change_summary,evidence,source,proposal_id,"
                "source_content_hash,stale,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (eid, chapter_id, *payload[:-1], 0, now),
            )
            version_id = cur.lastrowid
        row = conn.execute(
            "SELECT v.id, v.entity_id, v.chapter_id, v.state_json, v.change_summary, v.evidence, "
            "v.source, v.proposal_id, v.source_content_hash, v.stale, v.created_at, "
            "c.title AS chapter_title, c.ord AS chapter_ord, "
            "CASE WHEN v.source_content_hash='' OR v.source_content_hash=c.content_hash THEN 1 ELSE 0 END AS source_hash_matches "
            "FROM entity_state_versions v JOIN chapters c ON c.id=v.chapter_id WHERE v.id=?",
            (version_id,),
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


# ---------- 创作生产画布（全书设定 + 章节场景 + 时点状态）----------

def _production_json_object(value):
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except Exception:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _production_json_list(value):
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
    except Exception:
        parsed = []
    return parsed if isinstance(parsed, list) else []


def get_production_settings(wid, user_id):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        row = conn.execute(
            "SELECT evidence_enabled,auto_analyze_on_leave,custom_fields_json,updated_at "
            "FROM production_work_settings WHERE work_id=?", (wid,),
        ).fetchone()
        if not row:
            return {"evidence_enabled": True, "auto_analyze_on_leave": True,
                    "custom_fields": [], "updated_at": None}
        item = dict(row)
        item["evidence_enabled"] = bool(item["evidence_enabled"])
        item["auto_analyze_on_leave"] = bool(item["auto_analyze_on_leave"])
        item["custom_fields"] = _production_json_list(item.pop("custom_fields_json", "[]"))
        return item


def save_production_settings(wid, user_id, values):
    values = values if isinstance(values, dict) else {}
    now = time.time()
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        old = conn.execute(
            "SELECT evidence_enabled,auto_analyze_on_leave,custom_fields_json "
            "FROM production_work_settings WHERE work_id=?", (wid,),
        ).fetchone()
        evidence_enabled = int(bool(values.get(
            "evidence_enabled", old["evidence_enabled"] if old else True
        )))
        auto_analyze = int(bool(values.get(
            "auto_analyze_on_leave", old["auto_analyze_on_leave"] if old else True
        )))
        custom_fields = values.get(
            "custom_fields", _production_json_list(old["custom_fields_json"]) if old else []
        )
        if not isinstance(custom_fields, list):
            return {"invalid_fields": True}
        clean_fields = []
        for field in custom_fields[:40]:
            if not isinstance(field, dict):
                continue
            name = str(field.get("name") or "").strip()[:80]
            type_ = str(field.get("type") or "text").strip()
            if not name or type_ not in {"text", "number", "level", "enum", "boolean", "hidden"}:
                continue
            clean_fields.append({
                "id": str(field.get("id") or hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]),
                "name": name, "type": type_,
                "options": [str(item).strip()[:80] for item in field.get("options", [])[:30]
                            if str(item).strip()] if isinstance(field.get("options"), list) else [],
            })
        conn.execute(
            "INSERT INTO production_work_settings(work_id,user_id,evidence_enabled,auto_analyze_on_leave,"
            "custom_fields_json,updated_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(work_id) DO UPDATE SET evidence_enabled=excluded.evidence_enabled,"
            "auto_analyze_on_leave=excluded.auto_analyze_on_leave,custom_fields_json=excluded.custom_fields_json,"
            "updated_at=excluded.updated_at",
            (wid, user_id, evidence_enabled, auto_analyze,
             json.dumps(clean_fields, ensure_ascii=False), now),
        )
    return get_production_settings(wid, user_id)


def _production_card_state_at(conn, card_id, target_chapter_id, before=False):
    if target_chapter_id is None:
        return None
    op = "<" if before else "<="
    row = conn.execute(
        "SELECT v.id,v.card_id,v.chapter_id,v.state_json,v.change_summary,v.evidence,"
        "v.evidence_start,v.evidence_end,v.source_content_hash,v.source,v.stale,v.created_at,"
        "c.title AS chapter_title,c.ord AS chapter_ord "
        "FROM production_card_versions v JOIN chapters c ON c.id=v.chapter_id "
        "JOIN chapters target ON target.id=? "
        f"WHERE v.card_id=? AND c.work_id=target.work_id AND c.deleted_at IS NULL "
        f"AND c.ord {op} target.ord AND v.stale=0 "
        "AND (v.source_content_hash='' OR v.source_content_hash=c.content_hash) "
        "ORDER BY c.ord DESC,v.id DESC LIMIT 1",
        (target_chapter_id, card_id),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["state"] = _production_json_object(item.pop("state_json", "{}"))
    item["is_stale"] = bool(item.pop("stale", 0))
    return item


def _production_card_states_for_work(conn, wid, target_chapter_id, before=False):
    if target_chapter_id is None:
        return {}
    op = "<" if before else "<="
    rows = conn.execute(
        "SELECT v.id,v.card_id,v.chapter_id,v.state_json,v.change_summary,v.evidence,"
        "v.evidence_start,v.evidence_end,v.source_content_hash,v.source,v.stale,v.created_at,"
        "c.title AS chapter_title,c.ord AS chapter_ord "
        "FROM production_card_versions v JOIN production_cards p ON p.id=v.card_id "
        "JOIN chapters c ON c.id=v.chapter_id JOIN chapters target ON target.id=? "
        f"WHERE p.work_id=? AND c.work_id=target.work_id AND c.deleted_at IS NULL "
        f"AND c.ord {op} target.ord AND v.stale=0 "
        "AND (v.source_content_hash='' OR v.source_content_hash=c.content_hash) "
        "ORDER BY v.card_id,c.ord DESC,v.id DESC",
        (target_chapter_id, wid),
    ).fetchall()
    latest = {}
    for row in rows:
        if row["card_id"] in latest:
            continue
        item = dict(row)
        item["state"] = _production_json_object(item.pop("state_json", "{}"))
        item["is_stale"] = bool(item.pop("stale", 0))
        latest[row["card_id"]] = item
    return latest


_PRODUCTION_STATE_UNSET = object()


def _production_card_payload(conn, row, target_chapter_id=None, before=False,
                             current_state=_PRODUCTION_STATE_UNSET):
    if not row:
        return None
    item = dict(row)
    item["attributes"] = _production_json_object(item.pop("attributes_json", "{}"))
    item["truth"] = _production_json_object(item.pop("truth_json", "{}"))
    item["category_label"] = PRODUCTION_CARD_CATEGORY_LABELS.get(item.get("category"), item.get("category"))
    item["current_state"] = (
        _production_card_state_at(conn, item["id"], target_chapter_id, before)
        if current_state is _PRODUCTION_STATE_UNSET else current_state
    )
    return item


def list_production_cards(wid, user_id, at_chapter_id=None, before=False, include_pending=True):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        if at_chapter_id is not None and not _chapter_for_work(conn, at_chapter_id, wid):
            return None
        where = "work_id=? AND status<>'archived'"
        params = [wid]
        if at_chapter_id is not None:
            op = "<" if before else "<="
            where += (
                " AND ((source_chapter_id IS NULL AND (introduced_at_ord IS NULL OR introduced_at_ord "
                f"{op} (SELECT ord FROM chapters WHERE id=?))) OR EXISTS (SELECT 1 FROM chapters source "
                "JOIN chapters target ON target.id=? WHERE source.id=production_cards.source_chapter_id "
                f"AND source.work_id=target.work_id AND source.ord {op} target.ord))"
            )
            params.extend((at_chapter_id, at_chapter_id))
        if not include_pending:
            where += " AND status='confirmed'"
        rows = conn.execute(
            "SELECT * FROM production_cards WHERE " + where + " ORDER BY category,name,id", params
        ).fetchall()
        states = _production_card_states_for_work(conn, wid, at_chapter_id, before=before)
        return [
            _production_card_payload(conn, row, at_chapter_id, before, states.get(row["id"]))
            for row in rows
        ]


def get_production_card(card_id, user_id, at_chapter_id=None):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT p.* FROM production_cards p JOIN works w ON w.id=p.work_id "
            "WHERE p.id=? AND w.user_id=?", (card_id, user_id),
        ).fetchone()
        if not row:
            return None
        if at_chapter_id is not None and not _chapter_for_work(conn, at_chapter_id, row["work_id"]):
            return None
        item = _production_card_payload(conn, row, at_chapter_id)
        versions = conn.execute(
            "SELECT v.*,c.title AS chapter_title,c.ord AS chapter_ord FROM production_card_versions v "
            "JOIN chapters c ON c.id=v.chapter_id WHERE v.card_id=? ORDER BY c.ord DESC,v.id DESC",
            (card_id,),
        ).fetchall()
        item["versions"] = []
        for version in versions:
            value = dict(version)
            value["state"] = _production_json_object(value.pop("state_json", "{}"))
            value["is_stale"] = bool(value.pop("stale", 0)) or bool(
                value.get("source_content_hash") and value["source_content_hash"] != conn.execute(
                    "SELECT content_hash FROM chapters WHERE id=?", (value["chapter_id"],)
                ).fetchone()[0]
            )
            item["versions"].append(value)
        return item


def save_production_card(wid, user_id, values, card_id=None):
    values = values if isinstance(values, dict) else {}
    now = time.time()
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        old = None
        if card_id is not None:
            old = conn.execute("SELECT * FROM production_cards WHERE id=? AND work_id=?", (card_id, wid)).fetchone()
            if not old:
                return None
        category = str(values.get("category", old["category"] if old else "rule") or "rule").strip()
        if category not in PRODUCTION_CARD_CATEGORIES:
            return {"invalid_category": True}
        name = str(values.get("name", old["name"] if old else "") or "").strip()[:160]
        if not name:
            return {"invalid_name": True}
        scope_type = str(values.get("scope_type", old["scope_type"] if old else "global") or "global")
        if scope_type not in PRODUCTION_SCOPE_TYPES:
            return {"invalid_scope": True}
        def chosen(key, default=""):
            return values[key] if key in values else (old[key] if old else default)
        attrs = chosen("attributes", _production_json_object(old["attributes_json"]) if old else {})
        truth = chosen("truth", _production_json_object(old["truth_json"]) if old else {})
        if not isinstance(attrs, dict) or not isinstance(truth, dict):
            return {"invalid_json": True}
        settings_row = conn.execute(
            "SELECT custom_fields_json FROM production_work_settings WHERE work_id=?", (wid,),
        ).fetchone()
        custom_fields = _production_json_list(settings_row["custom_fields_json"]) if settings_row else []
        attrs = dict(attrs)
        for field in custom_fields:
            field_name = str(field.get("name") or "")
            if not field_name or field_name not in attrs:
                continue
            type_ = field.get("type") or "text"
            value = attrs[field_name]
            if type_ == "number":
                try:
                    attrs[field_name] = float(value) if "." in str(value) else int(value)
                except (TypeError, ValueError):
                    attrs.pop(field_name, None)
            elif type_ == "boolean":
                attrs[field_name] = value if isinstance(value, bool) else str(value).lower() in {"1", "true", "yes", "是"}
            elif type_ in {"enum", "level"}:
                options = [str(item) for item in field.get("options", [])]
                if options and str(value) not in options:
                    attrs.pop(field_name, None)
                else:
                    attrs[field_name] = str(value)
            else:
                attrs[field_name] = str(value)[:4000]
        start_id = chosen("scope_start_chapter_id", None)
        end_id = chosen("scope_end_chapter_id", None)
        source_id = chosen("source_chapter_id", None)
        for chapter_id in (start_id, end_id, source_id):
            if chapter_id is not None and not _chapter_for_work(conn, chapter_id, wid):
                return {"invalid_chapter": True}
        scene_id = chosen("scope_scene_id", None)
        if scene_id is not None and not conn.execute(
            "SELECT 1 FROM production_scenes s JOIN chapters c ON c.id=s.chapter_id "
            "WHERE s.id=? AND c.work_id=?", (scene_id, wid),
        ).fetchone():
            return {"invalid_scene": True}
        if scope_type == "scene" and scene_id is None:
            return {"invalid_scene": True}
        if source_id is not None:
            introduced = conn.execute("SELECT ord FROM chapters WHERE id=?", (source_id,)).fetchone()
            introduced_at_ord = introduced["ord"] if introduced else None
            source_chapter_deleted = 0
        else:
            introduced_at_ord = old["introduced_at_ord"] if old and "introduced_at_ord" in old.keys() else None
            source_chapter_deleted = old["source_chapter_deleted"] if old and "source_chapter_deleted" in old.keys() else 0
        status = str(chosen("status", "confirmed") or "confirmed")
        if status not in {"confirmed", "pending", "archived"}:
            status = "confirmed"
        payload = (
            category, name, str(chosen("summary", "") or "")[:4000],
            str(chosen("detail", "") or "")[:16000], json.dumps(attrs, ensure_ascii=False),
            json.dumps(truth, ensure_ascii=False), str(chosen("reader_state", "") or "")[:6000],
            scope_type, start_id, end_id, scene_id, status, source_id, introduced_at_ord,
            source_chapter_deleted, now,
        )
        if old:
            conn.execute(
                "UPDATE production_cards SET category=?,name=?,summary=?,detail=?,attributes_json=?,truth_json=?,"
                "reader_state=?,scope_type=?,scope_start_chapter_id=?,scope_end_chapter_id=?,scope_scene_id=?,"
                "status=?,source_chapter_id=?,introduced_at_ord=?,source_chapter_deleted=?,updated_at=? WHERE id=?",
                (*payload, card_id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO production_cards(work_id,category,name,summary,detail,attributes_json,truth_json,"
                "reader_state,scope_type,scope_start_chapter_id,scope_end_chapter_id,scope_scene_id,status,"
                "source_chapter_id,introduced_at_ord,source_chapter_deleted,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (wid, *payload[:-1], now, now),
            )
            card_id = cur.lastrowid
        row = conn.execute("SELECT * FROM production_cards WHERE id=?", (card_id,)).fetchone()
        return _production_card_payload(conn, row)


def archive_production_card(card_id, user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT p.id FROM production_cards p JOIN works w ON w.id=p.work_id WHERE p.id=? AND w.user_id=?",
            (card_id, user_id),
        ).fetchone()
        if not row:
            return False
        conn.execute("UPDATE production_cards SET status='archived',updated_at=? WHERE id=?", (time.time(), card_id))
        return True


def create_production_card_version(card_id, user_id, chapter_id, state, change_summary="", evidence="",
                                   evidence_start=None, evidence_end=None, source="manual"):
    state = state if isinstance(state, dict) else {}
    now = time.time()
    with get_conn() as conn:
        card = conn.execute(
            "SELECT p.id,p.work_id FROM production_cards p JOIN works w ON w.id=p.work_id "
            "WHERE p.id=? AND w.user_id=?", (card_id, user_id),
        ).fetchone()
        if not card:
            return None
        chapter = conn.execute(
            "SELECT id,work_id,title,ord,content,content_hash FROM chapters "
            "WHERE id=? AND work_id=? AND deleted_at IS NULL",
            (chapter_id, card["work_id"]),
        ).fetchone()
        if not chapter:
            return {"invalid_chapter": True}
        if not state and not str(change_summary or "").strip():
            return {"empty_state": True}
        source_hash = (
            chapter["content_hash"] or _content_fingerprint(chapter["content"] or "")
            if source == "ai" else ""
        )
        cur = conn.execute(
            "INSERT INTO production_card_versions(card_id,chapter_id,state_json,change_summary,evidence,"
            "evidence_start,evidence_end,source_content_hash,source,stale,created_at) VALUES(?,?,?,?,?,?,?,?,?,0,?)",
            (card_id, chapter_id, json.dumps(state, ensure_ascii=False), str(change_summary or "")[:4000],
             str(evidence or "")[:4000], evidence_start, evidence_end,
             source_hash, source, now),
        )
        row = conn.execute(
            "SELECT v.*,c.title AS chapter_title,c.ord AS chapter_ord FROM production_card_versions v "
            "JOIN chapters c ON c.id=v.chapter_id WHERE v.id=?", (cur.lastrowid,),
        ).fetchone()
        result = dict(row)
        result["state"] = _production_json_object(result.pop("state_json", "{}"))
        result["is_stale"] = False
        return result


def _production_scene_payload(row):
    if not row:
        return None
    item = dict(row)
    item["refs"] = _production_json_object(item.pop("refs_json", "{}"))
    item["is_stale"] = bool(item.pop("stale", 0)) or bool(
        item.get("source_content_hash") and item.get("chapter_content_hash")
        and item["source_content_hash"] != item["chapter_content_hash"]
    )
    item.pop("chapter_content_hash", None)
    return item


def list_production_scenes(chapter_id, user_id, include_stale=False):
    with get_conn() as conn:
        if not _chapter_owned(conn, chapter_id, user_id):
            return None
        stale_clause = "" if include_stale else " AND s.stale=0"
        rows = conn.execute(
            "SELECT s.*,c.content_hash AS chapter_content_hash,p.name AS location_name "
            "FROM production_scenes s JOIN chapters c ON c.id=s.chapter_id "
            "LEFT JOIN production_cards p ON p.id=s.location_card_id "
            "WHERE s.chapter_id=?" + stale_clause + " ORDER BY s.ord,s.id", (chapter_id,),
        ).fetchall()
        return [_production_scene_payload(row) for row in rows]


def save_production_scene(chapter_id, user_id, values, scene_id=None):
    values = values if isinstance(values, dict) else {}
    now = time.time()
    with get_conn() as conn:
        chapter = conn.execute(
            "SELECT c.id,c.work_id,c.content,c.content_hash FROM chapters c JOIN works w ON w.id=c.work_id "
            "WHERE c.id=? AND w.user_id=? AND c.deleted_at IS NULL", (chapter_id, user_id),
        ).fetchone()
        if not chapter:
            return None
        old = None
        if scene_id is not None:
            old = conn.execute("SELECT * FROM production_scenes WHERE id=? AND chapter_id=?", (scene_id, chapter_id)).fetchone()
            if not old:
                return None
        def chosen(key, default=""):
            return values[key] if key in values else (old[key] if old else default)
        title = str(chosen("title", "新场景") or "新场景").strip()[:160]
        refs = values["refs"] if "refs" in values else (_production_json_object(old["refs_json"]) if old else {})
        if not isinstance(refs, dict):
            return {"invalid_refs": True}
        location_id = chosen("location_card_id", None)
        if location_id is not None:
            location = conn.execute(
                "SELECT id FROM production_cards WHERE id=? AND work_id=? AND category='location' AND status<>'archived'",
                (location_id, chapter["work_id"]),
            ).fetchone()
            if not location:
                return {"invalid_location": True}
        if old:
            default_ord = old["ord"]
        else:
            default_ord = conn.execute(
                "SELECT COALESCE(MAX(ord),0)+1 FROM production_scenes WHERE chapter_id=?", (chapter_id,)
            ).fetchone()[0]
        try:
            ord_ = max(1, int(chosen("ord", default_ord)))
        except Exception:
            ord_ = default_ord
        # Once an author edits an AI scene it becomes authored content and must
        # survive subsequent replacement of disposable AI analysis results.
        author_edit = bool(old and old["source"] == "ai" and "source" not in values)
        source = "manual" if author_edit else str(chosen("source", "manual") or "manual")[:40]
        source_hash = "" if author_edit else str(chosen("source_content_hash", "") or "")
        if source == "ai" and not source_hash:
            source_hash = chapter["content_hash"] or _content_fingerprint(chapter["content"] or "")
        payload = (
            ord_, title, str(chosen("summary", "") or "")[:6000],
            str(chosen("time_label", "") or "")[:500], location_id,
            str(chosen("goal", "") or "")[:4000], str(chosen("conflict", "") or "")[:4000],
            str(chosen("outcome", "") or "")[:4000], json.dumps(refs, ensure_ascii=False),
            str(chosen("evidence", "") or "")[:4000], chosen("evidence_start", None),
            chosen("evidence_end", None), source_hash, source, now,
        )
        if old:
            conn.execute(
                "UPDATE production_scenes SET ord=?,title=?,summary=?,time_label=?,location_card_id=?,goal=?,"
                "conflict=?,outcome=?,refs_json=?,evidence=?,evidence_start=?,evidence_end=?,source_content_hash=?,"
                "source=?,stale=0,updated_at=? WHERE id=?", (*payload, scene_id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO production_scenes(chapter_id,ord,title,summary,time_label,location_card_id,goal,conflict,"
                "outcome,refs_json,evidence,evidence_start,evidence_end,source_content_hash,source,stale,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)",
                (chapter_id, *payload[:-1], now, now),
            )
            scene_id = cur.lastrowid
        row = conn.execute(
            "SELECT s.*,c.content_hash AS chapter_content_hash,p.name AS location_name FROM production_scenes s "
            "JOIN chapters c ON c.id=s.chapter_id LEFT JOIN production_cards p ON p.id=s.location_card_id "
            "WHERE s.id=?", (scene_id,),
        ).fetchone()
        return _production_scene_payload(row)


def delete_production_scene(scene_id, user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT s.id,s.chapter_id FROM production_scenes s JOIN chapters c ON c.id=s.chapter_id "
            "JOIN works w ON w.id=c.work_id WHERE s.id=? AND w.user_id=?", (scene_id, user_id),
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM production_proposals WHERE scene_id=?", (scene_id,))
        conn.execute(
            "UPDATE production_cards SET scope_type='chapter_range',scope_start_chapter_id=?,"
            "scope_end_chapter_id=?,scope_scene_id=NULL WHERE scope_scene_id=?",
            (row["chapter_id"], row["chapter_id"], scene_id),
        )
        conn.execute("DELETE FROM production_scenes WHERE id=?", (scene_id,))
        return True


def get_production_layout(wid, user_id, chapter_id=None):
    scope_key = f"chapter:{chapter_id}" if chapter_id is not None else "overview"
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        if chapter_id is not None and not _chapter_for_work(conn, chapter_id, wid):
            return None
        row = conn.execute(
            "SELECT data_json,updated_at FROM production_canvas_layouts WHERE work_id=? AND scope_key=?",
            (wid, scope_key),
        ).fetchone()
        return {"scope_key": scope_key, "layout": _production_json_object(row["data_json"]) if row else {},
                "updated_at": row["updated_at"] if row else None}


def save_production_layout(wid, user_id, chapter_id, layout):
    if not isinstance(layout, dict):
        return {"invalid_layout": True}
    scope_key = f"chapter:{chapter_id}" if chapter_id is not None else "overview"
    now = time.time()
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        if chapter_id is not None and not _chapter_for_work(conn, chapter_id, wid):
            return {"invalid_chapter": True}
        conn.execute(
            "INSERT INTO production_canvas_layouts(work_id,chapter_id,scope_key,data_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(work_id,scope_key) DO UPDATE SET chapter_id=excluded.chapter_id,"
            "data_json=excluded.data_json,updated_at=excluded.updated_at",
            (wid, chapter_id, scope_key, json.dumps(layout, ensure_ascii=False), now, now),
        )
        return {"scope_key": scope_key, "layout": layout, "updated_at": now}


def _production_proposal_payload(row):
    if not row:
        return None
    item = dict(row)
    item["before"] = _production_json_object(item.pop("before_json", "{}"))
    item["after"] = _production_json_object(item.pop("after_json", "{}"))
    item["is_stale"] = bool(
        item.get("source_content_hash") and item.get("chapter_content_hash")
        and item["source_content_hash"] != item["chapter_content_hash"]
    )
    item.pop("chapter_content_hash", None)
    return item


def list_production_proposals(chapter_id, user_id, status=None):
    with get_conn() as conn:
        if not _chapter_owned(conn, chapter_id, user_id):
            return None
        params = [chapter_id]
        where = "p.chapter_id=?"
        if status:
            where += " AND p.status=?"
            params.append(status)
        rows = conn.execute(
            "SELECT p.*,c.content_hash AS chapter_content_hash FROM production_proposals p "
            "JOIN chapters c ON c.id=p.chapter_id WHERE " + where + " ORDER BY p.id DESC", params,
        ).fetchall()
        return [_production_proposal_payload(row) for row in rows]


def upsert_production_proposal(wid, user_id, chapter_id, item):
    item = item if isinstance(item, dict) else {}
    now = time.time()
    proposal_type = str(item.get("proposal_type") or "").strip()
    if proposal_type not in PRODUCTION_PROPOSAL_TYPES:
        return {"invalid_type": True}
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        chapter = conn.execute(
            "SELECT id,content,content_hash FROM chapters WHERE id=? AND work_id=? AND deleted_at IS NULL",
            (chapter_id, wid),
        ).fetchone()
        if not chapter:
            return {"invalid_chapter": True}
        category = str(item.get("category") or "").strip()
        if proposal_type in {"new_card", "card_update", "card_state"} and category not in PRODUCTION_CARD_CATEGORIES:
            return {"invalid_category": True}
        target_id = item.get("target_id") if isinstance(item.get("target_id"), int) else None
        if target_id is not None:
            target = conn.execute("SELECT id FROM production_cards WHERE id=? AND work_id=?", (target_id, wid)).fetchone()
            if not target:
                return {"invalid_target": True}
        name = str(item.get("name") or "").strip()[:160]
        summary = str(item.get("change_summary") or "").strip()[:4000]
        evidence = str(item.get("evidence") or "").strip()[:4000]
        if not summary and not name:
            return {"invalid": True}
        source_hash = chapter["content_hash"] or _content_fingerprint(chapter["content"] or "")
        existing = conn.execute(
            "SELECT id FROM production_proposals WHERE chapter_id=? AND proposal_type=? "
            "AND COALESCE(target_id,0)=COALESCE(?,0) AND name=? AND status='pending' ORDER BY id DESC LIMIT 1",
            (chapter_id, proposal_type, target_id, name),
        ).fetchone()
        values = (
            item.get("scene_id") if isinstance(item.get("scene_id"), int) else None,
            category, name, "ordinary" if item.get("severity") == "ordinary" else "major",
            json.dumps(item.get("before") if isinstance(item.get("before"), dict) else {}, ensure_ascii=False),
            json.dumps(item.get("after") if isinstance(item.get("after"), dict) else {}, ensure_ascii=False),
            summary, evidence,
            item.get("evidence_start") if isinstance(item.get("evidence_start"), int) else None,
            item.get("evidence_end") if isinstance(item.get("evidence_end"), int) else None,
            max(0.0, min(1.0, float(item.get("confidence") or 0))), source_hash, now,
        )
        if existing:
            conn.execute(
                "UPDATE production_proposals SET scene_id=?,category=?,name=?,severity=?,before_json=?,after_json=?,"
                "change_summary=?,evidence=?,evidence_start=?,evidence_end=?,confidence=?,source_content_hash=?,"
                "updated_at=?,resolved_at=NULL WHERE id=?", (*values, existing["id"]),
            )
            proposal_id = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO production_proposals(work_id,chapter_id,scene_id,proposal_type,target_id,category,name,"
                "severity,before_json,after_json,change_summary,evidence,evidence_start,evidence_end,confidence,status,"
                "source_content_hash,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?)",
                (wid, chapter_id, values[0], proposal_type, target_id, *values[1:-1], now, now),
            )
            proposal_id = cur.lastrowid
        row = conn.execute(
            "SELECT p.*,c.content_hash AS chapter_content_hash FROM production_proposals p "
            "JOIN chapters c ON c.id=p.chapter_id WHERE p.id=?", (proposal_id,),
        ).fetchone()
        return _production_proposal_payload(row)


def resolve_production_proposal(proposal_id, user_id, accept=True):
    now = time.time()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT p.*,c.content,c.content_hash AS chapter_content_hash FROM production_proposals p "
            "JOIN chapters c ON c.id=p.chapter_id JOIN works w ON w.id=p.work_id "
            "WHERE p.id=? AND w.user_id=?", (proposal_id, user_id),
        ).fetchone()
        if not row:
            return None
        if row["status"] != "pending":
            return {"resolved": True}
        if row["source_content_hash"] and row["source_content_hash"] != row["chapter_content_hash"]:
            conn.execute(
                "UPDATE production_proposals SET status='stale',updated_at=?,resolved_at=? WHERE id=?",
                (now, now, proposal_id),
            )
            return {"stale": True}
        if not accept:
            conn.execute(
                "UPDATE production_proposals SET status='rejected',updated_at=?,resolved_at=? WHERE id=?",
                (now, now, proposal_id),
            )
            return {"ok": True, "status": "rejected"}
        after = _production_json_object(row["after_json"])
        result = None
        if row["proposal_type"] == "new_card":
            category = row["category"] if row["category"] in PRODUCTION_CARD_CATEGORIES else "rule"
            name = str(after.get("name") or row["name"] or "新设定").strip()[:160]
            cur = conn.execute(
                "INSERT INTO production_cards(work_id,category,name,summary,detail,attributes_json,truth_json,"
                "reader_state,scope_type,status,source_chapter_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,'confirmed',?,?,?)",
                (row["work_id"], category, name, str(after.get("summary") or row["change_summary"] or "")[:4000],
                 str(after.get("detail") or "")[:16000],
                 json.dumps(after.get("attributes") if isinstance(after.get("attributes"), dict) else {}, ensure_ascii=False),
                 json.dumps(after.get("truth") if isinstance(after.get("truth"), dict) else {}, ensure_ascii=False),
                 str(after.get("reader_state") or "")[:6000],
                 after.get("scope_type") if after.get("scope_type") in PRODUCTION_SCOPE_TYPES else "global",
                 row["chapter_id"], now, now),
            )
            result = {"card_id": cur.lastrowid}
        elif row["proposal_type"] == "card_update" and row["target_id"]:
            card = conn.execute(
                "SELECT * FROM production_cards WHERE id=? AND work_id=?", (row["target_id"], row["work_id"])
            ).fetchone()
            if not card:
                return {"invalid_target": True}
            attrs = after.get("attributes") if isinstance(after.get("attributes"), dict) else _production_json_object(card["attributes_json"])
            truth = after.get("truth") if isinstance(after.get("truth"), dict) else _production_json_object(card["truth_json"])
            conn.execute(
                "UPDATE production_cards SET name=?,summary=?,detail=?,attributes_json=?,truth_json=?,reader_state=?,updated_at=? WHERE id=?",
                (str(after.get("name", card["name"]) or card["name"])[:160],
                 str(after.get("summary", card["summary"]) or "")[:4000],
                 str(after.get("detail", card["detail"]) or "")[:16000],
                 json.dumps(attrs, ensure_ascii=False), json.dumps(truth, ensure_ascii=False),
                 str(after.get("reader_state", card["reader_state"]) or "")[:6000], now, row["target_id"]),
            )
            result = {"card_id": row["target_id"]}
        elif row["proposal_type"] == "card_state" and row["target_id"]:
            state = after.get("state") if isinstance(after.get("state"), dict) else after
            conn.execute(
                "DELETE FROM production_card_versions WHERE card_id=? AND chapter_id=? AND source='ai'",
                (row["target_id"], row["chapter_id"]),
            )
            cur = conn.execute(
                "INSERT INTO production_card_versions(card_id,chapter_id,state_json,change_summary,evidence,"
                "evidence_start,evidence_end,source_content_hash,source,stale,created_at) "
                "VALUES(?,?,?,?,?,?,?,?, 'ai',0,?)",
                (row["target_id"], row["chapter_id"], json.dumps(state, ensure_ascii=False),
                 row["change_summary"], row["evidence"], row["evidence_start"], row["evidence_end"],
                 row["source_content_hash"], now),
            )
            result = {"version_id": cur.lastrowid, "card_id": row["target_id"]}
        elif row["proposal_type"] == "scene":
            next_ord = conn.execute(
                "SELECT COALESCE(MAX(ord),0)+1 FROM production_scenes WHERE chapter_id=?", (row["chapter_id"],)
            ).fetchone()[0]
            cur = conn.execute(
                "INSERT INTO production_scenes(chapter_id,ord,title,summary,time_label,location_card_id,goal,conflict,"
                "outcome,refs_json,evidence,evidence_start,evidence_end,source_content_hash,source,stale,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ai',0,?,?)",
                (row["chapter_id"], int(after.get("ord") or next_ord),
                 str(after.get("title") or row["name"] or f"场景 {next_ord}")[:160],
                 str(after.get("summary") or row["change_summary"] or "")[:6000],
                 str(after.get("time_label") or "")[:500],
                 after.get("location_card_id") if isinstance(after.get("location_card_id"), int) else None,
                 str(after.get("goal") or "")[:4000], str(after.get("conflict") or "")[:4000],
                 str(after.get("outcome") or "")[:4000],
                 json.dumps(after.get("refs") if isinstance(after.get("refs"), dict) else {}, ensure_ascii=False),
                 row["evidence"], row["evidence_start"], row["evidence_end"], row["source_content_hash"], now, now),
            )
            result = {"scene_id": cur.lastrowid}
        else:
            return {"invalid_target": True}
        conn.execute(
            "UPDATE production_proposals SET status='accepted',updated_at=?,resolved_at=? WHERE id=?",
            (now, now, proposal_id),
        )
        return {"ok": True, "status": "accepted", "result": result or {}}


def _production_scope_applies(conn, card, chapter_id):
    if card.get("scope_type") == "global" or chapter_id is None:
        return True
    chapter = conn.execute("SELECT ord FROM chapters WHERE id=?", (chapter_id,)).fetchone()
    if not chapter:
        return False
    if card.get("scope_type") == "scene":
        return bool(card.get("scope_scene_id") and conn.execute(
            "SELECT 1 FROM production_scenes WHERE id=? AND chapter_id=? AND stale=0",
            (card["scope_scene_id"], chapter_id),
        ).fetchone())
    start = conn.execute("SELECT ord FROM chapters WHERE id=?", (card.get("scope_start_chapter_id"),)).fetchone()
    end = conn.execute("SELECT ord FROM chapters WHERE id=?", (card.get("scope_end_chapter_id"),)).fetchone()
    return (not start or chapter["ord"] >= start["ord"]) and (not end or chapter["ord"] <= end["ord"])


def production_context_digest(wid, user_id, chapter_id=None, limit=80,
                              include_dynamic_state=True, global_only=False):
    cards = list_production_cards(wid, user_id, chapter_id, include_pending=False)
    if not cards:
        return ""
    lines = []
    with get_conn() as conn:
        for card in cards:
            if (len(lines) >= limit or (global_only and card.get("scope_type") != "global")
                    or not _production_scope_applies(conn, card, chapter_id)):
                continue
            pieces = [card.get("summary") or card.get("detail") or ""]
            state = (card.get("current_state") or {}).get("state") or {}
            if state and include_dynamic_state:
                public_state = "；".join(
                    f"{key}={value}" for key, value in state.items() if value and not str(key).startswith("_")
                )
                if public_state:
                    pieces.append("当前状态：" + public_state)
            truth = card.get("truth") or {}
            reader_known = (state.get("_reader_known") if include_dynamic_state else None) or truth.get("reader_known")
            character_knowledge = (state.get("_character_knowledge") if include_dynamic_state else None) or truth.get("character_knowledge")
            secrecy = (state.get("_secrecy") if include_dynamic_state else None) or truth.get("secrecy")
            reader_state = ((state.get("_reader_state") if include_dynamic_state else None)
                            or card.get("reader_state"))
            if truth.get("objective"):
                pieces.append("客观真相：" + str(truth["objective"]))
            if reader_known:
                pieces.append("读者已知：" + str(reader_known))
            if character_knowledge:
                pieces.append("人物认知：" + str(character_knowledge))
            if secrecy:
                pieces.append("保密边界（未到揭示时不得写出）：" + str(secrecy))
            if reader_state:
                pieces.append("读者侧状态：" + str(reader_state))
            content = "；".join(piece for piece in pieces if piece)
            lines.append(f"[{card['category_label']}] {card['name']}：{content}")
    label = "生产画布全书设定" if global_only else "生产画布已确认设定（按当前章节时点生效）"
    return label + "：\n" + "\n".join(lines) if lines else ""


def get_production_overview(wid, user_id):
    with get_conn() as conn:
        if not _work_owned(conn, wid, user_id):
            return None
        chapters = [dict(row) for row in conn.execute(
            "SELECT c.id,c.title,c.ord,c.workflow_status,c.analysis_status,c.production_analysis_status,"
            "c.production_analyzed_at,length(c.content) AS chars,"
            "(SELECT COUNT(*) FROM production_scenes s WHERE s.chapter_id=c.id AND s.stale=0) AS scene_count,"
            "(SELECT COUNT(*) FROM production_proposals p WHERE p.chapter_id=c.id AND p.status='pending') AS pending_count,"
            "(SELECT COUNT(*) FROM production_impact_flags f WHERE f.affected_chapter_id=c.id AND f.status='open') AS impact_count "
            "FROM chapters c WHERE c.work_id=? AND c.deleted_at IS NULL ORDER BY c.ord", (wid,),
        )]
        counts = {category: 0 for category in PRODUCTION_CARD_CATEGORIES}
        for row in conn.execute(
            "SELECT category,COUNT(*) AS count FROM production_cards WHERE work_id=? AND status='confirmed' GROUP BY category",
            (wid,),
        ):
            counts[row["category"]] = row["count"]
        pending_cards = conn.execute(
            "SELECT COUNT(*) FROM production_cards WHERE work_id=? AND status='pending'", (wid,)
        ).fetchone()[0]
    return {"work_id": wid, "chapters": chapters, "card_counts": counts,
            "pending_card_count": pending_cards, "settings": get_production_settings(wid, user_id),
            "layout": get_production_layout(wid, user_id)}


def get_chapter_production(chapter_id, user_id):
    chapter = get_chapter_meta(chapter_id, user_id)
    if not chapter:
        return None
    wid = chapter["work_id"]
    before_cards = list_production_cards(wid, user_id, chapter_id, before=True, include_pending=False) or []
    after_cards = list_production_cards(wid, user_id, chapter_id, before=False, include_pending=False) or []
    before_characters = list_character_cards(wid, user_id, chapter_id, before=True) or []
    after_characters = list_character_cards(wid, user_id, chapter_id, before=False) or []
    scenes = list_production_scenes(chapter_id, user_id) or []
    proposals = list_production_proposals(chapter_id, user_id) or []
    with get_conn() as conn:
        impacts = [dict(row) for row in conn.execute(
            "SELECT f.*,c.title AS source_chapter_title,c.ord AS source_chapter_ord "
            "FROM production_impact_flags f JOIN chapters c ON c.id=f.source_chapter_id "
            "WHERE f.affected_chapter_id=? AND f.status='open' ORDER BY f.id DESC", (chapter_id,),
        )]
    related_ids = set()
    for scene in scenes:
        if scene.get("location_card_id"):
            related_ids.add(scene["location_card_id"])
        refs = scene.get("refs") or {}
        for key in ("card_ids", "skill_ids", "item_ids", "location_ids", "rule_ids"):
            for value in refs.get(key, []) if isinstance(refs.get(key), list) else []:
                if isinstance(value, int):
                    related_ids.add(value)
    for item in after_cards:
        item["related"] = item["id"] in related_ids or bool(item.get("current_state") and item["current_state"].get("chapter_id") == chapter_id)
    return {
        "chapter": chapter, "before": {"characters": before_characters, "cards": before_cards},
        "after": {"characters": after_characters, "cards": after_cards},
        "scenes": scenes, "proposals": proposals, "impacts": impacts,
        "settings": get_production_settings(wid, user_id),
        "layout": get_production_layout(wid, user_id, chapter_id),
    }


def resolve_production_impact(impact_id, user_id):
    now = time.time()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT f.id FROM production_impact_flags f JOIN works w ON w.id=f.work_id "
            "WHERE f.id=? AND w.user_id=?", (impact_id, user_id),
        ).fetchone()
        if not row:
            return False
        conn.execute("UPDATE production_impact_flags SET status='resolved',resolved_at=? WHERE id=?", (now, impact_id))
        return True


def _invalidate_production_chapter(conn, chapter_id, reason):
    chapter = conn.execute("SELECT id,work_id,ord FROM chapters WHERE id=?", (chapter_id,)).fetchone()
    if not chapter:
        return
    conn.execute(
        "UPDATE chapters SET production_analysis_status='needs_review',production_analysis_hash='' WHERE id=?",
        (chapter_id,),
    )
    conn.execute("UPDATE production_scenes SET stale=1 WHERE chapter_id=? AND source_content_hash<>''", (chapter_id,))
    conn.execute(
        "UPDATE production_card_versions SET stale=1 WHERE chapter_id=? AND source_content_hash<>''", (chapter_id,)
    )
    conn.execute(
        "UPDATE production_proposals SET status='stale',updated_at=?,resolved_at=? "
        "WHERE chapter_id=? AND status='pending' AND source_content_hash<>''",
        (time.time(), time.time(), chapter_id),
    )


def propagate_world_state_impact(chapter_id, user_id, summary):
    """Create downstream review flags once, after semantic analysis found real changes."""
    now = time.time()
    with get_conn() as conn:
        chapter = conn.execute(
            "SELECT c.id,c.work_id,c.ord FROM chapters c JOIN works w ON w.id=c.work_id "
            "WHERE c.id=? AND w.user_id=?", (chapter_id, user_id),
        ).fetchone()
        if not chapter:
            return False
        conn.execute(
            "INSERT INTO production_impact_flags(work_id,source_chapter_id,affected_chapter_id,summary,status,created_at) "
            "SELECT ?,?,target.id,?,'open',? FROM chapters target "
            "WHERE target.work_id=? AND target.deleted_at IS NULL AND target.ord>? "
            "AND NOT EXISTS (SELECT 1 FROM production_impact_flags f WHERE f.source_chapter_id=? "
            "AND f.affected_chapter_id=target.id AND f.status='open')",
            (chapter["work_id"], chapter_id,
             (summary or "前序章节状态已变化，请检查本章连续性")[:500], now,
             chapter["work_id"], chapter["ord"], chapter_id),
        )
        return True


def replace_ai_production_scenes(chapter_id, user_id, scenes):
    scenes = scenes if isinstance(scenes, list) else []
    now = time.time()
    with get_conn() as conn:
        chapter = conn.execute(
            "SELECT c.id,c.work_id,c.content,c.content_hash FROM chapters c JOIN works w ON w.id=c.work_id "
            "WHERE c.id=? AND w.user_id=? AND c.deleted_at IS NULL", (chapter_id, user_id),
        ).fetchone()
        if not chapter:
            return None
        source_hash = chapter["content_hash"] or _content_fingerprint(chapter["content"] or "")
        previous = [dict(row) for row in conn.execute(
            "SELECT * FROM production_scenes WHERE chapter_id=? AND source='ai' AND stale=0 ORDER BY ord,id",
            (chapter_id,),
        ).fetchall()]
        unused = {row["id"]: row for row in previous}
        normalized_titles = [re.sub(r"\s+", "", str(item.get("title") or "")).casefold()
                             for item in scenes if isinstance(item, dict)]
        result = []
        for index, raw in enumerate(scenes[:40]):
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or f"场景 {index + 1}").strip()[:160]
            refs = raw.get("refs") if isinstance(raw.get("refs"), dict) else {}
            location_id = raw.get("location_card_id") if isinstance(raw.get("location_card_id"), int) else None
            if location_id and not conn.execute(
                "SELECT 1 FROM production_cards WHERE id=? AND work_id=? AND category='location'",
                (location_id, chapter["work_id"]),
            ).fetchone():
                location_id = None
            evidence_start = raw.get("evidence_start") if isinstance(raw.get("evidence_start"), int) else None
            evidence_end = raw.get("evidence_end") if isinstance(raw.get("evidence_end"), int) else None
            matched = None
            normalized_evidence = re.sub(r"\s+", "", str(raw.get("evidence") or "")).casefold()
            if normalized_evidence:
                evidence_matches = [row for row in unused.values()
                                    if re.sub(r"\s+", "", row.get("evidence") or "").casefold()
                                    == normalized_evidence]
                if len(evidence_matches) == 1:
                    matched = evidence_matches[0]
            if matched is None:
                normalized_title = re.sub(r"\s+", "", title).casefold()
                title_matches = [row for row in unused.values()
                                 if re.sub(r"\s+", "", row.get("title") or "").casefold()
                                 == normalized_title]
                if normalized_title and normalized_titles.count(normalized_title) == 1 and len(title_matches) == 1:
                    matched = title_matches[0]
            if matched is None:
                def similarity(row):
                    def ratio(left, right):
                        left = re.sub(r"\s+", "", str(left or "")).casefold()
                        right = re.sub(r"\s+", "", str(right or "")).casefold()
                        return SequenceMatcher(None, left, right).ratio() if left and right else 0.0
                    title_score = ratio(title, row.get("title"))
                    evidence_score = ratio(raw.get("evidence"), row.get("evidence"))
                    goal_score = ratio(raw.get("goal"), row.get("goal"))
                    summary_score = ratio(raw.get("summary"), row.get("summary"))
                    location_score = 1.0 if location_id is not None and location_id == row.get("location_card_id") else 0.0
                    score = (title_score * 0.42 + evidence_score * 0.28 + goal_score * 0.15
                             + summary_score * 0.10 + location_score * 0.05)
                    return score, max(title_score, evidence_score)
                candidates = sorted(
                    ((similarity(row), row) for row in unused.values()),
                    key=lambda item: item[0][0], reverse=True,
                )
                if candidates:
                    (best_score, best_anchor), best_row = candidates[0]
                    runner_up = candidates[1][0][0] if len(candidates) > 1 else 0.0
                    if best_anchor >= 0.72 and best_score >= 0.68 and best_score - runner_up >= 0.08:
                        matched = best_row
            payload = (
                index + 1, title, str(raw.get("summary") or "")[:6000],
                str(raw.get("time_label") or "")[:500], location_id,
                str(raw.get("goal") or "")[:4000], str(raw.get("conflict") or "")[:4000],
                str(raw.get("outcome") or "")[:4000], json.dumps(refs, ensure_ascii=False),
                str(raw.get("evidence") or "")[:4000], evidence_start, evidence_end, source_hash, now,
            )
            if matched:
                scene_id = matched["id"]
                unused.pop(scene_id, None)
                conn.execute(
                    "UPDATE production_scenes SET ord=?,title=?,summary=?,time_label=?,location_card_id=?,goal=?,"
                    "conflict=?,outcome=?,refs_json=?,evidence=?,evidence_start=?,evidence_end=?,"
                    "source_content_hash=?,source='ai',stale=0,updated_at=? WHERE id=?",
                    (*payload, scene_id),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO production_scenes(chapter_id,ord,title,summary,time_label,location_card_id,goal,conflict,"
                    "outcome,refs_json,evidence,evidence_start,evidence_end,source_content_hash,source,stale,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ai',0,?,?)",
                    (chapter_id, *payload[:-1], now, now),
                )
                scene_id = cur.lastrowid
            result.append(scene_id)
        for stale_id in unused:
            conn.execute("UPDATE production_scenes SET stale=1,updated_at=? WHERE id=?", (now, stale_id))
            conn.execute(
                "UPDATE production_cards SET scope_type='chapter_range',scope_start_chapter_id=?,"
                "scope_end_chapter_id=?,scope_scene_id=NULL WHERE scope_scene_id=?",
                (chapter_id, chapter_id, stale_id),
            )
            conn.execute(
                "UPDATE production_proposals SET status='stale',updated_at=?,resolved_at=? "
                "WHERE scene_id=? AND status='pending'", (now, now, stale_id),
            )
        conn.execute(
            "UPDATE chapters SET production_analysis_status='current',production_analysis_hash=?,"
            "production_analyzed_at=? WHERE id=?", (source_hash, now, chapter_id),
        )
        return {"scene_ids": result, "source_content_hash": source_hash}


def mark_production_analysis_current(chapter_id, user_id):
    now = time.time()
    with get_conn() as conn:
        chapter = conn.execute(
            "SELECT c.id,c.content,c.content_hash FROM chapters c JOIN works w ON w.id=c.work_id "
            "WHERE c.id=? AND w.user_id=?", (chapter_id, user_id),
        ).fetchone()
        if not chapter:
            return None
        source_hash = chapter["content_hash"] or _content_fingerprint(chapter["content"] or "")
        conn.execute(
            "UPDATE chapters SET production_analysis_status='current',production_analysis_hash=?,"
            "production_analyzed_at=? WHERE id=?", (source_hash, now, chapter_id),
        )
        return {"status": "current", "source_content_hash": source_hash, "analyzed_at": now}


def _world_state_analysis_payload(row):
    if not row:
        return None
    try:
        result = json.loads(row["result_json"])
    except (TypeError, json.JSONDecodeError):
        return None
    return {
        "id": row["id"], "result": result, "input_hash": row["input_hash"],
        "source_content_hash": row["source_content_hash"], "provider": row["provider"],
        "model": row["model"], "generation": row["generation"],
        "applied_at": row["applied_at"], "created_at": row["created_at"],
    }


def get_world_state_analysis(chapter_id, user_id, input_hash, analyzer_version, provider, model):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT a.* FROM world_state_analyses a "
            "JOIN chapters c ON c.id=a.chapter_id JOIN works w ON w.id=c.work_id "
            "WHERE a.chapter_id=? AND w.user_id=? AND a.input_hash=? AND a.analyzer_version=? "
            "AND a.provider=? AND a.model=? ORDER BY a.generation DESC LIMIT 1",
            (chapter_id, user_id, input_hash or "", analyzer_version, provider or "", model or ""),
        ).fetchone()
        return _world_state_analysis_payload(row)


def create_world_state_analysis(chapter_id, user_id, input_hash, source_content_hash,
                                analyzer_version, provider, model, result, force=False):
    """Create one immutable analysis generation, or reuse the current generation."""
    now = time.time()
    with get_conn() as conn:
        if not _chapter_owned(conn, chapter_id, user_id):
            return None
        existing = conn.execute(
            "SELECT * FROM world_state_analyses WHERE chapter_id=? AND input_hash=? AND analyzer_version=? "
            "AND provider=? AND model=? ORDER BY generation DESC LIMIT 1",
            (chapter_id, input_hash or "", analyzer_version, provider or "", model or ""),
        ).fetchone()
        if existing and not force:
            payload = _world_state_analysis_payload(existing)
            if payload:
                payload["created"] = False
            return payload
        generation = int(existing["generation"] or 0) + 1 if existing else 1
        cur = conn.execute(
            "INSERT INTO world_state_analyses(chapter_id,input_hash,source_content_hash,analyzer_version,"
            "provider,model,generation,result_json,applied_at,created_at) VALUES(?,?,?,?,?,?,?,?,NULL,?)",
            (chapter_id, input_hash or "", source_content_hash or "", analyzer_version,
             provider or "", model or "", generation, json.dumps(result, ensure_ascii=False), now),
        )
        row = conn.execute("SELECT * FROM world_state_analyses WHERE id=?", (cur.lastrowid,)).fetchone()
        payload = _world_state_analysis_payload(row)
        if payload:
            payload["created"] = True
        return payload


def mark_world_state_analysis_applied(analysis_id, user_id):
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE world_state_analyses SET applied_at=? WHERE id=? AND applied_at IS NULL "
            "AND chapter_id IN (SELECT c.id FROM chapters c JOIN works w ON w.id=c.work_id WHERE w.user_id=?)",
            (now, analysis_id, user_id),
        )
        return cur.rowcount == 1


# ---------- AI Skills（用户可复用的 agent 指令模板）----------

def _sync_builtin_agent_skills(conn, user_id=None):
    """Install/update repository-owned Skills for existing users and newly registered accounts."""
    packages = builtin_skills.load_builtin_skills()
    if not packages:
        return
    if user_id is None:
        user_ids = [row["id"] for row in conn.execute("SELECT id FROM users")]
    else:
        user_ids = [user_id]
    now = time.time()
    for uid in user_ids:
        for package in packages:
            row = conn.execute(
                "SELECT id,name,description,instruction,source_kind,source_markdown,enabled "
                "FROM agent_skills WHERE user_id=? AND builtin_key=?",
                (uid, package["builtin_key"]),
            ).fetchone()
            changed = not row or any((
                row["name"] != package["name"],
                row["description"] != package["description"],
                row["instruction"] != package["instruction"],
                row["source_kind"] != "builtin",
                row["source_markdown"] != package["source_markdown"],
                not row["enabled"],
            ))
            if row:
                skill_id = row["id"]
                existing_resources = {
                    item["path"]: item["content"] for item in conn.execute(
                        "SELECT path,content FROM agent_skill_resources WHERE skill_id=?", (skill_id,)
                    )
                }
                wanted_resources = {item["path"]: item["content"] for item in package["resources"]}
                resources_changed = existing_resources != wanted_resources
                if changed:
                    conn.execute(
                        "UPDATE agent_skills SET work_id=NULL,name=?,description=?,instruction=?,source_kind='builtin',"
                        "source_markdown=?,enabled=1,updated_at=? WHERE id=?",
                        (package["name"], package["description"], package["instruction"],
                         package["source_markdown"], now, skill_id),
                    )
                if resources_changed:
                    conn.execute("DELETE FROM agent_skill_resources WHERE skill_id=?", (skill_id,))
                    for resource in package["resources"]:
                        conn.execute(
                            "INSERT INTO agent_skill_resources(skill_id,path,content,created_at) VALUES(?,?,?,?)",
                            (skill_id, resource["path"], resource["content"], now),
                        )
                    if not changed:
                        conn.execute("UPDATE agent_skills SET updated_at=? WHERE id=?", (now, skill_id))
            else:
                cur = conn.execute(
                    "INSERT INTO agent_skills(user_id,work_id,name,description,instruction,source_kind,"
                    "source_markdown,builtin_key,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (uid, None, package["name"], package["description"], package["instruction"], "builtin",
                     package["source_markdown"], package["builtin_key"], 1, now, now),
                )
                for resource in package["resources"]:
                    conn.execute(
                        "INSERT INTO agent_skill_resources(skill_id,path,content,created_at) VALUES(?,?,?,?)",
                        (cur.lastrowid, resource["path"], resource["content"], now),
                    )


def is_builtin_agent_skill(skill_id, user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT source_kind FROM agent_skills WHERE id=? AND user_id=?", (skill_id, user_id)
        ).fetchone()
        return bool(row and row["source_kind"] == "builtin")

def list_agent_skills(user_id, work_id=None):
    """取用户可见的 Skill：通用 Skill 加当前作品专用 Skill。"""
    with get_conn() as conn:
        if work_id is not None and not _work_owned(conn, work_id, user_id):
            return None
        if work_id is None:
            rows = conn.execute(
                "SELECT s.id, s.user_id, s.work_id, s.name, s.description, s.instruction, s.source_kind, s.enabled, s.created_at, s.updated_at, "
                "(SELECT COUNT(*) FROM agent_skill_resources r WHERE r.skill_id=s.id) AS resource_count "
                "FROM agent_skills s WHERE s.user_id=? AND s.work_id IS NULL "
                "ORDER BY CASE WHEN s.source_kind='builtin' THEN 0 ELSE 1 END, s.updated_at DESC, s.id DESC",
                (user_id,),
            )
        else:
            rows = conn.execute(
                "SELECT s.id, s.user_id, s.work_id, s.name, s.description, s.instruction, s.source_kind, s.enabled, s.created_at, s.updated_at, "
                "(SELECT COUNT(*) FROM agent_skill_resources r WHERE r.skill_id=s.id) AS resource_count "
                "FROM agent_skills s WHERE s.user_id=? AND (s.work_id IS NULL OR s.work_id=?) "
                "ORDER BY CASE WHEN s.source_kind='builtin' THEN 0 ELSE 1 END, "
                "CASE WHEN s.work_id IS NULL THEN 0 ELSE 1 END, s.updated_at DESC, s.id DESC",
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


def _agent_skill_mutable(conn, skill_id, user_id):
    row = conn.execute(
        "SELECT source_kind FROM agent_skills WHERE id=? AND user_id=?", (skill_id, user_id)
    ).fetchone()
    return bool(row and row["source_kind"] != "builtin")


def update_agent_skill(skill_id, user_id, work_id, name, description, instruction, enabled):
    now = time.time()
    with get_conn() as conn:
        if not _agent_skill_mutable(conn, skill_id, user_id):
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
        if not _agent_skill_mutable(conn, skill_id, user_id):
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
                "ORDER BY CASE WHEN source_kind='builtin' THEN 0 ELSE 1 END, updated_at DESC, id DESC LIMIT ?",
                (user_id, limit),
            )
        else:
            rows = conn.execute(
                "SELECT id, name, description, source_kind FROM agent_skills "
                "WHERE user_id=? AND enabled=1 AND (work_id IS NULL OR work_id=?) "
                "ORDER BY CASE WHEN source_kind='builtin' THEN 0 ELSE 1 END, "
                "CASE WHEN work_id IS NULL THEN 0 ELSE 1 END, updated_at DESC, id DESC LIMIT ?",
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
            "outcome_summary,outcome_source_hash,outcome_source_revision,outcome_updated_at, "
            "branch_of_chapter_id, branch_from_revision_id, content_revision, analysis_status, "
            "analysis_reason, analysis_checked_at, production_analysis_status, production_analyzed_at, "
            "(SELECT COUNT(*) FROM story_memory_items m WHERE m.chapter_id=chapters.id "
            "AND m.status='confirmed' AND m.stale=0) AS confirmed_memory_count, "
            "(SELECT COUNT(*) FROM production_scenes s WHERE s.chapter_id=chapters.id) AS production_scene_count, "
            "(SELECT COUNT(*) FROM production_proposals p WHERE p.chapter_id=chapters.id AND p.status='pending') "
            "AS production_pending_count "
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
            return None
        current = [row["id"] for row in conn.execute(
            "SELECT id FROM chapters WHERE work_id=? AND deleted_at IS NULL ORDER BY ord,id", (wid,),
        ).fetchall()]
        try:
            ids = [int(value) for value in ids]
        except (TypeError, ValueError):
            return {"invalid_order": True}
        if len(ids) != len(set(ids)) or set(ids) != set(current):
            return {"invalid_order": True}
        changed_at = next((index for index, value in enumerate(ids) if current[index] != value), None)
        if changed_at is None:
            return {"ok": True, "affected": 0}
        for i, cid in enumerate(ids):
            conn.execute("UPDATE chapters SET ord=? WHERE id=? AND work_id=?", (-1000000 - i, cid, wid))
        for i, cid in enumerate(ids):
            conn.execute(
                "UPDATE chapters SET ord=? WHERE id=? AND work_id=?",
                (i + 1, cid, wid),
            )
        affected_ids = ids[changed_at:]
        placeholders = ",".join("?" for _ in affected_ids)
        reason = "章节顺序已变化，需要重新检查 World State 时间线"
        conn.execute(
            f"UPDATE chapters SET production_analysis_status='needs_review',production_analysis_hash='',"
            f"analysis_status='needs_review',analysis_reason=? WHERE id IN ({placeholders})",
            (reason, *affected_ids),
        )
        for table in ("production_scenes", "production_card_versions", "entity_state_versions",
                      "plot_state_versions", "story_memory_items"):
            conn.execute(
                f"UPDATE {table} SET stale=1 WHERE chapter_id IN ({placeholders}) AND source_content_hash<>''",
                affected_ids,
            )
        conn.execute(
            f"UPDATE production_proposals SET status='stale',updated_at=?,resolved_at=? "
            f"WHERE chapter_id IN ({placeholders}) AND status='pending'", (time.time(), time.time(), *affected_ids),
        )
        conn.execute(
            f"UPDATE entity_state_proposals SET status='stale',updated_at=?,resolved_at=? "
            f"WHERE chapter_id IN ({placeholders}) AND status='pending'", (time.time(), time.time(), *affected_ids),
        )
        conn.execute(
            f"UPDATE plot_state_proposals SET status='stale',updated_at=?,resolved_at=? "
            f"WHERE chapter_id IN ({placeholders}) AND status='pending'", (time.time(), time.time(), *affected_ids),
        )
        conn.execute(
            f"UPDATE story_plan_realizations SET stale=1,updated_at=? "
            f"WHERE chapter_id IN ({placeholders})", (time.time(), *affected_ids),
        )
        return {"ok": True, "affected": len(affected_ids)}


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
            "outcome_summary,outcome_source_hash,outcome_source_revision,outcome_updated_at, "
            "content_hash, content_revision, analysis_status, analysis_reason, analysis_checked_at, "
            "production_analysis_status, production_analysis_hash, production_analyzed_at "
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
            "workflow_checked_at,outcome_summary,outcome_source_hash,outcome_source_revision,outcome_updated_at, "
            "updated_at,content_revision,analysis_status,analysis_reason,analysis_checked_at "
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


def save_chapter_outcome_summary(cid, user_id, summary, expected_content_hash=None, expected_revision=None):
    """Save a prose-derived outcome; it is valid only for this exact chapter revision."""
    now = time.time()
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        row = conn.execute(
            "SELECT content_hash,content_revision FROM chapters WHERE id=? AND deleted_at IS NULL", (cid,),
        ).fetchone()
        if not row:
            return None
        if expected_content_hash is not None and (row["content_hash"] or "") != (expected_content_hash or ""):
            return {"stale": True}
        if expected_revision is not None and int(row["content_revision"] or 1) != int(expected_revision):
            return {"stale": True}
        clean = _clean_story_plan_text("summary", summary)
        conn.execute(
            "UPDATE chapters SET outcome_summary=?,outcome_source_hash=?,outcome_source_revision=?,"
            "outcome_updated_at=?,updated_at=? WHERE id=?",
            (clean, row["content_hash"] or _content_fingerprint(""), row["content_revision"] or 1, now, now, cid),
        )
        return {
            "ok": True, "outcome_summary": clean, "outcome_source_hash": row["content_hash"] or "",
            "outcome_source_revision": row["content_revision"] or 1, "outcome_updated_at": now,
        }


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
    _invalidate_production_chapter(conn, cid, reason)
    conn.execute(
        "UPDATE story_plan_realizations SET stale=1,updated_at=? WHERE chapter_id=? AND source_content_hash<>?",
        (now, cid, new_hash),
    )
    if invalidate:
        _invalidate_chapter_derived_state(conn, cid, reason)
    return {"changed": True, "content_hash": new_hash, "content_revision": revision}


def update_chapter(cid, user_id, title, content, notes, expected_revision=None):
    """Update the editor document with optimistic concurrency when requested.

    Agent/internal callers may omit ``expected_revision``. Browser editor saves must
    provide it so a stale tab cannot silently overwrite a newer device revision.
    """
    now = time.time()
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        row = conn.execute(
            "SELECT id,work_id,title,content,notes,content_hash,content_revision,analysis_status,analysis_reason "
            "FROM chapters "
            "WHERE id=? AND deleted_at IS NULL", (cid,),
        ).fetchone()
        if not row:
            return None
        current_revision = max(1, int(row["content_revision"] or 1))
        if expected_revision is not None:
            try:
                expected_revision = int(expected_revision)
            except (TypeError, ValueError):
                return {"invalid_revision": True}
            if expected_revision != current_revision:
                return {
                    "conflict": True,
                    "server": {
                        "id": row["id"], "title": row["title"] or "", "content": row["content"] or "",
                        "notes": row["notes"] or "", "content_revision": current_revision,
                    },
                }

        next_title = row["title"] if title is None else str(title)
        next_content = row["content"] if content is None else str(content)
        next_notes = row["notes"] if notes is None else str(notes)
        content_changed = next_content != (row["content"] or "")
        document_changed = content_changed or next_title != (row["title"] or "") or next_notes != (row["notes"] or "")
        if not document_changed:
            return {
                "ok": True, "content_revision": current_revision, "content_hash": row["content_hash"] or "",
                "analysis_status": row["analysis_status"], "analysis_reason": row["analysis_reason"] or "",
            }

        next_revision = current_revision + 1
        next_hash = _content_fingerprint(next_content) if content_changed else (row["content_hash"] or _content_fingerprint(next_content))
        analysis_status = "needs_review" if content_changed else None
        cur = conn.execute(
            "UPDATE chapters SET title=?,content=?,notes=?,content_hash=?,content_revision=?,"
            "analysis_status=COALESCE(?,analysis_status),"
            "analysis_reason=CASE WHEN ? IS NOT NULL THEN ? ELSE analysis_reason END,"
            "analysis_checked_at=CASE WHEN ? IS NOT NULL THEN NULL ELSE analysis_checked_at END,updated_at=? "
            "WHERE id=? AND content_revision=?",
            (next_title, next_content, next_notes, next_hash, next_revision, analysis_status,
             analysis_status, "正文已修改，需重新分析", analysis_status, now, cid, current_revision),
        )
        if cur.rowcount != 1:
            latest = conn.execute(
                "SELECT id,title,content,notes,content_revision FROM chapters WHERE id=?", (cid,),
            ).fetchone()
            return {"conflict": True, "server": dict(latest) if latest else {}}
        if content_changed:
            _invalidate_production_chapter(conn, cid, "正文已修改，需重新分析")
            conn.execute(
                "UPDATE story_plan_realizations SET stale=1,updated_at=? "
                "WHERE chapter_id=? AND source_content_hash<>?", (now, cid, next_hash),
            )
        conn.execute("UPDATE works SET updated_at=? WHERE id=?", (now, row["work_id"]))
        return {
            "ok": True, "content_revision": next_revision, "content_hash": next_hash,
            "analysis_status": "needs_review" if content_changed else row["analysis_status"],
            "analysis_reason": "正文已修改，需重新分析" if content_changed else (row["analysis_reason"] or ""),
        }


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
        conn.execute("UPDATE story_plan_realizations SET stale=1,updated_at=? WHERE chapter_id=?", (now, cid))
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
        conn.execute("DELETE FROM production_proposals WHERE chapter_id=?", (cid,))
        conn.execute(
            "UPDATE production_cards SET status='archived',scope_scene_id=NULL WHERE scope_scene_id IN "
            "(SELECT id FROM production_scenes WHERE chapter_id=?)", (cid,),
        )
        conn.execute("DELETE FROM production_scenes WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM production_card_versions WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM production_impact_flags WHERE source_chapter_id=? OR affected_chapter_id=?", (cid, cid))
        conn.execute("DELETE FROM production_canvas_layouts WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM world_state_analyses WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM story_plan_realizations WHERE chapter_id=?", (cid,))
        conn.execute("DELETE FROM story_plan_links WHERE target_type='chapter' AND target_id=?", (cid,))
        conn.execute("UPDATE story_plan_nodes SET chapter_id=NULL WHERE chapter_id=?", (cid,))
        conn.execute("UPDATE story_plan_nodes SET scope_start_chapter_id=NULL WHERE scope_start_chapter_id=?", (cid,))
        conn.execute("UPDATE story_plan_nodes SET scope_end_chapter_id=NULL WHERE scope_end_chapter_id=?", (cid,))
        conn.execute(
            "UPDATE production_cards SET introduced_at_ord=COALESCE(introduced_at_ord,"
            "(SELECT ord FROM chapters WHERE id=?)),source_chapter_deleted=1,source_chapter_id=NULL "
            "WHERE source_chapter_id=?", (cid, cid),
        )
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


def create_chapter_conflict_branch(cid, user_id, title, content, notes=""):
    """Preserve a stale browser draft as a new branch without touching canonical text."""
    now = time.time()
    with get_conn() as conn:
        if not _chapter_owned(conn, cid, user_id):
            return None
        source = conn.execute(
            "SELECT work_id,title,workflow_goal FROM chapters WHERE id=? AND deleted_at IS NULL", (cid,),
        ).fetchone()
        if not source:
            return None
        branch_title = (title or "").strip()[:200] or f"{source['title'] or '章节'} · 冲突分支"
        branch_content = str(content or "")
        ord_ = conn.execute(
            "SELECT COALESCE(MAX(ord),0)+1 FROM chapters WHERE work_id=?", (source["work_id"],),
        ).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO chapters(work_id,title,ord,content,notes,workflow_status,workflow_goal,"
            "branch_of_chapter_id,branch_from_revision_id,content_hash,content_revision,analysis_status,analysis_reason,"
            "created_at,updated_at) VALUES(?,?,?,?,?,'drafting',?,?,NULL,?,?,?,?,?,?)",
            (source["work_id"], branch_title, ord_, branch_content, str(notes or ""),
             source["workflow_goal"] or "", cid, _content_fingerprint(branch_content), 1,
             "needs_review" if branch_content.strip() else "fresh",
             "多端编辑冲突分支需要独立分析" if branch_content.strip() else "", now, now),
        )
        conn.execute("UPDATE works SET updated_at=? WHERE id=?", (now, source["work_id"]))
        return {"id": cur.lastrowid, "work_id": source["work_id"], "title": branch_title,
                "ord": ord_, "branch_of_chapter_id": cid}


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
