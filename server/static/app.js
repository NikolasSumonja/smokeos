"use strict";
// SmokeOS dashboard: farm view, rig pages with tabs, flight sheets, web shell.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let DATA = null;            // last /api/rigs answer
let ROUTE = { view: "farm" };
let FS = null;              // flight sheets cache
const rigUI = {};           // per-rig UI state: {range, chart, oc}

// ------------------------------------------------------------------ helpers
async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", "X-SmokeOS": "1", ...(opts.headers || {}) },
  });
  if (r.status === 401 && !path.includes("/shell")) { location.href = "/login"; throw new Error("login"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `Error ${r.status}`);
  return j;
}
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove("show"), 2800);
}
function fmtHR(h) {
  if (!h) return "0";
  const u = ["H/s", "kH/s", "MH/s", "GH/s", "TH/s", "PH/s", "EH/s"];
  let i = 0; while (h >= 1000 && i < u.length - 1) { h /= 1000; i++; }
  return `${h.toFixed(h >= 100 ? 1 : 2)} ${u[i]}`;
}
function fmtDur(s) {
  s = Math.max(0, Math.floor(s));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}
function cmpVer(a, b) {
  const pa = String(a).split(".").map(Number), pb = String(b).split(".").map(Number);
  for (let i = 0; i < 3; i++) { if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) - (pb[i] || 0); }
  return 0;
}
// hashrate without the unit, in a given unit (for compact GPU tiles like Hive's "84.53")
function hrIn(h, unitOf) {
  if (h == null) return "–";
  const u = ["H/s", "kH/s", "MH/s", "GH/s", "TH/s", "PH/s"]; let d = 1, i = 0;
  while (unitOf / d >= 1000 && i < u.length - 1) { d *= 1000; i++; }
  return (h / d).toFixed(h / d >= 100 ? 1 : 2);
}
const n0 = (v) => (v === null || v === undefined ? "–" : Math.round(v));
const tempClass = (t) => (t == null ? "" : t >= 80 ? "t-crit" : t >= 72 ? "t-warn" : "t-ok");
const fanClass = (g) => (g.fan === 0 && g.temp > 50 ? "t-warn" : "");
// Hive-style heat colours for chips and tiles
const heat = (t) => (t == null ? "h-n" : t >= 75 ? "h-r" : t >= 70 ? "h-o" : t >= 60 ? "h-y" : "h-g");
const fanHeat = (f) => (f == null ? "h-n" : f === 0 ? "h-n" : f >= 90 ? "h-r" : f >= 75 ? "h-o" : f >= 55 ? "h-y" : "h-b");
const isPhone = () => matchMedia("(max-width:640px)").matches;
function coinTotals(rigs) {
  const by = {};
  for (const r of rigs) {
    if (!r.online || !r.report.hashrate) continue;
    const c = (r.report.flightsheet && r.report.flightsheet.coin) || (r.report.miner && r.report.miner.name) || "?";
    by[c] = (by[c] || 0) + r.report.hashrate;
  }
  return Object.entries(by);
}
function modal(html) { $("#modalBox").innerHTML = html; $("#modal").classList.add("show"); }
function closeModal() { $("#modal").classList.remove("show", "bottom"); $("#modalBox").onclick = null; }
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
function confirmBox(text, okLabel = "Confirm", danger = false) {
  return new Promise((res) => {
    modal(`<h3>${esc(text)}</h3><div class="row">
      <button class="btn" data-a="no">Cancel</button>
      <button class="btn ${danger ? "danger" : "primary"}" data-a="yes">${esc(okLabel)}</button></div>`);
    $("#modalBox").onclick = (e) => { const a = e.target.dataset.a; if (!a) return; closeModal(); res(a === "yes"); };
  });
}
function copy(text, label = "Copied") {
  navigator.clipboard.writeText(text).then(() => toast(label)).catch(() => toast("Copy is blocked here – select the text instead"));
}
const rigById = (id) => DATA && DATA.rigs.find((r) => r.id === Number(id));
const b64enc = (s) => { const b = new TextEncoder().encode(s); let o = ""; for (let i = 0; i < b.length; i += 0x8000) o += String.fromCharCode(...b.subarray(i, i + 0x8000)); return btoa(o); };
const b64dec = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

// ------------------------------------------------------------------ routing
const TABS = [["overview", "Overview"], ["flightsheet", "Flight Sheet"], ["overclock", "Overclocking"], ["autofan", "Autofan"],
  ["stats", "Stats"], ["activity", "Activity"], ["settings", "Settings"]];
function parseRoute() {
  const h = location.hash.replace(/^#/, "");
  let m;
  if (h === "flightsheets") return { view: "flightsheets" };
  if ((m = h.match(/^rig-(\d+)(?:-([a-z]+))?$/))) return { view: "rig", id: Number(m[1]), tab: TABS.some((t) => t[0] === m[2]) ? m[2] : "overview" };
  return { view: "farm" };
}
function go(hash) { if (location.hash !== "#" + hash) location.hash = hash; else render(true); }
window.addEventListener("hashchange", () => render(true));

function sheet(title, items) {
  modal(`<div class="sheet"><h3>${esc(title)}</h3>${items.map((it, i) => `<button class="btn sheetbtn ${it.cls || ""}" data-i="${i}">${it.label}</button>`).join("")}
    <button class="btn sheetbtn" data-i="x">Cancel</button></div>`);
  $("#modal").classList.add("bottom");
  $$("#modalBox [data-i]").forEach((b) => b.onclick = () => { $("#modal").classList.remove("bottom"); closeModal(); const it = items[b.dataset.i]; if (it) it.go(); });
}
const ICON = (d) => `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
const IC = {
  farm: ICON('<rect x="3" y="4" width="18" height="6" rx="1.5"/><rect x="3" y="14" width="18" height="6" rx="1.5"/><path d="M7 7h.01M7 17h.01"/>'),
  fs: ICON('<path d="M5 15c-1.5 1.5-2 4-2 6 2 0 4.5-.5 6-2"/><path d="M9 15l-3-3c1-4 5-9 13-9 0 8-5 12-9 13z"/><circle cx="15" cy="9" r="1.6"/>'),
  upd: ICON('<path d="M20 11a8 8 0 0 0-14.3-4.9L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.3 4.9L20 16"/><path d="M20 20v-4h-4"/>'),
  add: ICON('<path d="M12 5v14M5 12h14"/>'),
  more: ICON('<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>'),
  ov: ICON('<rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="5" rx="1.5"/><rect x="13" y="10" width="8" height="11" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/>'),
  oc: ICON('<path d="M6 4v16M12 4v16M18 4v16"/><circle cx="6" cy="9" r="2" fill="currentColor"/><circle cx="12" cy="15" r="2" fill="currentColor"/><circle cx="18" cy="8" r="2" fill="currentColor"/>'),
  stats: ICON('<path d="M3 12h4l3-7 4 14 3-7h4"/>'),
};
function renderBottomBar() {
  const bar = $("#bottombar"); if (!bar) return;
  const badge = DATA ? DATA.rigs.filter((r) => r.outdated).length + (DATA.latest && cmpVer(DATA.latest, DATA.version) > 0 ? 1 : 0) : 0;
  let items;
  if (ROUTE.view === "rig") {
    const id = ROUTE.id, t = ROUTE.tab;
    items = [["overview", IC.ov, "Overview", `#rig-${id}-overview`], ["flightsheet", IC.fs, "Fl Sheet", `#rig-${id}-flightsheet`],
      ["overclock", IC.oc, "Overclock", `#rig-${id}-overclock`], ["stats", IC.stats, "Stats", `#rig-${id}-stats`]]
      .map(([k, ic, l, h]) => `<a href="${h}" class="${t === k ? "active" : ""}"><span class="bi">${ic}</span>${l}</a>`).join("")
      + `<button id="bbMore" class="${["autofan", "activity", "settings"].includes(t) ? "active" : ""}"><span class="bi">${IC.more}</span>More</button>`;
  } else {
    items = `<a href="#farm" class="${ROUTE.view === "farm" ? "active" : ""}"><span class="bi">${IC.farm}</span>Farm</a>
      <a href="#flightsheets" class="${ROUTE.view === "flightsheets" ? "active" : ""}"><span class="bi">${IC.fs}</span>Fl Sheets</a>
      <button id="bbUpd"><span class="bi">${IC.upd}${badge ? `<span class="badge">${badge}</span>` : ""}</span>Updates</button>
      <button id="bbAdd"><span class="bi">${IC.add}</span>Add rig</button>
      <button id="bbMore"><span class="bi">${IC.more}</span>More</button>`;
  }
  bar.innerHTML = items;
  const m = $("#bbMore", bar);
  if (ROUTE.view === "rig") m.onclick = () => sheet(rigById(ROUTE.id) ? rigById(ROUTE.id).name : "Rig", [
    { label: "Autofan", go: () => go(`rig-${ROUTE.id}-autofan`) }, { label: "Activity & miner output", go: () => go(`rig-${ROUTE.id}-activity`) },
    { label: "Settings", go: () => go(`rig-${ROUTE.id}-settings`) }, { label: "&gt;_ Shell", cls: "primary", go: () => openShell(ROUTE.id) },
    { label: "← All rigs", go: () => go("farm") }]);
  else {
    m.onclick = () => sheet("SmokeOS", [{ label: "Security log", go: () => $("#auditBtn").click() }, { label: "Log out", cls: "danger", go: () => $("#logout").click() }]);
    $("#bbUpd", bar).onclick = showUpdates; $("#bbAdd", bar).onclick = addRig;
  }
}

function render(full) {
  ROUTE = parseRoute();
  renderBottomBar();
  $$("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === (ROUTE.view === "rig" ? "farm" : ROUTE.view)));
  if (!DATA) return;
  if (ROUTE.view === "farm") renderFarm();
  else if (ROUTE.view === "flightsheets") { if (full) renderFlightSheets(); }
  else renderRig(full);
}

// ------------------------------------------------------------------ farm view
function gpuChips(gpus) {
  return gpus.map((g) => `<span class="chip ${heat(g.temp)}" title="GPU${g.index} ${esc(g.name)}">${n0(g.temp)}°</span>`).join("");
}
function rigState(r) { return !r.online ? "off" : r.alerts.some((a) => a.level === "crit") ? "off" : r.alerts.length ? "warn" : "on"; }
function renderFarm() {
  const d = DATA, v = $("#view");
  const online = d.rigs.filter((r) => r.online), gpus = online.flatMap((r) => r.report.gpus || []);
  const temps = gpus.map((g) => g.temp).filter((t) => t != null), hot = temps.length ? Math.max(...temps) : null;
  const crit = d.rigs.flatMap((r) => r.alerts.filter((a) => a.level === "crit").map((a) => ({ ...a, rig: r })));
  v.innerHTML = `
    <div class="strip">
      <div class="st"><div class="v">${online.length}${d.rigs.length - online.length ? `<sup class="t-crit">${d.rigs.length - online.length}</sup>` : ""}</div><div class="k">Rigs online</div></div>
      <div class="st"><div class="v">${gpus.length}${gpus.filter((g) => g.temp >= 75).length ? `<sup class="t-crit">${gpus.filter((g) => g.temp >= 75).length}</sup>` : ""}</div><div class="k">GPUs</div></div>
      <div class="st"><div class="v"><span class="bolt">⚡</span>${(gpus.reduce((s, g) => s + (g.power || 0), 0) / 1000).toFixed(3)}<small> kW</small></div><div class="k">Consumption</div></div>
      ${coinTotals(d.rigs).map(([c, h]) => `<div class="st"><div class="v">${fmtHR(h).replace(/ (\S+)$/, "<small> $1</small>")}</div><div class="k">${esc(c)}</div></div>`).join("")}
      <div class="st"><div class="v ${tempClass(hot)}">${hot == null ? "–" : Math.round(hot) + "°"}</div><div class="k">Hottest GPU</div></div>
    </div>
    <div class="alerts">${crit.map((a) => `<a class="alert crit" href="#rig-${a.rig.id}"><b>${esc(a.rig.name)}</b> — ${esc(a.msg)}</a>`).join("")}</div>
    ${d.rigs.length ? `<div class="farmlist">${d.rigs.map((r) => {
      const rep = r.report || {}, g = rep.gpus || [], fs = rep.flightsheet, m = rep.miner || {};
      return `<a class="farmrow state-${rigState(r)}" href="#rig-${r.id}">
        <span class="dot ${rigState(r)}"></span>
        <span class="fr-name"><b>${esc(r.name)}</b><span class="muted">${r.online ? esc([(rep.system || {}).local_ip, rep.boot_time ? fmtDur(d.now - rep.boot_time) : ""].filter(Boolean).join(" · ")) : r.last_seen ? "offline " + fmtDur(d.now - r.last_seen) : "waiting for agent"}</span></span>
        <span class="fr-fs">${fs ? `<b>${esc(fs.name)}</b><span class="muted">${esc(fs.coin || "")} · ${esc(m.name || "")}</span>` : `<span class="muted">${esc(m.name || (rep.manage_miner === false ? "HiveOS" : "no flight sheet"))}</span>`}</span>
        <span class="fr-hr">${r.online ? fmtHR(rep.hashrate) : "–"}</span>
        <span class="fr-chips">${gpuChips(g)}</span>
        <span class="fr-pw">${r.online ? Math.round(g.reduce((s, x) => s + (x.power || 0), 0)) + " W" : ""}</span>
        <span class="fr-tags">${r.alerts.filter((a) => r.online).map((a) => `<span class="tag ${a.level}">${esc(a.msg)}</span>`).join(" ")}${r.outdated ? ' <span class="tag warn">update</span>' : ""}</span>
      </a>`;
    }).join("")}</div>` : `<div class="empty">No rigs yet.<br><br><button class="btn primary" id="firstRig">Add your first rig</button></div>`}`;
  const f = $("#firstRig"); if (f) f.onclick = addRig;
}

// ------------------------------------------------------------------ rig view
function ui(id) { return (rigUI[id] = rigUI[id] || { range: 24 }); }
function renderRig(full) {
  const r = rigById(ROUTE.id), v = $("#view");
  if (!r) { v.innerHTML = `<div class="empty">That rig doesn't exist (any more). <a href="#farm">Back to the farm</a></div>`; return; }
  const page = $("#rigpage");
  if (full || !page || page.dataset.id !== String(r.id) || page.dataset.tab !== ROUTE.tab) {
    v.innerHTML = `<div id="rigpage" data-id="${r.id}" data-tab="${ROUTE.tab}">
      <div class="righead">
        <a class="back" href="#farm">← Farm</a>
        <div class="switcher"><button class="btn switchbtn" id="switchBtn"><b>${esc(r.name)}</b> ⌄</button>
          <div class="switchpop" id="switchPop" hidden><input id="switchQ" placeholder="Rig name or IP"><div id="switchList"></div></div></div>
        <div class="spacer"></div>
        <div class="rigactions">
          <button class="btn small" data-cmd="restart_miner">↻ Restart<span class="long"> miner</span></button>
          <button class="btn small" data-cmd="toggle_miner" id="toggleMiner">■ Stop<span class="long"> miner</span></button>
          <button class="btn small danger" data-cmd="reboot">⏻ Reboot</button>
          <button class="btn small primary" id="shellBtn" title="Open a terminal on this rig">&gt;_ Shell</button>
        </div>
      </div>
      <div class="statusbox" id="statusbox"></div>
      <div class="infoline" id="infoline"></div>
      <nav class="tabs">${TABS.map(([k, l]) => `<a href="#rig-${r.id}-${k}" class="${k === ROUTE.tab ? "active" : ""}">${l}</a>`).join("")}</nav>
      <section id="tab"></section>
    </div>`;
    wireRigHead(r);
    renderTab(r, true);
  } else renderTab(r, false);
  renderRigLive(r);
}
function wireRigHead(r) {
  $("#switchBtn").onclick = (e) => { e.stopPropagation(); const p = $("#switchPop"); p.hidden = !p.hidden; if (!p.hidden) { $("#switchQ").value = ""; fillSwitch(""); $("#switchQ").focus(); } };
  $("#switchQ").oninput = (e) => fillSwitch(e.target.value);
  $("#switchQ").onkeydown = (e) => { if (e.key === "Enter") { const a = $("#switchList a"); if (a) a.click(); } if (e.key === "Escape") $("#switchPop").hidden = true; };
  document.onclick = (e) => { const p = $("#switchPop"); if (p && !p.hidden && !e.target.closest(".switcher")) p.hidden = true; };
  $$("[data-cmd]", $(".rigactions")).forEach((b) => b.onclick = async () => {
    const rr = rigById(r.id); let t = b.dataset.cmd;
    if (t === "toggle_miner") t = rr.report.miner && rr.report.miner.running === false ? "start_miner" : "stop_miner";
    if (t === "reboot" && !(await confirmBox(`Reboot ${rr.name}?`, "Reboot", true))) return;
    if (t === "stop_miner" && !(await confirmBox(`Stop mining on ${rr.name}?`, "Stop", true))) return;
    sendCmd(r.id, t, {});
  });
  $("#shellBtn").onclick = () => openShell(r.id);
}
function fillSwitch(qs) {
  qs = qs.toLowerCase();
  $("#switchList").innerHTML = DATA.rigs.filter((x) => x.name.toLowerCase().includes(qs) || ((x.report.system || {}).local_ip || "").includes(qs))
    .map((x) => `<a href="#rig-${x.id}-${ROUTE.tab}"><span class="dot ${rigState(x)}"></span>${esc(x.name)}<span class="muted">${esc((x.report.system || {}).local_ip || "")}</span></a>`).join("") || `<div class="muted">No match</div>`;
}
function renderRigLive(r) {
  const rep = r.report || {}, g = rep.gpus || [], m = rep.miner || {}, fs = rep.flightsheet, sys = rep.system || {}, now = DATA.now;
  const tm = $("#toggleMiner"); if (tm) tm.innerHTML = m.running === false ? '▶ Start<span class="long"> miner</span>' : '■ Stop<span class="long"> miner</span>';
  $("#statusbox").innerHTML = `
    <div class="sb-left">
      <div class="sb-fs"><span class="dot ${rigState(r)}"></span><b>${fs ? esc(fs.name) : rep.manage_miner === false ? "HiveOS" : "No flight sheet"}</b><span class="muted"> flight sheet</span></div>
      <div class="sb-miner"><span class="mono">${esc(m.name || "–")}</span>${m.running === false ? ' <span class="tag warn">stopped</span>' : ""}<span class="muted">${fs && fs.coin ? " · " + esc(fs.coin) : ""}</span></div>
      <div class="sb-hr">${r.online ? fmtHR(rep.hashrate) : "offline"}</div>
    </div>
    <div class="sb-gpus">${g.map((x) => `<div class="gt" title="GPU${x.index} ${esc(x.name)}"><div class="gt-t ${heat(x.temp)}">${n0(x.temp)}°</div><div class="gt-f ${fanHeat(x.fan)}">${n0(x.fan)}%</div><div class="gt-h">${hrIn(x.hashrate, rep.hashrate || x.hashrate)}</div><div class="gt-p">${n0(x.power)}W</div></div>`).join("")}</div>`;
  $("#infoline").innerHTML = r.online ? `
    <span><b>BOOTED</b> ${fmtDur(now - (rep.boot_time || now))} ago</span>
    <span><b>MINER UPTIME</b> ${m.uptime ? fmtDur(m.uptime) : "–"}</span>
    <span><b>IP</b> ${esc(sys.local_ip || "–")}</span>
    <span><b>AGENT</b> v${esc(rep.agent_version || "?")}${r.outdated ? ' <span class="tag warn">update</span>' : ""}</span>
    ${r.alerts.map((a) => `<span class="tag ${a.level}">${esc(a.msg)}</span>`).join(" ")}`
    : `<span class="tag crit">${esc((r.alerts[0] || {}).msg || "offline")}</span>`;
}
function renderTab(r, full) {
  const t = ROUTE.tab, el = $("#tab");
  if (t === "overview") return tabOverview(r, el);
  if (!full) { if (t === "activity") minerLogLive(r); if (t === "overclock") ocLive(r); return; }   // tabs with inputs aren't redrawn by the 5 s refresh
  if (t === "flightsheet") return tabFlightSheet(r, el);
  if (t === "overclock") return tabOverclock(r, el);
  if (t === "autofan") return tabAutofan(r, el);
  if (t === "stats") return tabStats(r, el);
  if (t === "activity") return tabActivity(r, el);
  if (t === "settings") return tabSettings(r, el);
}

// --- Overview
const EVC = {};   // per-rig cache: recent events + sparkline points
async function overviewExtras(r) {
  const c = (EVC[r.id] = EVC[r.id] || { t: 0 });
  if (Date.now() - c.t > 30000) {
    c.t = Date.now();
    try {
      const [log, hist] = await Promise.all([api(`/api/rigs/${r.id}/log`), api(`/api/rigs/${r.id}/history?hours=6`)]);
      c.events = [...log.events.map((e) => ({ ts: e.ts, lvl: e.level, msg: e.message })),
        ...log.commands.filter((x) => x.type !== "open_shell").map((x) => ({ ts: x.created, lvl: x.status === "failed" ? "warn" : "cmd", msg: `${x.type.replace(/_/g, " ")} ${x.status}` }))]
        .sort((a, b) => b.ts - a.ts).slice(0, 6);
      c.points = hist.points;
    } catch { /* offline */ }
  }
  const box = $("#ovx"); if (!box || !c.events) return;
  box.innerHTML = `<div class="evchips">${c.events.map((e) => `<span class="evchip ${e.lvl}">${esc(e.msg)} <span class="muted">${fmtDur(Date.now() / 1000 - e.ts)}</span></span>`).join("")}</div>
    <div class="spark"><canvas id="spark"></canvas><div class="spark-v"><span class="muted">6h</span> ${fmtHR(r.report.hashrate)}</div></div>`;
  if (window.Chart && c.points && c.points.length) {
    new Chart($("#spark"), { type: "line", data: { labels: c.points.map(() => ""), datasets: [{ data: c.points.map((p) => p.hashrate || 0), borderColor: "#2ecc71",
      backgroundColor: "rgba(46,204,113,.12)", fill: true, pointRadius: 0, borderWidth: 1.5, tension: .3 }] },
      options: { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: { x: { display: false }, y: { display: false, beginAtZero: true } } } });
  }
}
function tabOverview(r, el) {
  const rep = r.report || {}, g = rep.gpus || [], s = rep.system || {};
  const pw = Math.round(g.reduce((a, x) => a + (x.power || 0), 0));
  const keep = $("#ovx") ? $("#ovx").innerHTML : "";
  el.innerHTML = `<div id="ovx">${keep}</div>
    <div class="tablewrap"><table class="gputable">
      <thead><tr><th>GPU</th><th>Model</th><th>Hashrate</th><th>Temp</th><th>Fan</th><th>Power</th><th>Limit</th><th>Core</th><th>Mem</th><th>Load</th></tr></thead>
      <tbody>${g.map((x) => `<tr><td class="c-gpu">GPU ${x.index}<div class="muted small">${esc(x.bus || x.amd_card || "")}</div></td>
        <td class="c-model ${x.vendor === "amd" ? "amd" : "nv"}">${esc(x.name)}</td>
        <td data-l="Hashrate" class="c-hr">${x.hashrate != null ? fmtHR(x.hashrate) : "–"}</td>
        <td data-l="Temp" class="${tempClass(x.temp)}">${n0(x.temp)}°C</td><td data-l="Fan" class="${fanClass(x)}">${n0(x.fan)}%</td>
        <td data-l="Power">${n0(x.power)} W</td><td data-l="Limit">${x.power_limit ? n0(x.power_limit) + " W" : "–"}</td>
        <td data-l="Core">${n0(x.core)}</td><td data-l="Mem">${n0(x.mem)}</td><td data-l="Load">${n0(x.util)}%</td></tr>`).join("") || `<tr><td colspan="10" class="muted">${r.online ? "No GPUs reported" : "Rig is offline"}</td></tr>`}</tbody>
    </table></div>
    <div class="systiles">
      <div class="stat"><div class="v">${s.load ? s.load.map((x) => x.toFixed(2)).join(" ") : "–"}</div><div class="k">Load average</div></div>
      <div class="stat"><div class="v">${s.cpu_temp != null ? s.cpu_temp + "°" : "–"}</div><div class="k">CPU temp</div></div>
      <div class="stat"><div class="v">${s.disk_free_gb != null ? s.disk_free_gb + " GB" : "–"}</div><div class="k">Free space</div></div>
      <div class="stat"><div class="v">${s.ram_free_mb != null ? (s.ram_free_mb / 1024).toFixed(1) + " GB" : "–"}</div><div class="k">Free RAM</div></div>
      <div class="stat"><div class="v">${pw} W</div><div class="k">GPU power</div></div>
      <div class="stat"><div class="v">${esc(s.nvidia_driver || "–")}</div><div class="k">NVIDIA driver</div></div>
    </div>
    <div class="sysdetails">
      <div><span class="muted">Motherboard</span> ${esc(s.board || "–")}</div><div><span class="muted">System</span> ${esc(s.os || "–")}</div>
      <div><span class="muted">CPU</span> ${esc(s.cpu || "–")}</div><div><span class="muted">Kernel</span> ${esc(s.kernel || "–")}</div>
      <div><span class="muted">Hostname</span> ${esc(rep.hostname || "–")}</div><div><span class="muted">Local IP</span> ${esc(s.local_ip || "–")}</div>
      <div><span class="muted">MAC</span> <span class="mono">${esc(s.mac || "–")}</span></div><div><span class="muted">Agent</span> v${esc(rep.agent_version || "?")}</div>
    </div>`;
  if (!keep || Date.now() - ((EVC[r.id] || {}).t || 0) > 30000) overviewExtras(r);
}

// --- Flight sheet (per rig)
async function loadFS() { FS = await api("/api/flightsheets"); return FS; }
async function tabFlightSheet(r, el) {
  el.innerHTML = `<div class="muted">Loading…</div>`;
  const [fsd, cur] = await Promise.all([loadFS(), api(`/api/rigs/${r.id}/flightsheet`)]);
  const rep = r.report || {};
  if (rep.manage_miner === false) {
    el.innerHTML = `<div class="panel"><b>This rig is monitor-only.</b><p class="muted">HiveOS runs its miner, so SmokeOS doesn't change it. Flight sheets work once the rig boots from a SmokeOS USB.</p></div>`;
    return;
  }
  const curFs = fsd.flightsheets.find((f) => f.id === cur.flightsheet_id);
  el.innerHTML = `<div class="panel">
      <div class="section-title">Current</div>
      <div class="fs-current">${curFs ? fsCard(curFs, fsd.miners, false) : rep.flightsheet ? `<b>${esc(rep.flightsheet.name)}</b>` : `<span class="muted">None. The rig runs the MINER_CMD from its rig.conf${(rep.miner || {}).name ? ` (${esc(rep.miner.name)})` : ""}.</span>`}</div>
      <div class="section-title">Switch to</div>
      ${fsd.flightsheets.length ? `<div class="fs-pick">
        <select id="fsSel">${fsd.flightsheets.map((f) => `<option value="${f.id}" ${curFs && f.id === curFs.id ? "selected" : ""}>${esc(f.name)}${f.coin ? " · " + esc(f.coin) : ""} (${esc(fsd.miners[f.miner].label)})</option>`).join("")}</select>
        <button class="btn primary" id="fsApply">Apply</button></div>
        <p class="muted small">The miner restarts with the new settings within a few seconds.</p>`
      : `<p class="muted">No flight sheets yet.</p>`}
      <a class="btn small" href="#flightsheets">Manage flight sheets →</a>
    </div>`;
  const ap = $("#fsApply");
  if (ap) ap.onclick = async () => {
    const id = Number($("#fsSel").value);
    try { await api(`/api/flightsheets/${id}/apply`, { method: "POST", body: JSON.stringify({ rig_ids: [r.id] }) }); toast("Flight sheet sent – miner restarting"); setTimeout(() => { refresh().then(() => render(true)); }, 2500); }
    catch (e) { toast(e.message); }
  };
}

// --- Overclocking
const OC_KEYS = [["core_lock", "Core lock", "MHz", 300, 3200], ["core_offset", "Core offset", "MHz", -1000, 1000], ["mem_offset", "Mem offset", "Hive units", -4000, 4000],
  ["power_limit", "Power limit", "W", 50, 600], ["fan", "Fan", "%", 20, 100]];
async function tabOverclock(r, el) {
  const u = ui(r.id);
  el.innerHTML = `<div class="muted">Loading…</div>`;
  try { u.oc = (await api(`/api/rigs/${r.id}/oc`)).profile || {}; } catch { u.oc = {}; }
  const gpus = (r.report || {}).gpus || [];
  el.innerHTML = `<div class="tablewrap"><table class="oc">
      <thead><tr><th>GPU</th>${OC_KEYS.map(([, l, unit]) => `<th>${l}<br><span class="muted">${unit}</span></th>`).join("")}<th></th></tr></thead>
      <tbody>${gpus.length ? gpus.map((g) => {
        if (g.vendor !== "nvidia") return `<tr><td>GPU${g.index} · ${esc(g.name)}</td><td colspan="6" class="muted">AMD: monitor only for now</td></tr>`;
        const o = u.oc[String(g.index)] || {};
        return `<tr data-gpu="${g.index}"><td class="c-model">GPU${g.index} · ${esc(g.name)}<div class="oc-live" data-live="${g.index}"></div></td>${OC_KEYS.map(([k, l, unit, lo, hi]) => `<td data-l="${l} (${unit})"><input type="number" inputmode="numeric" data-k="${k}" min="${lo}" max="${hi}" value="${o[k] ? o[k] : ""}"
          placeholder="${k === "power_limit" && g.power_default ? Math.round(g.power_default) : k === "fan" ? "auto" : "stock"}"></td>`).join("")}
          <td class="c-copy"><button class="btn small" data-copy="${g.index}" title="Copy this row to every card of the same model">Copy to same</button></td></tr>`;
      }).join("") : `<tr><td colspan="7" class="muted">Waiting for GPU data from the rig…</td></tr>`}</tbody></table></div>
    <div class="actions"><button class="btn primary" id="ocSave">Save &amp; apply</button><button class="btn" id="ocStock">Reset all to stock</button></div>
    <p class="muted small">Empty = left alone (nothing is pre-set). Clearing a box puts that setting back to stock. Saved on the rig and re-applied after every reboot.
      Memory offset uses the same numbers as HiveOS (Linux units, about 2× Afterburner). NVIDIA only for now.</p>`;
  $$("[data-copy]", el).forEach((b) => b.onclick = () => {
    const src = $(`tr[data-gpu="${b.dataset.copy}"]`, el), model = gpus.find((g) => String(g.index) === b.dataset.copy).name;
    $$("tr[data-gpu]", el).forEach((tr) => {
      const g = gpus.find((x) => String(x.index) === tr.dataset.gpu);
      if (tr !== src && g && g.name === model) $$("input[data-k]", tr).forEach((i) => { i.value = $(`input[data-k="${i.dataset.k}"]`, src).value; });
    });
    toast(`Copied to every ${model}`);
  });
  ocLive(r);
  const save = async () => {
    const profile = {};
    $$("tr[data-gpu]", el).forEach((tr) => { const g = {}; $$("input[data-k]", tr).forEach((i) => { g[i.dataset.k] = Number(i.value) || 0; }); profile[tr.dataset.gpu] = g; });
    try { await api(`/api/rigs/${r.id}/oc`, { method: "PUT", body: JSON.stringify({ profile }) }); u.oc = profile; toast("Overclock sent – the result shows on the Activity tab"); }
    catch (e) { toast(e.message); }
  };
  $("#ocSave").onclick = save;
  $("#ocStock").onclick = async () => {
    if (!(await confirmBox(`Reset every card on ${r.name} to stock clocks, power and auto fan?`, "Reset", true))) return;
    $$("input[data-k]", el).forEach((i) => { i.value = ""; }); save();
  };
}

function ocLive(r) {
  const rep = rigById(r.id).report || {};
  for (const g of rep.gpus || []) {
    const el = $(`[data-live="${g.index}"]`); if (!el) continue;
    el.innerHTML = `<b>${g.hashrate != null ? fmtHR(g.hashrate) : "– H/s"}</b> · <span class="${tempClass(g.temp)}">${n0(g.temp)}°</span> · ${n0(g.power)} W · ${n0(g.core)} MHz`;
  }
}

// --- Autofan
async function tabAutofan(r, el) {
  el.innerHTML = `<div class="muted">Loading…</div>`;
  const af = (await api(`/api/rigs/${r.id}/autofan`)).autofan;
  el.innerHTML = `<div class="panel">
    <label class="switchrow"><input type="checkbox" id="afOn" ${af.enabled ? "checked" : ""}> <b>Autofan</b> <span class="muted">speeds each card's fans up and down to hold the target temperature</span></label>
    <div class="formgrid">
      <label>Target temperature <span class="muted">°C</span><input type="number" id="afT" min="40" max="85" value="${af.target}"></label>
      <label>Minimum fan <span class="muted">%</span><input type="number" id="afMin" min="0" max="100" value="${af.min_fan}"></label>
      <label>Maximum fan <span class="muted">%</span><input type="number" id="afMax" min="0" max="100" value="${af.max_fan}"></label>
    </div>
    <div class="actions"><button class="btn primary" id="afSave">Save &amp; apply</button></div>
    <p class="muted small">A fixed Fan % on the Overclocking tab overrides autofan for that card. NVIDIA only. A card whose fans are dead can't be cooled this way. The temp guard still pauses mining at the rig's limit.</p>
  </div>`;
  $("#afSave").onclick = async () => {
    try {
      await api(`/api/rigs/${r.id}/autofan`, { method: "PUT", body: JSON.stringify({ enabled: $("#afOn").checked, target: Number($("#afT").value), min_fan: Number($("#afMin").value), max_fan: Number($("#afMax").value) }) });
      toast("Autofan settings sent");
    } catch (e) { toast(e.message); }
  };
}

// --- Stats
function tabStats(r, el) {
  const u = ui(r.id);
  el.innerHTML = `<div class="ranges">${[[6, "6h"], [24, "24h"], [168, "7d"]].map(([h, l]) => `<button class="btn small ${u.range === h ? "active" : ""}" data-range="${h}">${l}</button>`).join("")}</div>
    <div class="chartbox"><canvas id="chart"></canvas></div>`;
  $$("[data-range]", el).forEach((b) => b.onclick = () => { u.range = Number(b.dataset.range); tabStats(r, el); });
  loadHistory(r.id);
}
async function loadHistory(id) {
  const u = ui(id), cv = $("#chart"); if (!cv || !window.Chart) return;
  let j; try { j = await api(`/api/rigs/${id}/history?hours=${u.range}`); } catch { return; }
  const pts = j.points;
  const labels = pts.map((p) => { const d = new Date(p.ts * 1000); return u.range > 24 ? d.toLocaleDateString([], { weekday: "short" }) + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); });
  const maxHR = Math.max(1, ...pts.map((p) => p.hashrate || 0)), units = ["H/s", "kH/s", "MH/s", "GH/s", "TH/s", "PH/s"];
  let div = 1, ui_ = 0; while (maxHR / div >= 1000 && ui_ < units.length - 1) { div *= 1000; ui_++; }
  if (u.chart) u.chart.destroy();
  u.chart = new Chart(cv, {
    type: "line",
    data: { labels, datasets: [
      { label: `Hashrate (${units[ui_]})`, data: pts.map((p) => (p.hashrate || 0) / div), borderColor: "#ff7a1a", backgroundColor: "rgba(255,122,26,.12)", fill: true, yAxisID: "y", pointRadius: 0, tension: .25, borderWidth: 2 },
      { label: "Power (W)", data: pts.map((p) => p.power), borderColor: "#4da3ff", yAxisID: "y1", pointRadius: 0, tension: .25, borderWidth: 1.5 },
      { label: "Hottest °C", data: pts.map((p) => p.max_temp), borderColor: "#ff4d4f", yAxisID: "y1", pointRadius: 0, tension: .25, borderWidth: 1, borderDash: [4, 3] }] },
    options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
      plugins: { legend: { labels: { color: "#8b93a1", boxWidth: 12 } } },
      scales: { x: { ticks: { color: "#8b93a1", maxTicksLimit: 7 }, grid: { color: "#1f242b" } },
        y: { beginAtZero: true, ticks: { color: "#ff7a1a" }, grid: { color: "#1f242b" } },
        y1: { position: "right", beginAtZero: true, ticks: { color: "#4da3ff" }, grid: { display: false } } } },
  });
}

// --- Activity
function tabActivity(r, el) {
  el.innerHTML = `<div class="section-title">Miner output</div><pre class="log mono" id="minerlog"></pre>
    <div class="section-title">Events &amp; commands</div><div class="list" id="eventlist"><span class="muted">Loading…</span></div>`;
  minerLogLive(r);
  loadLog(r.id);
}
function minerLogLive(r) {
  const el = $("#minerlog"); if (!el) return;
  const rep = rigById(r.id).report || {}, m = rep.miner || {};
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 10;
  el.textContent = (m.log || []).join("\n") || (rep.manage_miner === false ? "Monitor-only mode (miner managed by HiveOS)." : "No output yet.");
  if (atBottom) el.scrollTop = el.scrollHeight;
}
async function loadLog(id) {
  const box = $("#eventlist"); if (!box) return;
  let j; try { j = await api(`/api/rigs/${id}/log`); } catch { return; }
  const rows = [
    ...j.events.map((e) => ({ ts: e.ts, html: `<span class="tag ${e.level === "crit" ? "crit" : e.level === "warn" ? "warn" : ""}">${esc(e.level)}</span> ${esc(e.message)}` })),
    ...j.commands.filter((x) => x.type !== "open_shell").map((x) => ({ ts: x.created, html: `<span class="tag">cmd</span> ${esc(x.type)} → <b>${esc(x.status)}</b> <span class="muted">by ${esc(x.by_user)}</span>${x.result ? `<pre class="cmdres mono">${esc(x.result.slice(0, 1200))}</pre>` : ""}` })),
  ].sort((a, b) => b.ts - a.ts).slice(0, 50);
  box.innerHTML = rows.map((x) => `<div><span class="muted">${new Date(x.ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}</span> ${x.html}</div>`).join("") || "Nothing yet.";
}

// --- Settings
function tabSettings(r, el) {
  const rep = r.report || {};
  el.innerHTML = `<div class="panel">
    <div class="formgrid"><label>Rig name<input id="newName" value="${esc(r.name)}" maxlength="40"></label></div>
    <div class="actions"><button class="btn primary" id="saveName">Rename</button></div>
    <p class="muted small">The miner's worker name on the pool follows the dashboard name.</p>
  </div>
  <div class="panel">
    <div class="section-title">Security</div>
    <div class="actions"><button class="btn" id="rotTok">New token</button><button class="btn danger" id="delRig">Delete rig</button></div>
    <p class="muted small">A new token logs the rig out until it gets the new one (USB rigs on your home network, or with a farm key, add themselves back). Deleting removes its history and settings from the dashboard.</p>
  </div>
  <div class="panel"><div class="section-title">Agent</div>
    <div class="kv"><div>Version</div><div>v${esc(rep.agent_version || "?")}</div><div>Mode</div><div>${rep.manage_miner === false ? "Monitor only (HiveOS runs the miner)" : "SmokeOS runs the miner"}</div>
    <div>Hostname</div><div>${esc(rep.hostname || "–")}</div></div></div>`;
  $("#saveName").onclick = async () => {
    try { await api(`/api/rigs/${r.id}`, { method: "PATCH", body: JSON.stringify({ name: $("#newName").value.trim() }) }); toast("Renamed"); await refresh(); render(true); }
    catch (e) { toast(e.message); }
  };
  $("#rotTok").onclick = async () => {
    if (!(await confirmBox(`Make a new token for ${r.name}? The old one stops working immediately.`, "New token", true))) return;
    try { const j = await api(`/api/rigs/${r.id}/rotate`, { method: "POST" }); showToken(r.name, j.token); } catch (e) { toast(e.message); }
  };
  $("#delRig").onclick = async () => {
    if (!(await confirmBox(`Delete ${r.name} and all its history?`, "Delete", true))) return;
    try { await api(`/api/rigs/${r.id}`, { method: "DELETE" }); await refresh(); go("farm"); } catch (e) { toast(e.message); }
  };
}

async function sendCmd(id, type, params) {
  try { await api(`/api/rigs/${id}/command`, { method: "POST", body: JSON.stringify({ type, params }) }); toast("Sent to the rig"); setTimeout(refresh, 2500); }
  catch (e) { toast(e.message); }
}
function showToken(name, token) {
  modal(`<h3>Token for ${esc(name)}</h3>
    <p class="muted">Copy this into the rig's <span class="mono">/etc/smokeos/agent.json</span>. It's shown only once.</p>
    <div class="tokenbox mono">${esc(token)}</div>
    <div class="row"><button class="btn" id="copyTok">Copy</button><button class="btn primary" id="doneTok">Done</button></div>`);
  $("#copyTok").onclick = () => copy(token);
  $("#doneTok").onclick = closeModal;
}

// ------------------------------------------------------------------ flight sheets page
const ALGOS = ["pearlhash", "progpowz", "progpow", "kawpow", "autolykos2", "fishhash", "etchash", "karlsenhash", "xelishashv2"];
function fsCommand(f) {
  if (f.miner === "custom") return f.custom || "";
  const tpl = { krig: `krig-miner --url ${f.pool} --user ${f.login}`, srbminer: `SRBMiner-MULTI --disable-cpu --algorithm ${f.algo} --pool ${f.pool} --wallet ${f.login}`,
    "t-rex": `t-rex -a ${f.algo} -o ${f.pool} -u ${f.login} -p x` }[f.miner] || "";
  return tpl + (f.extra ? " " + f.extra : "");
}
function fsCard(f, miners, actions = true) {
  return `<div class="fscard">
    <div class="fs-top"><b>${esc(f.name)}</b>${f.coin ? `<span class="tag">${esc(f.coin)}</span>` : ""}<span class="muted">${esc(miners[f.miner] ? miners[f.miner].label : f.miner)}</span></div>
    ${f.miner === "custom" ? "" : `<div class="kv small"><div>Pool</div><div class="mono">${esc(f.pool)}</div><div>Wallet</div><div class="mono">${esc(f.login)}</div>${f.algo ? `<div>Algorithm</div><div>${esc(f.algo)}</div>` : ""}</div>`}
    <pre class="cmdres mono">${esc(fsCommand(f))}</pre>
    ${actions ? `<div class="fs-bottom"><span class="muted small">${f.rigs.length ? "Running on " + f.rigs.map(esc).join(", ") : "Not used by any rig"}</span>
      <span class="actions"><button class="btn small primary" data-apply="${f.id}">Apply to rigs</button><button class="btn small" data-edit="${f.id}">Edit</button><button class="btn small danger" data-del="${f.id}">Delete</button></span></div>` : ""}
  </div>`;
}
async function renderFlightSheets() {
  const v = $("#view");
  v.innerHTML = `<div class="muted">Loading…</div>`;
  const d = await loadFS();
  v.innerHTML = `<div class="pagehead"><h2>Flight sheets</h2><button class="btn primary" id="fsNew">+ New flight sheet</button></div>
    <p class="muted">A flight sheet is what a rig mines: coin, pool, wallet and miner. Make one, then apply it to any rig. Editing a sheet updates every rig running it.</p>
    <div class="fsgrid">${d.flightsheets.map((f) => fsCard(f, d.miners)).join("") || `<div class="empty">No flight sheets yet. Make one for Pearl on Kryptex to start.</div>`}</div>`;
  $("#fsNew").onclick = () => fsEditor(null, d);
  $$("[data-edit]", v).forEach((b) => b.onclick = () => fsEditor(d.flightsheets.find((f) => f.id === Number(b.dataset.edit)), d));
  $$("[data-apply]", v).forEach((b) => b.onclick = () => fsApplyDialog(d.flightsheets.find((f) => f.id === Number(b.dataset.apply))));
  $$("[data-del]", v).forEach((b) => b.onclick = async () => {
    const f = d.flightsheets.find((x) => x.id === Number(b.dataset.del));
    if (!(await confirmBox(`Delete the flight sheet "${f.name}"? Rigs running it keep mining as they are.`, "Delete", true))) return;
    try { await api(`/api/flightsheets/${f.id}`, { method: "DELETE" }); renderFlightSheets(); } catch (e) { toast(e.message); }
  });
}
function fsEditor(f, d) {
  const x = f || { name: "", coin: "PRL", miner: "krig", pool: "prl-us.kryptex.network:8048", login: "krxX8K7VD2/%RIG_NAME%", algo: "", extra: "", custom: "" };
  modal(`<h3>${f ? "Edit" : "New"} flight sheet</h3>
    <div class="formgrid two">
      <label>Name<input id="fName" value="${esc(x.name)}" placeholder="Pearl Kryptex"></label>
      <label>Coin<input id="fCoin" value="${esc(x.coin)}" placeholder="PRL"></label>
      <label>Miner<select id="fMiner">${Object.entries(d.miners).map(([k, m]) => `<option value="${k}" ${k === x.miner ? "selected" : ""}>${esc(m.label)}</option>`).join("")}</select></label>
      <label class="f-algo">Algorithm<input id="fAlgo" list="algos" value="${esc(x.algo)}" placeholder="pearlhash"></label>
      <label class="f-std wide">Pool<input id="fPool" value="${esc(x.pool)}" placeholder="prl-us.kryptex.network:7048"></label>
      <label class="f-std wide">Wallet / login<input id="fLogin" value="${esc(x.login)}" placeholder="krxX8K7VD2/%RIG_NAME%"></label>
      <label class="f-std wide">Extra miner options<input id="fExtra" value="${esc(x.extra)}" placeholder="optional"></label>
      <label class="f-custom wide">Full miner command<input id="fCustom" value="${esc(x.custom)}" placeholder="/opt/smokeos/miners/... --pool ... --wallet ..."></label>
    </div>
    <datalist id="algos">${ALGOS.map((a) => `<option value="${a}">`).join("")}</datalist>
    <p class="muted small">%RIG_NAME% becomes each rig's dashboard name, so every rig shows up as its own worker on the pool.</p>
    <div class="section-title">Command the rig will run</div><pre class="cmdres mono" id="fPrev"></pre>
    <div class="row"><button class="btn" id="fCancel">Cancel</button><button class="btn primary" id="fSave">${f ? "Save" : "Create"}</button></div>`);
  const read = () => ({ name: $("#fName").value, coin: $("#fCoin").value, miner: $("#fMiner").value, algo: $("#fAlgo").value, pool: $("#fPool").value, login: $("#fLogin").value, extra: $("#fExtra").value, custom: $("#fCustom").value });
  const upd = () => {
    const y = read(), m = d.miners[y.miner];
    $$(".f-std").forEach((e) => { e.hidden = y.miner === "custom"; });
    $(".f-custom").hidden = y.miner !== "custom";
    $(".f-algo").hidden = !m.algo;
    $("#fPrev").textContent = fsCommand(y);
  };
  $$("#modalBox input, #modalBox select").forEach((i) => { i.addEventListener("input", upd); });
  $("#fMiner").addEventListener("change", () => {
    const m = $("#fMiner").value;
    if (m === "srbminer" && /8048$/.test($("#fPool").value)) { $("#fPool").value = $("#fPool").value.replace(/8048$/, "7048"); if (!$("#fAlgo").value) $("#fAlgo").value = "pearlhash"; }
    if (m === "krig" && /7048$/.test($("#fPool").value)) $("#fPool").value = $("#fPool").value.replace(/7048$/, "8048");
    upd();
  });
  upd();
  $("#fCancel").onclick = closeModal;
  $("#fSave").onclick = async () => {
    try {
      if (f) { const j = await api(`/api/flightsheets/${f.id}`, { method: "PUT", body: JSON.stringify(read()) }); toast(j.reapplied ? `Saved – updating ${j.reapplied} rig(s)` : "Saved"); }
      else { await api("/api/flightsheets", { method: "POST", body: JSON.stringify(read()) }); toast("Flight sheet created"); }
      closeModal(); renderFlightSheets();
    } catch (e) { toast(e.message); }
  };
}
function fsApplyDialog(f) {
  modal(`<h3>Apply "${esc(f.name)}"</h3>
    <div class="list checklist">${DATA.rigs.map((r) => `<label><input type="checkbox" value="${r.id}" ${r.report.manage_miner === false ? "disabled" : ""}>
      <span class="dot ${rigState(r)}"></span> ${esc(r.name)} <span class="muted">${r.report.manage_miner === false ? "(HiveOS, monitor only)" : (r.report.flightsheet || {}).name ? "now: " + esc(r.report.flightsheet.name) : ""}</span></label>`).join("")}</div>
    <div class="row"><button class="btn" id="aCancel">Cancel</button><button class="btn primary" id="aGo">Apply</button></div>`);
  $("#aCancel").onclick = closeModal;
  $("#aGo").onclick = async () => {
    const ids = $$("#modalBox input:checked").map((i) => Number(i.value));
    if (!ids.length) return toast("Pick at least one rig");
    try { const j = await api(`/api/flightsheets/${f.id}/apply`, { method: "POST", body: JSON.stringify({ rig_ids: ids }) }); toast(`Applied to ${j.applied.join(", ")}`); closeModal(); setTimeout(renderFlightSheets, 1500); }
    catch (e) { toast(e.message); }
  };
}

// ------------------------------------------------------------------ shell
const SH = { sid: null, es: null, term: null, fit: null, queue: "", timer: null, onresize: null };
function openShell(rid) {
  const r = rigById(rid);
  if (!r.online) return toast(`${r.name} is offline`);
  modal(`<h3>Shell on ${esc(r.name)}</h3><p class="muted">This is a root terminal on the rig. Enter your 2FA code to open it.</p>
    <input id="shCode" inputmode="numeric" autocomplete="one-time-code" maxlength="7" placeholder="123 456">
    <div class="row"><button class="btn" id="shCancel">Cancel</button><button class="btn primary" id="shGo">Open shell</button></div>`);
  $("#shCode").focus();
  $("#shCancel").onclick = closeModal;
  const go_ = async () => {
    try { const j = await api(`/api/rigs/${rid}/shell`, { method: "POST", body: JSON.stringify({ code: $("#shCode").value }) }); closeModal(); startTerm(r, j.sid); }
    catch (e) { toast(e.message); }
  };
  $("#shGo").onclick = go_;
  $("#shCode").onkeydown = (e) => { if (e.key === "Enter") go_(); };
}
function startTerm(r, sid) {
  closeTerm();
  SH.sid = sid;
  $("#termRig").textContent = r.name; $("#termState").textContent = "connecting…";
  $("#termwrap").hidden = false;
  const term = new Terminal({ cursorBlink: true, fontSize: 14, fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    theme: { background: "#0a0b0d", foreground: "#e6e8eb", cursor: "#ff7a1a", selectionBackground: "#ff7a1a55" }, scrollback: 5000 });
  const fit = new FitAddon.FitAddon(); term.loadAddon(fit);
  term.open($("#termEl")); fit.fit(); term.focus();
  term.writeln("\x1b[90mWaiting for the rig to connect…\x1b[0m");
  SH.term = term; SH.fit = fit;
  let got = false;
  const send = (body) => api(`/api/shell/${sid}/input`, { method: "POST", body: JSON.stringify(body) }).catch(() => {});
  send({ cols: term.cols, rows: term.rows });
  term.onData((d) => { SH.queue += d; if (!SH.timer) SH.timer = setTimeout(() => { const q = SH.queue; SH.queue = ""; SH.timer = null; send({ d: b64enc(q) }); }, 8); });
  term.onResize(({ cols, rows }) => send({ cols, rows }));
  SH.onresize = () => { try { fit.fit(); } catch (e) { /* hidden */ } };
  window.addEventListener("resize", SH.onresize);
  const es = new EventSource(`/api/shell/${sid}/stream`);
  SH.es = es;
  es.onmessage = (e) => {
    const j = JSON.parse(e.data);
    if (!j.d) return;
    if (!got) { got = true; term.reset(); $("#termState").textContent = "connected"; }
    term.write(b64dec(j.d));
  };
  es.addEventListener("closed", () => { term.writeln("\r\n\x1b[90m[shell closed]\x1b[0m"); $("#termState").textContent = "closed"; es.close(); });
  es.onerror = () => { if (es.readyState === 2) $("#termState").textContent = "disconnected"; };
  setTimeout(() => { if (!got && SH.sid === sid) term.writeln("\x1b[33mStill waiting… the rig may be busy or offline.\x1b[0m"); }, 15000);
}
function closeTerm() {
  if (SH.sid) api(`/api/shell/${SH.sid}/close`, { method: "POST" }).catch(() => {});
  if (SH.es) SH.es.close();
  if (SH.term) SH.term.dispose();
  if (SH.onresize) window.removeEventListener("resize", SH.onresize);
  Object.assign(SH, { sid: null, es: null, term: null, fit: null, queue: "", timer: null, onresize: null });
  $("#termEl").innerHTML = ""; $("#termwrap").hidden = true;
}
$("#termClose").onclick = closeTerm;

// ------------------------------------------------------------------ top bar: add rig, updates, log
async function addRig() {
  let fk = "", home = {};
  try { fk = (await api("/api/farm")).farm_key; home = await api("/api/home"); } catch (e) { return toast(e.message); }
  modal(`<h3>Add a rig</h3>
    <div class="homebox">
      <div class="homehead"><b>Home plug-and-play</b>
        <button class="btn small ${home.auto_add ? "primary" : ""}" id="homeToggle">${home.auto_add ? "ON" : "OFF"}</button></div>
      <p class="muted">Plug a SmokeOS USB into a rig on your home network and boot it. It finds this dashboard and adds itself. Nothing to type.</p>
      ${home.auto_add && !home.listening_lan ? `<div class="alert warn">Restart the server once so it listens on your home network.</div>` : ""}
      <div class="kv"><div>Dashboard link for away</div><div><input id="homeUrl" placeholder="https://box.tail1234.ts.net" value="${esc(home.public_url || "")}"></div>
        <div>Tailscale key</div><div><input id="homeKey" type="password" placeholder="${home.tailscale_key_set ? "saved ✓ (type to replace)" : "tskey-auth-… (reusable)"}"></div></div>
      <p class="muted small">Rigs added at home get these two, so they keep working after you move them to the apartment.</p>
      <div class="actions"><button class="btn small" id="homeSave">Save</button></div>
    </div>
    <div class="section-title">Or set up by hand</div>
    <p class="muted"><b>USB rig away from home:</b> put these in the USB's <span class="mono">rig.conf</span> with a Tailscale key, boot. The rig adds itself.</p>
    <div class="kv"><div>SERVER_URL</div><div class="mono">${esc(home.public_url || location.origin)}</div>
      <div>FARM_KEY</div><div class="mono" id="fk">••••••••••••••••</div></div>
    <div class="actions"><button class="btn small" id="showFk">Show</button><button class="btn small" id="copyFk">Copy farm key</button>
      <button class="btn small danger" id="rotFk">New farm key</button></div>
    <p class="muted"><b>Existing Ubuntu / HiveOS rig:</b> create it here and paste the token into its agent.json.</p>
    <input id="rigName" placeholder="Rig name, e.g. Big_Smoke" maxlength="40">
    <div class="row"><button class="btn" id="cancelRig">Close</button><button class="btn primary" id="saveRig">Create + get token</button></div>`);
  $("#homeToggle").onclick = async () => { try { await api("/api/home", { method: "POST", body: JSON.stringify({ auto_add: !home.auto_add }) }); addRig(); } catch (e) { toast(e.message); } };
  $("#homeSave").onclick = async () => {
    const b = { public_url: $("#homeUrl").value };
    if ($("#homeKey").value.trim()) b.tailscale_authkey = $("#homeKey").value.trim();
    try { await api("/api/home", { method: "POST", body: JSON.stringify(b) }); toast("Saved"); addRig(); } catch (e) { toast(e.message); }
  };
  $("#showFk").onclick = () => { $("#fk").textContent = fk; };
  $("#copyFk").onclick = () => copy(fk, "Farm key copied");
  $("#rotFk").onclick = async () => {
    if (!(await confirmBox("Make a new farm key? Rigs already added keep working; new USBs need the new key.", "New key", true))) return addRig();
    try { await api("/api/farm/rotate", { method: "POST" }); toast("New farm key made"); } catch (e) { toast(e.message); }
    addRig();
  };
  $("#cancelRig").onclick = closeModal;
  $("#saveRig").onclick = async () => {
    try { const j = await api("/api/rigs", { method: "POST", body: JSON.stringify({ name: $("#rigName").value }) }); showToken(j.name, j.token); refresh(); }
    catch (e) { toast(e.message); }
  };
}
$("#addRig").onclick = addRig;

async function showUpdates() {
  let u; try { u = await api("/api/updates"); } catch (e) { return toast(e.message); }
  const newer = u.latest && cmpVer(u.latest, u.server_version) > 0;
  const old = u.rigs.filter((r) => u.latest && r.version && cmpVer(u.latest, r.version) > 0);
  modal(`<h3>Updates</h3>
    <div class="kv">
      <div>Server</div><div>v${esc(u.server_version)}</div>
      <div>Latest release</div><div>${u.latest ? "v" + esc(u.latest) : "–"}</div>
      <div>GitHub repo</div><div class="mono">${esc(u.repo || "not set")}</div>
      <div>Signing key</div><div>${u.signed ? "✓ verified releases only" : "<span class='t-crit'>missing</span>"}</div>
      <div>Last check</div><div>${u.checking ? "checking…" : u.last_check ? new Date(u.last_check * 1000).toLocaleString() : "never"}</div>
    </div>
    ${u.error ? `<div class="alert warn">${esc(u.error)}</div>` : ""}
    ${u.notes ? `<div class="notes">${esc(u.notes)}</div>` : ""}
    <div class="section-title">Rigs</div>
    <div class="list">${u.rigs.map((r) => `<div>${esc(r.name)} — v${esc(r.version || "?")} ${u.latest && r.version && cmpVer(u.latest, r.version) > 0 ? '<span class="tag warn">update available</span>' : '<span class="tag">up to date</span>'} ${r.online ? "" : '<span class="muted">(offline)</span>'}</div>`).join("") || "No rigs"}</div>
    <div class="actions">
      <button class="btn small" id="updCheck">Check now</button>
      <button class="btn small ${old.length ? "primary" : ""}" id="updRigs" ${old.length ? "" : "disabled"}>Update ${old.length} rig(s)</button>
      <button class="btn small ${newer ? "primary" : ""}" id="updServer" ${newer ? "" : "disabled"}>Update server</button>
    </div>
    <p class="muted small">Rigs with AUTO_UPDATE on install new releases by themselves within a minute of the server downloading them.</p>
    <div class="row"><button class="btn" id="updClose">Close</button></div>`);
  $("#updClose").onclick = closeModal;
  $("#updCheck").onclick = async () => { await api("/api/updates/check", { method: "POST" }).catch((e) => toast(e.message)); toast("Checking GitHub…"); setTimeout(showUpdates, 4000); };
  $("#updRigs").onclick = async () => { try { const j = await api("/api/updates/rigs", { method: "POST" }); toast(`Update sent to ${j.queued} rig(s)`); closeModal(); } catch (e) { toast(e.message); } };
  $("#updServer").onclick = async () => {
    if (!(await confirmBox(`Update the server to v${u.latest}? The dashboard restarts (about 10 s).`, "Update"))) return showUpdates();
    try { await api("/api/updates/server", { method: "POST" }); toast("Server updating – reloading in 10 s"); setTimeout(() => location.reload(), 10000); } catch (e) { toast(e.message); }
  };
}
$("#updBtn").onclick = showUpdates;
$("#menuBtn").onclick = (e) => { e.stopPropagation(); $("#hdrActions").classList.toggle("open"); };
document.addEventListener("click", (e) => {
  if (e.target.closest("#menuBtn")) return;
  if (!e.target.closest(".hdractions") || e.target.closest("button")) $("#hdrActions").classList.remove("open");
}, true);
$("#logout").onclick = async () => { await api("/api/logout", { method: "POST" }).catch(() => {}); location.href = "/login"; };
$("#auditBtn").onclick = async () => {
  const j = await api("/api/audit");
  modal(`<h3>Security log</h3><div class="list" style="max-height:60vh;overflow:auto">${j.audit.map((a) =>
    `<div><span class="muted">${new Date(a.ts * 1000).toLocaleString()}</span> <b>${esc(a.username)}</b> ${esc(a.action)} ${esc(a.detail || "")} <span class="muted">${esc(a.ip)}</span></div>`).join("") || "Empty"}</div>
    <div class="row"><button class="btn" id="closeAudit">Close</button></div>`);
  $("#closeAudit").onclick = closeModal;
};

// ------------------------------------------------------------------ refresh loop
async function refresh() {
  try { DATA = await api("/api/rigs"); } catch { $("#updated").textContent = "offline"; return; }
  const outdated = DATA.rigs.filter((r) => r.outdated).length + (DATA.latest && cmpVer(DATA.latest, DATA.version) > 0 ? 1 : 0);
  $("#updBadge").hidden = !outdated; $("#updBadge").textContent = outdated;
  $("#menuBadge").hidden = !outdated; $("#menuBadge").textContent = outdated;
  const crit = DATA.rigs.reduce((n, r) => n + r.alerts.filter((a) => a.level === "crit").length, 0);
  document.title = crit ? `(${crit}) SmokeOS` : "SmokeOS";
  $("#updated").textContent = "live";
  render(false);
}
refresh().then(() => render(true));
setInterval(refresh, 5000);
setInterval(() => { if (ROUTE.view === "rig") { if (ROUTE.tab === "stats") loadHistory(ROUTE.id); if (ROUTE.tab === "activity") loadLog(ROUTE.id); } }, 30000);
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
