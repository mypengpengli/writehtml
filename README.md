# 写作 — AI 助手改稿 + 语音指令

服务器部署一个网页，手机/电脑打开，直接写正文，或用右侧 AI 助手续写、改稿、回退版本。智能体语音默认走“浏览器录音 → 直接发送给支持音频输入的 AI 模型”；关闭直发后才走“录音上传 → 后端音频转写 → AI 执行指令”，不依赖 Chrome 的 Google 语音识别服务。多用户，每人用自己的大模型 key，互不影响。

## 功能

- **智能体语音**：右侧 AI 助手默认把录音直接交给当前 AI 模型理解和执行，不调用转写。关闭「直接发送语音给 AI」后，才使用 OpenAI 兼容的 `/audio/transcriptions`；Base URL、Key 和模型 ID 均可自由填写，也可直接沿用文字模型的中转站，不要求 OpenAI 官方 Key。中转站本身必须实际提供该音频路由。
- **选区 AI**：在正文里选中一段，点「问 AI」可把这段作为右侧 AI 助手的本轮上下文，直接说“把这段改紧张点”；也可点「缩写 / 改写」快速原地替换。结果可 Ctrl+Z 或在 AI 对话里撤销。
- **文档附件**：AI 输入框旁的回形针支持 `.txt`、`.md`、`.docx` 和 `.pdf`。服务端提取文字后只作为本轮参考资料，不把全文永久写进聊天历史；可复制文字的 PDF 可直接读取，扫描版 PDF 第一版暂不做 OCR。
- **可视化 Diff**：历史版本里点「对比」，把某版本与当前正文逐行比对，红删绿增，一眼看清改了什么。
- **回收站**：删章节是软删（移到回收站），🗑回收 里可恢复或彻底清空，找回不丢稿。
- **编辑器语义层与人物档案**：正文编辑时自动高亮人物 / 地点 / 物品 / 组织 / 概念，不要求先写 `@`；悬停查看卡片，点击人物直接打开基础设定、当前动态、人物关系和成长历史。实体仍会自动拼进 AI 的设定（bible），保证全文一致。
- **角色形象生成**：人物卡可编辑提示词并生成、重生成或删除角色图。生图服务支持独立的 OpenAI 兼容 Base URL、Key、模型 ID 和尺寸，也可沿用文字模型地址与 Key；图片持久保存在数据卷，不把大文件塞进 SQLite。
- **可视化大纲 / 情节沙盘**：一个作品可建多棵互不影响的分支树；可同步真实章节和历史分支、拖动与整理节点、手工加分支，或让 AI 给出“发散 / 收束 / 推进”三种候选。沙盘试错不改正文，只有主动采纳才创建正式章节。
- **拆书引擎**：支持 TXT、Markdown、Word、可复制文字的 PDF 和 EPUB。先自动切章，再按“逐章精读 / 骨架优先”逐章提取摘要、人物、地点、物品、组织和关系；每章完成即保存，可暂停续跑、单章重试或停止并保留已有成果。
- **AI Skills**：在「⋯」→「AI Skills」中创建可复用的写作规则，或导入标准 `SKILL.md` / ZIP Skill 包（支持按需读取 `references/` 文本）。可设为所有作品通用或当前作品专用；Agent 会先看到元数据，任务匹配时再加载完整 Skill。规则会同时传给文字、选区和直发语音请求，也会传给 Agent 后续的续写/改写工具，但不会存入聊天历史。
- **找回**：从历史版本把旧草稿找回成正文。
- **AI 助手（agent）**：🤖 常驻右侧侧栏，用自然语言让 AI 直接动手——改某段文字、续写、回退到历史版本、改标题/备注、加章、摘要、设定校验。可在同一个会话中列出章节并按章节 ID 连续创建、读取和写入多章，没有“每次只能写一章”的产品限制。每个覆盖操作前自动存版本快照，跨章节撤销也会回退实际被改的章节。
- **多会话与上下文控制**：每个章节可以新建、切换、重命名、归档和删除多组 AI 会话。默认「继续」会读取并保存当前会话；「无历史」本轮不把旧聊天发给模型，但会把本轮结果留在当前会话；「临时一问」既不读取也不保存聊天记录。
- **Pi 原生智能体**：AI 助手以官方 Pi Coding Agent 运行，原生 `read / bash / edit / write / grep / find / ls`、本机 `SKILL.md`、扩展和已安装包均可用；现有章节、版本和选区工具作为额外 Pi 工具保留。
- **流式回答**：文字指令和直发语音都通过增量接口返回。模型开始输出后，右侧助手立即显示生成内容和工具执行状态；前端按浏览器绘制帧合并更新，长回复不会每个 token 重排整页。
- **联网搜索**：在「模型与联网设置」中可保存多条 Tavily Key（每行一条）。Agent 遇到今天、最新、新闻或明确的网上查询时调用 `web_search`，Key 按请求轮换，遇到无效、限流、额度或服务错误会自动尝试下一条；搜索卡片保留可点击来源。
- **多模态灵感库**：在「⋯」→「灵感库」保存文字、图片、语音、音乐、视频和网页链接。系统先持久保存作者原话与原始素材，再由 AI 后台整理为可检索卡片；通用灵感和作品专用灵感严格隔离于正式故事记忆。Agent 可通过工具真实保存、搜索、编辑并记录灵感实际使用到哪一章。
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
# 访问 http://服务器IP:9123  使用注册码注册
```

> 这段就是仓库里的 `deploy.sh`，也可以 `curl -fsSL …/deploy.sh | bash` 或手动 `bash deploy.sh`。

**不需要 `.env`**：当前 `docker-compose.yml` 使用注册码 `lipeng%@0` 开放注册，已有账号不受影响。这个码会随公开仓库一起可见，正式公网使用时应直接修改 `SIGNUP_CODE` 后重建容器。每位用户登录后在「模型与联网设置」里填自己的模型配置和 Tavily Key 池。

### 联网搜索配置

网页设置中的 Tavily Key 只保存在当前用户的 SQLite 设置里，接口只返回数量和末四位掩码。输入多条时会整体替换当前 Key 池，留空保持原值，也可以勾选清空。未配置个人 Key 时，才回退到服务器的 `TAVILY_API_KEYS`。

```env
TAVILY_API_KEYS=tvly-key-1,tvly-key-2
TAVILY_PROJECT_ID=writehtml
TAVILY_SEARCH_DEPTH=basic
TAVILY_SEARCH_MAX_RESULTS=5
```

`basic` 搜索每次消耗 1 credit；多个 Key 若属于同一个 Tavily 账号，仍共享该账号的套餐总额度。Key 池用于轮询和故障切换，不用于绕过套餐限制。

**更新**（改完代码 push 后，在服务器上重跑上面那段，或）：

```bash
cd /opt/1panel/docker/compose/writehtml && bash deploy.sh
```

数据库、灵感原始素材和运行时恢复文件都在 `./data/` 卷里，容器重建不会丢。备份时应备份整个 `./data/`，不能只复制 `writehtml.db`。

### AI 助手流式输出

网页端默认使用以下两个增量接口：

```text
POST /api/agent/stream
POST /api/agent/audio/stream
```

响应类型是 `application/x-ndjson`，一行一个 JSON 事件。`assistant_delta` 用于生成中的文字，`tool_start` 和 `status` 用于显示当前阶段，`ping` 防止长工具任务期间连接空闲超时，最后一条 `result` 携带完整、已保存的权威结果。发生错误时返回 `error`；如果本机 launcher 留下了可恢复回合，事件中会带 `turn_id`。

旧接口 `POST /api/agent` 和 `POST /api/agent/audio` 仍然保留，供旧客户端或接口调用一次性取得完整 JSON。普通润色、扩写、摘要等原子编辑接口仍先生成完整结果再应用，避免半段正文进入编辑器。

Pi 供应商请求和兼容 Agent 请求都会显式开启 `stream=true`。流式 HTTP 任务在独立线程运行，不阻塞 FastAPI 处理其他请求；浏览器最多每个动画帧刷新一次消息 DOM。Pi 进程仍按回合隔离，不复用带状态的全局 Agent 进程，避免并发会话、工具状态和工作目录互相污染。

启用“本机 meta-memory launcher”后是一个有意的例外：浏览器会实时看到“正在生成 / 正在确认 / 正在保存”等状态，但不会提前收到 `assistant_delta`。完整回答必须先写入独立回答文件，并经 `after` 或 `recovery replay` 确认后，才通过最终 `result` 返回。只使用 Pi 原生 Skills、没有配置 launcher 时，文字仍正常实时流出。

应用响应已经包含 `X-Accel-Buffering: no` 和 `Cache-Control: no-cache, no-transform`。如果域名前还有 Nginx、1Panel 反代或 CDN，仍然整段出现而不是逐步显示，应关闭这两个接口的代理缓冲和压缩：

```nginx
location ~ ^/api/agent/(stream|audio/stream)$ {
    proxy_pass http://127.0.0.1:9123;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_buffering off;
    proxy_cache off;
    gzip off;
    proxy_read_timeout 300s;
}
```

修改 1Panel 生成的站点配置后需要重载 Nginx。若使用 CDN，还要关闭该路径的响应缓存、内容改写和“完整响应后再压缩”一类功能。

### Pi 原生 Skills、扩展和包（可选）

AI 助手使用官方 Pi Coding Agent 的原生资源加载机制。它会读取 Pi 标准全局目录（默认 `~/.pi/agent`，可用 `PI_CODING_AGENT_DIR` 指定）、项目目录下的 `.pi/`，并可通过 `PI_AGENT_SKILL_DIR` 追加一个 Skill 目录。原生 Pi 的 `SKILL.md`、扩展和已安装包可使用 Pi 自带的文件与 `bash` 工具。

Docker 中需要持久化或挂载这些内容时，可在 `docker-compose.yml` 中配置：

```yaml
environment:
  - PI_CODING_AGENT_DIR=/opt/pi-agent
  - PI_AGENT_SKILL_DIR=/opt/pi-skills
  - PI_AGENT_WORKSPACE_DIR=/app
volumes:
  - /opt/pi-agent:/opt/pi-agent
  - /opt/pi-skills:/opt/pi-skills
```

注意：这是不受限的服务器进程能力。能够向 AI 助手发指令的人，可能通过模型调用读取、修改或执行该工作目录中的内容。只应在受信任的私有部署中启用，工作目录也应是明确授权给 Pi 的项目目录。

### 本机 meta-memory launcher（可选）

这是给 meta-memory 安装器使用的**服务端生命周期**。只有同时配置本机 Skill 目录和安装器生成的 launcher 后才启用；它与上面的 Pi 原生工具能力独立。

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

### AI 上下文窗口

对话不再按固定的 12000 字符提前压缩。默认按模型的 200000 Token 上下文窗口计算整个请求，在预计达到 90% 时触发压缩，并先为本轮回答预留 8192 Token。预算包含系统提示、作品资料、章节上下文、Skill、工具定义和聊天历史，因此触发点不是简单的“历史达到 180000 字符”。

可按实际模型调整：

```env
AGENT_CONTEXT_WINDOW_TOKENS=200000
AGENT_CONTEXT_TRIGGER_RATIO=0.90
AGENT_MAX_OUTPUT_TOKENS=8192
AGENT_PRESERVE_RECENT=24
AGENT_SUMMARY_MAX=2000
```

压缩只处理较早的聊天消息，保留摘要和最近消息；会话本身仍保存在 SQLite 中。使用上下文不足 200K 的模型时，应把窗口配置为该模型的真实值。

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
python test_smoke.py                                       # 接口、兼容 Agent 流式输出（无需真实 LLM key）
python test_pi_runtime.py                                  # Pi 流式输出、原生工具、Skill、语音与 launcher
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
2. 「⋯」里的 ⚙ 填好自己的 OpenAI 兼容中转站（Base URL / API Key / 模型 ID）。默认勾选「直接发送语音给 AI」；只有关闭它时才需要转写。转写可以沿用中转站，也可另填一套服务和任意受支持的模型 ID。
3. 「＋作品」「＋章」建好目标；可顺手在「⋯」👥实体 建人物/地点卡片、在作品设定写大纲。常用文风或审稿要求可在「⋯」→「AI Skills」存成模板。
4. 直接手写正文，或打开右侧 🤖 AI 助手。
5. 打开右侧 🤖 AI 助手，点标题栏 ✦ 为本轮选择需要的 Skill，再用大白话让它干活：「把第三段改紧张点」「连续写接下来的三章并分别保存」「退回昨晚那版」。也可以先选中正文交给 AI，或点回形针附加 TXT、Markdown、Word、PDF 资料。
6. 点 AI 助手输入框旁的 🎤 录音。默认会直接交给 AI 模型处理；关闭直发后才会转写成文字，可设置转写后自动发送或先放入输入框确认。
7. 在「⋯」→「灵感库」记录零散想法，并可在灵感库内上传图片、音乐和视频。也可以直接对 AI 说“把这个梗记到灵感库”，Agent 只有在工具真实保存成功后才会确认。
8. 每步自动存版本，点「撤销」秒回。想精修切到正文框手改（自动保存）。删章先进回收站，可找回。
9. 点 📖 进入阅读视图通读，`@名` 悬浮看卡片；「⋯」🕘历史 里可对比版本、找回旧稿。

## 说明

- **语音直发与转写**：默认直发语音时，当前 AI 模型必须支持音频输入；不支持会明确提示，关闭直发即可改用转写。转写服务要求 `/audio/transcriptions`，可沿用中转站的 Base URL/Key 并自由填写其支持的模型 ID，也可使用独立服务。若中转站只有 `/chat/completions` 而没有音频转写路由，换普通聊天模型 ID 仍然无法转写。
- **文档附件边界**：默认单文件最大 25MB，提取后单文件最多 120000 字符、本轮合计最多 160000 字符、最多 8 份；可用 `AGENT_DOCUMENT_MAX_BYTES`、`AGENT_DOCUMENT_MAX_CHARS`、`AGENT_DOCUMENT_TOTAL_MAX_CHARS` 和 `AGENT_DOCUMENT_MAX_FILES` 调整。附件全文只进入当前回合，不进入持久聊天历史。
- **AI Skills 的来源**：网页导入的 `SKILL.md` / ZIP 仍只作为数据库中的文本规则和资料；Pi 原生 Skills、扩展和包则从服务器文件系统按 Pi 标准加载，并可使用 Pi 原生工具。两者是不同来源，不要把不可信网页上传包当作服务器本机 Pi 包安装。
- **灵感素材限制**：默认单张图片 20MB、音频 100MB、视频 300MB，每个用户去重后总计 5GB；都可通过 `INSPIRATION_*` 环境变量调整。上传会校验扩展名、MIME、文件签名和 SHA-256。
- API key 只存在服务器数据库里（按用户隔离，前端只调自家后端，不暴露 key）。
- 数据库为 `./data/writehtml.db`，灵感原始文件位于 `./data/inspirations/`；应定期一致备份整个 `./data/`。
