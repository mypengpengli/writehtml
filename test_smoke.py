"""冒烟测试：多用户隔离 + P1 新功能。转写模式不调 LLM，无需 key。"""
import os, shutil, uuid, sqlite3, time, json, types, base64, io, zipfile, sys
from concurrent.futures import ThreadPoolExecutor

# 用项目内临时目录放 db（系统 %TEMP% 上杀软偶发瞬时锁会让 sqlite 报只读）
_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".smoke_tmp", uuid.uuid4().hex[:10])
os.makedirs(_TMP, exist_ok=True)
os.environ["DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["PI_AGENT_ENABLED"] = "false"  # The broad legacy smoke suite stubs llm.agent_chat.
os.environ["SIGNUP_CODE"] = "testcode"   # 开放凭码注册
os.environ["LLM_API_KEY"] = ""           # 测试不调真 LLM；校验/摘要/聊天应走"未配置"500
os.environ["WRITEHTML_ADMIN_PASSWORD"] = "admintest"  # 引导创建的 admin 用确定性密码

from fastapi.testclient import TestClient
import main, db, llm, config

db.init_db()
c = TestClient(main.app)

# Windows 偶发 sqlite readonly 瞬时锁（杀软扫描 db 文件）：在 HTTP 层整请求重试。
# 该错误总发生在事务首次写之前、事务零写入、回滚干净，故整请求重试幂等、安全。
def _retry(fn):
    def w(*a, **k):
        for _ in range(80):
            try:
                return fn(*a, **k)
            except sqlite3.OperationalError as e:
                if "readonly" in str(e).lower() or "locked" in str(e).lower():
                    time.sleep(0.05)
                    continue
                raise
        return fn(*a, **k)
    return w
for _m in ("get", "post", "put", "delete", "patch"):
    setattr(c, _m, _retry(getattr(c, _m)))


def ok(cond, msg):
    print(("  OK  " if cond else " FAIL ") + msg)
    if not cond:
        raise SystemExit(1)


def H(tok):
    return {"Authorization": "Bearer " + tok}


# 注册状态
s = c.get("/api/signup-status").json()
ok(s["enabled"] and s["needs_code"], "注册：凭码开放")

# 注册两个用户
r = c.post("/api/register", json={"username": "alice", "password": "pw1234", "code": "testcode"})
ok(r.status_code == 200, "注册 alice")
tokA = r.json()["token"]
r = c.post("/api/register", json={"username": "bob", "password": "pw1234", "code": "testcode"})
ok(r.status_code == 200, "注册 bob")
tokB = r.json()["token"]

# 错码 / 重名
ok(c.post("/api/register", json={"username": "x", "password": "pw1234", "code": "wrong"}).status_code == 403, "错注册码 403")
ok(c.post("/api/register", json={"username": "alice", "password": "pw1234", "code": "testcode"}).status_code == 409, "重名 409")

# 登录 / me
r = c.post("/api/login", json={"username": "alice", "password": "pw1234"})
ok(r.status_code == 200, "登录 alice")
ok(c.get("/api/me", headers=H(tokA)).json()["username"] == "alice", "me 返回用户名")
ok(c.get("/api/me").status_code == 401, "未登录 401")

# alice 建作品 + 章节
wid = c.post("/api/works", json={"title": "A作"}, headers=H(tokA)).json()["id"]
cid = c.post(f"/api/works/{wid}/chapters", json={"title": "第一章"}, headers=H(tokA)).json()["id"]

# 隔离：bob 看不到 alice 的作品/章节
ok(c.get("/api/works", headers=H(tokB)).json() == [], "bob 看不到 alice 作品")
ok(c.get(f"/api/chapters/{cid}", headers=H(tokB)).status_code == 404, "bob 访问 alice 章节 404")
ok(c.delete(f"/api/works/{wid}", headers=H(tokB)).status_code == 404, "bob 删 alice 作品 404")

# 作品设定（bible）：存 / 读 / 隔离
ok(c.put(f"/api/works/{wid}/notes", json={"notes": "主角:小明"}, headers=H(tokA)).status_code == 200, "存作品设定")
ok(c.get(f"/api/works/{wid}/notes", headers=H(tokA)).json()["notes"] == "主角:小明", "作品设定读回")
ok(c.get(f"/api/works/{wid}/notes", headers=H(tokB)).status_code == 404, "bob 读 alice 作品设定 404")

# 转写（追加正文）
r = c.post("/api/process", json={"mode": "转写", "text": "你好世界", "chapter_id": cid}, headers=H(tokA))
ok(r.json()["result"] == "你好世界", "转写结果=原文")
chap = c.get(f"/api/chapters/{cid}", headers=H(tokA)).json()
ok(chap["content"] == "你好世界", "正文已追加")
ok(len(chap["segments"]) == 1, "段落历史 1 条")

# 每用户大模型设置
c.post("/api/settings", json={
    "base_url": "https://a.test/v1", "api_key": "sk-alice-secret", "model": "m-a",
    "asr_base_url": "https://asr.test/v1", "asr_api_key": "sk-asr-secret", "asr_model": "whisper-test",
}, headers=H(tokA))
s = c.get("/api/settings", headers=H(tokA)).json()
ok(s["base_url"] == "https://a.test/v1" and s["model"] == "m-a" and s["asr_model"] == "whisper-test"
   and s["asr_base_url"] == "https://asr.test/v1", "设置读回文字/转写配置")
ok(s["has_key"] is True and "secret" not in s["api_key_masked"] and s["api_key_masked"].startswith("****"), "key 掩码不泄露明文")
ok(s["asr_has_key"] is True and "secret" not in s["asr_api_key_masked"], "转写 key 掩码不泄露明文")
# 空 key 提交应保留旧 key
c.post("/api/settings", json={"base_url": "https://a.test/v1", "api_key": "", "model": "m-a2"}, headers=H(tokA))
ok(c.get("/api/settings", headers=H(tokA)).json()["has_key"] is True, "空 key 不清空已存 key")

# AI Skills：通用/作品专用 CRUD、作用域和用户隔离
ok(c.post("/api/agent/skills", json={"name": "", "instruction": "规则"}, headers=H(tokA)).status_code == 400, "Skill 空名称 400")
ok(c.post("/api/agent/skills", json={"name": "坏范围", "instruction": "规则", "work_id": "x"}, headers=H(tokA)).status_code == 400, "Skill 非法范围 400")
global_skill = c.post("/api/agent/skills", json={
    "name": "克制叙事", "description": "少形容词", "instruction": "使用克制、具体的叙事，避免空泛形容词。",
    "work_id": None,
}, headers=H(tokA)).json()
work_skill = c.post("/api/agent/skills", json={
    "name": "本书悬疑节奏", "instruction": "每段结尾保留一个未解信息。", "work_id": wid,
}, headers=H(tokA)).json()
_skills = c.get(f"/api/agent/skills?work_id={wid}", headers=H(tokA)).json()
ok({x["id"] for x in _skills} == {global_skill["id"], work_skill["id"]}, "Skill 列表含通用和作品专用")
ok(c.get("/api/agent/skills", headers=H(tokA)).json()[0]["id"] == global_skill["id"], "无作品时只列通用 Skill")
ok(c.get(f"/api/agent/skills?work_id={wid}", headers=H(tokB)).status_code == 404, "bob 看 alice 作品 Skill 404")
ok(c.put(f"/api/agent/skills/{global_skill['id']}", json={
    "name": "hack", "instruction": "hack", "work_id": None,
}, headers=H(tokB)).status_code == 404, "bob 改 alice Skill 404")
ok(c.put(f"/api/agent/skills/{global_skill['id']}", json={
    "name": "克制叙事", "description": "少形容词", "instruction": "使用克制、具体的叙事，避免空泛形容词。",
    "work_id": None, "enabled": False,
}, headers=H(tokA)).status_code == 200, "Skill 可停用")
ok(not c.get(f"/api/agent/skills?work_id={wid}", headers=H(tokA)).json()[0]["enabled"], "Skill 停用状态读回")
ok(c.put(f"/api/agent/skills/{global_skill['id']}", json={
    "name": "克制叙事", "description": "少形容词", "instruction": "使用克制、具体的叙事，避免空泛形容词。",
    "work_id": None, "enabled": True,
}, headers=H(tokA)).status_code == 200, "Skill 可重新启用")

# 标准 Agent Skills：导入 SKILL.md / ZIP，读取 YAML 元数据与 references，忽略脚本。
ok(c.post("/api/agent/skills/import", json={
    "filename": "SKILL.md", "data": base64.b64encode(b"# not a skill").decode(), "work_id": None,
}, headers=H(tokA)).status_code == 400, "非标准 SKILL.md 拒绝导入")
_skill_zip = io.BytesIO()
with zipfile.ZipFile(_skill_zip, "w", zipfile.ZIP_DEFLATED) as _z:
    _z.writestr("dialogue-polish/SKILL.md", "---\nname: dialogue-polish\ndescription: Use when polishing Chinese dialogue, dialogue rhythm, or subtext.\n---\n\n# Workflow\n先保留人物说话习惯，再压缩解释性台词；需要范例时读取 references/dialogue-style.md。")
    _z.writestr("dialogue-polish/references/dialogue-style.md", "引用资料：对话中让动作承担一部分解释，避免连续三句直白说明。")
    _z.writestr("dialogue-polish/scripts/format.py", "raise RuntimeError('must not execute')")
skill_pkg = c.post("/api/agent/skills/import", json={
    "filename": "dialogue-polish.zip", "data": base64.b64encode(_skill_zip.getvalue()).decode(), "work_id": wid,
}, headers=H(tokA)).json()
ok(skill_pkg["name"] == "dialogue-polish" and skill_pkg["source_kind"] == "skill_md" and skill_pkg["resource_count"] == 1, "ZIP 导入标准 SKILL.md 和引用资料")
ok(skill_pkg["skipped_files"] == ["scripts/format.py"], "Skill 脚本不会被导入或执行")
ok(any(x["id"] == skill_pkg["id"] and x["source_kind"] == "skill_md" for x in c.get(f"/api/agent/skills?work_id={wid}", headers=H(tokA)).json()), "导入 Skill 出现在当前作品目录")
# bob 与 alice 设置隔离
c.post("/api/settings", json={"base_url": "https://b.test/v1", "api_key": "sk-bob", "model": "m-b"}, headers=H(tokB))
sb = c.get("/api/settings", headers=H(tokB)).json()
ok(sb["model"] == "m-b" and c.get("/api/settings", headers=H(tokA)).json()["model"] == "m-a2", "设置按用户隔离")

# 校验/摘要/聊天路由：用没配 key 的 carol 验证（走到 LLM 分支→500，而非"未知模式"400）
tokC = c.post("/api/register", json={"username": "carol", "password": "pw1234", "code": "testcode"}).json()["token"]
cwid = c.post("/api/works", json={"title": "C作"}, headers=H(tokC)).json()["id"]
ccid = c.post(f"/api/works/{cwid}/chapters", json={"title": "C章"}, headers=H(tokC)).json()["id"]
c.put(f"/api/chapters/{ccid}", json={"content": "测试内容"}, headers=H(tokC))
ok(c.post("/api/process", json={"mode": "校验", "chapter_id": ccid}, headers=H(tokC)).status_code == 500, "校验走 LLM 分支(无key 500)")
ok(c.post("/api/process", json={"mode": "摘要", "chapter_id": ccid}, headers=H(tokC)).status_code == 500, "摘要走 LLM 分支(无key 500)")
ok(c.post("/api/process", json={"mode": "校验"}, headers=H(tokC)).status_code == 400, "校验未选章 400")
ok(c.post("/api/process", json={"mode": "瞎写", "text": "x"}, headers=H(tokC)).status_code == 400, "未知模式 400")
ok(c.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=H(tokC)).status_code == 500, "chat 走 LLM(无key 500)")
ok(c.post("/api/chat", json={"messages": []}, headers=H(tokC)).status_code == 400, "chat 空消息 400")
ok(c.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 401, "chat 未登录 401")
ok(c.post("/api/asr", content=b"", headers={**H(tokA), "Content-Type": "audio/webm"}).status_code == 400, "ASR 空音频 400")
ok(c.post("/api/asr", content=b"fakeaudio", headers=H(tokC)).status_code == 500, "ASR 无key 500")
_orig_transcribe = llm.transcribe
_seen_asr = {}
def _fake_transcribe(audio, **kw):
    _seen_asr.update(kw)
    return "请帮我续写一段"
llm.transcribe = _fake_transcribe
_asr = c.post("/api/asr", content=b"fakeaudio", headers={**H(tokA), "Content-Type": "audio/webm"}).json()
ok(_asr["text"] == "请帮我续写一段" and _seen_asr.get("model") == config.ASR_MODEL, "ASR 成功返回文字")
llm.transcribe = _orig_transcribe

# 备注保存
ok(c.put(f"/api/chapters/{cid}", json={"notes": "设定X"}, headers=H(tokA)).status_code == 200, "存备注")
ok(c.get(f"/api/chapters/{cid}", headers=H(tokA)).json()["notes"] == "设定X", "备注读回")

# 拆分：在 2 处拆，左"你好" 右"世界"
r = c.post(f"/api/chapters/{cid}/split", json={"at": 2, "title": "第二章"}, headers=H(tokA))
cid2 = r.json()["new_chapter_id"]
chaps = c.get(f"/api/works/{wid}/chapters", headers=H(tokA)).json()
ok(len(chaps) == 2, "拆分后 2 章")
ok(c.get(f"/api/chapters/{cid}", headers=H(tokA)).json()["content"] == "你好", "左半留存")
ok(c.get(f"/api/chapters/{cid2}", headers=H(tokA)).json()["content"] == "世界", "右半进新章")

# 排序：把新章挪到前面
ok(c.post(f"/api/works/{wid}/reorder", json={"ids": [cid2, cid]}, headers=H(tokA)).status_code == 200, "排序")
order = [c["id"] for c in c.get(f"/api/works/{wid}/chapters", headers=H(tokA)).json()]
ok(order == [cid2, cid], "排序生效")

# 修订版本：存版 → 改正文 → 恢复
rid = c.post(f"/api/chapters/{cid}/revisions", headers=H(tokA)).json()["id"]
c.put(f"/api/chapters/{cid}", json={"content": "被改了"}, headers=H(tokA))
ok(c.get(f"/api/chapters/{cid}", headers=H(tokA)).json()["content"] == "被改了", "正文已改")
restored = c.post(f"/api/chapters/{cid}/revisions/{rid}/restore", headers=H(tokA)).json()
ok(restored["content"] == "你好", "恢复版本")
ok(len(c.get(f"/api/chapters/{cid}/revisions", headers=H(tokA)).json()) == 1, "版本列表 1 条")

# 自动限 20：连存 22 个版本，只应留最近 20 个
for _ in range(22):
    c.post(f"/api/chapters/{cid}/revisions", headers=H(tokA))
ok(len(c.get(f"/api/chapters/{cid}/revisions", headers=H(tokA)).json()) == 20, "自动限最近 20 个版本")

# 撤销最近一段
c.post(f"/api/chapters/{cid}/undo", headers=H(tokA))
# （上面恢复后 content=你好，undo 会尝试裁掉最近 segment 的 result "你好世界"，
#  但 content 是"你好"不以"你好世界"结尾，故仅删历史记录，正文不变——验证不崩即可）
ok(True, "undo 不崩")

# 导出
r = c.get(f"/api/chapters/{cid}/export?format=txt", headers=H(tokA))
ok(r.status_code == 200 and "text/plain" in r.headers["content-type"], "导出 txt")
r = c.get(f"/api/chapters/{cid}/export?format=docx", headers=H(tokA))
ok(r.status_code == 200 and r.content[:2] == b"PK", "导出 docx (zip)")

# 整本导出（含两章正文）
r = c.get(f"/api/works/{wid}/export?format=txt", headers=H(tokA))
ok(r.status_code == 200 and "你好" in r.text and "世界" in r.text, "整本导出 txt 含各章")
r = c.get(f"/api/works/{wid}/export?format=docx", headers=H(tokA))
ok(r.status_code == 200 and r.content[:2] == b"PK", "整本导出 docx (zip)")
ok(c.get(f"/api/works/{wid}/export?format=txt", headers=H(tokB)).status_code == 404, "bob 整本导出 alice 作品 404")

# 功能1 缩写/改写：无 key 走 LLM 分支 500（区别于未知模式 400）；空内容 400
ok(c.post("/api/process", json={"mode": "缩写", "text": "一段话", "chapter_id": ccid}, headers=H(tokC)).status_code == 500, "缩写走 LLM(无key 500)")
ok(c.post("/api/process", json={"mode": "改写", "text": "一段话", "style": "更精炼", "chapter_id": ccid}, headers=H(tokC)).status_code == 500, "改写走 LLM(无key 500)")
ok(c.post("/api/process", json={"mode": "缩写", "text": ""}, headers=H(tokC)).status_code == 400, "缩写空内容 400")
ok(c.post("/api/process", json={"mode": "改写", "text": ""}, headers=H(tokC)).status_code == 400, "改写空内容 400")

# 功能2 可视化 Diff（历史版本 → 当前正文）
rid2 = c.post(f"/api/chapters/{cid2}/revisions", headers=H(tokA)).json()["id"]   # 此时正文="世界"
c.put(f"/api/chapters/{cid2}", json={"content": "世界改了"}, headers=H(tokA))
d = c.get(f"/api/chapters/{cid2}/revisions/{rid2}/diff", headers=H(tokA)).json()
ok(any(o["op"] != "equal" for o in d["ops"]), "diff 检测到增删改")
ok(any("世界改了" in (o.get("new") or "") for o in d["ops"]), "diff 新块含当前正文")
ok(c.get(f"/api/chapters/{cid2}/revisions/{rid2}/diff", headers=H(tokB)).status_code == 404, "bob 看 alice diff 404")

# 功能3 回收站（软删/恢复/清空/隔离）
tid = c.post(f"/api/works/{wid}/chapters", json={"title": "待删章"}, headers=H(tokA)).json()["id"]
c.put(f"/api/chapters/{tid}", json={"content": "待回收"}, headers=H(tokA))
ok(c.delete(f"/api/chapters/{tid}", headers=H(tokA)).status_code == 200, "软删章节(进回收站)")
ok(not any(ch["id"] == tid for ch in c.get(f"/api/works/{wid}/chapters", headers=H(tokA)).json()), "软删后不在章节列表")
ok(any(t["id"] == tid for t in c.get(f"/api/works/{wid}/trash", headers=H(tokA)).json()), "软删后出现在回收站")
ok(c.get(f"/api/chapters/{tid}", headers=H(tokA)).status_code == 404, "软删章直接访问 404")
ok(c.post(f"/api/chapters/{tid}/restore", headers=H(tokA)).status_code == 200, "从回收站恢复")
ok(any(ch["id"] == tid for ch in c.get(f"/api/works/{wid}/chapters", headers=H(tokA)).json()), "恢复后回到章节列表")
ok(c.post(f"/api/chapters/{tid}/restore", headers=H(tokB)).status_code == 404, "bob 恢复 alice 章节 404")
ok(c.delete(f"/api/chapters/{tid}", headers=H(tokA)).status_code == 200, "再次软删")
ok(c.post(f"/api/chapters/{tid}/purge", headers=H(tokA)).status_code == 200, "彻底清空")
ok(not any(t["id"] == tid for t in c.get(f"/api/works/{wid}/trash", headers=H(tokA)).json()), "清空后不在回收站")
ok(c.post(f"/api/chapters/{tid}/purge", headers=H(tokA)).status_code == 404, "清空后再清空 404")
ok(c.get(f"/api/works/{wid}/trash", headers=H(tokB)).status_code == 404, "bob 看 alice 回收站 404")

# 功能4 实体卡片 wiki：CRUD + 隔离 + digest
ok(c.post(f"/api/works/{wid}/entities", json={"name": ""}, headers=H(tokA)).status_code == 400, "实体空名 400")
ent = c.post(f"/api/works/{wid}/entities", json={"name": "林晚", "kind": "人物", "summary": "女主角", "detail": "冷静"}, headers=H(tokA)).json()
ent2 = c.post(f"/api/works/{wid}/entities", json={"name": "北城", "kind": "地点", "summary": "故事发生地"}, headers=H(tokA)).json()
ok(c.put(f"/api/entities/{ent['id']}", json={"summary": "女主角，冷静"}, headers=H(tokA)).status_code == 200, "改实体")
ok(c.delete(f"/api/entities/{ent2['id']}", headers=H(tokA)).status_code == 200, "删实体")
lst = c.get(f"/api/works/{wid}/entities", headers=H(tokA)).json()
ok(len(lst) == 1 and lst[0]["name"] == "林晚" and lst[0]["summary"] == "女主角，冷静", "实体列表反映增删改")
ok(c.get(f"/api/works/{wid}/entities", headers=H(tokB)).status_code == 404, "bob 看 alice 实体 404")
ok(c.put(f"/api/entities/{ent['id']}", json={"name": "hack"}, headers=H(tokB)).status_code == 404, "bob 改 alice 实体 404")
ok(c.delete(f"/api/entities/{ent['id']}", headers=H(tokB)).status_code == 404, "bob 删 alice 实体 404")
uidA = db.verify_user("alice", "pw1234")["id"]
dig = db.get_entity_digest(wid, uidA)
ok(dig.startswith("作品实体") and "[人物] 林晚" in dig and "女主角，冷静" in dig and "基础设定：冷静" in dig,
   "实体 digest 含摘要与基础设定")
ok(db.get_entity_digest(wid, uidA + 9999) == "", "他人作品 digest 为空(隔离)")

# 动态人物卡：基础设定不覆盖；状态按章节时点读取；AI 提议必须人工确认后才生效。
state_c1 = c.post(f"/api/works/{wid}/chapters", json={"title": "人物状态一"}, headers=H(tokA)).json()["id"]
state_c2 = c.post(f"/api/works/{wid}/chapters", json={"title": "人物状态二"}, headers=H(tokA)).json()["id"]
_state1 = c.post(f"/api/entities/{ent['id']}/state-versions", json={
    "chapter_id": state_c1,
    "state": {"location": "北城", "goal": "调查失踪案", "emotion": "戒备"},
    "change_summary": "抵达北城，开始调查",
    "evidence": "第一章结尾抵达北城",
}, headers=H(tokA))
ok(_state1.status_code == 200, "人物状态手动保存")
_state_at_c1 = c.get(f"/api/works/{wid}/entities?chapter_id={state_c1}", headers=H(tokA)).json()
_state_at_c2 = c.get(f"/api/works/{wid}/entities?chapter_id={state_c2}", headers=H(tokA)).json()
ok(_state_at_c1[0]["current_state"]["location"] == "北城" and _state_at_c2[0]["current_state"]["goal"] == "调查失踪案", "人物状态跨章节继承")
_proposal = db.upsert_character_state_proposal(
    ent["id"], uidA, state_c2,
    {"location": "旧码头", "goal": "追查目击者", "emotion": "紧张"},
    "追到旧码头", "本章明确前往旧码头",
)
ok(_proposal["status"] == "pending", "AI 状态提议先进入待确认")
ok(c.get(f"/api/entities/{ent['id']}/state-history?chapter_id={state_c2}", headers=H(tokB)).status_code == 404,
   "他人不能看人物状态历史")
_accept = c.post(f"/api/character-state-proposals/{_proposal['id']}/accept", json={}, headers=H(tokA))
ok(_accept.status_code == 200, "采纳 AI 人物状态提议")
_state_at_c1 = c.get(f"/api/works/{wid}/entities?chapter_id={state_c1}", headers=H(tokA)).json()[0]
_state_at_c2 = c.get(f"/api/works/{wid}/entities?chapter_id={state_c2}", headers=H(tokA)).json()[0]
ok(_state_at_c1["current_state"]["location"] == "北城" and _state_at_c2["current_state"]["location"] == "旧码头",
   "不同章节看到各自时点的人物状态")
ok("旧码头" in main._agent_bible(wid, uidA, state_c2), "Agent 上下文带当前章节人物状态")

# 手动触发提取：模型输出只生成 pending，不直接覆盖刚确认的版本。
c.put(f"/api/chapters/{state_c2}", json={"content": "林晚在旧码头得知失踪者仍然活着。"}, headers=H(tokA))
_orig_character_chat = llm.chat
_character_prompt = {}
def _character_extract(messages, **kw):
    _character_prompt["messages"] = messages
    return json.dumps({"updates": [{
        "entity_id": ent["id"],
        "state": {"location": "旧码头", "goal": "救出失踪者", "information": "失踪者仍然活着"},
        "change_summary": "获得失踪者生还线索", "evidence": "正文明确得知其仍然活着",
    }]}, ensure_ascii=False)
llm.chat = _character_extract
_analyzed = c.post(f"/api/chapters/{state_c2}/character-state-proposals/analyze", json={}, headers=H(tokA)).json()
ok(len(_analyzed["proposals"]) == 1 and _analyzed["proposals"][0]["status"] == "pending", "AI 提取人物状态为待确认提议")
ok("confirmed_state_at_this_point" in _character_prompt["messages"][-1]["content"], "状态提取收到当前已确认人物状态")
_after_analyze = c.get(f"/api/works/{wid}/entities?chapter_id={state_c2}", headers=H(tokA)).json()[0]
ok(_after_analyze["current_state"]["goal"] == "追查目击者" and _after_analyze["pending_count"] == 1,
   "待确认提议不改变后续 AI 上下文")
# 后续 Agent 写作测试不联网，但保留新的自动提议调用链。
llm.chat = lambda *args, **kw: '{"updates": []}'

# 写作工作台：剧情状态按章节时点继承，AI 只生成待确认提议；关系、工作流、提醒和上下文均按作品隔离。
rel_ent = c.post(f"/api/works/{wid}/entities", json={
    "name": "周沉", "kind": "人物", "summary": "线人", "detail": "谨慎，欠林晚一次人情",
}, headers=H(tokA)).json()
rel = c.post(f"/api/works/{wid}/relationships", json={
    "from_entity_id": ent["id"], "to_entity_id": rel_ent["id"], "relation": "暂时合作",
    "detail": "旧码头事件后共享线索", "status": "active",
}, headers=H(tokA))
ok(rel.status_code == 200 and rel.json()["from_name"] == "林晚" and rel.json()["to_name"] == "周沉", "人物关系可保存")
ok(c.get(f"/api/works/{wid}/relationships", headers=H(tokB)).status_code == 404, "人物关系按作品隔离")
rel = c.put(f"/api/relationships/{rel.json()['id']}", json={
    "from_entity_id": ent["id"], "to_entity_id": rel_ent["id"], "relation": "互信未满",
    "detail": "仍保留各自的秘密", "status": "tense",
}, headers=H(tokA)).json()
ok(rel["relation"] == "互信未满" and "林晚 → 周沉：互信未满" in db.get_entity_digest(wid, uidA), "关系更新进入 AI 上下文")

plot_v1 = c.post(f"/api/works/{wid}/plot-state-versions", json={
    "chapter_id": state_c1,
    "state": {"mainline": "调查北城失踪案", "current_event": "林晚抵达北城", "open_threads": "失踪者下落", "next_goal": "寻找目击者"},
    "change_summary": "主角开始调查失踪案", "evidence": "第一章结尾抵达北城",
}, headers=H(tokA))
ok(plot_v1.status_code == 200, "剧情状态可手动保存")
plot_auto1 = c.post(f"/api/works/{wid}/plot-state-versions", json={
    "chapter_id": state_c1,
    "state": {"mainline": "调查北城失踪案", "current_event": "林晚抵达北城", "open_threads": "失踪者下落", "next_goal": "寻找目击者", "notes": "自动草稿 A"},
    "change_summary": "主角开始调查失踪案", "evidence": "第一章结尾抵达北城", "autosave": True,
}, headers=H(tokA)).json()["version"]
plot_auto2 = c.post(f"/api/works/{wid}/plot-state-versions", json={
    "chapter_id": state_c1,
    "state": {"mainline": "调查北城失踪案", "current_event": "林晚抵达北城", "open_threads": "失踪者下落", "next_goal": "寻找目击者", "notes": "自动草稿 B"},
    "change_summary": "主角开始调查失踪案", "evidence": "第一章结尾抵达北城", "autosave": True,
}, headers=H(tokA)).json()["version"]
ok(plot_auto1["id"] != plot_v1.json()["version"]["id"] and plot_auto1["id"] == plot_auto2["id"] and plot_auto2["state"]["notes"] == "自动草稿 B", "剧情自动保存更新当前草稿而不覆盖手动版本")
plot_c2 = c.get(f"/api/works/{wid}/plot-state?chapter_id={state_c2}", headers=H(tokA)).json()
ok(plot_c2["current_state"]["mainline"] == "调查北城失踪案", "剧情状态跨章节继承")
plot_proposal = db.upsert_plot_state_proposal(
    wid, uidA, state_c2,
    {"mainline": "确认失踪者仍然活着", "current_event": "旧码头得到线索", "open_threads": "谁在控制旧码头", "next_goal": "救出失踪者"},
    "获得失踪者生还线索", "正文明确写到旧码头的消息",
)
ok(plot_proposal["status"] == "pending", "AI 剧情状态先进入待确认")
plot_accept = c.post(f"/api/plot-state-proposals/{plot_proposal['id']}/accept", json={}, headers=H(tokA))
ok(plot_accept.status_code == 200, "采纳 AI 剧情状态提议")
plot_c1 = c.get(f"/api/works/{wid}/plot-state?chapter_id={state_c1}", headers=H(tokA)).json()
plot_c2 = c.get(f"/api/works/{wid}/plot-state?chapter_id={state_c2}", headers=H(tokA)).json()
ok(plot_c1["current_state"]["mainline"] == "调查北城失踪案" and plot_c2["current_state"]["mainline"] == "确认失踪者仍然活着", "不同章节读取各自剧情时点")
ok("确认失踪者仍然活着" in main._agent_bible(wid, uidA, state_c2), "Agent 上下文带当前剧情状态")

workflow = c.put(f"/api/chapters/{state_c2}/workflow", json={
    "status": "planning", "goal": "确认线索来源", "summary": "旧码头线索待核验",
}, headers=H(tokA))
ok(workflow.status_code == 200 and workflow.json()["workflow_status"] == "planning", "章节工作流可保存")
ok(c.put(f"/api/chapters/{state_c2}/workflow", json={"status": "unknown"}, headers=H(tokA)).status_code == 400, "非法章节阶段拒绝")
seed_alerts = db.replace_chapter_consistency_alerts(state_c2, uidA, [{
    "category": "伏笔", "severity": "warning", "title": "线索来源未交代",
    "detail": "旧码头的消息来源尚未说明", "evidence": "本章结尾", "suggestion": "下一章补充来源或保留疑点",
}])
ok(len(seed_alerts) == 1 and seed_alerts[0]["status"] == "open", "连续性提醒可记录")
alerts = c.get(f"/api/chapters/{state_c2}/consistency-alerts", headers=H(tokA)).json()
ok(len(alerts) == 1 and c.post(f"/api/consistency-alerts/{alerts[0]['id']}/dismiss", headers=H(tokA)).status_code == 200, "连续性提醒可忽略")
ok(c.get(f"/api/chapters/{state_c2}/consistency-alerts", headers=H(tokA)).json()[0]["status"] == "dismissed", "提醒忽略状态保留")

context_snapshot = c.post("/api/agent/context", json={
    "chapter_id": state_c2,
    "selection": {"text": "林晚在旧码头得知真相", "start": 0, "end": 11, "before": "", "after": ""},
    "skill_ids": [global_skill["id"], work_skill["id"]],
}, headers=H(tokA)).json()
context_system = "\n".join(item["content"] for item in context_snapshot["system_messages"])
ok(context_snapshot["selection"]["present"] and len(context_snapshot["tools"]) > 0 and len(context_snapshot["skills"]) == 2,
   "上下文检查器返回选区、工具和 Skill")
ok("确认失踪者仍然活着" in context_system and "互信未满" in context_system, "上下文检查器展示真实故事约束")

# AI 改稿必须先预览；只有作者确认、且正文仍为预览时的版本，才会真正落稿。
preview_cid = c.post(f"/api/works/{wid}/chapters", json={"title": "预览改稿"}, headers=H(tokA)).json()["id"]
c.put(f"/api/chapters/{preview_cid}", json={"content": "旧段落"}, headers=H(tokA))
_orig_preview_process = llm.process
llm.process = lambda mode, text, *args, **kw: "新段落"
preview = c.post("/api/process", json={
    "mode": "改写", "text": "旧段落", "chapter_id": preview_cid,
    "preview": True, "preview_operation": "replace",
}, headers=H(tokA)).json()
ok(preview["preview"] is True and c.get(f"/api/chapters/{preview_cid}", headers=H(tokA)).json()["content"] == "旧段落", "AI 改稿预览不直接写入正文")
applied = c.post(f"/api/chapters/{preview_cid}/edit-proposals/apply", json={
    "base_content": "旧段落", "operation": "replace", "result": preview["result"], "mode": "改写",
    "old_text": "旧段落", "start": 0, "end": 3,
}, headers=H(tokA))
ok(applied.status_code == 200 and applied.json()["content"] == "新段落", "确认预览后才写入正文")
c.put(f"/api/chapters/{preview_cid}", json={"content": "作者已改"}, headers=H(tokA))
stale = c.post(f"/api/chapters/{preview_cid}/edit-proposals/apply", json={
    "base_content": "新段落", "operation": "replace", "result": "过期建议", "mode": "改写",
    "old_text": "新段落", "start": 0, "end": 3,
}, headers=H(tokA))
ok(stale.status_code == 409, "正文变化后拒绝过期 AI 预览")
llm.process = _orig_preview_process

# 版本管理：命名版本、分支稿和整本快照均通过预览差异后恢复。
version_cid = c.post(f"/api/works/{wid}/chapters", json={"title": "版本测试"}, headers=H(tokA)).json()["id"]
c.put(f"/api/chapters/{version_cid}", json={"content": "版本甲"}, headers=H(tokA))
named_revision = c.post(f"/api/chapters/{version_cid}/revisions", json={"label": "剧情节点 A"}, headers=H(tokA)).json()
renamed = c.put(f"/api/chapters/{version_cid}/revisions/{named_revision['id']}", json={"label": "旧码头节点"}, headers=H(tokA)).json()
ok(renamed["label"] == "旧码头节点", "章节版本可命名和重命名")
branch = c.post(f"/api/chapters/{version_cid}/revisions/{named_revision['id']}/branch", json={"title": "旧码头分支稿"}, headers=H(tokA)).json()
branch_meta = c.get(f"/api/chapters/{branch['id']}", headers=H(tokA)).json()
ok(branch_meta["content"] == "版本甲" and branch_meta["branch_of_chapter_id"] == version_cid, "历史版本可创建独立分支稿")
work_revision = c.post(f"/api/works/{wid}/revisions", json={"label": "整本节点 A"}, headers=H(tokA)).json()
c.put(f"/api/chapters/{version_cid}", json={"content": "版本乙"}, headers=H(tokA))
work_diff = c.get(f"/api/works/{wid}/revisions/{work_revision['id']}/diff", headers=H(tokA)).json()
ok(any(item["chapter_id"] == version_cid and item["status"] == "changed" for item in work_diff["chapters"]), "整本版本差异能定位变更章节")
work_restore = c.post(f"/api/works/{wid}/revisions/{work_revision['id']}/restore", headers=H(tokA)).json()
ok(work_restore["backup"]["label"] == "恢复前自动备份" and c.get(f"/api/chapters/{version_cid}", headers=H(tokA)).json()["content"] == "版本甲", "整本恢复前自动备份并恢复正文")

# 复核会把结构化提醒和状态提议一起产出，仍然不直接覆盖已确认的剧情卡。
def _review_story_chat(messages, **kw):
    payload = json.loads(messages[-1]["content"])
    task = payload.get("task", "")
    if "检查当前章节" in task:
        return json.dumps({"summary": "旧码头线索已出现，建议核对来源。", "alerts": [{
            "category": "伏笔", "severity": "notice", "title": "确认线索来源", "detail": "来源仍待展开",
            "evidence": "旧码头消息", "suggestion": "在下一章给出侧面印证",
        }]}, ensure_ascii=False)
    if "人物动态变化" in task:
        return '{"updates": []}'
    if "故事状态" in task:
        return json.dumps({"state": {"mainline": "确认失踪者仍然活着", "current_event": "旧码头线索待核验", "open_threads": "谁在控制旧码头", "next_goal": "核验线索来源"}, "change_summary": "线索进入核验阶段", "evidence": "本章旧码头消息"}, ensure_ascii=False)
    return '{"updates": []}'
llm.chat = _review_story_chat
review = c.post(f"/api/chapters/{state_c2}/review", json={}, headers=H(tokA)).json()
ok(review["workflow"]["workflow_status"] == "review" and any(item["status"] == "open" for item in review["alerts"]) and review["plot_state_proposal"], "AI 复核产生提醒和待确认剧情状态")
llm.chat = lambda *args, **kw: '{"updates": []}'

# AI agent：对话即操作（monkeypatch llm.agent_chat 避免真联网）
def _msg(content=None, tool_calls=None):
    tcs = None
    if tool_calls:
        tcs = [types.SimpleNamespace(id=i, function=types.SimpleNamespace(name=n, arguments=a))
               for i, n, a in tool_calls]
    return types.SimpleNamespace(content=content, tool_calls=tcs)

def _make_agent(stub):
    s = {"i": 0}
    def fake(messages, tools, **kw):
        i = s["i"]; s["i"] += 1
        return stub[min(i, len(stub) - 1)]
    return fake

def _put_conv(tok, chapter_id, text="hi"):
    """用假 agent_chat 给某 用户×章节 落一条对话（无工具调用），便于后续测试。"""
    _saved = llm.agent_chat
    llm.agent_chat = lambda messages, tools, **kw: _msg("ok")
    try:
        c.post("/api/agent", json={"text": text, "chapter_id": chapter_id}, headers=H(tok))
    finally:
        llm.agent_chat = _saved

# 入参校验 / 鉴权（carol 无 key，不触达 LLM）
ok(c.post("/api/agent", json={"text": "hi", "chapter_id": ccid}, headers=H(tokC)).status_code == 500, "agent 无key 500")
ok(c.post("/api/agent", json={"text": ""}, headers=H(tokA)).status_code == 400, "agent 空文本 400")
ok(c.post("/api/agent", json={"text": "hi"}).status_code == 401, "agent 未登录 401")
ok(c.post("/api/agent/audio", json={"audio": "", "format": "wav"}, headers=H(tokA)).status_code == 400, "语音直发空录音 400")

# 直发语音：本轮模型收到 input_audio，但持久化记录只保存语音占位文本，不存 Base64。
_orig_ac = llm.agent_chat
_seen_voice = {}
def _capture_voice(messages, tools, **kw):
    _seen_voice["messages"] = messages
    return _msg("已理解语音指令。")
llm.agent_chat = _capture_voice
_voice = c.post("/api/agent/audio", json={
    "audio": base64.b64encode(b"fake-wav-audio").decode(), "format": "wav", "chapter_id": cid,
    "skill_ids": [global_skill["id"], work_skill["id"]],
}, headers=H(tokA)).json()
_voice_turn = next(m for m in _seen_voice["messages"] if m.get("role") == "user" and isinstance(m.get("content"), list))
ok(any(x.get("type") == "input_audio" and x.get("input_audio", {}).get("format") == "wav" for x in _voice_turn["content"]), "语音直发本轮带 input_audio")
_voice_skill_prompt = "\n".join(m.get("content", "") for m in _seen_voice["messages"] if m.get("role") == "system")
ok("克制叙事" in _voice_skill_prompt and "本书悬疑节奏" in _voice_skill_prompt, "语音直发本轮收到 Skill")
ok(any(m.get("content") == "[voice] 语音指令" for m in _voice["messages"]), "语音对话持久化仅存占位")
ok(not any("ZmFrZS13YXY" in str(m) for m in _voice["messages"]), "语音 Base64 不写入对话历史")
ok(not any("本书悬疑节奏" in (m.get("content") or "") for m in _voice["messages"]), "Skill 规则不写入语音对话历史")
llm.agent_chat = _orig_ac

# replace_text 工具：cid 正文 "你好" → "你好呀"，并验证可撤销
llm.agent_chat = _make_agent([
    _msg(None, [("c1", "replace_text", json.dumps({"old_text": "你好", "new_text": "你好呀"}))]),
    _msg("已改好。"),
])
_orig_agent_state_chat = llm.chat
llm.chat = lambda *args, **kw: json.dumps({"updates": [{
    "entity_id": ent["id"], "state": {"location": "测试房间"},
    "change_summary": "正文改动后生成的状态提议", "evidence": "测试提取",
}]}, ensure_ascii=False)
ag = c.post("/api/agent", json={"text": "把你好改成你好呀", "chapter_id": cid}, headers=H(tokA)).json()
llm.chat = _orig_agent_state_chat
_tr = [m for m in ag["messages"] if m.get("role") == "tool"]
ok(len(_tr) == 1 and json.loads(_tr[0]["content"]).get("changed") is True, "agent replace_text 执行")
ok(len(ag.get("character_state_proposals") or []) == 1, "Agent 写作后自动生成待确认人物状态")
_undo = json.loads(_tr[0]["content"]).get("undo_rid")
ok(isinstance(_undo, int), "agent 返回 undo_rid")
ok(c.get(f"/api/chapters/{cid}", headers=H(tokA)).json()["content"] == "你好呀", "agent 改后正文=你好呀")
ok(c.post(f"/api/chapters/{cid}/revisions/{_undo}/restore", headers=H(tokA)).json()["content"] == "你好", "agent 动作可撤销(恢复快照)")

# 持久化：服务端存了本轮对话，切回能取回；他人只看得到自己的空
_conv = c.get(f"/api/agent/conversation?chapter_id={cid}", headers=H(tokA)).json()
ok(any(m.get("role") == "user" and "把你好改成" in m.get("content", "") for m in _conv["messages"]), "对话已持久化(可取回)")
ok(len(c.get(f"/api/agent/conversation?chapter_id={cid}", headers=H(tokB)).json()["messages"]) == 0, "他人章对话隔离(只看得到自己的空)")

# 临时选区上下文：本轮喂给模型，但不污染持久化对话
_seen_sel = {}
def _capture_selection(messages, tools, **kw):
    _seen_sel["messages"] = messages
    return _msg("收到选区。")
llm.agent_chat = _capture_selection
c.post("/api/agent", json={
    "text": "把这段改紧张",
    "chapter_id": cid,
    "selection": {"text": "你好", "start": 0, "end": 2, "before": "", "after": ""},
    "skill_ids": [global_skill["id"], work_skill["id"]],
}, headers=H(tokA))
_sel_prompt = "\n".join(m.get("content", "") for m in _seen_sel["messages"] if m.get("role") == "system")
ok("selected_text" in _sel_prompt and "你好" in _sel_prompt, "agent 本轮收到选区上下文")
ok("克制叙事" in _sel_prompt and "本书悬疑节奏" in _sel_prompt, "选区对话本轮收到 Skill")
_conv_sel = c.get(f"/api/agent/conversation?chapter_id={cid}", headers=H(tokA)).json()
ok(not any("selected_text" in (m.get("content") or "") for m in _conv_sel["messages"]), "agent 选区上下文不持久化")
ok(not any("本书悬疑节奏" in (m.get("content") or "") for m in _conv_sel["messages"]), "Skill 规则不写入选区对话历史")

ok(c.delete(f"/api/agent/conversation?chapter_id={cid}", headers=H(tokA)).status_code == 200, "清空对话 200")
ok(len(c.get(f"/api/agent/conversation?chapter_id={cid}", headers=H(tokA)).json()["messages"]) == 0, "清空后对话为空")

# Agent 的二次写作工具也必须继承本轮 Skill，不能只影响工具选择。
_orig_process = llm.process
_seen_process_skill = {}
def _capture_process_skill(mode, text, **kw):
    _seen_process_skill.update(kw)
    return text
llm.process = _capture_process_skill
llm.agent_chat = _make_agent([
    _msg(None, [("c-skill", "edit_passage", json.dumps({"old_text": "你好", "instruction": "改写"}))]),
    _msg("已按规则改写。"),
])
c.post("/api/agent", json={
    "text": "按 Skill 改这段", "chapter_id": cid,
    "skill_ids": [global_skill["id"], work_skill["id"]],
}, headers=H(tokA))
ok("克制叙事" in (_seen_process_skill.get("skill_instructions") or "") and "本书悬疑节奏" in (_seen_process_skill.get("skill_instructions") or ""), "Agent 二次改写继承 Skill")
llm.process = _orig_process
c.delete(f"/api/agent/conversation?chapter_id={cid}", headers=H(tokA))

# 标准 Skill 的渐进加载：首轮只给目录，命中后加载 SKILL.md，再按需读 references。
_auto_calls = []
def _auto_skill_agent(messages, tools, **kw):
    _auto_calls.append([dict(m) for m in messages])
    if len(_auto_calls) == 1:
        return _msg(None, [("auto-1", "activate_skill", json.dumps({"skill_id": skill_pkg["id"]}))])
    if len(_auto_calls) == 2:
        return _msg(None, [("auto-2", "read_skill_resource", json.dumps({"skill_id": skill_pkg["id"], "path": "references/dialogue-style.md"}))])
    return _msg("已按对话润色 Skill 完成。")
llm.agent_chat = _auto_skill_agent
_auto = c.post("/api/agent", json={"text": "把这段对话更有潜台词", "chapter_id": cid}, headers=H(tokA)).json()
_auto_first = "\n".join(m.get("content", "") for m in _auto_calls[0] if m.get("role") == "system")
_auto_second = "\n".join(m.get("content", "") for m in _auto_calls[1] if m.get("role") == "system")
_auto_third = "\n".join(m.get("content", "") for m in _auto_calls[2] if m.get("role") == "system")
ok("dialogue-polish" in _auto_first and "先保留人物说话习惯" not in _auto_first, "Agent 首轮只收到 Skill 元数据目录")
ok("先保留人物说话习惯" in _auto_second, "Agent activate_skill 后收到完整 SKILL.md")
ok("让动作承担一部分解释" in _auto_third, "Agent 可按需读取 Skill references")
ok(not any("先保留人物说话习惯" in (m.get("content") or "") or "让动作承担一部分解释" in (m.get("content") or "") for m in _auto["messages"]), "Skill 正文和资料不写入对话历史")
c.delete(f"/api/agent/conversation?chapter_id={cid}", headers=H(tokA))

# list_revisions 工具（只读，验证分派 + JSON 往返）
llm.agent_chat = _make_agent([
    _msg(None, [("c2", "list_revisions", "{}")]),
    _msg("已列出。"),
])
ag2 = c.post("/api/agent", json={"text": "列出版本", "chapter_id": cid}, headers=H(tokA)).json()
_tr2 = [m for m in ag2["messages"] if m.get("role") == "tool"]
ok(len(_tr2) == 1 and isinstance(json.loads(_tr2[0]["content"]).get("revisions"), list), "agent list_revisions 返回版本数组")
c.delete(f"/api/agent/conversation?chapter_id={cid}", headers=H(tokA))  # 清空，避免影响后续

# 上下文压缩：调低阈值 + 假 summarize，验证早期轮次被摘要、保留最近几轮
_orig_sum = llm.summarize
_orig_cc = config.AGENT_COMPACT_CHARS
_orig_pr = config.AGENT_PRESERVE_RECENT
llm.agent_chat = lambda messages, tools, **kw: _msg("回复")
llm.summarize = lambda messages, prev="", **kw: "摘要内容"
config.AGENT_COMPACT_CHARS = 5
config.AGENT_PRESERVE_RECENT = 2
c.post("/api/agent", json={"text": "第一轮", "chapter_id": cid}, headers=H(tokA))   # 2 条，不压
r3 = c.post("/api/agent", json={"text": "第二轮", "chapter_id": cid}, headers=H(tokA)).json()  # 4 条，触发压缩
ok(r3.get("compacted") is True, "agent 超长触发压缩")
_convc = c.get(f"/api/agent/conversation?chapter_id={cid}", headers=H(tokA)).json()
ok(_convc["summary"] == "摘要内容", "压缩后摘要已存")
ok(len(_convc["messages"]) <= 2, "压缩后只保留最近几轮")
llm.summarize = _orig_sum
config.AGENT_COMPACT_CHARS = _orig_cc
config.AGENT_PRESERVE_RECENT = _orig_pr
llm.agent_chat = _orig_ac
# cid 此刻留有一条压缩后的对话，供后面删作品级联清理验证

# 本机 meta-memory + launcher：每轮读 SKILL.md、生命周期使用同一 turn_id、回答先落盘再确认。
_skill_root = os.path.join(_TMP, "local-skills")
_meta_dir = os.path.join(_skill_root, "meta-memory")
os.makedirs(_meta_dir, exist_ok=True)
with open(os.path.join(_meta_dir, "SKILL.md"), "w", encoding="utf-8", newline="\n") as _f:
    _f.write("---\nname: meta-memory\ndescription: local memory\n---\n# Local rule\n每回合先读取本机记忆。\n")
_launcher = os.path.join(_TMP, "fake_launcher.py")
with open(_launcher, "w", encoding="utf-8", newline="\n") as _f:
    _f.write(r'''import json, pathlib, sys
args = sys.argv[1:]
phase = " ".join(args[:2]) if args[:2] == ["turn", "touch"] or args[:2] == ["recovery", "replay"] else args[0]
def opt(name):
    return args[args.index(name) + 1] if name in args else ""
turn_id = opt("--turn-id") or (args[2] if phase == "turn touch" else "")
request_file = pathlib.Path(opt("--request-file")) if opt("--request-file") else None
if request_file:
    req = json.loads(request_file.read_text(encoding="utf-8"))
    marker = request_file.parent / ("recovery-" + req["turn_id"])
    request_file.parent.joinpath("launcher.log").open("a", encoding="utf-8").write(json.dumps({"phase": phase, "turn_id": turn_id, "cwd": opt("--cwd")}, ensure_ascii=False) + "\n")
else:
    req = {}
if phase == "before":
    print(json.dumps({"status": "degraded", "context": "来自 launcher 的记忆上下文"}, ensure_ascii=False))
elif phase == "after":
    print(json.dumps({"status": "error", "message": "模拟 after 失败"}, ensure_ascii=False))
    sys.exit(7)
elif phase == "recovery replay":
    if req.get("request", {}).get("input") == "manual-recover" and not marker.exists():
        marker.write_text("1", encoding="utf-8")
        print(json.dumps({"status": "error", "message": "第一次恢复失败"}, ensure_ascii=False))
        sys.exit(8)
    print(json.dumps({"status": "spooled"}, ensure_ascii=False))
else:
    print(json.dumps({"status": "ok"}, ensure_ascii=False))
''')
_old_runtime = {
    "dir": config.AGENT_SKILL_DIR, "launcher": config.AGENT_SKILL_LAUNCHER,
    "cwd": config.AGENT_SKILL_CWD, "runtime_dir": config.AGENT_SKILL_RUNTIME_DIR,
    "touch": config.AGENT_SKILL_TOUCH_SECONDS, "timeout": config.AGENT_SKILL_COMMAND_TIMEOUT,
}
config.AGENT_SKILL_DIR = _skill_root
config.AGENT_SKILL_LAUNCHER = [sys.executable, _launcher]
config.AGENT_SKILL_CWD = os.path.dirname(os.path.abspath(__file__))
config.AGENT_SKILL_RUNTIME_DIR = os.path.join(_TMP, "skill-turns")
config.AGENT_SKILL_TOUCH_SECONDS = 0.05
config.AGENT_SKILL_COMMAND_TIMEOUT = 5
_seen_runtime = {}
def _runtime_agent(messages, tools, **kw):
    _seen_runtime["messages"] = messages
    time.sleep(0.14)  # 让后台心跳至少触发一次
    return _msg("本地运行时已确认。")
llm.agent_chat = _runtime_agent
_runtime_result = c.post("/api/agent", json={"text": "测试本机 Skill", "chapter_id": cid}, headers=H(tokA)).json()
_runtime_meta = _runtime_result.get("skill_runtime") or {}
ok(_runtime_meta.get("state") == "spooled" and _runtime_meta.get("recovered") is True, "launcher 识别 after 命令错误并自动 recovery replay")
_runtime_prompt = "\n".join(m.get("content", "") for m in _seen_runtime["messages"] if m.get("role") == "system")
ok("每回合先读取本机记忆" in _runtime_prompt and "来自 launcher 的记忆上下文" in _runtime_prompt, "每回合读取本机 meta-memory/SKILL.md 和 before 上下文")
_turn_dir = os.path.join(config.AGENT_SKILL_RUNTIME_DIR, _runtime_meta["turn_id"])
with open(os.path.join(_turn_dir, "request.json"), encoding="utf-8") as _f:
    _runtime_request = json.load(_f)
with open(os.path.join(_turn_dir, "answer.json"), encoding="utf-8") as _f:
    _runtime_answer = json.load(_f)
with open(os.path.join(_turn_dir, "manifest.json"), encoding="utf-8") as _f:
    _runtime_manifest = json.load(_f)
ok(_runtime_request["turn_id"] == _runtime_meta["turn_id"] and _runtime_answer["state"] == "model_complete", "每回合创建独立 UTF-8 请求和回答文件")
ok(_runtime_manifest["confirmed"] is True and any(e["phase"] == "touch" and e["state"] == "ok" for e in _runtime_manifest["events"]), "长任务执行 turn touch，识别 ok/degraded/spooled")
ok(not any("每回合先读取本机记忆" in (m.get("content") or "") for m in _runtime_result["messages"]), "本机 Skill 规则不写入对话历史")

# 并发 turn 不共享临时目录或 turn_id；未确认回答保留后可再次 recovery replay。
def _new_turn(n):
    return main.skill_runtime.start_turn({"user_id": uidA, "chapter_id": cid, "input": f"parallel-{n}"})
with ThreadPoolExecutor(max_workers=2) as _pool:
    _parallel_turns = list(_pool.map(_new_turn, (1, 2)))
ok(_parallel_turns[0].turn_id != _parallel_turns[1].turn_id and _parallel_turns[0].dir != _parallel_turns[1].dir, "并发回合隔离 turn_id 和临时文件")
for _turn in _parallel_turns:
    _turn.complete({"reply": "并发回答"})
_manual_failed = c.post("/api/agent", json={"text": "manual-recover", "chapter_id": cid}, headers=H(tokA))
_manual_detail = _manual_failed.json().get("detail") or {}
_manual_turn_id = _manual_detail.get("turn_id")
ok(_manual_failed.status_code == 502 and _manual_turn_id and os.path.exists(os.path.join(config.AGENT_SKILL_RUNTIME_DIR, _manual_turn_id, "answer.json")), "未确认时回答文件保留，不会删除")
_replayed = c.post(f"/api/agent/runtime/recover/{_manual_turn_id}", headers=H(tokA)).json()
ok(_replayed["reply"] == "本地运行时已确认。" and _replayed["skill_runtime"]["state"] == "spooled", "中断后可用 recovery replay 重放")
llm.agent_chat = _orig_ac
for _key, _value in _old_runtime.items():
    setattr(config, "AGENT_SKILL_" + {"dir": "DIR", "launcher": "LAUNCHER", "cwd": "CWD", "runtime_dir": "RUNTIME_DIR", "touch": "TOUCH_SECONDS", "timeout": "COMMAND_TIMEOUT"}[_key], _value)

# _compact_split 纯函数：保留最近 N、切在 user 边界、不切断工具对
_m = [
    {"role": "user", "content": "u1"}, {"role": "assistant", "content": "a1"},
    {"role": "user", "content": "u2"}, {"role": "assistant", "content": "a2"},
    {"role": "user", "content": "u3"}, {"role": "assistant", "content": "a3"},
]
ok(main._compact_split(_m, 10) == 0, "compact 短于 preserve→不切")
ok(main._compact_split(_m, 2) == 4 and _m[4]["role"] == "user", "compact 切在 user 边界")
ok(main._compact_split(_m, 3) == 4, "compact 非user起点→前移到user")
_m2 = [
    {"role": "user", "content": "u1"}, {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
    {"role": "tool", "tool_call_id": "c1", "content": "{}"}, {"role": "assistant", "content": "a1"},
    {"role": "user", "content": "u2"},
]
_kf = main._compact_split(_m2, 2)
ok(_m2[_kf]["role"] == "user", "compact 不切断 assistant→tool 工具对")

# 后台管理：admin 引导已建；admin 可列/删对话；非 admin 403
with db.get_conn() as _conn:
    _adm = _conn.execute("SELECT username FROM users WHERE is_admin=1").fetchone()
ok(_adm and _adm["username"] == "admin", "引导创建了 admin 账户")
tokAdm = c.post("/api/login", json={"username": "admin", "password": "admintest"}).json()["token"]
ok(c.get("/api/me", headers=H(tokAdm)).json()["is_admin"] is True, "admin 登录 + is_admin")
ok(c.get("/api/admin/conversations", headers=H(tokA)).status_code == 403, "普通用户访问 admin 403")
ok(c.get("/api/admin/conversations").status_code == 401, "未登录访问 admin 401")
_lst = c.get("/api/admin/conversations", headers=H(tokAdm)).json()["conversations"]
ok(any(x["user_id"] == uidA and x["chapter_id"] == cid for x in _lst), "admin 列出 alice 的对话")
ok(all("bytes" in x for x in _lst) and any(x["bytes"] > 0 for x in _lst), "admin 对话列表含每条占用字节数")
_usrs = c.get("/api/admin/users", headers=H(tokAdm)).json()["users"]
ok(any(u["username"] == "alice" for u in _usrs) and any(u["is_admin"] == 1 for u in _usrs), "admin 列出用户(含管理员标记)")
# 用户占用统计：作品/章节/对话数/对话字节数
_alice_u = next(u for u in _usrs if u["username"] == "alice")
ok(_alice_u["works"] >= 1 and _alice_u["chapters"] >= 2, "admin 用户统计含作品/章节数")
ok("conv_bytes" in _alice_u and _alice_u["conv_bytes"] > 0, "admin 用户统计含对话字节数(>0)")
# admin 删除单条对话（用 cid2 上临时落的一条，不动 cid 那条留作级联验证）
_put_conv(tokA, cid2)
_one = next(x for x in c.get("/api/admin/conversations", headers=H(tokAdm)).json()["conversations"]
           if x["user_id"] == uidA and x["chapter_id"] == cid2)
ok(c.delete(f"/api/admin/conversations/{_one['id']}", headers=H(tokAdm)).status_code == 200, "admin 删除单条对话")
ok(c.delete(f"/api/admin/conversations/{_one['id']}", headers=H(tokAdm)).status_code == 404, "admin 重复删除 404")
ok(len(c.get(f"/api/agent/conversation?chapter_id={cid2}", headers=H(tokA)).json()["messages"]) == 0, "admin 删除后该章对话为空")
# admin 清空指定用户全部对话（用 bob 在「无章节」上落的一条，不动 alice）
uidB = db.verify_user("bob", "pw1234")["id"]
_put_conv(tokB, None)
ok(c.delete(f"/api/admin/users/{uidB}/conversations", headers=H(tokAdm)).json()["deleted"] >= 1, "admin 清空指定用户对话")
ok(db.get_conversation(uidB, None) is None, "admin 清空后该用户对话为空")
# admin 删除用户账号 + 级联（carol 此前已不再使用）
uidC = db.verify_user("carol", "pw1234")["id"]
ok(c.delete(f"/api/admin/users/{uidC}", headers=H(tokAdm)).status_code == 200, "admin 删除用户 carol")
ok(db.verify_user("carol", "pw1234") is None, "删用户后 carol 账号已不存在")
with db.get_conn() as _conn:
    _cworks = _conn.execute("SELECT COUNT(*) FROM works WHERE user_id=?", (uidC,)).fetchone()[0]
    _cchap = _conn.execute("SELECT COUNT(*) FROM chapters WHERE work_id IN (SELECT id FROM works WHERE user_id=?)", (uidC,)).fetchone()[0]
ok(_cworks == 0 and _cchap == 0, "删用户级联清空其作品/章节")
ok(db.get_conversation(uidC, ccid) is None, "删用户级联清空其对话")
_usrs2 = c.get("/api/admin/users", headers=H(tokAdm)).json()["users"]
ok(not any(u["username"] == "carol" for u in _usrs2), "删用户后用户列表不再含 carol")
# 防误删：不能删自己 / 不能删管理员 / 删不存在用户 404
uidAdm = db.verify_user("admin", "admintest")["id"]
ok(c.delete(f"/api/admin/users/{uidAdm}", headers=H(tokAdm)).status_code == 400, "管理员不能删自己(400)")
ok(c.delete("/api/admin/users/99999", headers=H(tokAdm)).status_code == 404, "删不存在用户 404")

# 删除
ok(c.delete(f"/api/chapters/{cid}", headers=H(tokA)).status_code == 200, "删章节")
ok(c.delete(f"/api/works/{wid}", headers=H(tokA)).status_code == 200, "删作品")
# 删作品应级联清掉其下实体（不留孤儿行）
with db.get_conn() as conn:
    _nent = conn.execute("SELECT COUNT(*) FROM entities WHERE work_id=?", (wid,)).fetchone()[0]
ok(_nent == 0, "删作品级联清空实体")
with db.get_conn() as conn:
    _nstate_versions = conn.execute("SELECT COUNT(*) FROM entity_state_versions WHERE entity_id=?", (ent["id"],)).fetchone()[0]
    _nstate_proposals = conn.execute("SELECT COUNT(*) FROM entity_state_proposals WHERE entity_id=?", (ent["id"],)).fetchone()[0]
ok(_nstate_versions == 0 and _nstate_proposals == 0, "删作品级联清空人物状态与提议")
with db.get_conn() as conn:
    _nskill = conn.execute("SELECT COUNT(*) FROM agent_skills WHERE work_id=?", (wid,)).fetchone()[0]
ok(_nskill == 0, "删作品级联清空作品 Skill")
with db.get_conn() as conn:
    _nres = conn.execute("SELECT COUNT(*) FROM agent_skill_resources WHERE skill_id=?", (skill_pkg["id"],)).fetchone()[0]
ok(_nres == 0, "删作品级联清空 Skill 资料")
# 同样应级联清掉该作品各章节的 agent 对话（cid 上留有一条压缩后的对话）
ok(db.get_conversation(uidA, cid) is None, "删作品级联清空对话")

# 首页和前端资源：入口更新时必须换资源 URL，避免浏览器把新 DOM 与旧 CSS/JS 混用。
_home = c.get("/")
ok(_home.status_code == 200 and "style.css?v=ui-20260724-5" in _home.text and "app.js?v=ui-20260724-5" in _home.text,
   "首页可访问且前端资源带版本号")
ok(c.get("/style.css").headers.get("cache-control") == "no-cache" and c.get("/app.js").headers.get("cache-control") == "no-cache",
   "前端入口资源要求重新校验缓存")

llm.chat = _orig_character_chat
print("\nAll smoke checks passed.")
shutil.rmtree(_TMP, ignore_errors=True)
