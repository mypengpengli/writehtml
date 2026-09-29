"""Focused Writing Director and opt-in quality review checks (no network calls)."""
import os
import uuid
from pathlib import Path

root = Path(__file__).resolve().parent
test_dir = root / ".smoke_tmp" / ("director-" + uuid.uuid4().hex[:10])
test_dir.mkdir(parents=True, exist_ok=True)
os.environ["DB_PATH"] = str(test_dir / "test.db")
os.environ["PI_AGENT_ENABLED"] = "false"
os.environ["INSPIRATION_WORKER_ENABLED"] = "false"
os.environ["SIGNUP_CODE"] = "testcode"
os.environ["WRITEHTML_ADMIN_PASSWORD"] = "admintest"

from fastapi.testclient import TestClient
import config
import db
import llm
import main
import writing_guidance

db.init_db()
client = TestClient(main.app)


def expect(value, label):
    assert value, label
    print("OK", label)


token = client.post("/api/register", json={
    "username": "director-test", "password": "pw1234", "code": "testcode",
}).json()["token"]
headers = {"Authorization": "Bearer " + token}
uid = db.verify_user("director-test", "pw1234")["id"]
work_id = client.post("/api/works", json={"title": "导演测试"}, headers=headers).json()["id"]

empty = client.get(f"/api/works/{work_id}/writing-guidance", headers=headers).json()
expect(empty["stage"] == "foundation" and empty["primary_action"]["id"] == "build_story_foundation"
       and not empty["blockers"] and len(empty["actions"]) <= 2, "空作品只有软建议和备选动作")
expect(client.get(f"/api/works/{work_id}/writing-guidance?intent=bad", headers=headers).status_code == 400,
       "非法意图被拒绝")
expect(client.get(f"/api/works/{work_id}/writing-guidance", headers={}).status_code == 401,
       "引导数据需要登录")

chapter_id = client.post(f"/api/works/{work_id}/chapters", json={"title": "第一章"}, headers=headers).json()["id"]
book = client.post(f"/api/works/{work_id}/story-plan", json={
    "node_type": "book", "title": "故事方向", "summary": "寻找失踪的钥匙",
}, headers=headers).json()
no_chapter_plan = client.get(
    f"/api/works/{work_id}/writing-guidance?chapter_id={chapter_id}", headers=headers,
).json()
expect(no_chapter_plan["stage"] == "chapter_planning"
       and no_chapter_plan["primary_action"]["id"] == "complete_chapter_plan",
       "有总纲但无章纲时提示补本章计划")

plan = client.post(f"/api/works/{work_id}/story-plan", json={
    "node_type": "chapter", "parent_id": book["id"], "chapter_id": chapter_id,
    "title": "第一章计划", "goal": "找到线索", "conflict": "线索被人抢走", "expected_outcome": "决定合作",
}, headers=headers).json()
ready = client.get(f"/api/works/{work_id}/writing-guidance?chapter_id={chapter_id}", headers=headers).json()
expect(ready["stage"] == "drafting" and ready["readiness"]["score"] > no_chapter_plan["readiness"]["score"],
       "章纲完整后可直接进入正文写作")

saved = client.put(f"/api/chapters/{chapter_id}", json={
    "content": "林晚走进旧车站，看见钥匙落在陌生人手中。", "expected_revision": 1,
}, headers=headers)
expect(saved.status_code == 200, "测试正文成功保存")
draft = client.get(f"/api/works/{work_id}/writing-guidance?chapter_id={chapter_id}", headers=headers).json()
expect(draft["stage"] == "drafting", "写作中不因一次正文保存强制分析")
review = client.get(
    f"/api/works/{work_id}/writing-guidance?chapter_id={chapter_id}&intent=review_chapter", headers=headers,
).json()
expect(review["stage"] == "review" and review["primary_action"]["id"] == "review_chapter",
       "作者明确询问本章质量时推荐按需复核")

db.update_chapter_workflow(chapter_id, uid, status="review")
needs_sync = client.get(f"/api/works/{work_id}/writing-guidance?chapter_id={chapter_id}", headers=headers).json()
expect(needs_sync["stage"] == "state_sync" and needs_sync["primary_action"]["kind"] == "analyze_chapter",
       "进入复核阶段且状态过期时建议同步 World State")
with db.get_conn() as conn:
    conn.execute("UPDATE chapters SET production_analysis_status='current' WHERE id=?", (chapter_id,))
needs_realization = client.get(f"/api/works/{work_id}/writing-guidance?chapter_id={chapter_id}", headers=headers).json()
expect(needs_realization["primary_action"]["id"] == "review_plan_realization",
       "状态同步后建议核对正文和章节计划")
pending_realization = client.post(f"/api/story-plan/{plan['id']}/realizations", json={
    "chapter_id": chapter_id, "status": "pending", "expected_plan_revision": plan["revision"],
    "expected_content_revision": db.get_chapter_meta(chapter_id, uid)["content_revision"],
}, headers=headers)
expect(pending_realization.status_code == 200, "可以保存待核对的章节计划实现状态")
still_pending = client.get(f"/api/works/{work_id}/writing-guidance?chapter_id={chapter_id}", headers=headers).json()
expect(still_pending["primary_action"]["id"] == "review_plan_realization",
       "待核对记录不被误判为完成")

entity = db.create_entity(work_id, uid, "林晚", "人物", "旅客", "谨慎")
db.upsert_character_state_proposal(entity["id"], uid, chapter_id, {"location": "车站"}, "抵达车站")
db.upsert_plot_state_proposal(work_id, uid, chapter_id, {"current_event": "寻找钥匙"})
db.upsert_story_memory_proposal(work_id, uid, chapter_id, {
    "memory_type": "event", "title": "遇到陌生人", "content": "钥匙在陌生人手中", "evidence": "陌生人手中",
})
pending_guide = writing_guidance.build_writing_guidance(uid, work_id, chapter_id)
expect(pending_guide["primary_action"]["id"] == "open_chapter_state"
       and all(pending_guide["facts"]["pending_by_type"][kind] == 1 for kind in ("characters", "plot", "memory")),
       "人物、剧情和记忆提议均纳入导演待确认检查")
with db.get_conn() as conn:
    for table in ("entity_state_proposals", "plot_state_proposals", "story_memory_items"):
        conn.execute(f"UPDATE {table} SET source_content_hash='old-content' WHERE chapter_id=?", (chapter_id,))
stale_guide = writing_guidance.build_writing_guidance(uid, work_id, chapter_id)
expect(stale_guide["facts"]["pending_changes"] == 0
       and stale_guide["primary_action"]["id"] == "review_plan_realization",
       "旧正文产生的失效提议不重复催促确认")

expect(writing_guidance.infer_intent("别管规划，直接写第一章") == "write_chapter",
       "明确直接写能识别为写作意图")
expect(writing_guidance.infer_intent("先写再说") == "write_chapter", "口语化跳过规划指令能识别")
valid = writing_guidance.validate_guide_action(uid, work_id, chapter_id, {
    "id": "write_anyway", "work_id": work_id, "chapter_id": chapter_id,
})
expect(valid["id"] == "write_anyway", "直接写是合法的一次性覆盖动作")
for bad in (
    {"id": "eval", "work_id": work_id, "chapter_id": chapter_id},
    {"id": "write_anyway", "work_id": work_id + 1, "chapter_id": chapter_id},
    {"id": "write_anyway", "work_id": work_id, "chapter_id": chapter_id + 999},
):
    try:
        writing_guidance.validate_guide_action(uid, work_id, chapter_id, bad)
    except ValueError:
        pass
    else:
        raise AssertionError("非法按钮动作未被拒绝")
expect(True, "按钮 action 白名单、作品范围和章节范围均被校验")

original_key = config.LLM_API_KEY
original_chat = llm.chat
config.LLM_API_KEY = "fake-test-key"
calls = []


def fake_chat(messages, **kwargs):
    calls.append(messages)
    dimension = "开头吸引力" if "正文" in messages[1]["content"] else "主角目标"
    return ("{\"summary\":\"测试建议\",\"dimensions\":[{\"name\":\"" + dimension
            + "\",\"score\":7,\"evidence\":\"输入中有明确线索\",\"suggestion\":\"加强选择的代价\"}]}")


llm.chat = fake_chat
try:
    chapter_review = client.post(f"/api/chapters/{chapter_id}/writing-quality-review", json={}, headers=headers)
    expect(chapter_review.status_code == 200 and chapter_review.json()["dimensions"][0]["score"] == 7,
           "正文体检按需调用模型并返回证据化维度")
    plan_review = client.post(f"/api/story-plan/{plan['id']}/quality-review", json={}, headers=headers)
    expect(plan_review.status_code == 200 and plan_review.json()["kind"] == "plan",
           "规划体检按需调用模型")
    book_review = client.post(f"/api/story-plan/{book['id']}/quality-review", json={}, headers=headers)
    expect(book_review.status_code == 200 and "第一章计划" in calls[-1][1]["content"],
           "总纲体检能看到下级章节计划")
    expect(len(calls) == 3 and db.get_chapter_meta(chapter_id, uid)["content_revision"] == chapter_review.json()["source_version"]
           and db.get_story_plan_node(plan["id"], uid)["revision"] == plan["revision"],
           "体检不修改正文或规划")
finally:
    llm.chat = original_chat
    config.LLM_API_KEY = original_key

print("Writing Director checks passed.")
