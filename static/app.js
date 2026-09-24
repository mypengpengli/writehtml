/* 前端：多用户登录、目录树(拖拽)、语音、AI、自动保存、字数、查找替换、备注、拆分、版本、导出、阅读 */
const $ = (id) => document.getElementById(id);

let token = localStorage.getItem("token") || "";
let works = [];
let chapters = [];
let currentWorkId = null;
let currentChapterId = null;
let mode = "转写";

// 语音
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null, micOn = false, draftBuffer = "", recFatal = false;
// AI 侧栏：录音直发或转写 + 自动朗读
let agentRecorder = null, agentMicOn = false, agentChunks = [], agentStream = null, _agentPH = null, agentMicTimer = null;
let voiceDirectToModel = localStorage.getItem("voiceDirectToModel") !== "0"; // 默认直发语音
let voiceAsrAutoSend = localStorage.getItem("voiceAsrAutoSend") === "1";
let aiTts = localStorage.getItem("aiTts") !== "0"; // 默认开

// 正文保存协调器：单通道写服务器，editVersion 防止旧响应清掉新输入。
let saveTimer = null, dirty = false;
let editorEditVersion = 0;
let editorContentRevision = null;
let editorSavePromise = null;
let editorConflictResolver = null;
let editorConflictData = null;
// 查找
let findPos = [], findIdx = -1;
// 拖拽
let dragCid = null;
// AI 助手（agent）
let agentMsgs = [];
let agentBusy = false;
let agentReplyDraft = "";
let agentReplyStatus = "";
let agentReplyRenderPending = false;
let agentUndone = new Set();
let agentSelection = null;
let agentSessions = [];
let currentAgentSessionId = null;
let agentConversationSummary = "";
let agentConversationMode = "standard";
let agentConversationLoading = false;
let agentConversationRequest = 0;
let agentSessionScopeKey = "";
let agentSkills = [];
let agentSkillsWorkId = undefined;
let activeAgentSkillIds = new Set();
let pendingAgentDocuments = [];
let materialData = null;
let currentUsername = "";
// 写作工作台：剧情、章节流程、关系、提醒与本回合上下文共用一个右侧抽屉。
let storyTab = "plot";
let plotStateChapterId = null;
let plotStateProposalId = null;
let plotStateData = null;
let plotStateSaveTimer = null;
let chapterWorkflow = null;
let storyMemoryChapterId = null;
let storyMemoryData = null;
let storyMemorySearchResults = null;
let editingStoryMemoryId = null;
let entityRelations = [];
let editingRelationId = null;
let consistencyAlerts = [];
let agentContext = null;
let pendingEditReview = null;
let pendingRevisionRestore = null;
let pendingWorkRevisionRestore = null;
let voiceTraceState = null;
let voiceTraceHideTimer = null;
// 多模态创意灵感库
let inspirationItems = [];
let currentInspiration = null;
let inspirationScope = "all";
let inspirationSearchTimer = null;
let pendingInspirationFile = null;
let inspirationPreviewUrl = null;
let inspirationPendingPolls = 0;
// 创作生产画布
let productionMode = "overview";
let productionSection = localStorage.getItem("productionSection") || "canon";
let productionChapterId = null;
let productionOverviewData = null;
let productionChapterData = null;
let productionChapterPlans = [];
let productionCards = [];
let productionResourceCategory = "all";
let productionSelected = null;
let productionAutoClosedResources = false;
let productionLayoutTimer = null;
let productionLayoutPending = null;
let productionInspectorSaveTimer = null;
let productionInspectorSaving = false;
let productionZoom = 1;
let storyPlanNodes = [];
let storyPlanFilter = "all";
let storyPlanView = localStorage.getItem("storyPlanView") === "cards" ? "cards" : "route";
let storyPlanShowArchive = false;
let storyPlanLocateChapterId = null;
let storyPlanChildrenIndex = new Map();
let storyPlanCollapsed = new Set();
let storyPlanSaveTimer = null;
let storyPlanSaving = false;

class SerialSaveQueue {
  constructor() { this.tail = Promise.resolve(); this.pending = 0; }
  run(task) {
    this.pending += 1;
    const next = this.tail.then(task, task);
    this.tail = next.catch(() => {}).finally(() => { this.pending = Math.max(0, this.pending - 1); });
    return next;
  }
  async wait() { await this.tail; }
}
const productionSaveQueue = new SerialSaveQueue();
const plotStateSaveQueue = new SerialSaveQueue();
const productionLayoutSaveQueue = new SerialSaveQueue();

/* ---------- 图标（内联 SVG，Lucide 风格 24×24 描边） ---------- */
const _W = 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"';
const ICONS = {
  menu:     `<svg ${_W}><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>`,
  x:        `<svg ${_W}><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`,
  moon:     `<svg ${_W}><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/></svg>`,
  sun:      `<svg ${_W}><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>`,
  focus:    `<svg ${_W}><path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/><circle cx="12" cy="12" r="3"/></svg>`,
  book:     `<svg ${_W}><path d="M4 4h7a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H4z"/><path d="M20 4h-7a3 3 0 0 0-3 3v13a2 2 0 0 1 2-2h8z"/></svg>`,
  bot:      `<svg ${_W}><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M12 4v4"/><circle cx="12" cy="4" r="1" fill="currentColor" stroke="none"/><circle cx="9" cy="14" r="1" fill="currentColor" stroke="none"/><circle cx="15" cy="14" r="1" fill="currentColor" stroke="none"/></svg>`,
  more:     `<svg ${_W}><circle cx="5" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.6" fill="currentColor" stroke="none"/></svg>`,
  bookmark: `<svg ${_W}><path d="M6 3h12v18l-6-4-6 4z"/></svg>`,
  clock:    `<svg ${_W}><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>`,
  trash:    `<svg ${_W}><path d="M3 6h18"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>`,
  users:    `<svg ${_W}><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>`,
  sparkles: `<svg ${_W}><path d="M12 3l1.7 4.8L18 9.5l-4.3 1.7L12 16l-1.7-4.8L6 9.5l4.3-1.7z"/><path d="M18.5 14.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/></svg>`,
  settings: `<svg ${_W}><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1"/></svg>`,
  logout:   `<svg ${_W}><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/></svg>`,
  shield:   `<svg ${_W}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`,
  mic:      `<svg ${_W}><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><line x1="12" y1="18" x2="12" y2="22"/></svg>`,
  square:   `<svg ${_W}><rect x="6" y="6" width="12" height="12" rx="2"/></svg>`,
  undo:     `<svg ${_W}><path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6.7 2.3L3 13"/></svg>`,
  search:   `<svg ${_W}><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>`,
  scissors: `<svg ${_W}><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><line x1="20" y1="4" x2="8.5" y2="15.5"/><line x1="8.5" y1="8.5" x2="20" y2="20"/></svg>`,
  note:     `<svg ${_W}><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h6"/></svg>`,
  shrink:   `<svg ${_W}><polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="14" y1="10" x2="21" y2="3"/><line x1="3" y1="21" x2="10" y2="14"/></svg>`,
  pen:      `<svg ${_W}><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>`,
  volume:   `<svg ${_W}><path d="M11 5L6 9H3v6h3l5 4z"/><path d="M16 9a5 5 0 0 1 0 6"/><path d="M19 6a9 9 0 0 1 0 12"/></svg>`,
  mute:     `<svg ${_W}><path d="M11 5L6 9H3v6h3l5 4z"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/></svg>`,
  chevL:    `<svg ${_W}><polyline points="15 18 9 12 15 6"/></svg>`,
  chevU:    `<svg ${_W}><polyline points="6 15 12 9 18 15"/></svg>`,
  chevD:    `<svg ${_W}><polyline points="6 9 12 15 18 9"/></svg>`,
  chevR:    `<svg ${_W}><polyline points="9 18 15 12 9 6"/></svg>`,
  play:     `<svg ${_W}><polygon points="6 3 20 12 6 21" fill="currentColor" stroke="none"/></svg>`,
  plus:     `<svg ${_W}><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>`,
  enter:    `<svg ${_W}><polyline points="9 10 4 15 9 20"/><path d="M20 4v7a4 4 0 0 1-4 4H4"/></svg>`,
  download: `<svg ${_W}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>`,
  upload:   `<svg ${_W}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>`,
  feather:  `<svg ${_W}><path d="M20.2 12.2a6 6 0 0 0-8.5-8.5L5 10.5V19h8.5z"/><line x1="16" y1="8" x2="2" y2="22"/><line x1="17.5" y1="15" x2="9" y2="15"/></svg>`,
  type:     `<svg ${_W}><polyline points="4 7 4 4 20 4 20 7"/><line x1="9" y1="20" x2="15" y2="20"/><path d="M12 4v16"/></svg>`,
  grip:     `<svg ${_W}><circle cx="9" cy="6" r="1.4" fill="currentColor" stroke="none"/><circle cx="9" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="9" cy="18" r="1.4" fill="currentColor" stroke="none"/><circle cx="15" cy="6" r="1.4" fill="currentColor" stroke="none"/><circle cx="15" cy="12" r="1.4" fill="currentColor" stroke="none"/><circle cx="15" cy="18" r="1.4" fill="currentColor" stroke="none"/></svg>`,
  panel:    `<svg ${_W}><rect x="3" y="4" width="18" height="16" rx="2"/><line x1="9" y1="4" x2="9" y2="20"/><line x1="13" y1="9" x2="18" y2="9"/><line x1="13" y1="14" x2="18" y2="14"/></svg>`,
  eye:      `<svg ${_W}><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z"/><circle cx="12" cy="12" r="2.7"/></svg>`,
  refresh:  `<svg ${_W}><path d="M20 11a8 8 0 1 0 2 5.5"/><polyline points="20 4 20 11 13 11"/></svg>`,
  branch:   `<svg ${_W}><line x1="6" y1="3" x2="6" y2="15"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="6" r="3"/><path d="M6 6a12 12 0 0 0 12 0"/></svg>`,
  nodes:    `<svg ${_W}><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="8.5" y="14" width="7" height="7" rx="1"/><path d="M6.5 10v2h11v-2M12 12v2"/></svg>`,
  alert:    `<svg ${_W}><path d="M10.3 3.8 2.7 17a2 2 0 0 0 1.7 3h15.2a2 2 0 0 0 1.7-3L13.7 3.8a2 2 0 0 0-3.4 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
  check:    `<svg ${_W}><polyline points="20 6 9 17 4 12"/></svg>`,
  bulb:     `<svg ${_W}><path d="M9 18h6"/><path d="M10 22h4"/><path d="M8.4 14.5A7 7 0 1 1 15.6 14.5C14.6 15.2 14 16.4 14 18h-4c0-1.6-.6-2.8-1.6-3.5z"/></svg>`,
  paperclip:`<svg ${_W}><path d="m21.4 11.6-8.9 8.9a6 6 0 0 1-8.5-8.5l9.6-9.6a4 4 0 0 1 5.7 5.7l-9.6 9.6a2 2 0 0 1-2.8-2.8l8.9-8.9"/></svg>`,
  star:     `<svg ${_W}><polygon points="12 2 15.1 8.3 22 9.3 17 14.1 18.2 21 12 17.7 5.8 21 7 14.1 2 9.3 8.9 8.3 12 2"/></svg>`,
  archive:  `<svg ${_W}><rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v12h14V8"/><path d="M10 12h4"/></svg>`,
  image:    `<svg ${_W}><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>`,
  music:    `<svg ${_W}><path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/></svg>`,
  film:     `<svg ${_W}><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M7 4v16M17 4v16M2 9h5M17 9h5M2 15h5M17 15h5"/></svg>`,
  copy:     `<svg ${_W}><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>`,
};
function svg(n) { return ICONS[n] || ""; }
// 把图标注入 [data-ic] 元素；data-label 存在则图标后跟文字（移动端更易用）
function setIcon(el, n, label) {
  if (!el || !ICONS[n]) return;
  el.innerHTML = ICONS[n] + (label != null ? `<span class="ic-label">${label}</span>` : "");
}
function applyIcons() {
  document.querySelectorAll("[data-ic]").forEach(el => setIcon(el, el.dataset.ic, el.dataset.label));
}

/* ---------- 交互层：toast / 询问卡 / 忙碌态（替代原生 alert·prompt·confirm） ---------- */
function showToast(msg, type) {
  const host = $("toast"); if (!host) return;
  const t = document.createElement("div");
  t.className = "toast" + (type ? " " + type : "");
  t.textContent = msg;
  host.appendChild(t);
  requestAnimationFrame(() => t.classList.add("show"));
  setTimeout(() => { t.classList.remove("show"); setTimeout(() => t.remove(), 220); }, 2400);
}
let _askResolve = null;
function askCard({ title, msg, input, def, okText, danger }) {
  return new Promise(resolve => {
    _askResolve = resolve;
    $("askTitle").textContent = title || "";
    const m = $("askMsg");
    m.textContent = msg || "";
    m.classList.toggle("hidden", !msg);
    const inp = $("askInput");
    if (input) { inp.classList.remove("hidden"); inp.placeholder = input; inp.value = def || ""; }
    else { inp.classList.add("hidden"); inp.value = ""; }
    const ok = $("askOk");
    ok.textContent = okText || "确定";
    ok.classList.toggle("danger-btn", !!danger);
    $("askOverlay").classList.remove("hidden");
    if (input) setTimeout(() => { inp.focus(); inp.select(); }, 40);
    else setTimeout(() => ok.focus(), 40);
  });
}
function closeAsk(ok) {
  if (!_askResolve) return;
  const inp = $("askInput");
  let r;
  if (!ok) r = false;                                   // 取消
  else if (!inp.classList.contains("hidden")) r = inp.value; // prompt：返回输入
  else r = true;                                        // confirm：返回 true
  $("askOverlay").classList.add("hidden");
  const fn = _askResolve; _askResolve = null; fn(r);
}
document.addEventListener("keydown", e => {
  if (e.key === "Escape" && _askResolve) {
    e.preventDefault();
    e.stopImmediatePropagation();
    closeAsk(false);
  }
});
// 忙碌态：el 加 .is-busy（CSS 变灰+禁点）；带文字的按钮顺带塞个转圈
function busy(el, on, text) {
  if (!el) return;
  el.classList.toggle("is-busy", on);
  if (text != null) el.innerHTML = on ? '<span class="spinner"></span> ' + text : text;
}

/* ---------- 通用 ---------- */

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: "Bearer " + token } : {}),
    },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401) { showLogin(); throw new Error("未登录"); }
  if (!res.ok) {
    const info = await responseErrorData(res);
    const error = new Error(info.message);
    error.status = res.status;
    error.detail = info.detail;
    throw error;
  }
  return res.json();
}

async function streamAgent(body, onEvent, path = "/api/agent/stream") {
  const res = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Accept": "application/x-ndjson",
      ...(token ? { Authorization: "Bearer " + token } : {}),
    },
    body: JSON.stringify(body),
  });
  if (res.status === 401) { showLogin(); throw new Error("未登录"); }
  if (!res.ok) throw new Error(await responseError(res));
  if (!res.body) throw new Error("当前浏览器不支持流式响应");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result = null;
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split("\n");
    buffer = done ? "" : lines.pop();
    for (const line of lines) {
      if (!line.trim()) continue;
      let event;
      try { event = JSON.parse(line); }
      catch (e) { throw new Error("流式响应格式无效"); }
      if (event.type === "ping") continue;
      if (event.type === "error") {
        let message = event.message || "Agent 执行失败";
        if (event.turn_id) message += `（恢复编号：${event.turn_id}）`;
        throw new Error(message);
      }
      if (event.type === "result") result = event.data;
      else onEvent(event);
    }
    if (done) break;
  }
  if (!result) throw new Error("Agent 流式响应未返回最终结果");
  return result;
}

async function responseErrorData(res) {
  const raw = (await res.text()).trim();
  let msg = raw || res.statusText || "请求失败";
  let detail = null;
  try {
    const data = JSON.parse(raw);
    detail = data.detail ?? data;
    if (data.detail && typeof data.detail === "object") {
      msg = data.detail.message || msg;
      if (data.detail.turn_id) msg += `（恢复编号：${data.detail.turn_id}）`;
    } else {
      msg = data.detail || data.message || msg;
    }
  } catch (e) {}
  msg = String(msg).replace(/\s+/g, " ");
  return { message: msg.length > 260 ? msg.slice(0, 260) + "…" : msg, detail };
}
async function responseError(res) {
  return (await responseErrorData(res)).message;
}

const tail = (s, n) => (!s ? "" : s.length > n ? s.slice(-n) : s);
const charCount = (s) => (s || "").replace(/\s/g, "").length;
const esc = (s) => (s || "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function safeHttpUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch (e) { return ""; }
}
const workflowLabels = { planning: "规划", drafting: "写作", review: "复核", final: "定稿" };
function workflowLabel(status) { return workflowLabels[status] || workflowLabels.drafting; }
function appendText(t) {
  const el = $("content");
  if (el.value && !el.value.endsWith("\n")) el.value += "\n";
  el.value += t;
  el.scrollTop = el.scrollHeight;
}

/* ---------- 登录 / 注册 ---------- */

function showLogin() {
  if (!$("inspirationWorkspace")?.classList.contains("hidden")) closeInspirationLibrary();
  $("app")?.classList.remove("production-open");
  $("app").classList.add("hidden");
  $("reader")?.classList.add("hidden");
  $("login").classList.remove("hidden");
}
function showApp() { $("login").classList.add("hidden"); $("app").classList.remove("hidden"); }
function showRegister() { $("loginForm").classList.add("hidden"); $("registerForm").classList.remove("hidden"); $("loginMsg").textContent = ""; }
function showLoginForm() { $("registerForm").classList.add("hidden"); $("loginForm").classList.remove("hidden"); $("loginMsg").textContent = ""; }

async function doLogin() {
  try {
    const r = await api("/api/login", { body: { username: $("user").value, password: $("pwd").value } });
    token = r.token; localStorage.setItem("token", token);
    $("user").value = ""; $("pwd").value = "";
    await init();
  } catch (e) { $("loginMsg").textContent = "用户名或密码错误"; }
}

async function doRegister() {
  try {
    const r = await api("/api/register", {
      body: { username: $("regUser").value, password: $("regPwd").value, code: $("regCode").value },
    });
    token = r.token; localStorage.setItem("token", token);
    await init();
  } catch (e) { $("loginMsg").textContent = e.message; }
}

async function doLogout() {
  if (!await flushEditorSave()) return;
  try { await api("/api/logout"); } catch (e) {}
  clearEntityImageCache();
  token = ""; localStorage.removeItem("token");
  agentSessions = []; currentAgentSessionId = null; agentMsgs = [];
  agentConversationSummary = ""; agentSessionScopeKey = ""; currentUsername = "";
  showLogin();
}

/* ---------- 目录树 ---------- */

async function init() {
  showApp();
  const me = await api("/api/me", { method: "GET" });
  if (currentUsername && currentUsername !== me.username) {
    clearEntityImageCache();
    agentSkills = []; agentSkillsWorkId = undefined; activeAgentSkillIds = new Set();
    agentSessions = []; currentAgentSessionId = null; agentMsgs = [];
    agentConversationSummary = ""; agentSessionScopeKey = "";
  }
  currentUsername = me.username || "";
  $("meName").textContent = me.username ? `${me.username} · 目录` : "目录";
  if (me.is_admin) $("adminBtn").classList.remove("hidden");
  await loadWorks();
  await loadModelSelectors();
}

async function loadWorks() {
  works = await api("/api/works", { method: "GET" });
  if (!works.some(work => work.id === currentWorkId)) {
    currentWorkId = works.length ? works[0].id : null;
  }
  currentChapterId = null;
  await loadChapters();
}

function renderTree() {
  const tree = $("workTree");
  if (!works.length) { tree.innerHTML = `<button class="empty-cta" onclick="newWork()">${svg("plus")} 新建第一篇作品</button>`; return; }
  tree.innerHTML = works.map(w => {
    const open = w.id === currentWorkId;
    const items = open ? chapters.map(c => `
      <div class="chap ${c.id === currentChapterId ? "cur" : ""}" draggable="true"
           onclick="selectChapter(${c.id})"
           ondragstart="dragStart(event,${c.id})"
           ondragover="dragOver(event)"
           ondragleave="dragLeave(event)"
           ondrop="dragDrop(event,${c.id})">
        <span class="drag">${svg("grip")}</span>
        <span class="c-title">${esc(c.title) || "(无标题)"}</span>
        ${c.branch_of_chapter_id ? `<span class="chap-branch" title="从历史版本创建的分支稿">${svg("branch")}</span>` : ""}
        <span class="chap-stage stage-${esc(c.workflow_status || "drafting")}" title="章节阶段">${esc(workflowLabel(c.workflow_status))}</span>
        ${c.analysis_status === "needs_review" ? '<span class="chap-analysis" title="正文已变化，派生资料需要重新分析">需复核</span>' : ""}
        <span class="c-wc">${(c.chars || 0)}字</span>
        <button class="c-del" onclick="event.stopPropagation();delChapter(${c.id})" title="删除">${svg("x")}</button>
      </div>`).join("") : "";
    return `
      <div class="work ${open ? "open" : ""}">
        <div class="w-row" onclick="selectWork(${w.id})">
          <span class="w-title">${esc(w.title)}</span>
          <button class="ic" onclick="event.stopPropagation();openWorkNotes(${w.id})" title="创作总则">${svg("book")}</button>
          <button class="c-del" onclick="event.stopPropagation();delWork(${w.id})" title="删除">${svg("x")}</button>
        </div>
        ${open ? `<div class="chaps">${items || '<div class="empty">点「＋章」</div>'}</div>
                 <button class="add-chap" onclick="newChapter(${w.id})">${svg("plus")}<span class="ic-label">新章</span></button>` : ""}
      </div>`;
  }).join("");
}

async function selectWork(wid) {
  if (!await flushEditorSave()) return;
  currentWorkId = wid;
  currentChapterId = null;
  entitiesCache = []; entitiesCacheWorkId = null; entitiesCacheChapterId = null;
  renderSemanticEditor();
  await loadChapters();
}

async function loadChapters() {
  if (!currentWorkId) {
    chapters = []; renderTree(); updateWC();
    await Promise.all([loadAgentSkills(), loadAgentSessions()]);
    return;
  }
  chapters = await api(`/api/works/${currentWorkId}/chapters`, { method: "GET" });
  if (!chapters.find(c => c.id === currentChapterId)) {
    currentChapterId = chapters.length ? chapters[chapters.length - 1].id : null;
  }
  if (currentChapterId) await Promise.all([loadChapter(), loadAgentSessions()]);
  else {
    $("content").value = ""; $("chapTitle").value = ""; $("notes").value = "";
    await Promise.all([loadAgentSessions(), loadWikiEntities()]);
  }
  renderTree(); updateWC();
  await loadAgentSkills();
}

async function selectChapter(cid) {
  if (cid === currentChapterId) return;
  if (!await flushEditorSave()) return;
  currentChapterId = cid;
  plotStateChapterId = cid;
  storyMemoryChapterId = cid;
  storyMemorySearchResults = null;
  editingStoryMemoryId = null;
  clearAgentSelection();
  agentUndone.clear();
  setAgentConversationMode("standard");
  await Promise.all([loadAgentSessions(), loadChapter()]);
  renderTree();
  if (window.innerWidth <= 700) $("app").classList.remove("side-open");
}

async function loadChapter() {
  if (!currentChapterId) return;
  const c = await api(`/api/chapters/${currentChapterId}`, { method: "GET" });
  $("chapTitle").value = c.title || "";
  $("content").value = c.content || "";
  $("notes").value = c.notes || "";
  editorContentRevision = Math.max(1, +(c.content_revision || 1));
  editorEditVersion = 0;
  dirty = false;
  const restored = restoreEditorDraft(c);
  renderSemanticEditor();
  updateSaveStat(restored ? "已恢复本机草稿" : "");
  updateWC();
  const cur = chapters.find(x => x.id === currentChapterId);
  if (cur) cur.chars = charCount(c.content || "");
  try { await loadWikiEntities(); }
  catch (e) { entitiesCache = []; entitiesCacheWorkId = null; entitiesCacheChapterId = null; renderSemanticEditor(); }
  if (!$("wikiOverlay").classList.contains("hidden")) await refreshCharacterCards();
  if (!$("characterStateOverlay").classList.contains("hidden") && characterStateChapterId === currentChapterId) {
    await loadCharacterState();
  }
  if ($("app").classList.contains("story-open")) await refreshStoryDrawer();
}

async function newWork() {
  const title = await askCard({ title: "新建作品", input: "作品名", def: "新作品", okText: "新建" });
  if (!title) return;
  if (!await flushEditorSave()) return;
  const r = await api("/api/works", { body: { title } });
  currentWorkId = r.id; currentChapterId = null;
  await loadWorks();
}

async function newChapter(wid) {
  const title = await askCard({ title: "新建章节", input: "章节名", def: "新章节", okText: "新建" });
  if (!title) return;
  if (!await flushEditorSave()) return;
  const r = await api(`/api/works/${wid}/chapters`, { body: { title } });
  currentWorkId = wid; currentChapterId = r.id;
  await loadChapters();
}

async function delChapter(cid) {
  if (!await askCard({ title: "移到回收站？", msg: "可找回。在回收站点「彻底删除」才会真正删除。", okText: "移到回收站", danger: true })) return;
  if (!await flushEditorSave()) return;
  await api(`/api/chapters/${cid}`, { method: "DELETE" });
  currentChapterId = null;
  await loadChapters();
}

async function delWork(wid) {
  if (!await askCard({ title: "删除整个作品？", msg: "作品及其所有章节将被删除，不可恢复。", okText: "删除", danger: true })) return;
  if (!await flushEditorSave()) return;
  await api(`/api/works/${wid}`, { method: "DELETE" });
  currentWorkId = null; currentChapterId = null;
  await loadWorks();
}

/* ---------- 回收站 ---------- */
async function openTrash() {
  if (!currentWorkId) { showToast("先选一个作品", "err"); return; }
  const list = await api(`/api/works/${currentWorkId}/trash`, { method: "GET" });
  $("trashList").innerHTML = list.length ? list.map(c => `
    <div class="rev">
      <span>${esc(c.title)} · ${c.chars}字 · ${new Date(c.deleted_at * 1000).toLocaleString()}</span>
      <button class="ic" onclick="restoreFromTrash(${c.id})">恢复</button>
      <button class="ic" onclick="purgeFromTrash(${c.id})" title="彻底删除，不可恢复">彻底删</button>
    </div>`).join("") : '<div class="empty">回收站是空的</div>';
  $("trashOverlay").classList.remove("hidden");
}
function closeTrash() { $("trashOverlay").classList.add("hidden"); }
async function restoreFromTrash(cid) {
  await api(`/api/chapters/${cid}/restore`, { method: "POST" });
  await loadChapters();
  await openTrash();
  flash("已恢复");
}
async function purgeFromTrash(cid) {
  if (!await askCard({ title: "彻底删除？", msg: "这一步不可恢复。", okText: "彻底删除", danger: true })) return;
  await api(`/api/chapters/${cid}/purge`, { method: "POST" });
  await openTrash();
}

/* ---------- 拖拽排序 ---------- */

function dragStart(ev, cid) { dragCid = cid; ev.dataTransfer.effectAllowed = "move"; }
function dragOver(ev) { ev.preventDefault(); ev.dataTransfer.dropEffect = "move"; ev.currentTarget.classList.add("drag-over"); }
function dragLeave(ev) { ev.currentTarget.classList.remove("drag-over"); }
async function dragDrop(ev, targetCid) {
  ev.preventDefault();
  ev.currentTarget.classList.remove("drag-over");
  if (dragCid === null || dragCid === targetCid) { dragCid = null; return; }
  const ids = chapters.map(c => c.id);
  const from = ids.indexOf(dragCid), to = ids.indexOf(targetCid);
  ids.splice(from, 1); ids.splice(to, 0, dragCid);
  dragCid = null;
  chapters.sort((a, b) => ids.indexOf(a.id) - ids.indexOf(b.id));
  renderTree();
  await api(`/api/works/${currentWorkId}/reorder`, { body: { ids } });
}

/* ---------- 自动保存 + 字数 ---------- */

function editorDraftKey(chapterId = currentChapterId, workId = currentWorkId) {
  if (!chapterId || !workId) return "";
  return `writehtml:editor-draft:${currentUsername || "anonymous"}:${workId}:${chapterId}`;
}
function editorSnapshot() {
  return {
    chapter_id: currentChapterId,
    work_id: currentWorkId,
    title: $("chapTitle").value,
    content: $("content").value,
    notes: $("notes").value,
    base_revision: editorContentRevision,
    edit_version: editorEditVersion,
    saved_at: Date.now(),
  };
}
function stashEditorDraft(snapshot = editorSnapshot()) {
  const key = editorDraftKey(snapshot.chapter_id, snapshot.work_id);
  if (!key) return;
  try { localStorage.setItem(key, JSON.stringify(snapshot)); } catch (e) {}
}
function clearEditorDraft(chapterId = currentChapterId, workId = currentWorkId) {
  const key = editorDraftKey(chapterId, workId);
  if (!key) return;
  try { localStorage.removeItem(key); } catch (e) {}
}
function restoreEditorDraft(serverChapter) {
  const key = editorDraftKey(serverChapter.id, serverChapter.work_id);
  if (!key) return false;
  let draft = null;
  try { draft = JSON.parse(localStorage.getItem(key) || "null"); } catch (e) { draft = null; }
  if (!draft || draft.chapter_id !== serverChapter.id) return false;
  const differs = draft.title !== (serverChapter.title || "") || draft.content !== (serverChapter.content || "") ||
    draft.notes !== (serverChapter.notes || "");
  if (!differs) { clearEditorDraft(serverChapter.id, serverChapter.work_id); return false; }
  $("chapTitle").value = draft.title || "";
  $("content").value = draft.content || "";
  $("notes").value = draft.notes || "";
  editorContentRevision = Math.max(1, +(draft.base_revision || serverChapter.content_revision || 1));
  editorEditVersion = Math.max(1, +(draft.edit_version || 1));
  dirty = true;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, 500);
  return true;
}
function markEditorDirty() {
  editorEditVersion += 1;
  dirty = true;
  stashEditorDraft();
  updateSaveStat("已本机暂存");
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, 1500);
}
function onContentInput() {
  markEditorDirty(); updateWC();
  typewriterCenter();
  updateSelectionTools();
  queueSemanticEditorRender();
}
function onNotesInput() { markEditorDirty(); }

function waitForEditorConflict(local, server) {
  editorConflictData = { local, server };
  $("editorConflictLocal").value = local.content || "";
  $("editorConflictServer").value = server.content || "";
  $("editorConflictOverlay").classList.remove("hidden");
  return new Promise(resolve => { editorConflictResolver = resolve; });
}
function resolveEditorConflict(action) {
  $("editorConflictOverlay").classList.add("hidden");
  const resolve = editorConflictResolver;
  editorConflictResolver = null;
  if (resolve) resolve(action);
}
async function handleEditorConflict(snapshot, server) {
  updateSaveStat("存在多端冲突");
  stashEditorDraft(snapshot);
  const action = await waitForEditorConflict(snapshot, server || {});
  if (action === "server") {
    $("chapTitle").value = server.title || "";
    $("content").value = server.content || "";
    $("notes").value = server.notes || "";
    editorContentRevision = Math.max(1, +(server.content_revision || editorContentRevision || 1));
    editorEditVersion += 1;
    dirty = false;
    clearEditorDraft(snapshot.chapter_id, snapshot.work_id);
    renderSemanticEditor(); updateWC(); updateSaveStat("已采用服务器版本");
    return true;
  }
  if (action === "branch") {
    const branch = await api(`/api/chapters/${snapshot.chapter_id}/conflict-branch`, { body: {
      title: `${snapshot.title || "章节"} · 冲突分支`, content: snapshot.content, notes: snapshot.notes,
    }});
    clearEditorDraft(snapshot.chapter_id, snapshot.work_id);
    dirty = false;
    currentWorkId = branch.work_id;
    currentChapterId = branch.id;
    await loadChapters();
    showToast("本机稿已另存为分支", "ok");
    return true;
  }
  if (action === "local") {
    editorContentRevision = Math.max(1, +(server.content_revision || editorContentRevision || 1));
    dirty = true;
    stashEditorDraft({ ...snapshot, base_revision: editorContentRevision, edit_version: editorEditVersion });
    return "retry";
  }
  dirty = true;
  updateSaveStat("冲突未处理，草稿仍在本机");
  return false;
}

async function runEditorSaveLoop() {
  clearTimeout(saveTimer);
  saveTimer = null;
  while (currentChapterId && dirty) {
    const snapshot = editorSnapshot();
    const saveVersion = editorEditVersion;
    updateSaveStat("保存中…");
    try {
      const saved = await api(`/api/chapters/${snapshot.chapter_id}`, {
        method: "PUT",
        body: { title: snapshot.title, content: snapshot.content, notes: snapshot.notes,
          expected_revision: snapshot.base_revision },
      });
      if (currentChapterId !== snapshot.chapter_id) return false;
      editorContentRevision = Math.max(1, +(saved.analysis?.content_revision || editorContentRevision || 1));
      const cur = chapters.find(x => x.id === snapshot.chapter_id);
      if (cur) {
        cur.chars = charCount(snapshot.content);
        cur.content_revision = editorContentRevision;
        cur.analysis_status = saved.analysis?.status;
        cur.analysis_reason = saved.analysis?.reason;
      }
      if (editorEditVersion === saveVersion) {
        dirty = false;
        clearEditorDraft(snapshot.chapter_id, snapshot.work_id);
        updateSaveStat("已保存");
      } else {
        dirty = true;
        stashEditorDraft();
        updateSaveStat("有新内容，继续保存…");
      }
      renderTree();
    } catch (e) {
      if (e.status === 409 && e.detail?.code === "chapter_revision_conflict") {
        const handled = await handleEditorConflict(snapshot, e.detail.server || {});
        if (handled === "retry") continue;
        return handled;
      }
      dirty = true;
      stashEditorDraft();
      updateSaveStat("保存失败，草稿在本机");
      return false;
    }
  }
  return true;
}
async function saveNow() {
  if (!currentChapterId) return true;
  if (editorSavePromise) return editorSavePromise;
  if (!dirty) return true;
  editorSavePromise = runEditorSaveLoop();
  try { return await editorSavePromise; }
  finally { editorSavePromise = null; }
}
async function flushEditorSave() {
  clearTimeout(saveTimer);
  saveTimer = null;
  const saved = await saveNow();
  if (!saved) showToast("正文尚未同步，已停止切换；本机草稿仍然保留", "err");
  return !!saved;
}
function saveTitle() { markEditorDirty(); saveNow(); }
function updateSaveStat(s) { $("saveStat").textContent = s; }
function updateWC() {
  const live = charCount($("content").value);
  let total = chapters.reduce((s, c) => s + (c.chars || 0), 0);
  const cur = chapters.find(c => c.id === currentChapterId);
  if (cur) total = total - (cur.chars || 0) + live;
  $("wc").textContent = `本章 ${live} 字 · 全文 ${total} 字`;
}

/* ---------- 模式 ---------- */

function setMode(m) {
  mode = m;
  const sel = $("modeSel");
  if (sel && sel.value !== m) sel.value = m;
  document.querySelectorAll(".mode").forEach(b => b.classList.toggle("active", b.dataset.mode === m));
  const gen = $("genBtn");
  if (gen) {
    gen.classList.toggle("hidden", !(m === "扩写" || m === "续写"));
    gen.title = m === "扩写" ? "把当前语音草稿扩写到正文" : "把当前语音草稿续写到正文";
  }
  draftBuffer = ""; showDraft("");
}

/* ---------- 语音 ---------- */

// 致命错误：停麦克风且不让 onend 自动重启，避免无手势重启→not-allowed 死循环
const FATAL_REC_ERRORS = new Set(["not-allowed", "service-not-allowed", "audio-capture", "network"]);
function explainRecError(err) {
  const m = {
    "not-allowed": "麦克风被拒(not-allowed)：HTTPS 下仍报此错，多半是浏览器站点麦克风权限未真正「允许」、或处于无痕模式、或麦克风被别的程序占用",
    "service-not-allowed": "识别服务被拒(service-not-allowed)：Chrome 转写服务连不上（国内网络够不着 Google），需改用自有/自托管 ASR",
    "audio-capture": "麦克风硬件不可用或被占用(audio-capture)",
    "network": "识别服务网络中断(network)",
    "no-speech": "没听到声音(no-speech)",
    "aborted": "已中止(aborted)",
  };
  return m[err] || ("语音错误：" + err);
}

function setupRec() {
  if (!SR) { $("micStatus").textContent = "不支持语音识别（用安卓 Chrome）"; return; }
  if (rec) return;
  rec = new SR();
  rec.lang = "zh-CN"; rec.continuous = true; rec.interimResults = true;
  rec.onresult = (e) => {
    let interim = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const r = e.results[i];
      if (r.isFinal) onFinal(r[0].transcript); else interim += r[0].transcript;
    }
    showDraft(draftBuffer + interim);
  };
  rec.onend = () => {
    // 连续重听：仅在未致命报错时才程序化重启；否则停麦克风（无手势重启会被拒 not-allowed）
    if (micOn && !recFatal) { try { rec.start(); } catch (e) {} }
    else { micOn = false; setMic(false); }
  };
  rec.onerror = (e) => {
    // no-speech/aborted 不致命，保持连续重听；其余一律停麦克风、不再自动重启
    const fatal = !["no-speech", "aborted"].includes(e.error);
    if (fatal) { recFatal = true; micOn = false; setMic(false); $("micStatus").textContent = explainRecError(e.error); }
  };
}

function toggleMic() {
  showToast("连续转写已停用，请用右侧 AI 助手的语音按钮", "err");
  return;
  if (!rec) { showToast("浏览器不支持语音识别，请用安卓 Chrome", "err"); return; }
  if (micOn) { micOn = false; try { rec.stop(); } catch (e) {} setMic(false); }
  else {
    if (agentMicOn) { agentMicOn = false; try { agentRecorder?.stop(); } catch (e) {} setAgentMic(false); } // 互斥
    micOn = true; recFatal = false; draftBuffer = ""; try { rec.start(); } catch (e) {} setMic(true);
  }
}
function setMic(on) {
  if (!$("micBtn")) return;
  setIcon($("micBtn"), on ? "square" : "mic", on ? "停止" : "开始说");
  $("micBtn").classList.toggle("on", on);
  $("micStatus").textContent = on ? "正在听…" : "";
}
function onFinal(text) {
  text = text.trim();
  if (!text) return;
  if (mode === "转写" || mode === "润色") processAndAppend(text);
  else { draftBuffer += text; showDraft(draftBuffer); }
}
function showDraft(s) {
  const el = $("draft");
  if (!s) { el.textContent = "这里显示你正在说的话…"; el.classList.remove("active"); }
  else { el.textContent = s; el.classList.add("active"); }
}

function updateSelectionTools() {
  const el = $("content"), tools = $("selectionTools");
  if (!el || !tools) return;
  tools.classList.toggle("hidden", el.selectionStart === el.selectionEnd);
}

function getContentSelection() {
  const el = $("content");
  if (!el || el.selectionStart === el.selectionEnd) return null;
  const start = el.selectionStart, end = el.selectionEnd;
  const text = el.value.slice(start, end);
  if (!text.trim()) return null;
  if (text.length > 8000) {
    showToast("选区太长，请少选一点再交给 AI", "err");
    return null;
  }
  return {
    text,
    start,
    end,
    before: el.value.slice(Math.max(0, start - 300), start),
    after: el.value.slice(end, Math.min(el.value.length, end + 300)),
    title: $("chapTitle").value || "",
  };
}

function selectionPreview(sel) {
  const s = (sel?.text || "").trim().replace(/\s+/g, " ");
  return s.length > 120 ? s.slice(0, 120) + "…" : s;
}

function renderAgentSelection() {
  const box = $("agentSelection");
  if (!box) return;
  box.classList.toggle("hidden", !agentSelection);
  if (agentSelection) $("agentSelectionText").textContent = selectionPreview(agentSelection);
}

function quoteSelectionToAgent() {
  if (!currentChapterId) { showToast("先选择或新建一个章节", "err"); return; }
  const sel = getContentSelection();
  if (!sel) { showToast("先在正文里选中一段文字", "err"); return; }
  agentSelection = sel;
  if (!$("app").classList.contains("ai-open")) toggleAISide();
  renderAgentSelection();
  const input = $("agentInput");
  input.placeholder = "告诉 AI 要怎么处理这段选区，比如：把这段改得更紧张";
  input.focus();
  showToast("已把选区交给 AI，本轮发送会带上这段文字", "ok");
}

function clearAgentSelection() {
  agentSelection = null;
  renderAgentSelection();
  const input = $("agentInput");
  if (input) input.placeholder = "让 AI 帮你改稿、续写、回退版本…（Enter 发送，Shift+Enter 换行）";
}

function chooseAgentDocuments() {
  if (agentBusy) return;
  $("agentDocumentFiles").click();
}

function renderAgentDocuments() {
  const host = $("agentDocuments");
  if (!host) return;
  host.classList.toggle("hidden", !pendingAgentDocuments.length);
  host.innerHTML = pendingAgentDocuments.map((document, index) => `
    <div class="agent-document-chip" title="${esc(document.name)}">
      ${svg("paperclip")}
      <span>${esc(document.name)}</span>
      <small>${document.chars.toLocaleString()} 字${document.truncated ? " · 已截取" : ""}</small>
      ${currentWorkId ? `<button class="agent-document-save" onclick="saveAgentDocumentAsMaterial(${index})" title="保存后可在以后回合按需召回">存为长期</button>` : ""}
      <button class="ic" onclick="removeAgentDocument(${index})" title="移除附件">${svg("x")}</button>
    </div>`).join("");
}

function removeAgentDocument(index) {
  if (agentBusy) return;
  pendingAgentDocuments.splice(index, 1);
  renderAgentDocuments();
}

async function saveAgentDocumentAsMaterial(index) {
  const document = pendingAgentDocuments[index];
  if (!document || !currentWorkId) return;
  try {
    await api(`/api/works/${currentWorkId}/materials/documents`, {
      body: { name: document.name, content: document.text, enabled: true, pinned: false },
    });
    showToast(`《${document.name}》已存为本书长期资料；本轮附件仍保留`, "ok");
    if (!$('materialOverlay')?.classList.contains('hidden')) await loadMaterialHub();
  } catch (e) { showToast("保存长期资料失败：" + e.message, "err"); }
}

async function addAgentDocuments(fileList) {
  const input = $("agentDocumentFiles");
  const files = Array.from(fileList || []);
  if (input) input.value = "";
  if (!files.length) return;
  const button = $("agentDocumentBtn");
  button.disabled = true;
  button.classList.add("is-busy");
  try {
    for (const file of files) {
      const response = await fetch("/api/agent/documents/extract", {
        method: "POST",
        headers: {
          "Content-Type": file.type || "application/octet-stream",
          "X-File-Name": encodeURIComponent(file.name),
          ...(token ? { Authorization: "Bearer " + token } : {}),
        },
        body: file,
      });
      if (response.status === 401) { showLogin(); throw new Error("未登录"); }
      if (!response.ok) throw new Error(await responseError(response));
      const document = await response.json();
      const existing = pendingAgentDocuments.findIndex(item => item.name === document.name);
      if (existing >= 0) pendingAgentDocuments.splice(existing, 1, document);
      else pendingAgentDocuments.push(document);
      renderAgentDocuments();
      showToast(`${document.name} 已加入本轮资料${document.truncated ? "（内容较长，已截取）" : ""}`, "ok");
    }
  } catch (e) {
    showToast("添加附件失败：" + e.message, "err");
  } finally {
    button.disabled = false;
    button.classList.remove("is-busy");
  }
}

/* ---------- AI 处理：先生成预览，作者确认后才写入正文 ---------- */

async function notifyStoryUpdates(result) {
  const count = Array.isArray(result?.character_state_proposals) ? result.character_state_proposals.length : 0;
  const plot = result?.plot_state_proposal ? 1 : 0;
  const memories = Array.isArray(result?.memory_proposals) ? result.memory_proposals.length : 0;
  if (count || plot || memories) {
    const labels = [];
    if (count) labels.push(`${count} 条人物状态`);
    if (plot) labels.push("剧情推进");
    if (memories) labels.push(`${memories} 条故事记忆`);
    showToast(`已生成待确认：${labels.join("、")}`, "ok");
    await refreshCharacterCards();
    if ($("app").classList.contains("story-open")) await refreshStoryDrawer();
  }
}

function openEditReview(proposal) {
  pendingEditReview = proposal;
  const replacing = proposal.operation === "replace";
  $("editReviewMeta").textContent = `${proposal.mode || "AI 改稿"} · ${replacing ? "替换选区" : "追加到章末"}`;
  $("editReviewScope").textContent = replacing
    ? `将替换当前章节中的 ${charCount(proposal.old_text)} 字选区`
    : "将追加到当前章节末尾";
  $("editReviewOld").textContent = replacing ? proposal.old_text : proposal.base_content.slice(-1600) || "（当前正文为空）";
  $("editReviewNew").textContent = proposal.result || "（模型没有返回内容）";
  $("editReviewBackdrop").classList.remove("hidden");
  $("editReviewDrawer").classList.remove("hidden");
  $("editReviewApplyBtn").textContent = replacing ? "接受替换" : "接受追加";
}

function closeEditReview() {
  $("editReviewBackdrop").classList.add("hidden");
  $("editReviewDrawer").classList.add("hidden");
  pendingEditReview = null;
}

async function applyEditReview() {
  const proposal = pendingEditReview;
  if (!proposal || proposal.chapter_id !== currentChapterId) { closeEditReview(); return; }
  const button = $("editReviewApplyBtn");
  busy(button, true, "应用中");
  try {
    const result = await api(`/api/chapters/${proposal.chapter_id}/edit-proposals/apply`, {
      body: proposal,
    });
    $("content").value = result.content || "";
    if (result.title != null) $("chapTitle").value = result.title;
    dirty = false;
    updateSaveStat("");
    updateWC();
    const chapter = chapters.find(item => item.id === currentChapterId);
    if (chapter) chapter.chars = charCount(result.content || "");
    renderTree();
    updateSelectionTools();
    closeEditReview();
    await notifyStoryUpdates(result);
    flash("已应用 AI 改稿");
  } catch (e) {
    showToast(e.message, "err");
  } finally {
    if (pendingEditReview) busy(button, false, proposal.operation === "replace" ? "接受替换" : "接受追加");
  }
}

async function processAndAppend(text) {
  if (!currentChapterId) { showToast("先选择或新建一个章节", "err"); return; }
  const el = $("content");
  setMicStatus("处理中…");
  try {
    // 纯转写仍按原有语音落稿方式直接追加；所有 AI 生成正文先进入预览。
    if (mode === "转写") {
      const r = await api("/api/process", { body: { mode, text, context: tail(el.value, 1500), chapter_id: currentChapterId } });
      appendText(r.result);
      onContentInput();
      await notifyStoryUpdates(r);
      return;
    }
    if (!await flushEditorSave()) return;
    const baseContent = el.value;
    const r = await api("/api/process", {
      body: { mode, text, context: tail(baseContent, 1500), chapter_id: currentChapterId,
              preview: true, preview_operation: "append" },
    });
    openEditReview({ chapter_id: currentChapterId, mode: r.mode || mode, operation: r.operation || "append",
      result: r.result || "", base_content: baseContent, old_text: "", start: null, end: null });
  } catch (e) { setMicStatus("出错：" + e.message); }
  finally { setMicStatus(""); }
}

// 选区操作：缩写 / 改写风格。结果先进入预览，确认后由后端验证原文仍未变化再替换。
async function processSelection(m, style) {
  const el = $("content");
  if (!currentChapterId) { showToast("先选择或新建一个章节", "err"); return; }
  const s = el.selectionStart, e = el.selectionEnd;
  if (s == null || s === e) { showToast("先在正文里选中一段文字再操作", "err"); return; }
  const selected = el.value.slice(s, e);
  setMicStatus("处理中…");
  try {
    if (!await flushEditorSave()) return;
    const baseContent = el.value;
    const r = await api("/api/process", {
      body: { mode: m, text: selected, context: tail(baseContent, 1500), chapter_id: currentChapterId, style,
              preview: true, preview_operation: "replace" },
    });
    openEditReview({ chapter_id: currentChapterId, mode: r.mode || m, operation: r.operation || "replace",
      result: r.result || "", base_content: baseContent, old_text: selected, start: s, end: e });
  } catch (e) { setMicStatus("出错：" + e.message); }
  finally { setMicStatus(""); }
}

async function generate() {
  if (!draftBuffer) { setMicStatus("先说点内容再生成"); return; }
  const text = draftBuffer; draftBuffer = ""; showDraft("");
  const gen = $("genBtn");
  busy(gen, true);
  try { await processAndAppend(text); } finally { busy(gen, false); }
}

async function undo() {
  if (!currentChapterId) return;
  if (!await flushEditorSave()) return;
  const c = await api(`/api/chapters/${currentChapterId}/undo`, { method: "POST" });
  $("content").value = c.content || "";
  onContentInput();
  flash("已撤销最近一段");
}
function setMicStatus(s) { $("micStatus").textContent = s; }
let flashTimer;
function flash(msg) {
  $("micStatus").textContent = msg;
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { if (!micOn) setMicStatus(""); }, 1500);
}

/* ---------- 查找 / 替换 ---------- */

function toggleFind() { $("findBar").classList.toggle("hidden"); if (!$("findBar").classList.contains("hidden")) $("findInput").focus(); }
function doFind() {
  const q = $("findInput").value, t = $("content").value;
  findPos = []; findIdx = -1;
  if (q) { let i = 0; while ((i = t.indexOf(q, i)) >= 0) { findPos.push(i); i += q.length; } }
  $("findInfo").textContent = findPos.length ? `${findPos.length} 处` : "无";
  if (findPos.length) { findIdx = 0; showMatch(); }
}
let _findTimer = null;
function doFindDebounced() { clearTimeout(_findTimer); _findTimer = setTimeout(doFind, 180); } // 长文查找防抖
function showMatch() {
  if (findIdx < 0) return;
  const ta = $("content"), q = $("findInput").value;
  const start = findPos[findIdx], end = start + q.length;
  ta.focus(); ta.setSelectionRange(start, end);
  const linesBefore = ta.value.slice(0, start).split("\n").length;
  ta.scrollTop = (linesBefore - 1) * 28;
}
function findNext() { if (!findPos.length) { doFind(); return; } findIdx = (findIdx + 1) % findPos.length; showMatch(); }
function doReplace() {
  if (findIdx < 0) return;
  const ta = $("content"), q = $("findInput").value, r = $("replaceInput").value;
  const start = findPos[findIdx];
  ta.value = ta.value.slice(0, start) + r + ta.value.slice(start + q.length);
  onContentInput(); doFind();
}
function replaceAll() {
  const ta = $("content"), q = $("findInput").value, r = $("replaceInput").value;
  if (!q) return;
  ta.value = ta.value.split(q).join(r);
  onContentInput(); doFind(); flash("已全部替换");
}

/* ---------- 备注 / 拆分 ---------- */

function toggleNotes() { $("notesBar").classList.toggle("hidden"); if (!$("notesBar").classList.contains("hidden")) $("notes").focus(); }

async function splitChapter() {
  if (!currentChapterId) { showToast("先选择章节", "err"); return; }
  const ta = $("content");
  const at = ta.selectionStart;
  if (at <= 0) { showToast("把光标放在要拆分的位置（拆分点之后的内容会进新章节）", "err"); return; }
  const title = await askCard({ title: "拆分为新章节", msg: "拆分点之后的内容会进入新章节。", input: "新章节标题", def: "新章节", okText: "拆分" });
  if (!title) return;
  await api(`/api/chapters/${currentChapterId}/split`, { body: { at, title } });
  await loadChapters();
  flash("已拆分");
}

/* ---------- 修订版本 ---------- */

async function saveRevision() {
  if (!currentChapterId) return;
  if (!await flushEditorSave()) return;
  await api(`/api/chapters/${currentChapterId}/revisions`, { method: "POST" });
  flash("已存为版本");
}

async function saveNamedRevision() {
  if (!currentChapterId) return;
  if (!await flushEditorSave()) return;
  const label = await askCard({ title: "保存命名章节版本", input: "版本名称（可选）", def: "", okText: "保存" });
  if (label === false) return;
  await api(`/api/chapters/${currentChapterId}/revisions`, { body: { label: label || "" } });
  await showRevisions();
  showToast("章节版本已保存", "ok");
}

async function saveNamedWorkRevision() {
  if (!currentWorkId) return;
  if (!await flushEditorSave()) return;
  const label = await askCard({ title: "保存整本版本", input: "版本名称（可选）", def: "", okText: "保存" });
  if (label === false) return;
  await api(`/api/works/${currentWorkId}/revisions`, { body: { label: label || "" } });
  await showRevisions();
  showToast("整本版本已保存", "ok");
}

async function showRevisions() {
  if (!currentChapterId || !currentWorkId) return;
  const [list, workList] = await Promise.all([
    api(`/api/chapters/${currentChapterId}/revisions`, { method: "GET" }),
    api(`/api/works/${currentWorkId}/revisions`, { method: "GET" }),
  ]);
  $("revList").innerHTML = list.length ? list.map(r => `
    <div class="rev">
      <span><b>${esc(r.label || "未命名快照")}</b><small>${new Date(r.created_at * 1000).toLocaleString()} · ${r.chars}字</small></span>
      <span class="rev-actions">
        <button class="ic" onclick="openDiff(${r.id}, true)" title="先查看差异再恢复">${svg("eye")}</button>
        <button class="ic" onclick="renameRevision(${r.id})" title="重命名版本">${svg("pen")}</button>
        <button class="ic" onclick="createBranchFromRevision(${r.id})" title="从此版本创建可编辑分支稿">${svg("branch")}</button>
        <button class="ic" onclick="recoverFromRevision(${r.id})" title="让 AI 从旧草稿中找回内容并先预览">${svg("sparkles")}</button>
      </span>
    </div>`).join("") : '<div class="empty">还没有存过版本</div>';
  $("workRevList").innerHTML = workList.length ? workList.map(r => `
    <div class="rev">
      <span><b>${esc(r.label || "整本快照")}</b><small>${new Date(r.created_at * 1000).toLocaleString()} · ${r.chapters}章</small></span>
      <span class="rev-actions"><button class="ic" onclick="openWorkDiff(${r.id}, true)" title="查看整本变化并恢复">${svg("eye")}</button></span>
    </div>`).join("") : '<div class="empty">还没有整本版本</div>';
  $("revOverlay").classList.remove("hidden");
}
function closeRevisions() { $("revOverlay").classList.add("hidden"); }
async function renameRevision(rid, existing) {
  const label = await askCard({ title: "重命名章节版本", input: "版本名称", def: existing || "", okText: "保存" });
  if (label === false) return;
  await api(`/api/chapters/${currentChapterId}/revisions/${rid}`, { method: "PUT", body: { label: label || "" } });
  await showRevisions();
}
async function createBranchFromRevision(rid) {
  const title = await askCard({ title: "创建分支稿", msg: "会在本作品中新增一章，可独立编辑，不会覆盖当前主线。", input: "分支章节标题", def: `${$("chapTitle").value || "章节"} · 分支`, okText: "创建" });
  if (!title) return;
  if (!await flushEditorSave()) return;
  const result = await api(`/api/chapters/${currentChapterId}/revisions/${rid}/branch`, { body: { title } });
  currentChapterId = result.id;
  closeRevisions();
  await loadChapters();
  showToast("已创建分支稿", "ok");
}
async function openDiff(rid, allowRestore = false) {
  if (!currentChapterId) return;
  setMicStatus("对比中…");
  try {
    const d = await api(`/api/chapters/${currentChapterId}/revisions/${rid}/diff`, { method: "GET" });
    $("diffTitle").textContent = "版本对比（历史 → 当前）";
    $("diffSub").textContent = `${d.rev_title || "(无标题)"}  →  ${d.cur_title || "(当前)"}  ·  ${new Date(d.rev_at * 1000).toLocaleString()}`;
    $("diffBody").innerHTML = (d.ops || []).map(o => {
      if (o.op === "equal")  return `<div class="d-eq">${esc(o.old)}</div>`;
      if (o.op === "delete") return `<div class="d-del">－ ${esc(o.old)}</div>`;
      if (o.op === "insert") return `<div class="d-ins">＋ ${esc(o.new)}</div>`;
      // replace：先旧（红）后新（绿）
      return `<div class="d-del">－ ${esc(o.old)}</div><div class="d-ins">＋ ${esc(o.new)}</div>`;
    }).join("") || '<div class="empty">无差异</div>';
    pendingRevisionRestore = allowRestore ? rid : null;
    pendingWorkRevisionRestore = null;
    $("diffRestoreBtn").classList.toggle("hidden", !allowRestore);
    $("diffRestoreBtn").textContent = "确认恢复此版本";
    $("diffOverlay").classList.remove("hidden");
  } catch (e) { showToast("对比失败：" + e.message, "err"); }
  setMicStatus("");
}
async function openWorkDiff(rid, allowRestore = false) {
  if (!currentWorkId) return;
  const data = await api(`/api/works/${currentWorkId}/revisions/${rid}/diff`, { method: "GET" });
  $("diffTitle").textContent = "整本版本对比";
  $("diffSub").textContent = `${data.revision.label || "整本快照"} · ${new Date(data.revision.created_at * 1000).toLocaleString()}`;
  $("diffBody").innerHTML = (data.chapters || []).map(item => {
    const fields = (item.changed_fields || []).join("、");
    const label = { added: "新增", removed: "已移除", changed: "有修改", same: "无变化" }[item.status] || item.status;
    return `<div class="work-diff-row work-diff-${esc(item.status)}"><b>${esc(item.title || "无标题")}</b><span>${esc(label)}${fields ? ` · ${esc(fields)}` : ""}</span><small>${item.chars_before} → ${item.chars_now} 字</small></div>`;
  }).join("") || '<div class="empty">无章节变化</div>';
  pendingRevisionRestore = null;
  pendingWorkRevisionRestore = allowRestore ? rid : null;
  $("diffRestoreBtn").classList.toggle("hidden", !allowRestore);
  $("diffRestoreBtn").textContent = "确认恢复整本";
  $("diffOverlay").classList.remove("hidden");
}
function closeDiff() {
  $("diffOverlay").classList.add("hidden");
  pendingRevisionRestore = null;
  pendingWorkRevisionRestore = null;
  $("diffRestoreBtn").classList.add("hidden");
}
async function applyPendingRestore() {
  if (pendingRevisionRestore) {
    const rid = pendingRevisionRestore;
    const c = await api(`/api/chapters/${currentChapterId}/revisions/${rid}/restore`, { method: "POST" });
    $("content").value = c.content || ""; $("chapTitle").value = c.title || "";
    dirty = false; updateSaveStat(""); updateWC();
    closeDiff(); closeRevisions();
    flash("已恢复章节版本");
    return;
  }
  if (pendingWorkRevisionRestore) {
    const rid = pendingWorkRevisionRestore;
    await api(`/api/works/${currentWorkId}/revisions/${rid}/restore`, { body: {} });
    closeDiff(); closeRevisions();
    await loadChapters();
    showToast("已恢复整本版本；恢复前快照已自动保存", "ok");
  }
}
async function recoverFromRevision(rid) {
  if (!currentChapterId) return;
  if (!await flushEditorSave()) return;
  setMicStatus("AI 找回中…");
  try {
    const r = await api("/api/process", {
      body: { mode: "找回", chapter_id: currentChapterId, revision_id: rid, preview: true, preview_operation: "append" },
    });
    closeRevisions();
    openEditReview({ chapter_id: currentChapterId, mode: r.mode || "找回", operation: r.operation || "append",
      result: r.result || "", base_content: $("content").value, old_text: "", start: null, end: null });
  } catch (e) { setMicStatus("出错：" + e.message); }
  finally { setMicStatus(""); }
}

/* ---------- 导出 ---------- */

async function exportChap(fmt) {
  if (!fmt) return;
  try {
    let url, name;
    if (fmt.startsWith("work-")) {
      const f = fmt.slice(5);
      if (!currentWorkId) { showToast("先选作品", "err"); return; }
      url = `/api/works/${currentWorkId}/export?format=${f}`;
      const w = works.find(x => x.id === currentWorkId);
      name = ((w && w.title) || "work") + "." + f;
    } else {
      if (!currentChapterId) { showToast("先选章节", "err"); return; }
      url = `/api/chapters/${currentChapterId}/export?format=${fmt}`;
      name = ($("chapTitle").value || "chapter") + "." + fmt;
    }
    const res = await fetch(url, { headers: { Authorization: "Bearer " + token } });
    if (!res.ok) throw new Error("导出失败");
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    a.click(); URL.revokeObjectURL(a.href);
    showToast("已导出 " + name, "ok");
  } catch (e) { showToast(e.message || "导出失败", "err"); }
}

/* ---------- 大模型设置 ---------- */

let activeModelId = "";
let availableModelIds = [];
let modelRuntimeOptions = {};

function normalizeModelIds(models, active = "") {
  const result = [];
  for (const value of [...(Array.isArray(models) ? models : []), active]) {
    const id = typeof value === "string" ? value.trim() : "";
    if (id && !result.includes(id)) result.push(id);
  }
  return result;
}
function renderTavilyKeyStatus(settings = {}) {
  const userCount = Number(settings.tavily_user_key_count || 0);
  const totalCount = Number(settings.tavily_key_count || 0);
  const masks = Array.isArray(settings.tavily_api_key_masks) ? settings.tavily_api_key_masks : [];
  const source = settings.tavily_key_source;
  const status = $("tavilyKeyStatus");
  if (status) {
    if (source === "user") status.textContent = `已保存 ${userCount} 条${masks.length ? ` · ${masks.join("、")}` : ""}`;
    else if (source === "server") status.textContent = `服务器已配置 ${totalCount} 条`;
    else status.textContent = "未配置";
  }
  const clearRow = $("clearTavilyKeysRow");
  if (clearRow) clearRow.classList.toggle("hidden", !userCount);
}
function toggleTavilyKeyVisibility() {
  const input = $("setTavilyKeys");
  if (!input) return;
  const revealed = input.classList.toggle("revealed");
  const button = $("toggleTavilyKeysBtn");
  if (button) button.title = revealed ? "隐藏输入内容" : "显示输入内容";
}
function renderModelSelectors(settings = {}) {
  if (settings.model_runtime_options && typeof settings.model_runtime_options === "object") {
    modelRuntimeOptions = settings.model_runtime_options;
  }
  activeModelId = (settings.model || activeModelId || "").trim();
  availableModelIds = normalizeModelIds(settings.models, activeModelId);
  if (!activeModelId && availableModelIds.length) activeModelId = availableModelIds[0];
  const options = availableModelIds.map(id => `<option value="${esc(id)}">${esc(id)}</option>`).join("");
  ["modelQuickSwitch", "agentModelSwitch"].forEach(id => {
    const select = $(id);
    if (!select) return;
    select.innerHTML = options || '<option value="">未设置模型</option>';
    select.value = activeModelId;
    select.disabled = availableModelIds.length < 2;
    select.title = activeModelId ? `当前模型：${activeModelId}` : "未设置模型";
  });
  const datalist = $("modelIdOptions");
  if (datalist) datalist.innerHTML = options;
}
function loadModelRuntimeFields(model, fallback = {}) {
  model = (model || "").trim();
  const limits = modelRuntimeOptions[model] || fallback || {};
  if ($("setContextWindowTokens")) {
    $("setContextWindowTokens").value = Number(limits.context_window_tokens || 200000);
  }
  if ($("setWorldStateContentChars")) {
    $("setWorldStateContentChars").value = Number(limits.world_state_content_chars || 30000);
  }
}
async function loadModelSelectors() {
  try {
    renderModelSelectors(await api("/api/settings", { method: "GET" }));
  } catch (e) {
    // The editor remains usable when settings are temporarily unavailable.
  }
}
async function switchActiveModel(model) {
  model = (model || "").trim();
  if (!model || model === activeModelId) return;
  const previous = activeModelId;
  ["modelQuickSwitch", "agentModelSwitch"].forEach(id => { if ($(id)) $(id).disabled = true; });
  try {
    const result = await api("/api/settings/active-model", { body: { model } });
    modelRuntimeOptions[result.model || model] = {
      context_window_tokens: result.context_window_tokens,
      world_state_content_chars: result.world_state_content_chars,
    };
    renderModelSelectors(result);
    if (!$("setOverlay").classList.contains("hidden")) {
      $("setModel").value = result.model || model;
      $("setModels").value = (result.models || []).join("\n");
      loadModelRuntimeFields(result.model || model, result);
    }
    showToast(`已切换模型：${result.model || model}`, "ok");
  } catch (e) {
    renderModelSelectors({ model: previous, models: availableModelIds });
    showToast(e.message || "切换模型失败", "err");
  }
}

async function openSettings() {
  try {
    const s = await api("/api/settings", { method: "GET" });
    $("setBaseUrl").value = s.base_url || "";
    $("setModel").value = s.model || "";
    $("setModels").value = normalizeModelIds(s.models, s.model).join("\n");
    renderModelSelectors(s);
    loadModelRuntimeFields(s.model, s);
    $("setAsrBaseUrl").value = s.asr_base_url || "";
    $("setAsrModel").value = s.asr_model || "whisper-1";
    $("setImageBaseUrl").value = s.image_base_url || "";
    $("setImageModel").value = s.image_model || "";
    $("setImageSize").value = s.image_size || "1024x1024";
    $("setTavilyKeys").value = "";
    $("setTavilyKeys").classList.remove("revealed");
    $("toggleTavilyKeysBtn").title = "显示输入内容";
    $("clearTavilyKeys").checked = false;
    renderTavilyKeyStatus(s);
    // key 不回传明文：已填则用掩码占位提示，留空表示不改
    $("setApiKey").value = s.api_key_masked || "";
    $("setApiKey").placeholder = s.has_key ? `${s.api_key_masked}（留空=不改）` : "sk-…";
    $("setAsrApiKey").value = s.asr_api_key_masked || "";
    $("setAsrApiKey").placeholder = s.asr_has_key ? `${s.asr_api_key_masked}（留空=不改）` : "留空则沿用上方中转站 Key";
    $("setImageApiKey").value = s.image_api_key_masked || "";
    $("setImageApiKey").placeholder = s.image_has_key
      ? `${s.image_api_key_masked}（留空=不改；可沿用文字模型）` : "留空则沿用文字模型 Key";
    $("setVoiceDirect").checked = voiceDirectToModel;
    $("setVoiceAuto").checked = voiceAsrAutoSend;
    $("setMsg").textContent = "";
  } catch (e) { $("setMsg").textContent = e.message; }
  updateVoiceSettingHint();
  $("setOverlay").classList.remove("hidden");
}
function closeSettings() { $("setOverlay").classList.add("hidden"); }
function updateVoiceSettingHint() {
  const direct = $("setVoiceDirect")?.checked;
  const box = $("asrSettings");
  if (box) box.classList.toggle("hidden", !!direct);
  const hint = $("voiceModeHint");
  if (hint) hint.textContent = direct
    ? "录音会直接发送给当前 AI 模型，不调用 Whisper。当前模型必须支持音频输入。"
    : "录音会先调用下方中转站的转写接口，模型 ID 可自由填写；得到文字后再发送给当前写作模型。";
}
async function saveSettings() {
  const base_url = $("setBaseUrl").value.trim();
  const model = $("setModel").value.trim();
  const models = $("setModels").value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
  const context_window_tokens = Number($("setContextWindowTokens").value);
  const world_state_content_chars = Number($("setWorldStateContentChars").value);
  const asr_base_url = $("setAsrBaseUrl").value.trim();
  const asr_model = $("setAsrModel").value.trim();
  let api_key = $("setApiKey").value.trim();
  let asr_api_key = $("setAsrApiKey").value.trim();
  let image_api_key = $("setImageApiKey").value.trim();
  const tavily_api_keys = $("setTavilyKeys").value
    .split(/[\r\n,;]+/).map(value => value.trim()).filter(Boolean);
  // 若用户没动 key 输入框（仍是掩码占位），传空让后端保留旧值
  if (api_key.startsWith("****")) api_key = "";
  if (asr_api_key.startsWith("****")) asr_api_key = "";
  if (image_api_key.startsWith("****")) image_api_key = "";
  voiceDirectToModel = $("setVoiceDirect").checked;
  voiceAsrAutoSend = $("setVoiceAuto").checked;
  localStorage.setItem("voiceDirectToModel", voiceDirectToModel ? "1" : "0");
  localStorage.setItem("voiceAsrAutoSend", voiceAsrAutoSend ? "1" : "0");
  setAgentMic(false);
  try {
    const body = {
      base_url, api_key, model, models, context_window_tokens, world_state_content_chars,
      asr_base_url, asr_api_key, asr_model,
      image_base_url: $("setImageBaseUrl").value.trim(), image_api_key,
      image_model: $("setImageModel").value.trim(), image_size: $("setImageSize").value,
    };
    if (tavily_api_keys.length) body.tavily_api_keys = tavily_api_keys;
    else if ($("clearTavilyKeys").checked) body.tavily_api_keys = [];
    const result = await api("/api/settings", { body });
    renderModelSelectors(result);
    loadModelRuntimeFields(result.model || model, result);
    renderTavilyKeyStatus(result);
    $("setModel").value = result.model || model;
    $("setModels").value = (result.models || models).join("\n");
    $("setTavilyKeys").value = "";
    $("setTavilyKeys").classList.remove("revealed");
    $("clearTavilyKeys").checked = false;
    $("setMsg").textContent = "已保存";
    setTimeout(closeSettings, 600);
  } catch (e) { $("setMsg").textContent = e.message; }
}

/* ---------- 创作总则 ---------- */

let notesWorkId = null;
async function openWorkNotes(wid) {
  notesWorkId = wid;
  try {
    const r = await api(`/api/works/${wid}/notes`, { method: "GET" });
    $("wnText").value = r.notes || "";
    $("wnMsg").textContent = "";
  } catch (e) { $("wnMsg").textContent = e.message; }
  $("wnOverlay").classList.remove("hidden");
}
function closeWorkNotes() { $("wnOverlay").classList.add("hidden"); }
async function saveWorkNotes() {
  try {
    await api(`/api/works/${notesWorkId}/notes`, { method: "PUT", body: { notes: $("wnText").value } });
    $("wnMsg").textContent = "已保存";
    setTimeout(closeWorkNotes, 600);
  } catch (e) { $("wnMsg").textContent = e.message; }
}

/* ---------- 可视化大纲 / 情节分支沙盘 ---------- */

let sandboxList = [];
let currentSandbox = null;
let selectedSandboxNodeId = null;
let sandboxSaveTimer = null;
let sandboxDrag = null;
let sandboxAiCandidates = [];

function sandboxNodeId(prefix = "plot") {
  return `${prefix}-${globalThis.crypto?.randomUUID?.() || (Date.now().toString(36) + Math.random().toString(36).slice(2))}`;
}
function sandboxData() {
  if (!currentSandbox.data || !Array.isArray(currentSandbox.data.nodes) || !Array.isArray(currentSandbox.data.edges)) {
    currentSandbox.data = { nodes: [], edges: [] };
  }
  return currentSandbox.data;
}
async function openOutlineSandbox() {
  if (!currentWorkId) { showToast("先选一个作品", "err"); return; }
  if (!await flushEditorSave()) return;
  $("outlineOverlay").classList.remove("hidden");
  try {
    sandboxList = await api(`/api/works/${currentWorkId}/sandboxes`, { method: "GET" });
    if (!sandboxList.length) {
      currentSandbox = await api(`/api/works/${currentWorkId}/sandboxes`, { body: { name: "主线推演", data: { nodes: [], edges: [] } } });
      sandboxList = [{ id: currentSandbox.id, name: currentSandbox.name, node_count: 0, edge_count: 0 }];
      syncSandboxChapters(false);
      await saveOutlineSandbox(false);
    } else await loadOutlineSandbox(sandboxList[0].id);
    renderSandboxList();
  } catch (e) { showToast("沙盘加载失败：" + e.message, "err"); }
}
async function closeOutlineSandbox() {
  clearTimeout(sandboxSaveTimer);
  if (currentSandbox) await saveOutlineSandbox(false).catch(() => {});
  $("outlineOverlay").classList.add("hidden");
  sandboxDrag = null;
}
async function loadOutlineSandbox(sid) {
  currentSandbox = await api(`/api/sandboxes/${sid}`, { method: "GET" });
  selectedSandboxNodeId = null; sandboxAiCandidates = [];
  renderSandboxList(); renderOutlineSandbox(); renderSandboxInspector();
}
function renderSandboxList() {
  const host = $("sandboxList"); if (!host) return;
  host.innerHTML = sandboxList.map(item => `
    <div class="sandbox-list-item ${currentSandbox?.id === item.id ? "active" : ""}">
      <button class="sandbox-list-main" data-sandbox-open="${item.id}"><span>${esc(item.name)}</span><small>${item.node_count || 0} 节点</small></button>
      <span class="sandbox-list-actions">
        <button data-sandbox-rename="${item.id}" title="重命名">✎</button>
        <button data-sandbox-delete="${item.id}" title="删除">×</button>
      </span>
    </div>`).join("") || '<div class="empty">暂无沙盘</div>';
  host.querySelectorAll("[data-sandbox-open]").forEach(button => button.addEventListener("click", () => loadOutlineSandbox(+button.dataset.sandboxOpen)));
  host.querySelectorAll("[data-sandbox-rename]").forEach(button => button.addEventListener("click", () => renameOutlineSandbox(+button.dataset.sandboxRename)));
  host.querySelectorAll("[data-sandbox-delete]").forEach(button => button.addEventListener("click", () => deleteOutlineSandbox(+button.dataset.sandboxDelete)));
}
async function newOutlineSandbox() {
  const name = await askCard({ title: "新建情节沙盘", input: "沙盘名称", def: `情节假设 ${sandboxList.length + 1}`, okText: "新建" });
  if (!name) return;
  const created = await api(`/api/works/${currentWorkId}/sandboxes`, { body: { name, data: { nodes: [], edges: [] } } });
  sandboxList.unshift({ id: created.id, name: created.name, node_count: 0, edge_count: 0 });
  await loadOutlineSandbox(created.id);
}
async function renameOutlineSandbox(sid) {
  const row = sandboxList.find(item => item.id === sid); if (!row) return;
  const name = await askCard({ title: "重命名沙盘", input: "沙盘名称", def: row.name, okText: "保存" });
  if (!name || name === row.name) return;
  try {
    const saved = await api(`/api/sandboxes/${sid}`, { method: "PUT", body: { name } });
    row.name = saved.name;
    if (currentSandbox?.id === sid) currentSandbox.name = saved.name;
    renderSandboxList(); showToast("沙盘已重命名", "ok");
  } catch (e) { showToast("重命名失败：" + e.message, "err"); }
}
async function deleteOutlineSandbox(sid) {
  const row = sandboxList.find(item => item.id === sid); if (!row) return;
  if (!await askCard({ title: `删除“${row.name}”？`, msg: "该沙盘中的情节推演会被删除，不影响已经采纳的正文章节。", okText: "删除", danger: true })) return;
  try {
    await api(`/api/sandboxes/${sid}`, { method: "DELETE" });
    sandboxList = sandboxList.filter(item => item.id !== sid);
    if (currentSandbox?.id === sid) {
      currentSandbox = null; selectedSandboxNodeId = null;
      if (sandboxList.length) await loadOutlineSandbox(sandboxList[0].id);
      else {
        currentSandbox = await api(`/api/works/${currentWorkId}/sandboxes`, { body: { name: "主线推演", data: { nodes: [], edges: [] } } });
        sandboxList = [{ id: currentSandbox.id, name: currentSandbox.name, node_count: 0, edge_count: 0 }];
        renderOutlineSandbox(); renderSandboxInspector();
      }
    }
    renderSandboxList(); showToast("沙盘已删除", "ok");
  } catch (e) { showToast("删除失败：" + e.message, "err"); }
}
function scheduleSandboxSave() {
  clearTimeout(sandboxSaveTimer);
  sandboxSaveTimer = setTimeout(() => saveOutlineSandbox(false), 900);
}
async function saveOutlineSandbox(feedback = true) {
  if (!currentSandbox) return;
  clearTimeout(sandboxSaveTimer);
  const button = $("sandboxSaveBtn");
  if (feedback) busy(button, true, "保存中");
  try {
    const saved = await api(`/api/sandboxes/${currentSandbox.id}`, { method: "PUT", body: { name: currentSandbox.name, data: sandboxData() } });
    currentSandbox = saved;
    const row = sandboxList.find(item => item.id === saved.id);
    if (row) Object.assign(row, { name: saved.name, node_count: saved.data.nodes.length, edge_count: saved.data.edges.length });
    renderSandboxList();
    if (feedback) showToast("沙盘已保存", "ok");
  } catch (e) { if (feedback) showToast("沙盘保存失败：" + e.message, "err"); }
  finally { if (feedback) busy(button, false, "保存"); }
}
function sandboxKindLabel(kind) {
  return ({ volume: "卷", chapter: "章", plot: "情节", choice: "选择", ending: "结局" })[kind] || "情节";
}
function sandboxDirectionClass(direction) {
  return ({ "主线": "dir-main", "推进": "dir-advance", "发散": "dir-diverge", "收束": "dir-converge" })[direction] || "";
}
function sandboxHiddenNodeIds() {
  const data = sandboxData(), children = new Map();
  data.edges.forEach(edge => {
    if (!children.has(edge.from)) children.set(edge.from, []);
    children.get(edge.from).push(edge.to);
  });
  const hidden = new Set();
  function hideDescendants(nodeId, trail = new Set([nodeId])) {
    (children.get(nodeId) || []).forEach(childId => {
      if (trail.has(childId)) return;
      hidden.add(childId);
      const nextTrail = new Set(trail); nextTrail.add(childId);
      hideDescendants(childId, nextTrail);
    });
  }
  data.nodes.filter(node => node.collapsed).forEach(node => hideDescendants(node.id));
  return hidden;
}
function renderOutlineSandbox() {
  if (!currentSandbox) return;
  const data = sandboxData(), nodesHost = $("sandboxNodes"), edgeHost = $("sandboxEdges"), world = $("sandboxWorld");
  const hidden = sandboxHiddenNodeIds();
  const visibleNodes = data.nodes.filter(node => !hidden.has(node.id));
  const parents = new Set(data.edges.map(edge => edge.from));
  const maxX = Math.max(2200, ...data.nodes.map(node => (+node.x || 0) + 360));
  const maxY = Math.max(1400, ...data.nodes.map(node => (+node.y || 0) + 260));
  world.style.width = `${maxX}px`; world.style.height = `${maxY}px`;
  edgeHost.setAttribute("width", maxX); edgeHost.setAttribute("height", maxY);
  nodesHost.innerHTML = visibleNodes.map(node => `
    <article class="sandbox-node kind-${esc(node.kind)} ${sandboxDirectionClass(node.direction)} ${node.id === selectedSandboxNodeId ? "selected" : ""}"
      data-node-id="${esc(node.id)}" style="left:${+node.x || 0}px;top:${+node.y || 0}px">
      <div class="sandbox-node-dir">${esc(node.direction || sandboxKindLabel(node.kind))}</div>
      <div class="sandbox-node-title">${esc(node.title)}</div>
      <div class="sandbox-node-summary">${esc(node.summary || "点击填写这个情节点会发生什么")}</div>
      <div class="sandbox-node-meta">${node.chapter_id ? "已关联正文章节" : node.plan_node_id ? "已采纳为规划" : "候选未来"}${node.characters ? ` · ${esc(node.characters)}` : ""}</div>
      ${parents.has(node.id) ? `<button class="sandbox-node-collapse" data-collapse-node="${esc(node.id)}" title="${node.collapsed ? "展开子节点" : "收起子节点"}">${node.collapsed ? "+" : "−"}</button>` : ""}
    </article>`).join("");
  nodesHost.querySelectorAll(".sandbox-node").forEach(element => {
    element.addEventListener("pointerdown", startSandboxDrag);
    element.addEventListener("click", () => selectSandboxNode(element.dataset.nodeId));
  });
  nodesHost.querySelectorAll("[data-collapse-node]").forEach(button => {
    button.addEventListener("pointerdown", event => event.stopPropagation());
    button.addEventListener("click", event => { event.stopPropagation(); toggleSandboxCollapse(button.dataset.collapseNode); });
  });
  renderSandboxEdges();
}
function renderSandboxEdges() {
  if (!currentSandbox) return;
  const data = sandboxData(), byId = new Map(data.nodes.map(node => [node.id, node]));
  const hidden = sandboxHiddenNodeIds();
  $("sandboxEdges").innerHTML = data.edges.map(edge => {
    const from = byId.get(edge.from), to = byId.get(edge.to); if (!from || !to || hidden.has(edge.from) || hidden.has(edge.to)) return "";
    const x1 = (+from.x || 0) + 220, y1 = (+from.y || 0) + 48, x2 = +to.x || 0, y2 = (+to.y || 0) + 48;
    const bend = Math.max(50, Math.abs(x2 - x1) * .45);
    const path = `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`;
    const direction = ["主线", "推进", "发散", "收束"].includes(edge.label) ? edge.label : to.direction;
    return `<path class="sandbox-edge ${sandboxDirectionClass(direction)}" d="${path}"></path>${edge.label ? `<text class="sandbox-edge-label" x="${(x1 + x2) / 2}" y="${(y1 + y2) / 2 - 6}">${esc(edge.label)}</text>` : ""}`;
  }).join("");
}
function toggleSandboxCollapse(nodeId) {
  const node = sandboxData().nodes.find(item => item.id === nodeId); if (!node) return;
  node.collapsed = !node.collapsed; renderOutlineSandbox(); scheduleSandboxSave();
}
function selectSandboxNode(nodeId) {
  selectedSandboxNodeId = nodeId; sandboxAiCandidates = [];
  $("sandboxNodes").querySelectorAll(".sandbox-node").forEach(el => el.classList.toggle("selected", el.dataset.nodeId === nodeId));
  renderSandboxInspector();
}
function selectedSandboxNode() { return sandboxData().nodes.find(node => node.id === selectedSandboxNodeId); }
function renderSandboxInspector() {
  const node = currentSandbox && selectedSandboxNode();
  $("sandboxEmptyInspector").classList.toggle("hidden", !!node);
  $("sandboxNodeInspector").classList.toggle("hidden", !node);
  if (!node) return;
  $("sandboxNodeBadge").textContent = node.direction || sandboxKindLabel(node.kind);
  $("sandboxNodeTitle").value = node.title || ""; $("sandboxNodeSummary").value = node.summary || "";
  $("sandboxNodeKind").value = node.kind || "plot"; $("sandboxNodeDirection").value = node.direction || "";
  $("sandboxNodeContextChapter").innerHTML = `<option value="">故事开头</option>` + chapters.map(chapter =>
    `<option value="${chapter.id}" ${node.context_chapter_id === chapter.id ? "selected" : ""}>截至第${chapter.ord}章《${esc(chapter.title || "未命名")}》</option>`).join("");
  $("sandboxNodeCharacters").value = node.characters || "";
  $("sandboxOpenChapterBtn").classList.toggle("hidden", !node.chapter_id);
  $("sandboxOpenPlanBtn").classList.toggle("hidden", !node.plan_node_id);
  $("sandboxAdoptMode").value = "plan";
  $("sandboxAdoptBtn").classList.toggle("hidden", !!node.chapter_id && !!node.plan_node_id); renderSandboxCandidates();
}
function updateSelectedSandboxNode() {
  const node = selectedSandboxNode(); if (!node) return;
  node.title = $("sandboxNodeTitle").value; node.summary = $("sandboxNodeSummary").value;
  node.kind = $("sandboxNodeKind").value; node.direction = $("sandboxNodeDirection").value;
  node.context_chapter_id = +$("sandboxNodeContextChapter").value || null;
  node.characters = $("sandboxNodeCharacters").value;
  const incoming = sandboxData().edges.find(edge => edge.to === node.id);
  if (incoming && node.direction) incoming.label = node.direction;
  $("sandboxNodeBadge").textContent = node.direction || sandboxKindLabel(node.kind);
  const el = [...$("sandboxNodes").querySelectorAll(".sandbox-node")].find(item => item.dataset.nodeId === node.id);
  if (el) {
    el.className = `sandbox-node kind-${node.kind} ${sandboxDirectionClass(node.direction)} selected`;
    el.querySelector(".sandbox-node-dir").textContent = node.direction || sandboxKindLabel(node.kind);
    el.querySelector(".sandbox-node-title").textContent = node.title || "未命名情节点";
    el.querySelector(".sandbox-node-summary").textContent = node.summary || "点击填写这个情节点会发生什么";
    el.querySelector(".sandbox-node-meta").textContent = `${node.chapter_id ? "已关联正文章节" : node.plan_node_id ? "已采纳为规划" : "候选未来"}${node.characters ? ` · ${node.characters}` : ""}`;
  }
  renderSandboxEdges();
  scheduleSandboxSave();
}

function addSandboxRoot() {
  if (!currentSandbox) return;
  const viewport = $("sandboxViewport");
  const node = { id: sandboxNodeId("root"), title: "新的情节起点", summary: "", kind: "plot", direction: "",
    characters: "", chapter_id: null, context_chapter_id: null,
    x: viewport.scrollLeft + 70, y: viewport.scrollTop + 80, collapsed: false };
  sandboxData().nodes.push(node); renderOutlineSandbox(); selectSandboxNode(node.id); scheduleSandboxSave();
}
function addSandboxBranch(candidate = null) {
  const parent = selectedSandboxNode();
  if (!parent) { showToast("先选择一个父节点", "err"); return; }
  parent.collapsed = false;
  const siblings = sandboxData().edges.filter(edge => edge.from === parent.id).length;
  const node = { id: sandboxNodeId("branch"), title: candidate?.title || "新的分支", summary: candidate?.summary || "",
    kind: "choice", direction: candidate?.direction || "发散", characters: candidate?.characters || "",
    chapter_id: null, context_chapter_id: parent.context_chapter_id !== undefined
      ? parent.context_chapter_id : (parent.chapter_id || null),
    x: (+parent.x || 0) + 290, y: (+parent.y || 0) + siblings * 135, collapsed: false };
  sandboxData().nodes.push(node);
  sandboxData().edges.push({ id: sandboxNodeId("edge"), from: parent.id, to: node.id, label: node.direction });
  sandboxAiCandidates = []; renderOutlineSandbox(); selectSandboxNode(node.id); scheduleSandboxSave();
  return node;
}
async function deleteSelectedSandboxNode() {
  const node = selectedSandboxNode(); if (!node) return;
  if (!await askCard({ title: `删除“${node.title || "未命名节点"}”？`, msg: "只删除这个节点；它的子节点会保留为新的起点。", okText: "删除", danger: true })) return;
  sandboxData().nodes = sandboxData().nodes.filter(item => item.id !== node.id);
  sandboxData().edges = sandboxData().edges.filter(edge => edge.from !== node.id && edge.to !== node.id);
  selectedSandboxNodeId = null; renderOutlineSandbox(); renderSandboxInspector(); scheduleSandboxSave();
}
function syncSandboxChapters(feedback = true) {
  if (!currentSandbox) return;
  const data = sandboxData(), byChapter = new Map(data.nodes.filter(node => node.chapter_id).map(node => [node.chapter_id, node]));
  const mains = chapters.filter(chapter => !chapter.branch_of_chapter_id);
  let previous = null, added = 0;
  mains.forEach((chapter, index) => {
    let node = byChapter.get(chapter.id);
    if (!node) {
      node = { id: `chapter-${chapter.id}`, title: chapter.title || `第${chapter.ord}章`, summary: chapter.outcome_summary || chapter.workflow_goal || "",
        kind: "chapter", direction: "推进", characters: "", chapter_id: chapter.id, x: 70 + index * 280, y: 90, collapsed: false };
      data.nodes.push(node); byChapter.set(chapter.id, node); added++;
    } else node.title = chapter.title || node.title;
    if (previous && !data.edges.some(edge => edge.from === previous.id && edge.to === node.id)) {
      data.edges.push({ id: sandboxNodeId("edge"), from: previous.id, to: node.id, label: "主线" });
    }
    previous = node;
  });
  chapters.filter(chapter => chapter.branch_of_chapter_id).forEach((chapter, index) => {
    if (byChapter.has(chapter.id)) return;
    const parent = byChapter.get(chapter.branch_of_chapter_id);
    const node = { id: `chapter-${chapter.id}`, title: chapter.title || "章节分支", summary: chapter.outcome_summary || "",
      kind: "chapter", direction: "发散", characters: "", chapter_id: chapter.id,
      x: (parent?.x || 70) + 280, y: (parent?.y || 90) + 150 + index * 120, collapsed: false };
    data.nodes.push(node); byChapter.set(chapter.id, node); added++;
    if (parent) data.edges.push({ id: sandboxNodeId("edge"), from: parent.id, to: node.id, label: "正文分支" });
  });
  if (data.nodes.length) autoLayoutSandbox(false); else renderOutlineSandbox();
  scheduleSandboxSave();
  if (feedback) showToast(added ? `已同步 ${added} 个章节节点` : "章节节点已是最新", added ? "ok" : "");
}
function autoLayoutSandbox(feedback = true) {
  if (!currentSandbox) return;
  const data = sandboxData(), byId = new Map(data.nodes.map(node => [node.id, node]));
  const children = new Map(data.nodes.map(node => [node.id, []])), incoming = new Set();
  data.edges.forEach(edge => { if (byId.has(edge.from) && byId.has(edge.to)) { children.get(edge.from).push(edge.to); incoming.add(edge.to); } });
  const roots = data.nodes.filter(node => !incoming.has(node.id)); let row = 0; const placed = new Set();
  function place(node, depth) {
    if (!node || placed.has(node.id)) return;
    placed.add(node.id); node.x = 70 + depth * 290; node.y = 70 + row * 135; row++;
    (children.get(node.id) || []).forEach(id => place(byId.get(id), depth + 1));
  }
  roots.forEach(node => place(node, 0)); data.nodes.forEach(node => place(node, 0));
  renderOutlineSandbox(); scheduleSandboxSave(); if (feedback) showToast("布局已整理", "ok");
}
function startSandboxDrag(event) {
  if (event.button !== 0) return;
  const element = event.currentTarget, node = sandboxData().nodes.find(item => item.id === element.dataset.nodeId);
  if (!node) return;
  selectSandboxNode(node.id);
  sandboxDrag = { element, node, pointerId: event.pointerId, startX: event.clientX, startY: event.clientY,
    x: +node.x || 0, y: +node.y || 0 };
  element.setPointerCapture?.(event.pointerId);
}
document.addEventListener("pointermove", event => {
  if (!sandboxDrag || event.pointerId !== sandboxDrag.pointerId) return;
  sandboxDrag.node.x = Math.max(0, sandboxDrag.x + event.clientX - sandboxDrag.startX);
  sandboxDrag.node.y = Math.max(0, sandboxDrag.y + event.clientY - sandboxDrag.startY);
  sandboxDrag.element.style.left = `${sandboxDrag.node.x}px`; sandboxDrag.element.style.top = `${sandboxDrag.node.y}px`;
  renderSandboxEdges();
});
document.addEventListener("pointerup", event => {
  if (!sandboxDrag || event.pointerId !== sandboxDrag.pointerId) return;
  sandboxDrag = null; scheduleSandboxSave();
});
async function expandSandboxNode() {
  const node = selectedSandboxNode(); if (!node) return;
  const button = $("sandboxAiBtn"); busy(button, true, "展开中");
  try {
    const result = await api(`/api/sandboxes/${currentSandbox.id}/expand`, { body: { node_id: node.id, instruction: $("sandboxAiInstruction").value.trim() } });
    sandboxAiCandidates = result.candidates || []; renderSandboxCandidates();
  } catch (e) { showToast(e.message, "err"); }
  finally { busy(button, false, "AI 展开三个候选"); }
}
function renderSandboxCandidates() {
  const host = $("sandboxCandidates"); if (!host) return;
  host.innerHTML = sandboxAiCandidates.map((item, index) => `
    <div class="sandbox-candidate" data-direction="${esc(item.direction)}"><b>${esc(item.direction)} · ${esc(item.title)}</b><p>${esc(item.summary)}</p><div class="set-actions"><button data-candidate-index="${index}">保留</button><button class="ic" data-candidate-expand="${index}">保留并继续展开</button></div></div>`).join("");
  host.querySelectorAll("[data-candidate-index]").forEach(button => button.addEventListener("click", () => addSandboxBranch(sandboxAiCandidates[+button.dataset.candidateIndex])));
  host.querySelectorAll("[data-candidate-expand]").forEach(button => button.addEventListener("click", async () => {
    const candidate = sandboxAiCandidates[+button.dataset.candidateExpand];
    if (!candidate || !addSandboxBranch(candidate)) return;
    await expandSandboxNode();
  }));
}
function sandboxExportMarkdown(rootId = null) {
  const data = sandboxData(), byId = new Map(data.nodes.map(node => [node.id, node]));
  const children = new Map(data.nodes.map(node => [node.id, []])), incoming = new Set();
  data.edges.forEach(edge => {
    if (!byId.has(edge.from) || !byId.has(edge.to)) return;
    children.get(edge.from).push({ node: byId.get(edge.to), label: edge.label || "" }); incoming.add(edge.to);
  });
  const roots = rootId && byId.has(rootId) ? [byId.get(rootId)] : data.nodes.filter(node => !incoming.has(node.id));
  const lines = [`# ${currentSandbox?.name || "情节沙盘"}`, ""]; const seen = new Set();
  function append(node, depth, edgeLabel = "") {
    if (!node || seen.has(node.id)) return;
    seen.add(node.id);
    const prefix = "  ".repeat(depth);
    const direction = node.direction || edgeLabel || sandboxKindLabel(node.kind);
    lines.push(`${prefix}- **${node.title || "未命名节点"}**（${direction}）`);
    if (node.summary) lines.push(`${prefix}  ${String(node.summary).replace(/\r?\n/g, `\n${prefix}  `)}`);
    if (node.characters) lines.push(`${prefix}  - 角色：${node.characters}`);
    if (node.chapter_id) lines.push(`${prefix}  - 正文章节 ID：${node.chapter_id}`);
    (children.get(node.id) || []).forEach(child => append(child.node, depth + 1, child.label));
  }
  roots.forEach(root => append(root, 0));
  if (!rootId) data.nodes.forEach(node => append(node, 0));
  return lines.join("\n");
}
async function exportSandboxTree(rootId = null) {
  if (!currentSandbox) return;
  const markdown = sandboxExportMarkdown(rootId);
  const base = (rootId ? selectedSandboxNode()?.title : currentSandbox.name) || "情节沙盘";
  const filename = `${base.replace(/[\\/:*?"<>|]+/g, "-")}.md`;
  const url = URL.createObjectURL(new Blob([markdown], { type: "text/markdown;charset=utf-8" }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = filename; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  await navigator.clipboard?.writeText(markdown).catch(() => {});
  showToast(rootId ? "此分支已导出，并复制为 Markdown" : "整棵沙盘已导出，并复制为 Markdown", "ok");
}
async function adoptSandboxNode() {
  const node = selectedSandboxNode(); if (!node) return;
  try {
    const mode = $("sandboxAdoptMode").value;
    const result = await api(`/api/sandboxes/${currentSandbox.id}/nodes/${encodeURIComponent(node.id)}/adopt`, { body: {
      mode, target_plan_id: node.plan_node_id || null,
    }});
    node.plan_node_id = result.plan?.id || node.plan_node_id;
    if (result.chapter?.id) { node.chapter_id = result.chapter.id; node.kind = "chapter"; await loadChapters(); }
    await saveOutlineSandbox(false); renderOutlineSandbox(); renderSandboxInspector();
    showToast(result.chapter ? "已采纳为正式规划并创建章节" : "已采纳为正式剧情规划", "ok");
  } catch (e) { showToast(e.message, "err"); }
}
async function openSandboxPlan() {
  const node = selectedSandboxNode(); if (!node?.plan_node_id) return;
  const planId = node.plan_node_id;
  await closeOutlineSandbox();
  if (!$("app").classList.contains("production-open")) await openProductionCanvas();
  await setProductionSection("plan");
  inspectStoryPlan(planId);
}
async function openSandboxChapter() {
  const node = selectedSandboxNode(); if (!node?.chapter_id) return;
  const chapterId = node.chapter_id;
  await closeOutlineSandbox(); await selectChapter(chapterId);
}

/* ---------- 拆书引擎 ---------- */

let disassemblyJobs = [];
let currentDisassemblyJob = null;
let disassemblyRunning = false;

function disassemblyStatusLabel(status) {
  return ({ ready: "已切章，等待开始", running: "分析中", paused: "已暂停", partial: "已停止并保存", completed: "已完成", cancelled: "已取消" })[status] || status;
}
function updateDisassemblyMode() {
  $("disassemblyTitleRow").classList.toggle("hidden", $("disassemblyMode").value !== "new");
}
function previewDisassemblyFile() {
  const file = $("disassemblyFile").files?.[0];
  $("disassemblyFileMeta").textContent = file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB` : "选择文件后自动识别章节；开始前会显示切章结果。";
  if (file && !$("disassemblyTitle").value) $("disassemblyTitle").value = file.name.replace(/\.[^.]+$/, "");
}
async function openDisassembly() {
  $("disassemblyOverlay").classList.remove("hidden");
  $("disassemblyMode").value = currentWorkId ? "merge" : "new";
  updateDisassemblyMode();
  try {
    disassemblyJobs = await api("/api/disassembly/jobs", { method: "GET" });
    renderDisassemblyJobSwitch();
    if (disassemblyJobs.length) await switchDisassemblyJob(disassemblyJobs[0].id);
    else renderDisassemblyJob();
  } catch (e) { $("disassemblyPrepareMsg").textContent = e.message; }
}
async function closeDisassembly() {
  if (disassemblyRunning && currentDisassemblyJob) {
    disassemblyRunning = false;
    await api(`/api/disassembly/jobs/${currentDisassemblyJob.id}/pause`, { body: {} }).catch(() => {});
  }
  $("disassemblyOverlay").classList.add("hidden");
}
function renderDisassemblyJobSwitch() {
  $("disassemblyJobSwitch").innerHTML = disassemblyJobs.length
    ? disassemblyJobs.map(job => `<option value="${job.id}" ${currentDisassemblyJob?.id === job.id ? "selected" : ""}>${esc(job.source_name)} · ${esc(disassemblyStatusLabel(job.status))}</option>`).join("")
    : '<option value="">暂无任务</option>';
}
async function switchDisassemblyJob(jobId) {
  $("disassemblyExtractionSummary").classList.add("hidden");
  $("disassemblyExtractionSummary").textContent = "";
  if (!jobId) { currentDisassemblyJob = null; renderDisassemblyJob(); return; }
  disassemblyRunning = false;
  try {
    currentDisassemblyJob = await api(`/api/disassembly/jobs/${+jobId}`, { method: "GET" });
    renderDisassemblyJobSwitch(); renderDisassemblyJob();
  } catch (e) { showToast(e.message, "err"); }
}
function renderDisassemblyJob() {
  const job = currentDisassemblyJob;
  $("disassemblyEmpty").classList.toggle("hidden", !!job);
  $("disassemblyJob").classList.toggle("hidden", !job);
  if (!job) return;
  $("disassemblyJobName").textContent = job.source_name;
  $("disassemblyJobStatus").textContent = `${disassemblyStatusLabel(job.status)} · ${job.processed_chapters}/${job.total_chapters} 章`;
  $("disassemblyProgressBar").style.width = `${job.total_chapters ? Math.round(job.processed_chapters / job.total_chapters * 100) : 0}%`;
  const stats = job.stats || {};
  $("disassemblyStats").innerHTML = [["人物", stats.characters], ["地点", stats.locations], ["物品", stats.items], ["组织", stats.organizations], ["关系", stats.relations]]
    .map(([label, value]) => `<span class="disassembly-stat">${label} ${value || 0}</span>`).join("");
  const terminal = ["completed", "partial", "cancelled"].includes(job.status);
  $("disassemblyStartBtn").disabled = terminal || disassemblyRunning;
  $("disassemblyPauseBtn").disabled = !disassemblyRunning;
  $("disassemblyFinishBtn").disabled = terminal;
  $("disassemblyChapters").innerHTML = (job.chapters || []).map(chapter => {
    const summary = chapter.result?.summary || chapter.excerpt || "";
    const styleNotes = chapter.result?.style_notes || "";
    const status = chapter.status === "done" ? "已分析" : chapter.status === "error" ? "出错" : chapter.status === "running" ? "分析中" : "等待";
    return `<div class="disassembly-chapter ${esc(chapter.status)}">
      <div class="disassembly-chapter-head"><b>${chapter.ord}. ${esc(chapter.title)}</b><span>${status} · ${chapter.chars || 0} 字</span></div>
      ${summary ? `<p>${esc(summary.slice(0, 280))}${summary.length > 280 ? "…" : ""}</p>` : ""}
      ${styleNotes ? `<p>文风 / 技法：${esc(styleNotes.slice(0, 180))}${styleNotes.length > 180 ? "…" : ""}</p>` : ""}
      ${chapter.error ? `<p class="error-text">${esc(chapter.error)} <button class="ic" data-retry-chapter="${chapter.id}">重试本章</button></p>` : ""}
    </div>`;
  }).join("");
  $("disassemblyChapters").querySelectorAll("[data-retry-chapter]").forEach(button => button.addEventListener("click", () => retryDisassemblyChapter(+button.dataset.retryChapter)));
}
async function prepareDisassembly() {
  const file = $("disassemblyFile").files?.[0];
  if (!file) { $("disassemblyPrepareMsg").textContent = "请先选择书稿文件"; return; }
  const mode = $("disassemblyMode").value;
  if (mode === "merge" && !currentWorkId) { $("disassemblyPrepareMsg").textContent = "当前没有可合入的作品，请选择新建作品"; return; }
  const button = $("disassemblyPrepareBtn"); busy(button, true, "切章中");
  $("disassemblyPrepareMsg").textContent = "正在读取并自动切章…";
  try {
    const headers = {
      "Content-Type": file.type || "application/octet-stream", "X-File-Name": encodeURIComponent(file.name),
      "X-Disassembly-Mode": mode, "X-Disassembly-Strategy": $("disassemblyStrategy").value,
      "X-Target-Work-Id": String(currentWorkId || ""), "X-Project-Title": encodeURIComponent($("disassemblyTitle").value.trim()),
      ...(token ? { Authorization: "Bearer " + token } : {}),
    };
    const response = await fetch("/api/disassembly/prepare", { method: "POST", headers, body: file });
    if (!response.ok) throw new Error(await responseError(response));
    const prepared = await response.json();
    currentDisassemblyJob = prepared.job;
    disassemblyJobs = [prepared.job, ...disassemblyJobs.filter(job => job.id !== prepared.job.id)];
    if (prepared.created_work) {
      await loadWorks();
      if (currentWorkId !== prepared.created_work.id) await selectWork(prepared.created_work.id);
    } else await loadChapters();
    renderDisassemblyJobSwitch(); renderDisassemblyJob();
    $("disassemblyPrepareMsg").textContent = `已识别 ${prepared.chapter_count} 章、${prepared.source_chars} 字；请检查右侧切章结果后开始分析。`;
  } catch (e) { $("disassemblyPrepareMsg").textContent = e.message; }
  finally { busy(button, false, "上传并自动切章"); }
}
async function startDisassembly() {
  if (!currentDisassemblyJob || disassemblyRunning) return;
  disassemblyRunning = true; renderDisassemblyJob();
  try {
    while (disassemblyRunning) {
      const step = await api(`/api/disassembly/jobs/${currentDisassemblyJob.id}/step`, { body: {} });
      currentDisassemblyJob = step.job;
      const row = disassemblyJobs.find(job => job.id === currentDisassemblyJob.id);
      if (row) Object.assign(row, currentDisassemblyJob);
      renderDisassemblyJobSwitch(); renderDisassemblyJob();
      if (!step.ok) { showToast(`拆到《${(currentDisassemblyJob.chapters || []).find(item => item.id === step.chapter_id)?.title || "本章"}》时暂停：${step.error}`, "err"); break; }
      if (["completed", "partial", "cancelled"].includes(currentDisassemblyJob.status)) break;
      await new Promise(resolve => setTimeout(resolve, 30));
    }
  } catch (e) { showToast("拆书中断：" + e.message, "err"); }
  finally {
    if (!disassemblyRunning && currentDisassemblyJob && currentDisassemblyJob.status === "running") {
      currentDisassemblyJob = await api(`/api/disassembly/jobs/${currentDisassemblyJob.id}/pause`, { body: {} }).catch(() => currentDisassemblyJob);
    }
    disassemblyRunning = false; renderDisassemblyJob();
    if (currentDisassemblyJob?.target_work_id === currentWorkId) await Promise.all([loadChapters(), loadWikiEntities()]);
    if (currentDisassemblyJob?.status === "completed") showToast("拆书完成，设定与章节已写入作品", "ok");
  }
}
async function pauseDisassembly() {
  if (!currentDisassemblyJob) return;
  disassemblyRunning = false;
  currentDisassemblyJob = await api(`/api/disassembly/jobs/${currentDisassemblyJob.id}/pause`, { body: {} });
  renderDisassemblyJob(); showToast("已暂停，当前完成的章节都已保存", "ok");
}
async function finishDisassembly() {
  if (!currentDisassemblyJob) return;
  disassemblyRunning = false;
  currentDisassemblyJob = await api(`/api/disassembly/jobs/${currentDisassemblyJob.id}/finish`, { body: {} });
  renderDisassemblyJob();
  if (currentDisassemblyJob.target_work_id === currentWorkId) await Promise.all([loadChapters(), loadWikiEntities()]);
  showToast("已停止；现有拆书结果已保留", "ok");
}
async function retryDisassemblyChapter(chapterId) {
  if (!currentDisassemblyJob) return;
  currentDisassemblyJob = await api(`/api/disassembly/jobs/${currentDisassemblyJob.id}/chapters/${chapterId}/retry`, { body: {} });
  renderDisassemblyJob(); showToast("已放回待分析队列", "ok");
}

/* ---------- 创作资料中心：事实、参考、风格、桥段与附件分层融合 ---------- */

let materialSettingsTimer = null;
function materialSettingsValue() {
  return {
    use_story_memory: $("materialUseMemory").checked,
    use_reference_projects: $("materialUseReferences").checked,
    use_style_profile: $("materialUseStyle").checked,
    use_inspirations: $("materialUseInspirations").checked,
    use_reference_documents: $("materialUseDocuments").checked,
    style_strength: $("materialStyleStrength").value,
  };
}
async function openMaterialHub() {
  if (!currentWorkId) { showToast("先选择一个作品", "err"); return; }
  $("materialOverlay").classList.remove("hidden");
  await loadMaterialHub();
}
function closeMaterialHub() { $("materialOverlay").classList.add("hidden"); }
async function loadMaterialHub() {
  if (!currentWorkId) return;
  try {
    materialData = await api(`/api/works/${currentWorkId}/materials`, { method: "GET" });
    renderMaterialHub();
  } catch (e) { showToast("资料中心加载失败：" + e.message, "err"); }
}
function renderMaterialProfile(styleProfile) {
  const profile = styleProfile?.profile || {};
  const labels = {
    narrative_voice: "叙述声音", point_of_view: "叙事视角", pacing: "节奏", sentence_rhythm: "句式节律",
    diction: "措辞", description_preferences: "描写偏好", dialogue_pattern: "对话习惯",
    emotional_tone: "情绪底色", avoid: "避免事项",
  };
  const rows = Object.entries(labels).filter(([key]) => profile[key]).map(([key, label]) =>
    `<div><b>${label}</b><p>${esc(profile[key])}</p></div>`);
  const groups = [["character_voices", "人物语言指纹"], ["description_craft", "描写技法"], ["plot_devices", "桥段机制"]];
  for (const [key, label] of groups) {
    if (!Array.isArray(profile[key]) || !profile[key].length) continue;
    rows.push(`<div><b>${label}</b><p>${profile[key].slice(0, 12).map(item => esc(Object.values(item || {}).filter(Boolean).join(" · "))).join("<br>")}</p></div>`);
  }
  return rows.length ? `${styleProfile?.source_label ? `<small>来源：${esc(styleProfile.source_label)}</small>` : ""}${rows.join("")}` : '<div class="empty">还没有语言指纹。可从当前正文提炼，也可在整本拆书完成后提炼。</div>';
}
function renderMaterialHub() {
  if (!materialData) return;
  const settings = materialData.settings || {};
  $("materialUseMemory").checked = settings.use_story_memory !== false;
  $("materialUseReferences").checked = settings.use_reference_projects !== false;
  $("materialUseStyle").checked = settings.use_style_profile !== false;
  $("materialUseInspirations").checked = settings.use_inspirations !== false;
  $("materialUseDocuments").checked = settings.use_reference_documents !== false;
  $("materialStyleStrength").value = settings.style_strength || "balanced";
  $("materialStyleProfile").innerHTML = renderMaterialProfile(materialData.style_profile);
  const mounted = new Set((materialData.mounts || []).map(item => item.reference_work_id));
  const available = (materialData.available_works || []).filter(item => !mounted.has(item.id));
  $("materialReferenceWork").innerHTML = available.length
    ? available.map(item => `<option value="${item.id}">${esc(item.title)}</option>`).join("")
    : '<option value="">没有可挂载的其他作品</option>';
  $("materialReferenceList").innerHTML = (materialData.mounts || []).length ? materialData.mounts.map(item => {
    const uses = [item.use_style && "文风", item.use_plot && "桥段", item.use_world && "世界观"].filter(Boolean).join("、") || "未启用参考方向";
    return `<article class="material-list-item"><div><b>${esc(item.reference_title)}</b><small>${esc(uses)} · 只读参考，不作为本书事实</small></div>
      <div class="material-reference-directions">
        <label><input type="checkbox" ${item.use_style ? "checked" : ""} onchange="updateMaterialReference(${item.id},'use_style',this.checked)">文风</label>
        <label><input type="checkbox" ${item.use_plot ? "checked" : ""} onchange="updateMaterialReference(${item.id},'use_plot',this.checked)">桥段</label>
        <label><input type="checkbox" ${item.use_world ? "checked" : ""} onchange="updateMaterialReference(${item.id},'use_world',this.checked)">世界观</label>
      </div><button class="ic danger-lite" onclick="removeMaterialReference(${item.id})">卸载</button></article>`;
  }).join("") : '<div class="empty">尚未挂载参考工程。</div>';
  $("materialExtractionList").innerHTML = (materialData.extractions || []).length ? materialData.extractions.map(item => {
    const profile = item.result?.style_profile || {}, voices = Array.isArray(profile.character_voices) ? profile.character_voices.length : 0;
    const crafts = Array.isArray(profile.description_craft) ? profile.description_craft.length : 0;
    const plots = Array.isArray(item.result?.plot_devices) ? item.result.plot_devices.length : 0;
    const time = item.updated_at ? new Date(item.updated_at * 1000).toLocaleString() : "";
    const status = item.status === "completed" ? "已完成" : item.status === "failed" ? "失败" : item.status;
    const detail = item.status === "completed" ? `${voices} 份人物声音 · ${crafts} 条描写技法 · ${plots} 个桥段 · ${(item.inspiration_ids || []).length} 条已入灵感库` : (item.error || "未完成");
    return `<article class="material-list-item material-extraction-card" data-status="${esc(item.status)}"><div><b>${esc(item.source_name || "拆书任务")} · ${esc(status)}</b><small>${esc(detail)}${time ? ` · ${esc(time)}` : ""}</small></div></article>`;
  }).join("") : '<div class="empty">还没有整本提炼记录。完成拆书分析后，可在拆书页点击“提炼整本资料”。</div>';
  $("materialDocumentList").innerHTML = (materialData.documents || []).length ? materialData.documents.map(item => `
    <article class="material-list-item"><div><b>${esc(item.name)}</b><small>${Number(item.chars || 0).toLocaleString()} 字${item.tags ? ` · ${esc(item.tags)}` : ""}</small></div>
      <button class="ic" onclick="openMaterialDocumentPreview(${item.id})">查看</button>
      <label><input type="checkbox" ${item.enabled ? "checked" : ""} onchange="updateMaterialDocument(${item.id},{enabled:this.checked})">启用</label>
      <label><input type="checkbox" ${item.pinned ? "checked" : ""} onchange="updateMaterialDocument(${item.id},{pinned:this.checked})">置顶</label>
      <button class="ic danger-lite" onclick="deleteMaterialDocument(${item.id})">删除</button></article>`).join("") : '<div class="empty">还没有长期资料。本轮附件不会自动长期保存。</div>';
}
function saveMaterialSettings() {
  clearTimeout(materialSettingsTimer);
  $("materialSettingsMsg").textContent = "正在保存…";
  materialSettingsTimer = setTimeout(async () => {
    try {
      const result = await api(`/api/works/${currentWorkId}/materials/settings`, { method: "PUT", body: materialSettingsValue() });
      if (materialData) materialData.settings = result.settings;
      $("materialSettingsMsg").textContent = "已保存";
      setTimeout(() => { if ($("materialSettingsMsg").textContent === "已保存") $("materialSettingsMsg").textContent = ""; }, 1200);
    } catch (e) { $("materialSettingsMsg").textContent = e.message; }
  }, 220);
}
async function addMaterialReference() {
  const reference_work_id = +$("materialReferenceWork").value;
  if (!reference_work_id) { showToast("没有可挂载的参考工程", "err"); return; }
  try {
    await api(`/api/works/${currentWorkId}/materials/references`, { body: {
      reference_work_id, enabled: true, use_style: $("materialRefStyle").checked,
      use_plot: $("materialRefPlot").checked, use_world: $("materialRefWorld").checked,
    }});
    await loadMaterialHub(); showToast("参考工程已挂载", "ok");
  } catch (e) { showToast(e.message, "err"); }
}
async function updateMaterialReference(mountId, key, checked) {
  const item = materialData?.mounts?.find(row => row.id === mountId); if (!item) return;
  item[key] = checked;
  try {
    await api(`/api/works/${currentWorkId}/materials/references`, { body: {
      reference_work_id: item.reference_work_id, enabled: item.enabled !== false,
      use_style: !!item.use_style, use_plot: !!item.use_plot, use_world: !!item.use_world,
    }});
    await loadMaterialHub(); showToast("参考方向已更新", "ok");
  } catch (e) { await loadMaterialHub(); showToast(e.message, "err"); }
}
async function removeMaterialReference(mountId) {
  try { await api(`/api/works/${currentWorkId}/materials/references/${mountId}`, { method: "DELETE" }); await loadMaterialHub(); }
  catch (e) { showToast(e.message, "err"); }
}
function chooseMaterialDocument() { $("materialDocumentFile").click(); }
async function addMaterialDocument(file) {
  const input = $("materialDocumentFile");
  if (!file || !currentWorkId) return;
  try {
    const response = await fetch("/api/agent/documents/extract", {
      method: "POST", headers: { "Content-Type": file.type || "application/octet-stream", "X-File-Name": encodeURIComponent(file.name), ...(token ? { Authorization: "Bearer " + token } : {}) }, body: file,
    });
    if (!response.ok) throw new Error(await responseError(response));
    const document = await response.json();
    await api(`/api/works/${currentWorkId}/materials/documents`, { body: { name: document.name, content: document.text, enabled: true, pinned: false } });
    await loadMaterialHub(); showToast(`${document.name} 已存为长期资料`, "ok");
  } catch (e) { showToast("长期资料保存失败：" + e.message, "err"); }
  finally { if (input) input.value = ""; }
}
async function updateMaterialDocument(documentId, changes) {
  try { await api(`/api/works/${currentWorkId}/materials/documents/${documentId}`, { method: "PUT", body: changes }); await loadMaterialHub(); }
  catch (e) { showToast(e.message, "err"); }
}
async function openMaterialDocumentPreview(documentId) {
  try {
    const item = await api(`/api/works/${currentWorkId}/materials/documents/${documentId}`, { method: "GET" });
    $("materialDocumentPreviewTitle").textContent = item.name || "长期资料";
    $("materialDocumentPreviewContent").textContent = item.content || "";
    $("materialDocumentPreviewOverlay").classList.remove("hidden");
  } catch (e) { showToast("资料打开失败：" + e.message, "err"); }
}
function closeMaterialDocumentPreview() { $("materialDocumentPreviewOverlay").classList.add("hidden"); }
async function deleteMaterialDocument(documentId) {
  if (!await askCard({ title: "删除这份长期资料？", msg: "仅删除资料中心副本，不影响原文件。", okText: "删除", danger: true })) return;
  try { await api(`/api/works/${currentWorkId}/materials/documents/${documentId}`, { method: "DELETE" }); await loadMaterialHub(); }
  catch (e) { showToast(e.message, "err"); }
}
async function analyzeMaterialStyle() {
  if (!currentWorkId) return;
  const button = $("materialAnalyzeStyleBtn"); busy(button, true, "提炼中");
  try {
    const result = await api(`/api/works/${currentWorkId}/materials/style/analyze`, { body: {} });
    if (materialData) materialData.style_profile = result.style_profile;
    renderMaterialHub(); showToast("本书语言指纹已刷新", "ok");
  } catch (e) { showToast(e.message, "err"); }
  finally { busy(button, false, "从当前正文刷新"); }
}
async function extractDisassemblyMaterials() {
  if (!currentDisassemblyJob) { showToast("先选择一个拆书任务", "err"); return; }
  const button = $("disassemblyExtractBtn"); busy(button, true, "提炼中");
  try {
    const result = await api(`/api/disassembly/jobs/${currentDisassemblyJob.id}/materials/extract`, { body: { create_inspirations: true } });
    const plots = (result.plot_devices || []).length, saved = (result.inspiration_ids || []).length;
    $("disassemblyExtractionSummary").textContent = `提炼完成：语言指纹已保存，识别 ${plots} 个桥段，其中 ${saved} 个已加入灵感库。可到“创作资料中心”核对和启停。`;
    $("disassemblyExtractionSummary").classList.remove("hidden");
    showToast(`整本资料已提炼：语言指纹已保存，${saved} 个桥段已加入灵感库`, "ok");
    if (currentDisassemblyJob.target_work_id === currentWorkId) await loadMaterialHub();
  } catch (e) { showToast(e.message, "err"); }
  finally { busy(button, false, "提炼整本资料"); }
}

/* ---------- AI 写作工具（校验/摘要，不污染正文） ---------- */

function openAITools() { $("aiResult").textContent = ""; $("aiOverlay").classList.remove("hidden"); }
function closeAITools() { $("aiOverlay").classList.add("hidden"); }
async function aiCheck() {
  if (!currentChapterId) { $("aiResult").textContent = "先选一章"; return; }
  $("aiResult").textContent = "校验中…";
  busy($("aiCheckBtn"), true);
  try {
    if (!await flushEditorSave()) return;
    const r = await api(`/api/chapters/${currentChapterId}/review`, { body: {} });
    consistencyAlerts = r.alerts || [];
    syncChapterWorkflow(r.workflow);
    $("aiResult").textContent = consistencyAlerts.length
      ? consistencyAlerts.map(item => `[${item.severity}] ${item.title}\n${item.detail || item.suggestion || ""}`).join("\n\n")
      : "未发现明确的连续性冲突。";
    await notifyStoryUpdates(r);
  } catch (e) { $("aiResult").textContent = "出错：" + e.message; }
  finally { busy($("aiCheckBtn"), false); }
}
async function aiSynopsis() {
  if (!currentChapterId) { $("aiResult").textContent = "先选一章"; return; }
  $("aiResult").textContent = "生成摘要中…";
  busy($("aiSynBtn"), true);
  try {
    const r = await api("/api/process", { body: { mode: "摘要", chapter_id: currentChapterId } });
    $("notes").value = r.result;                 // 摘要填进备注 → 成为续写/扩写/找回的上下文
    if ($("notesBar").classList.contains("hidden")) toggleNotes();
    onNotesInput();
    $("aiResult").textContent = "已填入备注：\n\n" + r.result;
  } catch (e) { $("aiResult").textContent = "出错：" + e.message; }
  finally { busy($("aiSynBtn"), false); }
}

/* ---------- AI Skills（可复用的 Agent 写作规则） ---------- */

function agentSkillStorageKey() { return `agentSkillIds:${currentUsername || "anonymous"}:${currentWorkId || "global"}`; }
function restoreActiveSkills() {
  try {
    const saved = JSON.parse(localStorage.getItem(agentSkillStorageKey()) || "[]");
    activeAgentSkillIds = new Set(Array.isArray(saved) ? saved.filter(Number.isInteger) : []);
  } catch (e) { activeAgentSkillIds = new Set(); }
}
function persistActiveSkills() {
  localStorage.setItem(agentSkillStorageKey(), JSON.stringify([...activeAgentSkillIds]));
}
function enabledSkills() { return agentSkills.filter(s => Number(s.enabled)); }
function selectedSkills() {
  const active = activeAgentSkillIds;
  return enabledSkills().filter(s => active.has(s.id));
}
async function loadAgentSkills() {
  const changedWork = agentSkillsWorkId !== currentWorkId;
  try {
    const q = currentWorkId ? `?work_id=${encodeURIComponent(currentWorkId)}` : "";
    agentSkills = await api(`/api/agent/skills${q}`, { method: "GET" });
    agentSkillsWorkId = currentWorkId;
    if (changedWork) restoreActiveSkills();
    const allowed = new Set(enabledSkills().map(s => s.id));
    activeAgentSkillIds = new Set([...activeAgentSkillIds].filter(id => allowed.has(id)));
    persistActiveSkills();
  } catch (e) {
    agentSkills = [];
    if (changedWork) activeAgentSkillIds = new Set();
  }
  renderAgentSkills();
  if (!$("skillPickerOverlay").classList.contains("hidden")) renderSkillPickerList();
  if (!$("skillsOverlay").classList.contains("hidden")) renderSkillList();
}
function renderAgentSkills() {
  const host = $("agentSkills");
  const skills = selectedSkills();
  if (!skills.length) { host.innerHTML = ""; host.classList.add("hidden"); return; }
  host.innerHTML = skills.map(s => `
    <button class="agent-skill-chip" onclick="removeActiveSkill(${s.id})" title="取消本轮 Skill：${esc(s.name)}">
      ${svg("sparkles")}<span>${esc(s.name)}</span>${svg("x")}
    </button>`).join("");
  host.classList.remove("hidden");
}
function toggleActiveSkill(skillId) {
  if (activeAgentSkillIds.has(skillId)) activeAgentSkillIds.delete(skillId);
  else {
    if (activeAgentSkillIds.size >= 4) { showToast("一次最多选择 4 个 Skill", "err"); return; }
    activeAgentSkillIds.add(skillId);
  }
  persistActiveSkills();
  renderAgentSkills();
  renderSkillPickerList();
}
function removeActiveSkill(skillId) {
  activeAgentSkillIds.delete(skillId);
  persistActiveSkills();
  renderAgentSkills();
  if (!$("skillPickerOverlay").classList.contains("hidden")) renderSkillPickerList();
}
function clearActiveSkills() {
  activeAgentSkillIds.clear();
  persistActiveSkills();
  renderAgentSkills();
  renderSkillPickerList();
}
async function openSkillPicker() {
  await loadAgentSkills();
  renderSkillPickerList();
  $("skillPickerOverlay").classList.remove("hidden");
}
function closeSkillPicker() { $("skillPickerOverlay").classList.add("hidden"); }
function renderSkillPickerList() {
  const host = $("skillPickerList");
  const skills = enabledSkills();
  host.innerHTML = skills.length ? skills.map(s => `
    <label class="skill-pick-row">
      <input type="checkbox" ${activeAgentSkillIds.has(s.id) ? "checked" : ""} onchange="toggleActiveSkill(${s.id})">
      <span class="skill-pick-copy"><b>${esc(s.name)}</b>${s.description ? `<small>${esc(s.description)}</small>` : ""}</span>
      <span class="skill-pick-tags"><span class="skill-scope">${s.work_id == null ? "通用" : "本作品"}</span>${s.source_kind === "builtin" ? '<span class="skill-source builtin">内置</span>' : (s.source_kind === "skill_md" ? '<span class="skill-source">MD</span>' : "")}</span>
    </label>`).join("") : '<div class="empty">还没有可用的 Skill。点右上角设置图标新建。</div>';
}

let editingSkillId = null;
async function openSkills() {
  await loadAgentSkills();
  renderSkillList();
  resetSkillForm();
  const importScope = $("skillImportScope");
  importScope.querySelector('option[value="work"]').disabled = !currentWorkId;
  if (!currentWorkId && importScope.value === "work") importScope.value = "global";
  $("skillsOverlay").classList.remove("hidden");
}
function closeSkills() { $("skillsOverlay").classList.add("hidden"); }
function chooseSkillImport() {
  const input = $("skillImportInput");
  input.value = "";
  input.click();
}
async function importSkillFile() {
  const input = $("skillImportInput"), file = input.files?.[0];
  if (!file) return;
  const workScope = $("skillImportScope").value === "work";
  if (workScope && !currentWorkId) { $("skillMsg").textContent = "当前没有可关联的作品"; return; }
  if (file.size > 2 * 1024 * 1024) { $("skillMsg").textContent = "Skill 文件不能超过 2MB"; return; }
  $("skillMsg").textContent = "正在导入…";
  try {
    const r = await api("/api/agent/skills/import", { body: {
      filename: file.name, data: await blobToBase64(file),
      work_id: workScope ? currentWorkId : null, enabled: $("skillEnabled").checked,
    } });
    await loadAgentSkills();
    renderSkillList();
    resetSkillForm();
    const skipped = Array.isArray(r.skipped_files) ? r.skipped_files.length : 0;
    showToast(skipped ? `已导入 ${r.name}；${skipped} 个脚本或非文本文件未加载` : `已导入 ${r.name}`, "ok");
  } catch (e) { $("skillMsg").textContent = "导入失败：" + e.message; }
  finally { input.value = ""; }
}
function renderSkillList() {
  const host = $("skillList");
  host.innerHTML = agentSkills.length ? agentSkills.map(s => `
    <div class="skill-row${Number(s.enabled) ? "" : " disabled"}">
      <div class="skill-row-main">
        <div class="skill-row-head"><b>${esc(s.name)}</b><span class="skill-scope">${s.work_id == null ? "通用" : "本作品"}</span>${s.source_kind === "builtin" ? '<span class="skill-source builtin">内置</span>' : (s.source_kind === "skill_md" ? '<span class="skill-source">SKILL.md</span>' : "")}${Number(s.resource_count) ? `<span class="skill-source">${Number(s.resource_count)} 资料</span>` : ""}${Number(s.enabled) ? "" : '<span class="skill-off">已停用</span>'}</div>
        ${s.description ? `<div class="skill-desc">${esc(s.description)}</div>` : ""}
        <div class="skill-rule">${esc(s.instruction)}</div>
      </div>
      <div class="skill-row-actions">${s.source_kind === "builtin" ? '<span class="set-hint">系统维护</span>' : `
        <button class="ic" onclick="startEditSkill(${s.id})" title="编辑 Skill">${svg("pen")}</button>
        <button class="ic" onclick="delSkill(${s.id})" title="删除 Skill">${svg("trash")}</button>`}
      </div>
    </div>`).join("") : '<div class="empty">还没有 Skill。先为常用写作要求建一个模板。</div>';
}
function resetSkillForm() {
  editingSkillId = null;
  $("skillName").value = "";
  $("skillDescription").value = "";
  $("skillInstruction").value = "";
  $("skillEnabled").checked = true;
  const scope = $("skillScope");
  scope.disabled = !currentWorkId;
  scope.value = currentWorkId ? "work" : "global";
  $("skillSaveBtn").textContent = "新增 Skill";
  $("skillCancelBtn").classList.add("hidden");
  $("skillMsg").textContent = "";
}
function startEditSkill(skillId) {
  const s = agentSkills.find(x => x.id === skillId);
  if (!s) return;
  if (s.source_kind === "builtin") { showToast("内置 Skill 由系统维护，不能编辑", "err"); return; }
  editingSkillId = skillId;
  $("skillName").value = s.name || "";
  $("skillDescription").value = s.description || "";
  $("skillInstruction").value = s.instruction || "";
  $("skillEnabled").checked = Boolean(Number(s.enabled));
  const scope = $("skillScope");
  scope.disabled = !currentWorkId;
  scope.value = s.work_id == null ? "global" : "work";
  $("skillSaveBtn").textContent = "保存修改";
  $("skillCancelBtn").classList.remove("hidden");
  $("skillMsg").textContent = "";
  $("skillName").focus();
}
async function saveSkill() {
  const name = $("skillName").value.trim();
  const instruction = $("skillInstruction").value.trim();
  if (!name || !instruction) { $("skillMsg").textContent = "请填写名称和规则"; return; }
  const workScope = $("skillScope").value === "work";
  if (workScope && !currentWorkId) { $("skillMsg").textContent = "当前没有可关联的作品"; return; }
  const body = {
    name, instruction, work_id: workScope ? currentWorkId : null,
    description: $("skillDescription").value.trim(), enabled: $("skillEnabled").checked,
  };
  const btn = $("skillSaveBtn");
  busy(btn, true, editingSkillId ? "保存" : "新增");
  try {
    if (editingSkillId) await api(`/api/agent/skills/${editingSkillId}`, { method: "PUT", body });
    else await api("/api/agent/skills", { body });
    await loadAgentSkills();
    renderSkillList();
    resetSkillForm();
  } catch (e) { $("skillMsg").textContent = e.message; }
  finally { busy(btn, false, editingSkillId ? "保存修改" : "新增 Skill"); }
}
async function delSkill(skillId) {
  const skill = agentSkills.find(item => item.id === skillId);
  if (skill?.source_kind === "builtin") { showToast("内置 Skill 不能删除", "err"); return; }
  if (!await askCard({ title: "删除这个 Skill？", msg: "已删除的规则不能恢复。", okText: "删除", danger: true })) return;
  try {
    await api(`/api/agent/skills/${skillId}`, { method: "DELETE" });
    activeAgentSkillIds.delete(skillId);
    persistActiveSkills();
    await loadAgentSkills();
    if (editingSkillId === skillId) resetSkillForm();
  } catch (e) { showToast("删除失败：" + e.message, "err"); }
}

/* ---------- AI 助手（常驻侧栏，对话即操作，自动存版本可撤销） ---------- */

function agentScopeKey() {
  if (currentChapterId != null) return `chapter:${currentChapterId}`;
  if (currentWorkId != null) return `work:${currentWorkId}`;
  return "global";
}
function agentScopeParams() {
  const params = new URLSearchParams();
  if (currentChapterId != null) params.set("chapter_id", currentChapterId);
  else if (currentWorkId != null) params.set("work_id", currentWorkId);
  return params;
}
function agentScopeBody() {
  return {
    chapter_id: currentChapterId,
    work_id: currentChapterId == null ? currentWorkId : null,
  };
}
function agentScopeLabel() {
  if (currentChapterId != null) {
    const chapter = chapters.find(item => item.id === currentChapterId);
    return chapter ? `第${chapter.ord || ""}章《${chapter.title || "无标题"}》` : "当前章节";
  }
  const work = works.find(item => item.id === currentWorkId);
  return work ? `作品《${work.title}》` : "通用会话";
}
function setAgentConversationMode(mode) {
  agentConversationMode = ["standard", "ignore_history", "temporary"].includes(mode) ? mode : "standard";
  document.querySelectorAll("#agentConversationModes button").forEach(button => {
    button.classList.toggle("active", button.dataset.mode === agentConversationMode);
  });
}
function formatAgentSessionTime(value) {
  if (!value) return "";
  try { return new Date(value * 1000).toLocaleString([], { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }); }
  catch (e) { return ""; }
}
function renderAgentSessionControls() {
  const select = $("agentSessionSwitch");
  const activeSessions = agentSessions.filter(item => !item.archived);
  select.innerHTML = activeSessions.length
    ? activeSessions.map(item => `<option value="${item.id}">${esc(item.title || "新会话")}</option>`).join("")
    : '<option value="">新会话</option>';
  select.value = currentAgentSessionId == null ? "" : String(currentAgentSessionId);
  select.disabled = agentBusy || !activeSessions.length;
  $("agentSummaryBar").classList.toggle("hidden", !agentConversationSummary);
  $("agentSessionScope").textContent = agentScopeLabel();
  const details = $("agentSummaryDetails");
  details.classList.toggle("hidden", !agentConversationSummary);
  $("agentSummaryText").textContent = agentConversationSummary || "";
  renderAgentSessionList();
}
function renderAgentSessionList() {
  const host = $("agentSessionList");
  if (!host) return;
  host.innerHTML = agentSessions.length ? agentSessions.map(item => {
    const status = item.archived ? "已归档" : `${item.msg_count || 0} 条消息`;
    const active = item.is_active && !item.archived;
    return `<div class="agent-session-item ${item.archived ? "archived" : ""}">
      <button class="agent-session-main" ${item.archived ? "disabled" : `onclick="switchAgentSession(${item.id})"`}>
        <b>${esc(item.title || "新会话")}</b>
        <small>${status}${item.updated_at ? ` · ${formatAgentSessionTime(item.updated_at)}` : ""}${item.has_summary ? " · 有摘要" : ""}</small>
      </button>
      ${active ? '<span class="agent-session-active">当前</span>' : ""}
      <span class="agent-session-actions">
        ${item.archived
          ? `<button class="ic" onclick="restoreAgentSession(${item.id})" title="恢复会话">${svg("refresh")}</button>`
          : `<button class="ic" onclick="renameAgentSession(${item.id})" title="重命名">${svg("pen")}</button>
             <button class="ic" onclick="archiveAgentSession(${item.id})" title="归档">${svg("archive")}</button>`}
        <button class="ic" onclick="deleteAgentSession(${item.id})" title="永久删除">${svg("trash")}</button>
      </span>
    </div>`;
  }).join("") : '<div class="agent-session-empty">当前范围还没有会话</div>';
}

function toggleAISide() {
  const app = $("app");
  const opening = !app.classList.contains("ai-open");
  // 笔记本宽度下两个工具栏会挤压正文；一次只展开一个，手机仍由 CSS 处理为全屏面板。
  if (opening && workspaceNeedsExclusivePane() && app.classList.contains("story-open")) closeStoryDrawer();
  const open = app.classList.toggle("ai-open");
  localStorage.setItem("aiOpen", open ? "1" : "0");
  if (open) setTimeout(() => {
    setAiTtsBtn(); renderAgent(); renderAgentSelection(); renderAgentSkills(); renderAgentSessionControls();
    if (agentSessionScopeKey !== agentScopeKey()) loadAgentSessions();
    $("agentInput").focus();
  }, 50);
  else if ("speechSynthesis" in window) speechSynthesis.cancel(); // 收起侧栏时停止朗读
}
function renderAgentSources(sources) {
  if (!Array.isArray(sources)) return "";
  const links = sources.slice(0, 10).map((source, index) => {
    const url = safeHttpUrl(source?.url);
    if (!url) return "";
    let domain = "";
    try { domain = new URL(url).hostname.replace(/^www\./, ""); } catch (e) {}
    return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer"><b>${index + 1}. ${esc(source?.title || domain || "网页来源")}</b><small>${esc(domain)}</small></a>`;
  }).filter(Boolean).join("");
  return links ? `<div class="agent-source-list"><span>搜索来源</span>${links}</div>` : "";
}
function scheduleAgentReplyRender() {
  if (agentReplyRenderPending) return;
  agentReplyRenderPending = true;
  requestAnimationFrame(() => {
    agentReplyRenderPending = false;
    if (!agentBusy) return;
    const host = $("agentMsgs");
    const draftNode = $("agentStreamText");
    const statusNode = $("agentStreamStatusText");
    if (!!draftNode !== !!agentReplyDraft || !!statusNode !== !!agentReplyStatus) {
      renderAgent();
      return;
    }
    if (draftNode) draftNode.textContent = agentReplyDraft;
    if (statusNode) statusNode.textContent = agentReplyStatus;
    if (host) host.scrollTop = host.scrollHeight;
  });
}
function updateAgentReplyStream(event) {
  if (event.type === "assistant_start") {
    agentReplyDraft = "";
    agentReplyStatus = "正在生成回答";
  } else if (event.type === "assistant_delta" && typeof event.delta === "string") {
    agentReplyDraft += event.delta;
    agentReplyStatus = "";
  } else if (event.type === "tool_start") {
    agentReplyStatus = `正在使用 ${event.name || "工具"}`;
  } else if (event.type === "status") {
    agentReplyStatus = event.message || "";
  }
  scheduleAgentReplyRender();
}
function renderAgentInlineMarkdown(value) {
  return esc(String(value || ""))
    .replace(/`([^`\n]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
}
function renderAgentMarkdown(value) {
  const lines = String(value || "").replace(/\r\n?/g, "\n").split("\n");
  let html = "", list = "", inCode = false, code = [];
  const closeList = () => { if (list) { html += `</${list}>`; list = ""; } };
  const closeCode = () => {
    if (!inCode) return;
    html += `<pre><code>${esc(code.join("\n"))}</code></pre>`;
    inCode = false; code = [];
  };
  for (const line of lines) {
    if (/^\s*```/.test(line)) { if (inCode) closeCode(); else { closeList(); inCode = true; } continue; }
    if (inCode) { code.push(line); continue; }
    let match;
    if (!line.trim()) { closeList(); continue; }
    if ((match = line.match(/^\s*(#{1,3})\s+(.+)$/))) {
      closeList(); const level = Math.min(5, match[1].length + 2); html += `<h${level}>${renderAgentInlineMarkdown(match[2])}</h${level}>`; continue;
    }
    if ((match = line.match(/^\s*[-*]\s+(.+)$/))) {
      if (list !== "ul") { closeList(); list = "ul"; html += "<ul>"; }
      html += `<li>${renderAgentInlineMarkdown(match[1])}</li>`; continue;
    }
    if ((match = line.match(/^\s*\d+[.)、]\s*(.+)$/))) {
      if (list !== "ol") { closeList(); list = "ol"; html += "<ol>"; }
      html += `<li>${renderAgentInlineMarkdown(match[1])}</li>`; continue;
    }
    closeList();
    if ((match = line.match(/^\s*>\s*(.+)$/))) html += `<blockquote>${renderAgentInlineMarkdown(match[1])}</blockquote>`;
    else html += `<p>${renderAgentInlineMarkdown(line)}</p>`;
  }
  closeList(); closeCode();
  return html;
}
function agentToolMeta(name) {
  const value = String(name || "tool");
  if (value === "web_search" || /search|find/i.test(value)) return { tone: "search", icon: "search", label: "搜索" };
  if (/read|list|get|context|inspect/i.test(value)) return { tone: "read", icon: "eye", label: "读取" };
  if (/delete|remove|purge/i.test(value)) return { tone: "danger", icon: "trash", label: "删除" };
  if (/write|replace|update|save|create|add|restore|edit/i.test(value)) return { tone: "write", icon: "pen", label: "写入" };
  return { tone: "neutral", icon: "settings", label: "工具" };
}
async function copyAgentMessage(index) {
  const content = agentMsgs[index]?.content || "";
  if (!content) return;
  try { await navigator.clipboard.writeText(content); showToast("AI 回复已复制", "ok"); }
  catch (e) { showToast("复制失败，请长按文字复制", "err"); }
}
function insertAgentMessage(index) {
  const content = agentMsgs[index]?.content || "";
  if (!currentChapterId || !content) { showToast("先打开一个正文章节", "err"); return; }
  const editor = $("content"), start = editor.selectionStart, end = editor.selectionEnd;
  const prefix = start > 0 && editor.value[start - 1] !== "\n" ? "\n" : "";
  editor.setRangeText(prefix + content, start, end, "end");
  onContentInput(); editor.focus(); showToast("已插入正文，可继续编辑或撤销", "ok");
}
function retryAgentMessage(index) {
  for (let i = index - 1; i >= 0; i--) {
    if (agentMsgs[i]?.role === "user") {
      if (agentMsgs[i].content === "[voice] 语音指令") {
        showToast("直发模式不会在浏览器保留原始录音，请重新录音；转写模式的文字可以直接重试", "err"); return;
      }
      $("agentInput").value = agentMsgs[i].content || ""; sendAgent(); return;
    }
  }
  showToast("没有找到可重新发送的文字指令", "err");
}
function renderAgent() {
  const el = $("agentMsgs");
  if (agentConversationLoading) {
    el.innerHTML = '<div class="agent-chat-loading"><span class="spinner"></span><span>正在加载会话</span></div>';
    return;
  }
  if (!agentMsgs.length && !agentBusy) {
    el.innerHTML = '<div class="empty">当前会话还没有消息</div>';
    return;
  }
  let html = "";
  let temporaryLabelShown = false;
  for (let messageIndex = 0; messageIndex < agentMsgs.length; messageIndex++) {
    const m = agentMsgs[messageIndex];
    if (m.temporary && !temporaryLabelShown) {
      html += '<div class="chat-temporary-label">临时一问 · 不保存</div>';
      temporaryLabelShown = true;
    }
    const temporaryClass = m.temporary ? " temporary" : "";
    if (m.role === "user") {
      html += m.content === "[voice] 语音指令"
        ? `<div class="cm user voice${temporaryClass}">${svg("mic")}<span><b>你 · 语音</b><br>语音指令</span></div>`
        : `<div class="cm user${temporaryClass}"><div class="agent-message-label">你</div><div>${esc(m.content)}</div></div>`;
    } else if (m.role === "assistant") {
      if (m.content) {
        const isError = /^(出错|失败|请求失败|网络错误|语音直发失败)[:：]/.test(m.content.trim());
        html += `<div class="cm assistant${isError ? " error-message" : ""}${temporaryClass}">
          <div class="agent-message-label">${svg(isError ? "alert" : "bot")}<span>${isError ? "请求出错" : "AI"}</span></div>
          <div class="agent-message-body">${renderAgentMarkdown(m.content)}</div>
          <div class="agent-message-actions">
            <button onclick="copyAgentMessage(${messageIndex})" title="复制回复">${svg("copy")}复制</button>
            ${isError ? `<button onclick="retryAgentMessage(${messageIndex})" title="不用重新输入，重新发送上一条文字指令">${svg("refresh")}重新发送</button>` : `<button onclick="insertAgentMessage(${messageIndex})" title="插入当前章节光标位置">${svg("enter")}插入正文</button>`}
          </div>
        </div>`;
      }
    } else if (m.role === "tool") {
      let r = {}; try { r = JSON.parse(m.content); } catch (e) {}
      if (r.error) {
        html += `<div class="cm action tool-danger"><div class="act-bar"><span class="act-txt">${svg("alert")}<b>工具出错</b><span>${esc(r.error)}</span></span></div></div>`;
      } else {
        const sum = r.summary || "已执行操作";
        const rid = r.undo_rid;
        const actionChapterId = Number.isInteger(r.chapter_id) ? r.chapter_id : currentChapterId;
        const undone = rid && agentUndone.has(rid);
        const card = undone
          ? `<span class="done-tag">已撤销</span>`
          : (rid && actionChapterId ? `<button class="undo-btn" onclick="undoAgentAction(${rid},${actionChapterId})">撤销</button>` : "");
        const sources = renderAgentSources(r.sources);
        const meta = agentToolMeta(m.name);
        html += `<div class="cm action tool-${meta.tone}${undone ? " done" : ""}${temporaryClass}"><div class="act-bar"><span class="act-txt">${svg(meta.icon)}<b>${meta.label}</b><span>${esc(sum)}</span></span>${card || '<span class="agent-step-status">✓ 完成</span>'}</div>${sources}</div>`;
      }
    }
  }
  if (agentBusy && agentReplyDraft) {
    html += `<div id="agentStreamBubble" class="cm assistant streaming"><div class="agent-message-label">${svg("bot")}<span>AI · 生成中</span></div><span id="agentStreamText">${esc(agentReplyDraft)}</span><span class="stream-caret"></span></div>`;
  }
  if (agentBusy && agentReplyStatus) {
    html += `<div class="agent-stream-status"><span class="spinner"></span><span id="agentStreamStatusText">${esc(agentReplyStatus)}</span></div>`;
  } else if (agentBusy && !agentReplyDraft) {
    html += `<div class="cm assistant"><div class="agent-message-label">${svg("sparkles")}<span>AI</span></div>… 思考中</div>`;
  }
  el.innerHTML = html;
  el.scrollTop = el.scrollHeight;
}
async function applyAgentResult(r, selection, baseMessages = null) {
  if (selection) clearAgentSelection();
  const resultMessages = Array.isArray(r.messages) ? r.messages : [];
  if (r.temporary) {
    agentMsgs = (baseMessages || []).concat(resultMessages.map(message => ({ ...message, temporary: true })));
  } else if (resultMessages.length) {
    agentMsgs = resultMessages;
  }
  if (!r.temporary && r.session_id != null) {
    currentAgentSessionId = +r.session_id;
    agentConversationSummary = r.conversation_summary || "";
    const session = agentSessions.find(item => item.id === currentAgentSessionId);
    if (session) {
      session.title = r.session_title || session.title;
      session.msg_count = resultMessages.length;
      session.is_active = true;
      session.has_summary = !!agentConversationSummary;
      session.updated_at = Date.now() / 1000;
    }
    await loadAgentSessions(false);
  }
  if (r.compacted) showToast("已按上下文预算压缩早期对话", "ok");
  let contentChanged = false, sidebarDirty = false, entitiesDirty = false, productionDirty = false;
  const changedChapterIds = new Set();
  for (const m of resultMessages) {
    if (m.role === "tool") {
      let rr = {}; try { rr = JSON.parse(m.content); } catch (e) {}
      if (rr.changed) contentChanged = true;
      if (rr.sidebar_dirty) sidebarDirty = true;
      if (rr.entities_dirty) entitiesDirty = true;
      if (rr.production_dirty) productionDirty = true;
      if (rr.changed && Number.isInteger(rr.chapter_id)) changedChapterIds.add(rr.chapter_id);
    }
  }
  if (sidebarDirty) await loadChapters();
  if (entitiesDirty) {
    await loadWikiEntities();
    if (!$('wikiOverlay').classList.contains('hidden')) renderWikiList();
  }
  if (contentChanged && currentChapterId && (!changedChapterIds.size || changedChapterIds.has(currentChapterId))) {
    await loadChapter();
  }
  if ((productionDirty || r.production_analysis) && $("app").classList.contains("production-open")) {
    await (productionMode === "chapter" ? loadProductionChapter() : loadProductionOverview());
  }
  await notifyStoryUpdates(r);
  if (!$('characterStateOverlay').classList.contains('hidden') && characterStateChapterId === currentChapterId) {
    await loadCharacterState();
  }
}
function speakLatestAgentTurn() {
  const uIdx = agentMsgs.map(m => m.role).lastIndexOf("user");
  let spoken = "";
  for (let i = (uIdx < 0 ? 0 : uIdx + 1); i < agentMsgs.length; i++) {
    const m = agentMsgs[i];
    if (m.role === "assistant" && m.content) spoken += m.content + " ";
    else if (m.role === "tool") {
      let rr = {}; try { rr = JSON.parse(m.content); } catch (e) {}
      if (rr.summary) spoken += rr.summary + " ";
      else if (rr.error) spoken += "操作出错，" + rr.error + "。";
    }
  }
  speakAgentText(spoken);
}
async function sendAgent() {
  if (agentBusy) return;
  const el = $("agentInput");
  let text = el.value.trim();
  const documents = pendingAgentDocuments.map(({ name, text: content }) => ({ name, text: content }));
  if (!text && documents.length) text = "请阅读并根据本轮附件协助我。";
  if (!text) return;
  const conversationMode = agentConversationMode;
  const baseMessages = agentMsgs.slice();
  let selection = agentSelection;
  if (selection?.context_kind === "canvas_target") {
    try { selection = await refreshCanvasAgentTarget(selection); }
    catch (e) { showToast(e.message, "err"); return; }
  }
  el.value = "";
  // 先把正文框里未保存的手动编辑落库，避免 AI 基于旧正文操作、回显时覆盖手打内容
  if (!await flushEditorSave()) return;
  agentMsgs.push({ role: "user", content: text, temporary: conversationMode === "temporary" });
  agentBusy = true;
  agentReplyDraft = "";
  agentReplyStatus = "正在发送";
  busy($("sendBtn"), true, "发送");
  renderAgent();
  const body = {
    text, ...agentScopeBody(), session_id: currentAgentSessionId,
    conversation_mode: conversationMode,
  };
  if (selection) body.selection = selection;
  if (activeAgentSkillIds.size) body.skill_ids = [...activeAgentSkillIds];
  if (documents.length) body.documents = documents;
  try {
    const r = await streamAgent(body, updateAgentReplyStream);
    pendingAgentDocuments = [];
    renderAgentDocuments();
    agentReplyDraft = "";
    agentReplyStatus = "正在更新界面";
    scheduleAgentReplyRender();
    await applyAgentResult(r, selection, baseMessages);
  } catch (e) {
    agentMsgs.push({ role: "assistant", content: "出错：" + e.message });
  } finally {
    agentBusy = false;
    agentReplyDraft = "";
    agentReplyStatus = "";
    busy($("sendBtn"), false, "发送");
    setAgentConversationMode("standard");
    renderAgentSessionControls();
    renderAgent();
    speakLatestAgentTurn();
    $("agentInput").focus(); // 发完聚焦输入框，方便连续对话
  }
}
async function undoAgentAction(rid, chapterId = currentChapterId) {
  if (!chapterId || !rid) return;
  try {
    await api(`/api/chapters/${chapterId}/revisions/${rid}/restore`, { method: "POST" });
    agentUndone.add(rid);
    await loadChapters();
    if (chapterId === currentChapterId) await loadChapter();
    renderAgent();
  } catch (e) { showToast("撤销失败：" + e.message, "err"); }
}
async function clearAgent() {
  if (currentAgentSessionId != null) await deleteAgentSession(currentAgentSessionId);
}
async function loadAgentSessions(loadActive = true) {
  const scope = agentScopeKey();
  const requestId = ++agentConversationRequest;
  if (loadActive) {
    agentConversationLoading = true;
    if ($("app").classList.contains("ai-open")) renderAgent();
  }
  try {
    const params = agentScopeParams();
    params.set("include_archived", "1");
    const result = await api(`/api/agent/sessions?${params.toString()}`, { method: "GET" });
    if (requestId !== agentConversationRequest || scope !== agentScopeKey()) return;
    agentSessions = Array.isArray(result.sessions) ? result.sessions : [];
    currentAgentSessionId = result.active_session_id == null ? null : +result.active_session_id;
    agentSessionScopeKey = scope;
    renderAgentSessionControls();
    if (loadActive) {
      await loadConversation(currentAgentSessionId);
    } else {
      agentConversationLoading = false;
      if ($("app").classList.contains("ai-open")) renderAgent();
    }
  } catch (e) {
    if (requestId !== agentConversationRequest) return;
    if (loadActive) {
      agentSessions = []; currentAgentSessionId = null; agentMsgs = [];
      agentConversationSummary = ""; agentConversationLoading = false;
    }
    renderAgentSessionControls();
    if ($("app").classList.contains("ai-open")) renderAgent();
  }
}
function openAgentSessions() {
  renderAgentSessionControls();
  $("agentSessionOverlay").classList.remove("hidden");
  loadAgentSessions(false);
}
function closeAgentSessions() { $("agentSessionOverlay").classList.add("hidden"); }
async function newAgentSession() {
  if (agentBusy) { showToast("请等当前回复完成", "err"); return; }
  try {
    const session = await api("/api/agent/sessions", { body: { ...agentScopeBody() } });
    currentAgentSessionId = +session.id;
    agentMsgs = []; agentConversationSummary = ""; agentUndone.clear();
    setAgentConversationMode("standard");
    await loadAgentSessions(false);
    renderAgentSessionControls(); renderAgent();
    closeAgentSessions();
    $("agentInput").focus();
  } catch (e) { showToast("新建会话失败：" + e.message, "err"); }
}
async function switchAgentSession(value) {
  const sessionId = +value;
  if (!sessionId) { await newAgentSession(); return; }
  if (agentBusy) {
    renderAgentSessionControls();
    showToast("请等当前回复完成", "err");
    return;
  }
  if (sessionId === currentAgentSessionId) { closeAgentSessions(); return; }
  try {
    await api(`/api/agent/sessions/${sessionId}/activate`, { body: {} });
    currentAgentSessionId = sessionId;
    agentUndone.clear();
    setAgentConversationMode("standard");
    await loadAgentSessions();
    closeAgentSessions();
  } catch (e) { showToast("切换会话失败：" + e.message, "err"); }
}
async function renameAgentSession(sessionId) {
  const session = agentSessions.find(item => item.id === sessionId);
  if (!session) return;
  const title = await askCard({
    title: "重命名会话", input: "会话名称", def: session.title || "新会话", okText: "保存",
  });
  if (title === false) return;
  try {
    const updated = await api(`/api/agent/sessions/${sessionId}`, { method: "PATCH", body: { title } });
    session.title = updated.title;
    renderAgentSessionControls();
  } catch (e) { showToast("重命名失败：" + e.message, "err"); }
}
async function archiveAgentSession(sessionId) {
  const session = agentSessions.find(item => item.id === sessionId);
  if (!session) return;
  const confirmed = await askCard({
    title: "归档这个会话？", msg: "归档后不会参与当前对话，之后可以恢复。",
    okText: "归档",
  });
  if (!confirmed) return;
  try {
    await api(`/api/agent/sessions/${sessionId}`, { method: "PATCH", body: { archived: true } });
    await loadAgentSessions(sessionId === currentAgentSessionId);
  } catch (e) { showToast("归档失败：" + e.message, "err"); }
}
async function restoreAgentSession(sessionId) {
  try {
    await api(`/api/agent/sessions/${sessionId}`, { method: "PATCH", body: { archived: false } });
    currentAgentSessionId = sessionId;
    await loadAgentSessions();
    closeAgentSessions();
  } catch (e) { showToast("恢复失败：" + e.message, "err"); }
}
async function deleteAgentSession(sessionId) {
  const session = agentSessions.find(item => item.id === +sessionId);
  const confirmed = await askCard({
    title: "永久删除会话？",
    msg: `“${session?.title || "当前会话"}”的聊天记录和压缩摘要都会删除，小说正文和人物资料不受影响。`,
    okText: "永久删除", danger: true,
  });
  if (!confirmed) return;
  try {
    await api(`/api/agent/sessions/${sessionId}`, { method: "DELETE" });
    const deletedCurrent = +sessionId === currentAgentSessionId;
    if (deletedCurrent) {
      agentMsgs = []; agentConversationSummary = ""; agentUndone.clear();
    }
    await loadAgentSessions(deletedCurrent);
  } catch (e) { showToast("删除失败：" + e.message, "err"); }
}
async function loadConversation(sessionId = currentAgentSessionId) {
  const scope = agentScopeKey();
  const requestId = ++agentConversationRequest;
  agentConversationLoading = true;
  if ($("app").classList.contains("ai-open")) renderAgent();
  try {
    const params = agentScopeParams();
    if (sessionId != null) params.set("session_id", sessionId);
    const r = await api(`/api/agent/conversation?${params.toString()}`, { method: "GET" });
    if (requestId !== agentConversationRequest || scope !== agentScopeKey()) return;
    agentMsgs = Array.isArray(r.messages) ? r.messages : [];
    currentAgentSessionId = r.session_id == null ? null : +r.session_id;
    agentConversationSummary = r.summary || "";
    agentSessionScopeKey = scope;
  } catch (e) {
    if (requestId !== agentConversationRequest) return;
    agentMsgs = []; currentAgentSessionId = null; agentConversationSummary = "";
  } finally {
    if (requestId === agentConversationRequest) {
      agentConversationLoading = false;
      renderAgentSessionControls();
      if ($("app").classList.contains("ai-open")) renderAgent();
    }
  }
}
function openAdmin() { location.href = "admin.html"; }

/* 智能体语音：默认直发当前模型；关闭直发时才走独立 ASR。 */

function setVoiceTrace(state, detail = "", type = "") {
  if (voiceTraceHideTimer) {
    clearTimeout(voiceTraceHideTimer);
    voiceTraceHideTimer = null;
  }
  voiceTraceState = { state, detail, type };
  const host = $("voiceTrace");
  if (!host) return;
  host.classList.remove("hidden", "err", "ok", "recording");
  if (type) host.classList.add(type);
  if (state === "录音中") host.classList.add("recording");
  host.innerHTML = `${svg(state === "出错" ? "alert" : state === "录音中" ? "mic" : "check")}<span><b>${esc(state)}</b>${detail ? `<small>${esc(detail)}</small>` : ""}</span>`;
  if (type === "ok") {
    const shownState = voiceTraceState;
    voiceTraceHideTimer = setTimeout(() => {
      if (voiceTraceState !== shownState) return;
      voiceTraceState = null;
      voiceTraceHideTimer = null;
      host.classList.add("hidden");
      host.classList.remove("ok", "recording");
      host.innerHTML = "";
    }, 2500);
  }
}
async function micPermissionHint() {
  const parts = [];
  if (!window.isSecureContext) parts.push("当前页面不是安全上下文，请使用 HTTPS 打开网站");
  try {
    const status = await navigator.permissions?.query?.({ name: "microphone" });
    if (status?.state === "denied") parts.push("浏览器站点权限目前为拒绝");
  } catch (e) {}
  return parts.join("；");
}

function bestAudioMime() {
  const types = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
  return types.find(t => window.MediaRecorder && MediaRecorder.isTypeSupported(t)) || "";
}
function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error("无法读取录音"));
    reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
    reader.readAsDataURL(blob);
  });
}
function writeWavText(view, offset, text) {
  for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
}
async function recordToMonoWav(blob) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) throw new Error("浏览器无法转换录音格式");
  const ctx = new Ctx();
  try {
    const source = await ctx.decodeAudioData(await blob.arrayBuffer());
    const rate = 16000;
    const frames = Math.ceil(source.duration * rate);
    const wav = new ArrayBuffer(44 + frames * 2);
    const view = new DataView(wav);
    writeWavText(view, 0, "RIFF"); view.setUint32(4, 36 + frames * 2, true);
    writeWavText(view, 8, "WAVEfmt "); view.setUint32(16, 16, true);
    view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    writeWavText(view, 36, "data"); view.setUint32(40, frames * 2, true);
    const channels = Array.from({ length: source.numberOfChannels }, (_, i) => source.getChannelData(i));
    for (let i = 0; i < frames; i++) {
      const at = i * source.sampleRate / rate;
      const left = Math.floor(at), right = Math.min(left + 1, source.length - 1), mix = at - left;
      let sample = 0;
      for (const ch of channels) sample += ch[left] * (1 - mix) + ch[right] * mix;
      sample = Math.max(-1, Math.min(1, sample / channels.length));
      view.setInt16(44 + i * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    }
    return new Blob([wav], { type: "audio/wav" });
  } finally {
    try { await ctx.close(); } catch (e) {}
  }
}
async function transcribeAgentAudio(blob) {
  const btn = $("agentMicBtn");
  btn.classList.add("is-busy");
  setIcon(btn, "mic");
  btn.title = "转写中…";
  const el = $("agentInput");
  if (!_agentPH) _agentPH = el.placeholder;
  el.placeholder = "语音转写中…";
  setVoiceTrace("正在转写", "录音会先转为 16kHz 单声道 WAV，再发送到配置的 /audio/transcriptions 接口");
  try {
    // MediaRecorder 在 Chrome/Android 上通常生成 WebM/Opus。部分 OpenAI
    // 兼容网关无法解析 WebM 时长，因此与直发模式一样先统一为 WAV。
    const wav = await recordToMonoWav(blob);
    const res = await fetch("/api/asr", {
      method: "POST",
      headers: {
        "Content-Type": "audio/wav",
        ...(token ? { Authorization: "Bearer " + token } : {}),
      },
      body: wav,
    });
    if (res.status === 401) { showLogin(); throw new Error("未登录"); }
    if (!res.ok) throw new Error(await responseError(res));
    const r = await res.json();
    const text = (r.text || "").trim();
    if (!text) throw new Error("没有识别到文字");
    el.value = text;
    el.placeholder = _agentPH || "";
    const route = r.voice?.route || "转写接口";
    if (voiceAsrAutoSend) {
      const sending = sendAgent();
      setVoiceTrace("发送完成", `${route} · 文字已发送给 AI`, "ok");
      await sending;
    }
    else el.focus();
    if (!voiceAsrAutoSend) setVoiceTrace("转写完成", `${route} · 已填入输入框`, "ok");
  } catch (e) {
    el.placeholder = "转写失败：" + e.message;
    setVoiceTrace("出错", "转写链路：" + e.message, "err");
    showToast("转写失败：" + e.message, "err");
  } finally {
    btn.classList.remove("is-busy");
    if (!agentMicOn) setAgentMic(false);
  }
}
async function sendAgentAudio(blob) {
  if (agentBusy) return;
  const btn = $("agentMicBtn"), el = $("agentInput");
  let selection = agentSelection;
  const conversationMode = agentConversationMode;
  const baseMessages = agentMsgs.slice();
  if (!await flushEditorSave()) return;
  if (selection?.context_kind === "canvas_target") {
    try { selection = await refreshCanvasAgentTarget(selection); }
    catch (e) { showToast(e.message, "err"); return; }
  }
  agentMsgs.push({ role: "user", content: "[voice] 语音指令", temporary: conversationMode === "temporary" });
  agentBusy = true;
  agentReplyDraft = "";
  agentReplyStatus = "正在处理录音";
  btn.classList.add("is-busy");
  if (!_agentPH) _agentPH = el.placeholder;
  el.placeholder = "正在把语音发送给 AI…";
  setVoiceTrace("正在直发", "录音将直接发送给当前 Agent 模型，不经过转写");
  renderAgent();
  try {
    const wav = await recordToMonoWav(blob);
    if (wav.size > 5 * 1024 * 1024) throw new Error("录音太长，请控制在一分钟内");
    const audio = await blobToBase64(wav);
    const body = {
      audio, format: "wav", selection, ...agentScopeBody(),
      session_id: currentAgentSessionId, conversation_mode: conversationMode,
      ...(activeAgentSkillIds.size ? { skill_ids: [...activeAgentSkillIds] } : {}),
      ...(pendingAgentDocuments.length ? {
        documents: pendingAgentDocuments.map(({ name, text }) => ({ name, text })),
      } : {}),
    };
    const result = await streamAgent(body, updateAgentReplyStream, "/api/agent/audio/stream");
    pendingAgentDocuments = [];
    renderAgentDocuments();
    agentReplyDraft = "";
    agentReplyStatus = "正在更新界面";
    scheduleAgentReplyRender();
    setVoiceTrace("语音已发送", `${result.voice?.route || "当前 Agent 模型"} · ${result.voice?.model || ""}`, "ok");
    await applyAgentResult(result, selection, baseMessages);
  } catch (e) {
    agentMsgs.push({ role: "assistant", content: "语音直发失败：" + e.message, temporary: conversationMode === "temporary" });
    setVoiceTrace("出错", "直发链路：" + e.message, "err");
    showToast("语音直发失败：" + e.message, "err");
  } finally {
    agentBusy = false;
    agentReplyDraft = "";
    agentReplyStatus = "";
    btn.classList.remove("is-busy");
    setAgentConversationMode("standard");
    renderAgentSessionControls();
    el.placeholder = _agentPH || "让 AI 帮你改稿、续写、回退版本…";
    renderAgent();
    speakLatestAgentTurn();
    el.focus();
  }
}
function explainMicStartError(e) {
  if (e?.name === "NotAllowedError") {
    return "麦克风未获允许：请检查手机系统是否允许 Chrome 使用麦克风，并在浏览器站点设置中把当前网站的麦克风权限改为允许";
  }
  if (e?.name === "NotReadableError") return "麦克风正被其他通话或录音应用占用";
  if (e?.name === "NotFoundError") return "没有检测到可用麦克风";
  return "无法启动录音：" + (e?.message || e?.name || "未知错误");
}
async function toggleAgentMic() {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    showToast("浏览器不支持录音上传，请用新版 Chrome/Edge", "err");
    return;
  }
  if (!window.isSecureContext) {
    const msg = "当前页面不是安全上下文，浏览器不会开放麦克风。请通过 HTTPS 访问网站。";
    setVoiceTrace("出错", msg, "err");
    showToast(msg, "err");
    return;
  }
  if (agentMicOn) {
    agentMicOn = false;
    clearTimeout(agentMicTimer); agentMicTimer = null;
    try { agentRecorder?.stop(); } catch (e) {}
    setAgentMic(false);
    return;
  }
  if (micOn) { micOn = false; try { rec?.stop(); } catch (e) {} setMic(false); }
  const el = $("agentInput");
  el.value = "";
  if (_agentPH) el.placeholder = _agentPH;
  try {
    agentStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mimeType = bestAudioMime();
    agentChunks = [];
    agentRecorder = new MediaRecorder(agentStream, mimeType ? { mimeType } : undefined);
    agentRecorder.ondataavailable = e => { if (e.data && e.data.size) agentChunks.push(e.data); };
    agentRecorder.onstop = () => {
      clearTimeout(agentMicTimer); agentMicTimer = null;
      agentStream?.getTracks().forEach(t => t.stop());
      agentStream = null;
      const blob = new Blob(agentChunks, { type: mimeType || "audio/webm" });
      agentChunks = [];
      if (blob.size < 300) { showToast("录音太短", "err"); return; }
      if (voiceDirectToModel) {
        setVoiceTrace("正在直发", "录音已结束，准备上传给当前 Agent 模型");
        sendAgentAudio(blob);
      } else {
        setVoiceTrace("正在转写", "录音已结束，准备调用转写服务");
        transcribeAgentAudio(blob);
      }
    };
    agentRecorder.onerror = e => {
      agentMicOn = false;
      clearTimeout(agentMicTimer); agentMicTimer = null;
      setAgentMic(false);
      agentStream?.getTracks().forEach(t => t.stop());
      const msg = e.error?.message || e.error?.name || "未知错误";
      setVoiceTrace("出错", "录音链路：" + msg, "err");
      showToast("录音失败：" + msg, "err");
    };
    agentMicOn = true;
    setAgentMic(true);
    setVoiceTrace("录音中", voiceDirectToModel ? "结束后将直接发送给当前 Agent 模型" : "结束后将先转写为文字");
    agentRecorder.start();
    agentMicTimer = setTimeout(() => {
      if (!agentMicOn) return;
      showToast("已录满一分钟，正在结束录音", "ok");
      toggleAgentMic();
    }, 60000);
  } catch (e) {
    agentMicOn = false;
    setAgentMic(false);
    const extra = await micPermissionHint();
    const msg = explainMicStartError(e) + (extra ? "；" + extra : "");
    el.placeholder = msg;
    setVoiceTrace("出错", msg, "err");
    showToast(msg, "err");
  }
}
function setAgentMic(on) {
  const b = $("agentMicBtn"); if (!b) return;
  setIcon(b, on ? "square" : "mic");
  b.classList.toggle("on", on);
  b.title = on ? "正在录音，再按结束" : (voiceDirectToModel ? "语音直发给 AI" : "录音后转写");
}
function toggleAiTts() {
  aiTts = !aiTts;
  localStorage.setItem("aiTts", aiTts ? "1" : "0");
  setAiTtsBtn();
  if (!aiTts && "speechSynthesis" in window) speechSynthesis.cancel();
}
function setAiTtsBtn() {
  const b = $("aiTtsBtn"); if (!b) return;
  setIcon(b, aiTts ? "volume" : "mute");
  b.classList.toggle("on", aiTts);
  b.title = aiTts ? "自动朗读：开（点一下关）" : "自动朗读：关（点一下开）";
}
function speakAgentText(txt) {
  if (!aiTts || !txt || !txt.trim() || !("speechSynthesis" in window)) return;
  speechSynthesis.cancel(); // 新回复来了，打断上一段
  const u = new SpeechSynthesisUtterance(txt.trim());
  u.lang = "zh-CN"; u.rate = 1;
  speechSynthesis.speak(u);
}

/* ---------- 实体卡片 wiki（人物/地点…，喂给 AI 当结构化设定） ---------- */

let entitiesCache = [];
let entitiesCacheWorkId = null;
let entitiesCacheChapterId = null;
let editingEntityId = null;
let characterStateEntityId = null;
let characterStateChapterId = null;
let characterStateProposalId = null;
let characterStateData = null;
let entityImageEntityId = null;
let characterDetailTab = "profile";
let entityImagePromptMode = "latest";
let entityImageSettingsLoaded = false;
let semanticPopTimer = null;
let semanticRenderTimer = null;
let semanticMatcherCache = { signature: "", regex: null, byName: new Map(), entities: [] };
const entityImageObjectUrls = new Map();
const imageAssetObjectUrls = new Map();
let entityImageHistory = [];
let characterImageLibraryItems = [];
let characterImageFilter = "all";
let currentLightboxImageId = null;
const characterStateFields = [
  ["location", "所在地点", "characterStateLocation"],
  ["goal", "当前目标", "characterStateGoal"],
  ["emotion", "当前情绪", "characterStateEmotion"],
  ["physical", "身体状态", "characterStatePhysical"],
  ["information", "已知信息", "characterStateInformation"],
  ["relationships", "关系变化", "characterStateRelationships"],
  ["assets", "能力 / 物品", "characterStateAssets"],
  ["secrets", "秘密 / 承诺", "characterStateSecrets"],
  ["notes", "补充", "characterStateNotes"],
];

function semanticKindClass(kind) {
  return ({ 人物: "person", 地点: "place", 物品: "item", 组织: "org", 概念: "concept" })[kind] || "concept";
}
function regexEscape(value) { return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
function queueSemanticEditorRender() {
  clearTimeout(semanticRenderTimer);
  semanticRenderTimer = setTimeout(() => {
    semanticRenderTimer = null;
    renderSemanticEditor();
  }, 110);
}
function semanticMatcher() {
  const seen = new Set();
  const entities = entitiesCache
    .filter(entity => entity && entity.name && !seen.has(entity.name.toLocaleLowerCase()) && seen.add(entity.name.toLocaleLowerCase()))
    .sort((a, b) => b.name.length - a.name.length);
  const signature = entities.map(entity => `${entity.id}:${entity.name}`).join("|");
  if (semanticMatcherCache.signature === signature) return semanticMatcherCache;
  const byName = new Map(entities.map(entity => [entity.name.toLocaleLowerCase(), entity]));
  let regex = null;
  if (entities.length) {
    try { regex = new RegExp(entities.map(entity => regexEscape(entity.name)).join("|"), "giu"); }
    catch (e) { regex = null; }
  }
  semanticMatcherCache = { signature, regex, byName, entities };
  return semanticMatcherCache;
}
function renderSemanticEditor() {
  const editor = $("content"), textHost = $("semanticText"), layer = $("semanticLayer");
  if (!editor || !textHost || !layer) return;
  const text = editor.value || "";
  const { entities, byName, regex } = semanticMatcher();
  if (!text || !entities.length) {
    textHost.textContent = "";
    layer.classList.add("hidden");
    editor.classList.remove("semantic-active");
    return;
  }
  if (!regex) { textHost.textContent = text; return; }
  regex.lastIndex = 0;
  let html = "", last = 0, match;
  while ((match = regex.exec(text))) {
    const value = match[0];
    const entity = byName.get(value.toLocaleLowerCase());
    if (!entity || (value.length === 1 && text[match.index - 1] !== "@")) continue;
    html += esc(text.slice(last, match.index));
    html += `<mark class="semantic-token semantic-${semanticKindClass(entity.kind)}" data-entity-id="${entity.id}" tabindex="-1">${esc(value)}</mark>`;
    last = match.index + value.length;
  }
  html += esc(text.slice(last));
  textHost.innerHTML = html;
  layer.classList.remove("hidden");
  editor.classList.add("semantic-active");
  syncSemanticEditor();
}
function syncSemanticEditor() {
  const editor = $("content"), textHost = $("semanticText");
  if (!editor || !textHost) return;
  textHost.style.width = `${editor.clientWidth}px`;
  textHost.style.transform = `translate(${-editor.scrollLeft}px, ${-editor.scrollTop}px)`;
}
function hideSemanticPop(delay = 100) {
  clearTimeout(semanticPopTimer);
  semanticPopTimer = setTimeout(() => $("semanticPop")?.classList.add("hidden"), delay);
}
function showSemanticPop(eid, anchor) {
  clearTimeout(semanticPopTimer);
  const entity = entitiesCache.find(item => item.id === eid);
  const pop = $("semanticPop");
  if (!entity || !pop || !anchor) return;
  const state = entity.kind === "人物" ? characterStateBrief(entity.current_state) : "";
  pop.innerHTML = `
    <div class="mp-head"><b>${esc(entity.name)}</b> · ${esc(entity.kind)}</div>
    ${entity.summary ? `<div class="mp-sum">${esc(entity.summary)}</div>` : ""}
    ${state ? `<div class="mp-state">${esc(state)}</div>` : ""}
    ${entity.detail ? `<div class="mp-det">${esc(entity.detail.slice(0, 180))}${entity.detail.length > 180 ? "…" : ""}</div>` : ""}
    <div class="semantic-pop-actions"><button onclick="openSemanticEntity(${entity.id})">${entity.kind === "人物" ? "打开动态 / 关系 / 历史" : "打开设定卡"}</button></div>`;
  pop.classList.remove("hidden");
  const rect = anchor.getBoundingClientRect();
  const width = pop.offsetWidth, height = pop.offsetHeight;
  pop.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - width - 8))}px`;
  pop.style.top = `${Math.max(8, rect.bottom + height + 8 > window.innerHeight ? rect.top - height - 8 : rect.bottom + 8)}px`;
}
async function openSemanticEntity(eid) {
  $("semanticPop")?.classList.add("hidden");
  const entity = entitiesCache.find(item => item.id === eid);
  if (!entity) return;
  if (entity.kind === "人物") await openCharacterState(eid);
  else { await openWiki(); startEditEntity(eid); }
}

function entityInitial(entity) { return Array.from((entity?.name || "?").trim())[0] || "?"; }
function clearEntityImageCache() {
  entityImageObjectUrls.forEach(url => URL.revokeObjectURL(url));
  entityImageObjectUrls.clear();
  imageAssetObjectUrls.forEach(url => URL.revokeObjectURL(url));
  imageAssetObjectUrls.clear();
}
function entityPortraitHtml(entity, large = false) {
  return `<button type="button" class="character-portrait${large ? " character-portrait-lg" : ""}" onclick="openEntityImage(${entity.id})" title="打开 ${esc(entity.name)} 的形象与图库">
    <span>${esc(entityInitial(entity))}</span>
    ${entity?.has_image ? `<img class="hidden" data-entity-image="${entity.id}" alt="${esc(entity.name)}的角色图">` : ""}
  </button>`;
}
async function entityImageUrl(eid, force = false) {
  if (force && entityImageObjectUrls.has(eid)) {
    URL.revokeObjectURL(entityImageObjectUrls.get(eid));
    entityImageObjectUrls.delete(eid);
  }
  if (entityImageObjectUrls.has(eid)) return entityImageObjectUrls.get(eid);
  const response = await fetch(`/api/entities/${eid}/image`, {
    headers: token ? { Authorization: "Bearer " + token } : {}, cache: "no-store",
  });
  if (!response.ok) return "";
  const url = URL.createObjectURL(await response.blob());
  entityImageObjectUrls.set(eid, url);
  return url;
}
async function hydrateEntityImages(root = document, force = false) {
  const nodes = [...root.querySelectorAll("img[data-entity-image]")];
  const ids = [...new Set(nodes.map(node => +node.dataset.entityImage).filter(Boolean))];
  await Promise.all(ids.map(async eid => {
    const url = await entityImageUrl(eid, force).catch(() => "");
    if (!url) return;
    root.querySelectorAll(`img[data-entity-image="${eid}"]`).forEach(img => {
      img.src = url; img.classList.remove("hidden");
      const initial = img.previousElementSibling;
      if (initial) initial.classList.add("hidden");
      const loading = img.nextElementSibling;
      if (loading) loading.classList.add("hidden");
    });
  }));
}
function defaultCharacterImagePrompt(entity) {
  const parts = [
    "Create a polished full-body character concept illustration for a novel.",
    "Show one clearly identifiable character in a vertical 3:4 composition, complete silhouette and visible feet, natural posture, coherent costume design, intentional lighting, harmonious color palette, and an uncluttered atmospheric background.",
    "Preserve the canonical details below and express personality through posture, gaze, costume, props, lighting and composition. Do not invent conflicting traits.",
    "No text, letters, captions, logos, signatures, watermarks, split panels, duplicate people, extra limbs, malformed hands, or cropped feet.",
    `Character name for identity reference only (do not render it as text): ${entity.name}.`,
  ];
  if (entity.summary) parts.push(`Canonical character summary (source language): ${entity.summary}`);
  if (entity.detail) parts.push(`Canonical appearance, personality and background details (source language): ${entity.detail}`);
  const state = entity.current_state || characterStateData?.current_state || {};
  const live = [["所在地点", state.location], ["当前目标", state.goal], ["情绪", state.emotion], ["身体状态", state.physical], ["能力与物品", state.assets], ["补充状态", state.notes]]
    .filter(([, value]) => value).map(([label, value]) => `${label}：${value}`).join("；");
  if (live) parts.push(`Current story-state cues (source language; use only visually relevant details): ${live}`);
  return parts.join("\n");
}

async function imageAssetUrl(imageId, force = false) {
  if (force && imageAssetObjectUrls.has(imageId)) {
    URL.revokeObjectURL(imageAssetObjectUrls.get(imageId));
    imageAssetObjectUrls.delete(imageId);
  }
  if (imageAssetObjectUrls.has(imageId)) return imageAssetObjectUrls.get(imageId);
  const response = await fetch(`/api/entity-images/${imageId}/content`, {
    headers: token ? { Authorization: "Bearer " + token } : {}, cache: "no-store",
  });
  if (!response.ok) return "";
  const url = URL.createObjectURL(await response.blob());
  imageAssetObjectUrls.set(imageId, url);
  return url;
}
async function hydrateImageAssets(root = document) {
  const nodes = [...root.querySelectorAll("img[data-image-asset]")];
  await Promise.all(nodes.map(async img => {
    const url = await imageAssetUrl(+img.dataset.imageAsset).catch(() => "");
    if (url) { img.src = url; img.classList.remove("hidden"); }
  }));
}
function imageAssetCard(item, context = "person") {
  const time = item.created_at ? new Date(item.created_at * 1000).toLocaleString() : "";
  const openAction = context === "library" ? `openCharacterFromImageLibrary(${item.entity_id})` : `openImageAssetLightbox(${item.id})`;
  const title = context === "library" ? `进入 ${item.entity_name || "人物"} 的人物档案` : (item.prompt || "点击查看大图");
  return `<article class="image-asset-card${item.selected ? " selected" : ""}" onclick="${openAction}" title="${esc(title)}">
    <div class="image-asset-thumb"><img class="hidden" data-image-asset="${item.id}" alt="${esc(item.entity_name || "角色")}的形象"><span>${svg("image")}</span>${item.selected ? '<b class="image-selected-badge">当前主图</b>' : ""}</div>
    <div class="image-asset-meta"><b>${esc(item.entity_name || "角色形象")}</b><small>${esc([item.size, item.model, time].filter(Boolean).join(" · "))}</small></div>
    <div class="image-asset-actions">
      ${item.selected ? '<span>正在使用</span>' : `<button onclick="event.stopPropagation();selectCharacterImage(${item.id})">设为主图</button>`}
      <button onclick="event.stopPropagation();reuseCharacterImagePrompt(${item.id})">复用</button>
      <button onclick="event.stopPropagation();downloadCharacterImageAsset(${item.id})">下载</button>
      <button class="danger-lite" onclick="event.stopPropagation();deleteCharacterImageAsset(${item.id})">删除</button>
    </div>
  </article>`;
}
function renderEntityImageHistory() {
  const host = $("entityImageHistory");
  if (!host) return;
  $("entityImageHistoryCount").textContent = `${entityImageHistory.length} 张`;
  $("characterImagesTabCount").textContent = entityImageHistory.length;
  host.innerHTML = entityImageHistory.length ? entityImageHistory.map(item => imageAssetCard(item, "person")).join("") : '<div class="empty">生成后的图片会保存在这里，不会覆盖旧图。</div>';
  hydrateImageAssets(host);
}
async function loadEntityImageHistory() {
  if (!entityImageEntityId) { entityImageHistory = []; renderEntityImageHistory(); return []; }
  const data = await api(`/api/entities/${entityImageEntityId}/images`, { method: "GET" });
  entityImageHistory = data.items || [];
  renderEntityImageHistory();
  return entityImageHistory;
}
function renderEntityImagePreview(entity) {
  const host = $("entityImagePreview");
  if (!host) return;
  host.innerHTML = entity?.has_image
    ? `<img class="hidden" data-entity-image="${entity.id}" alt="${esc(entity.name)}的角色图"><span>正在载入角色图…</span>`
    : "尚未生成角色图";
  if (entity?.has_image) hydrateEntityImages(host);
  $("entityImageDeleteBtn").classList.toggle("hidden", !entity?.has_image);
}
async function openEntityImage(eid) { await openCharacterState(eid, "images"); }
function closeEntityImage() { switchCharacterDetailTab("profile"); }
function markEntityImagePromptCustom() {
  entityImagePromptMode = "custom";
  const status = $("entityImagePromptStatus");
  status.textContent = "已补充自定义要求；生成时仍会自动合并最新人物设定";
  status.classList.add("custom");
}
function refreshEntityImagePromptFromLatest(showFeedback = false) {
  const entity = characterStateData?.entity || entitiesCache.find(item => item.id === entityImageEntityId);
  if (!entity) return;
  $("entityImagePrompt").value = defaultCharacterImagePrompt({ ...entity, current_state: characterStateData?.current_state || entity.current_state });
  entityImagePromptMode = "latest";
  const status = $("entityImagePromptStatus");
  status.textContent = "已读取最新人物设定与当前章节状态";
  status.classList.remove("custom");
  if (showFeedback) showToast("已重新读取最新人物设定", "ok");
}
async function prepareEntityImagePanel(forcePrompt = false) {
  const entity = characterStateData?.entity || entitiesCache.find(item => item.id === characterStateEntityId);
  if (!entity) return;
  entityImageEntityId = entity.id;
  if (forcePrompt || !$("entityImagePrompt").value || entityImagePromptMode === "latest") refreshEntityImagePromptFromLatest(false);
  $("entityImageMsg").textContent = "";
  if (!entityImageSettingsLoaded) {
    try {
      const settings = await api("/api/settings", { method: "GET" });
      const wanted = settings.image_size || "1024x1536";
      $("entityImageSize").value = [...$("entityImageSize").options].some(option => option.value === wanted) ? wanted : "1024x1536";
    } catch (e) { $("entityImageSize").value = "1024x1536"; }
    entityImageSettingsLoaded = true;
  }
  renderEntityImagePreview(entity);
  await loadEntityImageHistory().catch(e => { $("entityImageMsg").textContent = "人物形象加载失败：" + e.message; });
}
async function switchCharacterDetailTab(tab = "profile") {
  characterDetailTab = tab === "images" ? "images" : "profile";
  $("characterProfilePanel").classList.toggle("hidden", characterDetailTab !== "profile");
  $("characterImagesPanel").classList.toggle("hidden", characterDetailTab !== "images");
  $("characterProfileTab").classList.toggle("active", characterDetailTab === "profile");
  $("characterImagesTab").classList.toggle("active", characterDetailTab === "images");
  $("characterProfileTab").setAttribute("aria-selected", String(characterDetailTab === "profile"));
  $("characterImagesTab").setAttribute("aria-selected", String(characterDetailTab === "images"));
  $("characterStateAnalyzeBtn").classList.toggle("hidden", characterDetailTab === "images" || !characterStateChapterId);
  if (characterDetailTab === "images") await prepareEntityImagePanel();
}
async function polishEntityImagePrompt() {
  if (!entityImageEntityId) return;
  const button = $("entityImagePolishBtn");
  $("entityImageMsg").textContent = "";
  busy(button, true, "正在整理");
  try {
    const result = await api(`/api/entities/${entityImageEntityId}/image/prompt`, { body: {
      prompt: $("entityImagePrompt").value.trim(), style: $("entityImageStyle").value.trim(), chapter_id: characterStateChapterId || currentChapterId,
    }});
    $("entityImagePrompt").value = result.prompt || $("entityImagePrompt").value;
    entityImagePromptMode = "custom";
    $("entityImagePromptStatus").textContent = "已结合最新人物档案整理，可继续修改";
    $("entityImagePromptStatus").classList.add("custom");
    $("entityImageMsg").textContent = "已结合人物卡整理成英文生图提示词，你仍可继续修改";
    showToast("角色提示词已整理", "ok");
  } catch (e) { $("entityImageMsg").textContent = e.message; }
  finally { busy(button, false, "AI 整理为英文生图词"); applyIcons(); }
}
async function generateEntityImage() {
  if (!entityImageEntityId) return;
  const button = $("entityImageGenerateBtn");
  $("entityImageMsg").textContent = "";
  busy(button, true, "生成中");
  try {
    const result = await api(`/api/entities/${entityImageEntityId}/image/generate`, {
      body: { prompt: $("entityImagePrompt").value.trim(), style: $("entityImageStyle").value.trim(),
        size: $("entityImageSize").value, chapter_id: characterStateChapterId || currentChapterId },
    });
    const entity = entitiesCache.find(item => item.id === entityImageEntityId);
    if (entity) Object.assign(entity, result);
    if (characterStateData?.entity?.id === entityImageEntityId) Object.assign(characterStateData.entity, result);
    await entityImageUrl(entityImageEntityId, true);
    renderEntityImagePreview(entity || characterStateData?.entity);
    await loadEntityImageHistory();
    if (!$("wikiOverlay").classList.contains("hidden")) renderWikiList();
    if (!$("characterStateOverlay").classList.contains("hidden") && characterStateData) renderCharacterState(characterStateData);
    $("entityImageMsg").textContent = "角色图已生成，旧图仍保留在下方历史图库";
    showToast("角色图已生成并加入图库", "ok");
  } catch (e) { $("entityImageMsg").textContent = e.message; }
  finally { busy(button, false, "生成角色图"); }
}
async function deleteEntityImage() {
  if (!entityImageEntityId || !await askCard({ title: "删除这张角色图？", msg: "人物设定和历史不会删除。", okText: "删除", danger: true })) return;
  try {
    await api(`/api/entities/${entityImageEntityId}/image`, { method: "DELETE" });
    if (entityImageObjectUrls.has(entityImageEntityId)) URL.revokeObjectURL(entityImageObjectUrls.get(entityImageEntityId));
    entityImageObjectUrls.delete(entityImageEntityId);
    const items = await loadEntityImageHistory();
    const entity = entitiesCache.find(item => item.id === entityImageEntityId);
    if (entity) entity.has_image = items.length > 0;
    if (characterStateData?.entity?.id === entityImageEntityId) characterStateData.entity.has_image = items.length > 0;
    renderEntityImagePreview(entity || characterStateData?.entity);
    if (!$("wikiOverlay").classList.contains("hidden")) renderWikiList();
    if (!$("characterStateOverlay").classList.contains("hidden") && characterStateData) renderCharacterState(characterStateData);
    showToast(items.length ? "当前图已删除，已自动切换到上一张" : "角色图已删除", "ok");
  } catch (e) { $("entityImageMsg").textContent = e.message; }
}

async function openCurrentEntityImageLightbox() {
  if (!entityImageEntityId) return;
  if (!entityImageHistory.length) await loadEntityImageHistory().catch(() => {});
  const item = entityImageHistory.find(row => row.selected) || entityImageHistory[0];
  if (item) openImageAssetLightbox(item.id);
}
async function openImageAssetLightbox(imageId) {
  const item = [...entityImageHistory, ...characterImageLibraryItems].find(row => row.id === imageId);
  if (!item) return;
  const url = await imageAssetUrl(imageId).catch(() => "");
  if (!url) { showToast("图片载入失败", "err"); return; }
  $("imageLightboxImg").src = url;
  $("imageLightboxTitle").textContent = `${item.entity_name || "角色形象"}${item.selected ? " · 当前主图" : ""}`;
  $("imageLightboxPrompt").textContent = item.prompt || "没有保存提示词";
  currentLightboxImageId = imageId;
  $("imageLightbox").classList.remove("hidden");
}
function lightboxImageItem() {
  return [...entityImageHistory, ...characterImageLibraryItems].find(row => row.id === currentLightboxImageId);
}
async function copyLightboxPrompt() {
  const prompt = lightboxImageItem()?.prompt || "";
  if (!prompt) { showToast("这张图没有保存提示词", "err"); return; }
  await navigator.clipboard?.writeText(prompt).catch(() => {}); showToast("提示词已复制", "ok");
}
async function reuseLightboxPrompt() {
  const item = lightboxImageItem(); if (!item) return;
  closeImageLightbox();
  await reuseCharacterImagePrompt(item.id);
}
async function downloadLightboxImage() {
  const item = lightboxImageItem(); if (!item) return;
  await downloadCharacterImageAsset(item.id);
}
async function reuseCharacterImagePrompt(imageId) {
  const item = [...entityImageHistory, ...characterImageLibraryItems].find(row => row.id === imageId);
  if (!item) return;
  closeCharacterImageLibrary();
  await openCharacterState(item.entity_id, "images");
  $("entityImagePrompt").value = item.prompt || $("entityImagePrompt").value;
  entityImagePromptMode = "custom";
  $("entityImagePromptStatus").textContent = "已复用历史提示词；生成时会以最新人物设定覆盖其中的冲突信息";
  $("entityImagePromptStatus").classList.add("custom");
  $("entityImageMsg").textContent = "可以调整后再次生成";
}
async function downloadCharacterImageAsset(imageId) {
  const item = [...entityImageHistory, ...characterImageLibraryItems].find(row => row.id === imageId);
  if (!item) return;
  const url = await imageAssetUrl(item.id).catch(() => "");
  if (!url) { showToast("图片载入失败", "err"); return; }
  const anchor = document.createElement("a"); anchor.href = url;
  anchor.download = `${(item.entity_name || "角色形象").replace(/[\\/:*?"<>|]+/g, "-")}-${item.id}.png`; anchor.click();
}
async function openLightboxCharacterProfile() {
  const item = lightboxImageItem(); if (!item) return;
  closeImageLightbox(); closeCharacterImageLibrary();
  await openCharacterState(item.entity_id, "profile");
}
function closeImageLightbox() { $("imageLightbox").classList.add("hidden"); currentLightboxImageId = null; }
async function openCharacterFromImageLibrary(entityId) {
  closeCharacterImageLibrary();
  await openCharacterState(entityId, "images");
}
async function openCharacterImageLibrary(entityId = null) {
  if (!currentWorkId) { showToast("先选择作品", "err"); return; }
  characterImageFilter = entityId ? String(entityId) : "all";
  $("characterImageLibraryOverlay").classList.remove("hidden");
  await loadCharacterImageLibrary();
}
function closeCharacterImageLibrary() { $("characterImageLibraryOverlay").classList.add("hidden"); }
function renderCharacterImageLibrary() {
  const counts = new Map();
  characterImageLibraryItems.forEach(item => counts.set(item.entity_id, (counts.get(item.entity_id) || 0) + 1));
  const characters = [...new Map(characterImageLibraryItems.map(item => [item.entity_id, item.entity_name || "未命名角色"])).entries()];
  $("characterImageFilters").innerHTML = characters.map(([id, name]) =>
    `<button class="${characterImageFilter === String(id) ? "active" : ""}" onclick="setCharacterImageFilter('${id}')">${esc(name)} <small>${counts.get(id)}</small></button>`).join("");
  const allButton = document.querySelector('[data-image-filter="all"]');
  if (allButton) allButton.classList.toggle("active", characterImageFilter === "all");
  const visible = characterImageFilter === "all" ? characterImageLibraryItems : characterImageLibraryItems.filter(item => String(item.entity_id) === characterImageFilter);
  const host = $("characterImageLibraryGrid");
  host.innerHTML = visible.length ? visible.map(item => imageAssetCard(item, "library")).join("") : '<div class="empty">这里还没有角色图片。先从人物档案生成一张吧。</div>';
  hydrateImageAssets(host);
}
async function loadCharacterImageLibrary() {
  if (!currentWorkId) return;
  try {
    const data = await api(`/api/works/${currentWorkId}/images?category=characters`, { method: "GET" });
    characterImageLibraryItems = data.items || [];
    renderCharacterImageLibrary();
  } catch (e) { $("characterImageLibraryGrid").innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}
function setCharacterImageFilter(value) { characterImageFilter = String(value || "all"); renderCharacterImageLibrary(); }
async function selectCharacterImage(imageId) {
  try {
    const result = await api(`/api/entity-images/${imageId}/select`, { body: {} });
    const eid = result.entity_id;
    if (entityImageObjectUrls.has(eid)) URL.revokeObjectURL(entityImageObjectUrls.get(eid));
    entityImageObjectUrls.delete(eid);
    const entity = entitiesCache.find(item => item.id === eid);
    if (entity) entity.has_image = true;
    if (characterStateData?.entity?.id === eid) characterStateData.entity.has_image = true;
    if (entityImageEntityId === eid) {
      await loadEntityImageHistory();
      renderEntityImagePreview(entity || characterStateData?.entity);
    }
    if (!$("characterImageLibraryOverlay").classList.contains("hidden")) await loadCharacterImageLibrary();
    if (!$("wikiOverlay").classList.contains("hidden")) renderWikiList();
    if (!$("characterStateOverlay").classList.contains("hidden") && characterStateData) renderCharacterState(characterStateData);
    showToast("已设为角色主形象", "ok");
  } catch (e) { showToast(e.message, "err"); }
}
async function deleteCharacterImageAsset(imageId) {
  if (!await askCard({ title: "从角色图库删除这张图？", msg: "删除后无法恢复；人物设定不会受影响。", okText: "删除", danger: true })) return;
  try {
    const result = await api(`/api/entity-images/${imageId}`, { method: "DELETE" });
    if (imageAssetObjectUrls.has(imageId)) URL.revokeObjectURL(imageAssetObjectUrls.get(imageId));
    imageAssetObjectUrls.delete(imageId);
    if (entityImageObjectUrls.has(result.entity_id)) URL.revokeObjectURL(entityImageObjectUrls.get(result.entity_id));
    entityImageObjectUrls.delete(result.entity_id);
    const entity = entitiesCache.find(item => item.id === result.entity_id);
    if (entity) entity.has_image = !!result.has_image;
    if (characterStateData?.entity?.id === result.entity_id) characterStateData.entity.has_image = !!result.has_image;
    if (entityImageEntityId === result.entity_id) {
      await loadEntityImageHistory();
      renderEntityImagePreview(entity || characterStateData?.entity);
    }
    if (!$("characterImageLibraryOverlay").classList.contains("hidden")) await loadCharacterImageLibrary();
    if (!$("wikiOverlay").classList.contains("hidden")) renderWikiList();
    if (!$("characterStateOverlay").classList.contains("hidden") && characterStateData) renderCharacterState(characterStateData);
    closeImageLightbox();
    showToast("图片已从图库删除", "ok");
  } catch (e) { showToast(e.message, "err"); }
}

function entityChapterQuery() { return currentChapterId ? `?chapter_id=${currentChapterId}` : ""; }
async function loadWikiEntities() {
  if (!currentWorkId) { entitiesCache = []; entitiesCacheWorkId = null; entitiesCacheChapterId = null; renderSemanticEditor(); return; }
  entitiesCache = await api(`/api/works/${currentWorkId}/entities${entityChapterQuery()}`, { method: "GET" });
  entitiesCacheWorkId = currentWorkId;
  entitiesCacheChapterId = currentChapterId || null;
  renderSemanticEditor();
}

async function openWiki() {
  if (!currentWorkId) { showToast("先选一个作品", "err"); return; }
  try {
    await loadWikiEntities();
    renderWikiList();
    resetEntityForm();
    $("wikiOverlay").classList.remove("hidden");
  } catch (e) { showToast("加载失败：" + e.message, "err"); }
}
function closeWiki() { $("wikiOverlay").classList.add("hidden"); }
function characterStateBrief(state) {
  const parts = characterStateFields
    .filter(([key]) => state && state[key])
    .slice(0, 3)
    .map(([key, label]) => `${label}：${state[key]}`);
  const text = parts.join(" · ");
  return text.length > 130 ? text.slice(0, 130) + "…" : text;
}
function characterStateSnapshot(state) {
  const items = characterStateFields.filter(([key]) => state && state[key]).map(([key, label]) =>
    `<span><b>${esc(label)}</b>${esc(state[key])}</span>`);
  return items.length ? `<div class="character-state-snapshot">${items.join("")}</div>` : '<div class="character-state-empty">尚未记录动态状态</div>';
}
function renderWikiList() {
  $("wikiList").innerHTML = entitiesCache.length ? entitiesCache.map(e => `
    <div class="ent">
      <div class="ent-main">
        ${e.kind === "人物" ? entityPortraitHtml(e) : ""}
        <div class="ent-copy">
          <div class="ent-h"><b>${esc(e.kind)}</b> · ${esc(e.name)}</div>
          ${e.summary ? `<div class="ent-s">${esc(e.summary)}</div>` : ""}
          ${e.detail ? `<div class="ent-d">${esc(e.detail)}</div>` : ""}
          ${e.kind === "人物" && currentChapterId ? `<div class="ent-state">${esc(characterStateBrief(e.current_state) || "截至本章未记录动态状态")}</div>` : ""}
          <div class="ent-a">
            ${e.kind === "人物" ? `<button class="ic" onclick="openCharacterState(${e.id})">人物档案${e.pending_count ? ` · 待确认 ${e.pending_count}` : ""}</button>` : ""}
            <button class="ic" onclick="startEditEntity(${e.id})">编辑</button>
            <button class="ic" onclick="delEntity(${e.id})">删除</button>
          </div>
        </div>
      </div>
    </div>`).join("") : '<div class="empty">还没有人物/设定卡片</div>';
  hydrateEntityImages($("wikiList"));
}
function resetEntityForm() {
  editingEntityId = null;
  $("entName").value = ""; $("entSummary").value = ""; $("entDetail").value = "";
  $("entKind").selectedIndex = 0;
  $("entSaveBtn").textContent = "新增";
  $("entInsBtn").classList.add("hidden");
}
async function saveEntity() {
  const name = $("entName").value.trim();
  if (!name) { $("entMsg").textContent = "名称不能为空"; return; }
  const body = { name, kind: $("entKind").value,
    summary: $("entSummary").value, detail: $("entDetail").value };
  try {
    if (editingEntityId) {
      await api(`/api/entities/${editingEntityId}`, { method: "PUT", body });
    } else {
      await api(`/api/works/${currentWorkId}/entities`, { method: "POST", body });
    }
    await loadWikiEntities();
    renderWikiList(); resetEntityForm();
    $("entMsg").textContent = "已保存";
    setTimeout(() => { $("entMsg").textContent = ""; }, 1200);
  } catch (e) { $("entMsg").textContent = e.message; }
}
function startEditEntity(eid) {
  const e = entitiesCache.find(x => x.id === eid);
  if (!e) return;
  editingEntityId = eid;
  $("entName").value = e.name;
  $("entKind").value = e.kind;
  $("entSummary").value = e.summary || "";
  $("entDetail").value = e.detail || "";
  $("entSaveBtn").textContent = "保存修改";
  $("entInsBtn").classList.remove("hidden");
  $("entName").focus();
}
async function delEntity(eid) {
  if (!await askCard({ title: "删除这张卡片？", okText: "删除", danger: true })) return;
  await api(`/api/entities/${eid}`, { method: "DELETE" });
  if (entityImageObjectUrls.has(eid)) URL.revokeObjectURL(entityImageObjectUrls.get(eid));
  entityImageObjectUrls.delete(eid);
  await loadWikiEntities();
  if (editingEntityId === eid) resetEntityForm();
  renderWikiList();
}
function insertEntity() {
  const name = $("entName").value.trim();
  if (!name) { $("entMsg").textContent = "先填名称"; return; }
  const el = $("content");
  el.setRangeText("@" + name, el.selectionStart, el.selectionEnd, "end");
  onContentInput();
  el.focus();
}

function characterStateLabel(chapter) {
  return chapter ? `第${chapter.ord}章《${chapter.title || "无标题"}》` : "未选择章节";
}
function characterStateSource(source) {
  return ({ manual: "手动记录", ai_confirmed: "AI 提议已采纳", ai_edited: "AI 提议编辑后采纳" })[source] || "";
}
function setCharacterStateForm(state, summary = "", evidence = "", proposalId = null) {
  for (const [key, , id] of characterStateFields) $(id).value = (state && state[key]) || "";
  $("characterStateSummary").value = summary || "";
  $("characterStateEvidence").value = evidence || "";
  characterStateProposalId = proposalId;
  $("characterStateSaveBtn").textContent = proposalId ? "确认并保存" : "保存为本章状态";
}
function characterStateFormValue() {
  const state = {};
  for (const [key, , id] of characterStateFields) state[key] = $(id).value.trim();
  return state;
}
function characterStateOptions() {
  if (!chapters.length) return '<option value="">尚未创建章节（仍可编辑设定和形象）</option>';
  return chapters.map(chapter =>
    `<option value="${chapter.id}" ${chapter.id === characterStateChapterId ? "selected" : ""}>${esc(characterStateLabel(chapter))}</option>`
  ).join("");
}
async function openCharacterState(eid, tab = "profile") {
  const changedPerson = characterStateEntityId !== eid;
  cancelCharacterBaseProfile();
  characterStateEntityId = eid;
  entityImageEntityId = eid;
  characterStateChapterId = currentChapterId || chapters[chapters.length - 1]?.id || null;
  characterStateProposalId = null;
  if (changedPerson) {
    entityImageHistory = [];
    $("entityImagePrompt").value = "";
    $("entityImageStyle").value = "";
    entityImagePromptMode = "latest";
    renderEntityImageHistory();
  }
  $("characterStateOverlay").classList.remove("hidden");
  await loadCharacterState();
  await switchCharacterDetailTab(tab);
  if (characterDetailTab === "profile") await loadEntityImageHistory().catch(() => {});
}
function closeCharacterState() {
  cancelCharacterBaseProfile();
  $("characterStateOverlay").classList.add("hidden");
  characterStateEntityId = null;
  entityImageEntityId = null;
  characterStateData = null;
  characterStateProposalId = null;
  characterDetailTab = "profile";
}
async function changeCharacterStateChapter() {
  characterStateChapterId = +$("characterStateChapter").value || null;
  characterStateProposalId = null;
  await loadCharacterState();
}
function renderCharacterState(data) {
  characterStateData = data;
  const entity = data.entity;
  $("characterStateTitle").textContent = `${entity.name} · 人物档案`;
  $("characterStateChapter").innerHTML = characterStateOptions();
  $("characterStateChapter").disabled = !chapters.length;
  $("characterStateMeta").textContent = characterStateLabel(data.target_chapter);
  $("characterStateBase").innerHTML = `
    <div><b>基础设定</b>${entity.summary ? ` · ${esc(entity.summary)}` : ""}</div>
    ${entity.detail ? `<div>${esc(entity.detail)}</div>` : ""}`;
  $("characterStatePortrait").innerHTML = `<span>${esc(entityInitial(entity))}</span>${entity.has_image ? `<img class="hidden" data-entity-image="${entity.id}" alt="${esc(entity.name)}的角色图">` : ""}`;
  $("characterStatePortrait").onclick = () => switchCharacterDetailTab("images");
  $("characterStatePortrait").title = "查看这个人物的主形象与全部历史图片";
  const version = data.state_version;
  $("characterStateSource").textContent = version
    ? `${characterStateSource(version.source)} · 生效于${characterStateLabel({ ord: version.chapter_ord, title: version.chapter_title })}`
    : "尚无已确认状态";
  setCharacterStateForm(data.current_state || {});
  for (const [, , id] of characterStateFields) $(id).disabled = !characterStateChapterId;
  $("characterStateSummary").disabled = !characterStateChapterId;
  $("characterStateEvidence").disabled = !characterStateChapterId;
  $("characterStateSaveBtn").disabled = !characterStateChapterId;
  $("characterStateAnalyzeBtn").classList.toggle("hidden", characterDetailTab === "images" || !characterStateChapterId);
  const proposals = (data.proposals || []).filter(p => p.status === "pending");
  $("characterStateProposals").innerHTML = proposals.map(p => `
    <div class="character-state-proposal">
      <div class="character-state-proposal-head"><b>AI 待确认</b><span>${esc(p.change_summary || "状态更新")}</span></div>
      ${p.evidence ? `<div class="character-state-proof">${esc(p.evidence)}</div>` : ""}
      ${characterStateSnapshot(p.state)}
      <div class="character-state-actions">
        <button onclick="acceptCharacterStateProposal(${p.id})">采纳</button>
        <button onclick="editCharacterStateProposal(${p.id})">编辑后采纳</button>
        <button class="danger-lite" onclick="rejectCharacterStateProposal(${p.id})">忽略</button>
      </div>
    </div>`).join("");
  const relations = data.relations || [];
  $("characterStateRelations").innerHTML = relations.length ? relations.map(item => {
    const outgoing = item.from_entity_id === entity.id;
    const other = outgoing ? item.to_name : item.from_name;
    return `<div class="character-relation"><b>${outgoing ? "→" : "←"} ${esc(other)}</b> · ${esc(item.relation)}${item.detail ? `<small>${esc(item.detail)}</small>` : ""}</div>`;
  }).join("") : '<div class="empty">尚未记录人物关系；可在“故事资料 → 人物关系”中添加。</div>';
  const history = data.history || [];
  $("characterStateHistory").innerHTML = history.length ? history.map(item => `
    <div class="character-state-history-item">
      <div><b>${esc(characterStateLabel({ ord: item.chapter_ord, title: item.chapter_title }))}</b><span>${esc(characterStateSource(item.source))}</span></div>
      ${item.change_summary ? `<p>${esc(item.change_summary)}</p>` : ""}
      ${item.evidence ? `<small>${esc(item.evidence)}</small>` : ""}
      ${characterStateSnapshot(item.state)}
    </div>`).join("") : '<div class="empty">尚无成长记录</div>';
  if (entity.has_image) hydrateEntityImages($("characterStateOverlay"));
}
async function loadCharacterState() {
  if (!characterStateEntityId) return;
  try {
    const query = characterStateChapterId ? `?chapter_id=${characterStateChapterId}` : "";
    const data = await api(`/api/entities/${characterStateEntityId}/state-history${query}`, { method: "GET" });
    renderCharacterState(data);
  } catch (e) {
    $("characterStateMsg").textContent = e.message;
  }
}
function editCharacterBaseProfile() {
  const entity = characterStateData?.entity;
  if (!entity) return;
  $("characterBaseName").value = entity.name || "";
  $("characterBaseSummary").value = entity.summary || "";
  $("characterBaseDetail").value = entity.detail || "";
  $("characterBaseMsg").textContent = "";
  $("characterStateBase").classList.add("hidden");
  $("characterBaseEditor").classList.remove("hidden");
  $("characterBaseName").focus();
}
function cancelCharacterBaseProfile() {
  $("characterBaseEditor").classList.add("hidden");
  $("characterStateBase").classList.remove("hidden");
}
async function saveCharacterBaseProfile() {
  const name = $("characterBaseName").value.trim();
  if (!name) { $("characterBaseMsg").textContent = "人物姓名不能为空"; return; }
  try {
    await api(`/api/entities/${characterStateEntityId}`, { method: "PUT", body: {
      name, kind: "人物", summary: $("characterBaseSummary").value.trim(), detail: $("characterBaseDetail").value.trim(),
    }});
    cancelCharacterBaseProfile();
    await refreshCharacterCards();
    await loadCharacterState();
    refreshEntityImagePromptFromLatest(false);
    $("entityImagePromptStatus").textContent = "人物设定已更新，生图提示词已同步刷新";
    showToast("人物设定与生图信息已同步更新", "ok");
  } catch (e) { $("characterBaseMsg").textContent = e.message; }
}
async function refreshCharacterCards() {
  if (currentWorkId) {
    await loadWikiEntities();
    if (!$("wikiOverlay").classList.contains("hidden")) renderWikiList();
  }
}
async function analyzeCharacterState() {
  if (!characterStateChapterId) return;
  const button = $("characterStateAnalyzeBtn");
  busy(button, true, "提取中");
  try {
    const result = await api(`/api/chapters/${characterStateChapterId}/character-state-proposals/analyze`, { body: {} });
    await loadCharacterState();
    await refreshCharacterCards();
    showToast(result.proposals.length ? `已生成 ${result.proposals.length} 条待确认状态` : "未发现需要记录的人物变化", result.proposals.length ? "ok" : "");
  } catch (e) {
    $("characterStateMsg").textContent = e.message;
  } finally {
    busy(button, false, "提取本章变化");
  }
}
async function acceptCharacterStateProposal(pid) {
  try {
    await api(`/api/character-state-proposals/${pid}/accept`, { body: {} });
    characterStateProposalId = null;
    await loadCharacterState();
    await refreshCharacterCards();
    showToast("已采纳人物状态", "ok");
  } catch (e) { $("characterStateMsg").textContent = e.message; }
}
function editCharacterStateProposal(pid) {
  const proposal = (characterStateData?.proposals || []).find(item => item.id === pid);
  if (!proposal) return;
  setCharacterStateForm(proposal.state, proposal.change_summary, proposal.evidence, pid);
  $("characterStateSummary").focus();
}
async function rejectCharacterStateProposal(pid) {
  try {
    await api(`/api/character-state-proposals/${pid}/reject`, { body: {} });
    if (characterStateProposalId === pid) setCharacterStateForm(characterStateData?.current_state || {});
    await loadCharacterState();
    await refreshCharacterCards();
    showToast("已忽略该提议");
  } catch (e) { $("characterStateMsg").textContent = e.message; }
}
async function saveCharacterState() {
  if (!characterStateEntityId || !characterStateChapterId) return;
  const button = $("characterStateSaveBtn");
  const confirming = !!characterStateProposalId;
  busy(button, true, confirming ? "确认中" : "保存中");
  const body = {
    state: characterStateFormValue(),
    change_summary: $("characterStateSummary").value.trim(),
    evidence: $("characterStateEvidence").value.trim(),
  };
  try {
    if (characterStateProposalId) {
      await api(`/api/character-state-proposals/${characterStateProposalId}/accept`, { body });
    } else {
      await api(`/api/entities/${characterStateEntityId}/state-versions`, {
        body: { ...body, chapter_id: characterStateChapterId },
      });
    }
    characterStateProposalId = null;
    await loadCharacterState();
    await refreshCharacterCards();
    showToast("人物状态已保存", "ok");
  } catch (e) {
    $("characterStateMsg").textContent = e.message;
  } finally {
    busy(button, false, "保存为本章状态");
  }
}

/* ---------- 写作工作台：大屏抽屉，按需展开而不挤占编辑区 ---------- */

const plotStateFields = [
  ["mainline", "主线进度", "plotStateMainline"],
  ["current_event", "当前事件", "plotStateCurrentEvent"],
  ["timeline", "时间线", "plotStateTimeline"],
  ["locations", "地点", "plotStateLocations"],
  ["conflicts", "核心冲突", "plotStateConflicts"],
  ["open_threads", "未回收伏笔", "plotStateOpenThreads"],
  ["next_goal", "下一章目标", "plotStateNextGoal"],
  ["notes", "补充", "plotStateNotes"],
];
const plotStateSourceLabels = { autosave: "自动保存", manual: "手动记录", ai_confirmed: "AI 提议已采纳", ai_edited: "AI 提议编辑后采纳" };

function workspaceNeedsExclusivePane() {
  return window.innerWidth > 700 && window.innerWidth < 1450;
}
function openStoryDrawer(tab = "plot") {
  if (!currentWorkId) { showToast("请先创建或选择一个作品", "err"); return; }
  const app = $("app");
  if (workspaceNeedsExclusivePane() && app.classList.contains("ai-open")) {
    app.classList.remove("ai-open");
    localStorage.setItem("aiOpen", "0");
    if ("speechSynthesis" in window) speechSynthesis.cancel();
  }
  app.classList.add("story-open");
  selectStoryTab(tab);
}
function toggleStoryDrawer() {
  if ($("app").classList.contains("story-open")) closeStoryDrawer();
  else openStoryDrawer(storyTab);
}
function closeStoryDrawer() { $("app").classList.remove("story-open"); }
async function selectStoryTab(tab) {
  storyTab = ["plot", "workflow", "memory", "relations", "alerts", "context"].includes(tab) ? tab : "plot";
  document.querySelectorAll(".story-tab").forEach(button => {
    button.classList.toggle("active", button.dataset.storyTab === storyTab);
  });
  document.querySelectorAll(".story-panel").forEach(panel => {
    panel.classList.toggle("hidden", panel.id !== `storyPanel${storyTab[0].toUpperCase()}${storyTab.slice(1)}`);
  });
  if ($("app").classList.contains("story-open")) await refreshStoryDrawer();
}
async function refreshStoryDrawer() {
  if (!currentWorkId) return;
  try {
    if (storyTab === "plot") await loadPlotState();
    else if (storyTab === "workflow") await loadWorkflow();
    else if (storyTab === "memory") await loadStoryMemories();
    else if (storyTab === "relations") await loadRelationships();
    else if (storyTab === "alerts") await loadConsistencyAlerts();
    else if (storyTab === "context") await loadAgentContext();
  } catch (e) { showToast(e.message, "err"); }
}

function storyChapterLabel(chapter) {
  return chapter ? `第${chapter.ord}章《${chapter.title || "无标题"}》` : "未选择章节";
}
function plotStateOptions() {
  return chapters.map(chapter =>
    `<option value="${chapter.id}" ${chapter.id === plotStateChapterId ? "selected" : ""}>${esc(storyChapterLabel(chapter))}</option>`
  ).join("");
}
function plotStateSnapshot(state) {
  const facts = plotStateFields.filter(([key]) => state?.[key]).map(([key, label]) =>
    `<div><b>${esc(label)}</b><span>${esc(state[key])}</span></div>`
  );
  return facts.length ? `<div class="state-snapshot">${facts.join("")}</div>` : "";
}
function setPlotStateForm(state, summary = "", evidence = "", proposalId = null) {
  for (const [key, , id] of plotStateFields) $(id).value = state?.[key] || "";
  $("plotStateSummary").value = summary || "";
  $("plotStateEvidence").value = evidence || "";
  plotStateProposalId = proposalId;
  $("plotStateSaveBtn").textContent = proposalId ? "确认并保存" : "保存为本章剧情状态";
}
function plotStateFormValue() {
  const state = {};
  for (const [key, , id] of plotStateFields) state[key] = $(id).value.trim();
  return state;
}
function plotStateDraftKey() {
  if (!currentWorkId || !plotStateChapterId) return "";
  return `writehtml:plot-state-draft:${currentUsername || "anonymous"}:${currentWorkId}:${plotStateChapterId}`;
}
function plotStateDraftValue() {
  return {
    state: plotStateFormValue(),
    change_summary: $("plotStateSummary").value.trim(),
    evidence: $("plotStateEvidence").value.trim(),
    proposal_id: plotStateProposalId,
    saved_at: Date.now(),
  };
}
function plotStateHasContent(draft) {
  return Object.values(draft?.state || {}).some(Boolean);
}
function plotStateDraftSignature(draft) {
  return JSON.stringify({ state: draft?.state || {}, change_summary: draft?.change_summary || "", evidence: draft?.evidence || "" });
}
function readPlotStateDraft(key = plotStateDraftKey()) {
  if (!key) return null;
  try {
    const value = JSON.parse(localStorage.getItem(key) || "null");
    return value && typeof value === "object" ? value : null;
  } catch (e) { return null; }
}
function writePlotStateDraft(draft = plotStateDraftValue()) {
  const key = plotStateDraftKey();
  if (!key) return;
  try { localStorage.setItem(key, JSON.stringify(draft)); } catch (e) {}
}
function clearPlotStateDraft(key = plotStateDraftKey()) {
  if (!key) return;
  try { localStorage.removeItem(key); } catch (e) {}
}
function setPlotStateSaveMessage(message, tone = "") {
  const el = $("plotStateMsg");
  el.textContent = message || "";
  el.classList.toggle("autosave-ok", tone === "ok");
  el.classList.toggle("autosave-warn", tone === "warn");
}
function queuePlotStateAutosave() {
  const draft = plotStateDraftValue();
  writePlotStateDraft(draft); // 先写本机，断电/网络短断也不会立即丢。
  clearTimeout(plotStateSaveTimer);
  if (plotStateProposalId) {
    setPlotStateSaveMessage("已本机暂存。AI 提议仍需手动确认，不会自动采纳。", "warn");
    return;
  }
  if (!plotStateHasContent(draft)) {
    setPlotStateSaveMessage("已本机暂存；至少填写一项剧情状态后会自动同步。", "warn");
    return;
  }
  setPlotStateSaveMessage("已本机暂存，正在自动同步…", "warn");
  plotStateSaveTimer = setTimeout(() => {
    plotStateSaveTimer = null;
    savePlotState(true);
  }, 1200);
}
function restorePlotStateDraft() {
  const key = plotStateDraftKey();
  const draft = readPlotStateDraft(key);
  if (!draft || !draft.state || typeof draft.state !== "object") return;
  if (draft.proposal_id && draft.proposal_id !== plotStateProposalId) {
    clearPlotStateDraft(key);
    return;
  }
  for (const [field, , id] of plotStateFields) $(id).value = draft.state[field] || "";
  $("plotStateSummary").value = draft.change_summary || "";
  $("plotStateEvidence").value = draft.evidence || "";
  if (plotStateProposalId) {
    setPlotStateSaveMessage("已恢复本机草稿。AI 提议仍需点击“确认并保存”。", "warn");
  } else if (plotStateHasContent(draft)) {
    setPlotStateSaveMessage("已恢复本机草稿，正在自动同步…", "warn");
    clearTimeout(plotStateSaveTimer);
    plotStateSaveTimer = setTimeout(() => { plotStateSaveTimer = null; savePlotState(true); }, 1200);
  }
}
function renderPlotState(data) {
  plotStateData = data;
  if (!plotStateChapterId && data.target_chapter) plotStateChapterId = data.target_chapter.id;
  $("plotStateChapter").innerHTML = plotStateOptions();
  $("plotStateMeta").textContent = storyChapterLabel(data.target_chapter);
  const version = data.state_version;
  $("plotStateSource").textContent = version
    ? `${plotStateSourceLabels[version.source] || "已记录"} · 生效于第${version.chapter_ord}章`
    : "尚无已确认状态";
  setPlotStateForm(data.current_state || {});
  restorePlotStateDraft();
  const proposals = (data.proposals || []).filter(item => item.status === "pending");
  $("plotStateProposals").innerHTML = proposals.map(item => `
    <div class="story-proposal">
      <div><b>AI 待确认</b><span>${esc(item.change_summary || "剧情推进")}</span></div>
      ${item.evidence ? `<small>${esc(item.evidence)}</small>` : ""}
      ${plotStateSnapshot(item.state)}
      <div class="story-proposal-actions">
        <button onclick="acceptPlotStateProposal(${item.id})">采纳</button>
        <button class="ic" onclick="editPlotStateProposal(${item.id})" title="编辑后采纳">${svg("pen")}</button>
        <button class="ic" onclick="rejectPlotStateProposal(${item.id})" title="忽略">${svg("x")}</button>
      </div>
    </div>`).join("");
  $("plotStateHistory").innerHTML = (data.history || []).length ? data.history.map(item => `
    <div class="story-timeline-item">
      <div><b>第${item.chapter_ord}章《${esc(item.chapter_title || "无标题")}》</b><span>${esc(plotStateSourceLabels[item.source] || "已记录")}</span></div>
      ${item.change_summary ? `<p>${esc(item.change_summary)}</p>` : ""}
      ${item.evidence ? `<small>${esc(item.evidence)}</small>` : ""}
      ${plotStateSnapshot(item.state)}
    </div>`).join("") : '<div class="empty">尚无剧情推进记录</div>';
}
async function loadPlotState() {
  if (!currentWorkId) return;
  if (!chapters.length) {
    $("plotStateMeta").textContent = "请先创建章节";
    $("plotStateProposals").innerHTML = "";
    $("plotStateHistory").innerHTML = '<div class="empty">暂无章节</div>';
    return;
  }
  if (!chapters.some(item => item.id === plotStateChapterId)) plotStateChapterId = currentChapterId || chapters[chapters.length - 1].id;
  const data = await api(`/api/works/${currentWorkId}/plot-state?chapter_id=${plotStateChapterId}`, { method: "GET" });
  renderPlotState(data);
}
async function changePlotStateChapter() {
  clearTimeout(plotStateSaveTimer);
  plotStateChapterId = +$("plotStateChapter").value || null;
  plotStateProposalId = null;
  await loadPlotState();
}
async function analyzePlotState() {
  if (!plotStateChapterId) return;
  const button = $("plotStateAnalyzeBtn");
  busy(button, true, "提取中");
  try {
    const result = await api(`/api/chapters/${plotStateChapterId}/plot-state-proposals/analyze`, { body: {} });
    await loadPlotState();
    showToast(result.proposal ? "已生成剧情状态待确认" : "未发现需要记录的剧情推进", result.proposal ? "ok" : "");
  } catch (e) { $("plotStateMsg").textContent = e.message; }
  finally { busy(button, false); applyIcons(); }
}
async function acceptPlotStateProposal(pid) {
  try {
    await api(`/api/plot-state-proposals/${pid}/accept`, { body: {} });
    clearPlotStateDraft();
    plotStateProposalId = null;
    await loadPlotState();
    showToast("已采纳剧情状态", "ok");
  } catch (e) { $("plotStateMsg").textContent = e.message; }
}
function editPlotStateProposal(pid) {
  const proposal = (plotStateData?.proposals || []).find(item => item.id === pid);
  if (!proposal) return;
  setPlotStateForm(proposal.state, proposal.change_summary, proposal.evidence, pid);
  $("plotStateSummary").focus();
}
async function rejectPlotStateProposal(pid) {
  try {
    await api(`/api/plot-state-proposals/${pid}/reject`, { body: {} });
    clearPlotStateDraft();
    if (plotStateProposalId === pid) setPlotStateForm(plotStateData?.current_state || {});
    await loadPlotState();
    showToast("已忽略该剧情提议");
  } catch (e) { $("plotStateMsg").textContent = e.message; }
}
async function savePlotState(automatic = false) {
  if (!currentWorkId || !plotStateChapterId) return;
  const button = $("plotStateSaveBtn");
  const confirming = !!plotStateProposalId;
  if (automatic && confirming) return;
  if (!automatic) {
    clearTimeout(plotStateSaveTimer);
    busy(button, true, confirming ? "确认中" : "保存中");
  }
  const workId = currentWorkId;
  const chapterId = plotStateChapterId;
  const body = { state: plotStateFormValue(), change_summary: $("plotStateSummary").value.trim(),
    evidence: $("plotStateEvidence").value.trim() };
  const draftKey = plotStateDraftKey();
  const signature = plotStateDraftSignature(body);
  const proposalId = plotStateProposalId;
  if (automatic && !plotStateHasContent(body)) return;
  return plotStateSaveQueue.run(async () => {
    try {
      let response;
      if (proposalId) response = await api(`/api/plot-state-proposals/${proposalId}/accept`, { body });
      else response = await api(`/api/works/${workId}/plot-state-versions`, { body: { ...body, chapter_id: chapterId, autosave: automatic } });
      const storedDraft = readPlotStateDraft(draftKey);
      if (storedDraft && plotStateDraftSignature(storedDraft) === signature) clearPlotStateDraft(draftKey);
      if (plotStateProposalId === proposalId) plotStateProposalId = null;
      if (automatic) {
        if (currentWorkId === workId && plotStateChapterId === chapterId) {
          if (plotStateData) {
            plotStateData.current_state = body.state;
            plotStateData.state_version = response.version || plotStateData.state_version;
          }
          const version = response.version;
          if (version) $("plotStateSource").textContent = `${plotStateSourceLabels[version.source] || "已记录"} · 生效于第${version.chapter_ord}章`;
          setPlotStateSaveMessage("已自动保存到服务器。", "ok");
        }
        return;
      }
      await loadPlotState();
      showToast("剧情状态已保存", "ok");
    } catch (e) {
      setPlotStateSaveMessage(automatic ? "服务器暂不可用，草稿已保存在本机。" : e.message, automatic ? "warn" : "");
    } finally {
      if (!automatic) busy(button, false, "保存为本章剧情状态");
    }
  });
}

function syncChapterWorkflow(workflow) {
  chapterWorkflow = workflow;
  const chapter = chapters.find(item => item.id === workflow?.id);
  if (chapter && workflow) {
    chapter.workflow_status = workflow.workflow_status;
    chapter.workflow_goal = workflow.workflow_goal;
    chapter.workflow_summary = workflow.workflow_summary;
    chapter.workflow_checked_at = workflow.workflow_checked_at;
    renderTree();
  }
}
function renderWorkflow(workflow) {
  chapterWorkflow = workflow;
  $("workflowMeta").textContent = workflow ? `${storyChapterLabel(workflow)} · ${workflowLabel(workflow.workflow_status)}` : "请先选择章节";
  $("workflowGoal").value = workflow?.workflow_goal || "";
  $("workflowSummary").value = workflow?.workflow_summary || "";
  document.querySelectorAll("[data-workflow]").forEach(button => {
    button.classList.toggle("active", button.dataset.workflow === (workflow?.workflow_status || "drafting"));
    button.disabled = !workflow;
  });
}
async function loadWorkflow() {
  if (!currentChapterId) { renderWorkflow(null); return; }
  const workflow = await api(`/api/chapters/${currentChapterId}/workflow`, { method: "GET" });
  renderWorkflow(workflow);
}
async function saveWorkflow() {
  if (!currentChapterId) return;
  const button = $("workflowSaveBtn");
  busy(button, true, "保存中");
  try {
    const workflow = await api(`/api/chapters/${currentChapterId}/workflow`, { method: "PUT", body: {
      goal: $("workflowGoal").value, summary: $("workflowSummary").value,
    }});
    syncChapterWorkflow(workflow); renderWorkflow(workflow);
    showToast("本章计划已保存", "ok");
  } catch (e) { $("workflowMsg").textContent = e.message; }
  finally { busy(button, false, "保存本章计划"); }
}
async function setWorkflowStatus(status) {
  if (!currentChapterId) return;
  try {
    const workflow = await api(`/api/chapters/${currentChapterId}/workflow`, { method: "PUT", body: { status } });
    syncChapterWorkflow(workflow); renderWorkflow(workflow);
  } catch (e) { $("workflowMsg").textContent = e.message; }
}
async function runChapterReview() {
  if (!currentChapterId) return;
  if (!await flushEditorSave()) return;
  const button = $("workflowReviewBtn");
  busy(button, true, "复核中");
  try {
    const result = await api(`/api/chapters/${currentChapterId}/review`, { body: {} });
    syncChapterWorkflow(result.workflow); renderWorkflow(result.workflow);
    consistencyAlerts = result.alerts || [];
    await notifyStoryUpdates(result);
    showToast(consistencyAlerts.length ? `复核完成：${consistencyAlerts.length} 条提醒` : "复核完成，未发现明确冲突", consistencyAlerts.length ? "" : "ok");
  } catch (e) { $("workflowMsg").textContent = e.message; }
  finally { busy(button, false, "AI 复核"); }
}

const storyMemoryTypeLabels = {
  event: "重要事件", fact: "明确事实", knowledge: "知情变化", relationship_change: "关系变化",
  item_change: "物品变化", location_change: "地点变化", ability_change: "能力变化",
  world_rule: "世界规则", promise: "承诺/任务", secret: "重要秘密",
};

function storyMemoryChapterOptions() {
  return chapters.map(chapter =>
    `<option value="${chapter.id}" ${chapter.id === storyMemoryChapterId ? "selected" : ""}>${esc(storyChapterLabel(chapter))}</option>`
  ).join("");
}
function storyMemoryTypeOptions(selected) {
  return Object.entries(storyMemoryTypeLabels).map(([value, label]) =>
    `<option value="${value}" ${value === selected ? "selected" : ""}>${esc(label)}</option>`
  ).join("");
}
function storyMemoryPoint(item) {
  return `第${item.chapter_ord || "?"}章《${item.chapter_title || "无标题"}》`;
}
function storyMemoryState(item) {
  if (item.is_stale || item.status === "stale") return "来源已过期";
  if (item.status === "proposed") return "待确认";
  if (item.status === "rejected") return "已拒绝";
  return "已确认";
}
function storyMemoryEditor(item, proposal) {
  const id = item.id;
  return `<div class="memory-inline-editor">
    <div class="memory-edit-row"><label>类型<select id="storyMemoryType-${id}">${storyMemoryTypeOptions(item.memory_type)}</select></label><label>重要度<select id="storyMemoryImportance-${id}">${[1,2,3,4,5].map(v => `<option value="${v}" ${v === item.importance ? "selected" : ""}>${v}</option>`).join("")}</select></label></div>
    <label>标题<input id="storyMemoryTitle-${id}" value="${esc(item.title)}"></label>
    <label>记忆内容<textarea id="storyMemoryContent-${id}">${esc(item.content)}</textarea></label>
    <label>来源证据<textarea id="storyMemoryEvidence-${id}">${esc(item.evidence || "")}</textarea></label>
    <div class="memory-edit-actions">
      <button onclick="${proposal ? `acceptStoryMemory(${id},true)` : `saveStoryMemory(${id})`}">${proposal ? "编辑后采纳" : "保存修改"}</button>
      <button class="ic" data-ic="x" onclick="cancelStoryMemoryEdit()" title="取消编辑"></button>
    </div>
  </div>`;
}
function readStoryMemoryEditor(item) {
  const id = item.id;
  return {
    memory_type: $(`storyMemoryType-${id}`).value,
    title: $(`storyMemoryTitle-${id}`).value.trim(),
    content: $(`storyMemoryContent-${id}`).value.trim(),
    evidence: $(`storyMemoryEvidence-${id}`).value.trim(),
    importance: +$(`storyMemoryImportance-${id}`).value || 3,
    entity_ids: item.entity_ids || [],
  };
}
function renderStoryMemoryItem(item, kind) {
  const editable = editingStoryMemoryId === item.id;
  const proposal = kind === "proposal";
  const actions = proposal ? `
      <div class="story-proposal-actions">
        <button onclick="acceptStoryMemory(${item.id})">采纳</button>
        <button class="ic" data-ic="pen" onclick="editStoryMemory(${item.id})" title="编辑后采纳"></button>
        <button class="ic" data-ic="x" onclick="rejectStoryMemory(${item.id})" title="拒绝"></button>
      </div>` : (!item.is_stale && item.status === "confirmed" ? `
      <div class="story-proposal-actions"><button class="ic" data-ic="pen" onclick="editStoryMemory(${item.id})" title="编辑这条记忆"></button></div>` : "");
  return `<article class="story-memory-item ${item.is_stale ? "stale" : ""}">
    <details ${proposal || editable ? "open" : ""}>
      <summary><b>${esc(storyMemoryTypeLabels[item.memory_type] || "故事记忆")}</b><span>${esc(item.title || "未命名")}</span><small class="memory-status">${esc(storyMemoryState(item))}</small></summary>
      <p class="memory-content">${esc(item.content || "")}</p>
      <small class="memory-evidence">${esc(storyMemoryPoint(item))}${item.entity_names?.length ? ` · ${esc(item.entity_names.join("、"))}` : ""}${item.evidence ? `\n证据：${esc(item.evidence)}` : ""}</small>
      ${editable ? storyMemoryEditor(item, proposal) : actions}
    </details>
  </article>`;
}
function renderStoryMemories(data) {
  storyMemoryData = data || {};
  const target = data?.target_chapter || null;
  if (!storyMemoryChapterId && target) storyMemoryChapterId = target.id;
  $("storyMemoryChapter").innerHTML = storyMemoryChapterOptions();
  const counts = data?.counts || {};
  const status = target?.analysis_status === "needs_review" ? "需要重新分析" : "当前来源已复核";
  $("storyMemoryMeta").textContent = target ? `${storyChapterLabel(target)} · ${status}` : "请先选择章节";
  const warnings = [];
  if (target?.analysis_status === "needs_review") warnings.push(`本章正文已变化：${target.analysis_reason || "派生资料需要重新分析"}。`);
  if (counts.stale) warnings.push(`已有 ${counts.stale} 条故事记忆的来源已过期，系统不会把它们用于 AI 召回。`);
  $("storyMemoryWarning").textContent = warnings.join("\n");
  $("storyMemoryWarning").classList.toggle("hidden", !warnings.length);
  const proposals = data?.proposals || [];
  const acceptAllButton = $("storyMemoryAcceptAllBtn");
  acceptAllButton.classList.toggle("hidden", !proposals.length);
  acceptAllButton.disabled = !proposals.length;
  $("storyMemoryProposalCount").textContent = proposals.length ? `${proposals.length} 条` : "";
  $("storyMemoryProposals").innerHTML = proposals.length ? proposals.map(item => renderStoryMemoryItem(item, "proposal")).join("") : '<div class="empty">本章还没有待确认故事记忆</div>';
  const confirmed = storyMemorySearchResults || data?.confirmed || [];
  $("storyMemoryConfirmedTitle").textContent = storyMemorySearchResults ? "检索结果" : "已确认记忆";
  $("storyMemoryConfirmedCount").textContent = confirmed.length ? `${confirmed.length} 条` : "";
  $("storyMemoryConfirmed").innerHTML = confirmed.length ? confirmed.map(item => renderStoryMemoryItem(item, "confirmed")).join("") : '<div class="empty">没有匹配的已确认故事记忆</div>';
  const stale = data?.stale || [];
  $("storyMemoryStaleCount").textContent = stale.length ? `${stale.length} 条` : "";
  $("storyMemoryStale").innerHTML = stale.length ? stale.map(item => renderStoryMemoryItem(item, "stale")).join("") : '<div class="empty">暂无来源过期的故事记忆</div>';
  applyIcons();
}
async function loadStoryMemories() {
  if (!currentWorkId) return;
  if (!chapters.length) {
    renderStoryMemories({ target_chapter: null, proposals: [], confirmed: [], stale: [], counts: {} });
    return;
  }
  if (!chapters.some(item => item.id === storyMemoryChapterId)) storyMemoryChapterId = currentChapterId || chapters[chapters.length - 1].id;
  const data = await api(`/api/works/${currentWorkId}/story-memory-overview?chapter_id=${storyMemoryChapterId}`, { method: "GET" });
  renderStoryMemories(data);
}
async function changeStoryMemoryChapter() {
  storyMemoryChapterId = +$("storyMemoryChapter").value || null;
  storyMemorySearchResults = null;
  editingStoryMemoryId = null;
  $("storyMemorySearch").value = "";
  await loadStoryMemories();
}
function editStoryMemory(memoryId) {
  editingStoryMemoryId = memoryId;
  renderStoryMemories(storyMemoryData);
}
function cancelStoryMemoryEdit() {
  editingStoryMemoryId = null;
  renderStoryMemories(storyMemoryData);
}
function findStoryMemory(memoryId) {
  const groups = [storyMemoryData?.proposals, storyMemorySearchResults, storyMemoryData?.confirmed, storyMemoryData?.stale];
  return groups.flatMap(items => items || []).find(item => item.id === memoryId) || null;
}
async function acceptStoryMemory(memoryId, edited = false) {
  const item = findStoryMemory(memoryId);
  if (!item) return;
  try {
    const changes = edited ? readStoryMemoryEditor(item) : null;
    await api(`/api/story-memories/${memoryId}/accept`, { body: changes ? { changes } : {} });
    editingStoryMemoryId = null;
    storyMemorySearchResults = null;
    await loadStoryMemories();
    showToast("已确认故事记忆", "ok");
  } catch (e) { showToast(e.message, "err"); }
}
async function acceptAllStoryMemories() {
  const proposals = (storyMemoryData?.proposals || []).filter(item => item.status === "proposed" && !item.is_stale);
  if (!proposals.length) return;
  const button = $("storyMemoryAcceptAllBtn");
  busy(button, true, "");
  try {
    const failures = [];
    for (const item of proposals) {
      try { await api(`/api/story-memories/${item.id}/accept`, { body: {} }); }
      catch (e) { failures.push(item.title || `#${item.id}`); }
    }
    editingStoryMemoryId = null;
    storyMemorySearchResults = null;
    await loadStoryMemories();
    showToast(failures.length ? `已采纳 ${proposals.length - failures.length} 条；${failures.length} 条需重新处理` : `已采纳 ${proposals.length} 条故事记忆`, failures.length ? "" : "ok");
  } finally {
    busy(button, false, "");
    applyIcons();
  }
}
async function rejectStoryMemory(memoryId) {
  try {
    await api(`/api/story-memories/${memoryId}/reject`, { body: {} });
    editingStoryMemoryId = null;
    await loadStoryMemories();
    showToast("已拒绝故事记忆提议");
  } catch (e) { showToast(e.message, "err"); }
}
async function saveStoryMemory(memoryId) {
  const item = findStoryMemory(memoryId);
  if (!item) return;
  try {
    await api(`/api/story-memories/${memoryId}`, { method: "PUT", body: readStoryMemoryEditor(item) });
    editingStoryMemoryId = null;
    storyMemorySearchResults = null;
    await loadStoryMemories();
    showToast("故事记忆已更新", "ok");
  } catch (e) { showToast(e.message, "err"); }
}
async function analyzeStoryMemories() {
  if (!storyMemoryChapterId) return;
  if (storyMemoryChapterId === currentChapterId && !await flushEditorSave()) return;
  const button = $("storyMemoryAnalyzeBtn");
  busy(button, true, "分析中");
  try {
    const result = await api(`/api/chapters/${storyMemoryChapterId}/story-memories/analyze`, { body: {} });
    storyMemorySearchResults = null;
    await loadStoryMemories();
    showToast(result.proposals?.length ? `已生成 ${result.proposals.length} 条待确认故事记忆` : "未发现需要长期记录的事实", result.proposals?.length ? "ok" : "");
  } catch (e) { showToast(e.message, "err"); }
  finally { busy(button, false); applyIcons(); }
}
async function markStoryMemoryStale() {
  if (!storyMemoryChapterId) return;
  if (!await askCard({ title: "标记本章重大修改？", msg: "本章的故事记忆、AI 状态提议和连续性提醒会标记为需要重新分析；已确认的原始记录不会被删除。", okText: "标记并重新分析", danger: true })) return;
  try {
    const result = await api(`/api/chapters/${storyMemoryChapterId}/story-memories/mark-stale`, { body: {} });
    storyMemorySearchResults = null;
    await loadStoryMemories();
    showToast(`已标记本章资料失效；后续 ${result.later_chapters || 0} 章可能受影响`, "ok");
  } catch (e) { showToast(e.message, "err"); }
}
async function searchStoryMemories() {
  if (!currentWorkId || !storyMemoryData) return;
  const query = $("storyMemorySearch").value.trim();
  if (!query) {
    storyMemorySearchResults = null;
    renderStoryMemories(storyMemoryData);
    return;
  }
  try {
    storyMemorySearchResults = await api(`/api/works/${currentWorkId}/story-memories/search?q=${encodeURIComponent(query)}&before_chapter_id=${storyMemoryChapterId || ""}`, { method: "GET" });
    editingStoryMemoryId = null;
    renderStoryMemories(storyMemoryData);
  } catch (e) { showToast(e.message, "err"); }
}

function relationEntityOptions(selectedId) {
  const characters = entitiesCache.filter(item => item.kind === "人物");
  return `<option value="">选择人物</option>` + characters.map(item =>
    `<option value="${item.id}" ${item.id === selectedId ? "selected" : ""}>${esc(item.name)}</option>`
  ).join("");
}
function renderRelationForm() {
  const current = entityRelations.find(item => item.id === editingRelationId);
  $("relationFrom").innerHTML = relationEntityOptions(current?.from_entity_id);
  $("relationTo").innerHTML = relationEntityOptions(current?.to_entity_id);
  $("relationName").value = current?.relation || "";
  $("relationStatus").value = current?.status || "active";
  $("relationDetail").value = current?.detail || "";
  $("relationFormTitle").textContent = current ? "编辑关系" : "新增关系";
  $("relationSaveBtn").textContent = current ? "保存修改" : "保存关系";
  $("relationCancelBtn").classList.toggle("hidden", !current);
}
function renderRelations() {
  const nodes = new Map();
  entityRelations.forEach(item => {
    nodes.set(item.from_entity_id, item.from_name);
    nodes.set(item.to_entity_id, item.to_name);
  });
  $("relationMap").innerHTML = entityRelations.length ? `
    <div class="relation-map-nodes">${[...nodes].map(([id, name]) => `<span class="relation-node" title="人物关系节点">${esc(name)}</span>`).join("")}</div>
    <div class="relation-map-links">${entityRelations.map(item => `<div><span>${esc(item.from_name)}</span><b>${esc(item.relation)}</b><span>${esc(item.to_name)}</span></div>`).join("")}</div>`
    : '<div class="empty">尚未记录人物关系</div>';
  $("relationList").innerHTML = entityRelations.length ? entityRelations.map(item => `
    <div class="relation-row">
      <div><b>${esc(item.from_name)}</b><span>${esc(item.relation)}</span><b>${esc(item.to_name)}</b>${item.status && item.status !== "active" ? `<small>${esc(item.status)}</small>` : ""}</div>
      ${item.detail ? `<p>${esc(item.detail)}</p>` : ""}
      <span class="relation-actions"><button class="ic" onclick="editRelation(${item.id})" title="编辑关系">${svg("pen")}</button><button class="ic" onclick="deleteRelation(${item.id})" title="删除关系">${svg("x")}</button></span>
    </div>`).join("") : "";
  renderRelationForm();
}
async function loadRelationships() {
  if (!currentWorkId) return;
  await loadWikiEntities();
  entityRelations = await api(`/api/works/${currentWorkId}/relationships`, { method: "GET" });
  renderRelations();
}
function resetRelationForm() {
  editingRelationId = null;
  renderRelationForm();
  $("relationMsg").textContent = "";
}
function editRelation(rid) {
  editingRelationId = rid;
  renderRelationForm();
  $("relationName").focus();
}
async function saveRelation() {
  if (!currentWorkId) return;
  const from_entity_id = +$("relationFrom").value;
  const to_entity_id = +$("relationTo").value;
  const body = { from_entity_id, to_entity_id, relation: $("relationName").value,
    status: $("relationStatus").value, detail: $("relationDetail").value };
  try {
    if (editingRelationId) await api(`/api/relationships/${editingRelationId}`, { method: "PUT", body });
    else await api(`/api/works/${currentWorkId}/relationships`, { body });
    resetRelationForm();
    await loadRelationships();
    showToast("人物关系已保存", "ok");
  } catch (e) { $("relationMsg").textContent = e.message; }
}
async function deleteRelation(rid) {
  if (!await askCard({ title: "删除这条人物关系？", okText: "删除", danger: true })) return;
  try {
    await api(`/api/relationships/${rid}`, { method: "DELETE" });
    if (editingRelationId === rid) resetRelationForm();
    await loadRelationships();
  } catch (e) { $("relationMsg").textContent = e.message; }
}

function renderConsistencyAlerts() {
  $("alertsMeta").textContent = currentChapterId ? storyChapterLabel(chapters.find(item => item.id === currentChapterId)) : "请先选择章节";
  $("alertsList").innerHTML = consistencyAlerts.length ? consistencyAlerts.map(item => `
    <div class="consistency-alert severity-${esc(item.severity || "notice")} ${item.is_stale ? "stale" : ""}">
      <div><span class="alert-mark">${svg(item.severity === "critical" ? "alert" : item.severity === "warning" ? "alert" : "eye")}</span><b>${esc(item.title)}</b><small>${esc(item.category || "连续性")}</small>${item.is_stale ? '<small>来源已过期</small>' : ""}</div>
      ${item.detail ? `<p>${esc(item.detail)}</p>` : ""}
      ${item.evidence ? `<small class="alert-evidence">${esc(item.evidence)}</small>` : ""}
      ${item.suggestion ? `<p class="alert-suggestion">${esc(item.suggestion)}</p>` : ""}
      ${item.status === "open" && !item.is_stale ? `<button class="ic" onclick="dismissConsistencyAlert(${item.id})" title="忽略此提醒">${svg("x")}</button>` : `<span class="alert-dismissed">${item.is_stale ? "已过期" : "已忽略"}</span>`}
    </div>`).join("") : '<div class="empty">本章还没有连续性提醒</div>';
}
async function loadConsistencyAlerts() {
  if (!currentChapterId) { consistencyAlerts = []; renderConsistencyAlerts(); return; }
  consistencyAlerts = await api(`/api/chapters/${currentChapterId}/consistency-alerts`, { method: "GET" });
  renderConsistencyAlerts();
}
async function dismissConsistencyAlert(alertId) {
  try {
    await api(`/api/consistency-alerts/${alertId}/dismiss`, { body: {} });
    await loadConsistencyAlerts();
  } catch (e) { showToast(e.message, "err"); }
}

function contextTextBlock(title, text, empty = "无") {
  return `<div><b>${esc(title)}</b><pre>${esc(text || empty)}</pre></div>`;
}
function renderAgentContext(data) {
  agentContext = data;
  const chapter = data.chapter ? `第${data.chapter.ord}章《${data.chapter.title || "无标题"}》` : "未选择章节";
  const budget = data.context_budget || {};
  const formatTokens = value => value >= 1000 ? `${Math.round(value / 1000)}K` : String(value || 0);
  const estimate = data.estimated_tokens
    ? ` · 约 ${formatTokens(data.estimated_tokens)} / ${formatTokens(budget.window_tokens)} tokens`
    : "";
  $("contextMeta").textContent = `${data.engine} · ${chapter} · ${data.model || "未设置模型"}${estimate}`;
  $("contextSelection").innerHTML = contextTextBlock("选区", data.selection?.present ? data.selection.text : "本回合没有选区");
  const recalled = data.context_items || [];
  const excluded = data.context_exclusions || [];
  const conversation = data.conversation || {};
  $("contextRecall").innerHTML = `<div><b>会话上下文</b>
    <p>${conversation.use_history ? `${esc(conversation.title || "当前会话")} · ${conversation.message_count || 0} 条消息` : "本轮不读取旧聊天"}</p>
    ${conversation.has_summary ? `<details><summary>已压缩的早期对话摘要</summary><pre>${esc(conversation.summary || "")}</pre></details>` : ""}
    </div><div><b>本轮参考资料</b>${recalled.length ? recalled.map((item, index) =>
    `<details ${index < 3 ? "open" : ""}><summary>${esc(item.title || item.type || "上下文")} · ${esc(item.reason || "系统上下文")}${item.source_version ? ` · v${item.source_version}` : ""}</summary>${item.time_scope ? `<p>${esc(item.time_scope)}</p>` : ""}<pre>${esc(item.content || "")}</pre></details>`
    ).join("") : '<p>暂无可用的已确认故事资料</p>'}
    ${excluded.length ? `<details><summary>本轮未发送的剧情规划 · ${excluded.length} 项</summary><div class="context-exclusion-list">${excluded.map(item => `<p><b>${esc(item.title)}</b><span>${esc(item.reason)}</span></p>`).join("")}</div></details>` : ""}</div>`;
  const skills = data.skills || [];
  $("contextSkills").innerHTML = `<div><b>本回合 Skills</b>${skills.length ? skills.map(item =>
    `<details><summary>${esc(item.name)}${item.description ? ` · ${esc(item.description)}` : ""}</summary><pre>${esc(item.instruction)}</pre></details>`
  ).join("") : '<p>未手动选中 Skill</p>'}</div>`;
  const runtime = data.runtime || {};
  $("contextRuntime").innerHTML = contextTextBlock("运行时", [
    `Pi：${runtime.pi_enabled ? "已启用" : "未启用"}`,
    `本机工具：${(runtime.native_capabilities || []).join("、") || "无"}`,
    `工作目录：${runtime.cwd || "无"}`,
    `本机 Skill 目录：${(runtime.skill_dirs || []).join("、") || "无"}`,
    `launcher 生命周期：${runtime.launcher_lifecycle ? "已启用" : "未启用"}`,
    `联网搜索：${runtime.web_search_enabled ? `${runtime.web_search_provider || "Tavily"} · ${runtime.web_search_key_count || 0} 个 Key · ${runtime.web_search_key_source === "user" ? "用户配置" : "服务器配置"}` : "未配置"}`,
  ].join("\n"));
  $("contextTools").innerHTML = `<div><b>应用工具</b><div class="context-tool-list">${(data.tools || []).map(item =>
    `<details><summary>${esc(item.name)}</summary><p>${esc(item.description)}</p></details>`
  ).join("")}</div></div>`;
  $("contextSystem").innerHTML = (data.system_messages || []).map((item, index) =>
    `<details ${index === 0 ? "open" : ""}><summary>${esc(item.label || "系统上下文")} ${index + 1}</summary><pre>${esc(item.content)}</pre></details>`
  ).join("") || '<div class="empty">本回合没有系统上下文</div>';
}
async function loadAgentContext() {
  const button = $("contextRefreshBtn");
  busy(button, true);
  try {
    const data = await api("/api/agent/context", { body: {
      chapter_id: currentChapterId, work_id: currentChapterId == null ? currentWorkId : null,
      session_id: currentAgentSessionId, use_history: agentConversationMode === "standard",
      selection: agentSelection,
      skill_ids: activeAgentSkillIds.size ? [...activeAgentSkillIds] : [], text: $("agentInput")?.value || "",
      documents: pendingAgentDocuments.map(({ name, text }) => ({ name, text })),
    }});
    renderAgentContext(data);
  } catch (e) { $("contextMeta").textContent = e.message; }
  finally { busy(button, false); applyIcons(); }
}

// @提及：阅读视图里把 @名 包成可悬浮 span
function wrapMentions(html) {
  const names = entitiesCache.map(e => e.name).filter(Boolean)
    .sort((a, b) => b.length - a.length);   // 长名优先，避免短名子串误匹配
  if (!names.length) return html;
  const re = new RegExp("@" + names.map(n => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|@"), "g");
  return html.replace(re, m => `<span class="mention" data-name="${m.slice(1)}">${m}</span>`);
}
function showMentionPop(m) {
  const ent = entitiesCache.find(e => e.name === m.dataset.name);
  if (!ent) return;
  const pop = $("mentionPop");
  pop.innerHTML = `<div class="mp-head"><b>${esc(ent.kind)}</b> · ${esc(ent.name)}</div>`
    + (ent.summary ? `<div class="mp-sum">${esc(ent.summary)}</div>` : "")
    + (ent.current_state && characterStateBrief(ent.current_state) ? `<div class="mp-state">${esc(characterStateBrief(ent.current_state))}</div>` : "")
    + (ent.detail ? `<div class="mp-det">${esc(ent.detail)}</div>` : "");
  pop.classList.remove("hidden");
  const r = m.getBoundingClientRect();
  pop.style.left = Math.max(8, Math.min(r.left, innerWidth - 300)) + "px";
  pop.style.top = (r.bottom + 6) + "px";
}
function hideMentionPop() { $("mentionPop").classList.add("hidden"); }
async function ensureEntities() {
  if (currentWorkId && (entitiesCacheWorkId !== currentWorkId || entitiesCacheChapterId !== (currentChapterId || null))) {
    try { await loadWikiEntities(); } catch (e) { entitiesCache = []; }
  }
}
// 阅读视图 @提及 事件委托（只绑一次）
$("readView").addEventListener("click", e => {
  const m = e.target.closest(".mention");
  if (m) { showMentionPop(m); e.stopPropagation(); } else hideMentionPop();
});
$("readView").addEventListener("mouseover", e => {
  const m = e.target.closest(".mention");
  if (m) showMentionPop(m);
});
$("readView").addEventListener("mouseleave", hideMentionPop);

/* ---------- 阅读视图 ---------- */

let readerFontPx = +localStorage.getItem("rFont") || 19;
let readerLH = +localStorage.getItem("rLH") || 2;
let ttsPlaying = false;

async function toggleRead() {
  const r = $("reader");
  const closing = !r.classList.contains("hidden");
  if (closing && ttsPlaying) readerToggleTTS();
  r.classList.toggle("hidden");
  if (!r.classList.contains("hidden")) { await ensureEntities(); renderReader(); }
}
function renderReader() {
  $("readerTitle").textContent = $("chapTitle").value || "(无标题)";
  const v = $("readView");
  v.style.fontSize = readerFontPx + "px";
  v.style.lineHeight = readerLH;
  v.innerHTML = wrapMentions(esc($("content").value)).replace(/\n/g, "<br>");
  $("readerJump").innerHTML = chapters.map(c =>
    `<option value="${c.id}" ${c.id === currentChapterId ? "selected" : ""}>${esc(c.title)}</option>`).join("");
}
async function readerPrev() {
  const i = chapters.findIndex(c => c.id === currentChapterId);
  if (i > 0 && await flushEditorSave()) { currentChapterId = chapters[i - 1].id; await Promise.all([loadChapter(), loadAgentSessions()]); renderReader(); renderTree(); }
}
async function readerNext() {
  const i = chapters.findIndex(c => c.id === currentChapterId);
  if (i >= 0 && i < chapters.length - 1 && await flushEditorSave()) { currentChapterId = chapters[i + 1].id; await Promise.all([loadChapter(), loadAgentSessions()]); renderReader(); renderTree(); }
}
async function readerJumpTo() {
  const cid = +$("readerJump").value;
  if (cid && cid !== currentChapterId && await flushEditorSave()) { currentChapterId = cid; await Promise.all([loadChapter(), loadAgentSessions()]); renderReader(); renderTree(); }
}
function readerFont(d) { readerFontPx = Math.min(32, Math.max(14, readerFontPx + d)); localStorage.setItem("rFont", readerFontPx); $("readView").style.fontSize = readerFontPx + "px"; }
function readerLine() { readerLH = readerLH >= 2.6 ? 1.6 : +(readerLH + 0.3).toFixed(1); localStorage.setItem("rLH", readerLH); $("readView").style.lineHeight = readerLH; }
function setReaderTts(on) { setIcon($("ttsBtn"), on ? "square" : "play", on ? "停" : "朗读"); }
function readerToggleTTS() {
  if (!("speechSynthesis" in window)) { showToast("浏览器不支持朗读", "err"); return; }
  if (ttsPlaying) { speechSynthesis.cancel(); ttsPlaying = false; setReaderTts(false); return; }
  const u = new SpeechSynthesisUtterance($("content").value);
  u.lang = "zh-CN"; u.rate = 1;
  u.onend = () => { ttsPlaying = false; setReaderTts(false); };
  speechSynthesis.speak(u); ttsPlaying = true; setReaderTts(true);
}

/* ---------- 创作生产画布 ---------- */

const productionCategoryLabels = {
  character: "人物", rule: "规则", location: "地点", skill: "技能", item: "道具", organization: "组织",
};
const productionCategoryIcons = {
  character: "users", rule: "book", location: "focus", skill: "sparkles", item: "archive", organization: "panel",
};
const productionStateLabels = {
  location: "地点", goal: "目标", emotion: "情绪", physical: "身体",
  information: "已知", relationships: "关系", assets: "能力/物品", secrets: "秘密/承诺", notes: "补充",
};

function productionChapterOptions(selectedId = productionChapterId) {
  return chapters.map(chapter => `<option value="${chapter.id}" ${chapter.id === +selectedId ? "selected" : ""}>第${chapter.ord}章《${esc(chapter.title || "未命名")}》</option>`).join("");
}

function syncProductionHeader() {
  const select = $("productionChapterSelect");
  if (select) {
    select.innerHTML = productionChapterOptions();
    select.disabled = !chapters.length;
  }
  const locate = $("productionPlanLocate");
  if (locate) {
    locate.innerHTML = `<option value="">定位章节</option>${productionChapterOptions(storyPlanLocateChapterId)}`;
    locate.disabled = !chapters.length;
    locate.classList.toggle("hidden", productionSection !== "plan");
  }
  $("productionCanonBtn")?.classList.toggle("active", productionSection === "canon");
  $("productionPlanBtn")?.classList.toggle("active", productionSection === "plan");
  $("productionStateBtn")?.classList.toggle("active", productionSection === "state");
  const chapter = chapters.find(item => item.id === productionChapterId);
  $("productionSubtitle").textContent = productionSection === "canon" ? "人物与世界事实"
    : productionSection === "plan" ? "已选择的未来与候选推演"
    : (chapter ? `第${chapter.ord}章《${chapter.title || "未命名"}》` : "请选择章节");
  $("productionChapterSelect")?.classList.toggle("hidden", productionSection !== "state");
  $("productionAnalyzeBtn")?.classList.toggle("hidden", productionSection !== "state");
  $("productionAnalyzeBtn").disabled = productionSection !== "state" || !productionChapterId;
  $("productionSettingsBar")?.classList.add("hidden");
}

async function openProductionCanvas() {
  if (!currentWorkId) { showToast("请先新建或选择一个作品", "err"); return; }
  if (!await flushEditorSave()) return;
  closeStoryDrawer();
  $("app").classList.remove("side-open");
  $("app").classList.add("production-open");
  const workspace = $("productionWorkspace");
  workspace.classList.toggle("resources-closed", window.innerWidth < 1100);
  productionChapterId = currentChapterId || chapters[chapters.length - 1]?.id || null;
  if (!['canon', 'plan', 'state'].includes(productionSection)) productionSection = "canon";
  syncProductionHeader();
  await setProductionSection(productionSection, true);
}

async function maybePromptProductionAnalysis() {
  if (productionSection !== "state" || !productionChapterId || !productionChapterData?.settings?.auto_analyze_on_leave) return true;
  const status = productionChapterData.chapter?.production_analysis_status;
  if (status === "current" || !(productionChapterData.chapter?.content || "").trim()) return true;
  const analyze = await askCard({
    title: "本章画布尚未更新", msg: "正文已经保存，但场景与状态还没有重新分析。现在分析本章吗？",
    okText: "分析本章",
  });
  if (analyze) await analyzeProductionChapter();
  return true;
}

async function closeProductionCanvas() {
  await flushProductionLayoutSave();
  if (!await closeProductionInspector()) return;
  await maybePromptProductionAnalysis();
  $("app").classList.remove("production-open");
  clearProductionAgentTarget();
  if (currentChapterId) await loadChapter();
  return true;
}

async function showProductionOverview() {
  await setProductionSection("canon");
}

async function showProductionChapter() {
  await setProductionSection("state");
}

async function setProductionSection(section, opening = false) {
  if (!['canon', 'plan', 'state'].includes(section)) section = 'canon';
  if (!opening && productionSection === 'state' && section !== 'state') await maybePromptProductionAnalysis();
  await flushProductionLayoutSave();
  if (!await closeProductionInspector()) return;
  productionSection = section;
  if (window.innerWidth < 1100) $("productionWorkspace")?.classList.add("resources-closed");
  localStorage.setItem("productionSection", section);
  const planMode = section === "plan";
  $("productionResources")?.classList.toggle("hidden", planMode);
  $("storyPlanTreePane")?.classList.toggle("hidden", !planMode);
  $("storyPlanWorkspace")?.classList.toggle("hidden", !planMode);
  $("productionOverview")?.classList.add("hidden");
  $("productionCanvasViewport")?.classList.add("hidden");
  syncProductionHeader();
  if (planMode) {
    productionMode = "plan";
    await loadStoryPlan();
  } else if (section === "state") {
    if (!productionChapterId) productionChapterId = currentChapterId || chapters[chapters.length - 1]?.id || null;
    if (!productionChapterId) { showToast("当前作品还没有章节", "err"); productionSection = "canon"; return setProductionSection("canon", true); }
    productionMode = "chapter";
    await loadProductionChapter();
  } else {
    productionMode = "overview";
    await loadProductionOverview();
  }
}

async function changeProductionChapter(value) {
  const next = +value;
  if (!next || next === productionChapterId) return;
  await maybePromptProductionAnalysis();
  await flushProductionLayoutSave();
  if (!await closeProductionInspector()) return;
  productionChapterId = next;
  if (currentChapterId !== next) await selectChapter(next);
  productionSection = "state";
  productionMode = "chapter";
  productionSection = "state";
  localStorage.setItem("productionSection", "state");
  syncProductionHeader();
  await loadProductionChapter();
}

function locateStoryPlanChapter(value) {
  storyPlanLocateChapterId = +value || null;
  syncProductionHeader();
  renderStoryPlan();
  const row = $("storyPlanTree")?.querySelector(`[data-plan-chapter="${storyPlanLocateChapterId}"]`);
  row?.scrollIntoView({ block: "center", behavior: "smooth" });
}

function setProductionLoading(on) {
  $("productionLoading")?.classList.toggle("hidden", !on);
}

async function loadProductionResources(chapterId = productionChapterId) {
  if (!currentWorkId) return;
  const query = chapterId ? `?chapter_id=${chapterId}` : "";
  const [cards] = await Promise.all([
    api(`/api/works/${currentWorkId}/production/cards${query}`, { method: "GET" }),
    loadWikiEntities(),
  ]);
  productionCards = Array.isArray(cards) ? cards : [];
  renderProductionResources();
}

async function loadProductionOverview() {
  if (!currentWorkId) return;
  setProductionLoading(true);
  try {
    const [overview] = await Promise.all([
      api(`/api/works/${currentWorkId}/production`, { method: "GET" }),
      loadProductionResources(null),
    ]);
    productionOverviewData = overview;
    syncProductionSettings(overview.settings || {});
    renderProductionOverview();
    $("productionOverview").classList.remove("hidden");
    $("productionCanvasViewport").classList.add("hidden");
  } catch (e) {
    $("productionOverview").innerHTML = `<div class="production-empty">${svg("alert")}<b>画布读取失败</b><span>${esc(e.message)}</span></div>`;
  } finally { setProductionLoading(false); applyIcons(); }
}

async function loadProductionChapter() {
  if (!productionChapterId) return;
  setProductionLoading(true);
  try {
    const [chapterData, plans] = await Promise.all([
      api(`/api/chapters/${productionChapterId}/production`, { method: "GET" }),
      api(`/api/works/${currentWorkId}/story-plan`, { method: "GET" }),
    ]);
    productionChapterData = chapterData;
    productionChapterPlans = (plans || []).filter(item => item.chapter_id === productionChapterId);
    const layoutDraftKey = `productionLayoutDraft:${currentUsername || "user"}:${currentWorkId}:${productionChapterId}`;
    try {
      const draft = JSON.parse(localStorage.getItem(layoutDraftKey) || "null");
      if (draft?.layout && (!productionChapterData.layout?.updated_at || draft.saved_at / 1000 > productionChapterData.layout.updated_at)) {
        productionChapterData.layout = { ...(productionChapterData.layout || {}), layout: draft.layout };
        $("productionSaveStatus").textContent = "已恢复本机布局";
      }
    } catch (e) { /* ignore malformed local draft */ }
    productionCards = productionChapterData.after?.cards || [];
    syncProductionSettings(productionChapterData.settings || {});
    renderProductionResources();
    renderChapterPlanSummary();
    renderProductionCanvas();
    $("productionOverview").classList.add("hidden");
    $("productionCanvasViewport").classList.remove("hidden");
  } catch (e) {
    $("productionCanvas").innerHTML = `<div class="production-empty">${svg("alert")}<b>章节画布读取失败</b><span>${esc(e.message)}</span></div>`;
  } finally { setProductionLoading(false); applyIcons(); }
}

function renderChapterPlanSummary() {
  const host = $("chapterPlanSummary");
  if (!host) return;
  const plans = productionChapterPlans || [];
  host.innerHTML = `<div class="chapter-plan-summary-head"><b>本章计划</b><small>${plans.length ? `${plans.length} 条规划` : "暂无规划"}</small><button onclick="setProductionSection('plan')">查看规划</button></div>
    ${plans.length ? `<div class="chapter-plan-summary-list">${plans.map(plan => `<div><b>${esc(plan.title)}</b><span>${esc(plan.goal || plan.expected_outcome || plan.summary || "未填写目标")}</span><small>${esc(storyPlanRealizationLabel(plan, productionChapterId))}</small><button onclick="reviewChapterPlan(${plan.id})">核对</button></div>`).join("")}</div>` : ""}`;
}

async function reviewChapterPlan(planId) {
  const plan = productionChapterPlans.find(row => row.id === planId);
  if (!plan) return;
  if (!await closeProductionInspector()) return;
  productionSelected = { type: "plan_review", item: plan };
  const current = (plan.realizations || []).find(row => row.source_current && row.chapter_id === productionChapterId);
  openProductionInspector(plan.title, "正文实现核对", `<div class="production-inspector-form">
    <label>核对结果<select id="chapterPlanReviewStatus">${Object.entries({pending:"待核对",partial:"部分实现",realized:"已实现",deviated:"已偏离"}).map(([value,label]) => `<option value="${value}" ${current?.status === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
    <label>正文证据<textarea id="chapterPlanReviewEvidence" rows="5" placeholder="可选；作者手动核对可直接确认">${esc(current?.evidence || "")}</textarea></label>
    <label>说明<textarea id="chapterPlanReviewNotes">${esc(current?.notes || "")}</textarea></label>
    <div class="production-inspector-actions"><button onclick="saveChapterPlanReview(${plan.id})">保存核对</button><button onclick="handoffStoryPlanToAI(${plan.id})">交给 AI</button></div>
  </div>`);
}

async function saveChapterPlanReview(planId) {
  const plan = productionChapterPlans.find(row => row.id === planId);
  if (!plan) return;
  try {
    await api(`/api/story-plan/${planId}/realizations`, { body: {
      chapter_id: productionChapterId, status: $("chapterPlanReviewStatus").value,
      evidence: $("chapterPlanReviewEvidence").value, notes: $("chapterPlanReviewNotes").value,
      expected_plan_revision: plan.revision,
      expected_content_revision: productionChapterData?.chapter?.content_revision,
    }});
    await closeProductionInspector({ flush: false });
    await loadProductionChapter();
    showToast("核对已保存", "ok");
  } catch (e) { showToast(e.message, "err"); }
}

function syncProductionSettings(settings) {
  if ($("productionEvidenceEnabled")) $("productionEvidenceEnabled").checked = settings.evidence_enabled !== false;
  if ($("productionAutoAnalyze")) $("productionAutoAnalyze").checked = !!settings.auto_analyze_on_leave;
}

function renderProductionOverview() {
  const data = productionOverviewData;
  if (!data) return;
  const counts = data.card_counts || {};
  const items = productionResourceItems();
  const categories = ["character", "rule", "location", "skill", "item", "organization"];
  $("productionOverview").innerHTML = `
    <div class="production-overview-head"><h2>世界设定</h2><button onclick="openWorkNotes(${currentWorkId})" data-ic="book" data-label="创作总则"></button></div>
    <div class="production-stat-strip">${categories.map(category => `<button onclick="setProductionResourceCategory('${category}');$('productionResourceSearch')?.focus()"><b>${category === "character" ? items.filter(item => item.category === category).length : counts[category] || 0}</b><small>${productionCategoryLabels[category]}</small></button>`).join("")}</div>
    <div class="production-world-groups">${categories.map(category => {
      const group = items.filter(item => item.category === category);
      return `<section><h3>${productionCategoryLabels[category]} <small>${group.length}</small></h3><div>${group.slice(0, 12).map(item => `<button onclick="${item.resource_type === "character" ? `inspectProductionCharacter(${item.id})` : `inspectProductionCard(${item.id})`}"><b>${esc(item.name)}</b><small>${esc(item.resource_summary)}</small></button>`).join("") || '<span class="production-empty compact">暂无设定</span>'}</div></section>`;
    }).join("")}</div>`;
  applyIcons();
}

async function openProductionChapterFromOverview(chapterId) {
  productionChapterId = chapterId;
  if (currentChapterId !== chapterId) await selectChapter(chapterId);
  productionMode = "chapter";
  productionSection = "state";
  localStorage.setItem("productionSection", "state");
  syncProductionHeader();
  await loadProductionChapter();
}

function toggleProductionResources(force) {
  const workspace = $("productionWorkspace");
  const open = force == null ? workspace.classList.contains("resources-closed") : !!force;
  workspace.classList.toggle("resources-closed", !open);
}

function setProductionResourceCategory(category) {
  productionResourceCategory = category;
  document.querySelectorAll("#productionResourceTabs button").forEach(button =>
    button.classList.toggle("active", button.dataset.category === category));
  renderProductionResources();
}

function productionResourceItems() {
  const characters = (productionChapterData?.after?.characters || entitiesCache.filter(item => item.kind === "人物")).map(item => ({
    ...item, resource_type: "character", category: "character",
    resource_summary: item.summary || characterStateBrief(item.current_state || {}) || "人物卡",
  }));
  const cards = productionCards.map(item => ({
    ...item, resource_type: "card", resource_summary: item.summary || item.detail || "尚未填写说明",
  }));
  return [...characters, ...cards];
}

function renderProductionResources() {
  const host = $("productionResourceList");
  if (!host) return;
  const query = ($("productionResourceSearch")?.value || "").trim().toLowerCase();
  const items = productionResourceItems().filter(item => {
    if (productionResourceCategory !== "all" && item.category !== productionResourceCategory) return false;
    return !query || `${item.name} ${item.resource_summary}`.toLowerCase().includes(query);
  });
  $("productionResourceCount").textContent = `${items.length} 项`;
  host.innerHTML = items.length ? items.map(item => {
    const active = productionSelected?.type === item.resource_type && productionSelected?.item?.id === item.id;
    return `<button class="production-resource-row production-category-${item.category} ${active ? "active" : ""}" onclick="${item.resource_type === "character" ? `inspectProductionCharacter(${item.id})` : `inspectProductionCard(${item.id})`}">
      <span class="production-resource-mark"></span>
      <span class="production-resource-copy"><b>${esc(item.name)}</b><small>${esc(item.resource_summary)}</small></span>
      <small>${esc(productionCategoryLabels[item.category] || item.category)}</small>
    </button>`;
  }).join("") : `<div class="production-empty">${svg("search")}<span>没有匹配的资源</span></div>`;
}

const storyPlanTypeLabels = {
  book: "总纲", volume: "卷纲", arc: "阶段", chapter: "章节", scene: "场景", subplot: "支线", foreshadow: "伏笔",
};
const storyPlanStatusLabels = { planned: "规划中", committed: "已确定", abandoned: "已放弃" };
const storyPlanPolicyLabels = { auto: "自动", planning_only: "仅规划", writing_range: "写作范围", never: "不发送 AI" };

function rebuildStoryPlanIndexes() {
  storyPlanChildrenIndex = new Map();
  storyPlanNodes.forEach(item => {
    const parent = item.parent_id || null;
    if (!storyPlanChildrenIndex.has(parent)) storyPlanChildrenIndex.set(parent, []);
    storyPlanChildrenIndex.get(parent).push(item);
  });
  storyPlanChildrenIndex.forEach(items => items.sort((a, b) => (a.ord || 0) - (b.ord || 0) || a.id - b.id));
}

async function loadStoryPlan() {
  if (!currentWorkId) return;
  setProductionLoading(true);
  try {
    const [plans] = await Promise.all([
      api(`/api/works/${currentWorkId}/story-plan?include_abandoned=${storyPlanShowArchive ? 1 : 0}`, { method: "GET" }),
      loadProductionResources(productionChapterId),
    ]);
    storyPlanNodes = Array.isArray(plans) ? plans : [];
    rebuildStoryPlanIndexes();
    renderStoryPlan();
  } catch (e) {
    $("storyPlanBoard").innerHTML = `<div class="production-empty">${svg("alert")}<b>剧情规划读取失败</b><span>${esc(e.message)}</span></div>`;
  } finally { setProductionLoading(false); applyIcons(); }
}

function storyPlanChildren(parentId) {
  return storyPlanChildrenIndex.get(parentId || null) || [];
}

function storyPlanSearchMatches() {
  const query = ($("storyPlanSearch")?.value || "").trim().toLowerCase();
  if (!query) return null;
  const byId = new Map(storyPlanNodes.map(item => [item.id, item]));
  const visible = new Set();
  storyPlanNodes.forEach(item => {
    if (!`${item.title} ${item.summary} ${item.goal} ${item.conflict} ${item.expected_outcome} ${item.detail}`.toLowerCase().includes(query)) return;
    let current = item;
    while (current && !visible.has(current.id)) {
      visible.add(current.id);
      current = byId.get(current.parent_id);
    }
  });
  return visible;
}

function storyPlanTreeRows(parentId = null, depth = 0, visited = new Set(), matches = null) {
  return storyPlanChildren(parentId).map(item => {
    if (visited.has(item.id) || (matches && !matches.has(item.id))) return "";
    const nextVisited = new Set(visited); nextVisited.add(item.id);
    const selected = productionSelected?.type === "story_plan" && productionSelected.item?.id === item.id;
    const expanded = !!matches || !storyPlanCollapsed.has(item.id);
    const hasChildren = storyPlanChildren(item.id).length > 0;
    return `<div class="story-plan-tree-group">
      ${hasChildren ? `<button class="story-plan-collapse" onclick="toggleStoryPlanCollapsed(${item.id})" aria-label="${expanded ? "收起" : "展开"}${esc(item.title)}">${expanded ? "⌄" : "›"}</button>` : ""}
      <button class="story-plan-tree-row ${selected ? "active" : ""} ${item.status === "abandoned" ? "muted" : ""}"
              style="--plan-depth:${Math.min(depth, 6)}" data-plan-chapter="${item.chapter_id || ""}" onclick="inspectStoryPlan(${item.id})">
        <span class="story-plan-type">${esc(storyPlanTypeLabels[item.node_type] || item.node_type)}</span>
        <span><b>${esc(item.title)}</b><small>${esc(storyPlanStatusLabels[item.status] || item.status)}</small></span>
      </button>${expanded ? storyPlanTreeRows(item.id, depth + 1, nextVisited, matches) : ""}
    </div>`;
  }).join("");
}

function toggleStoryPlanCollapsed(id) {
  if (storyPlanCollapsed.has(id)) storyPlanCollapsed.delete(id); else storyPlanCollapsed.add(id);
  renderStoryPlan();
}

function storyPlanMatchesFilter(item) {
  if (storyPlanFilter === "all") return true;
  if (storyPlanFilter === "volume") return ["volume", "arc"].includes(item.node_type);
  if (storyPlanFilter === "thread") return ["subplot", "foreshadow"].includes(item.node_type);
  if (storyPlanFilter === "deviated") return (item.realizations || []).some(row => row.source_current && row.status === "deviated");
  return item.node_type === storyPlanFilter;
}

function storyPlanRouteRows(parentId = null, depth = 0, visited = new Set(), matches = null) {
  return storyPlanChildren(parentId).map(item => {
    if (visited.has(item.id)) return "";
    const nextVisited = new Set(visited); nextVisited.add(item.id);
    const children = storyPlanChildren(item.id);
    const expanded = !!matches || !storyPlanCollapsed.has(item.id);
    const visible = storyPlanMatchesFilter(item) && (!matches || matches.has(item.id))
      && (!storyPlanShowArchive || item.status === "abandoned");
    const childHtml = expanded ? storyPlanRouteRows(item.id, depth + 1, nextVisited, matches) : "";
    const row = visible ? `<div class="story-plan-route-row ${item.chapter_id === storyPlanLocateChapterId ? "current" : ""}" style="--plan-depth:${Math.min(depth, 6)}">
      ${children.length ? `<button class="ic story-plan-route-toggle" onclick="toggleStoryPlanCollapsed(${item.id})" title="${expanded ? "收起" : "展开"}">${expanded ? "⌄" : "›"}</button>` : '<span class="story-plan-route-toggle"></span>'}
      <button class="story-plan-route-main" onclick="inspectStoryPlan(${item.id})"><small>${esc(storyPlanTypeLabels[item.node_type] || item.node_type)}</small><b>${esc(item.title)}</b><span>${esc(item.summary || item.goal || "")}</span></button>
      <span class="story-plan-route-status">${esc(storyPlanStatusLabels[item.status] || item.status)} · ${esc(storyPlanRealizationLabel(item))}</span>
      <button class="ic" onclick="moveStoryPlan(${item.id},'up')" title="上移同级规划">${svg("chevU")}</button><button class="ic" onclick="moveStoryPlan(${item.id},'down')" title="下移同级规划">${svg("chevD")}</button>
    </div>` : "";
    return row + childHtml;
  }).join("");
}

function renderStoryPlan() {
  const matches = storyPlanSearchMatches();
  $("storyPlanCount").textContent = `${storyPlanNodes.filter(item => item.status !== "abandoned").length} 项`;
  $("storyPlanTree").innerHTML = storyPlanTreeRows(null, 0, new Set(), matches) || `<div class="production-empty compact"><span>${matches ? "没有匹配的计划" : "尚无正式规划"}</span></div>`;
  document.querySelectorAll("[data-plan-filter]").forEach(button =>
    button.classList.toggle("active", button.dataset.planFilter === storyPlanFilter));
  $("storyPlanRouteBtn")?.classList.toggle("active", storyPlanView === "route");
  $("storyPlanCardBtn")?.classList.toggle("active", storyPlanView === "cards");
  $("storyPlanArchiveBtn")?.classList.toggle("active", storyPlanShowArchive);
  const items = storyPlanNodes.filter(item => storyPlanMatchesFilter(item) && (!matches || matches.has(item.id))
    && (!storyPlanShowArchive || item.status === "abandoned"));
  const covered = new Set(storyPlanNodes.filter(item => item.chapter_id).map(item => item.chapter_id));
  const deviations = storyPlanNodes.filter(item => (item.realizations || []).some(row => row.source_current && row.status === "deviated")).length;
  $("storyPlanHealth").innerHTML = `<button onclick="setStoryPlanFilter('volume')">${storyPlanNodes.filter(item => item.node_type === "volume" || item.node_type === "arc").length} 卷 / 阶段</button><button onclick="setStoryPlanFilter('chapter')">${covered.size} / ${chapters.length} 章已有计划</button><button onclick="setStoryPlanFilter('thread')">${storyPlanNodes.filter(item => ["subplot", "foreshadow"].includes(item.node_type)).length} 支线 / 伏笔</button><button onclick="setStoryPlanFilter('deviated')">${deviations} 项有效偏离</button>`;
  const currentChapter = storyPlanLocateChapterId || productionChapterId;
  const cardHtml = item => {
    const body = item.context_summary || item.summary || item.goal || item.detail || "尚未填写内容";
    const realized = storyPlanRealizationAt(item, currentChapter);
    return `<button class="story-plan-card type-${item.node_type} ${item.status === "abandoned" ? "abandoned" : ""} ${item.chapter_id === currentChapter ? "current" : ""}" onclick="inspectStoryPlan(${item.id})">
      <span class="story-plan-card-head"><span>${esc(storyPlanTypeLabels[item.node_type] || item.node_type)}</span><small>${esc(storyPlanStatusLabels[item.status] || item.status)}</small></span>
      <b>${esc(item.title)}</b><p>${esc(body)}</p>
      <span class="story-plan-card-meta">${item.chapter_title ? `第${item.chapter_ord || "?"}章` : "未关联章节"}<i>${esc(storyPlanPolicyLabels[item.context_policy] || item.context_policy)}</i>${realized ? `<i>${esc({pending:"待核对",partial:"部分实现",realized:"已实现",deviated:"已偏离"}[realized.status] || realized.status)}</i>` : ""}</span>
    </button>`;
  };
  $("storyPlanBoard").innerHTML = items.length ? (storyPlanView === "cards"
    ? `<div class="story-plan-grid">${items.slice(0, 200).map(cardHtml).join("")}</div>${items.length > 200 ? `<p class="set-hint">只显示前 200 项，请使用搜索或分类缩小范围。</p>` : ""}`
    : `<div class="story-plan-route">${storyPlanRouteRows(null, 0, new Set(), matches)}</div>`)
    : `<div class="production-empty">${svg("nodes")}<b>${storyPlanShowArchive ? "暂无废案" : "这个分类还没有规划"}</b><button onclick="newStoryPlanNode()">新建计划</button></div>`;
  applyIcons();
}

function setStoryPlanView(view) {
  storyPlanView = view === "cards" ? "cards" : "route";
  localStorage.setItem("storyPlanView", storyPlanView);
  renderStoryPlan();
}

async function toggleStoryPlanArchive() {
  storyPlanShowArchive = !storyPlanShowArchive;
  await loadStoryPlan();
}

function setStoryPlanFilter(filter) {
  storyPlanFilter = filter;
  renderStoryPlan();
}

function storyPlanParentOptions(value, selfId = null) {
  return `<option value="">顶层</option>` + storyPlanNodes.filter(item => item.id !== selfId && item.status !== "abandoned")
    .map(item => `<option value="${item.id}" ${item.id === +value ? "selected" : ""}>${esc(storyPlanTypeLabels[item.node_type])} · ${esc(item.title)}</option>`).join("");
}

function storyPlanLinkPicker(item) {
  const links = new Set((item.links || []).map(link => `${link.target_type}:${link.target_id}`));
  const characters = entitiesCache.filter(entity => entity.kind === "人物");
  const cards = productionCards;
  if (!characters.length && !cards.length) return "";
  return `<div class="production-inspector-section"><h3>关联设定</h3>
    <div id="storyPlanSelectedLinks" class="story-plan-selected-links"></div>
    <input id="storyPlanLinkSearch" type="search" placeholder="搜索人物、地点、规则…" oninput="filterStoryPlanLinks()">
    <div class="story-plan-link-picker">
      ${characters.map(entity => `<label data-link-name="${esc(entity.name.toLowerCase())}"><input type="checkbox" data-plan-link="entity" value="${entity.id}" onchange="updateStoryPlanSelectedLinks()" ${links.has(`entity:${entity.id}`) ? "checked" : ""}><span>人物 · ${esc(entity.name)}</span></label>`).join("")}
      ${cards.map(card => `<label data-link-name="${esc(card.name.toLowerCase())}"><input type="checkbox" data-plan-link="production_card" value="${card.id}" onchange="updateStoryPlanSelectedLinks()" ${links.has(`production_card:${card.id}`) ? "checked" : ""}><span>${esc(productionCategoryLabels[card.category] || "设定")} · ${esc(card.name)}</span></label>`).join("")}
    </div></div>`;
}

function filterStoryPlanLinks() {
  const query = ($("storyPlanLinkSearch")?.value || "").trim().toLowerCase();
  document.querySelectorAll(".story-plan-link-picker label").forEach(label =>
    label.classList.toggle("hidden", !!query && !label.dataset.linkName.includes(query)));
}

function updateStoryPlanSelectedLinks() {
  const host = $("storyPlanSelectedLinks");
  if (!host) return;
  host.innerHTML = [...document.querySelectorAll("[data-plan-link]:checked")].map(input =>
    `<button type="button" onclick="removeStoryPlanLink('${input.dataset.planLink}',${+input.value})">${esc(input.closest("label")?.innerText || "设定")} ×</button>`).join("");
}

function removeStoryPlanLink(kind, id) {
  const input = document.querySelector(`[data-plan-link="${kind}"][value="${id}"]`);
  if (!input) return;
  input.checked = false;
  updateStoryPlanSelectedLinks();
  queueStoryPlanSave();
}

function storyPlanRealizationHtml(item) {
  const rows = (item.realizations || []).filter(row => !row.is_stale);
  if (!rows.length) return "";
  return `<div class="production-inspector-section"><h3>正文实现</h3>${rows.map(row => `<div class="production-evidence"><b>第${row.chapter_ord || "?"}章 · ${esc({pending:"待核对",partial:"部分实现",realized:"已实现",deviated:"已偏离"}[row.status] || row.status)}</b><br>${esc(row.notes || row.evidence || "无补充")}</div>`).join("")}</div>`;
}

function storyPlanForm(item) {
  return `<div class="production-inspector-form story-plan-form" oninput="if(event.target.id!=='storyPlanLinkSearch')queueStoryPlanSave()" onchange="queueStoryPlanSave()">
    <div class="story-plan-inspector-state"><span>${esc(storyPlanStatusLabels[item.status] || item.status)}</span><span>${esc(storyPlanRealizationLabel(item))}</span></div>
    <label>标题<input id="storyPlanTitle" maxlength="240" value="${esc(item.title || "")}" placeholder="例如：第一卷 · 失踪案"></label>
    <label>摘要<textarea id="storyPlanSummary">${esc(item.summary || "")}</textarea></label>
    <label>写作目标<textarea id="storyPlanGoal">${esc(item.goal || "")}</textarea></label>
    <label>核心冲突<textarea id="storyPlanConflict">${esc(item.conflict || "")}</textarea></label>
    <label>预期结果<textarea id="storyPlanOutcome">${esc(item.expected_outcome || "")}</textarea></label>
    <details class="story-plan-detail" ${item.detail ? "open" : ""}><summary>详细计划</summary><textarea id="storyPlanDetail" rows="6">${esc(item.detail || "")}</textarea></details>
    <label class="${item.node_type === "chapter" ? "" : "hidden"}">对应章节<select id="storyPlanChapter">${productionChapterSelectOptions(item.chapter_id)}</select></label>
    <label class="story-plan-checkbox"><input id="storyPlanWritingReference" type="checkbox" ${!["planning_only", "never"].includes(item.context_policy) ? "checked" : ""} onchange="toggleStoryPlanWritingReference()">写作时参考</label>
    <details class="story-plan-advanced"><summary>更多设置</summary>
      <div class="production-form-grid">
        <label>类型<select id="storyPlanType" onchange="updateStoryPlanChapterField()">${Object.entries(storyPlanTypeLabels).map(([value,label]) => `<option value="${value}" ${item.node_type === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
        <label>上级<select id="storyPlanParent">${storyPlanParentOptions(item.parent_id, item.id)}</select></label>
      </div>
      <label>上下文策略<select id="storyPlanPolicy" onchange="syncStoryPlanWritingReference()">${Object.entries(storyPlanPolicyLabels).map(([value,label]) => `<option value="${value}" ${item.context_policy === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
      <div class="production-form-grid"><label>范围起点<select id="storyPlanRangeStart">${productionChapterSelectOptions(item.scope_start_chapter_id)}</select></label><label>范围终点<select id="storyPlanRangeEnd">${productionChapterSelectOptions(item.scope_end_chapter_id)}</select></label></div>
      <label>上下文方向摘要<textarea id="storyPlanContextSummary" placeholder="可选；不填时使用摘要">${esc(item.context_summary || "")}</textarea></label>
      ${storyPlanLinkPicker(item)}
      ${item.id ? '<button onclick="previewStoryPlanContext()">预览写作上下文</button><div id="storyPlanContextPreview" class="story-plan-context-preview hidden"></div>' : ""}
      ${item.id ? `<button onclick="showStoryPlanVersions(${item.id})">版本历史</button>` : ""}
    </details>
    ${storyPlanRealizationHtml(item)}
    <div class="production-inspector-actions">
      <button onclick="saveStoryPlanFromInspector()">${item.id ? "保存" : "创建规划"}</button>
      ${item.id ? `<button onclick="handoffStoryPlanToAI(${item.id})">交给 AI</button><button onclick="openSandboxFromStoryPlan(${item.id})">从这里推演</button>${item.node_type === "chapter" ? `<button onclick="openOrCreatePlanChapter(${item.id})">${item.chapter_id ? "打开正文" : "创建对应章节"}</button>` : ""}<button onclick="newStoryPlanNode(${item.id})">新建子计划</button>${item.status === "abandoned" ? `<button onclick="restoreStoryPlanBranch(${item.id})">恢复分支</button>` : `<button onclick="setStoryPlanStatus(${item.id},'${item.status === "committed" ? "planned" : "committed"}')">${item.status === "committed" ? "改回规划中" : "确定采用"}</button><button class="danger-link" onclick="abandonStoryPlan(${item.id})">放弃此规划</button>`}` : '<button onclick="closeProductionInspector({discard:true})">取消</button>'}
    </div>
  </div>`;
}

function storyPlanRealizationAt(item, chapterId = null) {
  const limit = chapters.find(chapter => chapter.id === chapterId)?.ord ?? Infinity;
  return (item.realizations || []).filter(row => row.source_current && (row.chapter_ord ?? Infinity) <= limit)
    .sort((a, b) => (b.chapter_ord || 0) - (a.chapter_ord || 0) || b.id - a.id)[0] || null;
}

function storyPlanRealizationLabel(item, chapterId = null) {
  const current = storyPlanRealizationAt(item, chapterId);
  if (current) return { pending: "待核对", partial: "部分实现", realized: "已实现", deviated: "已偏离" }[current.status] || current.status;
  if (!item.chapter_id && !(item.realizations || []).length) return "未绑定正文";
  const limit = chapters.find(chapter => chapter.id === chapterId)?.ord ?? Infinity;
  return (item.realizations || []).some(row => (row.chapter_ord ?? Infinity) <= limit)
    ? "资料已变，待重新核对" : "待核对";
}

function toggleStoryPlanWritingReference() {
  if (!$("storyPlanPolicy")) return;
  const policy = $("storyPlanPolicy");
  const control = $("storyPlanWritingReference");
  if (control.checked) {
    const previous = control.dataset.previousPolicy;
    policy.value = previous === "writing_range" && ($("storyPlanRangeStart").value || $("storyPlanRangeEnd").value)
      ? previous : "auto";
  } else {
    control.dataset.previousPolicy = policy.value;
    policy.value = "planning_only";
  }
}

function syncStoryPlanWritingReference() {
  const policy = $("storyPlanPolicy").value;
  $("storyPlanWritingReference").checked = !["planning_only", "never"].includes(policy);
  if (!["planning_only", "never"].includes(policy)) $("storyPlanWritingReference").dataset.previousPolicy = policy;
}

function updateStoryPlanChapterField() {
  const field = $("storyPlanChapter")?.closest("label");
  if (field) field.classList.toggle("hidden", $("storyPlanType").value !== "chapter");
}

async function previewStoryPlanContext() {
  const selected = productionSelected;
  if (selected?.type !== "story_plan" || !selected.item?.id) return;
  clearTimeout(storyPlanSaveTimer);
  storyPlanSaveTimer = null;
  const saved = await saveStoryPlanFromInspector(true);
  if (!saved) { showToast("规划尚未同步，无法预览", "err"); return; }
  const chapterId = storyPlanLocateChapterId || saved.chapter_id || productionChapterId;
  const box = $("storyPlanContextPreview");
  if (!chapterId) {
    box.classList.remove("hidden");
    box.textContent = "请先在顶部定位一个章节，再预览写作上下文。";
    return;
  }
  try {
    const result = await api(`/api/chapters/${chapterId}/story-plan-context`, { method: "GET" });
    const included = (result.included || []).find(row => row.source_id === saved.id);
    const excluded = (result.excluded || []).find(row => row.id === saved.id);
    const chapter = chapters.find(row => row.id === chapterId);
    box.classList.remove("hidden");
    box.innerHTML = `<b>${esc(chapter?.title || "当前章节")} · ${included ? "写作上下文已选入" : "写作上下文未选入"}</b>`
      + (included ? `<p>${esc(included.reason || "")}</p><pre>${esc(included.content || "")}</pre>`
        : `<p>${esc(excluded?.reason || "未进入本次写作上下文；可能受到上下文预算影响")}</p>`);
  } catch (e) { showToast(e.message, "err"); }
}

function setStoryPlanAgentTarget(item) {
  agentSelection = { context_kind: "canvas_target", target_type: "story_plan", target_id: item.id,
    work_id: item.work_id,
    text: `[作者计划 / 尚未发生]\n${item.title}\n${item.summary || ""}\n目标：${item.goal || ""}\n冲突：${item.conflict || ""}\n预期结果：${item.expected_outcome || ""}`,
    title: item.title || "剧情规划" };
  renderAgentSelection();
}

async function handoffStoryPlanToAI(planId) {
  if (productionSelected?.type === "story_plan" && productionSelected.item?.id === planId) {
    clearTimeout(storyPlanSaveTimer);
    storyPlanSaveTimer = null;
    const saved = await saveStoryPlanFromInspector(true);
    if (!saved) { showToast("规划尚未同步，暂不能交给 AI", "err"); return; }
  }
  try {
    const item = await api(`/api/story-plan/${planId}`, { method: "GET" });
    if (item.work_id !== currentWorkId) throw new Error("目标不属于当前作品");
    setStoryPlanAgentTarget(item);
    openProductionAI();
  } catch (e) { showToast(e.message, "err"); }
}

async function setStoryPlanStatus(planId, status) {
  const item = storyPlanNodes.find(row => row.id === planId);
  if (!item) return;
  if (productionSelected?.item?.id === planId && $("storyPlanTitle")) {
    const saved = await saveStoryPlanFromInspector(true);
    if (!saved) return;
  }
  const current = storyPlanNodes.find(row => row.id === planId);
  try {
    const saved = await api(`/api/story-plan/${planId}`, { method: "PUT", body: {
      status, expected_revision: current.revision,
    }});
    storyPlanNodes[storyPlanNodes.findIndex(row => row.id === planId)] = saved;
    await loadStoryPlan();
    await inspectStoryPlan(planId);
  } catch (e) { showToast(e.message, "err"); }
}

async function openOrCreatePlanChapter(planId) {
  if (productionSelected?.item?.id === planId && $("storyPlanTitle")) {
    const saved = await saveStoryPlanFromInspector(true);
    if (!saved) return;
  }
  try {
    const result = await api(`/api/story-plan/${planId}/chapter`, { body: {} });
    if (!await closeProductionCanvas()) return;
    if (currentChapterId !== result.chapter.id) await selectChapter(result.chapter.id);
  } catch (e) { showToast(e.message, "err"); }
}

async function openSandboxFromStoryPlan(planId) {
  if (productionSelected?.item?.id === planId && $("storyPlanTitle")) {
    const saved = await saveStoryPlanFromInspector(true);
    if (!saved) return;
  }
  try {
    const plan = await api(`/api/story-plan/${planId}`, { method: "GET" });
    if (plan.source_sandbox_id) {
      $("outlineOverlay").classList.remove("hidden");
      sandboxList = await api(`/api/works/${currentWorkId}/sandboxes`, { method: "GET" });
      await loadOutlineSandbox(plan.source_sandbox_id);
      selectedSandboxNodeId = plan.source_node_id;
      renderOutlineSandbox(); renderSandboxInspector();
      return;
    }
    const kind = plan.node_type === "volume" ? "volume" : plan.node_type === "chapter" ? "chapter" : "plot";
    const id = sandboxNodeId(kind);
    const chapter = chapters.find(item => item.id === plan.chapter_id);
    const previous = chapter ? chapters.filter(item => item.ord < chapter.ord).sort((a, b) => b.ord - a.ord)[0] : null;
    const data = { nodes: [{ id, title: plan.title, summary: plan.summary || plan.goal || "",
      kind, direction: "主线", chapter_id: plan.chapter_id,
      context_chapter_id: chapter ? (previous?.id || null) : (storyPlanLocateChapterId || null),
      plan_node_id: plan.id, x: 220, y: 160 }], edges: [] };
    currentSandbox = await api(`/api/works/${currentWorkId}/sandboxes`, { body: { name: `从《${plan.title}》推演`, data } });
    sandboxList = await api(`/api/works/${currentWorkId}/sandboxes`, { method: "GET" });
    selectedSandboxNodeId = id;
    $("outlineOverlay").classList.remove("hidden");
    renderSandboxList(); renderOutlineSandbox(); renderSandboxInspector();
  } catch (e) { showToast(e.message, "err"); }
}

async function inspectStoryPlan(planId) {
  const item = storyPlanNodes.find(row => row.id === planId);
  if (!item) return;
  if (productionSelected && (productionSelected.type !== "story_plan" || productionSelected.item?.id !== planId)) {
    if (!await closeProductionInspector()) return;
  }
  productionSelected = { type: "story_plan", item };
  renderStoryPlan();
  openProductionInspector(item.title, `${storyPlanTypeLabels[item.node_type] || item.node_type} · v${item.revision}`, storyPlanForm(item));
  updateStoryPlanSelectedLinks();
}

async function newStoryPlanNode(parentId = null, requestedType = null) {
  if (productionSelected && !await closeProductionInspector()) return;
  const parent = storyPlanNodes.find(row => row.id === parentId);
  const hasBook = storyPlanNodes.some(row => row.node_type === "book" && row.status !== "abandoned");
  const nodeType = requestedType && storyPlanTypeLabels[requestedType] ? requestedType
    : parent ? ({ book: "volume", volume: "chapter", arc: "chapter", chapter: "scene" }[parent.node_type] || "chapter")
      : (hasBook ? "volume" : "book");
  const defaultParent = nodeType === "book" ? null : (parentId || (hasBook && !parent ? storyPlanNodes.find(row => row.node_type === "book")?.id : null));
  const item = { id: null, parent_id: defaultParent, node_type: nodeType,
    chapter_id: nodeType === "chapter" ? storyPlanLocateChapterId || productionChapterId : null,
    title: "", summary: "", detail: "", goal: "", conflict: "", expected_outcome: "", context_summary: "", status: "planned",
    context_policy: nodeType === "book" ? "planning_only" : "auto", links: [], realizations: [] };
  productionSelected = { type: "story_plan", item };
  openProductionInspector("新建剧情规划", "作者计划 · 尚未发生", storyPlanForm(item));
  updateStoryPlanSelectedLinks();
  setTimeout(() => $("storyPlanTitle")?.focus(), 30);
}

function readStoryPlanForm() {
  const links = [...document.querySelectorAll("[data-plan-link]:checked")].map(input => ({ target_type: input.dataset.planLink, target_id: +input.value }));
  return {
    title: $("storyPlanTitle").value.trim(), node_type: $("storyPlanType").value,
    parent_id: +$("storyPlanParent").value || null, status: productionSelected.item.status,
    context_policy: $("storyPlanPolicy").value, chapter_id: +$("storyPlanChapter").value || null,
    scope_start_chapter_id: +$("storyPlanRangeStart").value || null,
    scope_end_chapter_id: +$("storyPlanRangeEnd").value || null,
    summary: $("storyPlanSummary").value, goal: $("storyPlanGoal").value,
    conflict: $("storyPlanConflict").value, expected_outcome: $("storyPlanOutcome").value,
    context_summary: $("storyPlanContextSummary").value, detail: $("storyPlanDetail").value, links,
  };
}

function storyPlanDraftKey(selection = productionSelected) {
  if (!selection || !currentWorkId) return "";
  return `storyPlanDraft:${currentUsername || "user"}:${currentWorkId}:${selection.item?.id || "new"}`;
}

function stashStoryPlanDraft() {
  if (productionSelected?.type !== "story_plan" || !$("storyPlanTitle")) return;
  localStorage.setItem(storyPlanDraftKey(), JSON.stringify({ values: readStoryPlanForm(), saved_at: Date.now() }));
}

function restoreStoryPlanDraft() {
  let draft;
  try { draft = JSON.parse(localStorage.getItem(storyPlanDraftKey()) || "null"); } catch (e) { draft = null; }
  if (!draft?.values) return;
  const map = { title:"storyPlanTitle", node_type:"storyPlanType", parent_id:"storyPlanParent", status:"storyPlanStatus", context_policy:"storyPlanPolicy", chapter_id:"storyPlanChapter", scope_start_chapter_id:"storyPlanRangeStart", scope_end_chapter_id:"storyPlanRangeEnd", summary:"storyPlanSummary", goal:"storyPlanGoal", conflict:"storyPlanConflict", expected_outcome:"storyPlanOutcome", context_summary:"storyPlanContextSummary", detail:"storyPlanDetail" };
  Object.entries(map).forEach(([key,id]) => { if ($(id) && key in draft.values) $(id).value = draft.values[key] ?? ""; });
  document.querySelectorAll("[data-plan-link]").forEach(input => { input.checked = false; });
  (draft.values.links || []).forEach(link => { const input = document.querySelector(`[data-plan-link="${link.target_type}"][value="${link.target_id}"]`); if (input) input.checked = true; });
  if (draft.values.detail) $("storyPlanDetail")?.closest("details")?.setAttribute("open", "");
  syncStoryPlanWritingReference();
  updateStoryPlanSelectedLinks();
  $("productionSaveStatus").textContent = "已恢复本机草稿";
}

function queueStoryPlanSave() {
  stashStoryPlanDraft();
  $("productionSaveStatus").textContent = "已本机暂存";
  clearTimeout(storyPlanSaveTimer);
  if (!productionSelected?.item?.id) return;
  storyPlanSaveTimer = setTimeout(() => {
    storyPlanSaveTimer = null;
    saveStoryPlanFromInspector(true);
  }, 900);
}

async function saveStoryPlanFromInspector(quiet = false) {
  if (productionSelected?.type !== "story_plan") return null;
  const selection = productionSelected;
  const planId = selection.item.id || null;
  const body = readStoryPlanForm();
  if (!body.title) { if (!quiet) showToast("请填写规划标题", "err"); return null; }
  const oldDraftKey = storyPlanDraftKey(selection);
  const draftSnapshot = localStorage.getItem(oldDraftKey);
  return productionSaveQueue.run(async () => {
    const latest = planId
      ? (storyPlanNodes.find(row => row.id === planId) || selection.item)
      : selection.item;
    body.expected_revision = latest.revision;
    storyPlanSaving = true;
    $("productionSaveStatus").textContent = "正在同步…";
    try {
      const saved = await api(planId ? `/api/story-plan/${planId}` : `/api/works/${currentWorkId}/story-plan`, { method: planId ? "PUT" : "POST", body });
      if (localStorage.getItem(oldDraftKey) === draftSnapshot) localStorage.removeItem(oldDraftKey);
      const index = storyPlanNodes.findIndex(row => row.id === saved.id);
      if (index >= 0) storyPlanNodes[index] = saved; else storyPlanNodes.push(saved);
      rebuildStoryPlanIndexes();
      selection.item = saved;
      $("productionSaveStatus").textContent = "已同步";
      renderStoryPlan();
      if (!quiet || !planId) inspectStoryPlan(saved.id);
      if (!quiet) showToast(planId ? "剧情规划已保存" : "剧情规划已创建", "ok");
      return saved;
    } catch (e) {
      $("productionSaveStatus").textContent = "同步失败，草稿仍在本机";
      if (!quiet) showToast(e.message, "err");
      return null;
    } finally { storyPlanSaving = false; }
  });
}

async function abandonStoryPlan(planId) {
  const root = storyPlanNodes.find(row => row.id === planId);
  if (!root) return;
  let count = 0, pending = [planId];
  while (pending.length) { const id = pending.pop(); count += 1; pending.push(...storyPlanChildren(id).map(row => row.id)); }
  if (!await askCard({ title: `放弃《${root.title}》？`, msg: `将同时放弃该分支的 ${count} 条规划，保留版本和恢复入口。`, okText: "放弃分支", danger: true })) return;
  try { await api(`/api/story-plan/${planId}`, { method: "DELETE", body: { expected_revision: root.revision } }); await closeProductionInspector({ discard: true, flush: false }); await loadStoryPlan(); }
  catch (e) { showToast(e.message, "err"); }
}

async function restoreStoryPlanBranch(planId) {
  const root = storyPlanNodes.find(row => row.id === planId);
  if (!root) return;
  try {
    await api(`/api/story-plan/${planId}/restore-branch`, { body: { expected_revision: root.revision } });
    await closeProductionInspector({ discard: true, flush: false });
    storyPlanShowArchive = false;
    await loadStoryPlan();
    await inspectStoryPlan(planId);
  } catch (e) { showToast(e.message, "err"); }
}

async function moveStoryPlan(planId, direction) {
  const item = storyPlanNodes.find(row => row.id === planId);
  if (!item) return;
  if (productionSelected?.item?.id === planId && $("storyPlanTitle")) await closeProductionInspector();
  try {
    await api(`/api/story-plan/${planId}/move`, { body: { direction, expected_revision: item.revision } });
    await loadStoryPlan();
  } catch (e) { showToast(e.message, "err"); }
}

async function showStoryPlanVersions(planId) {
  try {
    const versions = await api(`/api/story-plan/${planId}/versions`, { method: "GET" });
    const item = storyPlanNodes.find(row => row.id === planId);
    openProductionInspector(item?.title || "规划版本", "历史版本", `<div class="production-inspector-form"><div class="story-plan-version-list">${versions.map(version => `<button onclick="restoreStoryPlanVersion(${planId},${version.revision})"><b>v${version.revision}</b><span>${esc(new Date(version.created_at * 1000).toLocaleString())}</span><small>${esc(version.snapshot?.summary || version.snapshot?.goal || "无摘要")}</small></button>`).join("") || '<div class="production-empty compact">暂无版本</div>'}</div><div class="production-inspector-actions"><button onclick="inspectStoryPlan(${planId})">返回编辑</button></div></div>`);
  } catch (e) { showToast(e.message, "err"); }
}

async function restoreStoryPlanVersion(planId, revision) {
  const item = storyPlanNodes.find(row => row.id === planId);
  if (!item) return;
  try { await api(`/api/story-plan/${planId}/restore`, { body: { revision, expected_revision: item.revision } }); await loadStoryPlan(); inspectStoryPlan(planId); showToast(`已恢复 v${revision} 并生成新版本`, "ok"); }
  catch (e) { showToast(e.message, "err"); }
}

function productionStateBrief(state) {
  if (!state || typeof state !== "object") return "暂无状态";
  return Object.entries(state).filter(([key, value]) => !key.startsWith("_") && value != null && String(value).trim())
    .slice(0, 4).map(([key, value]) => `${productionStateLabels[key] || key}：${value}`).join("；") || "暂无状态";
}

function productionPublicState(state) {
  return Object.fromEntries(Object.entries(state || {}).filter(([key]) => !key.startsWith("_")));
}

function productionRelatedCharacters(data) {
  const ids = new Set();
  (data.scenes || []).forEach(scene => (scene.refs?.character_ids || []).forEach(id => ids.add(+id)));
  const all = data.after?.characters || [];
  const related = all.filter(item => ids.has(item.id));
  return related.length ? related : all.slice(0, 8);
}

function productionRelatedCards(data) {
  const ids = new Set();
  (data.scenes || []).forEach(scene => {
    if (scene.location_card_id) ids.add(+scene.location_card_id);
    ["card_ids", "skill_ids", "item_ids", "location_ids", "rule_ids"].forEach(key =>
      (scene.refs?.[key] || []).forEach(id => ids.add(+id)));
  });
  const all = data.after?.cards || [];
  const related = all.filter(item => ids.has(item.id) || item.related);
  return related.length ? related : all.filter(item => item.category !== "rule").slice(0, 8);
}

function productionStateDiffs(data) {
  const rows = [];
  const knowledgeLabels = {
    _reader_known: "读者已知", _character_knowledge: "人物认知",
    _secrecy: "保密边界", _reader_state: "读者侧状态",
  };
  const collect = (kind, beforeItems, afterItems, stateOf) => {
    const beforeById = new Map((beforeItems || []).map(item => [item.id, item]));
    (afterItems || []).forEach(item => {
      const before = stateOf(beforeById.get(item.id)) || {};
      const after = stateOf(item) || {};
      const changes = [];
      new Set([...Object.keys(before), ...Object.keys(after)]).forEach(key => {
        const oldValue = before[key] == null ? "" : String(before[key]);
        const newValue = after[key] == null ? "" : String(after[key]);
        if (oldValue === newValue) return;
        changes.push({ label: knowledgeLabels[key] || productionStateLabels[key] || key,
          before: oldValue || "未记录", after: newValue || "已清除" });
      });
      if (changes.length) rows.push({ kind, id: item.id, name: item.name, changes });
    });
  };
  collect("character", data.before?.characters, data.after?.characters, item => item?.current_state);
  collect("card", data.before?.cards, data.after?.cards, item => item?.current_state?.state);
  return rows;
}

function productionDiffRows(data) {
  return productionStateDiffs(data).map(item => `
    <button class="production-node-row production-diff-row" onclick="${item.kind === "character" ? `inspectProductionCharacter(${item.id},'after')` : `inspectProductionCard(${item.id},'after')`}">
      <span>${svg(item.kind === "character" ? "users" : "refresh")}</span>
      <span class="production-node-row-copy"><b>${esc(item.name)}</b>${item.changes.slice(0, 4).map(change =>
        `<small><strong>${esc(change.label)}</strong>：${esc(change.before)} → ${esc(change.after)}</small>`).join("")}</span>
    </button>`).join("");
}

function productionNodeRow({ icon = "book", title, summary, onclick = "", extra = "" }) {
  return `<button class="production-node-row" ${onclick ? `onclick="${onclick}"` : ""}>
    <span>${svg(icon)}</span><span class="production-node-row-copy"><b>${esc(title || "未命名")}</b><small>${esc(summary || "暂无说明")}</small>${extra}</span>
  </button>`;
}

function productionDefaultLayout() {
  return { nodes: {
    rules: { x: 44, y: 34 }, before: { x: 44, y: 286 }, scenes: { x: 430, y: 118 },
    changes: { x: 920, y: 34 }, after: { x: 920, y: 326 },
  }};
}

function productionLayout() {
  const saved = productionChapterData?.layout?.layout;
  const base = productionDefaultLayout();
  if (!saved || typeof saved !== "object") return base;
  return { ...base, ...saved, nodes: { ...base.nodes, ...(saved.nodes || {}) } };
}

function productionNodeStyle(name, layout) {
  const pos = layout.nodes[name] || productionDefaultLayout().nodes[name];
  const x = Number.isFinite(+pos.x) ? Math.max(0, +pos.x) : 0;
  const y = Number.isFinite(+pos.y) ? Math.max(0, +pos.y) : 0;
  return `left:${x}px;top:${y}px`;
}

function renderProductionCanvas() {
  const data = productionChapterData;
  if (!data) return;
  const layout = productionLayout();
  const rules = (data.after?.cards || []).filter(item => item.category === "rule");
  const beforeCharacters = data.before?.characters || [];
  const beforeCards = data.before?.cards || [];
  const afterCharacters = productionRelatedCharacters(data);
  const afterCards = productionRelatedCards(data);
  const proposals = (data.proposals || []).filter(item => item.status === "pending" || item.is_stale);
  const impacts = data.impacts || [];
  const confirmedDiffRows = productionDiffRows(data);
  const beforeRows = [
    ...beforeCharacters.slice(0, 8).map(item => productionNodeRow({
      icon: "users", title: item.name, summary: productionStateBrief(item.current_state),
      onclick: `inspectProductionCharacter(${item.id},'before')`,
    })),
    ...beforeCards.filter(item => item.current_state).slice(0, 8).map(item => productionNodeRow({
      icon: productionCategoryIcons[item.category], title: item.name,
      summary: productionStateBrief(item.current_state?.state), onclick: `inspectProductionCard(${item.id},'before')`,
    })),
  ].join("");
  const afterRows = [
    ...afterCharacters.map(item => productionNodeRow({
      icon: "users", title: item.name, summary: productionStateBrief(item.current_state),
      onclick: `inspectProductionCharacter(${item.id},'after')`,
    })),
    ...afterCards.map(item => productionNodeRow({
      icon: productionCategoryIcons[item.category], title: item.name,
      summary: productionStateBrief(item.current_state?.state || item.attributes), onclick: `inspectProductionCard(${item.id},'after')`,
    })),
  ].join("");
  const sceneRows = (data.scenes || []).map((scene, index) => {
    const tags = [scene.time_label, scene.location_name, scene.goal && `目标：${scene.goal}`].filter(Boolean);
    return `<button class="production-node-row production-scene-row" onclick="inspectProductionScene(${scene.id})">
      <span class="production-scene-order">${index + 1}</span>
      <span class="production-node-row-copy"><b>${esc(scene.title)}</b><small>${esc(scene.summary || scene.conflict || "暂无场景摘要")}</small>
        ${tags.length ? `<span class="production-scene-tags">${tags.slice(0, 3).map(tag => `<span>${esc(tag)}</span>`).join("")}</span>` : ""}
      </span>
    </button>`;
  }).join("");
  const proposalRows = proposals.map(item => `<div class="production-node-row production-proposal-row ${item.is_stale ? "stale" : ""}">
    <span>${svg(item.is_stale ? "alert" : "sparkles")}</span>
    <span class="production-node-row-copy"><b>${esc(item.name || item.change_summary || "设定变化")}</b><small>${esc(item.change_summary || "等待作者确认")}</small></span>
    <span class="production-proposal-actions">
      ${item.is_stale ? "" : `<button class="ic" onclick="acceptProductionProposal(${item.id})" title="确认">${svg("check")}</button>`}
      <button class="ic" onclick="rejectProductionProposal(${item.id})" title="忽略">${svg("x")}</button>
    </span>
  </div>`).join("");
  const impactRows = impacts.map(item => `<div class="production-impact"><b>前序第${item.source_chapter_ord}章已修改</b><span>${esc(item.summary)}</span><button onclick="resolveProductionImpact(${item.id})">已检查</button></div>`).join("");
  const ruleRows = rules.map(item => productionNodeRow({
    icon: "book", title: item.name, summary: item.summary || productionStateBrief(item.current_state?.state),
    onclick: `inspectProductionCard(${item.id})`,
  })).join("");
  $("productionCanvas").innerHTML = `
    <div class="production-canvas-toolbar">
      <button class="ic" onclick="setProductionZoom(-0.1)" title="缩小">−</button>
      <button class="ic" onclick="setProductionZoom(0.1)" title="放大">＋</button>
      <button class="ic" data-ic="focus" onclick="resetProductionView()" title="复位视图"></button>
    </div>
    <svg class="production-links" aria-hidden="true">
      <path data-from="rules" data-to="scenes"></path><path data-from="before" data-to="scenes"></path>
      <path data-from="scenes" data-to="after"></path><path data-from="changes" data-to="after"></path>
    </svg>
    <section class="production-node rules" data-node="rules" style="${productionNodeStyle("rules", layout)}">
      <div class="production-node-head"><span class="production-node-title"><span>${svg("book")}</span><div><b>规则约束</b><small>当前章节生效</small></div></span><button class="ic" onclick="event.stopPropagation();newProductionCard('rule')" title="新增规则">${svg("plus")}</button></div>
      <div class="production-node-body">${ruleRows || `<div class="production-empty"><span>还没有正式规则卡</span></div>`}</div>
    </section>
    <section class="production-node before" data-node="before" style="${productionNodeStyle("before", layout)}">
      <div class="production-node-head"><span class="production-node-title"><span>${svg("clock")}</span><div><b>章前状态</b><small>进入本章之前</small></div></span></div>
      <div class="production-node-body production-state-grid">${beforeRows || `<div class="production-empty"><span>暂无章前状态</span></div>`}</div>
    </section>
    <section class="production-node scenes" data-node="scenes" style="${productionNodeStyle("scenes", layout)}">
      <div class="production-node-head"><span class="production-node-title"><span>${svg("nodes")}</span><div><b>本章场景</b><small>${data.scenes?.length || 0} 个场景单元</small></div></span><button class="ic" onclick="event.stopPropagation();newProductionScene()" title="新增场景">${svg("plus")}</button></div>
      <div class="production-node-body">${sceneRows || `<div class="production-empty">${svg("nodes")}<span>可手动新增，或点“分析本章”自动整理</span></div>`}<button class="production-add-row" onclick="newProductionScene()">${svg("plus")} 新增场景</button></div>
    </section>
    <section class="production-node changes" data-node="changes" style="${productionNodeStyle("changes", layout)}">
      <div class="production-node-head"><span class="production-node-title"><span>${svg("sparkles")}</span><div><b>本章变化</b><small>${productionStateDiffs(data).length} 项已记录 · ${proposals.length} 项待确认</small></div></span></div>
      <div class="production-node-body">${confirmedDiffRows}${impactRows}${proposalRows || (!confirmedDiffRows && !impactRows ? `<div class="production-empty"><span>本章暂无状态变化</span></div>` : "")}</div>
    </section>
    <section class="production-node after" data-node="after" style="${productionNodeStyle("after", layout)}">
      <div class="production-node-head"><span class="production-node-title"><span>${svg("check")}</span><div><b>章后状态</b><small>截至本章结束</small></div></span></div>
      <div class="production-node-body production-state-grid">${afterRows || `<div class="production-empty"><span>暂无章后状态</span></div>`}</div>
    </section>`;
  $("productionCanvas").style.zoom = productionZoom;
  applyIcons();
  initProductionNodeDrag();
  requestAnimationFrame(updateProductionLinks);
}

function updateProductionLinks() {
  const canvas = $("productionCanvas");
  if (!canvas || window.innerWidth <= 700) return;
  canvas.querySelectorAll(".production-links path").forEach(path => {
    const from = canvas.querySelector(`[data-node="${path.dataset.from}"]`);
    const to = canvas.querySelector(`[data-node="${path.dataset.to}"]`);
    if (!from || !to) return;
    const x1 = from.offsetLeft + from.offsetWidth, y1 = from.offsetTop + from.offsetHeight / 2;
    const x2 = to.offsetLeft, y2 = to.offsetTop + to.offsetHeight / 2;
    const bend = Math.max(50, Math.abs(x2 - x1) * .45);
    path.setAttribute("d", `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`);
  });
}

function initProductionNodeDrag() {
  if (window.innerWidth <= 700) return;
  $("productionCanvas").querySelectorAll(".production-node-head").forEach(head => {
    head.addEventListener("pointerdown", event => {
      if (event.target.closest("button")) return;
      const node = head.closest(".production-node");
      const startX = event.clientX, startY = event.clientY;
      const left = node.offsetLeft, top = node.offsetTop;
      head.setPointerCapture(event.pointerId);
      const move = moveEvent => {
        node.style.left = Math.max(0, left + (moveEvent.clientX - startX) / productionZoom) + "px";
        node.style.top = Math.max(0, top + (moveEvent.clientY - startY) / productionZoom) + "px";
        updateProductionLinks();
      };
      const up = () => {
        head.removeEventListener("pointermove", move);
        head.removeEventListener("pointerup", up);
        head.removeEventListener("pointercancel", up);
        const layout = productionLayout();
        layout.nodes[node.dataset.node] = { x: Math.round(node.offsetLeft), y: Math.round(node.offsetTop) };
        productionChapterData.layout = { ...(productionChapterData.layout || {}), layout };
        queueProductionLayoutSave();
      };
      head.addEventListener("pointermove", move);
      head.addEventListener("pointerup", up);
      head.addEventListener("pointercancel", up);
    });
  });
}

function queueProductionLayoutSave() {
  clearTimeout(productionLayoutTimer);
  const workId = currentWorkId, chapterId = productionChapterId, layout = productionLayout();
  const key = `productionLayoutDraft:${currentUsername || "user"}:${workId}:${chapterId}`;
  localStorage.setItem(key, JSON.stringify({ layout, saved_at: Date.now() }));
  $("productionSaveStatus").textContent = "布局已本机暂存";
  const draftSnapshot = localStorage.getItem(key);
  productionLayoutPending = { workId, chapterId, layout, key, draftSnapshot };
  productionLayoutTimer = setTimeout(flushProductionLayoutSave, 500);
}

async function flushProductionLayoutSave() {
  clearTimeout(productionLayoutTimer);
  productionLayoutTimer = null;
  const pending = productionLayoutPending;
  productionLayoutPending = null;
  if (pending) {
    const { workId, chapterId, layout, key, draftSnapshot } = pending;
    await productionLayoutSaveQueue.run(async () => {
      try {
        const saved = await api(`/api/works/${workId}/production/layout`, { method: "PUT", body: {
          chapter_id: chapterId, layout,
        }});
        if (productionChapterData && productionChapterId === chapterId) productionChapterData.layout = saved;
        if (localStorage.getItem(key) === draftSnapshot) localStorage.removeItem(key);
        if (productionChapterId === chapterId) $("productionSaveStatus").textContent = "布局已同步";
      } catch (e) {
        if (productionChapterId === chapterId) $("productionSaveStatus").textContent = "布局同步失败，草稿仍在本机";
      }
    });
  }
  await productionLayoutSaveQueue.wait();
}

function setProductionZoom(delta) {
  productionZoom = Math.max(.65, Math.min(1.25, +(productionZoom + delta).toFixed(2)));
  $("productionCanvas").style.zoom = productionZoom;
}

function resetProductionView() {
  productionZoom = window.innerWidth < 1200 ? .8 : 1;
  $("productionCanvasViewport").scrollTo({ left: 0, top: 0, behavior: "smooth" });
  $("productionCanvas").style.zoom = productionZoom;
}

function productionPairsText(value) {
  if (!value || typeof value !== "object") return "";
  return Object.entries(value).filter(([, item]) => item != null && String(item).trim())
    .map(([key, item]) => `${key}：${typeof item === "object" ? JSON.stringify(item) : item}`).join("\n");
}

function productionCustomFields() {
  const settings = productionChapterData?.settings || productionOverviewData?.settings || {};
  return Array.isArray(settings.custom_fields) ? settings.custom_fields : [];
}
function productionCustomFieldsForm(attributes = {}) {
  const fields = productionCustomFields().filter(field => field.type !== "hidden");
  if (!fields.length) return "";
  return `<div class="production-inspector-section"><h3>作品自定义字段</h3><div class="production-form-grid">${fields.map(field => {
    const id = `productionCustom_${String(field.id || field.name).replace(/[^a-zA-Z0-9_-]/g, "_")}`;
    const value = attributes?.[field.name];
    const data = `data-production-custom="${esc(field.name)}" data-custom-type="${esc(field.type)}"`;
    if (field.type === "boolean") return `<label>${esc(field.name)}<input id="${id}" ${data} type="checkbox" ${value ? "checked" : ""}></label>`;
    if (field.type === "enum" || field.type === "level") {
      const options = (field.options || []).map(option => `<option value="${esc(option)}" ${String(value ?? "") === String(option) ? "selected" : ""}>${esc(option)}</option>`).join("");
      return `<label>${esc(field.name)}<select id="${id}" ${data}><option value="">未设置</option>${options}</select></label>`;
    }
    return `<label>${esc(field.name)}<input id="${id}" ${data} type="${field.type === "number" ? "number" : "text"}" value="${esc(value == null ? "" : String(value))}"></label>`;
  }).join("")}</div></div>`;
}
function productionOtherAttributes(attributes = {}) {
  const configured = new Set(productionCustomFields().map(field => field.name));
  return Object.fromEntries(Object.entries(attributes || {}).filter(([key]) => !configured.has(key)));
}
function readProductionCustomAttributes(base = {}) {
  const attributes = { ...base };
  document.querySelectorAll("[data-production-custom]").forEach(input => {
    const name = input.dataset.productionCustom;
    const type = input.dataset.customType;
    let value = type === "boolean" ? input.checked : input.value;
    if (type === "number" && value !== "") value = Number(value);
    if (value === "" || value == null || (type === "number" && !Number.isFinite(value))) delete attributes[name];
    else attributes[name] = value;
  });
  return attributes;
}

function parseProductionPairs(value) {
  const result = {};
  String(value || "").split(/\r?\n/).forEach(line => {
    const index = line.search(/[：:]/);
    if (index < 1) return;
    const key = line.slice(0, index).trim();
    let item = line.slice(index + 1).trim();
    if (!key || !item) return;
    if (/^-?\d+(\.\d+)?$/.test(item)) item = +item;
    else if (item === "true" || item === "是") item = true;
    else if (item === "false" || item === "否") item = false;
    result[key] = item;
  });
  return result;
}

function productionCardById(cardId, point = "after") {
  const timed = productionChapterData?.[point]?.cards || [];
  return timed.find(item => item.id === cardId) || productionCards.find(item => item.id === cardId) || null;
}

function setProductionAgentTarget(type, item) {
  if (!item) return;
  let text = "";
  if (type === "card") {
    text = `[生产画布设定卡]\n类型：${productionCategoryLabels[item.category] || item.category}\n名称：${item.name}\n摘要：${item.summary || ""}\n详情：${item.detail || ""}\n当前状态：${productionStateBrief(item.current_state?.state)}`;
  } else if (type === "scene") {
    text = `[生产画布场景]\n名称：${item.title}\n摘要：${item.summary || ""}\n目标：${item.goal || ""}\n冲突：${item.conflict || ""}\n结果：${item.outcome || ""}`;
  } else if (type === "character") {
    text = `[生产画布人物]\n名称：${item.name}\n基础设定：${item.summary || ""}\n当前状态：${productionStateBrief(item.current_state)}`;
  }
  agentSelection = { context_kind: "canvas_target", target_type: type, target_id: item.id,
    work_id: currentWorkId, chapter_id: type === "scene" ? productionChapterId : null,
    text, title: item.name || item.title || "画布对象" };
  renderAgentSelection();
}

async function refreshCanvasAgentTarget(selection) {
  if (selection.work_id !== currentWorkId) {
    clearProductionAgentTarget();
    throw new Error("AI 目标不属于当前作品，请重新选择");
  }
  if (selection.target_type === "story_plan") {
    const item = await api(`/api/story-plan/${selection.target_id}`, { method: "GET" });
    if (item.work_id !== currentWorkId) throw new Error("规划目标已失效，请重新选择");
    setStoryPlanAgentTarget(item);
  } else if (selection.target_type === "card") {
    const rows = await api(`/api/works/${currentWorkId}/production/cards`, { method: "GET" });
    const item = rows.find(row => row.id === selection.target_id);
    if (!item) throw new Error("设定目标已归档，请重新选择");
    setProductionAgentTarget("card", item);
  } else if (selection.target_type === "scene") {
    const data = await api(`/api/chapters/${selection.chapter_id}/production`, { method: "GET" });
    const item = (data.scenes || []).find(row => row.id === selection.target_id);
    if (!item) throw new Error("场景目标已失效，请重新选择");
    setProductionAgentTarget("scene", item);
  } else if (selection.target_type === "character") {
    const rows = await api(`/api/works/${currentWorkId}/entities`, { method: "GET" });
    const item = rows.find(row => row.id === selection.target_id);
    if (!item) throw new Error("人物目标已失效，请重新选择");
    setProductionAgentTarget("character", item);
  }
  if (agentSelection.target_type === "scene") agentSelection.chapter_id = selection.chapter_id;
  return agentSelection;
}

async function handoffProductionObjectToAI(type, id) {
  if (["card", "scene"].includes(type) && productionSelected?.type === type && productionSelected.item?.id === id) {
    clearTimeout(productionInspectorSaveTimer);
    productionInspectorSaveTimer = null;
    const saved = type === "card" ? await saveProductionCardFromInspector(true) : await saveProductionSceneFromInspector(true);
    if (!saved) { showToast("内容尚未同步，暂不能交给 AI", "err"); return; }
  }
  const item = type === "card" ? productionCardById(id) : type === "scene"
    ? productionChapterData?.scenes?.find(row => row.id === id)
    : entitiesCache.find(row => row.id === id);
  if (!item) { showToast("目标已不存在", "err"); return; }
  setProductionAgentTarget(type, item);
  openProductionAI();
}

function clearProductionAgentTarget() {
  if (agentSelection?.context_kind === "canvas_target") clearAgentSelection();
}

function openProductionAI() {
  if (!$("app").classList.contains("ai-open")) toggleAISide();
  adjustProductionInspectorLayout();
  $("agentInput").focus();
}

function adjustProductionInspectorLayout() {
  const workspace = $("productionWorkspace");
  if (!workspace?.classList.contains("inspector-open") || window.innerWidth < 1100) return;
  const available = workspace.getBoundingClientRect().width;
  if (available - 286 - 340 < 480 && !workspace.classList.contains("resources-closed")) {
    productionAutoClosedResources = true;
    workspace.classList.add("resources-closed");
  }
}

window.addEventListener("resize", () => {
  if (!$("app")?.classList.contains("production-open")) return;
  if (window.innerWidth < 1100) $("productionWorkspace")?.classList.add("resources-closed");
  else adjustProductionInspectorLayout();
});

const productionStateFieldIds = new Set([
  "productionCardState", "productionStateReaderKnown", "productionStateCharacterKnowledge",
  "productionStateSecrecy", "productionStateReaderState", "productionCardStateSummary",
]);
function productionDraftKey(selection = productionSelected, kind = "base") {
  if (!selection || !currentWorkId) return "";
  const item = selection.item || {};
  const identity = item.id || `new-${item.category || "item"}`;
  return `productionDraft:${currentUsername || "user"}:${currentWorkId}:${productionChapterId || 0}:${selection.type}:${selection.point || "after"}:${identity}:${kind}`;
}

function stashProductionInspectorDraft(kind = "all") {
  const form = $("productionInspectorBody")?.querySelector(".production-inspector-form");
  if (!form || !["card", "scene"].includes(productionSelected?.type)) return;
  const kinds = kind === "all" ? ["base", "state"] : [kind];
  kinds.forEach(draftKind => {
    const key = productionDraftKey(productionSelected, draftKind);
    if (!key) return;
    const values = {};
    form.querySelectorAll("input[id],textarea[id],select[id]").forEach(input => {
      const isState = productionStateFieldIds.has(input.id);
      if ((draftKind === "state") !== isState) return;
      values[input.id] = input.type === "checkbox" ? input.checked : input.value;
    });
    if (Object.keys(values).length) localStorage.setItem(key, JSON.stringify({ values, saved_at: Date.now() }));
  });
}

function restoreProductionInspectorDraft() {
  let restored = false;
  ["base", "state"].forEach(kind => {
    const key = productionDraftKey(productionSelected, kind);
    if (!key) return;
    let draft = null;
    try { draft = JSON.parse(localStorage.getItem(key) || "null"); } catch (e) { draft = null; }
    if (!draft?.values) return;
    Object.entries(draft.values).forEach(([id, value]) => {
      const input = $(id);
      if (!input) return;
      if (input.type === "checkbox") input.checked = !!value;
      else input.value = value == null ? "" : value;
    });
    restored = true;
  });
  if (restored) $("productionSaveStatus").textContent = "已恢复本机草稿";
}

function clearProductionInspectorDraft(key = productionDraftKey(productionSelected, "base")) {
  if (key) localStorage.removeItem(key);
}

function markProductionInspectorDraft() {
  stashProductionInspectorDraft("state");
  if ($("productionSaveStatus")) $("productionSaveStatus").textContent = "已本机暂存";
}

window.addEventListener("pagehide", () => { stashProductionInspectorDraft(); stashStoryPlanDraft(); });
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") { stashProductionInspectorDraft(); stashStoryPlanDraft(); }
});

async function closeProductionInspector(options = {}) {
  const { flush = true, discard = false } = options;
  const selection = productionSelected;
  const shouldFlush = !!productionInspectorSaveTimer && !!productionSelected?.item?.id;
  const shouldFlushPlan = !!storyPlanSaveTimer && productionSelected?.type === "story_plan" && !!productionSelected?.item?.id;
  if (!discard) stashProductionInspectorDraft("all");
  if (!discard) stashStoryPlanDraft();
  clearTimeout(productionInspectorSaveTimer);
  clearTimeout(storyPlanSaveTimer);
  productionInspectorSaveTimer = null;
  storyPlanSaveTimer = null;
  await productionSaveQueue.wait();
  if (flush && shouldFlush && productionSelected === selection) {
    if (productionSelected?.type === "card") await saveProductionCardFromInspector(true);
    else if (productionSelected?.type === "scene") await saveProductionSceneFromInspector(true);
  }
  if (flush && shouldFlushPlan && productionSelected === selection) {
    const saved = await saveStoryPlanFromInspector(true);
    if (!saved) { showToast("规划尚未同步，草稿保留在本机", "err"); return false; }
  }
  if (discard) {
    clearProductionInspectorDraft(productionDraftKey(selection, "base"));
    clearProductionInspectorDraft(productionDraftKey(selection, "state"));
    if (selection?.type === "story_plan") localStorage.removeItem(storyPlanDraftKey(selection));
  }
  $("productionWorkspace")?.classList.remove("inspector-open");
  if (productionAutoClosedResources && window.innerWidth >= 1100) {
    $("productionWorkspace")?.classList.remove("resources-closed");
    productionAutoClosedResources = false;
  }
  productionSelected = null;
  if (productionSection === "plan") renderStoryPlan(); else renderProductionResources();
  return true;
}

function openProductionInspector(title, meta, html) {
  $("productionInspectorTitle").textContent = title;
  $("productionInspectorMeta").textContent = meta || "";
  $("productionInspectorBody").innerHTML = html;
  $("productionInspectorBody").scrollTop = 0;
  $("productionWorkspace").classList.add("inspector-open");
  adjustProductionInspectorLayout();
  if (productionSelected?.type === "story_plan") restoreStoryPlanDraft(); else restoreProductionInspectorDraft();
  applyIcons();
}

function productionChapterSelectOptions(value) {
  return `<option value="">不限</option>` + chapters.map(item =>
    `<option value="${item.id}" ${item.id === +value ? "selected" : ""}>第${item.ord}章《${esc(item.title || "未命名")}》</option>`).join("");
}

function productionSceneSelectOptions(value) {
  return `<option value="">未限定场景</option>` + (productionChapterData?.scenes || []).map(scene =>
    `<option value="${scene.id}" ${scene.id === +value ? "selected" : ""}>${scene.ord}. ${esc(scene.title || "未命名场景")}</option>`).join("");
}

function inspectProductionCard(cardId, point = "after") {
  const item = productionCardById(cardId, point);
  if (!item) return;
  stashProductionInspectorDraft();
  clearTimeout(productionInspectorSaveTimer);
  productionSelected = { type: "card", item, point };
  renderProductionResources();
  const truth = item.truth || {};
  const state = item.current_state?.state || {};
  const publicState = productionPublicState(state);
  const evidence = item.current_state?.evidence || "";
  const stateSection = point === "before" ? `
      <div class="production-inspector-section"><h3>章前状态快照</h3>
        <p class="production-inspector-note">这里展示进入本章前已经成立的状态，仅供核对，不能写回本章。</p>
        <label>当前状态<textarea readonly>${esc(productionPairsText(publicState) || "暂无")}</textarea></label>
        <label>读者已知<textarea readonly>${esc(state._reader_known ?? truth.reader_known ?? "暂无")}</textarea></label>
        <label>人物认知<textarea readonly>${esc(state._character_knowledge ?? truth.character_knowledge ?? "暂无")}</textarea></label>
        <label>保密边界<textarea readonly>${esc(state._secrecy ?? truth.secrecy ?? "暂无")}</textarea></label>
        <label>读者侧状态<textarea readonly>${esc(state._reader_state ?? item.reader_state ?? "暂无")}</textarea></label>
        ${evidence ? `<div class="production-evidence">证据：${esc(evidence)}${Number.isInteger(item.current_state?.evidence_start) ? `<button onclick="jumpToProductionEvidence(${item.current_state.evidence_start},${item.current_state.evidence_end})">查看正文</button>` : ""}</div>` : ""}
      </div>` : `
      <div class="production-inspector-section"><h3>本章状态版本</h3>
        <label>当前状态 <span>每行“字段：值”</span><textarea id="productionCardState" oninput="markProductionInspectorDraft();event.stopPropagation()">${esc(productionPairsText(publicState))}</textarea></label>
        <label>截至本章读者已知<textarea id="productionStateReaderKnown" oninput="markProductionInspectorDraft();event.stopPropagation()">${esc(state._reader_known ?? truth.reader_known ?? "")}</textarea></label>
        <label>截至本章人物认知<textarea id="productionStateCharacterKnowledge" oninput="markProductionInspectorDraft();event.stopPropagation()">${esc(state._character_knowledge ?? truth.character_knowledge ?? "")}</textarea></label>
        <label>截至本章保密边界<textarea id="productionStateSecrecy" oninput="markProductionInspectorDraft();event.stopPropagation()">${esc(state._secrecy ?? truth.secrecy ?? "")}</textarea></label>
        <label>截至本章读者侧状态<textarea id="productionStateReaderState" oninput="markProductionInspectorDraft();event.stopPropagation()">${esc(state._reader_state ?? item.reader_state ?? "")}</textarea></label>
        <label>变化说明<input id="productionCardStateSummary" oninput="markProductionInspectorDraft();event.stopPropagation()" placeholder="例如：道具转由林晚持有"></label>
        <button onclick="saveProductionCardStateFromInspector()">保存为本章状态</button>
        ${evidence ? `<div class="production-evidence">证据：${esc(evidence)}${Number.isInteger(item.current_state?.evidence_start) ? `<button onclick="jumpToProductionEvidence(${item.current_state.evidence_start},${item.current_state.evidence_end})">查看正文</button>` : ""}</div>` : ""}
      </div>`;
  openProductionInspector(item.name, `${productionCategoryLabels[item.category] || item.category} · ${point === "before" ? "章前" : "章后"}`, `
    <div class="production-inspector-form" oninput="queueProductionInspectorSave()">
      <div class="production-form-grid">
        <label>分类<select id="productionCardCategory">${Object.entries(productionCategoryLabels).filter(([key]) => key !== "character").map(([key, label]) => `<option value="${key}" ${item.category === key ? "selected" : ""}>${label}</option>`).join("")}</select></label>
        <label>生效范围<select id="productionCardScope"><option value="global" ${item.scope_type === "global" ? "selected" : ""}>全书</option><option value="chapter_range" ${item.scope_type === "chapter_range" ? "selected" : ""}>章节范围</option><option value="scene" ${item.scope_type === "scene" ? "selected" : ""}>本章场景</option></select></label>
      </div>
      <label>名称<input id="productionCardName" value="${esc(item.name)}"></label>
      <label>一句话设定<textarea id="productionCardSummary" rows="3">${esc(item.summary || "")}</textarea></label>
      <label>详细说明<textarea id="productionCardDetail" rows="5">${esc(item.detail || "")}</textarea></label>
      ${productionCustomFieldsForm(item.attributes)}
      <label>其他属性 <span>每行“字段：值”</span><textarea id="productionCardAttributes">${esc(productionPairsText(productionOtherAttributes(item.attributes)))}</textarea></label>
      <div class="production-form-grid">
        <label>起始章节<select id="productionCardScopeStart">${productionChapterSelectOptions(item.scope_start_chapter_id)}</select></label>
        <label>结束章节<select id="productionCardScopeEnd">${productionChapterSelectOptions(item.scope_end_chapter_id)}</select></label>
      </div>
      <label>限定场景<select id="productionCardScopeScene">${productionSceneSelectOptions(item.scope_scene_id)}</select></label>
      <div class="production-inspector-section"><h3>固定真相与初始边界</h3>
        <label>客观真相<textarea id="productionTruthObjective">${esc(truth.objective || "")}</textarea></label>
        <label>初始读者已知<textarea id="productionTruthReader">${esc(truth.reader_known || "")}</textarea></label>
        <label>初始人物认知<textarea id="productionTruthCharacters">${esc(truth.character_knowledge || "")}</textarea></label>
        <label>初始保密边界<textarea id="productionTruthSecrecy">${esc(truth.secrecy || "")}</textarea></label>
      </div>
      <label>初始读者侧状态<textarea id="productionCardReaderState">${esc(item.reader_state || "")}</textarea></label>
      ${stateSection}
      <div class="production-inspector-actions"><button onclick="saveProductionCardFromInspector()">保存设定卡</button><button onclick="handoffProductionObjectToAI('card',${item.id})">交给 AI</button><button class="danger-link" onclick="archiveProductionCard(${item.id})">归档</button></div>
    </div>`);
}

function newProductionCard(category = "rule") {
  stashProductionInspectorDraft();
  clearTimeout(productionInspectorSaveTimer);
  const item = { id: null, category, name: "", summary: "", detail: "", attributes: {}, truth: {}, reader_state: "", scope_type: "global" };
  productionSelected = { type: "card", item, point: "after" };
  openProductionInspector("新建设定卡", productionCategoryLabels[category] || "设定", `
    <div class="production-inspector-form" oninput="queueProductionInspectorSave()">
      <div class="production-form-grid">
        <label>分类<select id="productionCardCategory">${Object.entries(productionCategoryLabels).filter(([key]) => key !== "character").map(([key, label]) => `<option value="${key}" ${category === key ? "selected" : ""}>${label}</option>`).join("")}</select></label>
        <label>生效范围<select id="productionCardScope"><option value="global">全书</option><option value="chapter_range">章节范围</option><option value="scene">本章场景</option></select></label>
      </div>
      <label>名称<input id="productionCardName" placeholder="例如：灵力等级"></label>
      <label>一句话设定<textarea id="productionCardSummary" rows="3"></textarea></label>
      <label>详细说明<textarea id="productionCardDetail" rows="5"></textarea></label>
      ${productionCustomFieldsForm({})}
      <label>其他属性 <span>每行“字段：值”</span><textarea id="productionCardAttributes"></textarea></label>
      <div class="production-form-grid"><label>起始章节<select id="productionCardScopeStart">${productionChapterSelectOptions()}</select></label><label>结束章节<select id="productionCardScopeEnd">${productionChapterSelectOptions()}</select></label></div>
      <label>限定场景<select id="productionCardScopeScene">${productionSceneSelectOptions()}</select></label>
      <div class="production-inspector-section"><h3>真相与信息边界</h3>
        <label>客观真相<textarea id="productionTruthObjective"></textarea></label><label>读者当前已知<textarea id="productionTruthReader"></textarea></label>
        <label>人物认知与误解<textarea id="productionTruthCharacters"></textarea></label><label>保密边界<textarea id="productionTruthSecrecy"></textarea></label>
      </div>
      <label>读者侧状态<textarea id="productionCardReaderState"></textarea></label>
      <div class="production-inspector-actions"><button onclick="saveProductionCardFromInspector()">创建设定卡</button><button onclick="closeProductionInspector()">取消</button></div>
    </div>`);
  setTimeout(() => $("productionCardName")?.focus(), 30);
}

function readProductionCardForm() {
  const body = {
    category: $("productionCardCategory").value, name: $("productionCardName").value.trim(),
    summary: $("productionCardSummary").value, detail: $("productionCardDetail").value,
    attributes: readProductionCustomAttributes(parseProductionPairs($("productionCardAttributes").value)),
    truth: { objective: $("productionTruthObjective").value, reader_known: $("productionTruthReader").value,
      character_knowledge: $("productionTruthCharacters").value, secrecy: $("productionTruthSecrecy").value },
    reader_state: $("productionCardReaderState").value, scope_type: $("productionCardScope").value,
    scope_start_chapter_id: +$("productionCardScopeStart").value || null,
    scope_end_chapter_id: +$("productionCardScopeEnd").value || null,
    scope_scene_id: +$("productionCardScopeScene").value || null,
  };
  if (!productionSelected?.item?.id && productionChapterId) body.source_chapter_id = productionChapterId;
  return body;
}

function queueProductionInspectorSave() {
  if (!["card", "scene"].includes(productionSelected?.type)) return;
  stashProductionInspectorDraft("base");
  clearTimeout(productionInspectorSaveTimer);
  $("productionSaveStatus").textContent = "已本机暂存";
  if (!productionSelected?.item?.id) return;
  productionInspectorSaveTimer = setTimeout(() => {
    productionInspectorSaveTimer = null;
    $("productionSaveStatus").textContent = "正在同步";
    if (productionSelected?.type === "card") saveProductionCardFromInspector(true);
    else if (productionSelected?.type === "scene") saveProductionSceneFromInspector(true);
  }, 1000);
}

async function saveProductionCardFromInspector(quiet = false) {
  if (productionSelected?.type !== "card") return;
  const selection = productionSelected;
  const cardId = selection.item.id;
  const workId = currentWorkId;
  const draftKey = productionDraftKey(selection, "base");
  const draftSnapshot = localStorage.getItem(draftKey);
  const body = readProductionCardForm();
  if (!body.name) { if (!quiet) showToast("请填写设定卡名称", "err"); return; }
  return productionSaveQueue.run(async () => {
    try {
      productionInspectorSaving = true;
      const saved = await api(cardId ? `/api/production/cards/${cardId}` : `/api/works/${workId}/production/cards`, {
        method: cardId ? "PUT" : "POST", body,
      });
      if (localStorage.getItem(draftKey) === draftSnapshot) clearProductionInspectorDraft(draftKey);
      if (productionSelected === selection) {
        $("productionSaveStatus").textContent = "已同步";
        productionSelected.item = saved;
      }
      const index = productionCards.findIndex(item => item.id === saved.id);
      if (index >= 0) productionCards[index] = { ...productionCards[index], ...saved };
      else productionCards.push(saved);
      if (productionChapterData?.after?.cards) {
        const at = productionChapterData.after.cards.findIndex(item => item.id === saved.id);
        if (at >= 0) productionChapterData.after.cards[at] = { ...productionChapterData.after.cards[at], ...saved };
        else productionChapterData.after.cards.push(saved);
      }
      renderProductionResources();
      if (productionMode === "chapter") renderProductionCanvas();
      if (!quiet && productionSelected === selection) { showToast(cardId ? "设定卡已保存" : "设定卡已创建", "ok"); inspectProductionCard(saved.id); }
      return saved;
    } catch (e) {
      if ($("productionSaveStatus")) $("productionSaveStatus").textContent = "同步失败，草稿仍在本机";
      if (!quiet) showToast(e.message, "err");
      return null;
    } finally { productionInspectorSaving = false; }
  });
}

async function saveProductionCardStateFromInspector() {
  const item = productionSelected?.item;
  if (!item?.id || !productionChapterId || productionSelected?.point === "before") return;
  const state = parseProductionPairs($("productionCardState").value);
  state._reader_known = $("productionStateReaderKnown").value;
  state._character_knowledge = $("productionStateCharacterKnowledge").value;
  state._secrecy = $("productionStateSecrecy").value;
  state._reader_state = $("productionStateReaderState").value;
  Object.keys(state).forEach(key => { if (!String(state[key] ?? "").trim()) delete state[key]; });
  if (!Object.keys(state).length) { showToast("请至少填写一项状态", "err"); return; }
  const draftKey = productionDraftKey(productionSelected, "state");
  const draftSnapshot = localStorage.getItem(draftKey);
  try {
    await productionSaveQueue.run(() => api(`/api/production/cards/${item.id}/versions`, { body: {
      chapter_id: productionChapterId, state, change_summary: $("productionCardStateSummary").value,
    }}));
    if (localStorage.getItem(draftKey) === draftSnapshot) clearProductionInspectorDraft(draftKey);
    showToast("本章状态已保存", "ok");
    await loadProductionChapter();
    inspectProductionCard(item.id);
  } catch (e) { showToast(e.message, "err"); }
}

async function archiveProductionCard(cardId) {
  const ok = await askCard({ title: "归档设定卡", msg: "归档后不会再进入后续写作上下文，历史状态仍保留。", okText: "归档", danger: true });
  if (!ok) return;
  const selection = productionSelected;
  const draftKeys = [productionDraftKey(selection, "base"), productionDraftKey(selection, "state")];
  try {
    await closeProductionInspector({ flush: false });
    await api(`/api/production/cards/${cardId}`, { method: "DELETE" });
    draftKeys.forEach(clearProductionInspectorDraft);
    await (productionMode === "chapter" ? loadProductionChapter() : loadProductionOverview());
    showToast("设定卡已归档", "ok");
  } catch (e) { showToast(e.message, "err"); }
}

function inspectProductionCharacter(entityId, point = "after") {
  const item = (productionChapterData?.[point]?.characters || entitiesCache).find(entity => entity.id === entityId);
  if (!item) return;
  stashProductionInspectorDraft();
  clearTimeout(productionInspectorSaveTimer);
  productionSelected = { type: "character", item, point };
  renderProductionResources();
  const state = item.current_state || {};
  openProductionInspector(item.name, `人物 · ${point === "before" ? "章前" : "章后"}`, `
    <div class="production-inspector-form">
      <label>基础设定<textarea readonly>${esc(item.summary || "暂无")}</textarea></label>
      <label>详细档案<textarea readonly rows="6">${esc(item.detail || "暂无")}</textarea></label>
      <div class="production-inspector-section"><h3>当前状态</h3>${Object.entries(productionStateLabels).map(([key, label]) => state[key] ? `<div class="production-evidence"><b>${label}</b><br>${esc(state[key])}</div>` : "").join("") || '<div class="production-empty"><span>暂无动态状态</span></div>'}</div>
      <div class="production-inspector-actions"><button onclick="openCharacterState(${item.id},'profile')">打开人物卡</button><button onclick="handoffProductionObjectToAI('character',${item.id})">交给 AI</button></div>
    </div>`);
}

function productionLocationOptions(value) {
  const locations = productionCards.filter(item => item.category === "location");
  return `<option value="">未关联地点卡</option>` + locations.map(item => `<option value="${item.id}" ${item.id === +value ? "selected" : ""}>${esc(item.name)}</option>`).join("");
}

function productionCharacterPicker(selected = []) {
  const chosen = new Set((selected || []).map(Number));
  const characters = entitiesCache.filter(item => item.kind === "人物");
  if (!characters.length) return '<div class="production-empty compact"><span>还没有人物卡</span></div>';
  return `<div class="production-character-picker">${characters.map(item => `
    <label><input id="productionSceneCharacter${item.id}" type="checkbox" data-production-character="${item.id}" ${chosen.has(item.id) ? "checked" : ""}>
      <span><b>${esc(item.name)}</b><small>${esc(item.summary || productionStateBrief(item.current_state))}</small></span>
    </label>`).join("")}</div>`;
}

function inspectProductionScene(sceneId) {
  const item = productionChapterData?.scenes?.find(scene => scene.id === sceneId);
  if (!item) return;
  stashProductionInspectorDraft();
  clearTimeout(productionInspectorSaveTimer);
  productionSelected = { type: "scene", item };
  openProductionInspector(item.title, `场景 ${item.ord}`, productionSceneForm(item));
}

function newProductionScene() {
  if (!productionChapterId) return;
  stashProductionInspectorDraft();
  clearTimeout(productionInspectorSaveTimer);
  const item = { id: null, title: "", summary: "", time_label: "", location_card_id: null, goal: "", conflict: "", outcome: "", refs: {} };
  productionSelected = { type: "scene", item };
  openProductionInspector("新建场景", "本章场景", productionSceneForm(item));
  setTimeout(() => $("productionSceneTitle")?.focus(), 30);
}

function productionSceneForm(item) {
  return `<div class="production-inspector-form" oninput="queueProductionInspectorSave()">
    <label>场景标题<input id="productionSceneTitle" value="${esc(item.title || "")}" placeholder="例如：雨夜抵达旧码头"></label>
    <div class="production-form-grid"><label>时间<input id="productionSceneTime" value="${esc(item.time_label || "")}"></label><label>地点<select id="productionSceneLocation">${productionLocationOptions(item.location_card_id)}</select></label></div>
    <label>场景摘要<textarea id="productionSceneSummary">${esc(item.summary || "")}</textarea></label>
    <label>目标<textarea id="productionSceneGoal">${esc(item.goal || "")}</textarea></label>
    <label>冲突<textarea id="productionSceneConflict">${esc(item.conflict || "")}</textarea></label>
    <label>结果<textarea id="productionSceneOutcome">${esc(item.outcome || "")}</textarea></label>
    <label>关联人物 <span>按姓名勾选</span>${productionCharacterPicker(item.refs?.character_ids || [])}</label>
    ${item.evidence ? `<div class="production-evidence">证据：${esc(item.evidence)}${Number.isInteger(item.evidence_start) ? `<button onclick="jumpToProductionEvidence(${item.evidence_start},${item.evidence_end})">查看正文</button>` : ""}</div>` : ""}
    <div class="production-inspector-actions"><button onclick="saveProductionSceneFromInspector()">${item.id ? "保存场景" : "创建场景"}</button>${item.id ? `<button onclick="handoffProductionObjectToAI('scene',${item.id})">交给 AI</button><button class="danger-link" onclick="deleteProductionScene(${item.id})">删除</button>` : '<button onclick="closeProductionInspector()">取消</button>'}</div>
  </div>`;
}

function readProductionSceneForm() {
  const characterIds = [...document.querySelectorAll("[data-production-character]:checked")]
    .map(input => +input.dataset.productionCharacter).filter(Number.isInteger);
  return { chapter_id: productionChapterId, title: $("productionSceneTitle").value.trim(),
    summary: $("productionSceneSummary").value, time_label: $("productionSceneTime").value,
    location_card_id: +$("productionSceneLocation").value || null, goal: $("productionSceneGoal").value,
    conflict: $("productionSceneConflict").value, outcome: $("productionSceneOutcome").value,
    refs: { ...(productionSelected?.item?.refs || {}), character_ids: [...new Set(characterIds)] } };
}

async function saveProductionSceneFromInspector(quiet = false) {
  if (productionSelected?.type !== "scene") return;
  const selection = productionSelected;
  const sceneId = selection.item.id;
  const chapterId = productionChapterId;
  const draftKey = productionDraftKey(selection, "base");
  const draftSnapshot = localStorage.getItem(draftKey);
  const body = readProductionSceneForm();
  if (!body.title) { if (!quiet) showToast("请填写场景标题", "err"); return; }
  return productionSaveQueue.run(async () => {
    try {
      productionInspectorSaving = true;
      const saved = await api(sceneId ? `/api/production/scenes/${sceneId}` : `/api/chapters/${chapterId}/production/scenes`, {
        method: sceneId ? "PUT" : "POST", body,
      });
      if (localStorage.getItem(draftKey) === draftSnapshot) clearProductionInspectorDraft(draftKey);
      if (productionSelected === selection) {
        $("productionSaveStatus").textContent = "已同步";
        productionSelected.item = saved;
      }
      if (productionChapterData?.scenes) {
        const index = productionChapterData.scenes.findIndex(item => item.id === saved.id);
        if (index >= 0) productionChapterData.scenes[index] = saved; else productionChapterData.scenes.push(saved);
      }
      renderProductionCanvas();
      if (!quiet && productionSelected === selection) { showToast(sceneId ? "场景已保存" : "场景已创建", "ok"); inspectProductionScene(saved.id); }
      return saved;
    } catch (e) {
      if ($("productionSaveStatus")) $("productionSaveStatus").textContent = "同步失败，草稿仍在本机";
      if (!quiet) showToast(e.message, "err");
      return null;
    } finally { productionInspectorSaving = false; }
  });
}

async function deleteProductionScene(sceneId) {
  const ok = await askCard({ title: "删除场景", msg: "只删除画布场景节点，不会删除正文。", okText: "删除", danger: true });
  if (!ok) return;
  const selection = productionSelected;
  const draftKeys = [productionDraftKey(selection, "base"), productionDraftKey(selection, "state")];
  try {
    await closeProductionInspector({ flush: false });
    await api(`/api/production/scenes/${sceneId}`, { method: "DELETE" });
    draftKeys.forEach(clearProductionInspectorDraft);
    await loadProductionChapter();
  }
  catch (e) { showToast(e.message, "err"); }
}

async function acceptProductionProposal(proposalId) {
  try { await api(`/api/production/proposals/${proposalId}/accept`); await loadProductionChapter(); showToast("变化已确认", "ok"); }
  catch (e) { showToast(e.message, "err"); }
}

async function rejectProductionProposal(proposalId) {
  try { await api(`/api/production/proposals/${proposalId}/reject`); await loadProductionChapter(); }
  catch (e) { showToast(e.message, "err"); }
}

async function resolveProductionImpact(impactId) {
  try { await api(`/api/production/impacts/${impactId}/resolve`); await loadProductionChapter(); }
  catch (e) { showToast(e.message, "err"); }
}

async function analyzeProductionChapter() {
  if (!productionChapterId) return;
  if (currentChapterId === productionChapterId && !await flushEditorSave()) return;
  const button = $("productionAnalyzeBtn");
  busy(button, true, "分析中");
  try {
    const result = await api(`/api/chapters/${productionChapterId}/production/analyze`, { body: {} });
    productionChapterData = result.production;
    productionCards = productionChapterData.after?.cards || [];
    renderProductionResources(); renderProductionCanvas(); syncProductionHeader();
    showToast(`已整理 ${result.scene_count || 0} 个场景和 ${result.changes?.length || 0} 项变化`, "ok");
  } catch (e) { showToast("分析失败：" + e.message, "err"); }
  finally { busy(button, false, "分析本章"); applyIcons(); }
}

async function jumpToProductionEvidence(start, end) {
  if (!Number.isInteger(start)) return;
  await closeProductionCanvas();
  const editor = $("content");
  editor.focus(); editor.setSelectionRange(start, Number.isInteger(end) ? end : start);
  typewriterCenter(); updateSelectionTools();
}

function toggleProductionSettings() { $("productionSettingsBar").classList.toggle("hidden"); }

async function saveProductionSettings() {
  if (!currentWorkId) return;
  try {
    const old = productionChapterData?.settings || productionOverviewData?.settings || {};
    const settings = await api(`/api/works/${currentWorkId}/production/settings`, { method: "PUT", body: {
      evidence_enabled: $("productionEvidenceEnabled").checked,
      auto_analyze_on_leave: $("productionAutoAnalyze").checked,
      custom_fields: old.custom_fields || [],
    }});
    if (productionChapterData) productionChapterData.settings = settings;
    if (productionOverviewData) productionOverviewData.settings = settings;
    $("productionSaveStatus").textContent = "设置已保存";
  } catch (e) { showToast(e.message, "err"); }
}

function editProductionCustomFields() {
  const settings = productionChapterData?.settings || productionOverviewData?.settings || {};
  const value = (settings.custom_fields || []).map(field =>
    `${field.name}|${field.type}${field.options?.length ? `|${field.options.join(",")}` : ""}`).join("\n");
  productionSelected = { type: "settings", item: settings };
  openProductionInspector("自定义字段", "本作品", `<div class="production-inspector-form">
    <div class="production-evidence">每行一个字段：名称|类型|选项。类型可用 text、number、level、enum、boolean、hidden；enum/level 的选项用逗号分隔。</div>
    <label>字段定义<textarea id="productionCustomFields" rows="12">${esc(value)}</textarea></label>
    <div class="production-inspector-actions"><button onclick="saveProductionCustomFields()">保存字段</button><button onclick="closeProductionInspector()">取消</button></div>
  </div>`);
}

async function saveProductionCustomFields() {
  const fields = $("productionCustomFields").value.split(/\r?\n/).map((line, index) => {
    const [name, type = "text", options = ""] = line.split("|").map(item => item.trim());
    return name ? { id: `custom_${index}_${name}`, name, type, options: options.split(/[,，]/).map(item => item.trim()).filter(Boolean) } : null;
  }).filter(Boolean);
  try {
    const settings = await api(`/api/works/${currentWorkId}/production/settings`, { method: "PUT", body: {
      evidence_enabled: $("productionEvidenceEnabled").checked,
      auto_analyze_on_leave: $("productionAutoAnalyze").checked, custom_fields: fields,
    }});
    if (productionChapterData) productionChapterData.settings = settings;
    if (productionOverviewData) productionOverviewData.settings = settings;
    closeProductionInspector(); showToast("自定义字段已保存", "ok");
  } catch (e) { showToast(e.message, "err"); }
}

/* ---------- 多模态创意灵感库 ---------- */

const inspirationTypeLabels = {
  text: "文字", voice_note: "语音", image: "图片", meme: "梗图", audio: "音频",
  music: "音乐", video: "视频", link: "链接", quote: "对白", real_event: "现实事件", mixed: "混合",
};
const inspirationCategoryLabels = {
  general: "一般", comedy: "搞笑", plot: "剧情", dialogue: "对白", character: "人物",
  emotion: "情绪", visual: "画面", music: "音乐", sound: "音效", camera: "运镜",
  editing: "剪辑", worldbuilding: "世界观", action: "动作", romance: "感情",
  horror: "恐怖", suspense: "悬疑", production: "制作参考",
};
const inspirationStatusLabels = {
  inbox: "待整理", available: "可用", archived: "已归档", rejected: "已拒绝",
};
const inspirationAnalysisLabels = {
  pending: "整理中", completed: "已整理", failed: "整理失败",
};

function inspirationIcon(type) {
  if (type === "image" || type === "meme") return "image";
  if (type === "music" || type === "audio" || type === "voice_note") return "music";
  if (type === "video") return "film";
  return "bulb";
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(bytes >= 10 * 1024 * 1024 ? 0 : 1)} MB`;
}

function formatInspirationTime(value) {
  if (!value) return "";
  const date = new Date(Number(value) * 1000);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN", {
    month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

async function openInspirationLibrary() {
  $("inspirationWorkspace").classList.remove("hidden");
  document.body.classList.add("inspiration-open");
  syncInspirationScopeControls();
  currentInspiration = null;
  $("inspirationDetail").innerHTML = `
    <div class="inspiration-empty-detail">
      ${svg("bulb")}<b>未选择灵感</b>
      <span>从列表选择一条，或新建记录。</span>
    </div>`;
  await loadInspirations();
}

function syncInspirationScopeControls() {
  if (!currentWorkId && inspirationScope === "work") inspirationScope = "all";
  document.querySelectorAll("[data-inspiration-scope]").forEach(button => {
    button.classList.toggle("active", button.dataset.inspirationScope === inspirationScope);
    if (button.dataset.inspirationScope === "work") button.disabled = !currentWorkId;
  });
}

function closeInspirationLibrary() {
  $("inspirationWorkspace").classList.add("hidden");
  $("inspirationWorkspace").classList.remove("detail-open");
  document.body.classList.remove("inspiration-open");
  pendingInspirationFile = null;
  if (inspirationPreviewUrl) URL.revokeObjectURL(inspirationPreviewUrl);
  inspirationPreviewUrl = null;
  const input = $("inspirationQuickFile");
  if (input) input.value = "";
}

function chooseInspirationFileFromAgent() {
  $("inspirationQuickFile").click();
}

async function openInspirationCapture(file) {
  pendingInspirationFile = file || null;
  const input = $("inspirationQuickFile");
  if (input) input.value = "";
  if ($("inspirationWorkspace").classList.contains("hidden")) await openInspirationLibrary();
  showInspirationCapture();
}

function showInspirationCapture(item = null) {
  currentInspiration = item;
  $("inspirationWorkspace").classList.add("detail-open");
  if (inspirationPreviewUrl) URL.revokeObjectURL(inspirationPreviewUrl);
  inspirationPreviewUrl = pendingInspirationFile ? URL.createObjectURL(pendingInspirationFile) : null;
  const file = pendingInspirationFile;
  const inferredType = file?.type?.startsWith("image/") ? "image"
    : file?.type?.startsWith("audio/") ? "music"
    : file?.type?.startsWith("video/") ? "video" : (item?.source_type || "text");
  const scope = item?.scope || (currentWorkId ? "work" : "global");
  const categories = Object.entries(inspirationCategoryLabels).map(([value, label]) =>
    `<option value="${value}" ${(item?.primary_category || "general") === value ? "selected" : ""}>${label}</option>`
  ).join("");
  const sourceTypes = Object.entries(inspirationTypeLabels).map(([value, label]) =>
    `<option value="${value}" ${inferredType === value ? "selected" : ""}>${label}</option>`
  ).join("");
  const mediaPreview = inspirationPreviewUrl && file?.type?.startsWith("image/")
    ? `<div class="inspiration-capture-preview"><img src="${esc(inspirationPreviewUrl)}" alt="待保存图片预览"></div>`
    : inspirationPreviewUrl && file?.type?.startsWith("audio/")
      ? `<div class="inspiration-capture-preview audio"><audio controls preload="metadata" src="${esc(inspirationPreviewUrl)}"></audio></div>`
      : inspirationPreviewUrl && file?.type?.startsWith("video/")
        ? `<div class="inspiration-capture-preview"><video controls preload="metadata" src="${esc(inspirationPreviewUrl)}"></video></div>`
        : "";
  const filePreview = file ? `
    <div class="inspiration-file-ready">
      <span class="inspiration-file-icon">${svg(inspirationIcon(inferredType))}</span>
      <span><b>${esc(file.name)}</b><small>${formatBytes(file.size)} · 原始文件会完整保存</small></span>
      <button class="ic" data-ic="x" onclick="clearPendingInspirationFile()" title="移除素材"></button>
    </div>${mediaPreview}` : "";
  $("inspirationDetail").innerHTML = `
    <div class="inspiration-detail-head">
      <button class="ic inspiration-mobile-back" data-ic="chevL" onclick="closeInspirationDetail()" title="返回列表"></button>
      <div><span class="detail-eyebrow">${item ? "编辑灵感" : "快速记录"}</span><h2>${item ? esc(item.title) : "新灵感"}</h2></div>
      <button class="ic" data-ic="x" onclick="${item ? `selectInspiration(${item.id})` : "closeInspirationDetail()"}" title="取消"></button>
    </div>
    <form class="inspiration-capture" onsubmit="event.preventDefault();saveInspirationCapture()">
      ${filePreview}
      <div class="inspiration-form-row">
        <label>范围
          <select id="inspirationFormScope">
            <option value="global" ${scope === "global" ? "selected" : ""}>所有作品通用</option>
            <option value="work" ${scope === "work" ? "selected" : ""} ${currentWorkId ? "" : "disabled"}>当前作品</option>
          </select>
        </label>
        <label>素材类型<select id="inspirationFormType">${sourceTypes}</select></label>
        <label>主要分类<select id="inspirationFormCategory">${categories}</select></label>
      </div>
      <label>标题 <span>可以留空，让 AI 整理</span>
        <input id="inspirationFormTitle" maxlength="160" value="${esc(item?.title || "")}" placeholder="例如：一本正经地掩饰破防">
      </label>
      <label>原始想法 <span>这里永远保留，不会被 AI 摘要覆盖</span>
        <textarea id="inspirationFormRaw" rows="5" maxlength="30000" placeholder="刚想到的梗、对白、场景、现实事件……">${esc(item?.raw_text || "")}</textarea>
      </label>
      <label>我的联想 <span>它为什么有意思，适合用在哪里</span>
        <textarea id="inspirationFormImpression" rows="3" maxlength="12000" placeholder="例如：适合反派嘴硬时，先拍脸再切到捏碎的杯子">${esc(item?.user_impression || "")}</textarea>
      </label>
      <label>原始链接
        <input id="inspirationFormUrl" maxlength="4000" value="${esc(item?.assets?.find(a => a.source_url)?.source_url || "")}" placeholder="网页、音乐或视频链接（可选）">
      </label>
      <div class="inspiration-form-row compact">
        <label>复用方式
          <select id="inspirationFormReuse">
            <option value="adaptable" ${(item?.reuse_mode || "adaptable") === "adaptable" ? "selected" : ""}>可改编复用</option>
            <option value="one_off" ${item?.reuse_mode === "one_off" ? "selected" : ""}>一次性桥段</option>
            <option value="running_gag" ${item?.reuse_mode === "running_gag" ? "selected" : ""}>反复梗</option>
            <option value="reference_only" ${item?.reuse_mode === "reference_only" ? "selected" : ""}>只作参考</option>
          </select>
        </label>
        <label class="grow">标签
          <input id="inspirationFormTags" value="${esc((item?.tags || []).join("，"))}" placeholder="搞笑，反转，雨夜">
        </label>
      </div>
      <div class="inspiration-save-bar">
        <span id="inspirationFormMsg" class="msg"></span>
        <button type="submit" id="inspirationSaveBtn">${item ? "保存修改" : "保存灵感"}</button>
      </div>
    </form>`;
  applyIcons();
  setTimeout(() => $("inspirationFormRaw")?.focus(), 30);
}

function clearPendingInspirationFile() {
  pendingInspirationFile = null;
  showInspirationCapture(currentInspiration);
}

async function uploadInspirationFile(inspirationId, file, description) {
  const type = file.type.startsWith("image/") ? "image"
    : file.type.startsWith("audio/") ? "music"
    : file.type.startsWith("video/") ? "video" : "";
  const res = await fetch(`/api/inspirations/${inspirationId}/assets`, {
    method: "POST",
    headers: {
      Authorization: "Bearer " + token,
      "Content-Type": file.type || "application/octet-stream",
      "X-File-Name": encodeURIComponent(file.name),
      "X-Asset-Type": type,
      "X-Asset-Description": encodeURIComponent((description || "").slice(0, 3000)),
    },
    body: file,
  });
  if (res.status === 401) { showLogin(); throw new Error("未登录"); }
  if (!res.ok) throw new Error(await responseError(res));
  return res.json();
}

async function saveInspirationCapture() {
  if ($("inspirationSaveBtn")?.classList.contains("is-busy")) return;
  const raw = $("inspirationFormRaw").value.trim();
  const impression = $("inspirationFormImpression").value.trim();
  const sourceUrl = $("inspirationFormUrl").value.trim();
  if (!raw && !impression && !sourceUrl && !pendingInspirationFile) {
    $("inspirationFormMsg").textContent = "请写一句想法，或选择一个素材";
    return;
  }
  const scope = $("inspirationFormScope").value;
  if (scope === "work" && !currentWorkId) {
    $("inspirationFormMsg").textContent = "当前没有可关联的作品";
    return;
  }
  const button = $("inspirationSaveBtn");
  const wasEditing = Boolean(currentInspiration);
  busy(button, true, currentInspiration ? "保存中" : "正在保存");
  $("inspirationFormMsg").textContent = "";
  const body = {
    title: $("inspirationFormTitle").value.trim(),
    title_locked: Boolean($("inspirationFormTitle").value.trim()),
    raw_text: raw,
    user_impression: impression,
    source_url: sourceUrl,
    source_type: $("inspirationFormType").value,
    primary_category: $("inspirationFormCategory").value,
    scope,
    work_id: scope === "work" ? currentWorkId : null,
    reuse_mode: $("inspirationFormReuse").value,
    tags: $("inspirationFormTags").value,
    analyze: !pendingInspirationFile,
  };
  let createdInThisAttempt = false;
  try {
    let item;
    if (wasEditing) {
      item = (await api(`/api/inspirations/${currentInspiration.id}`, { method: "PUT", body })).inspiration;
    } else {
      item = (await api("/api/inspirations", { body })).inspiration;
      currentInspiration = item;
      createdInThisAttempt = true;
    }
    if (pendingInspirationFile) {
      await uploadInspirationFile(item.id, pendingInspirationFile, impression || raw);
      pendingInspirationFile = null;
      if (inspirationPreviewUrl) URL.revokeObjectURL(inspirationPreviewUrl);
      inspirationPreviewUrl = null;
    } else if (wasEditing) {
      await api(`/api/inspirations/${item.id}/analyze`, { body: {} });
    }
    showToast("灵感已保存，AI 正在整理", "ok");
    await loadInspirations(item.id);
    await selectInspiration(item.id);
  } catch (e) {
    if (createdInThisAttempt && pendingInspirationFile) {
      $("inspirationFormMsg").textContent = `文字已保存，素材上传失败：${e.message}。可直接再次保存重试。`;
      try { await api(`/api/inspirations/${currentInspiration.id}/analyze`, { body: {} }); } catch (_) {}
      await loadInspirations(currentInspiration?.id);
    } else {
      $("inspirationFormMsg").textContent = e.message;
    }
  } finally {
    busy(button, false, currentInspiration ? "保存修改" : "保存灵感");
  }
}

function setInspirationScope(scope) {
  if (scope === "work" && !currentWorkId) {
    showToast("当前没有作品，只能查看通用灵感", "err");
    return;
  }
  inspirationScope = scope;
  syncInspirationScopeControls();
  loadInspirations();
}

function scheduleInspirationSearch() {
  clearTimeout(inspirationSearchTimer);
  inspirationSearchTimer = setTimeout(loadInspirations, 260);
}

async function loadInspirations(selectId = null) {
  const host = $("inspirationList");
  if (!host || $("inspirationWorkspace").classList.contains("hidden")) return;
  host.innerHTML = `<div class="inspiration-loading"><span class="spinner"></span> 正在读取灵感</div>`;
  const params = new URLSearchParams({
    scope: inspirationScope,
    status: $("inspirationStatusFilter").value,
    query: $("inspirationSearch").value.trim(),
    page_size: "80",
  });
  if (currentWorkId) params.set("work_id", currentWorkId);
  const type = $("inspirationTypeFilter").value;
  if (type) params.set("source_type", type);
  if ($("inspirationFavoriteFilter").checked) params.set("favorite", "1");
  try {
    const result = await api(`/api/inspirations?${params}`, { method: "GET" });
    inspirationItems = result.items || [];
    $("inspirationCount").textContent = result.total
      ? `${result.total} 条 · 原始素材与作者联想永久保留`
      : "还没有符合条件的灵感";
    renderInspirationList();
    if (selectId && inspirationItems.some(item => item.id === selectId)) {
      document.querySelector(`[data-inspiration-id="${selectId}"]`)?.classList.add("active");
    }
  } catch (e) {
    host.innerHTML = `<div class="inspiration-empty"><b>读取失败</b><span>${esc(e.message)}</span></div>`;
  }
}

function renderInspirationList() {
  const host = $("inspirationList");
  if (!inspirationItems.length) {
    host.innerHTML = `
      <div class="inspiration-empty">
        ${svg("bulb")}<b>当前范围内没有灵感</b>
        <span>可以新建记录或上传素材。</span>
        <button onclick="showInspirationCapture()">${svg("plus")} 记录第一条</button>
      </div>`;
    return;
  }
  host.innerHTML = inspirationItems.map(item => `
    <button class="inspiration-row ${currentInspiration?.id === item.id ? "active" : ""}"
            data-inspiration-id="${item.id}" onclick="selectInspiration(${item.id})">
      <span class="inspiration-row-icon">${svg(inspirationIcon(item.source_type))}</span>
      <span class="inspiration-row-copy">
        <span class="inspiration-row-title">${item.favorite ? svg("star") : ""}<b>${esc(item.title || "未命名灵感")}</b></span>
        <span class="inspiration-row-text">${esc(item.creative_summary || item.user_impression || item.raw_text || "原始素材已保存")}</span>
        <span class="inspiration-row-meta">
          ${esc(inspirationTypeLabels[item.source_type] || "灵感")}
          · ${item.scope === "work" ? esc(item.work_title || "本作品") : "通用"}
          · ${esc(inspirationAnalysisLabels[item.analysis_status] || inspirationStatusLabels[item.library_status] || "")}
          ${item.use_count ? ` · 已用 ${item.use_count} 次` : ""}
        </span>
      </span>
      <span class="inspiration-row-time">${formatInspirationTime(item.updated_at)}</span>
    </button>`).join("");
}

function closeInspirationDetail() {
  $("inspirationWorkspace").classList.remove("detail-open");
}

async function selectInspiration(id, fromPoll = false) {
  if (!fromPoll) inspirationPendingPolls = 0;
  $("inspirationWorkspace").classList.add("detail-open");
  $("inspirationDetail").innerHTML = `<div class="inspiration-loading"><span class="spinner"></span> 正在读取详情</div>`;
  try {
    currentInspiration = (await api(`/api/inspirations/${id}`, { method: "GET" })).inspiration;
    renderInspirationList();
    renderInspirationDetail(currentInspiration);
    if (currentInspiration.analysis_status === "pending" && inspirationPendingPolls < 20) {
      inspirationPendingPolls += 1;
      setTimeout(async () => {
        if (!$("inspirationWorkspace").classList.contains("hidden") && currentInspiration?.id === id) {
          await selectInspiration(id, true);
          await loadInspirations(id);
        }
      }, 2600);
    }
  } catch (e) {
    $("inspirationDetail").innerHTML = `<div class="inspiration-empty"><b>读取失败</b><span>${esc(e.message)}</span></div>`;
  }
}

function inspirationTags(item) {
  return [...(item.tags || []), ...(item.mood_tags || []), ...(item.usage_tags || [])]
    .filter((value, index, all) => value && all.indexOf(value) === index);
}

function detailTextBlock(label, value) {
  return value ? `<section class="inspiration-detail-section"><h3>${label}</h3><p>${esc(value)}</p></section>` : "";
}

function renderInspirationDetail(item) {
  const tags = inspirationTags(item);
  const assets = item.assets || [];
  const usage = item.usages || [];
  const analysisNote = item.analysis_status === "failed"
    ? (item.analysis_error || "可稍后重试")
    : item.analysis_status === "pending"
      ? "原始内容已经安全保存，整理不会影响它"
      : ["audio", "music", "voice_note", "video"].includes(item.source_type)
        ? "当前依据作者描述、文件名和链接整理，原始素材保持不变"
        : "AI 分析是辅助信息，可以随时编辑";
  $("inspirationDetail").innerHTML = `
    <div class="inspiration-detail-head">
      <button class="ic inspiration-mobile-back" data-ic="chevL" onclick="closeInspirationDetail()" title="返回列表"></button>
      <div>
        <span class="detail-eyebrow">${esc(inspirationTypeLabels[item.source_type] || "灵感")} · ${item.scope === "work" ? esc(item.work_title || "本作品") : "所有作品通用"}</span>
        <h2>${esc(item.title || "未命名灵感")}</h2>
      </div>
      <div class="inspiration-detail-actions">
        <button class="ic ${item.favorite ? "active" : ""}" data-ic="star" onclick="toggleInspirationFavorite()" title="${item.favorite ? "取消收藏" : "收藏"}"></button>
        <button class="ic" data-ic="pen" onclick="editCurrentInspiration()" title="编辑"></button>
        <button class="ic" data-ic="more" onclick="toggleInspirationActions(event)" title="更多"></button>
        <div id="inspirationActions" class="inspiration-action-menu hidden">
          <button onclick="reanalyzeCurrentInspiration()">${svg("sparkles")} AI 重新整理</button>
          <button onclick="archiveCurrentInspiration()">${svg("archive")} ${item.library_status === "archived" ? "恢复为可用" : "归档"}</button>
          <button class="danger-link" onclick="deleteCurrentInspiration()">${svg("trash")} 删除灵感</button>
        </div>
      </div>
    </div>
    <div class="inspiration-detail-scroll">
      <div class="inspiration-status-strip ${item.analysis_status}">
        <span>${item.analysis_status === "pending" ? '<span class="spinner"></span>' : svg(item.analysis_status === "completed" ? "check" : "alert")}</span>
        <div><b>${esc(inspirationAnalysisLabels[item.analysis_status] || "已保存")}</b>
        <small>${esc(analysisNote)}</small></div>
      </div>
      ${assets.length ? `<section class="inspiration-detail-section"><h3>原始素材</h3><div id="inspirationAssets" class="inspiration-assets">
        ${assets.map(asset => `<div id="inspirationAsset-${asset.id}" class="inspiration-asset"><span class="spinner"></span> 正在准备 ${esc(asset.original_name || "素材")}</div>`).join("")}
      </div></section>` : ""}
      ${detailTextBlock("作者原话", item.raw_text)}
      ${detailTextBlock("我的联想", item.user_impression)}
      ${item.creative_summary || item.core_mechanism || item.suitable_context ? `
        <section class="inspiration-analysis-grid">
          ${item.creative_summary ? `<div><h3>AI 整理</h3><p>${esc(item.creative_summary)}</p></div>` : ""}
          ${item.core_mechanism ? `<div><h3>核心机制</h3><p>${esc(item.core_mechanism)}</p></div>` : ""}
          ${item.suitable_context ? `<div><h3>适用场景</h3><p>${esc(item.suitable_context)}</p></div>` : ""}
          ${item.adaptation_notes ? `<div><h3>写作改编</h3><p>${esc(item.adaptation_notes)}</p></div>` : ""}
          ${item.production_notes ? `<div><h3>漫剧与制作</h3><p>${esc(item.production_notes)}</p></div>` : ""}
        </section>` : ""}
      ${tags.length ? `<section class="inspiration-detail-section"><h3>标签</h3><div class="inspiration-tags">${tags.map(tag => `<span>${esc(tag)}</span>`).join("")}</div></section>` : ""}
      <section class="inspiration-use-section">
        <div class="inspiration-section-head"><div><h3>使用记录</h3><small>${usage.length ? `已记录 ${usage.length} 次` : "实际采用后再记录，推荐不算使用"}</small></div>
          ${currentChapterId ? `<button onclick="markCurrentInspirationUsed()">${svg("check")} 标记用于本章</button>` : ""}
        </div>
        <div class="inspiration-usage-list">
          ${usage.length ? usage.map(record => `
            <div><b>${record.chapter_ord ? `第${record.chapter_ord}章 ` : ""}${esc(record.chapter_title || record.work_title || "其他创作")}</b>
            <span>${esc(record.adaptation_summary || record.usage_type || "已使用")}</span>
            <small>${formatInspirationTime(record.created_at)}</small></div>`).join("") : '<div class="inspiration-muted">尚未使用</div>'}
        </div>
      </section>
    </div>`;
  applyIcons();
  hydrateInspirationAssets(assets);
}

async function hydrateInspirationAssets(assets) {
  for (const asset of assets) {
    const host = $(`inspirationAsset-${asset.id}`);
    if (!host) continue;
    if (asset.source_url) {
      host.innerHTML = `<a href="${esc(asset.source_url)}" target="_blank" rel="noopener noreferrer">${svg("paperclip")}<span><b>${esc(asset.original_name || "打开原始链接")}</b><small>${esc(asset.source_url)}</small></span></a>`;
      continue;
    }
    if (!asset.file_size) {
      host.innerHTML = `<span>${svg(inspirationIcon(asset.asset_type))}</span><span><b>${esc(asset.original_name || "素材")}</b><small>原始文件不可用</small></span>`;
      continue;
    }
    try {
      const access = await api(`/api/inspiration-assets/${asset.id}/access`, { method: "GET" });
      const caption = `<div class="inspiration-asset-caption"><b>${esc(asset.original_name || "素材")}</b><small>${formatBytes(asset.file_size)} · ${esc(asset.copyright_status === "unknown" ? "仅作创作参考" : asset.copyright_status)}</small></div>`;
      if ((asset.mime_type || "").startsWith("image/")) {
        host.innerHTML = `<img src="${esc(access.url)}" alt="${esc(asset.original_name || "灵感图片")}">${caption}`;
      } else if ((asset.mime_type || "").startsWith("audio/")) {
        host.innerHTML = `${caption}<audio controls preload="metadata" src="${esc(access.url)}"></audio>`;
      } else if ((asset.mime_type || "").startsWith("video/")) {
        host.innerHTML = `<video controls preload="metadata" src="${esc(access.url)}"></video>${caption}`;
      }
    } catch (e) {
      host.innerHTML = `<span>${svg("alert")}</span><span><b>${esc(asset.original_name || "素材")}</b><small>${esc(e.message)}</small></span>`;
    }
  }
}

function editCurrentInspiration() {
  pendingInspirationFile = null;
  if (currentInspiration) showInspirationCapture(currentInspiration);
}

function toggleInspirationActions(e) {
  e?.stopPropagation();
  $("inspirationActions")?.classList.toggle("hidden");
}

async function toggleInspirationFavorite() {
  if (!currentInspiration) return;
  const result = await api(`/api/inspirations/${currentInspiration.id}`, {
    method: "PUT", body: { favorite: !currentInspiration.favorite },
  });
  currentInspiration = result.inspiration;
  renderInspirationDetail(currentInspiration);
  await loadInspirations(currentInspiration.id);
}

async function reanalyzeCurrentInspiration() {
  if (!currentInspiration) return;
  await api(`/api/inspirations/${currentInspiration.id}/analyze`, { body: {} });
  showToast("已加入 AI 整理队列", "ok");
  await selectInspiration(currentInspiration.id);
}

async function archiveCurrentInspiration() {
  if (!currentInspiration) return;
  const next = currentInspiration.library_status === "archived" ? "available" : "archived";
  await api(`/api/inspirations/${currentInspiration.id}`, { method: "PUT", body: { library_status: next } });
  showToast(next === "archived" ? "已归档" : "已恢复", "ok");
  currentInspiration = null;
  closeInspirationDetail();
  await loadInspirations();
}

async function deleteCurrentInspiration() {
  if (!currentInspiration) return;
  const confirmed = await askCard({
    title: "删除灵感", msg: `将删除「${currentInspiration.title}」及其私有上传文件，无法从回收站恢复。`,
    okText: "确认删除", danger: true,
  });
  if (!confirmed) return;
  await api(`/api/inspirations/${currentInspiration.id}`, { method: "DELETE" });
  currentInspiration = null;
  closeInspirationDetail();
  $("inspirationDetail").innerHTML = `<div class="inspiration-empty-detail">${svg("bulb")}<b>灵感已删除</b><span>可以继续选择其他内容。</span></div>`;
  await loadInspirations();
}

async function markCurrentInspirationUsed() {
  if (!currentInspiration || !currentChapterId) return;
  const summary = await askCard({
    title: "记录使用方式", input: "例如：改成宗门比武中的身份误会", okText: "记录",
  });
  if (summary === false) return;
  await api(`/api/inspirations/${currentInspiration.id}/usages`, {
    body: {
      current_work_id: currentWorkId,
      current_chapter_id: currentChapterId,
      usage_type: "inserted",
      usage_status: "applied",
      adaptation_summary: summary || "已用于当前章节",
    },
  });
  showToast("已记录使用位置", "ok");
  await selectInspiration(currentInspiration.id);
  await loadInspirations(currentInspiration.id);
}

/* ---------- 布局 ---------- */

function toggleSidebar() { $("app").classList.toggle("side-open"); }
function toggleFocus() { $("app").classList.toggle("focus"); setTimeout(typewriterCenter, 30); }

/* 顶栏「⋯更多」下拉 */
function toggleMoreMenu(e) { e?.stopPropagation(); $("moreMenu").classList.toggle("hidden"); }
function closeMoreMenu() { $("moreMenu").classList.add("hidden"); }

/* 明暗主题：默认跟随系统，手动切换后记忆 */
function applyTheme(t) {
  if (t === "dark" || t === "light") document.documentElement.setAttribute("data-theme", t);
  else document.documentElement.removeAttribute("data-theme");
  const dark = t === "dark" || (t == null && matchMedia("(prefers-color-scheme: dark)").matches);
  const b = $("themeBtn"); if (b) setIcon(b, dark ? "sun" : "moon", dark ? "浅色主题" : "深色主题");
}
function toggleTheme() {
  const cur = document.documentElement.getAttribute("data-theme");
  const isDark = cur ? cur === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  const next = isDark ? "light" : "dark";
  localStorage.setItem("theme", next); applyTheme(next);
}
if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  if (!localStorage.getItem("theme")) applyTheme(null);
});

/* 衬线/无衬线字体（写小说更入戏） */
function applyFont(serif) {
  document.documentElement.classList.toggle("font-serif", !!serif);
  const b = $("fontBtn"); if (b) setIcon(b, "type", serif ? "衬线字体" : "无衬线字体");
}
function toggleFont() {
  const serif = !document.documentElement.classList.contains("font-serif");
  localStorage.setItem("fontSerif", serif ? "1" : "0"); applyFont(serif);
}

/* 专注模式打字机：用镜像测光标前文本高度，把光标行滚到视口约中部 */
let twMirror = null;
function typewriterCenter() {
  if (!$("app").classList.contains("focus")) return;
  const el = $("content"); if (!el) return;
  if (!twMirror) { twMirror = document.createElement("div"); twMirror.className = "tw-mirror"; document.body.appendChild(twMirror); }
  const cs = getComputedStyle(el);
  const sb = el.offsetWidth - el.clientWidth - 2;          // 滚动条宽（边框各 1px）
  twMirror.style.width = Math.max(0, el.clientWidth - sb) + "px";
  twMirror.style.fontSize = cs.fontSize;
  twMirror.style.lineHeight = cs.lineHeight;
  twMirror.style.fontFamily = cs.fontFamily;
  twMirror.style.padding = cs.padding;
  twMirror.style.boxSizing = "border-box";
  twMirror.textContent = el.value.slice(0, el.selectionStart);
  el.scrollTop = Math.max(0, twMirror.scrollHeight - el.clientHeight * 0.42);
}

/* ---------- 全局事件 ---------- */

document.addEventListener("keydown", (e) => {
  const inspirationOpen = !$("inspirationWorkspace")?.classList.contains("hidden");
  const productionOpen = $("app")?.classList.contains("production-open");
  if ((e.ctrlKey || e.metaKey) && e.key === "s") {
    e.preventDefault();
    if (inspirationOpen && $("inspirationSaveBtn")) saveInspirationCapture();
    else if (productionOpen && productionSelected?.type === "card") saveProductionCardFromInspector();
    else if (productionOpen && productionSelected?.type === "scene") saveProductionSceneFromInspector();
    else saveNow();
  }
  if ((e.ctrlKey || e.metaKey) && e.key === "f") {
    e.preventDefault();
    if (inspirationOpen) {
      $("inspirationSearch")?.focus();
      $("inspirationSearch")?.select();
    } else if (productionOpen) {
      toggleProductionResources(true);
      $("productionResourceSearch")?.focus();
      $("productionResourceSearch")?.select();
    } else toggleFind();
  }
  if (e.key === "Escape" && !$("inspirationWorkspace")?.classList.contains("hidden")) {
    const actions = $("inspirationActions");
    if (actions && !actions.classList.contains("hidden")) actions.classList.add("hidden");
    else closeInspirationLibrary();
  }
  if (e.key === "Escape" && productionOpen && !inspirationOpen) {
    if ($("productionWorkspace").classList.contains("inspector-open")) closeProductionInspector();
    else closeProductionCanvas();
  }
});
$("content").addEventListener("input", onContentInput);
$("content").addEventListener("scroll", syncSemanticEditor, { passive: true });
$("content").addEventListener("keyup", () => { typewriterCenter(); updateSelectionTools(); });
$("content").addEventListener("click", () => { typewriterCenter(); updateSelectionTools(); });
$("content").addEventListener("mouseup", updateSelectionTools);
$("content").addEventListener("select", updateSelectionTools);
$("semanticText").addEventListener("pointerover", e => {
  const tokenEl = e.target.closest(".semantic-token");
  if (tokenEl) showSemanticPop(+tokenEl.dataset.entityId, tokenEl);
});
$("semanticText").addEventListener("pointerout", e => {
  if (e.target.closest(".semantic-token")) hideSemanticPop(140);
});
$("semanticText").addEventListener("click", e => {
  const tokenEl = e.target.closest(".semantic-token");
  if (!tokenEl) return;
  e.preventDefault(); e.stopPropagation();
  openSemanticEntity(+tokenEl.dataset.entityId);
});
$("semanticPop").addEventListener("pointerenter", () => clearTimeout(semanticPopTimer));
$("semanticPop").addEventListener("pointerleave", () => hideSemanticPop(100));
window.addEventListener("resize", syncSemanticEditor, { passive: true });
$("notes").addEventListener("input", onNotesInput);
$("chapTitle").addEventListener("input", markEditorDirty);
window.addEventListener("pagehide", () => { if (dirty) stashEditorDraft(); });
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden" && dirty) stashEditorDraft();
});
plotStateFields.map(([, , id]) => id).concat(["plotStateSummary", "plotStateEvidence"])
  .forEach(id => $(id).addEventListener("input", queuePlotStateAutosave));
document.addEventListener("click", (e) => {
  const m = $("moreMenu");
  if (m && !m.classList.contains("hidden") && !e.target.closest(".menu-wrap")) m.classList.add("hidden");
  const inspirationActions = $("inspirationActions");
  if (inspirationActions && !inspirationActions.classList.contains("hidden")
      && !e.target.closest(".inspiration-detail-actions")) {
    inspirationActions.classList.add("hidden");
  }
  if (!e.target.closest(".semantic-token") && !e.target.closest("#semanticPop")) hideSemanticPop(0);
});

/* ---------- 启动 ---------- */

(async function start() {
  applyIcons();
  // 移动端键盘适配：跟随可视视口高度，避免输入框被键盘遮挡
  if (window.visualViewport) {
    const onVp = () => {
      document.documentElement.style.setProperty("--vp-h", window.visualViewport.height + "px");
      document.documentElement.style.setProperty("--vp-top", window.visualViewport.offsetTop + "px");
    };
    window.visualViewport.addEventListener("resize", onVp);
    window.visualViewport.addEventListener("scroll", onVp);
    onVp();
  }
  applyTheme(localStorage.getItem("theme"));
  applyFont(localStorage.getItem("fontSerif") === "1");
  if (localStorage.getItem("aiOpen") === "1") $("app").classList.add("ai-open");
  setAiTtsBtn(); // 朗读开关按钮图标按上次状态显示
  setAgentMic(false);
  // 根据是否开放注册，决定显示注册入口
  try {
    const s = await api("/api/signup-status", { method: "GET" });
    if (s.enabled) $("toRegister").classList.remove("hidden");
    // 开放注册且无需注册码时，隐藏注册码输入框
    $("regCode").classList.toggle("hidden", !s.needs_code);
  } catch (e) {}
  if (token) {
    try { await init(); return; } catch (e) { token = ""; localStorage.removeItem("token"); }
  }
  showLogin();
})();
