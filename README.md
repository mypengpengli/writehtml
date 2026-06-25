# 写作 — 语音口述 + AI 落稿

服务器部署一个网页，手机/电脑打开，点语音开始说，AI 按你选的模式（转写 / 润色 / 扩写 / 续写）帮你落稿。单人自用。

## 功能

- **语音输入**：安卓 Chrome 原生连续识别，免费、低延迟，静音自动保活重启。
- **四种模式**：
  - 转写：说一句落一句，不调 AI（原文直接进正文）。
  - 润色：每句送 AI 修通顺、标点、分段，忠于原意。
  - 扩写：口述大意后点「生成」，AI 扩成一段。
  - 续写：AI 读前文 + 你的口述方向，按风格往下写。
- **撤销**：一键撤销最近一段（含正文回退）。
- **手动编辑**：正文随时改，点「存」保存。
- **阅读视图**：全屏干净排版，自动适配夜间模式。
- **多端同步**：手机电脑访问同一服务器，数据在 SQLite。

## 部署（Docker，推荐）

服务器装好 Docker 和 Docker Compose 后：

```bash
# 1. 拉代码
git clone <你的仓库地址> writehtml && cd writehtml

# 2. 配置（首次）
cp .env.example .env
nano .env   # 设 APP_PASSWORD、LLM_BASE_URL、LLM_API_KEY、LLM_MODEL

# 3. 起服务
docker compose up -d --build
# 访问 http://服务器IP:9123
```

**更新**（改完代码 push 后，在服务器上）：

```bash
git pull && docker compose up -d --build
```

数据（`writehtml.db`）在 `./data/` 卷里，容器重建不丢稿；`.env` 在宿主机，不进镜像、不进 git。

### 不用 Docker 也行

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env && nano .env
python main.py
```

## 关于 HTTPS（重要）

手机浏览器调用麦克风 **必须 HTTPS**（localhost 除外）。正式用要在服务器前面挂反代 + 证书，例如：

```bash
# caddy 一行自动证书
caddy reverse-proxy --from your.domain --to localhost:8000
```

或用 nginx + Let's Encrypt。不配 HTTPS，手机上点录音会被浏览器拒绝。

## 用法

1. 浏览器打开网址 → 输密码登录。
2. 「＋作品」「＋章」建好目标。
3. 选模式（默认转写）→ 🎤 开始说。
4. 转写/润色会随说随落；扩写/续写先口述，再点「生成」。
5. 说错了点「撤销」；想精修切到正文框手改后「存」。
6. 点 📖 进入阅读视图通读。

## 说明

- **iOS 不适用**：Safari 的连续语音识别不稳，这套方案面向安卓（和电脑 Chrome）。若以后要支持 iPhone，需改走「录音上传→服务器 STT」路线。
- API key 只存在服务器的 `.env`，前端只调自家后端，不暴露 key。
- 数据库为同目录下 `writehtml.db`，定期备份即可。
