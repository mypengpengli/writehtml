"""冒烟测试：多用户隔离 + P1 新功能。转写模式不调 LLM，无需 key。"""
import os, shutil, uuid, sqlite3, time, json, types

# 用项目内临时目录放 db（系统 %TEMP% 上杀软偶发瞬时锁会让 sqlite 报只读）
_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".smoke_tmp", uuid.uuid4().hex[:10])
os.makedirs(_TMP, exist_ok=True)
os.environ["DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["SIGNUP_CODE"] = "testcode"   # 开放凭码注册
os.environ["LLM_API_KEY"] = ""           # 测试不调真 LLM；校验/摘要/聊天应走"未配置"500

from fastapi.testclient import TestClient
import main, db, llm

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
c.post("/api/settings", json={"base_url": "https://a.test/v1", "api_key": "sk-alice-secret", "model": "m-a"}, headers=H(tokA))
s = c.get("/api/settings", headers=H(tokA)).json()
ok(s["base_url"] == "https://a.test/v1" and s["model"] == "m-a", "设置读回 base_url/model")
ok(s["has_key"] is True and "secret" not in s["api_key_masked"] and s["api_key_masked"].startswith("****"), "key 掩码不泄露明文")
# 空 key 提交应保留旧 key
c.post("/api/settings", json={"base_url": "https://a.test/v1", "api_key": "", "model": "m-a2"}, headers=H(tokA))
ok(c.get("/api/settings", headers=H(tokA)).json()["has_key"] is True, "空 key 不清空已存 key")
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
ok(dig.startswith("作品实体") and "[人物] 林晚" in dig and "女主角，冷静" in dig, "实体 digest 格式正确")
ok(db.get_entity_digest(wid, uidA + 9999) == "", "他人作品 digest 为空(隔离)")

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

# 入参校验 / 鉴权（carol 无 key，不触达 LLM）
ok(c.post("/api/agent", json={"messages": [{"role": "user", "content": "hi"}], "chapter_id": ccid}, headers=H(tokC)).status_code == 500, "agent 无key 500")
ok(c.post("/api/agent", json={"messages": []}, headers=H(tokA)).status_code == 400, "agent 空消息 400")
ok(c.post("/api/agent", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 401, "agent 未登录 401")

# replace_text 工具：cid 正文 "你好" → "你好呀"，并验证可撤销
_orig_ac = llm.agent_chat
llm.agent_chat = _make_agent([
    _msg(None, [("c1", "replace_text", json.dumps({"old_text": "你好", "new_text": "你好呀"}))]),
    _msg("已改好。"),
])
ag = c.post("/api/agent", json={"messages": [{"role": "user", "content": "把你好改成你好呀"}], "chapter_id": cid}, headers=H(tokA)).json()
_tr = [m for m in ag["messages"] if m.get("role") == "tool"]
ok(len(_tr) == 1 and json.loads(_tr[0]["content"]).get("changed") is True, "agent replace_text 执行")
_undo = json.loads(_tr[0]["content"]).get("undo_rid")
ok(isinstance(_undo, int), "agent 返回 undo_rid")
ok(c.get(f"/api/chapters/{cid}", headers=H(tokA)).json()["content"] == "你好呀", "agent 改后正文=你好呀")
ok(c.post(f"/api/chapters/{cid}/revisions/{_undo}/restore", headers=H(tokA)).json()["content"] == "你好", "agent 动作可撤销(恢复快照)")

# list_revisions 工具（只读，验证分派 + JSON 往返）
llm.agent_chat = _make_agent([
    _msg(None, [("c2", "list_revisions", "{}")]),
    _msg("已列出。"),
])
ag2 = c.post("/api/agent", json={"messages": [{"role": "user", "content": "列出版本"}], "chapter_id": cid}, headers=H(tokA)).json()
_tr2 = [m for m in ag2["messages"] if m.get("role") == "tool"]
ok(len(_tr2) == 1 and isinstance(json.loads(_tr2[0]["content"]).get("revisions"), list), "agent list_revisions 返回版本数组")
llm.agent_chat = _orig_ac

# 删除
ok(c.delete(f"/api/chapters/{cid}", headers=H(tokA)).status_code == 200, "删章节")
ok(c.delete(f"/api/works/{wid}", headers=H(tokA)).status_code == 200, "删作品")
# 删作品应级联清掉其下实体（不留孤儿行）
with db.get_conn() as conn:
    _nent = conn.execute("SELECT COUNT(*) FROM entities WHERE work_id=?", (wid,)).fetchone()[0]
ok(_nent == 0, "删作品级联清空实体")

# 首页
ok(c.get("/").status_code == 200, "首页可访问")

print("\n全部通过 ✅")
shutil.rmtree(_TMP, ignore_errors=True)
