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

// 自动保存
let saveTimer = null, dirty = false;
// 查找
let findPos = [], findIdx = -1;
// 拖拽
let dragCid = null;
// AI 助手（agent）
let agentMsgs = [];
let agentBusy = false;
let agentUndone = new Set();
let agentSelection = null;
let agentSkills = [];
let agentSkillsWorkId = undefined;
let activeAgentSkillIds = new Set();
let currentUsername = "";
// 写作工作台：剧情、章节流程、关系、提醒与本回合上下文共用一个右侧抽屉。
let storyTab = "plot";
let plotStateChapterId = null;
let plotStateProposalId = null;
let plotStateData = null;
let chapterWorkflow = null;
let entityRelations = [];
let editingRelationId = null;
let consistencyAlerts = [];
let agentContext = null;
let pendingEditReview = null;
let pendingRevisionRestore = null;
let pendingWorkRevisionRestore = null;
let voiceTraceState = null;

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
  alert:    `<svg ${_W}><path d="M10.3 3.8 2.7 17a2 2 0 0 0 1.7 3h15.2a2 2 0 0 0 1.7-3L13.7 3.8a2 2 0 0 0-3.4 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>`,
  check:    `<svg ${_W}><polyline points="20 6 9 17 4 12"/></svg>`,
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
document.addEventListener("keydown", e => { if (e.key === "Escape" && _askResolve) closeAsk(false); });
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
  if (!res.ok) throw new Error(await responseError(res));
  return res.json();
}

async function responseError(res) {
  const raw = (await res.text()).trim();
  let msg = raw || res.statusText || "请求失败";
  try {
    const data = JSON.parse(raw);
    if (data.detail && typeof data.detail === "object") {
      msg = data.detail.message || msg;
      if (data.detail.turn_id) msg += `（恢复编号：${data.detail.turn_id}）`;
    } else {
      msg = data.detail || data.message || msg;
    }
  } catch (e) {}
  msg = String(msg).replace(/\s+/g, " ");
  return msg.length > 260 ? msg.slice(0, 260) + "…" : msg;
}

const tail = (s, n) => (!s ? "" : s.length > n ? s.slice(-n) : s);
const charCount = (s) => (s || "").replace(/\s/g, "").length;
const esc = (s) => (s || "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
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
  try { await api("/api/logout"); } catch (e) {}
  token = ""; localStorage.removeItem("token");
  showLogin();
}

/* ---------- 目录树 ---------- */

async function init() {
  showApp();
  const me = await api("/api/me", { method: "GET" });
  if (currentUsername && currentUsername !== me.username) {
    agentSkills = []; agentSkillsWorkId = undefined; activeAgentSkillIds = new Set();
  }
  currentUsername = me.username || "";
  $("meName").textContent = me.username ? `${me.username} · 目录` : "目录";
  if (me.is_admin) $("adminBtn").classList.remove("hidden");
  await loadWorks();
}

async function loadWorks() {
  works = await api("/api/works", { method: "GET" });
  currentWorkId = works.length ? works[0].id : null;
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
        <span class="c-wc">${(c.chars || 0)}字</span>
        <button class="c-del" onclick="event.stopPropagation();delChapter(${c.id})" title="删除">${svg("x")}</button>
      </div>`).join("") : "";
    return `
      <div class="work ${open ? "open" : ""}">
        <div class="w-row" onclick="selectWork(${w.id})">
          <span class="w-title">${esc(w.title)}</span>
          <button class="ic" onclick="event.stopPropagation();openWorkNotes(${w.id})" title="作品设定">${svg("book")}</button>
          <button class="c-del" onclick="event.stopPropagation();delWork(${w.id})" title="删除">${svg("x")}</button>
        </div>
        ${open ? `<div class="chaps">${items || '<div class="empty">点「＋章」</div>'}</div>
                 <button class="add-chap" onclick="newChapter(${w.id})">${svg("plus")}<span class="ic-label">新章</span></button>` : ""}
      </div>`;
  }).join("");
}

async function selectWork(wid) {
  if (dirty) await saveNow();
  currentWorkId = wid;
  currentChapterId = null;
  await loadChapters();
}

async function loadChapters() {
  if (!currentWorkId) {
    chapters = []; renderTree(); updateWC();
    await loadAgentSkills();
    return;
  }
  chapters = await api(`/api/works/${currentWorkId}/chapters`, { method: "GET" });
  if (!chapters.find(c => c.id === currentChapterId)) {
    currentChapterId = chapters.length ? chapters[chapters.length - 1].id : null;
  }
  if (currentChapterId) await loadChapter();
  else { $("content").value = ""; $("chapTitle").value = ""; $("notes").value = ""; }
  renderTree(); updateWC();
  await loadAgentSkills();
}

async function selectChapter(cid) {
  if (dirty) await saveNow();
  currentChapterId = cid;
  clearAgentSelection();
  agentUndone.clear();
  await loadConversation();  // 切章：从服务端拉取该章持久化对话（刷新/切回都不丢）
  await loadChapter();
  renderTree();
  if (window.innerWidth <= 700) $("app").classList.remove("side-open");
}

async function loadChapter() {
  if (!currentChapterId) return;
  const c = await api(`/api/chapters/${currentChapterId}`, { method: "GET" });
  $("chapTitle").value = c.title || "";
  $("content").value = c.content || "";
  $("notes").value = c.notes || "";
  dirty = false; updateSaveStat("");
  updateWC();
  const cur = chapters.find(x => x.id === currentChapterId);
  if (cur) cur.chars = charCount(c.content || "");
  if (entitiesCacheWorkId === currentWorkId) entitiesCacheChapterId = null;
  if (!$("wikiOverlay").classList.contains("hidden")) await refreshCharacterCards();
  if (!$("characterStateOverlay").classList.contains("hidden") && characterStateChapterId === currentChapterId) {
    await loadCharacterState();
  }
  if ($("app").classList.contains("story-open")) await refreshStoryDrawer();
}

async function newWork() {
  const title = await askCard({ title: "新建作品", input: "作品名", def: "新作品", okText: "新建" });
  if (!title) return;
  const r = await api("/api/works", { body: { title } });
  currentWorkId = r.id; currentChapterId = null;
  await loadWorks();
}

async function newChapter(wid) {
  const title = await askCard({ title: "新建章节", input: "章节名", def: "新章节", okText: "新建" });
  if (!title) return;
  const r = await api(`/api/works/${wid}/chapters`, { body: { title } });
  currentWorkId = wid; currentChapterId = r.id;
  await loadChapters();
}

async function delChapter(cid) {
  if (!await askCard({ title: "移到回收站？", msg: "可找回。在回收站点「彻底删除」才会真正删除。", okText: "移到回收站", danger: true })) return;
  if (dirty) await saveNow();
  await api(`/api/chapters/${cid}`, { method: "DELETE" });
  currentChapterId = null;
  await loadChapters();
}

async function delWork(wid) {
  if (!await askCard({ title: "删除整个作品？", msg: "作品及其所有章节将被删除，不可恢复。", okText: "删除", danger: true })) return;
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

function onContentInput() {
  dirty = true; updateSaveStat("未保存"); updateWC();
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveNow, 1500);
  typewriterCenter();
  updateSelectionTools();
}
function onNotesInput() { dirty = true; updateSaveStat("未保存"); clearTimeout(saveTimer); saveTimer = setTimeout(saveNow, 1500); }

async function saveNow() {
  if (!currentChapterId || !dirty) return;
  clearTimeout(saveTimer);
  updateSaveStat("保存中…");
  try {
    await api(`/api/chapters/${currentChapterId}`, {
      method: "PUT",
      body: { title: $("chapTitle").value, content: $("content").value, notes: $("notes").value },
    });
    dirty = false; updateSaveStat("已保存");
    const cur = chapters.find(x => x.id === currentChapterId);
    if (cur) cur.chars = charCount($("content").value);
    renderTree();
  } catch (e) { updateSaveStat("保存失败"); }
}
function saveTitle() { dirty = true; saveNow(); }
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

/* ---------- AI 处理：先生成预览，作者确认后才写入正文 ---------- */

async function notifyStoryUpdates(result) {
  const count = Array.isArray(result?.character_state_proposals) ? result.character_state_proposals.length : 0;
  const plot = result?.plot_state_proposal ? 1 : 0;
  if (count || plot) {
    const labels = [];
    if (count) labels.push(`${count} 条人物状态`);
    if (plot) labels.push("剧情推进");
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
    if (dirty) await saveNow();
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
    if (dirty) await saveNow();
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
  if (dirty) await saveNow();
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
  if (dirty) await saveNow();
  await api(`/api/chapters/${currentChapterId}/revisions`, { method: "POST" });
  flash("已存为版本");
}

async function saveNamedRevision() {
  if (!currentChapterId) return;
  if (dirty) await saveNow();
  const label = await askCard({ title: "保存命名章节版本", input: "版本名称（可选）", def: "", okText: "保存" });
  if (label === false) return;
  await api(`/api/chapters/${currentChapterId}/revisions`, { body: { label: label || "" } });
  await showRevisions();
  showToast("章节版本已保存", "ok");
}

async function saveNamedWorkRevision() {
  if (!currentWorkId) return;
  if (dirty) await saveNow();
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
  if (dirty) await saveNow();
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

async function openSettings() {
  try {
    const s = await api("/api/settings", { method: "GET" });
    $("setBaseUrl").value = s.base_url || "";
    $("setModel").value = s.model || "";
    $("setAsrBaseUrl").value = s.asr_base_url || "";
    $("setAsrModel").value = s.asr_model || "whisper-1";
    // key 不回传明文：已填则用掩码占位提示，留空表示不改
    $("setApiKey").value = s.api_key_masked || "";
    $("setApiKey").placeholder = s.has_key ? `${s.api_key_masked}（留空=不改）` : "sk-…";
    $("setAsrApiKey").value = s.asr_api_key_masked || "";
    $("setAsrApiKey").placeholder = s.asr_has_key ? `${s.asr_api_key_masked}（留空=不改）` : "留空则沿用文字模型的 Key";
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
    : "录音会先调用下方转写服务，得到文字后再发送给 AI。";
}
async function saveSettings() {
  const base_url = $("setBaseUrl").value.trim();
  const model = $("setModel").value.trim();
  const asr_base_url = $("setAsrBaseUrl").value.trim();
  const asr_model = $("setAsrModel").value.trim();
  let api_key = $("setApiKey").value.trim();
  let asr_api_key = $("setAsrApiKey").value.trim();
  // 若用户没动 key 输入框（仍是掩码占位），传空让后端保留旧值
  if (api_key.startsWith("****")) api_key = "";
  if (asr_api_key.startsWith("****")) asr_api_key = "";
  voiceDirectToModel = $("setVoiceDirect").checked;
  voiceAsrAutoSend = $("setVoiceAuto").checked;
  localStorage.setItem("voiceDirectToModel", voiceDirectToModel ? "1" : "0");
  localStorage.setItem("voiceAsrAutoSend", voiceAsrAutoSend ? "1" : "0");
  setAgentMic(false);
  try {
    await api("/api/settings", { body: { base_url, api_key, model, asr_base_url, asr_api_key, asr_model } });
    $("setMsg").textContent = "已保存";
    setTimeout(closeSettings, 600);
  } catch (e) { $("setMsg").textContent = e.message; }
}

/* ---------- 作品设定（bible，喂给 AI 当全文记忆） ---------- */

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

/* ---------- AI 写作工具（校验/摘要，不污染正文） ---------- */

function openAITools() { $("aiResult").textContent = ""; $("aiOverlay").classList.remove("hidden"); }
function closeAITools() { $("aiOverlay").classList.add("hidden"); }
async function aiCheck() {
  if (!currentChapterId) { $("aiResult").textContent = "先选一章"; return; }
  $("aiResult").textContent = "校验中…";
  busy($("aiCheckBtn"), true);
  try {
    if (dirty) await saveNow();
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
      <span class="skill-pick-tags"><span class="skill-scope">${s.work_id == null ? "通用" : "本作品"}</span>${s.source_kind === "skill_md" ? '<span class="skill-source">MD</span>' : ""}</span>
    </label>`).join("") : '<div class="empty">还没有可用的 Skill。点右上角设置图标新建。</div>';
}

let editingSkillId = null;
async function openSkills() {
  await loadAgentSkills();
  renderSkillList();
  resetSkillForm();
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
  const workScope = $("skillScope").value === "work";
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
        <div class="skill-row-head"><b>${esc(s.name)}</b><span class="skill-scope">${s.work_id == null ? "通用" : "本作品"}</span>${s.source_kind === "skill_md" ? '<span class="skill-source">SKILL.md</span>' : ""}${Number(s.resource_count) ? `<span class="skill-source">${Number(s.resource_count)} 资料</span>` : ""}${Number(s.enabled) ? "" : '<span class="skill-off">已停用</span>'}</div>
        ${s.description ? `<div class="skill-desc">${esc(s.description)}</div>` : ""}
        <div class="skill-rule">${esc(s.instruction)}</div>
      </div>
      <div class="skill-row-actions">
        <button class="ic" onclick="startEditSkill(${s.id})" title="编辑 Skill">${svg("pen")}</button>
        <button class="ic" onclick="delSkill(${s.id})" title="删除 Skill">${svg("trash")}</button>
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

function toggleAISide() {
  const open = $("app").classList.toggle("ai-open");
  localStorage.setItem("aiOpen", open ? "1" : "0");
  if (open) setTimeout(() => { setAiTtsBtn(); renderAgent(); renderAgentSelection(); renderAgentSkills(); $("agentInput").focus(); }, 50);
  else if ("speechSynthesis" in window) speechSynthesis.cancel(); // 收起侧栏时停止朗读
}
function renderAgent() {
  const el = $("agentMsgs");
  if (!agentMsgs.length && !agentBusy) {
    el.innerHTML = '<div class="empty">让 AI 帮你改稿、续写、回退版本… 每步操作自动存版本，可撤销。</div>';
    return;
  }
  let html = "";
  for (const m of agentMsgs) {
    if (m.role === "user") {
      html += m.content === "[voice] 语音指令"
        ? `<div class="cm user voice">${svg("mic")} 语音指令</div>`
        : `<div class="cm user">${esc(m.content)}</div>`;
    } else if (m.role === "assistant") {
      if (m.content) html += `<div class="cm assistant">${esc(m.content)}</div>`;
    } else if (m.role === "tool") {
      let r = {}; try { r = JSON.parse(m.content); } catch (e) {}
      if (r.error) {
        html += `<div class="cm err">${esc(r.error)}</div>`;
      } else {
        const sum = r.summary || "已执行操作";
        const rid = r.undo_rid;
        const undone = rid && agentUndone.has(rid);
        const card = undone
          ? `<span class="done-tag">已撤销</span>`
          : (rid ? `<button class="undo-btn" onclick="undoAgentAction(${rid})">撤销</button>` : "");
        html += `<div class="cm action${undone ? " done" : ""}"><div class="act-bar"><span class="act-txt">${svg("pen")} ${esc(sum)}</span>${card}</div></div>`;
      }
    }
  }
  if (agentBusy) html += '<div class="cm assistant">… 思考中</div>';
  el.innerHTML = html;
  el.scrollTop = el.scrollHeight;
}
async function applyAgentResult(r, selection) {
  if (selection) clearAgentSelection();
  agentMsgs = Array.isArray(r.messages) ? r.messages : agentMsgs;
  if (r.compacted) showToast("已压缩早期对话，保留最近几轮", "ok");
  let contentChanged = false, sidebarDirty = false;
  for (const m of agentMsgs) {
    if (m.role === "tool") {
      let rr = {}; try { rr = JSON.parse(m.content); } catch (e) {}
      if (rr.changed) contentChanged = true;
      if (rr.sidebar_dirty) sidebarDirty = true;
    }
  }
  if (sidebarDirty) await loadChapters();
  else if (contentChanged && currentChapterId) await loadChapter();
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
  const text = el.value.trim();
  if (!text) return;
  el.value = "";
  // 先把正文框里未保存的手动编辑落库，避免 AI 基于旧正文操作、回显时覆盖手打内容
  if (dirty) await saveNow();
  agentMsgs.push({ role: "user", content: text });
  agentBusy = true;
  busy($("sendBtn"), true, "发送");
  renderAgent();
  const selection = agentSelection;
  const body = { text, chapter_id: currentChapterId };
  if (selection) body.selection = selection;
  if (activeAgentSkillIds.size) body.skill_ids = [...activeAgentSkillIds];
  try {
    const r = await api("/api/agent", { body });
    await applyAgentResult(r, selection);
  } catch (e) {
    agentMsgs.push({ role: "assistant", content: "出错：" + e.message });
  } finally {
    agentBusy = false;
    busy($("sendBtn"), false, "发送");
    renderAgent();
    speakLatestAgentTurn();
    $("agentInput").focus(); // 发完聚焦输入框，方便连续对话
  }
}
async function undoAgentAction(rid) {
  if (!currentChapterId || !rid) return;
  try {
    await api(`/api/chapters/${currentChapterId}/revisions/${rid}/restore`, { method: "POST" });
    agentUndone.add(rid);
    await loadChapter();
    renderAgent();
  } catch (e) { showToast("撤销失败：" + e.message, "err"); }
}
async function clearAgent() {
  try {
    await api(`/api/agent/conversation${currentChapterId != null ? "?chapter_id=" + currentChapterId : ""}`, { method: "DELETE" });
  } catch (e) { showToast("清空失败：" + e.message, "err"); return; }
  agentMsgs = []; agentUndone.clear(); renderAgent();
}
async function loadConversation() {
  // 从服务端拉取当前 用户×章节 的持久化对话；刷新或切回都能恢复上下文
  try {
    const r = await api(`/api/agent/conversation${currentChapterId != null ? "?chapter_id=" + currentChapterId : ""}`, { method: "GET" });
    agentMsgs = Array.isArray(r.messages) ? r.messages : [];
  } catch (e) { agentMsgs = []; }
  if ($("app").classList.contains("ai-open")) renderAgent();
}
function openAdmin() { location.href = "admin.html"; }

/* 智能体语音：默认直发当前模型；关闭直发时才走独立 ASR。 */

function setVoiceTrace(state, detail = "", type = "") {
  voiceTraceState = { state, detail, type };
  const host = $("voiceTrace");
  if (!host) return;
  host.classList.remove("hidden", "err", "ok", "recording");
  if (type) host.classList.add(type);
  if (state === "录音中") host.classList.add("recording");
  host.innerHTML = `${svg(state === "出错" ? "alert" : state === "录音中" ? "mic" : "check")}<span><b>${esc(state)}</b>${detail ? `<small>${esc(detail)}</small>` : ""}</span>`;
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
  if (!Ctx) throw new Error("浏览器无法转换录音格式，请关闭直发语音后使用转写模式");
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
  setVoiceTrace("正在转写", "录音会先发送到配置的 /audio/transcriptions 接口");
  try {
    const res = await fetch("/api/asr", {
      method: "POST",
      headers: {
        "Content-Type": blob.type || "audio/webm",
        ...(token ? { Authorization: "Bearer " + token } : {}),
      },
      body: blob,
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
      setVoiceTrace("转写完成", `${route} · 正在把文字发送给 AI`, "ok");
      await sendAgent();
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
  const btn = $("agentMicBtn"), el = $("agentInput"), selection = agentSelection;
  if (dirty) await saveNow();
  agentMsgs.push({ role: "user", content: "[voice] 语音指令" });
  agentBusy = true;
  btn.classList.add("is-busy");
  if (!_agentPH) _agentPH = el.placeholder;
  el.placeholder = "正在把语音发送给 AI…";
  setVoiceTrace("正在直发", "录音将直接发送给当前 Agent 模型，不经过转写");
  renderAgent();
  try {
    const wav = await recordToMonoWav(blob);
    if (wav.size > 5 * 1024 * 1024) throw new Error("录音太长，请控制在一分钟内");
    const audio = await blobToBase64(wav);
    const res = await fetch("/api/agent/audio", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: "Bearer " + token } : {}),
      },
      body: JSON.stringify({
        audio, format: "wav", chapter_id: currentChapterId, selection,
        ...(activeAgentSkillIds.size ? { skill_ids: [...activeAgentSkillIds] } : {}),
      }),
    });
    if (res.status === 401) { showLogin(); throw new Error("未登录"); }
    if (!res.ok) throw new Error(await responseError(res));
    const result = await res.json();
    setVoiceTrace("语音已发送", `${result.voice?.route || "当前 Agent 模型"} · ${result.voice?.model || ""}`, "ok");
    await applyAgentResult(result, selection);
  } catch (e) {
    agentMsgs.push({ role: "assistant", content: "语音直发失败：" + e.message });
    setVoiceTrace("出错", "直发链路：" + e.message, "err");
    showToast("语音直发失败：" + e.message, "err");
  } finally {
    agentBusy = false;
    btn.classList.remove("is-busy");
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

function entityChapterQuery() { return currentChapterId ? `?chapter_id=${currentChapterId}` : ""; }
async function loadWikiEntities() {
  if (!currentWorkId) { entitiesCache = []; entitiesCacheWorkId = null; entitiesCacheChapterId = null; return; }
  entitiesCache = await api(`/api/works/${currentWorkId}/entities${entityChapterQuery()}`, { method: "GET" });
  entitiesCacheWorkId = currentWorkId;
  entitiesCacheChapterId = currentChapterId || null;
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
      <div class="ent-h"><b>${esc(e.kind)}</b> · ${esc(e.name)}</div>
      ${e.summary ? `<div class="ent-s">${esc(e.summary)}</div>` : ""}
      ${e.detail ? `<div class="ent-d">${esc(e.detail)}</div>` : ""}
      ${e.kind === "人物" && currentChapterId ? `<div class="ent-state">${esc(characterStateBrief(e.current_state) || "截至本章未记录动态状态")}</div>` : ""}
      <div class="ent-a">
        ${e.kind === "人物" ? `<button class="ic" onclick="openCharacterState(${e.id})">状态${e.pending_count ? ` · 待确认 ${e.pending_count}` : ""}</button>` : ""}
        <button class="ic" onclick="startEditEntity(${e.id})">编辑</button>
        <button class="ic" onclick="delEntity(${e.id})">删除</button>
      </div>
    </div>`).join("") : '<div class="empty">还没有人物/设定卡片</div>';
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
  return chapters.map(chapter =>
    `<option value="${chapter.id}" ${chapter.id === characterStateChapterId ? "selected" : ""}>${esc(characterStateLabel(chapter))}</option>`
  ).join("");
}
async function openCharacterState(eid) {
  if (!chapters.length) { showToast("请先创建章节", "err"); return; }
  characterStateEntityId = eid;
  characterStateChapterId = currentChapterId || chapters[chapters.length - 1].id;
  characterStateProposalId = null;
  $("characterStateOverlay").classList.remove("hidden");
  await loadCharacterState();
}
function closeCharacterState() {
  $("characterStateOverlay").classList.add("hidden");
  characterStateEntityId = null;
  characterStateData = null;
  characterStateProposalId = null;
}
async function changeCharacterStateChapter() {
  characterStateChapterId = +$("characterStateChapter").value || null;
  characterStateProposalId = null;
  await loadCharacterState();
}
function renderCharacterState(data) {
  characterStateData = data;
  const entity = data.entity;
  $("characterStateTitle").textContent = `${entity.name} · 动态卡`;
  $("characterStateChapter").innerHTML = characterStateOptions();
  $("characterStateMeta").textContent = characterStateLabel(data.target_chapter);
  $("characterStateBase").innerHTML = `
    <div><b>基础设定</b>${entity.summary ? ` · ${esc(entity.summary)}` : ""}</div>
    ${entity.detail ? `<div>${esc(entity.detail)}</div>` : ""}`;
  const version = data.state_version;
  $("characterStateSource").textContent = version
    ? `${characterStateSource(version.source)} · 生效于${characterStateLabel({ ord: version.chapter_ord, title: version.chapter_title })}`
    : "尚无已确认状态";
  setCharacterStateForm(data.current_state || {});
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
  const history = data.history || [];
  $("characterStateHistory").innerHTML = history.length ? history.map(item => `
    <div class="character-state-history-item">
      <div><b>${esc(characterStateLabel({ ord: item.chapter_ord, title: item.chapter_title }))}</b><span>${esc(characterStateSource(item.source))}</span></div>
      ${item.change_summary ? `<p>${esc(item.change_summary)}</p>` : ""}
      ${item.evidence ? `<small>${esc(item.evidence)}</small>` : ""}
      ${characterStateSnapshot(item.state)}
    </div>`).join("") : '<div class="empty">尚无成长记录</div>';
}
async function loadCharacterState() {
  if (!characterStateEntityId || !characterStateChapterId) return;
  try {
    const data = await api(`/api/entities/${characterStateEntityId}/state-history?chapter_id=${characterStateChapterId}`, { method: "GET" });
    renderCharacterState(data);
  } catch (e) {
    $("characterStateMsg").textContent = e.message;
  }
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
const plotStateSourceLabels = { manual: "手动记录", ai_confirmed: "AI 提议已采纳", ai_edited: "AI 提议编辑后采纳" };

function openStoryDrawer(tab = "plot") {
  if (!currentWorkId) { showToast("请先创建或选择一个作品", "err"); return; }
  $("app").classList.add("story-open");
  selectStoryTab(tab);
}
function toggleStoryDrawer() {
  if ($("app").classList.contains("story-open")) closeStoryDrawer();
  else openStoryDrawer(storyTab);
}
function closeStoryDrawer() { $("app").classList.remove("story-open"); }
async function selectStoryTab(tab) {
  storyTab = ["plot", "workflow", "relations", "alerts", "context"].includes(tab) ? tab : "plot";
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
    if (plotStateProposalId === pid) setPlotStateForm(plotStateData?.current_state || {});
    await loadPlotState();
    showToast("已忽略该剧情提议");
  } catch (e) { $("plotStateMsg").textContent = e.message; }
}
async function savePlotState() {
  if (!currentWorkId || !plotStateChapterId) return;
  const button = $("plotStateSaveBtn");
  const confirming = !!plotStateProposalId;
  busy(button, true, confirming ? "确认中" : "保存中");
  const body = { state: plotStateFormValue(), change_summary: $("plotStateSummary").value.trim(),
    evidence: $("plotStateEvidence").value.trim() };
  try {
    if (plotStateProposalId) await api(`/api/plot-state-proposals/${plotStateProposalId}/accept`, { body });
    else await api(`/api/works/${currentWorkId}/plot-state-versions`, { body: { ...body, chapter_id: plotStateChapterId } });
    plotStateProposalId = null;
    await loadPlotState();
    showToast("剧情状态已保存", "ok");
  } catch (e) { $("plotStateMsg").textContent = e.message; }
  finally { busy(button, false, "保存为本章剧情状态"); }
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
  if (dirty) await saveNow();
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
    <div class="consistency-alert severity-${esc(item.severity || "notice")}">
      <div><span class="alert-mark">${svg(item.severity === "critical" ? "alert" : item.severity === "warning" ? "alert" : "eye")}</span><b>${esc(item.title)}</b><small>${esc(item.category || "连续性")}</small></div>
      ${item.detail ? `<p>${esc(item.detail)}</p>` : ""}
      ${item.evidence ? `<small class="alert-evidence">${esc(item.evidence)}</small>` : ""}
      ${item.suggestion ? `<p class="alert-suggestion">${esc(item.suggestion)}</p>` : ""}
      ${item.status === "open" ? `<button class="ic" onclick="dismissConsistencyAlert(${item.id})" title="忽略此提醒">${svg("x")}</button>` : `<span class="alert-dismissed">已忽略</span>`}
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
  $("contextMeta").textContent = `${data.engine} · ${chapter} · ${data.model || "未设置模型"}`;
  $("contextSelection").innerHTML = contextTextBlock("选区", data.selection?.present ? data.selection.text : "本回合没有选区");
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
      chapter_id: currentChapterId, selection: agentSelection,
      skill_ids: activeAgentSkillIds.size ? [...activeAgentSkillIds] : [],
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
  if (i > 0) { if (dirty) await saveNow(); currentChapterId = chapters[i - 1].id; await loadChapter(); renderReader(); renderTree(); }
}
async function readerNext() {
  const i = chapters.findIndex(c => c.id === currentChapterId);
  if (i >= 0 && i < chapters.length - 1) { if (dirty) await saveNow(); currentChapterId = chapters[i + 1].id; await loadChapter(); renderReader(); renderTree(); }
}
async function readerJumpTo() {
  const cid = +$("readerJump").value;
  if (cid && cid !== currentChapterId) { if (dirty) await saveNow(); currentChapterId = cid; await loadChapter(); renderReader(); renderTree(); }
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
  if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); saveNow(); }
  if ((e.ctrlKey || e.metaKey) && e.key === "f") { e.preventDefault(); toggleFind(); }
});
$("content").addEventListener("input", onContentInput);
$("content").addEventListener("keyup", () => { typewriterCenter(); updateSelectionTools(); });
$("content").addEventListener("click", () => { typewriterCenter(); updateSelectionTools(); });
$("content").addEventListener("mouseup", updateSelectionTools);
$("content").addEventListener("select", updateSelectionTools);
$("notes").addEventListener("input", onNotesInput);
$("chapTitle").addEventListener("input", () => { dirty = true; updateSaveStat("未保存"); clearTimeout(saveTimer); saveTimer = setTimeout(saveNow, 1500); });
document.addEventListener("click", (e) => {
  const m = $("moreMenu");
  if (m && !m.classList.contains("hidden") && !e.target.closest(".menu-wrap")) m.classList.add("hidden");
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
