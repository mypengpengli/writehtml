"""Deterministic, advisory next steps for the existing writing workflow."""

import re

import db


INTENTS = {
    "general", "plan_story", "simulate", "write_chapter", "continue_chapter",
    "review_chapter", "next_chapter",
}
AGENT_ACTIONS = {
    "build_story_foundation", "create_book_plan", "complete_chapter_plan",
    "simulate_next", "write_chapter", "continue_chapter", "write_anyway",
    "review_plan_realization", "review_chapter", "go_next_chapter",
}
STAGE_LABELS = {
    "foundation": "故事基础", "story_planning": "剧情规划", "chapter_planning": "本章计划",
    "simulation": "剧情推演", "drafting": "正文写作", "state_sync": "状态同步",
    "review": "章节复核", "next_chapter": "下一章",
}


def infer_intent(text):
    text = (text or "").strip()
    if not text:
        return None
    if re.search(r"接下来怎么发展|剧情推演|推演一下|有哪些走向|分支剧情", text):
        return "simulate"
    if re.search(r"下一章|下章写什么", text):
        return "next_chapter"
    if re.search(r"续写|接着写|继续写|往下写", text):
        return "continue_chapter"
    if re.search(r"写第.{0,8}章|写一章|生成.{0,8}正文|直接写|开始写|帮我写|先写再说|就按现在.{0,5}写", text):
        return "write_chapter"
    if re.search(r"章纲|总纲|卷纲|大纲|规划剧情|怎么规划|想写小说", text):
        return "plan_story"
    if re.search(r"复核|体检|检查本章|检查.{0,8}正文|这章怎么样", text):
        return "review_chapter"
    return None


def _action(action_id, kind, label, work_id, chapter_id=None, plan_id=None, target=None):
    action = {"id": action_id, "kind": kind, "label": label, "work_id": work_id}
    if chapter_id is not None:
        action["chapter_id"] = chapter_id
    if plan_id is not None:
        action["plan_id"] = plan_id
    if target is not None:
        action["target"] = target
    return action


def build_writing_guidance(user_id, work_id, chapter_id=None, intent="general"):
    if intent not in INTENTS:
        raise ValueError("invalid intent")
    with db.get_conn() as conn:
        work = conn.execute("SELECT id,title,notes FROM works WHERE id=? AND user_id=?", (work_id, user_id)).fetchone()
        if not work:
            return None
        chapters = [dict(row) for row in conn.execute(
            "SELECT id,title,ord,length(content) AS chars,workflow_status,analysis_status,"
            "production_analysis_status,content_hash FROM chapters "
            "WHERE work_id=? AND deleted_at IS NULL ORDER BY ord,id", (work_id,),
        )]
        chapter = next((row for row in chapters if row["id"] == chapter_id), None) if chapter_id else (chapters[-1] if chapters else None)
        if chapter_id is not None and chapter is None:
            return None
        previous = next((row for row in reversed(chapters) if chapter and row["ord"] < chapter["ord"]), None)
        plans = [dict(row) for row in conn.execute(
            "SELECT id,node_type,status,parent_id,chapter_id,title,goal,conflict,expected_outcome,"
            "source_sandbox_id FROM story_plan_nodes WHERE work_id=? AND deleted_at IS NULL "
            "AND status<>'abandoned'", (work_id,),
        )]
        characters = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE work_id=? AND kind='人物'", (work_id,),
        ).fetchone()[0]
        rules = conn.execute(
            "SELECT COUNT(*) FROM production_cards WHERE work_id=? AND category='rule' AND status='confirmed'",
            (work_id,),
        ).fetchone()[0]
        pending = impacts = critical = warnings_count = 0
        if chapter:
            cid = chapter["id"]
            pending = conn.execute(
                "SELECT COUNT(*) FROM production_proposals WHERE chapter_id=? AND status='pending'", (cid,),
            ).fetchone()[0]
            impacts = conn.execute(
                "SELECT COUNT(*) FROM production_impact_flags WHERE affected_chapter_id=? AND status='open'", (cid,),
            ).fetchone()[0]
            alerts = conn.execute(
                "SELECT severity,COUNT(*) AS total FROM chapter_consistency_alerts "
                "WHERE chapter_id=? AND status='open' AND stale=0 "
                "AND (source_content_hash='' OR source_content_hash=?) GROUP BY severity",
                (cid, chapter["content_hash"]),
            ).fetchall()
            critical = sum(row["total"] for row in alerts if row["severity"] == "critical")
            warnings_count = sum(row["total"] for row in alerts if row["severity"] == "warning")
        sandbox_count = conn.execute(
            "SELECT COUNT(*) FROM story_sandboxes WHERE work_id=?", (work_id,),
        ).fetchone()[0]

    chapter_plans = [row for row in plans if chapter and row["chapter_id"] == chapter["id"]]
    book_plan = next((row for row in plans if row["node_type"] == "book"), None)
    main_plan = next((row for row in chapter_plans if row["node_type"] == "chapter"), None)
    main_plan = main_plan or (chapter_plans[0] if chapter_plans else None)
    realization_rows = db.list_chapter_story_plans(chapter["id"], user_id) if chapter and chapter_plans else []
    realized_ids = {
        item["id"] for item in (realization_rows or [])
        if any(record.get("source_current") and record["chapter_id"] == chapter["id"]
               and record["status"] in {"partial", "realized", "deviated"}
               for record in item.get("realizations") or [])
    }
    realization_pending = bool(chapter_plans and chapter and chapter["chars"]
                               and any(item["id"] not in realized_ids for item in chapter_plans))
    previous_stale = bool(previous and previous["chars"] and previous["production_analysis_status"] != "current")
    plan_complete = bool(main_plan and all((main_plan.get(field) or "").strip()
                                           for field in ("goal", "conflict", "expected_outcome")))
    checks = [
        {"key": "story_direction", "status": "ready" if book_plan else "optional", "label": "故事方向",
         "detail": "已有总纲" if book_plan else "还没有总纲；可以先写，再逐步整理"},
        {"key": "character", "status": "ready" if characters else "optional", "label": "人物基础",
         "detail": f"已有 {characters} 张人物卡" if characters else "尚未建立人物卡"},
        {"key": "chapter", "status": "ready" if chapter else "missing", "label": "目标章节",
         "detail": chapter["title"] if chapter else "还没有章节，AI 可以帮你创建"},
        {"key": "chapter_plan", "status": "ready" if plan_complete else "warning" if main_plan else "optional",
         "label": "本章计划", "detail": "目标、冲突和预期结果已明确" if plan_complete else
         "已有计划，但目标/冲突/预期结果尚未写全" if main_plan else "尚无直接关联的章节计划"},
        {"key": "previous_state", "status": "warning" if previous_stale else "ready", "label": "前文状态",
         "detail": "上一章状态尚未同步" if previous_stale else "没有发现前章状态待同步"},
    ]
    if critical:
        checks.append({"key": "continuity", "status": "warning", "label": "连续性风险",
                       "detail": f"有 {critical} 项当前正文的严重提醒"})
    score = (20 if chapter else 0) + (25 if main_plan else 0) + (10 if main_plan and main_plan["goal"].strip() else 0)
    score += (10 if main_plan and main_plan["conflict"].strip() else 0)
    score += (10 if main_plan and main_plan["expected_outcome"].strip() else 0)
    score += (10 if characters else 0) + (10 if not previous_stale else 0) + (5 if not critical else 0)
    level = "准备充分" if score >= 80 else "可以开写，尚可补充" if score >= 60 else "资料较少，也可以直接写"
    cid = chapter["id"] if chapter else None
    pid = main_plan["id"] if main_plan else None
    agent = lambda aid, label: _action(aid, "agent_intent", label, work_id, cid, pid)
    nav = lambda aid, label, target: _action(aid, "navigate", label, work_id, cid, pid, target)
    analyze = _action("sync_world_state", "analyze_chapter", "分析本章状态", work_id, cid)
    write_anyway = agent("write_anyway", "直接写")
    write = agent("write_chapter" if not chapter or not chapter["chars"] else "continue_chapter",
                  "开始写本章" if not chapter or not chapter["chars"] else "继续写本章")
    if intent == "simulate":
        stage, message = "simulation", "可以先推演几条候选路线；候选不会自动改正文或正式规划。"
        primary, alternatives = agent("simulate_next", "AI 帮我推演"), [nav("open_story_plan", "查看剧情规划", "production_plan"), write_anyway]
    elif intent == "review_chapter" and chapter and chapter["chars"]:
        stage, message = "review", "可以按需复核本章；连续性检查与文笔建议会分开呈现。"
        primary, alternatives = agent("review_chapter", "检查本章"), [nav("open_chapter_state", "查看章节状态", "production_state")]
    elif not book_plan and not characters and not chapters:
        stage, message = "foundation", "从一句话故事开始就行。可以先建立最小基础，也可以直接写第一章。"
        primary, alternatives = agent("build_story_foundation", "AI 帮我搭基础"), [agent("create_book_plan", "整理故事方向"), write_anyway]
    elif not book_plan and intent != "continue_chapter" and not (chapter and chapter["chars"]):
        stage, message = "story_planning", "已有一些材料，但故事方向尚未整理成总纲；这不是开写的前提。"
        primary, alternatives = agent("create_book_plan", "AI 帮我理总纲"), [write_anyway, nav("open_world", "看世界设定", "production_canon")]
    elif chapter and not chapter["chars"] and not plan_complete and intent != "continue_chapter":
        stage, message = "chapter_planning", "本章方向还不够明确。补一个简短章纲会更容易控制推进，也可以直接写。"
        primary, alternatives = agent("complete_chapter_plan", "AI 帮我补章纲"), [agent("simulate_next", "先推演"), write_anyway]
    elif chapter and chapter["chars"] and (intent == "next_chapter" or chapter["workflow_status"] in {"review", "final"}):
        if chapter["production_analysis_status"] != "current":
            stage, message = "state_sync", "正文已经有改动，本章状态尚未同步。建议先分析，再开始下一章。"
            primary, alternatives = analyze, [nav("open_chapter_state", "查看章节状态", "production_state"), agent("go_next_chapter", "先进入下一章")]
        elif pending or impacts or critical:
            stage, message = "review", "本章还有待确认的变化或风险，处理后再推进会更稳。"
            primary, alternatives = nav("open_chapter_state", "处理本章变化", "production_state"), [agent("go_next_chapter", "先进入下一章")]
        elif realization_pending:
            stage, message = "review", "本章计划还没有与正文核对。可以先核对，也可以继续下一章。"
            primary, alternatives = agent("review_plan_realization", "核对计划与正文"), [agent("go_next_chapter", "先进入下一章")]
        else:
            stage, message = "next_chapter", "本章当前没有待处理的状态或计划核对。"
            primary, alternatives = agent("go_next_chapter", "进入下一章"), [agent("simulate_next", "推演后续"), nav("open_story_plan", "查看规划", "production_plan")]
    else:
        stage = "drafting"
        message = "当前可以继续写；规划和设定是参考，不会阻止你动笔。"
        primary, alternatives = write, [analyze, nav("open_story_plan", "查看规划", "production_plan")] if chapter and chapter["chars"] else [nav("open_story_plan", "查看规划", "production_plan"), agent("simulate_next", "先推演")]
    if not chapter:
        alternatives = [item for item in alternatives if item["kind"] != "analyze_chapter"]
    return {
        "work_id": work_id, "chapter_id": cid, "stage": stage, "stage_label": STAGE_LABELS[stage],
        "intent": intent, "readiness": {"score": score, "level": level}, "checks": checks,
        "message": message, "warnings": [item["detail"] for item in checks if item["status"] == "warning"],
        "blockers": [], "primary_action": primary, "actions": alternatives[:2],
        "facts": {"chapter_chars": chapter["chars"] if chapter else 0, "chapter_workflow": chapter["workflow_status"] if chapter else None,
                  "production_analysis_status": chapter["production_analysis_status"] if chapter else None,
                  "character_count": characters, "rule_count": rules, "sandbox_count": sandbox_count,
                  "pending_changes": pending, "downstream_impacts": impacts, "critical_alerts": critical,
                  "warning_alerts": warnings_count, "realization_pending": realization_pending,
                  "work_notes_present": bool((work["notes"] or "").strip())},
    }


_ACTION_INSTRUCTIONS = {
    "build_story_foundation": "和作者确认一句话故事后，建立最小人物基础和必要设定；不要替作者决定整本书。",
    "create_book_plan": "读取现有故事资料，整理一个简洁总纲并保存到 Story Plan；不要写成世界事实。",
    "complete_chapter_plan": "读取本章现有计划与相关设定，只补缺失的目标、冲突或预期结果；保留作者已确定的内容。",
    "simulate_next": "实际使用沙盘工具创建或打开推演，并保存多个候选分支；候选不能直接当作正文事实或正式规划。",
    "write_chapter": "按作者当前要求写本章；若缺规划可简短提醒一次，然后继续，不得强制先规划。",
    "continue_chapter": "读取当前最新正文后续写；以数据库正文为准，不重复写入对话中的旧版本。",
    "write_anyway": "作者选择跳过规划建议，直接写作；只提醒一次明显风险，不得阻止或反复追问。",
    "review_plan_realization": "对照本章最新正文和章节计划，引用正文证据后核对实现状态；不得把未来计划当事实。",
    "review_chapter": "按作者要求复核本章；区分连续性事实风险和主观文学建议，不要自动改稿。",
    "go_next_chapter": "按作者要求进入下一章；先确认目标章节和当前状态，不要自动覆盖现有正文。",
}


def validate_guide_action(user_id, work_id, chapter_id, value):
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("id") not in AGENT_ACTIONS or value.get("work_id") != work_id:
        raise ValueError("invalid guide action")
    action_chapter = value.get("chapter_id")
    if action_chapter is not None and (not isinstance(action_chapter, int) or isinstance(action_chapter, bool)):
        raise ValueError("invalid chapter")
    if chapter_id is not None and action_chapter not in (None, chapter_id):
        raise ValueError("chapter changed")
    guidance = build_writing_guidance(user_id, work_id, action_chapter, "general")
    if guidance is None:
        raise ValueError("chapter not found")
    plan_id = value.get("plan_id")
    if plan_id is not None:
        if not isinstance(plan_id, int) or isinstance(plan_id, bool):
            raise ValueError("invalid plan")
        plan = db.get_story_plan_node(plan_id, user_id)
        if not plan or plan["work_id"] != work_id or plan["status"] == "abandoned":
            raise ValueError("plan not found")
    return {"id": value["id"], "work_id": work_id, "chapter_id": action_chapter, "plan_id": plan_id}


def guide_system_note(guidance, guide_action=None):
    if not guidance:
        return ""
    note = (f"【写作导演状态】{guidance['stage_label']}。{guidance['message']} "
            f"当前首选：{guidance['primary_action']['label']}。这是软建议，不得阻止作者的明确要求。")
    if guide_action:
        note += "\n【作者点击的操作】" + _ACTION_INSTRUCTIONS[guide_action["id"]]
        if guide_action.get("chapter_id"):
            note += f" 目标章节 id={guide_action['chapter_id']}。"
        if guide_action.get("plan_id"):
            note += f" 目标规划 id={guide_action['plan_id']}。"
    return note
