/* iDeviceTail viewer — all controls in the browser. Vanilla JS, virtualized list. */
"use strict";

const MAX_ROWS = 200_000, TRIM_CHUNK = 20_000, ROW_H = 22, OVERSCAN = 12;
const $ = (id) => document.getElementById(id);
const scroller = $("scroller"), spacer = $("spacer"), rowsEl = $("rows");

const state = {
  all: [], filtered: [], devices: new Map(), info: new Map(),
  selected: null, paused: false, pauseBuf: [], follow: true,
  dropped: 0, procs: new Set(), subs: new Set(),
  predicate: () => true, captures: [], sessionFile: null, engineA: null,
};

/* ---------------- WebSocket ---------------- */
let ws, backoff = 500;
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws?snapshot=5000`);
  ws.onopen = () => { backoff = 500; setConn(true); refreshState(); };
  ws.onclose = () => { setConn(false); setTimeout(connect, backoff); backoff = Math.min(backoff * 2, 15000); };
  ws.onerror = () => ws.close();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "snapshot") {
      (m.devices || []).forEach(upsertDevice);
      state.captures = m.captures || [];
      state.all = (m.logs || []).slice(-MAX_ROWS);
      indexAux(state.all); rebuildFiltered(); renderDevices();
    } else if (m.type === "logs") {
      if (typeof m.dropped === "number") state.dropped = m.dropped;
      ingest(m.logs || []);
    } else if (m.type === "device") {
      upsertDevice(m.device); renderDevices();
    } else if (m.type === "device_removed") {
      state.devices.delete(m.id); if (state.selected === m.id) state.selected = null; renderDevices(); syncToolbar();
    } else if (m.type === "engine") {
      $("device-hint").textContent = `Engine ${m.engine}: ${m.state}${m.detail ? " — " + m.detail : ""}`;
    }
  };
}
function setConn(ok) { const el = $("conn"); el.textContent = ok ? "live" : "reconnecting…"; el.className = "pill " + (ok ? "pill-ok" : "pill-bad"); }

async function refreshState() {
  try {
    const s = await (await fetch("/api/state")).json();
    state.captures = s.captures || [];
    state.sessionFile = s.session_file || null;
    state.engineA = s.engines && s.engines.device;
    (s.devices || []).forEach(upsertDevice);
    renderDevices(); syncToolbar(); updateCount();
    const ea = $("engine-a");
    if (state.engineA && state.engineA.available) { ea.textContent = "Engine A ✓"; ea.className = "pill pill-ok"; }
    else { ea.textContent = "Engine A: install pymobiledevice3"; ea.className = "pill pill-dim"; ea.title = "pip install \"pymobiledevice3>=11\" — needed for full system logs"; }
    const co = state.sessionFile && state.sessionFile.can_open;
    $("openfile-menu").querySelectorAll('[data-act]').forEach(b => b.style.display = co ? "block" : "none");
  } catch {}
}

/* ---------------- ingest / virtualization (unchanged core) ---------------- */
function ingest(logs) {
  if (!logs.length) return;
  if (state.paused) { state.pauseBuf.push(...logs); trimArr(state.pauseBuf); updateCount(); return; }
  for (const r of logs) { state.all.push(r); if (state.predicate(r)) state.filtered.push(r); }
  indexAux(logs);
  if (state.all.length > MAX_ROWS + TRIM_CHUNK) { state.all.splice(0, TRIM_CHUNK); rebuildFiltered(true); }
  else { trimArr(state.filtered); render(); }
  if (state.follow && !state.paused) scroller.scrollTop = scroller.scrollHeight;
  updateCount();
}
function trimArr(a) { if (a.length > MAX_ROWS) a.splice(0, a.length - MAX_ROWS); }
function indexAux(logs) {
  for (const r of logs) {
    if (r.process && !state.procs.has(r.process) && state.procs.size < 1000) { state.procs.add(r.process); addOpt("processes", r.process); }
    if (r.subsystem && !state.subs.has(r.subsystem) && state.subs.size < 1000) { state.subs.add(r.subsystem); addOpt("subsystems", r.subsystem); }
  }
}
function addOpt(listId, val) { const o = document.createElement("option"); o.value = val; $(listId).appendChild(o); }

const LEVELS = ["debug", "info", "notice", "warning", "error", "fault"];
function buildPredicate() {
  const q = $("f-search").value.trim(), useRe = $("f-regex").checked;
  const minLvl = LEVELS.indexOf($("f-level").value);
  const proc = $("f-process").value.trim().toLowerCase();
  const sub = $("f-subsystem").value.trim().toLowerCase();
  const src = $("f-source").value, dev = state.deviceFilter || "";
  let re = null; if (q && useRe) { try { re = new RegExp(q, "i"); } catch { re = null; } }
  const ql = q.toLowerCase();
  return (r) => {
    if (LEVELS.indexOf(r.level) < minLvl) return false;
    if (src && r.source !== src) return false;
    if (dev && r.device_id !== dev) return false;
    if (proc && !(r.process || "").toLowerCase().includes(proc)) return false;
    if (sub && !(r.subsystem || "").toLowerCase().includes(sub)) return false;
    if (q) {
      if (re) return re.test(r.message) || re.test(r.process || "") || re.test(r.subsystem || "");
      return (r.message || "").toLowerCase().includes(ql) || (r.process || "").toLowerCase().includes(ql) || (r.subsystem || "").toLowerCase().includes(ql);
    }
    return true;
  };
}
function rebuildFiltered(keepScroll) {
  state.predicate = buildPredicate();
  const top = scroller.scrollTop;
  state.filtered = state.all.filter(state.predicate);
  render();
  scroller.scrollTop = keepScroll ? top : (state.follow ? scroller.scrollHeight : top);
  updateCount();
}
let lastStart = -1, lastLen = -1;
function render() {
  const n = state.filtered.length;
  $("empty").hidden = n > 0 || state.all.length > 0;
  spacer.style.height = (n * ROW_H) + "px";
  let start = Math.max(0, Math.floor(scroller.scrollTop / ROW_H) - OVERSCAN);
  const visible = Math.ceil(scroller.clientHeight / ROW_H) + OVERSCAN * 2;
  const end = Math.min(n, start + visible);
  if (start === lastStart && end - start === lastLen) return;
  lastStart = start; lastLen = end - start;
  const frag = document.createDocumentFragment();
  for (let i = start; i < end; i++) frag.appendChild(rowEl(state.filtered[i]));
  rowsEl.replaceChildren(frag);
  rowsEl.style.transform = `translateY(${start * ROW_H}px)`;
}
function rowEl(r) {
  const d = document.createElement("div");
  d.className = "row " + r.level; d.onclick = () => showDetail(r);
  const dev = state.devices.get(r.device_id);
  d.innerHTML =
    `<span class="t">${fmtTime(r.ts)}</span>` +
    `<span class="dev">${esc((dev && dev.name) || r.device_name || "")}</span>` +
    `<span class="proc">${esc(r.process || "")}${r.pid ? "[" + r.pid + "]" : ""}</span>` +
    `<span class="lvl">${r.level}</span>` +
    `<span class="sub">${esc(r.subsystem || "")}${r.category ? ":" + esc(r.category) : ""}</span>` +
    `<span class="msg">${esc(r.message || "")}</span>`;
  return d;
}
scroller.addEventListener("scroll", () => {
  render();
  const atBottom = scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 4;
  if (!atBottom && state.follow) { state.follow = false; $("b-autoscroll").checked = false; }
  else if (atBottom && !state.follow) { state.follow = true; $("b-autoscroll").checked = true; }
});
window.addEventListener("resize", render);

/* ---------------- devices ---------------- */
const KIND_ICON = { iphone: "📱", ipad: "▦", ipod: "🎵", device: "📟" };
function upsertDevice(d) {
  if (!d || !d.id) return;
  state.devices.set(d.id, d);
  if (!state.info.has(d.id)) loadInfo(d.id);           // works for agent devices too
  if (!state.selected && d.udid) { state.selected = d.id; }
}
async function loadInfo(id, force) {
  try {
    const j = await (await fetch(`/api/devices/${encodeURIComponent(id)}/info${force ? "?refresh=1" : ""}`)).json();
    if (!j.error) { state.info.set(id, j); renderDevices(); }
  } catch {}
}
function capFor(id) { return state.captures.find(c => c.device_id === id); }

function renderDevices() {
  const ul = $("device-list"); ul.replaceChildren();
  const sorted = [...state.devices.values()].sort((a,b) => (b.udid?1:0)-(a.udid?1:0));
  for (const d of sorted) {
    const info = state.info.get(d.id) || {};
    const li = document.createElement("li");
    li.className = "dev-card" + (state.selected === d.id ? " selected" : "");
    li.onclick = () => { state.selected = d.id; renderDevices(); syncToolbar(); };
    const isAgent = (d.transports || []).includes("agent");
    const cap = capFor(d.id);
    const streaming = cap ? (cap.desired !== false) : ["streaming","connecting"].includes(d.connection_state);
    const ico = KIND_ICON[info.kind || "device"] || "📟";
    const model = info.model || d.model || "";
    const cellcap = info.capacity ? " · " + info.capacity : "";
    li.innerHTML = `
      <div class="dev-top">
        <div class="dev-ico">${ico}</div>
        <div style="min-width:0;flex:1">
          <div class="dev-name"><span class="dot ${cap ? cap.state : d.connection_state}"></span>${esc(info.name || d.name)}</div>
          <div class="dev-sub">${esc(model)}${esc(cellcap)}</div>
          <div class="dev-kv">
            ${info.serial ? `<b>SN</b> ${esc(info.serial)}<br>` : ""}
            <b>iOS</b> ${esc(info.os || d.os_version || "?")}${info.build ? " (" + esc(info.build) + ")" : ""}<br>
            <b>net</b> ${esc(d.address || "?")} · ${esc((d.transports || []).join(", ") || "—")}
            ${d.developer_mode === false ? '<br><span class="warn">⚠ Developer Mode OFF</span>' : ""}
            ${d.detail ? `<br><i>${esc(d.detail)}</i>` : ""}
          </div>
        </div>
      </div>
      <div class="dev-btns"></div>`;
    const btns = li.querySelector(".dev-btns");
    if (isAgent) {
      btns.innerHTML = `<span class="hint">streams automatically</span>`;
    } else if (d.udid) {
      btns.appendChild(mkb(streaming ? "■ Stop" : "▶ Start", streaming, (e) => { e.stopPropagation(); streaming ? stopCap(d.id) : startCap(d.id, "syslog"); }));
      btns.appendChild(mkb("os_trace", cap && cap.mode === "oslog", (e) => { e.stopPropagation(); startCap(d.id, "oslog"); }));
      btns.appendChild(mkb("Setup", false, (e) => { e.stopPropagation(); openSetup(d.id); }));
      btns.appendChild(mkb("Crashes", false, (e) => { e.stopPropagation(); post(`/api/devices/${d.id}/crashes`, {}, "Crash reports pulled"); }));
      btns.appendChild(mkb("Sysdiag", false, (e) => { e.stopPropagation(); post(`/api/devices/${d.id}/sysdiagnose`, {}, "sysdiagnose started (minutes)"); }));
    } else {
      btns.innerHTML = `<span class="hint">provision over USB once → then wireless</span>`;
      btns.appendChild(mkb("Setup", false, (e) => { e.stopPropagation(); openSetup(d.id); }));
    }
    ul.appendChild(li);
  }
  if (!state.devices.size) ul.innerHTML = `<li class="hint" style="border:none;background:none">Waiting for a device on the network…</li>`;
}
function mkb(label, on, fn) { const b = document.createElement("button"); b.textContent = label; if (on) b.classList.add("on"); b.onclick = fn; return b; }

/* ---------------- capture control (REST) ---------------- */
async function startCap(id, mode) {
  const r = await post(`/api/devices/${id}/start`, { mode }, null, true);
  if (r && r.error) toast("Start failed: " + r.error);
  else toast(`Started ${mode} on ${devName(id)}`);
  setTimeout(refreshState, 400);
}
async function stopCap(id) {
  await post(`/api/devices/${id}/stop`, {}, null, true);
  toast("Stopped " + devName(id)); setTimeout(refreshState, 300);
}
function devName(id) { const i = state.info.get(id), d = state.devices.get(id); return (i && i.name) || (d && d.name) || id; }

/* toolbar Start/Stop act on the selected device */
function syncToolbar() {
  const id = state.selected, d = id && state.devices.get(id);
  const isAgent = d && (d.transports || []).includes("agent");
  const cap = id && capFor(id);
  const streaming = !!cap && cap.desired !== false;
  const canA = d && d.udid && !isAgent;
  $("b-start").disabled = !canA || streaming;
  $("b-stop").disabled = !canA || !streaming;
  $("b-start").textContent = "▶ Start" + (d ? " · " + shortName(d) : "");
  $("b-stop").textContent = "■ Stop";
}
function shortName(d) { const i = state.info.get(d.id); return ((i && i.name) || d.name || "").slice(0, 18); }

$("b-start").onclick = () => state.selected && startCap(state.selected, "syslog");
$("b-stop").onclick = () => state.selected && stopCap(state.selected);
$("rescan").onclick = () => { refreshState(); state.devices.forEach((d) => d.udid && loadInfo(d.id, true)); toast("re-scanning…"); };

/* ---------------- Open file menu ---------------- */
const ofBtn = $("b-openfile"), ofMenu = $("openfile-menu");
ofBtn.onclick = (e) => { e.stopPropagation(); ofMenu.hidden = !ofMenu.hidden; };
document.addEventListener("click", () => { ofMenu.hidden = true; });
ofMenu.onclick = (e) => e.stopPropagation();
ofMenu.querySelectorAll("button[data-act]").forEach((b) => {
  b.onclick = async () => {
    ofMenu.hidden = true;
    const r = await post("/api/session-file/open", { which: b.dataset.act }, null, true);
    if (r && r.ok) toast((b.dataset.act === "reveal" ? "Revealed " : "Opened ") + r.detail);
    else toast("Could not open: " + (r && r.detail || "unknown"));
  };
});

/* ---------------- toolbar: export / clear / pause / filter ---------------- */
$("b-export").onclick = () => {
  const blob = new Blob([state.filtered.map(fmtLine).join("\n") + "\n"], { type: "text/plain" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = `idevicetail-view-${Date.now()}.log`; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  toast(`exported ${state.filtered.length} visible lines`);
};
$("b-clear").onclick = () => { state.all = []; state.filtered = []; state.dropped = 0; render(); updateCount(); toast("view cleared (files on disk are untouched)"); };
$("b-pause").onclick = () => {
  state.paused = !state.paused;
  $("b-pause").textContent = state.paused ? "Resume" : "Pause";
  $("b-pause").classList.toggle("active", state.paused);
  if (!state.paused && state.pauseBuf.length) { const b = state.pauseBuf; state.pauseBuf = []; ingest(b); }
};
$("b-autoscroll").onchange = (e) => { state.follow = e.target.checked; if (state.follow) scroller.scrollTop = scroller.scrollHeight; };
$("f-ok").onclick = () => rebuildFiltered();
for (const id of ["f-search", "f-regex", "f-level", "f-process", "f-subsystem", "f-source"])
  $(id).addEventListener("input", debounce(rebuildFiltered, 150));
$("f-search").addEventListener("keydown", (e) => { if (e.key === "Enter") rebuildFiltered(); });

/* ---------------- setup modal ---------------- */
let setupId = null;
function openSetup(id) {
  setupId = id;
  $("setup-title").textContent = "Setup — " + devName(id);
  $("setup-out").textContent = "";
  $("setup").hidden = false;
}
$("setup-close").onclick = () => ($("setup").hidden = true);
$("setup").onclick = (e) => { if (e.target === $("setup")) $("setup").hidden = true; };
$("setup").querySelectorAll("button[data-act]").forEach((b) => {
  b.onclick = async () => {
    const out = $("setup-out");
    out.textContent += `\n$ ${b.dataset.act} …\n`;
    const r = await post(`/api/devices/${setupId}/provision`, { action: b.dataset.act }, null, true);
    out.textContent += (r && (r.output || r.error || JSON.stringify(r))) + "\n";
    out.scrollTop = out.scrollHeight;
    setTimeout(() => loadInfo(setupId, true), 500);
  };
});

/* ---------------- detail panel ---------------- */
function showDetail(r) {
  $("detail").hidden = false;
  $("detail-body").textContent =
    Object.entries(r).map(([k, v]) => `${k.padEnd(12)} ${typeof v === "object" ? JSON.stringify(v) : v}`).join("\n") +
    "\n\n--- raw ---\n" + (r.raw || "");
}
$("detail-close").onclick = () => ($("detail").hidden = true);

/* ---------------- misc ---------------- */
async function post(url, body, okMsg, raw) {
  try {
    const r = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body || {}) });
    const j = await r.json().catch(() => ({}));
    if (okMsg) toast(okMsg);
    return j;
  } catch (e) { toast(String(e)); return { error: String(e) }; }
}
let toastT;
function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toastT); toastT = setTimeout(() => (t.hidden = true), 4200);
}
function updateCount() {
  const sf = state.sessionFile;
  $("count").innerHTML =
    `<span>${state.filtered.length.toLocaleString()} shown / ${state.all.length.toLocaleString()} buffered</span>` +
    (state.paused ? `<span>PAUSED (+${state.pauseBuf.length})</span>` : "") +
    (state.dropped ? `<span>${state.dropped} dropped (slow tab)</span>` : "") +
    (sf ? `<span class="fp" title="${esc(sf.log_path)}">📄 ${esc(baseName(sf.log_path))} · ${fmtBytes(sf.log_bytes)} · ${(sf.lines_written||0).toLocaleString()} lines</span>` : "");
  $("stats").textContent = `${state.devices.size} device(s)`;
}
function baseName(p) { return (p || "").split(/[\\/]/).pop(); }
function fmtBytes(n) { n = n || 0; if (n < 1024) return n + " B"; if (n < 1048576) return (n/1024).toFixed(0) + " KB"; return (n/1048576).toFixed(1) + " MB"; }
function fmtTime(ts) { const d = new Date(ts * 1000); return d.toTimeString().slice(0, 8) + "." + String(d.getMilliseconds()).padStart(3, "0"); }
function fmtLine(r) {
  return `${fmtTime(r.ts)}  ${(r.device_name||"").padEnd(14).slice(0,14)}  ${(r.process||"").padEnd(18).slice(0,18)}  ${r.level.toUpperCase().padEnd(7)}  ${r.subsystem ? "["+r.subsystem+(r.category?":"+r.category:"")+"] " : ""}${r.message||""}`;
}
function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

connect();
updateCount();
setInterval(refreshState, 5000);
