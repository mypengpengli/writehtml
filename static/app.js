/* 前端逻辑：登录、语音识别、模式切换、AI 处理、阅读 */
const $ = (id) => document.getElementById(id);

let token = localStorage.getItem("token") || "";
let currentChapterId = null;
let mode = "转写";

// 语音相关
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let rec = null;
let micOn = false;
let draftBuffer = ""; // 扩写/续写模式下累积的口述

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
  if (res.status === 401) {
    showLogin();
    throw new Error("未登录");
  }
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || res.statusText);
  }
  return res.json();
}

function tail(s, n) {
  if (!s) return "";
  return s.length > n ? s.slice(-n) : s;
}

/* ---------- 登录 ---------- */

function showLogin() {
  $("app").classList.add("hidden");
  $("login").classList.remove("hidden");
}

function showApp() {
  $("login").classList.add("hidden");
  $("app").classList.remove("hidden");
}

async function doLogin() {
  const pwd = $("pwd").value;
  try {
    const r = await api("/api/login", { body: { password: pwd } });
    token = r.token;
    localStorage.setItem("token", token);
    $("loginMsg").textContent = "";
    await init();
  } catch (e) {
    $("loginMsg").textContent = "密码错误或出错";
  }
}

async function doLogout() {
  try { await api("/api/logout", { method: "POST" }); } catch (e) {}
  token = "";
  localStorage.removeItem("token");
  showLogin();
}

/* ---------- 作品 / 章节 ---------- */

async function init() {
  showApp();
  await loadWorks();
  setupRec();
}

async function loadWorks() {
  const works = await api("/api/works", { method: "GET" });
  const sel = $("workSel");
  sel.innerHTML = works.map(w => `<option value="${w.id}">${esc(w.title)}</option>`).join("");
  if (works.length) await loadChapters();
  else $("chapSel").innerHTML = "";
}

async function newWork() {
  const title = prompt("作品名", "新作品");
  if (!title) return;
  await api("/api/works", { body: { title } });
  await loadWorks();
}

async function loadChapters() {
  const wid = $("workSel").value;
  if (!wid) return;
  const chaps = await api(`/api/works/${wid}/chapters`, { method: "GET" });
  const sel = $("chapSel");
  sel.innerHTML = chaps.map(c => `<option value="${c.id}">${esc(c.title)}</option>`).join("");
  if (chaps.length) await loadChapter();
  else { currentChapterId = null; $("content").value = ""; }
}

async function newChapter() {
  const wid = $("workSel").value;
  if (!wid) { alert("先建一个作品"); return; }
  const title = prompt("章节名", "第X章");
  if (!title) return;
  await api(`/api/works/${wid}/chapters`, { body: { title } });
  await loadChapters();
}

async function loadChapter() {
  const cid = $("chapSel").value;
  if (!cid) return;
  const c = await api(`/api/chapters/${cid}`, { method: "GET" });
  currentChapterId = c.id;
  $("content").value = c.content || "";
  $("readerTitle").textContent = c.title;
}

async function saveContent() {
  if (!currentChapterId) return;
  await api(`/api/chapters/${currentChapterId}`, {
    method: "PUT",
    body: { content: $("content").value },
  });
  flash("已保存");
}

/* ---------- 模式 ---------- */

function setMode(m) {
  mode = m;
  document.querySelectorAll(".mode").forEach(b =>
    b.classList.toggle("active", b.dataset.mode === m)
  );
  // 扩写/续写需要手动「生成」；转写/润色随说随落
  $("genBtn").classList.toggle("hidden", !(m === "扩写" || m === "续写"));
  draftBuffer = "";
  showDraft("");
}

/* ---------- 语音识别 ---------- */

function setupRec() {
  if (!SR) {
    $("micStatus").textContent = "浏览器不支持语音识别（用安卓 Chrome）";
    return;
  }
  rec = new SR();
  rec.lang = "zh-CN";
  rec.continuous = true;
  rec.interimResults = true;

  rec.onresult = (e) => {
    let interim = "";
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const res = e.results[i];
      if (res.isFinal) onFinal(res[0].transcript);
      else interim += res[0].transcript;
    }
    // 实时显示：累积缓冲 + 正在说的临时文本
    showDraft(draftBuffer + interim);
  };

  rec.onend = () => {
    if (micOn) {
      // 安卓静音会自动停，保活重启
      try { rec.start(); } catch (e) {}
    } else {
      setMic(false);
    }
  };

  rec.onerror = (e) => {
    $("micStatus").textContent = "错误：" + e.error;
  };
}

function toggleMic() {
  if (!rec) { alert("浏览器不支持语音识别，请用安卓 Chrome"); return; }
  if (micOn) {
    micOn = false;
    try { rec.stop(); } catch (e) {}
    setMic(false);
  } else {
    micOn = true;
    draftBuffer = "";
    try { rec.start(); } catch (e) {}
    setMic(true);
  }
}

function setMic(on) {
  $("micBtn").textContent = on ? "⏸ 停止" : "🎤 开始说";
  $("micBtn").classList.toggle("on", on);
  $("micStatus").textContent = on ? "正在听…" : "";
}

function onFinal(text) {
  text = text.trim();
  if (!text) return;
  if (mode === "转写" || mode === "润色") {
    // 随说随落
    processAndAppend(text);
  } else {
    // 扩写/续写：先累积，等「生成」
    draftBuffer += (draftBuffer ? "" : "") + text;
    showDraft(draftBuffer);
  }
}

function showDraft(s) {
  const el = $("draft");
  if (!s) {
    el.textContent = "这里显示你正在说的话…";
    el.classList.remove("active");
  } else {
    el.textContent = s;
    el.classList.add("active");
  }
}

/* ---------- AI 处理 ---------- */

async function processAndAppend(text) {
  if (!currentChapterId) { alert("先选择或新建一个章节"); return; }
  const ctx = tail($("content").value, 1500);
  setMicStatus("处理中…");
  try {
    const r = await api("/api/process", {
      body: { mode, text, context: ctx, chapter_id: currentChapterId },
    });
    if (r.content != null) {
      $("content").value = r.content;
    } else if (r.result) {
      appendText(r.result);
    }
    setMicStatus("");
  } catch (e) {
    setMicStatus("出错：" + e.message);
  }
}

async function generate() {
  if (!draftBuffer) { setMicStatus("先说点内容再生成"); return; }
  const text = draftBuffer;
  draftBuffer = "";
  showDraft("");
  await processAndAppend(text);
}

function appendText(t) {
  const el = $("content");
  if (el.value && !el.value.endsWith("\n")) el.value += "\n";
  el.value += t;
  el.scrollTop = el.scrollHeight;
}

async function undo() {
  if (!currentChapterId) return;
  const c = await api(`/api/chapters/${currentChapterId}/undo`, { method: "POST" });
  $("content").value = c.content || "";
  flash("已撤销最近一段");
}

function setMicStatus(s) { $("micStatus").textContent = s; }

/* ---------- 阅读视图 ---------- */

function toggleRead() {
  const r = $("reader");
  const on = r.classList.toggle("hidden");
  if (!r.classList.contains("hidden")) {
    $("readView").innerHTML = esc($("content").value).replace(/\n/g, "<br>");
  }
}

/* ---------- 小工具 ---------- */

function esc(s) {
  return (s || "").replace(/[&<>"]/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])
  );
}

let flashTimer;
function flash(msg) {
  $("micStatus").textContent = msg;
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => setMicStatus(""), 1500);
}

/* ---------- 启动 ---------- */

(async function start() {
  if (token) {
    try { await init(); return; } catch (e) { token = ""; localStorage.removeItem("token"); }
  }
  showLogin();
})();
