"""Durable multimodal inspiration library.

Story memory records established facts. Inspirations are deliberately separate:
they remain optional creative material until the author actually uses them.
"""
from __future__ import annotations

import atexit
import base64
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import config
import db
import llm


SOURCE_TYPES = {
    "text", "voice_note", "image", "meme", "audio", "music", "video",
    "link", "quote", "real_event", "mixed",
}
CATEGORIES = {
    "general", "comedy", "plot", "dialogue", "character", "emotion", "visual",
    "music", "sound", "camera", "editing", "worldbuilding", "action",
    "romance", "horror", "suspense", "production",
}
LIBRARY_STATUSES = {"inbox", "available", "archived", "rejected"}
REUSE_MODES = {"one_off", "adaptable", "running_gag", "reference_only"}
USE_POLICIES = {"suggest", "generate_candidate", "auto_allowed", "manual_only"}
ASSET_TYPES = {"image", "audio", "music", "video", "voice_note", "web_link"}
USAGE_TYPES = {"recommended", "reserved", "adapted", "inserted", "referenced", "combined", "rejected"}
USAGE_STATUSES = {"suggested", "accepted", "applied", "rejected", "cancelled"}

_LIST_FIELDS = ("tags", "mood_tags", "usage_tags", "search_keywords")
_LIST_COLUMNS = {
    "tags": "tags_json",
    "mood_tags": "mood_tags_json",
    "usage_tags": "usage_tags_json",
    "search_keywords": "search_keywords_json",
}
_TEXT_LIMITS = {
    "title": 160,
    "raw_text": 30_000,
    "user_impression": 12_000,
    "core_mechanism": 4_000,
    "creative_summary": 8_000,
    "suitable_context": 8_000,
    "adaptation_notes": 12_000,
    "production_notes": 12_000,
    "constraints_text": 6_000,
}

_ALLOWED_EXTENSIONS = {
    "image": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"},
    "audio": {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".webm"},
    "video": {".mp4", ".mov", ".webm", ".mkv"},
}
_MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
    "audio/ogg": ".ogg",
    "audio/flac": ".flac",
    "audio/webm": ".webm",
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
}

_worker_stop = threading.Event()
_worker_thread = None


class InspirationError(ValueError):
    pass


def _clip(value, limit):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return value.strip()[:limit]


def _json_list(value, limit=24, item_limit=80):
    if isinstance(value, str):
        value = re.split(r"[,，、;\n]+", value)
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for item in value:
        text = _clip(item, item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _importance(value, default=3):
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise InspirationError("重要度必须是 1 到 5 的整数")
    try:
        return max(1, min(int(value), 5))
    except (TypeError, ValueError):
        raise InspirationError("重要度必须是 1 到 5 的整数")


def _decode_list(raw):
    try:
        value = json.loads(raw or "[]")
    except Exception:
        value = []
    return _json_list(value)


def _search_tags(payload):
    values = []
    for field in _LIST_FIELDS:
        values.extend(_json_list(payload.get(field)))
    return " ".join(dict.fromkeys(values))


def _default_title(raw_text, source_type):
    line = next((line.strip() for line in (raw_text or "").splitlines() if line.strip()), "")
    if line:
        return line[:28] + ("…" if len(line) > 28 else "")
    labels = {
        "image": "图片灵感", "meme": "梗图灵感", "music": "音乐灵感",
        "audio": "音频灵感", "voice_note": "语音灵感", "video": "视频灵感",
        "link": "链接灵感",
    }
    return labels.get(source_type, "新灵感")


def _normalize_source_url(value):
    source_url = _clip(value, 4_000)
    if not source_url:
        return ""
    if any(ord(char) < 32 for char in source_url):
        raise InspirationError("原始链接包含无效字符")
    parsed = urlsplit(source_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise InspirationError("原始链接只支持 http 或 https 地址")
    return source_url


def _work_owned(conn, work_id, user_id):
    if work_id is None:
        return True
    return conn.execute(
        "SELECT 1 FROM works WHERE id=? AND user_id=?", (work_id, user_id)
    ).fetchone() is not None


def _normalize_scope(conn, user_id, payload, current_work_id=None):
    scope = payload.get("scope")
    work_id = payload.get("work_id")
    if scope == "global":
        return None
    if scope == "work" and work_id is None:
        work_id = current_work_id
    if work_id in ("", 0):
        work_id = None
    if work_id is not None:
        if isinstance(work_id, bool):
            raise InspirationError("作品范围无效")
        try:
            work_id = int(work_id)
        except (TypeError, ValueError):
            raise InspirationError("作品范围无效")
        if not _work_owned(conn, work_id, user_id):
            raise InspirationError("作品不存在")
    if scope == "work" and work_id is None:
        raise InspirationError("请选择要关联的作品")
    return work_id


_BASE_SELECT = """
SELECT i.*,
       w.title AS work_title,
       (SELECT COUNT(*) FROM inspiration_assets a WHERE a.inspiration_id=i.id) AS asset_count,
       (SELECT COUNT(*) FROM inspiration_usages u WHERE u.inspiration_id=i.id
        AND u.usage_status IN ('accepted','applied')) AS use_count,
       (SELECT MAX(u.created_at) FROM inspiration_usages u WHERE u.inspiration_id=i.id
        AND u.usage_status IN ('accepted','applied')) AS last_used_at
FROM creative_inspirations i
LEFT JOIN works w ON w.id=i.work_id
"""


def _row_payload(row):
    item = dict(row)
    for field, column in _LIST_COLUMNS.items():
        item[field] = _decode_list(item.pop(column, "[]"))
    item["scope"] = "work" if item.get("work_id") is not None else "global"
    item["favorite"] = bool(item.get("favorite"))
    item["title_locked"] = bool(item.get("title_locked"))
    item["asset_count"] = int(item.get("asset_count") or 0)
    item["use_count"] = int(item.get("use_count") or 0)
    item.pop("search_tags", None)
    return item


def create_inspiration(user_id, payload, *, current_work_id=None, queue=True):
    payload = payload if isinstance(payload, dict) else {}
    source_type = payload.get("source_type") if payload.get("source_type") in SOURCE_TYPES else "text"
    category = payload.get("primary_category") or payload.get("category") or "general"
    if category not in CATEGORIES:
        category = "general"
    raw_text = _clip(payload.get("raw_text"), _TEXT_LIMITS["raw_text"])
    title = _clip(payload.get("title"), _TEXT_LIMITS["title"])
    title_locked = bool(title and payload.get("title_locked", True))
    title = title or _default_title(raw_text, source_type)
    now = time.time()
    lists = {field: _json_list(payload.get(field)) for field in _LIST_FIELDS}
    with db.get_conn() as conn:
        work_id = _normalize_scope(conn, user_id, payload, current_work_id)
        reuse_mode = payload.get("reuse_mode")
        use_policy = payload.get("use_policy")
        status = payload.get("library_status")
        if reuse_mode not in REUSE_MODES:
            reuse_mode = "adaptable"
        if use_policy not in USE_POLICIES:
            use_policy = "generate_candidate"
        if status not in LIBRARY_STATUSES:
            status = "inbox"
        cur = conn.execute(
            "INSERT INTO creative_inspirations("
            "user_id,work_id,title,title_locked,raw_text,user_impression,source_type,primary_category,"
            "library_status,reuse_mode,use_policy,core_mechanism,creative_summary,suitable_context,"
            "adaptation_notes,production_notes,constraints_text,tags_json,mood_tags_json,usage_tags_json,"
            "search_keywords_json,search_tags,importance,favorite,analysis_status,analysis_error,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                user_id, work_id, title, int(title_locked), raw_text,
                _clip(payload.get("user_impression"), _TEXT_LIMITS["user_impression"]),
                source_type, category, status, reuse_mode, use_policy,
                _clip(payload.get("core_mechanism"), _TEXT_LIMITS["core_mechanism"]),
                _clip(payload.get("creative_summary"), _TEXT_LIMITS["creative_summary"]),
                _clip(payload.get("suitable_context"), _TEXT_LIMITS["suitable_context"]),
                _clip(payload.get("adaptation_notes"), _TEXT_LIMITS["adaptation_notes"]),
                _clip(payload.get("production_notes"), _TEXT_LIMITS["production_notes"]),
                _clip(payload.get("constraints_text"), _TEXT_LIMITS["constraints_text"]),
                json.dumps(lists["tags"], ensure_ascii=False),
                json.dumps(lists["mood_tags"], ensure_ascii=False),
                json.dumps(lists["usage_tags"], ensure_ascii=False),
                json.dumps(lists["search_keywords"], ensure_ascii=False),
                _search_tags(lists),
                _importance(payload.get("importance")),
                int(bool(payload.get("favorite"))),
                "pending", "", now, now,
            ),
        )
        inspiration_id = cur.lastrowid
        source_url = _normalize_source_url(payload.get("source_url"))
        if source_url:
            conn.execute(
                "INSERT INTO inspiration_assets("
                "inspiration_id,user_id,asset_type,original_name,mime_type,storage_path,source_url,"
                "file_size,content_hash,description,copyright_status,reference_only,processing_status,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    inspiration_id, user_id, "web_link", title, "text/uri-list", "", source_url,
                    0, "", raw_text, "unknown", 1, "uploaded", now, now,
                ),
            )
    if queue:
        queue_analysis(inspiration_id, user_id)
    return get_inspiration(inspiration_id, user_id)


def get_inspiration(inspiration_id, user_id, *, detail=True):
    with db.get_conn() as conn:
        row = conn.execute(
            _BASE_SELECT + " WHERE i.id=? AND i.user_id=?", (inspiration_id, user_id)
        ).fetchone()
        if not row:
            return None
        item = _row_payload(row)
        if not detail:
            return item
        item["assets"] = [dict(asset) for asset in conn.execute(
            "SELECT id,asset_type,original_name,mime_type,source_url,file_size,content_hash,"
            "duration_ms,width,height,transcript,description,copyright_status,reference_only,"
            "processing_status,processing_error,created_at "
            "FROM inspiration_assets WHERE inspiration_id=? AND user_id=? ORDER BY id",
            (inspiration_id, user_id),
        )]
        item["analyses"] = [dict(analysis) for analysis in conn.execute(
            "SELECT id,analysis_type,model,prompt_version,result_json,result_text,status,error,created_at "
            "FROM inspiration_analyses WHERE inspiration_id=? AND user_id=? ORDER BY id DESC LIMIT 12",
            (inspiration_id, user_id),
        )]
        for analysis in item["analyses"]:
            try:
                analysis["result"] = json.loads(analysis.pop("result_json") or "{}")
            except Exception:
                analysis["result"] = {}
        item["usages"] = [dict(usage) for usage in conn.execute(
            "SELECT u.id,u.work_id,u.chapter_id,u.usage_target_type,u.usage_target_id,u.usage_type,"
            "u.usage_status,u.adaptation_summary,u.generated_candidate,u.applied_excerpt,u.user_feedback,"
            "u.score,u.context_snapshot_json,u.created_at,u.updated_at,w.title AS work_title,"
            "c.title AS chapter_title,c.ord AS chapter_ord "
            "FROM inspiration_usages u LEFT JOIN works w ON w.id=u.work_id "
            "LEFT JOIN chapters c ON c.id=u.chapter_id "
            "WHERE u.inspiration_id=? AND u.user_id=? ORDER BY u.id DESC LIMIT 30",
            (inspiration_id, user_id),
        )]
        return item


def _fts_terms(query):
    runs = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]{2,}", query or "")
    terms = []
    for run in runs:
        candidates = [run]
        if len(run) > 6 and re.fullmatch(r"[\u4e00-\u9fff]+", run):
            candidates.extend(run[index:index + 3] for index in range(len(run) - 2))
        for term in candidates:
            if term not in terms:
                terms.append(term[:80])
    return terms[:12]


def _literal_terms(query):
    terms = []
    for run in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+", query or ""):
        candidates = (
            [run[index:index + 2] for index in range(max(1, len(run) - 1))]
            if re.fullmatch(r"[\u4e00-\u9fff]+", run) else [run]
        )
        for term in candidates:
            if term not in terms:
                terms.append(term[:80])
    return terms[:16]


def list_inspirations(user_id, *, work_id=None, scope="all", status="active",
                      source_type=None, query="", favorite=None, page=1, page_size=40):
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 40), 100))
    clauses = ["i.user_id=?"]
    params = [user_id]
    with db.get_conn() as conn:
        if work_id is not None:
            try:
                work_id = int(work_id)
            except (TypeError, ValueError):
                raise InspirationError("作品范围无效")
            if not _work_owned(conn, work_id, user_id):
                raise InspirationError("作品不存在")
            if scope == "global":
                clauses.append("i.work_id IS NULL")
            elif scope == "work":
                clauses.append("i.work_id=?")
                params.append(work_id)
            else:
                clauses.append("(i.work_id IS NULL OR i.work_id=?)")
                params.append(work_id)
        elif scope == "global":
            clauses.append("i.work_id IS NULL")
        elif scope == "work":
            clauses.append("i.work_id IS NOT NULL")
        if status == "active":
            clauses.append("i.library_status IN ('inbox','available')")
        elif status in LIBRARY_STATUSES:
            clauses.append("i.library_status=?")
            params.append(status)
        if source_type in SOURCE_TYPES:
            clauses.append("i.source_type=?")
            params.append(source_type)
        if favorite is not None:
            clauses.append("i.favorite=?")
            params.append(int(bool(favorite)))
        text_matches = []
        terms = _fts_terms(query)
        if terms:
            fts_query = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
            text_matches.append(
                "i.id IN (SELECT rowid FROM inspiration_fts WHERE inspiration_fts MATCH ?)"
            )
            params.append(fts_query)
        literal_terms = _literal_terms(query)
        if literal_terms:
            searchable = (
                "i.title || ' ' || i.raw_text || ' ' || i.user_impression || ' ' || "
                "i.core_mechanism || ' ' || i.creative_summary || ' ' || i.suitable_context || ' ' || i.search_tags"
            )
            text_matches.append("(" + " OR ".join(
                f"instr({searchable}, ?) > 0" for _ in literal_terms
            ) + ")")
            params.extend(literal_terms)
        if text_matches:
            clauses.append("(" + " OR ".join(text_matches) + ")")
        where = " WHERE " + " AND ".join(clauses)
        total = conn.execute(
            "SELECT COUNT(*) FROM creative_inspirations i" + where, params
        ).fetchone()[0]
        rows = conn.execute(
            _BASE_SELECT + where
            + " ORDER BY i.favorite DESC, i.importance DESC, i.updated_at DESC, i.id DESC LIMIT ? OFFSET ?",
            (*params, page_size, (page - 1) * page_size),
        ).fetchall()
        return {
            "items": [_row_payload(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }


def search_inspirations(user_id, query, *, work_id=None, include_global=True,
                        source_types=None, categories=None, include_used=True, limit=10):
    clauses = ["i.user_id=?", "i.library_status IN ('inbox','available')"]
    params = [user_id]
    with db.get_conn() as conn:
        if work_id is not None:
            try:
                work_id = int(work_id)
            except (TypeError, ValueError):
                raise InspirationError("作品范围无效")
            if not _work_owned(conn, work_id, user_id):
                raise InspirationError("作品不存在")
            if include_global:
                clauses.append("(i.work_id IS NULL OR i.work_id=?)")
            else:
                clauses.append("i.work_id=?")
            params.append(work_id)
        else:
            clauses.append("i.work_id IS NULL")
        source_types = [value for value in (source_types or []) if value in SOURCE_TYPES]
        if source_types:
            clauses.append("i.source_type IN (" + ",".join("?" for _ in source_types) + ")")
            params.extend(source_types)
        categories = [value for value in (categories or []) if value in CATEGORIES]
        if categories:
            clauses.append("i.primary_category IN (" + ",".join("?" for _ in categories) + ")")
            params.extend(categories)
        if not include_used:
            clauses.append(
                "(i.reuse_mode!='one_off' OR NOT EXISTS("
                "SELECT 1 FROM inspiration_usages ux WHERE ux.inspiration_id=i.id "
                "AND ux.usage_status IN ('accepted','applied')))"
            )
        terms = _fts_terms(query)
        literal_terms = _literal_terms(query)
        matches = []
        if terms:
            matches.append("i.id IN (SELECT rowid FROM inspiration_fts WHERE inspiration_fts MATCH ?)")
            params.append(" OR ".join('"' + term.replace('"', '""') + '"' for term in terms))
        if literal_terms:
            searchable = (
                "i.title || ' ' || i.raw_text || ' ' || i.user_impression || ' ' || "
                "i.core_mechanism || ' ' || i.creative_summary || ' ' || i.suitable_context || ' ' || i.search_tags"
            )
            matches.append("(" + " OR ".join(f"instr({searchable}, ?) > 0" for _ in literal_terms) + ")")
            params.extend(literal_terms)
        if matches:
            clauses.append("(" + " OR ".join(matches) + ")")
        rows = conn.execute(
            _BASE_SELECT + " WHERE " + " AND ".join(clauses)
            + " ORDER BY i.favorite DESC, i.importance DESC, i.updated_at DESC LIMIT ?",
            (*params, max(1, min(int(limit or 10), 30))),
        ).fetchall()
        return [_row_payload(row) for row in rows]


def update_inspiration(inspiration_id, user_id, payload):
    payload = payload if isinstance(payload, dict) else {}
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM creative_inspirations WHERE id=? AND user_id=?",
            (inspiration_id, user_id),
        ).fetchone()
        if not row:
            return None
        work_id = row["work_id"]
        if "scope" in payload or "work_id" in payload:
            work_id = _normalize_scope(conn, user_id, payload, work_id)
        updates = {"work_id": work_id, "updated_at": time.time()}
        for field, limit in _TEXT_LIMITS.items():
            if field in payload:
                updates[field] = _clip(payload.get(field), limit)
        if "title" in payload:
            if updates.get("title"):
                updates["title_locked"] = int(bool(payload.get("title_locked", True)))
            else:
                updates["title"] = _default_title(
                    updates.get("raw_text", row["raw_text"]),
                    payload.get("source_type") or row["source_type"],
                )
                updates["title_locked"] = 0
        if payload.get("source_type") in SOURCE_TYPES:
            updates["source_type"] = payload["source_type"]
        category = payload.get("primary_category") or payload.get("category")
        if category in CATEGORIES:
            updates["primary_category"] = category
        if payload.get("library_status") in LIBRARY_STATUSES:
            updates["library_status"] = payload["library_status"]
        if payload.get("reuse_mode") in REUSE_MODES:
            updates["reuse_mode"] = payload["reuse_mode"]
        if payload.get("use_policy") in USE_POLICIES:
            updates["use_policy"] = payload["use_policy"]
        if "favorite" in payload:
            updates["favorite"] = int(bool(payload["favorite"]))
        if "importance" in payload:
            updates["importance"] = _importance(payload["importance"])
        lists = {}
        for field, column in _LIST_COLUMNS.items():
            if field in payload:
                lists[field] = _json_list(payload[field])
                updates[column] = json.dumps(lists[field], ensure_ascii=False)
            else:
                lists[field] = _decode_list(row[column])
        updates["search_tags"] = _search_tags(lists)
        assignments = ", ".join(f"{field}=?" for field in updates)
        conn.execute(
            f"UPDATE creative_inspirations SET {assignments} WHERE id=? AND user_id=?",
            (*updates.values(), inspiration_id, user_id),
        )
        if "source_url" in payload:
            source_url = _normalize_source_url(payload.get("source_url"))
            link = conn.execute(
                "SELECT id FROM inspiration_assets "
                "WHERE inspiration_id=? AND user_id=? AND asset_type='web_link' ORDER BY id LIMIT 1",
                (inspiration_id, user_id),
            ).fetchone()
            if source_url and link:
                conn.execute(
                    "UPDATE inspiration_assets SET original_name=?,source_url=?,description=?,updated_at=? "
                    "WHERE id=? AND user_id=?",
                    (
                        updates.get("title", row["title"]),
                        source_url,
                        updates.get("raw_text", row["raw_text"]),
                        updates["updated_at"],
                        link["id"],
                        user_id,
                    ),
                )
                conn.execute(
                    "DELETE FROM inspiration_assets "
                    "WHERE inspiration_id=? AND user_id=? AND asset_type='web_link' AND id!=?",
                    (inspiration_id, user_id, link["id"]),
                )
            elif source_url:
                conn.execute(
                    "INSERT INTO inspiration_assets("
                    "inspiration_id,user_id,asset_type,original_name,mime_type,storage_path,source_url,"
                    "file_size,content_hash,description,copyright_status,reference_only,processing_status,"
                    "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        inspiration_id, user_id, "web_link",
                        updates.get("title", row["title"]), "text/uri-list", "", source_url,
                        0, "", updates.get("raw_text", row["raw_text"]), "unknown", 1,
                        "uploaded", updates["updated_at"], updates["updated_at"],
                    ),
                )
            else:
                conn.execute(
                    "DELETE FROM inspiration_assets "
                    "WHERE inspiration_id=? AND user_id=? AND asset_type='web_link'",
                    (inspiration_id, user_id),
                )
    return get_inspiration(inspiration_id, user_id)


def delete_inspiration(inspiration_id, user_id):
    with db.get_conn() as conn:
        if not conn.execute(
            "SELECT 1 FROM creative_inspirations WHERE id=? AND user_id=?",
            (inspiration_id, user_id),
        ).fetchone():
            return False
        paths = [row["storage_path"] for row in conn.execute(
            "SELECT storage_path FROM inspiration_assets WHERE inspiration_id=? AND user_id=? AND storage_path!=''",
            (inspiration_id, user_id),
        )]
        conn.execute("DELETE FROM inspiration_jobs WHERE inspiration_id=? AND user_id=?", (inspiration_id, user_id))
        conn.execute("DELETE FROM inspiration_usages WHERE inspiration_id=? AND user_id=?", (inspiration_id, user_id))
        conn.execute("DELETE FROM inspiration_analyses WHERE inspiration_id=? AND user_id=?", (inspiration_id, user_id))
        conn.execute("DELETE FROM inspiration_assets WHERE inspiration_id=? AND user_id=?", (inspiration_id, user_id))
        conn.execute("DELETE FROM creative_inspirations WHERE id=? AND user_id=?", (inspiration_id, user_id))
        remaining = {
            row["storage_path"] for row in conn.execute(
                "SELECT storage_path FROM inspiration_assets WHERE storage_path IN ("
                + ",".join("?" for _ in paths) + ")",
                paths,
            )
        } if paths else set()
    for relative in paths:
        if relative not in remaining:
            _remove_storage_path(relative)
    return True


def add_usage(inspiration_id, user_id, payload, *, current_work_id=None, current_chapter_id=None):
    payload = payload if isinstance(payload, dict) else {}
    now = time.time()
    usage_type = payload.get("usage_type") or "referenced"
    usage_status = payload.get("usage_status") or "applied"
    if usage_type not in USAGE_TYPES:
        raise InspirationError("灵感使用方式无效")
    if usage_status not in USAGE_STATUSES:
        raise InspirationError("灵感使用状态无效")
    score = payload.get("score")
    if score is not None:
        if isinstance(score, bool) or not isinstance(score, int):
            raise InspirationError("灵感评分必须是整数")
        score = max(1, min(score, 5))
    with db.get_conn() as conn:
        item = conn.execute(
            "SELECT id,reuse_mode FROM creative_inspirations WHERE id=? AND user_id=?",
            (inspiration_id, user_id),
        ).fetchone()
        if not item:
            return None
        work_id = payload.get("work_id", current_work_id)
        chapter_id = payload.get("chapter_id", current_chapter_id)
        if work_id is not None:
            try:
                work_id = int(work_id)
            except (TypeError, ValueError):
                raise InspirationError("作品范围无效")
            if not _work_owned(conn, work_id, user_id):
                raise InspirationError("作品不存在")
        if chapter_id is not None:
            try:
                chapter_id = int(chapter_id)
            except (TypeError, ValueError):
                raise InspirationError("章节无效")
            chapter = conn.execute(
                "SELECT c.work_id FROM chapters c JOIN works w ON w.id=c.work_id "
                "WHERE c.id=? AND w.user_id=? AND c.deleted_at IS NULL",
                (chapter_id, user_id),
            ).fetchone()
            if not chapter:
                raise InspirationError("章节不存在")
            if work_id is None:
                work_id = chapter["work_id"]
            elif work_id != chapter["work_id"]:
                raise InspirationError("章节不属于所选作品")
        context_snapshot = payload.get("context_snapshot")
        if not isinstance(context_snapshot, dict):
            context_snapshot = {}
        cur = conn.execute(
            "INSERT INTO inspiration_usages("
            "inspiration_id,user_id,work_id,chapter_id,usage_target_type,usage_target_id,"
            "usage_type,usage_status,adaptation_summary,generated_candidate,applied_excerpt,"
            "user_feedback,score,context_snapshot_json,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                inspiration_id, user_id, work_id, chapter_id,
                _clip(payload.get("usage_target_type") or "chapter", 40),
                _clip(payload.get("usage_target_id"), 120),
                usage_type,
                usage_status,
                _clip(payload.get("adaptation_summary"), 8_000),
                _clip(payload.get("generated_candidate"), 20_000),
                _clip(payload.get("applied_excerpt"), 20_000),
                _clip(payload.get("user_feedback"), 4_000),
                score,
                json.dumps(context_snapshot, ensure_ascii=False), now, now,
            ),
        )
        if item["reuse_mode"] == "one_off":
            conn.execute(
                "UPDATE creative_inspirations SET updated_at=? WHERE id=?",
                (now, inspiration_id),
            )
        usage_id = cur.lastrowid
    return {"ok": True, "usage_id": usage_id}


def _asset_kind(filename, mime_type, requested=None):
    suffix = Path(filename or "").suffix.lower()
    mime = (mime_type or "").split(";", 1)[0].strip().lower()
    generic_mime = mime in {"", "application/octet-stream"}
    if not suffix and mime in _MIME_EXTENSIONS:
        suffix = _MIME_EXTENSIONS[mime]
    major = mime.split("/", 1)[0] if "/" in mime else ""
    if generic_mime:
        if suffix in _ALLOWED_EXTENSIONS["image"]:
            kind = "image"
        elif suffix == ".webm":
            kind = "video"
        elif suffix in _ALLOWED_EXTENSIONS["video"] and suffix != ".webm":
            kind = "video"
        elif suffix in _ALLOWED_EXTENSIONS["audio"]:
            kind = "audio"
        else:
            kind = ""
    else:
        kind = "image" if major == "image" else "audio" if major == "audio" else "video" if major == "video" else ""
    if requested in {"music", "voice_note"} and kind == "audio":
        asset_type = requested
    else:
        asset_type = kind
    allowed_kind = "audio" if asset_type in {"audio", "music", "voice_note"} else asset_type
    if not allowed_kind or suffix not in _ALLOWED_EXTENSIONS.get(allowed_kind, set()):
        raise InspirationError("仅支持常见图片、音频、音乐和视频文件")
    if not generic_mime and major != allowed_kind:
        raise InspirationError("文件扩展名与内容类型不一致")
    if generic_mime:
        mime = next((
            key for key, value in _MIME_EXTENSIONS.items()
            if value == suffix and key.startswith(allowed_kind + "/")
        ), "")
    return asset_type, allowed_kind, suffix, mime or "application/octet-stream"


def _asset_max(kind):
    if kind == "image":
        return config.INSPIRATION_IMAGE_MAX_BYTES
    if kind == "audio":
        return config.INSPIRATION_AUDIO_MAX_BYTES
    return config.INSPIRATION_VIDEO_MAX_BYTES


def _user_storage_bytes(user_id):
    with db.get_conn() as conn:
        return int(conn.execute(
            "SELECT COALESCE(SUM(stored_size),0) FROM ("
            "SELECT MAX(file_size) AS stored_size FROM inspiration_assets "
            "WHERE user_id=? AND storage_path!='' GROUP BY storage_path)",
            (user_id,),
        ).fetchone()[0] or 0)


def _signature_valid(kind, suffix, head):
    if kind == "image":
        checks = {
            ".jpg": head.startswith(b"\xff\xd8\xff"),
            ".jpeg": head.startswith(b"\xff\xd8\xff"),
            ".png": head.startswith(b"\x89PNG\r\n\x1a\n"),
            ".gif": head.startswith((b"GIF87a", b"GIF89a")),
            ".webp": head.startswith(b"RIFF") and head[8:12] == b"WEBP",
            ".heic": b"ftyp" in head[:24],
        }
        return checks.get(suffix, False)
    if suffix == ".wav":
        return head.startswith(b"RIFF") and head[8:12] == b"WAVE"
    if suffix == ".flac":
        return head.startswith(b"fLaC")
    if suffix == ".ogg":
        return head.startswith(b"OggS")
    if suffix == ".mp3":
        return head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)
    if suffix in {".m4a", ".mp4", ".mov"}:
        return b"ftyp" in head[:24]
    if suffix in {".webm", ".mkv"}:
        return head.startswith(b"\x1a\x45\xdf\xa3")
    if suffix == ".aac":
        return len(head) > 1 and head[0] == 0xFF and head[1] & 0xF0 == 0xF0
    return False


def _storage_root():
    root = Path(config.INSPIRATION_STORAGE_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def cleanup_stale_temp_files(now=None):
    temp_dir = _storage_root() / "temp"
    if not temp_dir.is_dir():
        return 0
    cutoff = (now or time.time()) - max(1, config.INSPIRATION_TEMP_RETENTION_HOURS) * 3600
    removed = 0
    for path in temp_dir.iterdir():
        try:
            if path.is_file() and path.name.endswith(".upload") and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _resolve_storage_path(relative):
    root = _storage_root()
    target = (root / (relative or "")).resolve()
    if target == root or root not in target.parents:
        raise InspirationError("素材路径无效")
    return target


def _remove_storage_path(relative):
    try:
        target = _resolve_storage_path(relative)
        if target.is_file():
            target.unlink()
    except (OSError, InspirationError):
        pass


def _prepare_upload(user_id, inspiration_id, filename, mime_type, requested_type):
    filename = Path(filename or "素材").name[:240]
    asset_type, kind, suffix, mime = _asset_kind(filename, mime_type, requested_type)
    with db.get_conn() as conn:
        item = conn.execute(
            "SELECT 1 FROM creative_inspirations WHERE id=? AND user_id=?",
            (inspiration_id, user_id),
        ).fetchone()
        if not item:
            raise InspirationError("灵感不存在")
    root = _storage_root()
    temp_dir = root / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"{uuid.uuid4().hex}.upload"
    return filename, asset_type, kind, suffix, mime, temp_path


def _finish_upload(user_id, inspiration_id, filename, asset_type, kind, suffix, mime,
                   temp_path, size, digest, head, description=""):
    if not size:
        temp_path.unlink(missing_ok=True)
        raise InspirationError("素材文件为空")
    if not _signature_valid(kind, suffix, head):
        temp_path.unlink(missing_ok=True)
        raise InspirationError("文件内容与声明的素材格式不一致")
    root = _storage_root()
    with db.get_conn() as conn:
        existing = conn.execute(
            "SELECT storage_path FROM inspiration_assets WHERE user_id=? AND content_hash=? "
            "AND storage_path!='' LIMIT 1",
            (user_id, digest),
        ).fetchone()
    user_limit = config.INSPIRATION_USER_STORAGE_LIMIT_BYTES
    if not existing and user_limit > 0 and _user_storage_bytes(user_id) + size > user_limit:
        temp_path.unlink(missing_ok=True)
        raise InspirationError("灵感素材空间已满，请删除不用的原始素材或联系管理员调整配额")
    if existing:
        relative = existing["storage_path"]
        temp_path.unlink(missing_ok=True)
    else:
        stamp = datetime.now()
        directory = root / "users" / str(user_id) / "originals" / str(stamp.year) / f"{stamp.month:02d}"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{digest[:18]}-{uuid.uuid4().hex[:8]}{suffix}"
        os.replace(temp_path, target)
        relative = target.relative_to(root).as_posix()
    now = time.time()
    with db.get_conn() as conn:
        if not conn.execute(
            "SELECT 1 FROM creative_inspirations WHERE id=? AND user_id=?",
            (inspiration_id, user_id),
        ).fetchone():
            if not existing:
                _remove_storage_path(relative)
            raise InspirationError("灵感不存在")
        cur = conn.execute(
            "INSERT INTO inspiration_assets("
            "inspiration_id,user_id,asset_type,original_name,mime_type,storage_path,source_url,"
            "file_size,content_hash,description,copyright_status,reference_only,processing_status,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                inspiration_id, user_id, asset_type, filename, mime, relative, "", size, digest,
                _clip(description, 8_000), "unknown", 1, "uploaded", now, now,
            ),
        )
        source_type = "music" if asset_type == "music" else asset_type
        conn.execute(
            "UPDATE creative_inspirations SET source_type=CASE WHEN source_type='text' AND raw_text='' "
            "THEN ? ELSE source_type END, updated_at=? WHERE id=?",
            (source_type, now, inspiration_id),
        )
        return {"id": cur.lastrowid, "asset_type": asset_type, "original_name": filename,
                "mime_type": mime, "file_size": size}


async def add_asset_stream(user_id, inspiration_id, filename, mime_type, requested_type, stream,
                           description=""):
    prepared = _prepare_upload(user_id, inspiration_id, filename, mime_type, requested_type)
    filename, asset_type, kind, suffix, mime, temp_path = prepared
    max_bytes = _asset_max(kind)
    digest = hashlib.sha256()
    size = 0
    head = b""
    try:
        with temp_path.open("wb") as target:
            async for chunk in stream:
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise InspirationError(f"文件过大，当前类型上限为 {max_bytes // (1024 * 1024)}MB")
                if len(head) < 64:
                    head += chunk[:64 - len(head)]
                digest.update(chunk)
                target.write(chunk)
        return _finish_upload(
            user_id, inspiration_id, filename, asset_type, kind, suffix, mime,
            temp_path, size, digest.hexdigest(), head, description,
        )
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def add_asset_bytes(user_id, inspiration_id, data, filename, mime_type, requested_type,
                    description=""):
    prepared = _prepare_upload(user_id, inspiration_id, filename, mime_type, requested_type)
    filename, asset_type, kind, suffix, mime, temp_path = prepared
    if len(data or b"") > _asset_max(kind):
        raise InspirationError("素材文件过大")
    try:
        temp_path.write_bytes(data or b"")
        return _finish_upload(
            user_id, inspiration_id, filename, asset_type, kind, suffix, mime,
            temp_path, len(data or b""), hashlib.sha256(data or b"").hexdigest(),
            (data or b"")[:64], description,
        )
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def get_asset(asset_id, user_id):
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT a.* FROM inspiration_assets a JOIN creative_inspirations i ON i.id=a.inspiration_id "
            "WHERE a.id=? AND a.user_id=? AND i.user_id=?",
            (asset_id, user_id, user_id),
        ).fetchone()
        return dict(row) if row else None


def asset_file(asset_id, user_id):
    asset = get_asset(asset_id, user_id)
    if not asset or not asset.get("storage_path"):
        return None
    path = _resolve_storage_path(asset["storage_path"])
    if not path.is_file():
        return None
    return asset, path


def delete_user_files(user_id):
    with db.get_conn() as conn:
        paths = [row["storage_path"] for row in conn.execute(
            "SELECT storage_path FROM inspiration_assets WHERE user_id=? AND storage_path!=''",
            (user_id,),
        )]
    for relative in paths:
        _remove_storage_path(relative)
    user_dir = (_storage_root() / "users" / str(int(user_id))).resolve()
    root = _storage_root()
    if root in user_dir.parents and user_dir.is_dir():
        shutil.rmtree(user_dir, ignore_errors=True)


def queue_analysis(inspiration_id, user_id):
    now = time.time()
    with db.get_conn() as conn:
        if not conn.execute(
            "SELECT 1 FROM creative_inspirations WHERE id=? AND user_id=?",
            (inspiration_id, user_id),
        ).fetchone():
            return None
        existing = conn.execute(
            "SELECT id FROM inspiration_jobs WHERE inspiration_id=? AND user_id=? "
            "AND status IN ('pending','processing') ORDER BY id DESC LIMIT 1",
            (inspiration_id, user_id),
        ).fetchone()
        conn.execute(
            "UPDATE creative_inspirations SET analysis_status='pending',analysis_error='',updated_at=? "
            "WHERE id=? AND user_id=?",
            (now, inspiration_id, user_id),
        )
        if existing:
            return existing["id"]
        cur = conn.execute(
            "INSERT INTO inspiration_jobs("
            "inspiration_id,user_id,job_type,status,attempts,progress,error,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?,?)",
            (inspiration_id, user_id, "analyze", "pending", 0, 0, "", now, now),
        )
        return cur.lastrowid


def _claim_job():
    now = time.time()
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE inspiration_jobs SET status='pending',error='服务重启后自动续作',updated_at=? "
            "WHERE status='processing' AND updated_at<?",
            (now, now - 900),
        )
        row = conn.execute(
            "SELECT * FROM inspiration_jobs WHERE status='pending' ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            return None
        cur = conn.execute(
            "UPDATE inspiration_jobs SET status='processing',attempts=attempts+1,progress=10,"
            "started_at=COALESCE(started_at,?),updated_at=? WHERE id=? AND status='pending'",
            (now, now, row["id"]),
        )
        if cur.rowcount != 1:
            return None
        return dict(row)


def _parse_model_json(raw):
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text).strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise InspirationError("模型没有返回可解析的灵感卡")


def _analysis_messages(item, image=None):
    system = (
        "你是中文创作素材整理员。把作者保存的原始灵感整理成可检索的候选创意卡。"
        "灵感不是已经发生的剧情，不得改写或覆盖作者原话。只输出 JSON 对象，字段为："
        "title, primary_category, core_mechanism, creative_summary, suitable_context, "
        "adaptation_notes, production_notes, tags, mood_tags, usage_tags, search_keywords。"
        "数组字段每项简短，最多 8 项。不要声称外部音乐或视频可直接商用。"
        "没有实际图像输入时，不得声称已经看过、听过或播放过素材；"
        "音频和视频第一版只能依据作者描述、文件名和链接做保守整理。"
    )
    description = {
        "raw_text": item.get("raw_text") or "",
        "user_impression": item.get("user_impression") or "",
        "source_type": item.get("source_type") or "text",
        "current_title": item.get("title") or "",
        "assets": [
            {"type": asset.get("asset_type"), "name": asset.get("original_name"),
             "description": asset.get("description"), "source_url": asset.get("source_url")}
            for asset in item.get("assets") or []
        ],
    }
    content = [{"type": "text", "text": json.dumps(description, ensure_ascii=False)}]
    if image:
        asset, path = image
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:{asset['mime_type']};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
            },
        })
    return [{"role": "system", "content": system}, {"role": "user", "content": content}]


def _complete_job(job, *, error=None):
    now = time.time()
    with db.get_conn() as conn:
        if error:
            conn.execute(
                "UPDATE inspiration_jobs SET status='failed',progress=100,error=?,finished_at=?,updated_at=? "
                "WHERE id=?",
                (_clip(error, 2_000), now, now, job["id"]),
            )
            conn.execute(
                "UPDATE creative_inspirations SET analysis_status='failed',analysis_error=?,updated_at=? "
                "WHERE id=? AND user_id=?",
                (_clip(error, 2_000), now, job["inspiration_id"], job["user_id"]),
            )
        else:
            conn.execute(
                "UPDATE inspiration_jobs SET status='completed',progress=100,error='',finished_at=?,updated_at=? "
                "WHERE id=?",
                (now, now, job["id"]),
            )


def run_next_analysis_job():
    job = _claim_job()
    if not job:
        return False
    try:
        item = get_inspiration(job["inspiration_id"], job["user_id"])
        if not item:
            raise InspirationError("灵感已删除")
        settings = db.get_settings(job["user_id"]) or {}
        api_key = settings.get("llm_api_key") or config.LLM_API_KEY
        base_url = settings.get("llm_base_url") or config.LLM_BASE_URL
        model = settings.get("llm_model") or config.LLM_MODEL
        if not api_key:
            raise InspirationError("尚未配置 AI 模型，原始灵感已经保存，可配置后重新整理")
        image = None
        for asset in item.get("assets") or []:
            if (asset.get("mime_type") or "").startswith("image/") and asset.get("file_size", 0) <= 10 * 1024 * 1024:
                file_info = asset_file(asset["id"], job["user_id"])
                if file_info:
                    image = file_info
                    break
        try:
            raw = llm.chat(_analysis_messages(item, image), base_url=base_url, api_key=api_key, model=model)
        except Exception:
            if not image or not (item.get("raw_text") or item.get("user_impression")):
                raise
            raw = llm.chat(_analysis_messages(item, None), base_url=base_url, api_key=api_key, model=model)
        result = _parse_model_json(raw)
        category = result.get("primary_category")
        if category not in CATEGORIES:
            category = item.get("primary_category") or "general"
        lists = {
            "tags": _json_list(result.get("tags")),
            "mood_tags": _json_list(result.get("mood_tags")),
            "usage_tags": _json_list(result.get("usage_tags")),
            "search_keywords": _json_list(result.get("search_keywords")),
        }
        now = time.time()
        with db.get_conn() as conn:
            if not conn.execute(
                "SELECT 1 FROM creative_inspirations WHERE id=? AND user_id=?",
                (item["id"], job["user_id"]),
            ).fetchone():
                raise InspirationError("灵感已删除")
            cur = conn.execute(
                "INSERT INTO inspiration_analyses("
                "inspiration_id,user_id,analysis_type,model,prompt_version,result_json,result_text,status,error,created_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    item["id"], job["user_id"], item.get("source_type") or "general", model,
                    "inspiration-v1", json.dumps(result, ensure_ascii=False), raw, "completed", "", now,
                ),
            )
            title = item["title"] if item.get("title_locked") else (
                _clip(result.get("title"), _TEXT_LIMITS["title"]) or item["title"]
            )
            conn.execute(
                "UPDATE creative_inspirations SET title=?,primary_category=?,core_mechanism=?,"
                "creative_summary=?,suitable_context=?,adaptation_notes=?,production_notes=?,"
                "tags_json=?,mood_tags_json=?,usage_tags_json=?,search_keywords_json=?,search_tags=?,"
                "library_status=CASE WHEN library_status='inbox' THEN 'available' ELSE library_status END,"
                "analysis_status='completed',analysis_error='',current_analysis_id=?,updated_at=? "
                "WHERE id=? AND user_id=?",
                (
                    title, category,
                    _clip(result.get("core_mechanism"), _TEXT_LIMITS["core_mechanism"]),
                    _clip(result.get("creative_summary"), _TEXT_LIMITS["creative_summary"]),
                    _clip(result.get("suitable_context"), _TEXT_LIMITS["suitable_context"]),
                    _clip(result.get("adaptation_notes"), _TEXT_LIMITS["adaptation_notes"]),
                    _clip(result.get("production_notes"), _TEXT_LIMITS["production_notes"]),
                    json.dumps(lists["tags"], ensure_ascii=False),
                    json.dumps(lists["mood_tags"], ensure_ascii=False),
                    json.dumps(lists["usage_tags"], ensure_ascii=False),
                    json.dumps(lists["search_keywords"], ensure_ascii=False),
                    _search_tags(lists), cur.lastrowid, now, item["id"], job["user_id"],
                ),
            )
        _complete_job(job)
    except Exception as exc:
        _complete_job(job, error=str(exc))
    return True


def _worker_loop():
    while not _worker_stop.is_set():
        try:
            worked = run_next_analysis_job()
        except Exception as exc:
            print(f"[writehtml] 灵感后台任务暂时失败：{exc}")
            worked = False
        if not worked:
            _worker_stop.wait(max(0.25, config.INSPIRATION_WORKER_POLL_SECONDS))


def start_worker():
    global _worker_thread
    cleanup_stale_temp_files()
    if not config.INSPIRATION_WORKER_ENABLED:
        return
    if _worker_thread and _worker_thread.is_alive():
        return
    _worker_stop.clear()
    _worker_thread = threading.Thread(
        target=_worker_loop, name="writehtml-inspiration-worker", daemon=True,
    )
    _worker_thread.start()


def stop_worker():
    _worker_stop.set()


atexit.register(stop_worker)
