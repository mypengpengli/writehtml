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
// AI 助手（agent）
let agentMsgs = [];
let agentBusy = false;
let agentUndone = new Set();

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
          <button class="ic" onclick="event.stopPropagation();openWorkNotes(${w.id})" title="作品设定">📋</button>
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
  agentMsgs = []; agentUndone.clear();  // 切章清空 agent 上下文，避免跨章错乱
  if ($("app").classList.contains("ai-open")) renderAgent();
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
  if (!confirm("移到回收站？（可找回，点 🗑 彻底删除）")) return;
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

/* ---------- 回收站 ---------- */
async function openTrash() {
  if (!currentWorkId) { alert("先选一个作品"); return; }
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
  if (!confirm("彻底删除？这一步不可恢复。")) return;
  await api(`/api/chapters/${cid}/purge`, { method: "POST" });
  await openTrash();
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
  typewriterCenter();
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

// 选区操作：缩写 / 改写风格。对正文里选中的一段原地替换，可 Ctrl+Z 撤销
async function processSelection(m, style) {
  const el = $("content");
  if (!currentChapterId) { alert("先选择或新建一个章节"); return; }
  const s = el.selectionStart, e = el.selectionEnd;
  if (s == null || s === e) { alert("先在正文里选中一段文字再操作"); return; }
  setMicStatus("处理中…");
  try {
    const r = await api("/api/process", {
      body: { mode: m, text: el.value.slice(s, e), context: tail(el.value, 1500), chapter_id: currentChapterId, style }
    });
    el.setRangeText(r.result, s, e, "end");   // 原地替换选区，保留撤销历史
    onContentInput();                          // 触发自动保存（走 PUT /api/chapters/{cid}）
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
      <button class="ic" onclick="openDiff(${r.id})" title="和当前正文逐行对比增删">对比</button>
      <button class="ic" onclick="restoreRevision(${r.id})">恢复</button>
      <button class="ic" onclick="recoverFromRevision(${r.id})" title="让 AI 读这版旧草稿，把被删掉的好内容找回成段落追加">AI找回</button>
    </div>`).join("") : '<div class="empty">还没有存过版本</div>';
  $("revOverlay").classList.remove("hidden");
}
function closeRevisions() { $("revOverlay").classList.add("hidden"); }
async function openDiff(rid) {
  if (!currentChapterId) return;
  setMicStatus("对比中…");
  try {
    const d = await api(`/api/chapters/${currentChapterId}/revisions/${rid}/diff`, { method: "GET" });
    $("diffSub").textContent = `${d.rev_title || "(无标题)"}  →  ${d.cur_title || "(当前)"}  ·  ${new Date(d.rev_at * 1000).toLocaleString()}`;
    $("diffBody").innerHTML = (d.ops || []).map(o => {
      if (o.op === "equal")  return `<div class="d-eq">${esc(o.old)}</div>`;
      if (o.op === "delete") return `<div class="d-del">－ ${esc(o.old)}</div>`;
      if (o.op === "insert") return `<div class="d-ins">＋ ${esc(o.new)}</div>`;
      // replace：先旧（红）后新（绿）
      return `<div class="d-del">－ ${esc(o.old)}</div><div class="d-ins">＋ ${esc(o.new)}</div>`;
    }).join("") || '<div class="empty">无差异</div>';
    $("diffOverlay").classList.remove("hidden");
  } catch (e) { alert("对比失败：" + e.message); }
  setMicStatus("");
}
function closeDiff() { $("diffOverlay").classList.add("hidden"); }
async function restoreRevision(rid) {
  if (!confirm("恢复此版本？当前正文会被覆盖（可先存个版本备份）")) return;
  const c = await api(`/api/chapters/${currentChapterId}/revisions/${rid}/restore`, { method: "POST" });
  $("content").value = c.content || ""; $("chapTitle").value = c.title || "";
  onContentInput();
  closeRevisions();
  flash("已恢复");
}
async function recoverFromRevision(rid) {
  if (!currentChapterId) return;
  if (dirty) await saveNow();
  setMicStatus("AI 找回中…");
  try {
    const r = await api("/api/process", {
      body: { mode: "找回", chapter_id: currentChapterId, revision_id: rid },
    });
    appendText(r.result);          // 找回的段落追加进正文，非破坏式
    onContentInput();
    closeRevisions();
    flash("已找回内容并追加");
  } catch (e) { setMicStatus("出错：" + e.message); }
}

/* ---------- 导出 ---------- */

async function exportChap(fmt) {
  if (!fmt) return;
  try {
    let url, name;
    if (fmt.startsWith("work-")) {
      const f = fmt.slice(5);
      if (!currentWorkId) { alert("先选作品"); return; }
      url = `/api/works/${currentWorkId}/export?format=${f}`;
      const w = works.find(x => x.id === currentWorkId);
      name = ((w && w.title) || "work") + "." + f;
    } else {
      if (!currentChapterId) { alert("先选章节"); return; }
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
  try {
    const r = await api("/api/process", { body: { mode: "校验", chapter_id: currentChapterId } });
    $("aiResult").textContent = r.result;
  } catch (e) { $("aiResult").textContent = "出错：" + e.message; }
}
async function aiSynopsis() {
  if (!currentChapterId) { $("aiResult").textContent = "先选一章"; return; }
  $("aiResult").textContent = "生成摘要中…";
  try {
    const r = await api("/api/process", { body: { mode: "摘要", chapter_id: currentChapterId } });
    $("notes").value = r.result;                 // 摘要填进备注 → 成为续写/扩写/找回的上下文
    if ($("notesBar").classList.contains("hidden")) toggleNotes();
    onNotesInput();
    $("aiResult").textContent = "已填入备注：\n\n" + r.result;
  } catch (e) { $("aiResult").textContent = "出错：" + e.message; }
}

/* ---------- AI 助手（常驻侧栏，对话即操作，自动存版本可撤销） ---------- */

function toggleAISide() {
  const open = $("app").classList.toggle("ai-open");
  localStorage.setItem("aiOpen", open ? "1" : "0");
  if (open) setTimeout(() => { renderAgent(); $("agentInput").focus(); }, 50);
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
      html += `<div class="cm user">${esc(m.content)}</div>`;
    } else if (m.role === "assistant") {
      if (m.content) html += `<div class="cm assistant">${esc(m.content)}</div>`;
    } else if (m.role === "tool") {
      let r = {}; try { r = JSON.parse(m.content); } catch (e) {}
      if (r.error) {
        html += `<div class="cm err">⚠ ${esc(r.error)}</div>`;
      } else {
        const sum = r.summary || "已执行操作";
        const rid = r.undo_rid;
        const undone = rid && agentUndone.has(rid);
        const card = undone
          ? `<span class="done-tag">已撤销</span>`
          : (rid ? `<button class="undo-btn" onclick="undoAgentAction(${rid})">撤销</button>` : "");
        html += `<div class="cm action${undone ? " done" : ""}"><div class="act-bar"><span class="act-txt">✏️ ${esc(sum)}</span>${card}</div></div>`;
      }
    }
  }
  if (agentBusy) html += '<div class="cm assistant">… 思考中</div>';
  el.innerHTML = html;
  el.scrollTop = el.scrollHeight;
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
  renderAgent();
  try {
    const r = await api("/api/agent", { body: { messages: agentMsgs, chapter_id: currentChapterId } });
    agentMsgs = Array.isArray(r.messages) ? r.messages : agentMsgs;
    // 检测工具是否改动正文/侧栏，决定刷新哪块
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
  } catch (e) {
    agentMsgs.push({ role: "assistant", content: "出错：" + e.message });
  } finally {
    agentBusy = false;
    renderAgent();
  }
}
async function undoAgentAction(rid) {
  if (!currentChapterId || !rid) return;
  try {
    await api(`/api/chapters/${currentChapterId}/revisions/${rid}/restore`, { method: "POST" });
    agentUndone.add(rid);
    await loadChapter();
    renderAgent();
  } catch (e) { alert("撤销失败：" + e.message); }
}
function clearAgent() { agentMsgs = []; agentUndone.clear(); renderAgent(); }

/* ---------- 实体卡片 wiki（人物/地点…，喂给 AI 当结构化设定） ---------- */

let entitiesCache = [];
let entitiesCacheWorkId = null;
let editingEntityId = null;

async function openWiki() {
  if (!currentWorkId) { alert("先选一个作品"); return; }
  try {
    entitiesCache = await api(`/api/works/${currentWorkId}/entities`, { method: "GET" });
    entitiesCacheWorkId = currentWorkId;
    renderWikiList();
    resetEntityForm();
    $("wikiOverlay").classList.remove("hidden");
  } catch (e) { alert("加载失败：" + e.message); }
}
function closeWiki() { $("wikiOverlay").classList.add("hidden"); }
function renderWikiList() {
  $("wikiList").innerHTML = entitiesCache.length ? entitiesCache.map(e => `
    <div class="ent">
      <div class="ent-h"><b>${esc(e.kind)}</b> · ${esc(e.name)}</div>
      ${e.summary ? `<div class="ent-s">${esc(e.summary)}</div>` : ""}
      ${e.detail ? `<div class="ent-d">${esc(e.detail)}</div>` : ""}
      <div class="ent-a">
        <button class="ic" onclick="startEditEntity(${e.id})">编辑</button>
        <button class="ic" onclick="delEntity(${e.id})">删除</button>
      </div>
    </div>`).join("") : '<div class="empty">还没有实体卡片</div>';
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
    entitiesCache = await api(`/api/works/${currentWorkId}/entities`, { method: "GET" });
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
  if (!confirm("删除这张实体卡片？")) return;
  await api(`/api/entities/${eid}`, { method: "DELETE" });
  entitiesCache = await api(`/api/works/${currentWorkId}/entities`, { method: "GET" });
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
    + (ent.detail ? `<div class="mp-det">${esc(ent.detail)}</div>` : "");
  pop.classList.remove("hidden");
  const r = m.getBoundingClientRect();
  pop.style.left = Math.max(8, Math.min(r.left, innerWidth - 300)) + "px";
  pop.style.top = (r.bottom + 6) + "px";
}
function hideMentionPop() { $("mentionPop").classList.add("hidden"); }
async function ensureEntities() {
  if (currentWorkId && entitiesCacheWorkId !== currentWorkId) {
    try { entitiesCache = await api(`/api/works/${currentWorkId}/entities`, { method: "GET" });
      entitiesCacheWorkId = currentWorkId; } catch (e) { entitiesCache = []; }
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
function toggleFocus() { $("app").classList.toggle("focus"); setTimeout(typewriterCenter, 30); }

/* 顶栏「⋯更多」下拉 */
function toggleMoreMenu(e) { e?.stopPropagation(); $("moreMenu").classList.toggle("hidden"); }
function closeMoreMenu() { $("moreMenu").classList.add("hidden"); }

/* 明暗主题：默认跟随系统，手动切换后记忆 */
function applyTheme(t) {
  if (t === "dark" || t === "light") document.documentElement.setAttribute("data-theme", t);
  else document.documentElement.removeAttribute("data-theme");
  const dark = t === "dark" || (t == null && matchMedia("(prefers-color-scheme: dark)").matches);
  const b = $("themeBtn"); if (b) b.textContent = dark ? "☀" : "🌙";
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
  const b = $("fontBtn"); if (b) b.textContent = serif ? "宋" : "文";
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
$("content").addEventListener("keyup", typewriterCenter);
$("content").addEventListener("click", typewriterCenter);
$("notes").addEventListener("input", onNotesInput);
$("chapTitle").addEventListener("input", () => { dirty = true; clearTimeout(saveTimer); saveTimer = setTimeout(saveNow, 1500); });
document.addEventListener("click", (e) => {
  const m = $("moreMenu");
  if (m && !m.classList.contains("hidden") && !e.target.closest(".menu-wrap")) m.classList.add("hidden");
});

/* ---------- 启动 ---------- */

(async function start() {
  applyTheme(localStorage.getItem("theme"));
  applyFont(localStorage.getItem("fontSerif") === "1");
  if (localStorage.getItem("aiOpen") === "1") $("app").classList.add("ai-open");
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
