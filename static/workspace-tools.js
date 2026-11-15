/* Writing shortcuts and bounded relationship visualization; no external libraries. */
const chapterOrderBusy = new Set();
let chapterMenuState = null;

async function commitChapterOrder(ids) {
  const workId = currentWorkId, list = chapters;
  if (chapterOrderBusy.has(workId)) { showToast('正在保存章节顺序，请稍候', 'err'); return false; }
  if (ids.length !== list.length || new Set(ids).size !== ids.length || list.some(c => !ids.includes(c.id))) return false;
  const snapshot = list.map(c => ({...c})), rank = new Map(ids.map((id, i) => [id, i]));
  chapterOrderBusy.add(workId);
  list.sort((a, b) => rank.get(a.id) - rank.get(b.id));
  list.forEach((c, i) => { c.ord = i + 1; });
  closeChapterMenu(); renderTree();
  try {
    await api(`/api/works/${workId}/reorder`, {body: {ids}});
    const saved = await api(`/api/works/${workId}/chapters`, {method: 'GET'});
    if (currentWorkId === workId && chapters === list) {
      chapters = saved; renderTree(); updateWC();
      if ($('app').classList.contains('production-open')) await setProductionSection(productionSection, true);
    }
    return true;
  } catch (error) {
    // A response can be lost after a successful write. Prefer the server's order if reachable.
    let restored = snapshot;
    try { restored = await api(`/api/works/${workId}/chapters`, {method: 'GET'}); } catch (_) {}
    if (currentWorkId === workId && chapters === list) { chapters = restored; renderTree(); updateWC(); }
    showToast(`排序未确认，已恢复可用顺序：${error.message}`, 'err');
    return false;
  } finally { chapterOrderBusy.delete(workId); }
}
function moveChapter(cid, delta) {
  const ids = chapters.map(c => c.id), from = ids.indexOf(cid), to = from + delta;
  if (from < 0 || to < 0 || to >= ids.length) return;
  ids.splice(to, 0, ids.splice(from, 1)[0]);
  return commitChapterOrder(ids);
}
function closeChapterMenu(restoreFocus = false) {
  const state = chapterMenuState;
  $('chapterMenu')?.remove(); chapterMenuState = null;
  if (restoreFocus && state?.workId === currentWorkId) document.querySelector(`[data-chapter-menu="${state.id}"]`)?.focus();
}
function openChapterMenu(cid, anchor) {
  closeChapterMenu();
  const index = chapters.findIndex(c => c.id === cid), c = chapters[index];
  if (!c || chapterOrderBusy.has(currentWorkId)) return;
  chapterMenuState = {id: cid, workId: currentWorkId};
  const menu = document.createElement('div');
  menu.id = 'chapterMenu'; menu.className = 'menu chapter-menu'; menu.setAttribute('role', 'menu');
  menu.setAttribute('aria-label', `第${c.ord}章操作`);
  menu.innerHTML = `<div class="menu-label">第${c.ord}章 ${esc(c.title)}</div>
    <button role="menuitem" ${index === 0 ? 'disabled' : ''}>↑ 上移</button>
    <button role="menuitem" ${index === chapters.length - 1 ? 'disabled' : ''}>↓ 下移</button>
    <div class="menu-sep"></div><button role="menuitem" class="danger">删除本章…</button>`;
  [...menu.querySelectorAll('button')].forEach((button, i) => button.addEventListener('click', () => {
    if (chapterMenuState?.workId !== currentWorkId) { closeChapterMenu(); return; }
    closeChapterMenu(true);
    if (i < 2) moveChapter(cid, i === 0 ? -1 : 1); else delChapter(cid).catch(e => showToast(e.message, 'err'));
  }));
  menu.addEventListener('keydown', e => {
    if (e.isComposing || e.keyCode === 229) return;
    const items = [...menu.querySelectorAll('button:not(:disabled)')], at = items.indexOf(document.activeElement);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); items[(at + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]?.focus();
    }
    if (e.key === 'Tab') closeChapterMenu(true);
  });
  document.body.append(menu);
  const r = anchor.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(r.right - menu.offsetWidth, innerWidth - menu.offsetWidth - 8))}px`;
  menu.style.top = `${Math.max(8, Math.min(r.bottom + 4, innerHeight - menu.offsetHeight - 8))}px`;
  menu.querySelector('button:not(:disabled)')?.focus();
}
document.addEventListener('pointerdown', e => { if (!e.target.closest('#chapterMenu, .c-more')) closeChapterMenu(); });
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !e.isComposing && e.keyCode !== 229 && $('chapterMenu')) {
    e.preventDefault(); e.stopImmediatePropagation(); closeChapterMenu(true);
  }
}, true);
window.addEventListener('resize', () => closeChapterMenu());
document.querySelector('.tree').addEventListener('scroll', () => closeChapterMenu(), {passive: true});

// Per-chapter baselines exclude imported/new/deleted chapters from artificial progress.
function dailyWords(recordProgress = true) {
  if (!currentUsername || !currentWorkId || chaptersLoadedWorkId !== currentWorkId || !chapters.some(c => c.id === currentChapterId)) return null;
  const key = `writehtml:daily:${currentUsername}:${currentWorkId}`;
  const date = new Date(), today = `${date.getFullYear()}-${date.getMonth() + 1}-${date.getDate()}`;
  try {
    let record = JSON.parse(localStorage.getItem(key) || 'null'), changed = false;
    if (!record || record.date !== today || !record.counts || typeof record.counts !== 'object' || !record.deltas || typeof record.deltas !== 'object') {
      record = {date: today, counts: {}, deltas: {}}; changed = true;
    }
    if (recordProgress) {
      // Server chapter metadata counts whitespace; use actual editor text consistently instead.
      const count = charCount($('content').value), id = currentChapterId;
      if (!Number.isFinite(record.counts[id])) { record.counts[id] = count; changed = true; }
      const currentDelta = count - record.counts[id];
      if (record.deltas[id] !== currentDelta) { record.deltas[id] = currentDelta; changed = true; }
      if (changed) localStorage.setItem(key, JSON.stringify(record));
    }
    const delta = chapters.reduce((sum, c) => sum + (Number.isFinite(record.deltas[c.id]) ? record.deltas[c.id] : 0), 0);
    return Math.max(0, delta);
  } catch (_) { return null; }
}
// Display other tabs' progress without writing stale editor counts back and echoing forever.
window.addEventListener('storage', e => { if (e.key?.startsWith('writehtml:daily:')) updateWC({recordProgress:false}); });

let commandState = {items: [], shown: [], active: 0};
function commandPaletteItems() {
  const items = [];
  let group = '操作';
  [...$('moreMenu').children].forEach(el => {
    if (el.classList.contains('menu-label')) group = el.textContent.trim();
    if (el.tagName === 'BUTTON' && !el.classList.contains('hidden') && !el.disabled && el.id !== 'commandPaletteBtn') {
      items.push({group, label: el.textContent.trim(), run: () => el.click()});
    }
  });
  document.querySelectorAll('[data-workspace]').forEach(el => items.push({group:'视图', label:el.textContent.trim(), run:() => el.click()}));
  chapters.forEach(c => items.push({group:'章节', label:`第${c.ord}章 ${c.title}`, run:async () => {
    if ($('app').classList.contains('production-open')) {
      await closeProductionCanvas();
      if ($('app').classList.contains('production-open')) return;
    }
    await selectChapter(c.id);
  }}));
  return items;
}
function scoreCommand(item, query) {
  if (!query) return 1;
  const text = `${item.group} ${item.label}`.toLowerCase(), index = text.indexOf(query);
  if (index >= 0) return 1000 - Math.min(index, 900);
  let pos = 0;
  for (const char of query) { pos = text.indexOf(char, pos); if (pos < 0) return 0; pos++; }
  return 1;
}
function openCommandPalette() {
  if ($('app').classList.contains('hidden') || topModalOverlay()) return;
  closeMoreMenu(); closeChapterMenu();
  commandState = {items: commandPaletteItems(), shown: [], active: 0};
  $('commandOverlay').classList.remove('hidden');
  $('commandInput').value = ''; renderCommandPalette(); $('commandInput').focus();
}
function closeCommandPalette() { $('commandOverlay').classList.add('hidden'); }
function renderCommandPalette(keepActive = false) {
  const query = $('commandInput').value.trim().toLowerCase();
  commandState.shown = commandState.items.map(item => ({item, score:scoreCommand(item, query)}))
    .filter(row => row.score > 0).sort((a, b) => b.score - a.score).slice(0, 16).map(row => row.item);
  if (!keepActive) commandState.active = 0;
  $('commandList').innerHTML = commandState.shown.map((item, i) => `<div id="command-${i}" role="option" aria-selected="${i === commandState.active}" class="command-item${i === commandState.active ? ' active' : ''}" data-command="${i}"><small>${esc(item.group)}</small><span>${esc(item.label)}</span></div>`).join('') || '<div class="empty">没有匹配的操作或章节</div>';
  $('commandInput').setAttribute('aria-activedescendant', commandState.shown.length ? `command-${commandState.active}` : '');
  $('commandList').querySelector('.active')?.scrollIntoView({block:'nearest'});
}
function runCommand(index = commandState.active) {
  const item = commandState.shown[index];
  if (!item) return;
  closeCommandPalette();
  // Let the shared modal manager restore focus before opening the command's target.
  setTimeout(() => Promise.resolve().then(() => item.run()).catch(e => showToast(e.message, 'err')), 0);
}
$('commandInput').addEventListener('input', () => renderCommandPalette());
$('commandInput').addEventListener('keydown', e => {
  if (e.isComposing || e.keyCode === 229) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault(); const n = commandState.shown.length;
    commandState.active = n ? (commandState.active + (e.key === 'ArrowDown' ? 1 : n - 1)) % n : 0;
    renderCommandPalette(true);
  } else if (e.key === 'Enter') { e.preventDefault(); runCommand(); }
});
$('commandList').addEventListener('click', e => { const row = e.target.closest('[data-command]'); if (row) runCommand(+row.dataset.command); });
document.addEventListener('keydown', e => {
  if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || e.isComposing || e.keyCode === 229 || e.key.toLowerCase() !== 'k') return;
  e.preventDefault(); $('commandOverlay').classList.contains('hidden') ? openCommandPalette() : closeCommandPalette();
});

// Chapters retain the original state/view storage; gestures only change the viewport.
(function initProductionCanvasGestures() {
  const vp = $('productionCanvasViewport');
  let pan = null;
  vp.addEventListener('wheel', e => {
    if (!(e.ctrlKey || e.metaKey) || innerWidth <= 700) return;
    e.preventDefault();
    setProductionZoomAt(productionZoom * Math.exp(-Math.max(-60, Math.min(60, e.deltaY * (e.deltaMode === 1 ? 20 : 1))) * .002), e.clientX, e.clientY);
  }, {passive:false});
  vp.addEventListener('pointerdown', e => {
    if (e.button !== 0 || e.pointerType === 'touch' || innerWidth <= 700 || e.target.closest('.production-node, .chapter-plan-summary')) return;
    const r = vp.getBoundingClientRect();
    if (e.clientX - r.left >= vp.clientWidth || e.clientY - r.top >= vp.clientHeight) return;
    pan = {id:e.pointerId, x:e.clientX, y:e.clientY, left:vp.scrollLeft, top:vp.scrollTop};
    vp.setPointerCapture(e.pointerId); vp.classList.add('panning');
  });
  vp.addEventListener('pointermove', e => {
    if (pan?.id !== e.pointerId) return;
    vp.scrollLeft = pan.left - e.clientX + pan.x; vp.scrollTop = pan.top - e.clientY + pan.y;
  });
  ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(type => vp.addEventListener(type, () => { pan = null; vp.classList.remove('panning'); }));
})();

let relationGraphWork = null, relationGraphFocus = '';
function closeRelationGraph() { $('relationGraphOverlay').classList.add('hidden'); }
function openRelationGraph() {
  $('relationGraphOverlay').classList.remove('hidden');
  renderRelationGraph($('relationGraphExpanded'), entityRelations);
}
function renderRelationGraph(host, relations) {
  if (relationGraphWork !== currentWorkId) { relationGraphWork = currentWorkId; relationGraphFocus = ''; }
  const valid = relations.filter(r => Number.isInteger(r.id) && Number.isInteger(r.from_entity_id) && Number.isInteger(r.to_entity_id));
  const names = new Map();
  valid.forEach(r => { names.set(r.from_entity_id, r.from_name || '未命名'); names.set(r.to_entity_id, r.to_name || '未命名'); });
  if (!names.has(+relationGraphFocus)) relationGraphFocus = '';
  const scoped = relationGraphFocus ? valid.filter(r => r.from_entity_id === +relationGraphFocus || r.to_entity_id === +relationGraphFocus) : valid;
  const degree = new Map(); scoped.forEach(r => [r.from_entity_id, r.to_entity_id].forEach(id => degree.set(id, (degree.get(id) || 0) + 1)));
  const selected = [...degree.keys()].sort((a, b) => (a === +relationGraphFocus ? -1 : b === +relationGraphFocus ? 1 : degree.get(b) - degree.get(a) || a - b)).slice(0, 28);
  const nodes = selected.map(id => ({id, name:names.get(id), w:Math.min(148, 28 + Math.min(9, [...names.get(id)].length) * 13), h:30}));
  const byId = new Map(nodes.map(n => [n.id, n]));
  const links = scoped.filter(r => byId.has(r.from_entity_id) && byId.has(r.to_entity_id)).slice(0, 100);
  const width = Math.max(300, host.clientWidth || 460), height = host.classList.contains('expanded') ? Math.max(400, innerHeight * .72) : Math.max(320, Math.min(540, nodes.length * 28));
  const cx = width / 2, cy = height / 2, k = Math.sqrt(width * height / Math.max(1, nodes.length)) * .7;
  nodes.forEach((n, i) => { const angle = i / nodes.length * 2 * Math.PI; n.x = cx + Math.cos(angle) * (cx - 85); n.y = cy + Math.sin(angle) * (cy - 75); });
  const clamp = n => { n.x = Math.max(n.w / 2 + 12, Math.min(width - n.w / 2 - 12, n.x)); n.y = Math.max(80, Math.min(height - 40, n.y)); };
  // Bounded deterministic simulation: at most 28 nodes / 100 edges, independent of book size.
  for (let step = 0, temp = width / 8; step < 180; step++, temp *= .975) {
    nodes.forEach(n => { n.dx = (cx - n.x) * .02; n.dy = (cy - n.y) * .02; });
    nodes.forEach((a, i) => nodes.slice(i + 1).forEach(b => {
      const x = a.x - b.x, y = a.y - b.y, d = Math.max(1, Math.hypot(x, y)), f = k * k / d / d;
      a.dx += x * f; a.dy += y * f; b.dx -= x * f; b.dy -= y * f;
    }));
    links.forEach(r => { const a = byId.get(r.from_entity_id), b = byId.get(r.to_entity_id); if (a === b) return;
      const x = a.x - b.x, y = a.y - b.y, f = Math.hypot(x, y) / k;
      a.dx -= x * f; a.dy -= y * f; b.dx += x * f; b.dy += y * f;
    });
    nodes.forEach(n => { const d = Math.max(1, Math.hypot(n.dx, n.dy)); n.x += n.dx / d * Math.min(temp, d); n.y += n.dy / d * Math.min(temp, d); clamp(n); });
  }
  for (let pass = 0; pass < 40; pass++) nodes.forEach((a, i) => nodes.slice(i + 1).forEach(b => {
    const ox = (a.w + b.w) / 2 + 8 - Math.abs(a.x - b.x), oy = 44 - Math.abs(a.y - b.y);
    if (ox > 0 && oy > 0) { const sign = a.y < b.y ? -1 : 1; a.y += sign * oy / 2; b.y -= sign * oy / 2; clamp(a); clamp(b); }
  }));
  const pairs = new Map(), offsets = new Map();
  const pairKey = r => [r.from_entity_id, r.to_entity_id].sort((a, b) => a - b).join(':');
  links.forEach(r => pairs.set(pairKey(r), (pairs.get(pairKey(r)) || 0) + 1));
  const marker = `${host.id}-arrow`;
  const labelBoxes = [];
  function placeLabel(points, text) {
    const w = [...text].length * 12 + 8, h = 18;
    const candidates = points.map(([x, y]) => {
      x = Math.max(w / 2 + 4, Math.min(width - w / 2 - 4, x));
      y = Math.max(h, Math.min(height - h, y));
      const rect = {x: x - w / 2, y: y - h / 2, w, h};
      const overlap = b => Math.max(0, Math.min(rect.x + w, b.x + b.w) - Math.max(rect.x, b.x))
        * Math.max(0, Math.min(rect.y + h, b.y + b.h) - Math.max(rect.y, b.y));
      const cost = nodes.reduce((sum, n) => sum + 3 * overlap({x:n.x-n.w/2-4,y:n.y-19,w:n.w+8,h:38}),0)
        + labelBoxes.reduce((sum, b) => sum + overlap(b),0);
      return {x, y, rect, cost};
    }).sort((a,b) => a.cost-b.cost);
    labelBoxes.push(candidates[0].rect);
    return candidates[0];
  }
  const edges = links.map(r => {
    const a = byId.get(r.from_entity_id), b = byId.get(r.to_entity_id), key = pairKey(r), index = offsets.get(key) || 0;
    offsets.set(key, index + 1);
    let d, points;
    if (a === b) { const rise = 42 + Math.min(index, 3) * 10; d = `M ${a.x - 12} ${a.y - 15} C ${a.x - 45} ${a.y - rise}, ${a.x + 45} ${a.y - rise}, ${a.x + 12} ${a.y - 15}`; points = [[a.x,a.y-rise],[a.x-30,a.y-rise],[a.x+30,a.y-rise]]; }
    else {
      const [s, t] = a.id < b.id ? [a, b] : [b, a], length = Math.hypot(t.x - s.x, t.y - s.y) || 1, off = (index - (pairs.get(key) - 1) / 2) * 44;
      const qx = (a.x + b.x) / 2 - (t.y - s.y) / length * off, qy = (a.y + b.y) / 2 + (t.x - s.x) / length * off;
      const clip = (n, x, y) => { const dx = x - n.x, dy = y - n.y, q = Math.max(Math.abs(dx) / (n.w / 2 + 3), Math.abs(dy) / 18, .001); return [n.x + dx / q, n.y + dy / q]; };
      const [x1, y1] = clip(a, qx, qy), [x2, y2] = clip(b, qx, qy);
      d = `M ${x1} ${y1} Q ${qx} ${qy} ${x2} ${y2}`;
      points = [.5,.32,.68,.2,.8].map(t => [(1-t)**2*x1+2*(1-t)*t*qx+t*t*x2, (1-t)**2*y1+2*(1-t)*t*qy+t*t*y2]);
    }
    const text = [...String(r.relation || '关系')].slice(0, 8).join(''), label = placeLabel(points, text);
    return `<g class="rg-edge${r.status !== 'active' ? ' inactive' : ''}" data-relation="${r.id}" data-s="${a.id}" data-t="${b.id}" tabindex="0" role="button" aria-label="编辑关系：${esc(a.name)} ${esc(r.relation)} ${esc(b.name)}"><title>${esc(a.name)} → ${esc(r.relation)} → ${esc(b.name)}</title><path d="${d}" marker-end="url(#${marker})"/><text x="${label.x}" y="${label.y}">${esc(text)}</text></g>`;
  }).join('');
  host.innerHTML = `<div class="rg-head"><b>关系图</b><label>聚焦人物<select aria-label="聚焦关系人物"><option value="">全部</option>${[...names].map(([id, name]) => `<option value="${id}" ${String(id) === relationGraphFocus ? 'selected' : ''}>${esc(name)}</option>`).join('')}</select></label>${host.id === 'relationMap' ? '<button class="ic" onclick="openRelationGraph()" title="放大人物关系图">放大</button>' : ''}</div>
    <p class="rg-summary">显示 ${nodes.length}/${names.size} 人 · ${links.length}/${valid.length} 条关系；完整信息见下方列表</p>
    ${nodes.length ? `<svg role="group" aria-label="人物关系网络" viewBox="0 0 ${width} ${height}"><defs><marker id="${marker}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0 L10 5 L0 10 z"/></marker></defs>${edges}${nodes.map(n => `<g class="rg-node" data-person="${n.id}" transform="translate(${n.x} ${n.y})"><title>${esc(n.name)}</title><rect x="${-n.w / 2}" y="-15" width="${n.w}" height="30" rx="15"/><text>${esc([...n.name].slice(0, 9).join(''))}${[...n.name].length > 9 ? '…' : ''}</text></g>`).join('')}</svg>` : '<div class="empty">尚未记录人物关系</div>'}`;
  host.querySelector('select').onchange = e => { relationGraphFocus = e.target.value; renderRelationGraph(host, relations); };
  const edit = rid => { closeRelationGraph(); setTimeout(() => editRelation(rid), 0); };
  host.onclick = e => { const edge = e.target.closest('[data-relation]'); if (edge) edit(+edge.dataset.relation); };
  host.onkeydown = e => { if (e.isComposing) return; const edge = e.target.closest('[data-relation]'); if (edge && ['Enter', ' '].includes(e.key)) { e.preventDefault(); edit(+edge.dataset.relation); } };
  const highlight = id => {
    const svg = host.querySelector('svg'); if (!svg) return;
    svg.classList.toggle('dim', !!id);
    const related = new Set([id]);
    svg.querySelectorAll('.rg-edge').forEach(edge => { const match = edge.dataset.s === id || edge.dataset.t === id; edge.classList.toggle('hl', match); if (match) { related.add(edge.dataset.s); related.add(edge.dataset.t); } });
    svg.querySelectorAll('.rg-node').forEach(node => node.classList.toggle('hl', related.has(node.dataset.person)));
  };
  host.onpointerover = e => highlight(e.target.closest('[data-person]')?.dataset.person);
  host.onpointerleave = () => highlight(null);
}

let workspaceToolsResize = null;
window.addEventListener('resize', () => {
  if (workspaceToolsResize) cancelAnimationFrame(workspaceToolsResize);
  workspaceToolsResize = requestAnimationFrame(() => {
    workspaceToolsResize = null;
    if ($('productionCanvasViewport').getClientRects().length) {
      $('productionCanvas').style.zoom = innerWidth <= 700 ? 1 : productionZoom;
      updateProductionLinks();
    }
    for (const id of ['relationMap', 'relationGraphExpanded']) {
      const host = $(id);
      if (host.getClientRects().length && host.querySelector('svg')) renderRelationGraph(host, entityRelations);
    }
  });
});
