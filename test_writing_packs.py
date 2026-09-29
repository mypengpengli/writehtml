"""Writing reference packs, scoped resource paging and read-only diagnostics."""
import os
from pathlib import Path
import time
import uuid

root = Path(__file__).resolve().parent
tmp = root / ".smoke_tmp" / ("writing-packs-" + uuid.uuid4().hex[:10])
tmp.mkdir(parents=True)
os.environ.update(DB_PATH=str(tmp / "test.db"), WRITEHTML_ADMIN_PASSWORD="test-admin",
                  INSPIRATION_WORKER_ENABLED="false", PI_AGENT_ENABLED="false")

from fastapi.testclient import TestClient
import builtin_skills
import db
import main
from writing_checks import mask_dialogue, scan_writing_text


def check(value, label):
    assert value, label
    print("OK", label)


client = TestClient(main.app)
alice = db.create_user("packs-alice", "testpass")["id"]
bob = db.create_user("packs-bob", "testpass")["id"]
main._sessions["packs-alice"] = alice
main._sessions["packs-bob"] = bob
headers = {"Authorization": "Bearer packs-alice"}
bob_headers = {"Authorization": "Bearer packs-bob"}
wid = db.create_work(alice, "Pack test")["id"]
other_work = db.create_work(alice, "Other work")["id"]
cid = db.create_chapter(wid, alice, "Chapter")["id"]
other_cid = db.create_chapter(other_work, alice, "Other chapter")["id"]

skills = client.get("/api/agent/skills", headers=headers).json()
by_name = {item["name"]: item for item in skills}
check(len([name for name in by_name if name.startswith("workshop-")]) == 8,
      "All eight expert viewpoints installed for a new account")
check(by_name["de-ai-novel"]["display_name"] == "小说修订与去模板感"
      and by_name["novel-workshop"]["resource_count"] >= 50, "Both archives exposed as named, complete reference packs")
check([item["name"] for item in skills if item["auto_activate"]] == ["writing-director"],
      "Expert methods stay opt-in; only the neutral director activates automatically")
before = {item["name"]: item["id"] for item in db.list_agent_skills(alice)}
db.init_db()
check(before == {item["name"]: item["id"] for item in db.list_agent_skills(alice)},
      "Restart sync preserves Skill identities without duplicate installations")

sid = by_name["de-ai-novel"]["id"]
cfg = {"work_id": wid, "active_skill_ids": {sid}}
resources = main._tool_list_skill_resources(alice, cid, cfg, {"skill_id": sid})["resources"]
long_path = next(item["path"] for item in resources if "narrative-craft" in item["path"])
full = db.get_agent_skill_resource(alice, sid, long_path)["content"]
offset, pieces = 0, []
while offset is not None:
    result = client.get(f"/api/agent/skills/{sid}/resource", params={"path": long_path, "offset": offset}, headers=headers)
    check(result.status_code == 200, "Reference page loads")
    page = result.json()
    pieces.append(page["content"])
    offset = page["next_offset"]
check("".join(pieces) == full and len(full) > 6000, "Paging reconstructs the entire long reference without gaps")
tail_query = "C类"
found = main._tool_read_skill_resource(alice, cid, cfg, {"skill_id": sid, "path": long_path, "query": tail_query})
check(found["matched"] and tail_query in found["_skill_resource_system"], "Agent can locate a method inside long references")
missing = main._tool_read_skill_resource(alice, cid, cfg, {"skill_id": sid, "path": long_path, "query": "NO_SUCH_WRITING_REFERENCE_0912"})
check(not missing["matched"] and "_skill_resource_system" not in missing, "Missing keywords do not inject empty reference instructions")
check(client.get(f"/api/agent/skills/{sid}/resource", params={"path": long_path}, headers=bob_headers).status_code == 404,
      "Another account cannot read a user's Skill resources")
check(client.get(f"/api/agent/skills/{sid}/resources", headers=bob_headers).status_code == 404,
      "Resource metadata is account-isolated")
check(client.get(f"/api/agent/skills/{sid}/resource", params={"path": "../main.py"}, headers=headers).status_code == 400,
      "Resource paths cannot access filesystem files")
check("error" in main._tool_read_skill_resource(alice, cid, {}, {"skill_id": sid, "path": long_path}),
      "Reading reference rules requires an activated Skill")
check("error" in main._tool_read_skill_resource(alice, cid, cfg, {"skill_id": sid, "path": long_path, "offset": True}),
      "Invalid paging arguments produce a tool error")
custom = db.create_agent_skill(alice, wid, "work-only-expert", "Work-only method", "WORK_ONLY_RULE")
catalog = main._agent_skill_catalog_system(alice, None, wid)["content"]
check("work-only-expert" in catalog and "work-only-expert" not in main._agent_skill_catalog_system(alice, None, other_work)["content"],
      "Work-scoped Skills are discovered without an editor chapter")
activation = main._tool_activate_skill(alice, None, {"work_id": wid}, {"skill_id": custom["id"]})
check("WORK_ONLY_RULE" in activation["_skill_system"], "A work-only Agent turn can activate its own Skill")
check("error" in main._tool_activate_skill(alice, None, {"work_id": other_work}, {"skill_id": custom["id"]}),
      "A different work cannot activate that Skill")

text = "他很紧张。\n“我很紧张，不由得深吸一口气。”\n\n他推开车站那扇生锈的铁门。\n他推开车站那扇生锈的铁门。\n【自检报告】节奏良好。"
masked = mask_dialogue(text)
check(len(masked) == len(text) and masked.count("\n") == text.count("\n"), "Dialogue masking preserves evidence offsets")
result = scan_writing_text(text)
check(all(text[item["start"]:min(item["end"], item["start"] + 240)] == item["evidence"] for item in result["findings"]),
      "Every diagnostic points to an exact original excerpt")
check(any(item["rule"] == "nearby_repeat" and item["line"] == 5 for item in result["findings"]),
      "Nearby duplicate prose is detected with original line numbers")
check(any(item["category"] == "delivery" for item in result["findings"]), "Review metadata in prose is reported")
check(not any(item["line"] == 2 for item in result["findings"]), "Quoted dialogue is exempt from surface style rules")
check(not any(item["rule"] == "state_change" for item in scan_writing_text("门开着。\n他随后关上了门。\n门关着。")["findings"]),
      "An explicit state transition is not reported as a contradiction")
db.update_chapter(cid, alice, None, text, None)
chapter_before = db.get_chapter_meta(cid, alice)
api_result = client.post(f"/api/chapters/{cid}/writing-scan", json={}, headers=headers)
check(api_result.status_code == 200 and api_result.json()["source_version"] == chapter_before["content_revision"],
      "The UI diagnostic is tied to the chapter version it examined")
check(db.get_chapter_meta(cid, alice) == chapter_before, "Scanning has no chapter or World State side effects")
check(client.post(f"/api/chapters/{cid}/writing-scan", json={}, headers=bob_headers).status_code >= 400,
      "Chapter scans enforce ownership")
check("error" in main._tool_scan_writing_text(alice, cid, {"work_id": wid}, {"chapter_id": other_cid}),
      "Agent diagnostics enforce the current work scope")
check(client.post(f"/api/chapters/{cid}/writing-scan", json={"limit": -1}, headers=headers).status_code == 400,
      "Invalid scan paging returns a clean API error")
check(client.post(f"/api/chapters/{cid}/writing-scan", json={"profile": []}, headers=headers).status_code == 400,
      "Invalid profile types return a clean API error")
page1 = scan_writing_text(text, limit=1)
page2 = scan_writing_text(text, limit=1, offset=page1["next_offset"])
check(page1["findings"] != page2["findings"] and page1["total"] == page2["total"], "Diagnostics are paged, not truncated")
start = time.monotonic()
long_result = scan_writing_text(("正常文本描述车站的石阶。\n" * 4000) + "【自检报告】末尾杂质。", offset=0, limit=1)
check(long_result["counts"].get("delivery") == 1 and long_result["stats"]["characters"] > 40000,
      "Long chapters are fully scanned, including their end")
check(time.monotonic() - start < 5, "Long-chapter local scan stays responsive")
print("Writing pack checks passed.")
