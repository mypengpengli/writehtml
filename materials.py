"""Unified, inspectable writing materials for a work.

Facts, references and transient attachments deliberately keep different semantics:
story memories are canon; reference projects/documents and inspirations are optional
creative aids; style profiles describe abstract craft rather than reusable prose.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

import db


STYLE_STRENGTHS = {"light", "balanced", "strong"}
DEFAULT_SETTINGS = {
    "use_story_memory": True,
    "use_reference_projects": True,
    "use_style_profile": True,
    "use_inspirations": True,
    "use_reference_documents": True,
    "style_strength": "balanced",
}


class MaterialError(ValueError):
    pass


def _owned(conn, work_id, user_id):
    return conn.execute(
        "SELECT 1 FROM works WHERE id=? AND user_id=?", (work_id, user_id)
    ).fetchone() is not None


def _decode(raw, fallback):
    try:
        value = json.loads(raw or "")
    except Exception:
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def get_settings(user_id, work_id):
    with db.get_conn() as conn:
        if not _owned(conn, work_id, user_id):
            return None
        row = conn.execute(
            "SELECT * FROM work_material_settings WHERE work_id=? AND user_id=?",
            (work_id, user_id),
        ).fetchone()
    result = dict(DEFAULT_SETTINGS)
    if row:
        for key in DEFAULT_SETTINGS:
            if key == "style_strength":
                result[key] = row[key] if row[key] in STYLE_STRENGTHS else "balanced"
            else:
                result[key] = bool(row[key])
    return result


def save_settings(user_id, work_id, payload):
    current = get_settings(user_id, work_id)
    if current is None:
        return None
    payload = payload if isinstance(payload, dict) else {}
    for key in DEFAULT_SETTINGS:
        if key not in payload:
            continue
        if key == "style_strength":
            if payload[key] not in STYLE_STRENGTHS:
                raise MaterialError("语言指纹强度无效")
            current[key] = payload[key]
        else:
            current[key] = bool(payload[key])
    now = time.time()
    with db.get_conn() as conn:
        if not _owned(conn, work_id, user_id):
            return None
        conn.execute(
            "INSERT INTO work_material_settings(work_id,user_id,use_story_memory,use_reference_projects,"
            "use_style_profile,use_inspirations,use_reference_documents,style_strength,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(work_id) DO UPDATE SET "
            "use_story_memory=excluded.use_story_memory,use_reference_projects=excluded.use_reference_projects,"
            "use_style_profile=excluded.use_style_profile,use_inspirations=excluded.use_inspirations,"
            "use_reference_documents=excluded.use_reference_documents,style_strength=excluded.style_strength,"
            "updated_at=excluded.updated_at",
            (work_id, user_id, int(current["use_story_memory"]), int(current["use_reference_projects"]),
             int(current["use_style_profile"]), int(current["use_inspirations"]),
             int(current["use_reference_documents"]), current["style_strength"], now),
        )
    return current


def list_mounts(user_id, work_id):
    with db.get_conn() as conn:
        if not _owned(conn, work_id, user_id):
            return None
        rows = conn.execute(
            "SELECT m.*,w.title AS reference_title FROM work_reference_mounts m "
            "JOIN works w ON w.id=m.reference_work_id "
            "WHERE m.work_id=? AND m.user_id=? ORDER BY m.updated_at DESC,m.id DESC",
            (work_id, user_id),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        for key in ("enabled", "use_style", "use_plot", "use_world"):
            item[key] = bool(item[key])
        result.append(item)
    return result


def save_mount(user_id, work_id, payload):
    payload = payload if isinstance(payload, dict) else {}
    try:
        reference_work_id = int(payload.get("reference_work_id") or 0)
    except (TypeError, ValueError):
        raise MaterialError("参考工程无效")
    if not reference_work_id or reference_work_id == work_id:
        raise MaterialError("当前作品不能挂载自己")
    now = time.time()
    with db.get_conn() as conn:
        if not _owned(conn, work_id, user_id) or not _owned(conn, reference_work_id, user_id):
            return None
        count = conn.execute(
            "SELECT COUNT(*) FROM work_reference_mounts WHERE work_id=? AND reference_work_id<>?",
            (work_id, reference_work_id),
        ).fetchone()[0]
        if count >= 5:
            raise MaterialError("每个作品最多挂载 5 个参考工程")
        conn.execute(
            "INSERT INTO work_reference_mounts(user_id,work_id,reference_work_id,enabled,use_style,use_plot,"
            "use_world,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(work_id,reference_work_id) DO UPDATE SET enabled=excluded.enabled,"
            "use_style=excluded.use_style,use_plot=excluded.use_plot,use_world=excluded.use_world,"
            "updated_at=excluded.updated_at",
            (user_id, work_id, reference_work_id, int(payload.get("enabled", True)),
             int(payload.get("use_style", True)), int(payload.get("use_plot", True)),
             int(payload.get("use_world", False)), now, now),
        )
    return next((item for item in list_mounts(user_id, work_id) if item["reference_work_id"] == reference_work_id), None)


def delete_mount(user_id, work_id, mount_id):
    with db.get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM work_reference_mounts WHERE id=? AND work_id=? AND user_id=?",
            (mount_id, work_id, user_id),
        )
        return cur.rowcount > 0


def _profile_payload(row):
    if not row:
        return None
    item = dict(row)
    item["profile"] = _decode(item.pop("profile_json", "{}"), {})
    return item


def get_style_profile(user_id, work_id):
    with db.get_conn() as conn:
        if not _owned(conn, work_id, user_id):
            return None
        row = conn.execute(
            "SELECT * FROM work_style_profiles WHERE work_id=? AND user_id=?", (work_id, user_id)
        ).fetchone()
        return _profile_payload(row) or {"work_id": work_id, "profile": {}}


def save_style_profile(user_id, work_id, profile, *, source_kind="manual", source_label="", source_job_id=None):
    if not isinstance(profile, dict):
        raise MaterialError("语言指纹必须是结构化内容")
    now = time.time()
    cleaned = normalize_profile(profile)
    with db.get_conn() as conn:
        if not _owned(conn, work_id, user_id):
            return None
        conn.execute(
            "INSERT INTO work_style_profiles(work_id,user_id,source_kind,source_label,source_job_id,profile_json,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(work_id) DO UPDATE SET "
            "source_kind=excluded.source_kind,source_label=excluded.source_label,source_job_id=excluded.source_job_id,"
            "profile_json=excluded.profile_json,updated_at=excluded.updated_at",
            (work_id, user_id, str(source_kind)[:40], str(source_label)[:240], source_job_id,
             json.dumps(cleaned, ensure_ascii=False), now, now),
        )
        row = conn.execute("SELECT * FROM work_style_profiles WHERE work_id=?", (work_id,)).fetchone()
        return _profile_payload(row)


def normalize_profile(profile):
    scalar_keys = (
        "narrative_voice", "point_of_view", "pacing", "sentence_rhythm", "diction",
        "description_preferences", "dialogue_pattern", "emotional_tone", "avoid",
    )
    result = {key: str(profile.get(key) or "").strip()[:4000] for key in scalar_keys}
    for key in ("character_voices", "description_craft", "plot_devices"):
        values = profile.get(key) if isinstance(profile.get(key), list) else []
        result[key] = []
        for value in values[:40]:
            if not isinstance(value, dict):
                continue
            result[key].append({
                str(field)[:80]: str(content or "").strip()[:2000]
                for field, content in value.items() if str(content or "").strip()
            })
    return result


def list_documents(user_id, work_id, include_content=False):
    with db.get_conn() as conn:
        if not _owned(conn, work_id, user_id):
            return None
        fields = "*" if include_content else "id,user_id,work_id,name,tags,enabled,pinned,length(content) AS chars,created_at,updated_at"
        rows = conn.execute(
            f"SELECT {fields} FROM work_reference_documents WHERE work_id=? AND user_id=? "
            "ORDER BY pinned DESC,updated_at DESC,id DESC", (work_id, user_id)
        ).fetchall()
    return [{**dict(row), "enabled": bool(row["enabled"]), "pinned": bool(row["pinned"])} for row in rows]


def get_document(user_id, work_id, document_id):
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM work_reference_documents WHERE id=? AND work_id=? AND user_id=?",
            (document_id, work_id, user_id),
        ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["enabled"], item["pinned"] = bool(item["enabled"]), bool(item["pinned"])
    return item


def save_document(user_id, work_id, payload):
    payload = payload if isinstance(payload, dict) else {}
    name = " ".join(str(payload.get("name") or "长期参考资料").replace("\\", "/").rsplit("/", 1)[-1].split())[:240]
    content = str(payload.get("content") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not content:
        raise MaterialError("参考资料内容为空")
    if len(content) > 120_000:
        raise MaterialError("单份长期参考资料最多 12 万字，请先拆分")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    now = time.time()
    with db.get_conn() as conn:
        if not _owned(conn, work_id, user_id):
            return None
        conn.execute(
            "INSERT INTO work_reference_documents(user_id,work_id,name,content,content_hash,tags,enabled,pinned,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(work_id,content_hash) DO UPDATE SET "
            "name=excluded.name,tags=excluded.tags,enabled=excluded.enabled,pinned=excluded.pinned,updated_at=excluded.updated_at",
            (user_id, work_id, name, content, digest, str(payload.get("tags") or "")[:1000],
             int(payload.get("enabled", True)), int(payload.get("pinned", False)), now, now),
        )
        row = conn.execute(
            "SELECT id,user_id,work_id,name,tags,enabled,pinned,length(content) AS chars,created_at,updated_at "
            "FROM work_reference_documents WHERE work_id=? AND content_hash=?", (work_id, digest)
        ).fetchone()
    item = dict(row)
    item["enabled"], item["pinned"] = bool(item["enabled"]), bool(item["pinned"])
    return item


def update_document(user_id, work_id, document_id, payload):
    payload = payload if isinstance(payload, dict) else {}
    allowed = {key: payload[key] for key in ("name", "tags", "enabled", "pinned") if key in payload}
    if not allowed:
        return next((item for item in (list_documents(user_id, work_id) or []) if item["id"] == document_id), None)
    assignments, params = [], []
    for key, value in allowed.items():
        assignments.append(f"{key}=?")
        if key in {"enabled", "pinned"}:
            params.append(int(bool(value)))
        else:
            params.append(str(value or "")[:240 if key == "name" else 1000])
    params.extend([time.time(), document_id, work_id, user_id])
    with db.get_conn() as conn:
        cur = conn.execute(
            "UPDATE work_reference_documents SET " + ",".join(assignments) + ",updated_at=? "
            "WHERE id=? AND work_id=? AND user_id=?", params,
        )
        if not cur.rowcount:
            return None
    return next((item for item in list_documents(user_id, work_id) if item["id"] == document_id), None)


def delete_document(user_id, work_id, document_id):
    with db.get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM work_reference_documents WHERE id=? AND work_id=? AND user_id=?",
            (document_id, work_id, user_id),
        )
        return cur.rowcount > 0


def get_disassembly_material_source(user_id, job_id):
    with db.get_conn() as conn:
        job = conn.execute(
            "SELECT j.*,w.title AS work_title FROM book_disassembly_jobs j JOIN works w ON w.id=j.target_work_id "
            "WHERE j.id=? AND j.user_id=? AND w.user_id=?", (job_id, user_id, user_id)
        ).fetchone()
        if not job:
            return None
        rows = conn.execute(
            "SELECT ord,title,result_json FROM book_disassembly_chapters WHERE job_id=? AND status='done' ORDER BY ord,id",
            (job_id,),
        ).fetchall()
    return {"job": dict(job), "chapters": [
        {"ord": row["ord"], "title": row["title"], "result": _decode(row["result_json"], {})}
        for row in rows
    ]}


def save_disassembly_extraction(user_id, job_id, work_id, result, inspiration_ids=None, status="completed", error=""):
    now = time.time()
    with db.get_conn() as conn:
        if not _owned(conn, work_id, user_id):
            return None
        conn.execute(
            "INSERT INTO disassembly_material_extractions(job_id,user_id,work_id,status,result_json,"
            "inspiration_ids_json,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,result_json=excluded.result_json,"
            "inspiration_ids_json=excluded.inspiration_ids_json,error=excluded.error,updated_at=excluded.updated_at",
            (job_id, user_id, work_id, status, json.dumps(result or {}, ensure_ascii=False),
             json.dumps(inspiration_ids or [], ensure_ascii=False), str(error or "")[:1000], now, now),
        )
    return True


def get_dashboard(user_id, work_id):
    settings = get_settings(user_id, work_id)
    if settings is None:
        return None
    with db.get_conn() as conn:
        works = [dict(row) for row in conn.execute(
            "SELECT id,title FROM works WHERE user_id=? AND id<>? ORDER BY updated_at DESC", (user_id, work_id)
        )]
        extractions = [dict(row) for row in conn.execute(
            "SELECT x.job_id,x.status,x.result_json,x.inspiration_ids_json,x.error,x.updated_at,j.source_name "
            "FROM disassembly_material_extractions x JOIN book_disassembly_jobs j ON j.id=x.job_id "
            "WHERE x.user_id=? AND x.work_id=? ORDER BY x.updated_at DESC", (user_id, work_id)
        )]
    for item in extractions:
        item["result"] = _decode(item.pop("result_json"), {})
        item["inspiration_ids"] = _decode(item.pop("inspiration_ids_json"), [])
    return {
        "settings": settings,
        "mounts": list_mounts(user_id, work_id) or [],
        "available_works": works,
        "style_profile": get_style_profile(user_id, work_id),
        "documents": list_documents(user_id, work_id) or [],
        "extractions": extractions,
    }


def _terms(text):
    terms = set(re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", text or ""))
    for run in list(terms):
        if re.fullmatch(r"[\u4e00-\u9fff]+", run):
            terms.update(run[index:index + 2] for index in range(max(1, len(run) - 1)))
    return {term.lower() for term in terms if term}


def _render_profile(profile):
    labels = {
        "narrative_voice": "叙述声音", "point_of_view": "视角", "pacing": "节奏",
        "sentence_rhythm": "句式节律", "diction": "措辞", "description_preferences": "描写偏好",
        "dialogue_pattern": "对话习惯", "emotional_tone": "情绪底色", "avoid": "避免事项",
    }
    lines = [f"{label}：{profile[key]}" for key, label in labels.items() if profile.get(key)]
    voices = profile.get("character_voices") or []
    if voices:
        lines.append("人物语言指纹：" + "；".join("、".join(f"{k}={v}" for k, v in item.items()) for item in voices[:10]))
    craft = profile.get("description_craft") or []
    if craft:
        lines.append("描写手法：" + "；".join("、".join(f"{k}={v}" for k, v in item.items()) for item in craft[:8]))
    return "\n".join(lines)


def context_items(user_id, work_id, query, *, include_memory=True):
    """Return optional context items; canonical story memory remains in context_builder."""
    settings = get_settings(user_id, work_id)
    if settings is None:
        return []
    items = []
    profile = get_style_profile(user_id, work_id)
    if settings["use_style_profile"] and profile and profile.get("profile"):
        rendered = _render_profile(profile["profile"])
        if rendered:
            strength = {"light": "轻度参考", "balanced": "保持一致", "strong": "优先遵循"}[settings["style_strength"]]
            items.append({"type": "style_profile", "title": "本书语言指纹", "content": rendered[:9000],
                          "reason": f"作者已启用，强度：{strength}", "priority": 1})

    if settings["use_reference_projects"]:
        with db.get_conn() as conn:
            mounts = conn.execute(
                "SELECT m.*,w.title FROM work_reference_mounts m JOIN works w ON w.id=m.reference_work_id "
                "WHERE m.work_id=? AND m.user_id=? AND m.enabled=1 ORDER BY m.updated_at DESC LIMIT 5",
                (work_id, user_id),
            ).fetchall()
            for mount in mounts:
                blocks = []
                if mount["use_style"]:
                    row = conn.execute("SELECT profile_json FROM work_style_profiles WHERE work_id=?", (mount["reference_work_id"],)).fetchone()
                    if row:
                        text = _render_profile(_decode(row["profile_json"], {}))
                        if text:
                            blocks.append("抽象文风技法：\n" + text[:4500])
                if mount["use_plot"]:
                    rows = conn.execute(
                        "SELECT title,workflow_summary FROM chapters WHERE work_id=? AND deleted_at IS NULL "
                        "AND workflow_summary!='' ORDER BY ord LIMIT 18", (mount["reference_work_id"],)
                    ).fetchall()
                    if rows:
                        blocks.append("情节结构摘要：\n" + "\n".join(f"《{r['title']}》：{r['workflow_summary']}" for r in rows)[:5500])
                    inspirations = conn.execute(
                        "SELECT title,core_mechanism,adaptation_notes FROM creative_inspirations "
                        "WHERE work_id=? AND user_id=? AND primary_category='plot' AND library_status IN ('inbox','available') "
                        "ORDER BY importance DESC,updated_at DESC LIMIT 6", (mount["reference_work_id"], user_id)
                    ).fetchall()
                    if inspirations:
                        blocks.append("可改编桥段：\n" + "\n".join(
                            f"{r['title']}：{r['core_mechanism']}；改编边界：{r['adaptation_notes']}" for r in inspirations
                        )[:4500])
                if mount["use_world"]:
                    source_notes = conn.execute("SELECT notes FROM works WHERE id=?", (mount["reference_work_id"],)).fetchone()
                    if source_notes and source_notes["notes"]:
                        blocks.append("世界观参考：\n" + source_notes["notes"][:3500])
                if blocks:
                    items.append({"type": "reference_project", "title": f"只读参考工程：{mount['title']}",
                                  "content": "\n\n".join(blocks)[:11000],
                                  "reason": "作者挂载的借鉴源；只借结构和技法，不作为本书事实", "priority": 3})

    if settings["use_reference_documents"]:
        docs = list_documents(user_id, work_id, include_content=True) or []
        query_terms = _terms(query)
        ranked = []
        for document in docs:
            if not document["enabled"]:
                continue
            haystack = f"{document['name']} {document.get('tags') or ''} {document.get('content') or ''}".lower()
            score = sum(1 for term in query_terms if term in haystack)
            if document["pinned"]:
                score += 100
            if score or not query_terms:
                ranked.append((score, document))
        for _, document in sorted(ranked, key=lambda pair: (pair[0], pair[1]["updated_at"]), reverse=True)[:3]:
            items.append({"type": "reference_document", "title": f"长期参考：{document['name']}",
                          "content": document["content"][:6500],
                          "reason": "置顶资料" if document["pinned"] else "与本轮指令匹配的长期资料", "priority": 2})

    if settings["use_inspirations"] and (query or "").strip():
        try:
            import inspiration
            candidates = inspiration.search_inspirations(
                user_id, query, work_id=work_id, include_global=True, include_used=False, limit=4
            )
        except Exception:
            candidates = []
        if candidates:
            lines = []
            for item in candidates:
                if item.get("use_policy") == "manual_only":
                    continue
                lines.append(f"#{item['id']} {item.get('title') or '未命名'}："
                             f"{item.get('creative_summary') or item.get('core_mechanism') or item.get('raw_text') or ''}；"
                             f"改编提示：{item.get('adaptation_notes') or '需结合本书人物与因果重新设计'}")
            if lines:
                items.append({"type": "inspiration", "title": "召回的候选灵感", "content": "\n".join(lines)[:7000],
                              "reason": "与本轮创作意图匹配；仅为候选，采用后应记录使用位置", "priority": 3})
    return items
