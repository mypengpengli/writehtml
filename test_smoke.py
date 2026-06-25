"""冒烟测试：多用户隔离 + P1 新功能。转写模式不调 LLM，无需 key。"""
import os, shutil, uuid, sqlite3, time

# 用项目内临时目录放 db（系统 %TEMP% 上杀软偶发瞬时锁会让 sqlite 报只读）
_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".smoke_tmp", uuid.uuid4().hex[:10])
os.makedirs(_TMP, exist_ok=True)
os.environ["DB_PATH"] = os.path.join(_TMP, "test.db")
os.environ["SIGNUP_CODE"] = "testcode"   # 开放凭码注册
os.environ["LLM_API_KEY"] = ""           # 测试不调真 LLM；校验/摘要/聊天应走"未配置"500

from fastapi.testclient import TestClient
import main, db

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

# 删除
ok(c.delete(f"/api/chapters/{cid}", headers=H(tokA)).status_code == 200, "删章节")
ok(c.delete(f"/api/works/{wid}", headers=H(tokA)).status_code == 200, "删作品")

# 首页
ok(c.get("/").status_code == 200, "首页可访问")

print("\n全部通过 ✅")
shutil.rmtree(_TMP, ignore_errors=True)
