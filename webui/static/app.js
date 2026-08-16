/* LLM 社会模拟引擎 WebUI 前端逻辑 */
"use strict";

const $ = (sel) => document.querySelector(sel);

const state = {
  snapshot: null,
  lastEventTotal: 0,
  lastEventTick: null,
  thinking: null,
  tab: "events",
  started: false,
};

let refreshQueue = Promise.resolve();

/* ---------- 工具 ---------- */
function esc(v) {
  return String(v ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function fmtStates(states) {
  if (!states || !Object.keys(states).length) return "";
  return Object.entries(states)
    .map(([k, v]) => {
      const val = typeof v === "object" ? JSON.stringify(v) : v;
      return `<span class="c-state">${esc(k)}: ${esc(val)}</span>`;
    })
    .join("");
}

async function api(url, opts) {
  const r = await fetch(url, opts);
  let data = null;
  try { data = await r.json(); } catch (_) { /* 非 JSON */ }
  if (!r.ok || (data && data.ok === false)) {
    const msg = (data && (data.error || data.detail)) || `请求失败 (${r.status})`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

function toast(message, kind = "ok", ms = 3200) {
  const el = $("#toast");
  el.textContent = message;
  el.className = "toast" + (kind ? " " + kind : "");
  el.hidden = false;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => { el.hidden = true; }, ms);
}

/* ---------- 初始化 ---------- */
async function init() {
  bindControls();
  bindModal();
  connectStream();
  await Promise.all([loadScenes(), loadSaves()]);
  await refresh();
}

async function loadScenes() {
  try {
    const data = await api("/api/scenes");
    const sel = $("#scene-select");
    sel.innerHTML = data.scenes.map((s) =>
      `<option value="${esc(s)}">${esc(s)}</option>`
    ).join("");
    sel.value = data.default || data.scenes[0] || "";
  } catch (e) {
    toast("加载场景列表失败: " + e.message, "error");
  }
}

async function loadSaves() {
  try {
    const data = await api("/api/saves");
    const sel = $("#save-select");
    const opts = ['<option value="">（新建会话）</option>'];
    for (const s of data.saves) {
      opts.push(`<option value="${esc(s.path)}">${esc(s.name)} · ${esc(s.modified)}</option>`);
    }
    sel.innerHTML = opts.join("");
  } catch (e) {
    /* 存档目录可能不存在，忽略 */
  }
}

function connectStream() {
  const es = new EventSource("/api/stream");
  es.addEventListener("update", () => refresh());
  es.addEventListener("reset", () => {
    state.lastEventTotal = 0;
    state.lastEventTick = null;
    $("#events-body").innerHTML = "";
    refresh();
  });
  es.addEventListener("done", () => refresh());
  es.addEventListener("sim_error", (e) => {
    let msg = "模拟异常";
    try { msg = JSON.parse(e.data).message || msg; } catch (_) {}
    toast(msg, "error", 6000);
    refresh();
  });
  es.addEventListener("status", (e) => {
    try {
      const d = JSON.parse(e.data);
      if (d.status === "running" && d.message) {
        state.thinking = d.message;
        setStatus(d.message);
      } else {
        state.thinking = null;
        setStatus(d.message || "");
      }
    } catch (_) {}
  });
  es.onerror = () => { /* EventSource 自动重连 */ };
}

/* ---------- 控制 ---------- */
function bindControls() {
  $("#btn-start").addEventListener("click", startSession);
  $("#btn-next").addEventListener("click", () => runStep("/api/next"));
  $("#btn-step").addEventListener("click", () => runStep("/api/step"));
  $("#btn-auto").addEventListener("click", toggleAuto);
  $("#btn-save").addEventListener("click", saveSession);
  $("#btn-scene-info").addEventListener("click", () => {
    if (state.snapshot && state.snapshot.ready) showSceneInfo();
  });

  document.querySelectorAll(".tab").forEach((t) => {
    t.addEventListener("click", () => {
      state.tab = t.dataset.tab;
      document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
      if (state.tab === "events") {
        // 事件/消息共用容器，消息用 innerHTML 覆盖过，这里全量重建事件流
        state.lastEventTotal = 0;
        state.lastEventTick = null;
        $("#events-body").innerHTML = "";
      }
      renderEventsAndMessages();
    });
  });
}

function bindModal() {
  $("#modal-close").addEventListener("click", closeModal);
  $("#modal-backdrop").addEventListener("click", (e) => {
    if (e.target === e.currentTarget) closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeModal();
  });
}

async function startSession() {
  const scene = $("#scene-select").value;
  const load = $("#save-select").value;
  const mode = $("#mode-select").value;
  const max_ticks = parseInt($("#ticks-input").value, 10) || 20;

  if (!load && !scene) { toast("请选择场景", "error"); return; }

  setBusy(true);
  try {
    const snap = await api("/api/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scene: load ? null : scene, load: load || null, mode, max_ticks: max_ticks }),
    });
    state.started = true;
    state.lastEventTotal = 0;
    state.lastEventTick = null;
    $("#events-body").innerHTML = "";
    await refresh();
    toast(`已启动会话`, "ok");
  } catch (e) {
    toast("启动失败: " + e.message, "error", 6000);
  } finally {
    setBusy(false);
  }
}

async function runStep(path) {
  setBusy(true);
  try {
    await api(path, { method: "POST" });
    await refresh();
  } catch (e) {
    toast(e.message, "error", 6000);
  } finally {
    setBusy(false);
  }
}

async function toggleAuto() {
  const snap = state.snapshot;
  if (!snap || !snap.ready) return;
  setBusy(true);
  try {
    if (snap.session.auto_running) {
      await api("/api/stop", { method: "POST" });
    } else {
      await api("/api/auto", { method: "POST" });
    }
    await refresh();
  } catch (e) {
    toast(e.message, "error", 6000);
  } finally {
    setBusy(false);
  }
}

async function saveSession() {
  try {
    const data = await api("/api/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: null }),
    });
    toast("已保存: " + data.path, "ok", 5000);
    loadSaves();
  } catch (e) {
    toast("保存失败: " + e.message, "error", 6000);
  }
}

function setBusy(busy) {
  for (const id of ["#btn-start", "#btn-next", "#btn-step", "#btn-auto", "#btn-save"]) {
    const b = $(id);
    if (id === "#btn-start") continue; // 开始按钮永远可点
    b.disabled = busy || !state.snapshot || !state.snapshot.ready;
  }
}

function setStatus(text) {
  const el = $("#status-label");
  el.textContent = text || "";
  el.className = "status" + (text ? " " + statusClass(text) : "");
}

function statusClass(text) {
  if (/异常|失败|错误/.test(text)) return "error";
  if (/完成/.test(text)) return "done";
  if (/运行|生成|行动|停止中/.test(text)) return "running";
  return "";
}

/* ---------- 数据刷新 ---------- */
function refresh() {
  // 串行化：避免并发 fetch 的旧快照回退 lastEventTotal，导致事件重复渲染
  refreshQueue = refreshQueue
    .then(async () => {
      try {
        const snap = await api("/api/state");
        applySnapshot(snap);
      } catch (e) {
        /* 服务器未就绪时静默 */
      }
    })
    .catch(() => {});
  return refreshQueue;
}

function applySnapshot(snap) {
  state.snapshot = snap;

  if (!snap.ready) {
    renderEmpty();
    updateControls();
    return;
  }

  updateHeader(snap);
  renderLocations(snap);
  renderCharacters(snap);
  renderEventsAndMessages();
  updateControls();
}

function renderEmpty() {
  $("#btn-scene-info").disabled = true;
  $("#tick-label").textContent = "Tick —/—";
  $("#progress-fill").style.width = "0%";
}

function updateHeader(snap) {
  const s = snap.session;
  $("#tick-label").textContent = `Tick ${s.tick}/${s.end_tick} · 下一 ${s.next_tick} · 余 ${s.remaining}`;
  const total = Math.max(1, s.end_tick - s.start_tick + 1);
  const done = Math.max(0, s.tick - s.start_tick + 1);
  $("#progress-fill").style.width = Math.min(100, (done / total) * 100) + "%";

  if (s.status === "error") setStatus("❌ " + (s.error || "模拟异常"));
  else if (s.status === "done") setStatus("✅ 模拟完成");
  else if (state.thinking && (s.status === "running" || s.auto_running)) setStatus(state.thinking);
  else if (s.auto_running) setStatus("▶ 自动运行中…");
  else setStatus(s.loaded_from ? `载入存档: ${s.loaded_from}` : "就绪");
}

function updateControls() {
  const ready = state.snapshot && state.snapshot.ready;
  const auto = ready && state.snapshot.session.auto_running;
  const done = ready && state.snapshot.session.status === "done";

  $("#btn-scene-info").disabled = !ready;
  $("#btn-next").disabled = !ready || auto || done;
  $("#btn-step").disabled = !ready || auto || done;
  $("#btn-save").disabled = !ready;

  const autoBtn = $("#btn-auto");
  autoBtn.disabled = !ready || done;
  autoBtn.textContent = auto ? "⏸ 暂停" : "▶ 自动";
  autoBtn.classList.toggle("running", auto);
}

/* ---------- 渲染：地点 ---------- */
function renderLocations(snap) {
  const body = $("#locations-body");
  body.innerHTML = snap.world.locations.map((loc) => {
    const env = Object.entries(loc.environment)
      .map(([k, v]) => `<b>${esc(k)}</b> ${esc(v)}`)
      .join(" · ");
    const chips = loc.characters.map((name) => {
      const ch = snap.world.characters.find((c) => c.name === name);
      const npc = ch && ch.is_npc;
      return `<span class="chip ${npc ? "npc" : ""}" data-char="${esc(name)}" title="查看角色">${npc ? "🎭" : "👤"} ${esc(name)}</span>`;
    }).join("");
    return `
      <div class="location" data-loc="${esc(loc.name)}">
        <div class="location-head">
          <span class="loc-icon">${esc(loc.icon)}</span>
          <span class="loc-name">${esc(loc.name)}</span>
          <span class="loc-count">${loc.characters.length} 人</span>
        </div>
        ${env ? `<div class="location-env">${env}</div>` : ""}
        <div class="location-chars">${chips}</div>
      </div>`;
  }).join("");

  body.querySelectorAll(".location").forEach((el) => {
    el.addEventListener("click", (e) => {
      if (e.target.closest(".chip")) return;
      showLocationInfo(el.dataset.loc);
    });
  });
  body.querySelectorAll(".chip").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      showCharacterInfo(el.dataset.char);
    });
  });
}

/* ---------- 渲染：事件 / 消息 ---------- */
function renderEventsAndMessages() {
  if (state.tab === "messages") renderMessages();
  else renderEvents();
}

function renderEvents() {
  const body = $("#events-body");
  const events = state.snapshot.world.event_log;
  const total = state.snapshot.world.event_log_total;
  const start = Math.max(0, events.length - (total - state.lastEventTotal));

  if (events.length) {
    body.querySelectorAll("p.empty").forEach((el) => el.remove());
  }

  let lastTick = state.lastEventTick;
  for (let i = start; i < events.length; i++) {
    const ev = events[i];
    if (ev.tick !== lastTick) {
      const sep = document.createElement("div");
      sep.className = "tick-sep";
      sep.textContent = `── Tick ${ev.tick} ──`;
      body.appendChild(sep);
      lastTick = ev.tick;
    }
    body.appendChild(renderEvent(ev));
  }
  state.lastEventTotal = Math.max(state.lastEventTotal, total);
  state.lastEventTick = lastTick;

  if (!events.length && !body.children.length) {
    body.innerHTML = '<p class="empty">启动会话后，事件将在这里逐条呈现。</p>';
  }
  // 自动滚动到底部（仅当用户接近底部时）
  const nearBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 160;
  if (nearBottom) body.scrollTop = body.scrollHeight;
}

function renderEvent(ev) {
  const el = document.createElement("div");
  const snap = state.snapshot || {};
  const sourceIcons = snap.source_icons || {};
  const actionMeta = snap.action_meta || {};
  const icon = sourceIcons[ev.source_type] || "•";
  el.className = "event " + (ev.source_type || "");

  if (ev.source_type === "agent") {
    const meta = ev.meta || {};
    const am = actionMeta[meta.action_type] || {};
    const actionIcon = am.icon || "▶";
    let title = `<span class="icon">${actionIcon}</span>`;
    title += `<span class="text"><span class="who">${esc(ev.source)}</span>`;
    title += ` <span class="action-type">${esc(meta.action_type || "?")}</span>`;
    if (meta.target) title += ` → ${esc(meta.target)}`;
    if (meta.content) title += `: ${esc(meta.content)}`;
    title += "</span>";

    el.innerHTML = `<div class="line">${title}</div>`;

    // result 直接展示（不折叠），label 从 ActionSpec.result_labels 读
    if (meta.result && typeof meta.result === "object" && Object.keys(meta.result).length) {
      const labelMap = am.result_labels || {};
      const rows = Object.entries(meta.result).map(([k, v]) =>
        `<div class="result-row">${esc(labelMap[k] || k)}: ${esc(typeof v === "object" ? JSON.stringify(v) : v)}</div>`
      ).join("");
      el.innerHTML += `<div class="result">${rows}</div>`;
    }

    // 只折叠内心活动
    if (meta.internal_monologue) {
      el.innerHTML += `<details class="details"><summary>🧠 内心活动</summary><div class="monologue">${esc(meta.internal_monologue)}</div></details>`;
    }
  } else {
    el.innerHTML = `<div class="line"><span class="icon">${icon}</span><span class="text">${esc(ev.text)}</span></div>`;
  }
  return el;
}

function renderMessages() {
  const body = $("#events-body");
  const msgs = state.snapshot.world.messages;
  if (!msgs.length) {
    body.innerHTML = '<p class="empty">暂无消息。</p>';
    return;
  }
  body.innerHTML = msgs.map((m) => {
    const target = m.target ? ` → <span class="m-target">${esc(m.target)}</span>` : "";
    const recv = m.recipients && m.recipients.length ? ` (发给 ${esc(m.recipients.join(", "))})` : "";
    return `
      <div class="msg">
        <div class="m-head">[tick ${esc(m.tick)}] <span class="m-type">${esc(m.msg_type)}</span> · ${esc(m.sender)}${target}${recv}</div>
        <div class="m-body">${esc(m.content)}</div>
      </div>`;
  }).join("");
}

/* ---------- 渲染：角色 ---------- */
function renderCharacters(snap) {
  const body = $("#characters-body");
  body.innerHTML = snap.world.characters.map((c) => {
    const cls = c.is_npc ? "npc" : (c.agent_type === "ManualAgent" ? "manual" : "agent");
    const avatar = c.is_npc ? "🎭" : "👤";

    let bodyHtml = `<div class="c-body">`;
    bodyHtml += `<div class="c-section"><div class="c-label">状态</div><div class="c-states">${fmtStates(c.states) || '<span class="c-state">—</span>'}</div></div>`;

    if (!c.is_npc) {
      if (c.relationships && Object.keys(c.relationships).length) {
        const rels = Object.entries(c.relationships).map(([k, v]) =>
          `<div class="c-rel">${esc(k)}：${esc(Object.entries(v).map(([a, b]) => a + "=" + b).join(", "))}</div>`
        ).join("");
        bodyHtml += `<div class="c-section"><div class="c-label">关系</div>${rels}</div>`;
      }
      if (c.perceived_inbox && c.perceived_inbox.length) {
        const inbox = c.perceived_inbox.map((m) =>
          `<div class="c-mem">${esc(m.sender)}${m.target ? " → " + esc(m.target) : ""}: ${esc(m.content)}</div>`
        ).join("");
        bodyHtml += `<div class="c-section"><div class="c-label">本 tick 收到</div>${inbox}</div>`;
      }
      if (c.memory_summary) {
        bodyHtml += `<div class="c-section"><div class="c-label">记忆摘要</div><div class="c-mem">${esc(c.memory_summary)}</div></div>`;
      }
      if (c.recent_memories && c.recent_memories.length) {
        const mems = c.recent_memories.map((m) => `<div class="c-mem">${esc(m.event)}</div>`).join("");
        bodyHtml += `<div class="c-section"><div class="c-label">最近记忆</div>${mems}</div>`;
      }
      bodyHtml += `<div class="c-section"><button class="btn" data-char="${esc(c.name)}">📋 完整档案</button></div>`;
    }
    bodyHtml += "</div>";

    return `
      <details class="character ${cls}" data-char="${esc(c.name)}">
        <summary>
          <span class="c-avatar">${avatar}</span>
          <span class="c-name">${esc(c.name)}</span>
          <span class="c-badge">${esc(c.role)}</span>
          <span class="c-loc">@ ${esc(c.location)}</span>
        </summary>
        ${bodyHtml}
      </details>`;
  }).join("");

  body.querySelectorAll("button[data-char]").forEach((b) => {
    b.addEventListener("click", (e) => {
      e.preventDefault();
      showCharacterInfo(b.dataset.char);
    });
  });
  body.querySelectorAll("summary").forEach((s) => {
    s.addEventListener("dblclick", () => showCharacterInfo(s.parentElement.dataset.char));
  });
}

/* ---------- 弹窗 ---------- */
function openModal(title, bodyHtml) {
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = bodyHtml;
  $("#modal-backdrop").hidden = false;
}

function closeModal() {
  $("#modal-backdrop").hidden = true;
}

function showSceneInfo() {
  const snap = state.snapshot;
  const sections = snap.config_sections.map((s) =>
    `<div class="section"><h4>${esc(s.title)}</h4><pre>${esc(s.body)}</pre></div>`
  ).join("");
  const gm = snap.gm;
  const gmExtra = `
    <div class="section"><h4>🎲 GM 工具</h4><div class="tool-list">${gm.tools.map((t) => `<span class="tool">${esc(t)}</span>`).join("")}</div></div>
    <div class="section"><h4>🤖 Agent 工具</h4><div class="tool-list">${(snap.tools.agent || []).map((t) => `<span class="tool">${esc(t)}</span>`).join("")}</div></div>
  `;
  openModal(`📖 场景信息 — ${snap.scene.name}`, sections + gmExtra);
}

function showCharacterInfo(name) {
  const snap = state.snapshot;
  const c = snap.world.characters.find((x) => x.name === name);
  if (!c) return;

  const rows = [
    ["身份", c.role || "—"],
    ["位置", c.location || "—"],
  ].map(([k, v]) => `<div class="kv-row"><b>${esc(k)}</b><span>${esc(v)}</span></div>`).join("");

  let html = `<div class="section"><h4>${c.is_npc ? "🎭" : "👤"} ${esc(c.name)}</h4>${rows}</div>`;

  if (c.personality) html += `<div class="section"><h4>性格</h4><pre>${esc(c.personality)}</pre></div>`;
  if (c.goal) html += `<div class="section"><h4>目标</h4><pre>${esc(c.goal)}</pre></div>`;
  html += `<div class="section"><h4>状态</h4><div class="c-states">${fmtStates(c.states) || "—"}</div></div>`;

  if (!c.is_npc) {
    if (c.relationships && Object.keys(c.relationships).length) {
      const rels = Object.entries(c.relationships).map(([k, v]) =>
        `<div class="kv-row"><b>${esc(k)}</b><span>${esc(Object.entries(v).map(([a, b]) => a + "=" + b).join(", "))}</span></div>`
      ).join("");
      html += `<div class="section"><h4>关系属性</h4>${rels}</div>`;
    }
    html += `<div class="section"><h4>能力边界</h4>`
      + `<div class="kv-row"><b>可写状态</b><span>${esc((c.writable_states || []).join(", ") || "—")}</span></div>`
      + `<div class="kv-row"><b>私有状态</b><span>${esc((c.private_states || []).join(", ") || "—")}</span></div>`
      + `<div class="kv-row"><b>类型</b><span>${esc(c.agent_type || "Agent")}</span></div></div>`;
    if (c.last_observed_result) {
      html += `<div class="section"><h4>👁 上次观察</h4><pre>${esc(c.last_observed_result)}</pre></div>`;
    }
    if (c.memory_summary) {
      html += `<div class="section"><h4>🧠 记忆摘要</h4><pre>${esc(c.memory_summary)}</pre></div>`;
    }
    if (c.manual_plan && Object.keys(c.manual_plan).length) {
      html += `<div class="section"><h4>📋 手动计划</h4><pre>${esc(JSON.stringify(c.manual_plan, null, 2))}</pre></div>`;
    }
  }
  openModal(`角色档案 · ${c.name}`, html);
}

function showLocationInfo(name) {
  const snap = state.snapshot;
  const loc = snap.world.locations.find((l) => l.name === name);
  if (!loc) return;

  let html = `<div class="section"><h4>${esc(loc.icon)} ${esc(loc.name)}</h4></div>`;
  if (Object.keys(loc.environment).length) {
    const env = Object.entries(loc.environment).map(([k, v]) =>
      `<div class="kv-row"><b>${esc(k)}</b><span>${esc(v)}</span></div>`
    ).join("");
    html += `<div class="section"><h4>环境</h4>${env}</div>`;
  }
  html += `<div class="section"><h4>可见地点</h4><pre>${esc((loc.visible || []).join(", ") || "—")}</pre></div>`;
  html += `<div class="section"><h4>可达地点</h4><pre>${esc((loc.adjacent || []).join(", ") || "—")}</pre></div>`;
  html += `<div class="section"><h4>可调指标</h4><pre>${esc((loc.interactable || []).join(", ") || "—")}</pre></div>`;
  if (loc.characters.length) {
    html += `<div class="section"><h4>当前角色</h4><pre>${esc(loc.characters.join(", "))}</pre></div>`;
  }
  openModal(`地点详情 · ${loc.name}`, html);
}

document.addEventListener("DOMContentLoaded", init);
