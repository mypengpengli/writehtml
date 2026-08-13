"""Single, inspectable context assembly for writing and review tasks."""
from __future__ import annotations

from typing import Iterable

import db
import materials


TASK_LABELS = {
    "continue_writing": "续写",
    "rewrite_selection": "改写选区",
    "brainstorm": "创作讨论",
    "chapter_review": "章节复核",
    "extract_memory": "故事记忆提取",
    "extract_character_state": "人物状态提取",
    "extract_plot_state": "剧情状态提取",
    "answer_story_question": "故事问答",
}


def _clip(value, limit):
    value = (value or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit] + "\n[已截断]"


def _token_estimate(value):
    # Chinese prose is usually close to one token per 1.5-2.5 characters; use a
    # conservative estimate so the preview warns before a prompt gets too large.
    return max(1, (len(value or "") + 1) // 2)


def _item(item_type, title, content, reason, priority):
    content = (content or "").strip()
    if not content:
        return None
    return {
        "type": item_type,
        "title": title,
        "content": content,
        "reason": reason,
        "priority": priority,
        "estimated_tokens": _token_estimate(content),
    }


def _state_lines(labels, state):
    return "；".join(f"{labels[key]}：{state[key]}" for key in labels if state.get(key))


def _mentioned_entities(entities, text):
    text = text or ""
    matches = [entity for entity in entities if entity.get("name") and entity["name"] in text]
    return matches or entities[:6]


def _render_memories(memories):
    lines = []
    for memory in memories:
        entities = "、".join(memory.get("entity_names") or [])
        suffix = f"（{entities}）" if entities else ""
        lines.append(
            f"第{memory.get('chapter_ord') or '?'}章《{memory.get('chapter_title') or '未命名'}》："
            f"{memory.get('title') or '未命名记忆'}{suffix}\n{memory.get('content') or ''}"
        )
    return "\n\n".join(lines)


def _render_summaries(summaries):
    return "\n".join(
        f"第{item.get('ord') or '?'}章《{item.get('title') or '未命名'}》：{item.get('workflow_summary') or ''}"
        for item in summaries
    )


def _fit(items: Iterable[dict], token_budget):
    items = [item for item in items if item]
    if not token_budget:
        return items
    budget = max(800, int(token_budget))
    kept = []
    used = 0
    # Preserve author instruction, current text and explicit state before optional history.
    for item in sorted(items, key=lambda item: (item["priority"], item["estimated_tokens"])):
        if item["priority"] <= 1 or used + item["estimated_tokens"] <= budget:
            kept.append(item)
            used += item["estimated_tokens"]
    return kept


def render_context(context, types=None):
    allowed = set(types) if types else None
    items = context.get("context_items") or []
    blocks = []
    for item in items:
        if allowed and item.get("type") not in allowed:
            continue
        blocks.append(f"【{item.get('title') or item.get('type')}】\n{item.get('content') or ''}")
    return "\n\n".join(blocks)


def build_context(user_id, task_type, work_id, chapter_id=None, instruction="", selection=None,
                  skill_ids=None, token_budget=None):
    """Return prompt messages plus every source item and its recall reason.

    The returned structure is intentionally useful to both a model and the context
    preview UI. Only confirmed, source-current memories participate in recall.
    """
    if task_type not in TASK_LABELS:
        task_type = "answer_story_question"
    chapter = db.get_chapter_meta(chapter_id, user_id) if chapter_id else None
    if chapter_id and (not chapter or chapter["work_id"] != work_id):
        return None
    if not work_id:
        return None

    instruction = (instruction or "").strip()
    selected_text = ""
    if isinstance(selection, dict) and isinstance(selection.get("text"), str):
        selected_text = _clip(selection["text"], 8000)
    chapter_content = (chapter or {}).get("content") or ""
    chapter_tail = _clip(chapter_content[-7000:], 7000)
    mentions_text = "\n".join((instruction, selected_text, chapter_tail[-1800:]))
    items = []

    if instruction:
        items.append(_item("instruction", "作者本轮指令", instruction, "本轮明确请求", 0))
    if chapter:
        items.append(_item(
            "chapter", f"当前章节：第{chapter.get('ord') or '?'}章《{chapter.get('title') or '未命名'}》",
            selected_text if selected_text else chapter_tail,
            "用户选区" if selected_text else "当前章节的最近正文",
            0,
        ))
        if chapter.get("notes"):
            items.append(_item("chapter_notes", "本章备注", _clip(chapter["notes"], 3000), "作者为当前章留下的约束", 1))

    work_notes = db.get_work_notes(work_id, user_id) or ""
    if work_notes:
        items.append(_item("work_bible", "作品设定", _clip(work_notes, 9000), "全局创作约束", 1))

    entities = db.list_character_cards(work_id, user_id, chapter_id) or []
    active_entities = _mentioned_entities(entities, mentions_text)
    entity_ids = [entity["id"] for entity in active_entities]
    for entity in active_entities:
        facts = []
        if entity.get("summary"):
            facts.append("基础设定：" + _clip(entity["summary"], 900))
        if entity.get("detail"):
            facts.append("详细设定：" + _clip(entity["detail"], 1600))
        state = _state_lines(db.CHARACTER_STATE_LABELS, entity.get("current_state") or {})
        if state:
            facts.append("当前状态：" + state)
        if facts:
            items.append(_item(
                "character_state", f"人物卡：{entity.get('name') or '未命名'}", "\n".join(facts),
                "当前请求出现该人物" if entity.get("name") in mentions_text else "当前章节时点的人物状态",
                1 if entity.get("name") in mentions_text else 2,
            ))

    plot = db.get_plot_state_overview(work_id, user_id, chapter_id) if chapter_id else None
    if plot and not plot.get("invalid_chapter"):
        state = _state_lines(db.PLOT_STATE_LABELS, plot.get("current_state") or {})
        if state:
            items.append(_item("plot_state", "当前剧情状态", state, "当前章节时点的已确认剧情推进", 1))

    relationships = db.get_relationship_digest(work_id, user_id)
    if relationships:
        items.append(_item("relationships", "人物关系", _clip(relationships, 5000), "保持人物互动连续", 2))

    recall_query = "\n".join((instruction, selected_text, chapter_tail[-1200:]))
    material_settings = materials.get_settings(user_id, work_id) or materials.DEFAULT_SETTINGS
    memories = []
    if material_settings.get("use_story_memory", True):
        memories = db.search_story_memories(
            work_id, user_id, recall_query, entity_ids=entity_ids or None,
            before_chapter_id=chapter_id, limit=12,
        ) or []
        if not memories and chapter_id:
            memories = db.list_recent_story_memories(work_id, user_id, before_chapter_id=chapter_id, limit=6) or []
    if memories:
        items.append(_item(
            "memory", "召回的故事记忆", _render_memories(memories),
            "关键词、当前出场人物和章节时点匹配", 2,
        ))

    summaries = db.list_recent_chapter_summaries(work_id, user_id, before_chapter_id=chapter_id, limit=4) or []
    if summaries:
        items.append(_item("chapter_summary", "最近章节摘要", _render_summaries(summaries), "补足近期剧情衔接", 3))

    # Reference projects, style fingerprints, reusable documents and inspirations
    # are optional aids. Canonical work notes and confirmed story facts above keep
    # higher priority and are never overwritten by these sources.
    for material in materials.context_items(user_id, work_id, recall_query):
        items.append(_item(
            material.get("type") or "reference",
            material.get("title") or "创作参考资料",
            material.get("content") or "",
            material.get("reason") or "作者启用的参考资料",
            int(material.get("priority", 3)),
        ))

    skills = db.get_agent_skills_for_turn(user_id, work_id, skill_ids or []) if skill_ids else []
    if skills:
        skill_lines = [f"{skill['name']}：{_clip(skill.get('description') or skill.get('instruction') or '', 800)}"
                       for skill in skills]
        items.append(_item("skills", "本轮已启用 Skill", "\n".join(skill_lines), "作者本轮显式启用", 1))

    items = _fit(items, token_budget)
    messages = [{"role": "system", "content": f"【{item['title']}】\n{item['content']}"} for item in items]
    return {
        "task_type": task_type,
        "task_label": TASK_LABELS[task_type],
        "chapter": ({"id": chapter["id"], "work_id": chapter["work_id"], "title": chapter["title"],
                     "ord": chapter.get("ord"), "content_revision": chapter.get("content_revision"),
                     "analysis_status": chapter.get("analysis_status")} if chapter else None),
        "context_items": items,
        "messages": messages,
        "estimated_tokens": sum(item["estimated_tokens"] for item in items),
        "recalled_memory_ids": [memory["id"] for memory in memories],
    }
