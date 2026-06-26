# 写作 — 语音口述 + AI 落稿

服务器部署一个网页，手机/电脑打开，点语音开始说，AI 按你选的模式（转写 / 润色 / 扩写 / 续写）帮你落稿。多用户，每人用自己的大模型 key，互不影响；无需邀请码、注册码，也无需在服务器配 `.env` 密钥。

## 功能

- **语音输入**：安卓 Chrome 原生连续识别，免费、低延迟，静音自动保活重启。
- **四种主模式**：
  - 转写：说一句落一句，不调 AI（原文直接进正文）。
  - 润色：每句送 AI 修通顺、标点、分段，忠于原意。
  - 扩写：口述大意后点「生成」，AI 扩成一段。
  - 续写：AI 读前文 + 你的口述方向，按风格往下写。
- **选区缩写 / 改写**：在正文里选中一段，点「缩写选区」精简到约一半；或选个风格（更生动 / 更精炼 / 文艺风 / 口语化 / 悬疑感）再「改写选区」。结果直接替换选区，可 Ctrl+Z 撤销，不污染段落历史。
- **可视化 Diff**：历史版本里点「对比」，把某版本与当前正文逐行比对，红删绿增，一眼看清改了什么。
- **回收站**：删章节是软删（移到回收站），🗑回收 里可恢复或彻底清空，找回不丢稿。
- **实体卡片 Wiki**：👥实体 里建人物 / 地点 / 物品 / 组织 / 概念卡片（名 / 一句话摘要 / 详细设定）；「插入到正文」在光标处插入 `@名`；阅读视图里 `@名` 可悬浮点开看卡片。这些实体还会自动拼进 AI 的设定（bible），保证全文人设/地名一致。
- **找回**：从历史版本把旧草稿找回成正文。
- **AI 助手（agent）**：🤖 常驻右侧侧栏，用自然语言让 AI 直接动手——改某段文字、续写、回退到历史版本、改标题/备注、加章、摘要、设定校验。每个写操作前自动存版本快照，对话里每步带「撤销」按钮，错了秒回。不用再自己选中文字、点按钮。
- **AI 工具**：✦AI 做一致性校验（只列问题不改字）、生成本章摘要（填进备注当后续上下文）。
- **作品设定（bible）**：人物表 / 世界观 / 大纲，本作品下所有 AI 都会读到。
- **撤销 / 查找替换 / 拆分章节 / 存版 / 导出（txt、docx）/ 专注模式 / 阅读视图（夜间适配、朗读）**。
- **多端同步**：手机电脑访问同一服务器，数据在 SQLite。
- **界面**：明暗主题手动切换（🌙，默认跟随系统）、衬线/无衬线字体（文/宋，写小说更入戏）、专注模式（打字机式光标行居中）、顶栏精简（次要功能收进「⋯」菜单）、编辑区限宽居中。

## 部署（Docker，推荐）

服务器装好 Docker 和 Docker Compose 后，**直接粘贴下面整段**即可（首次部署和后续更新是同一个脚本，幂等，保留 `./data` 数据）：

```bash
DIR=/opt/1panel/docker/compose/writehtml
REPO=https://github.com/mypengpengli/writehtml.git
mkdir -p "$DIR" && cd "$DIR"
rm -rf /tmp/writehtml-update && git clone --depth 1 "$REPO" /tmp/writehtml-update
cp -rf /tmp/writehtml-update/. "$DIR"/ && rm -rf /tmp/writehtml-update
docker compose down 2>/dev/null || true
docker compose up -d --build --force-recreate
docker compose ps
# 访问 http://服务器IP:9123  自由注册即可使用
```

> 这段就是仓库里的 `deploy.sh`，也可以 `curl -fsSL …/deploy.sh | bash` 或手动 `bash deploy.sh`。

**不需要 `.env`**：注册默认开放、无需邀请码；每位用户登录后在右上角 ⚙ 设置里填自己的 `base_url / api_key / 模型ID`，服务器不存任何共享密钥。想改成凭注册码注册、或给所有用户兜底一个默认 key，编辑 `docker-compose.yml` 里对应几行注释即可。

**更新**（改完代码 push 后，在服务器上重跑上面那段，或）：

```bash
cd /opt/1panel/docker/compose/writehtml && bash deploy.sh
```

数据（`writehtml.db`）在 `./data/` 卷里，容器重建不丢稿；删这个目录才会丢。

### 不用 Docker 也行

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env && nano .env   # 仅本地调试用；生产用 compose，不需要 .env
python main.py
```

## 自检

```bash
python -m py_compile *.py && node --check static/app.js   # 语法
python test_smoke.py                                       # 接口冒烟（转写模式，无需 LLM key）
```

## 关于 HTTPS（重要）

手机浏览器调用麦克风 **必须 HTTPS**（localhost 除外）。正式用要在服务器前面挂反代 + 证书，例如：

```bash
# caddy 一行自动证书
caddy reverse-proxy --from your.domain --to localhost:9123
```

或用 nginx + Let's Encrypt。不配 HTTPS，手机上点录音会被浏览器拒绝。

## 用法

顶栏常驻：☰目录 / 标题 / 字数 / ⬇导出 / 🌙明暗 / 文衬线 / ⊚专注 / 📖阅读 / 🤖AI助手；其余（🗂存版 / 🕘历史 / 🗑回收 / 👥实体 / ✦AI / ⚙设置 / ⏻退出）收在「⋯」里。

1. 浏览器打开网址 → 注册账号（或登录）。
2. 「⋯」里的 ⚙ 填好自己的 OpenAI 兼容接口（base_url / api_key / 模型ID）。
3. 「＋作品」「＋章」建好目标；可顺手在「⋯」👥实体 建人物/地点卡片、在作品设定写大纲。
4. 选模式（默认转写）→ 🎤 开始说。
5. 转写/润色随说随落；扩写/续写先口述，再点「生成」。选中一段可缩写/改写。
6. 说错了「撤销」；想精修切到正文框手改（自动保存）。删章先进回收站，可找回。
7. 🤖 打开 AI 助手侧栏，用大白话让它干活：「把第三段改紧张点」「续写一段雨景」「退回昨晚那版」「这章摘要一下」。每步自动存版本，点「撤销」秒回。
8. 点 📖 进入阅读视图通读，`@名` 悬浮看卡片；「⋯」🕘历史 里可对比版本、找回旧稿。

## 说明

- **iOS 不适用**：Safari 的连续语音识别不稳，这套方案面向安卓（和电脑 Chrome）。若以后要支持 iPhone，需改走「录音上传→服务器 STT」路线。
- API key 只存在服务器数据库里（按用户隔离，前端只调自家后端，不暴露 key）。
- 数据库为 `./data/writehtml.db`，定期备份即可。
