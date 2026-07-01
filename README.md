# 写作 — AI 助手改稿 + 语音指令

服务器部署一个网页，手机/电脑打开，直接写正文，或用右侧 AI 助手续写、改稿、回退版本。智能体语音走“浏览器录音上传 → 后端音频转写 → AI 执行指令”，不再依赖 Chrome 的 Google 语音识别服务。多用户，每人用自己的大模型 key，互不影响。

## 功能

- **智能体语音**：右侧 AI 助手的麦克风会录音并上传后端转写，再把文字指令发给 AI。需要你的 OpenAI 兼容接口支持 `/audio/transcriptions`，默认转写模型 `whisper-1`。
- **选区 AI**：在正文里选中一段，点「问 AI」可把这段作为右侧 AI 助手的本轮上下文，直接说“把这段改紧张点”；也可点「缩写 / 改写」快速原地替换。结果可 Ctrl+Z 或在 AI 对话里撤销。
- **可视化 Diff**：历史版本里点「对比」，把某版本与当前正文逐行比对，红删绿增，一眼看清改了什么。
- **回收站**：删章节是软删（移到回收站），🗑回收 里可恢复或彻底清空，找回不丢稿。
- **实体卡片 Wiki**：👥实体 里建人物 / 地点 / 物品 / 组织 / 概念卡片（名 / 一句话摘要 / 详细设定）；「插入到正文」在光标处插入 `@名`；阅读视图里 `@名` 可悬浮点开看卡片。这些实体还会自动拼进 AI 的设定（bible），保证全文人设/地名一致。
- **找回**：从历史版本把旧草稿找回成正文。
- **AI 助手（agent）**：🤖 常驻右侧侧栏，用自然语言让 AI 直接动手——改某段文字、续写、回退到历史版本、改标题/备注、加章、摘要、设定校验。每个写操作前自动存版本快照，对话里每步带「撤销」按钮，错了秒回。支持**语音指令**（🎤 录音转写后发送）与**自动朗读**（🔊 把 AI 的回复与操作念给你听）；「说完是否自动发送」在设置里开关，默认自动发。
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

**不需要 `.env`**：注册默认开放、无需邀请码；每位用户登录后在右上角 ⚙ 设置里填自己的 `base_url / api_key / 模型ID / 语音转写模型`，服务器不存任何共享密钥。智能体语音要求该接口支持 OpenAI 兼容 `/audio/transcriptions`。想改成凭注册码注册、或给所有用户兜底一个默认 key，编辑 `docker-compose.yml` 里对应几行注释即可。

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

顶栏常驻：☰目录 / 标题 / 字数 / 🤖AI助手；导出、阅读、主题、字体、历史、回收站、设置等收在「⋯」里。

1. 浏览器打开网址 → 注册账号（或登录）。
2. 「⋯」里的 ⚙ 填好自己的 OpenAI 兼容接口（base_url / api_key / 模型ID / 语音转写模型）。
3. 「＋作品」「＋章」建好目标；可顺手在「⋯」👥实体 建人物/地点卡片、在作品设定写大纲。
4. 直接手写正文，或打开右侧 🤖 AI 助手。
5. 用大白话让它干活：「把第三段改紧张点」「续写一段雨景」「退回昨晚那版」「这章摘要一下」。也可以先在正文里选中一段，点「问 AI」，再说「把这段改紧张点」。
6. 点 AI 助手输入框旁的 🎤 录音，说完后会上传后端转写；可在设置里控制转写后自动发送或只填入输入框。
7. 每步自动存版本，点「撤销」秒回。想精修切到正文框手改（自动保存）。删章先进回收站，可找回。
8. 点 📖 进入阅读视图通读，`@名` 悬浮看卡片；「⋯」🕘历史 里可对比版本、找回旧稿。

## 说明

- **语音转写接口**：右侧智能体语音已经走「录音上传→服务器 ASR」。如果你使用的 `base_url` 不支持 `/audio/transcriptions`，文字 AI 仍可用，但语音按钮会提示转写失败。
- API key 只存在服务器数据库里（按用户隔离，前端只调自家后端，不暴露 key）。
- 数据库为 `./data/writehtml.db`，定期备份即可。
