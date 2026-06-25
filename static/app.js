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
let rec = null, micOn = false, draftBuffer = "";

// 自动保存
let saveTimer = null, dirty = false;
// 查找
let findPos = [], findIdx = -1;
// 拖拽
let dragCid = null;

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
  if (!res.ok) throw new Error((await res.text()) || res.statusText);
  return res.json();
}

const tail = (s, n) => (!s ? "" : s.length > n ? s.slice(-n) : s);
const charCount = (s) => (s || "").replace(/\s/g, "").length;
const esc = (s) => (s || "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
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
  setupRec();
  const me = await api("/api/me", { method: "GET" });
  $("meName").textContent = me.username ? `${me.username} · 目录` : "目录";
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
  if (!works.length) { tree.innerHTML = '<div class="empty">点「＋作品」开始</div>'; return; }
  tree.innerHTML = works.map(w => {
    const open = w.id === currentWorkId;
    const items = open ? chapters.map(c => `
      <div class="chap ${c.id === currentChapterId ? "cur" : ""}" draggable="true"
           onclick="selectChapter(${c.id})"
           ondragstart="dragStart(event,${c.id})"
           ondragover="dragOver(event)"
           ondrop="dragDrop(event,${c.id})">
        <span class="drag">⠿</span>
        <span class="c-title">${esc(c.title) || "(无标题)"}</span>
        <span class="c-wc">${(c.chars || 0)}字</span>
        <button class="c-del" onclick="event.stopPropagation();delChapter(${c.id})" title="删除">✕</button>
      </div>`).join("") : "";
    return `
      <div class="work ${open ? "open" : ""}">
        <div class="w-row" onclick="selectWork(${w.id})">
          <span class="w-title">${esc(w.title)}</span>
          <button class="c-del" onclick="event.stopPropagation();delWork(${w.id})" title="删除">✕</button>
        </div>
        ${open ? `<div class="chaps">${items || '<div class="empty">点「＋章」</div>'}</div>
                 <button class="add-chap" onclick="newChapter(${w.id})">＋ 新章</button>` : ""}
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
  if (!currentWorkId) { chapters = []; renderTree(); updateWC(); return; }
  chapters = await api(`/api/works/${currentWorkId}/chapters`, { method: "GET" });
  if (!chapters.find(c => c.id === currentChapterId)) {
    currentChapterId = chapters.length ? chapters[chapters.length - 1].id : null;
  }
  if (currentChapterId) await loadChapter();
  else { $("content").value = ""; $("chapTitle").value = ""; $("notes").value = ""; }
  renderTree(); updateWC();
}

async function selectChapter(cid) {
  if (dirty) await saveNow();
  currentChapterId = cid;
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
}

async function newWork() {
  const title = prompt("作品名", "新作品");
  if (!title) return;
  const r = await api("/api/works", { body: { title } });
  currentWorkId = r.id; currentChapterId = null;
  await loadWorks();
}

async function newChapter(wid) {
  const title = prompt("章节名", "新章节");
  if (!title) return;
  const r = await api(`/api/works/${wid}/chapters`, { body: { title } });
  currentWorkId = wid; currentChapterId = r.id;
  await loadChapters();
}

async function delChapter(cid) {
  if (!confirm("删除这一章？不可恢复。")) return;
  if (dirty) await saveNow();
  await api(`/api/chapters/${cid}`, { method: "DELETE" });
  currentChapterId = null;
  await loadChapters();
}

async function delWork(wid) {
  if (!confirm("删除整个作品及其所有章节？不可恢复。")) return;
  await api(`/api/works/${wid}`, { method: "DELETE" });
  currentWorkId = null; currentChapterId = null;
  await loadWorks();
}

/* ---------- 拖拽排序 ---------- */

function dragStart(ev, cid) { dragCid = cid; ev.dataTransfer.effectAllowed = "move"; }
function dragOver(ev) { ev.preventDefault(); ev.dataTransfer.dropEffect = "move"; }
async function dragDrop(ev, targetCid) {
  ev.preventDefault();
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
}
function onNotesInput() { dirty = true; clearTimeout(saveTimer); saveTimer = setTimeout(saveNow, 1500); }

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
  document.querySelectorAll(".mode").forEach(b => b.classList.toggle("active", b.dataset.mode === m));
  $("genBtn").classList.toggle("hidden", !(m === "扩写" || m === "续写"));
  draftBuffer = ""; showDraft("");
}

/* ---------- 语音 ---------- */

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
  rec.onend = () => { if (micOn) { try { rec.start(); } catch (e) {} } else setMic(false); };
  rec.onerror = (e) => { $("micStatus").textContent = "错误：" + e.error; };
}

function toggleMic() {
  if (!rec) { alert("浏览器不支持语音识别，请用安卓 Chrome"); return; }
  if (micOn) { micOn = false; try { rec.stop(); } catch (e) {} setMic(false); }
  else { micOn = true; draftBuffer = ""; try { rec.start(); } catch (e) {} setMic(true); }
}
function setMic(on) {
  $("micBtn").textContent = on ? "⏸ 停止" : "🎤 开始说";
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

/* ---------- AI 处理（本地追加，不覆盖正文，避免丢手打内容） ---------- */

async function processAndAppend(text) {
  if (!currentChapterId) { alert("先选择或新建一个章节"); return; }
  const ctx = tail($("content").value, 1500);
  setMicStatus("处理中…");
  try {
    const r = await api("/api/process", { body: { mode, text, context: ctx, chapter_id: currentChapterId } });
    appendText(r.result);          // 本地追加结果，保留正在手打的内容
    onContentInput();
    setMicStatus("");
  } catch (e) { setMicStatus("出错：" + e.message); }
}

async function generate() {
  if (!draftBuffer) { setMicStatus("先说点内容再生成"); return; }
  const text = draftBuffer; draftBuffer = ""; showDraft("");
  await processAndAppend(text);
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
  if (!currentChapterId) { alert("先选择章节"); return; }
  const ta = $("content");
  const at = ta.selectionStart;
  if (at <= 0) { alert("把光标放在要拆分的位置（拆分点之后的内容会进新章节）"); return; }
  const title = prompt("新章节标题", "新章节");
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

async function showRevisions() {
  if (!currentChapterId) return;
  const list = await api(`/api/chapters/${currentChapterId}/revisions`, { method: "GET" });
  $("revList").innerHTML = list.length ? list.map(r => `
    <div class="rev">
      <span>${new Date(r.created_at * 1000).toLocaleString()} · ${r.chars}字</span>
      <button class="ic" onclick="restoreRevision(${r.id})">恢复</button>
    </div>`).join("") : '<div class="empty">还没有存过版本</div>';
  $("revOverlay").classList.remove("hidden");
}
function closeRevisions() { $("revOverlay").classList.add("hidden"); }
async function restoreRevision(rid) {
  if (!confirm("恢复此版本？当前正文会被覆盖（可先存个版本备份）")) return;
  const c = await api(`/api/chapters/${currentChapterId}/revisions/${rid}/restore`, { method: "POST" });
  $("content").value = c.content || ""; $("chapTitle").value = c.title || "";
  onContentInput();
  closeRevisions();
  flash("已恢复");
}

/* ---------- 导出 ---------- */

async function exportChap(fmt) {
  if (!fmt) return;
  if (!currentChapterId) { alert("先选章节"); return; }
  try {
    const res = await fetch(`/api/chapters/${currentChapterId}/export?format=${fmt}`, { headers: { Authorization: "Bearer " + token } });
    if (!res.ok) throw new Error("导出失败");
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = ($("chapTitle").value || "chapter") + "." + fmt;
    a.click(); URL.revokeObjectURL(a.href);
  } catch (e) { alert(e.message); }
}

/* ---------- 大模型设置 ---------- */

async function openSettings() {
  try {
    const s = await api("/api/settings", { method: "GET" });
    $("setBaseUrl").value = s.base_url || "";
    $("setModel").value = s.model || "";
    // key 不回传明文：已填则用掩码占位提示，留空表示不改
    $("setApiKey").value = s.api_key_masked || "";
    $("setApiKey").placeholder = s.has_key ? `${s.api_key_masked}（留空=不改）` : "sk-…";
    $("setMsg").textContent = "";
  } catch (e) { $("setMsg").textContent = e.message; }
  $("setOverlay").classList.remove("hidden");
}
function closeSettings() { $("setOverlay").classList.add("hidden"); }
async function saveSettings() {
  const base_url = $("setBaseUrl").value.trim();
  const model = $("setModel").value.trim();
  let api_key = $("setApiKey").value.trim();
  // 若用户没动 key 输入框（仍是掩码占位），传空让后端保留旧值
  if (api_key.startsWith("****")) api_key = "";
  try {
    await api("/api/settings", { body: { base_url, api_key, model } });
    $("setMsg").textContent = "已保存";
    setTimeout(closeSettings, 600);
  } catch (e) { $("setMsg").textContent = e.message; }
}

/* ---------- 阅读视图 ---------- */

let readerFontPx = +localStorage.getItem("rFont") || 19;
let readerLH = +localStorage.getItem("rLH") || 2;
let ttsPlaying = false;

function toggleRead() {
  const r = $("reader");
  const closing = !r.classList.contains("hidden");
  if (closing && ttsPlaying) readerToggleTTS();
  r.classList.toggle("hidden");
  if (!r.classList.contains("hidden")) renderReader();
}
function renderReader() {
  $("readerTitle").textContent = $("chapTitle").value || "(无标题)";
  const v = $("readView");
  v.style.fontSize = readerFontPx + "px";
  v.style.lineHeight = readerLH;
  v.innerHTML = esc($("content").value).replace(/\n/g, "<br>");
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
function readerToggleTTS() {
  if (!("speechSynthesis" in window)) { alert("浏览器不支持朗读"); return; }
  if (ttsPlaying) { speechSynthesis.cancel(); ttsPlaying = false; $("ttsBtn").textContent = "▶朗读"; return; }
  const u = new SpeechSynthesisUtterance($("content").value);
  u.lang = "zh-CN"; u.rate = 1;
  u.onend = () => { ttsPlaying = false; $("ttsBtn").textContent = "▶朗读"; };
  speechSynthesis.speak(u); ttsPlaying = true; $("ttsBtn").textContent = "⏹停";
}

/* ---------- 布局 ---------- */

function toggleSidebar() { $("app").classList.toggle("side-open"); }
function toggleFocus() { $("app").classList.toggle("focus"); }

/* ---------- 全局事件 ---------- */

document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); saveNow(); }
  if ((e.ctrlKey || e.metaKey) && e.key === "f") { e.preventDefault(); toggleFind(); }
});
$("content").addEventListener("input", onContentInput);
$("notes").addEventListener("input", onNotesInput);
$("chapTitle").addEventListener("input", () => { dirty = true; clearTimeout(saveTimer); saveTimer = setTimeout(saveNow, 1500); });

/* ---------- 启动 ---------- */

(async function start() {
  // 根据是否开放注册，决定显示注册入口
  try {
    const s = await api("/api/signup-status", { method: "GET" });
    if (s.enabled) $("toRegister").classList.remove("hidden");
  } catch (e) {}
  if (token) {
    try { await init(); return; } catch (e) { token = ""; localStorage.removeItem("token"); }
  }
  showLogin();
})();
