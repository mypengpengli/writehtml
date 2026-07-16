# 写作 — AI 助手改稿 + 语音指令

服务器部署一个网页，手机/电脑打开，直接写正文，或用右侧 AI 助手续写、改稿、回退版本。智能体语音默认走“浏览器录音 → 直接发送给支持音频输入的 AI 模型”；关闭直发后才走“录音上传 → 后端音频转写 → AI 执行指令”，不依赖 Chrome 的 Google 语音识别服务。多用户，每人用自己的大模型 key，互不影响。

## 功能

- **智能体语音**：右侧 AI 助手默认把录音直接交给当前 AI 模型理解和执行，不调用 Whisper；该模型/网关必须支持 OpenAI 兼容的音频输入。关闭「直接发送语音给 AI」后，才使用独立的转写服务；可单独填转写 Base URL、Key、模型，默认 `whisper-1`。
- **选区 AI**：在正文里选中一段，点「问 AI」可把这段作为右侧 AI 助手的本轮上下文，直接说“把这段改紧张点”；也可点「缩写 / 改写」快速原地替换。结果可 Ctrl+Z 或在 AI 对话里撤销。
- **可视化 Diff**：历史版本里点「对比」，把某版本与当前正文逐行比对，红删绿增，一眼看清改了什么。
- **回收站**：删章节是软删（移到回收站），🗑回收 里可恢复或彻底清空，找回不丢稿。
- **实体卡片 Wiki**：👥实体 里建人物 / 地点 / 物品 / 组织 / 概念卡片（名 / 一句话摘要 / 详细设定）；「插入到正文」在光标处插入 `@名`；阅读视图里 `@名` 可悬浮点开看卡片。这些实体还会自动拼进 AI 的设定（bible），保证全文人设/地名一致。
- **AI Skills**：在「⋯」→「AI Skills」中创建可复用的写作规则，或导入标准 `SKILL.md` / ZIP Skill 包（支持按需读取 `references/` 文本）。可设为所有作品通用或当前作品专用；Agent 会先看到元数据，任务匹配时再加载完整 Skill。规则会同时传给文字、选区和直发语音请求，也会传给 Agent 后续的续写/改写工具，但不会存入聊天历史。
- **找回**：从历史版本把旧草稿找回成正文。
- **AI 助手（agent）**：🤖 常驻右侧侧栏，用自然语言让 AI 直接动手——改某段文字、续写、回退到历史版本、改标题/备注、加章、摘要、设定校验。每个写操作前自动存版本快照，对话里每步带「撤销」按钮，错了秒回。支持**语音指令**（🎤 默认直发模型，关闭直发后才转写）与**自动朗读**（🔊 把 AI 的回复与操作念给你听）。
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

**不需要 `.env`**：注册默认开放、无需邀请码；每位用户登录后在右上角 ⚙ 设置里填自己的 `base_url / api_key / 模型ID`，服务器不存任何共享密钥。默认直发语音需要当前模型支持音频输入；只有关闭直发时才需要另配 OpenAI 兼容 `/audio/transcriptions` 服务。想改成凭注册码注册、或给所有用户兜底一个默认 key，编辑 `docker-compose.yml` 里对应几行注释即可。

**更新**（改完代码 push 后，在服务器上重跑上面那段，或）：

```bash
cd /opt/1panel/docker/compose/writehtml && bash deploy.sh
```

数据（`writehtml.db`）在 `./data/` 卷里，容器重建不丢稿；删这个目录才会丢。

### 本机 meta-memory launcher（可选）

这是**服务端**能力，不是让网页用户执行服务器命令。只有同时配置了本机 Skill 目录和安装器生成的 launcher 后才启用；未配置时，原有 Agent 行为不变。

目录必须包含：

```text
<skill-dir>/meta-memory/SKILL.md
```

每个 Agent 回合都会重新读取这个文件，并将其作为仅本轮有效的系统规则。服务会以固定身份 `writehtml-writing-agent-v1`（可用环境变量改名）执行 launcher，工作目录为项目目录，同时显式传入 `--cwd`。launcher 协议为：

```text
launcher before --agent-id ... --turn-id ... --request-file ... --answer-file ... --cwd ...
launcher turn touch <turn-id> --agent-id ... --cwd ...
launcher after --agent-id ... --turn-id ... --request-file ... --answer-file ... --cwd ...
launcher recovery replay --agent-id ... --turn-id ... --request-file ... --answer-file ... --cwd ...
```

`stdout` 最后一条 JSON 应返回 `{"status":"ok"}`、`degraded` 或 `spooled`；非零退出码、未知状态和超时会被识别为命令错误。每轮在 `AGENT_SKILL_RUNTIME_DIR/<turn_id>/` 保留 UTF-8 的 `request.json`、`answer.json` 和 `manifest.json`。模型回答先写入 `answer.json`，只有 `after` 或 `recovery replay` 确认后才返回浏览器；确认前文件不会删除。自动重放仍失败时，可带原 id 调用 `POST /api/agent/runtime/recover/<turn_id>`，不会再次调用模型。

Docker 部署时把安装器产物和 Skill 目录挂进容器，再在 `docker-compose.yml` 的 `environment` 取消相应配置注释：

```yaml
environment:
  - AGENT_SKILL_DIR=/opt/meta-skills
  - AGENT_SKILL_LAUNCHER=/opt/meta-runtime/launcher
  - AGENT_SKILL_CWD=/app
volumes:
  - /opt/meta-skills:/opt/meta-skills:ro
  - /opt/meta-runtime:/opt/meta-runtime:ro
```

运行时请求和回答会包含作品指令和 Agent 回复，应只挂载到受控的服务器目录并定期按你的审计策略清理。

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
2. 「⋯」里的 ⚙ 填好自己的 OpenAI 兼容接口（base_url / api_key / 模型ID）。默认勾选「直接发送语音给 AI」；只有关闭它时才需要配置转写 Base URL、Key 和模型。
3. 「＋作品」「＋章」建好目标；可顺手在「⋯」👥实体 建人物/地点卡片、在作品设定写大纲。常用文风或审稿要求可在「⋯」→「AI Skills」存成模板。
4. 直接手写正文，或打开右侧 🤖 AI 助手。
5. 打开右侧 🤖 AI 助手，点标题栏 ✦ 为本轮选择需要的 Skill，再用大白话让它干活：「把第三段改紧张点」「续写一段雨景」「退回昨晚那版」「这章摘要一下」。也可以先在正文里选中一段，点「问 AI」，再说「把这段改紧张点」。
6. 点 AI 助手输入框旁的 🎤 录音。默认会直接交给 AI 模型处理；关闭直发后才会转写成文字，可设置转写后自动发送或先放入输入框确认。
7. 每步自动存版本，点「撤销」秒回。想精修切到正文框手改（自动保存）。删章先进回收站，可找回。
8. 点 📖 进入阅读视图通读，`@名` 悬浮看卡片；「⋯」🕘历史 里可对比版本、找回旧稿。

## 说明

- **语音直发与转写**：默认直发语音时，当前 AI 模型必须支持音频输入；不支持会明确提示，关闭直发即可改用转写。转写服务要求 `/audio/transcriptions`，可以与文字模型使用不同的 Base URL、Key 和模型；因此文字 AI 可用但转写服务不可用时，两者互不影响。
- **AI Skills 的执行边界**：网页导入的 `SKILL.md` / ZIP 只作为文本规则和文本资料，包内脚本永不执行。只有服务管理员通过环境变量配置的本机 `meta-memory` launcher 才能执行 CLI；Agent 可执行的作品动作仍受内置工具和账号权限限制。
- API key 只存在服务器数据库里（按用户隔离，前端只调自家后端，不暴露 key）。
- 数据库为 `./data/writehtml.db`，定期备份即可。
