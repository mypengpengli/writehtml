"""冒烟测试：用 TestClient 走一遍接口（转写模式不调 LLM，无需 key）。"""
import os, tempfile
tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp.close()
os.environ["DB_PATH"] = tmp.name  # 用临时文件库，测完删除

from fastapi.testclient import TestClient
import main, db

db.init_db()
c = TestClient(main.app)
H = {}


def ok(cond, msg):
    print(("  OK  " if cond else " FAIL ") + msg)
    if not cond:
        raise SystemExit(1)


# 未登录应 401
ok(c.get("/api/works").status_code == 401, "未登录 401")

# 登录（默认密码 changeme）
r = c.post("/api/login", json={"password": "changeme"})
ok(r.status_code == 200, "登录成功")
H["Authorization"] = "Bearer " + r.json()["token"]

# 建作品 + 章节
wid = c.post("/api/works", json={"title": "测试作品"}, headers=H).json()["id"]
ok(bool(wid), "建作品")
cid = c.post(f"/api/works/{wid}/chapters", json={"title": "第一章"}, headers=H).json()["id"]
ok(bool(cid), "建章节")

# 章节列表带字数
chaps = c.get(f"/api/works/{wid}/chapters", headers=H).json()
ok("chars" in chaps[0], "章节列表含 chars")

# 转写处理（追加到正文）
r = c.post("/api/process", json={"mode": "转写", "text": "你好世界", "chapter_id": cid}, headers=H)
ok(r.json()["result"] == "你好世界", "转写结果=原文")
chap = c.get(f"/api/chapters/{cid}", headers=H).json()
ok("你好世界" in chap["content"], "正文已追加")
ok(len(chap["segments"]) == 1, "段落历史记录1条")

# 撤销
chap = c.post(f"/api/chapters/{cid}/undo", headers=H).json()
ok("你好世界" not in (chap["content"] or ""), "撤销后正文回退")

# 手动保存
r = c.put(f"/api/chapters/{cid}", json={"title": "改名", "content": "手动正文"}, headers=H)
ok(r.status_code == 200 and r.json().get("ok"), "保存接口")

# 删除
ok(c.delete(f"/api/chapters/{cid}", headers=H).status_code == 200, "删章节")
ok(c.delete(f"/api/works/{wid}", headers=H).status_code == 200, "删作品")
ok(c.get("/api/works", headers=H).json() == [], "删除后为空")

# 首页能取到
ok(c.get("/").status_code == 200, "首页可访问")

print("\n全部通过 ✅")
os.unlink(tmp.name)
