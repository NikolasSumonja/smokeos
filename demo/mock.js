"use strict";
// ---------------------------------------------------------------------------
// SmokeOS demo backend: answers the dashboard's API calls with example data,
// including a pretend shell. Nothing here talks to real rigs.
// ---------------------------------------------------------------------------
const DEMO = (() => {
  const T = () => Date.now() / 1000;
  const r1 = (a, b) => a + Math.random() * (b - a);
  const mondayOnePm = (() => { const d = new Date(); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); d.setHours(13, 0, 0, 0); return d.getTime() / 1000; })();
  let nextCmd = 100, nextRig = 10, nextFs = 10;
  const state = {
    server: "1.1.0", latest: "1.1.1",
    notes: "SmokeOS 1.1.1\n• Rig pages with tabs, flight sheets and a web shell\n• Instant commands\n• Autofan",
    farmKey: "farm_K7vQ2m9XwTqLp4RzA8sYcN3e",
    serverUrl: "https://minecraft-box.tail1234.ts.net",
    home: { auto_add: true, public_url: "https://minecraft-box.tail1234.ts.net", tailscale_key_set: true, listening_lan: true },
    audit: [], rigs: [],
    fs: [
      { id: 1, name: "Pearl SRB", coin: "PRL", miner: "srbminer", pool: "prl-us.kryptex.network:7048", login: "krxX8K7VD2/%RIG_NAME%", algo: "pearlhash", extra: "", custom: "" },
      { id: 2, name: "Pearl KRig", coin: "PRL", miner: "krig", pool: "prl-us.kryptex.network:8048", login: "krxX8K7VD2/%RIG_NAME%", algo: "", extra: "", custom: "" },
      { id: 3, name: "Zano T-Rex", coin: "ZANO", miner: "t-rex", pool: "stratum+tcp://YOUR_ZANO_POOL:PORT", login: "YOUR_ZANO_WALLET.%RIG_NAME%", algo: "progpowz", extra: "", custom: "" },
    ],
  };
  const MINERS = { krig: { label: "KRig", algo: false }, srbminer: { label: "SRBMiner", algo: true }, "t-rex": { label: "T-Rex", algo: true }, custom: { label: "Custom command", algo: false } };
  const audit = (action, detail = "", user = "pound", ip = "100.101.12.4") => state.audit.unshift({ ts: T(), username: user, ip, action, detail });
  const gpu = (index, name, o) => Object.assign({ vendor: "nvidia", index, name, fan: 50, util: 100, mem: 7000, bus: `0${index + 5}:00.0` }, o);
  const sys = (o) => Object.assign({ kernel: "6.8.0-147-generic", load: [0.43, 0.49, 0.53], ram_total_mb: 3800, ram_free_mb: 1950, disk_free_gb: 107, disk_total_gb: 128,
    nvidia_driver: "580.178.04", os: "SmokeOS 1.1.0" }, o);

  state.rigs.push({
    id: 1, name: "Big_Smoke", online: true, version: "1.1.0", boot: mondayOnePm - 120, minerStart: mondayOnePm, fsId: 1,
    miner: "SRBMiner-MULTI", running: true, baseHr: 239.4e12,
    sys: sys({ cpu: "2 × Intel(R) Celeron(R) CPU G1820 @ 2.70GHz", board: "CRESCENTBAY INTEL Corporation", local_ip: "192.168.0.69", mac: "00:e0:0b:4b:42:17", cpu_temp: 46 }),
    gpus: [
      { vendor: "amd", index: 0, name: "Radeon RX 6600", bus: "03:00.0", temp: 44, fan: 29, power: 53, power_limit: null, core: 2350, mem: 1750, util: 99, hr: 7.34e12 },
      gpu(1, "GeForce RTX 3070", { bus: "05:00.0", temp: 64, fan: 67, power: 244, power_limit: 270, power_default: 270, core: 1890, hr: 84.53e12 }),
      gpu(2, "GeForce RTX 3070", { bus: "06:00.0", temp: 64, fan: 95, power: 238, power_limit: 240, power_default: 240, core: 1875, hr: 83.76e12 }),
      gpu(3, "GeForce RTX 2080", { bus: "07:00.0", temp: 71, fan: 0, power: 139, power_limit: 280, power_default: 280, core: 1650, mem: 7000, hr: 63.79e12 }),
    ],
    oc: {},
    af: { enabled: false, target: 65, min_fan: 40, max_fan: 100 }, events: [], commands: [],
  });
  state.rigs.push({
    id: 2, name: "Little_Smoke", online: true, version: "1.1.0", boot: mondayOnePm - 600, minerStart: mondayOnePm - 500, fsId: 3,
    miner: "t-rex", running: true, baseHr: 67.4e6,
    sys: sys({ cpu: "2 × Intel(R) Celeron(R) CPU 3865U @ 1.80GHz", board: "Onda B250 D8P-D4", local_ip: "192.168.0.71", mac: "00:e0:4c:68:12:aa", cpu_temp: 41, ram_total_mb: 7800, ram_free_mb: 6100 }),
    gpus: Array.from({ length: 7 }, (_, i) => gpu(i, "P106-100", {
      bus: `0${i + 1}:00.0`, temp: [55, 58, 61, 57, 60, 59, 63][i], fan: i === 6 ? 0 : [62, 65, 70, 64, 68, 66, 0][i],
      power: [96, 101, 104, 99, 102, 98, 100][i], power_limit: 120, power_default: 120, core: 1835, mem: 4504, hr: [9.63, 9.58, 9.71, 9.60, 9.66, 9.59, 9.63][i] * 1e6 })),
    oc: {}, af: { enabled: true, target: 62, min_fan: 45, max_fan: 100 }, events: [], commands: [],
  });
  state.rigs.push({
    id: 4, name: "rig-3f9a1c", online: true, version: "1.1.0", boot: T() - 300, minerStart: T() - 240, fsId: 2,
    miner: "krig-miner", running: true, baseHr: 47.2e12,
    sys: sys({ cpu: "4 × Intel(R) Pentium(R) Gold G5400", board: "ASUS PRIME H310M-E", local_ip: "192.168.0.84", mac: "04:d4:c4:3f:9a:1c", cpu_temp: 38 }),
    gpus: [gpu(0, "GeForce RTX 3060", { bus: "01:00.0", temp: 57, fan: 48, power: 118, power_limit: 170, power_default: 170, core: 1837, mem: 7500, hr: 47.2e12 })],
    oc: {}, af: { enabled: false, target: 65, min_fan: 40, max_fan: 100 }, events: [], commands: [],
  });
  state.rigs.push({
    id: 3, name: "ONDA", online: false, lastSeen: T() - 3 * 3600 - 840, version: "1.0.0", boot: mondayOnePm, fsId: 3,
    miner: "t-rex", running: false, baseHr: 0, sys: sys({ local_ip: "192.168.0.75" }), gpus: [], oc: {}, af: {}, events: [], commands: [],
  });
  const R = (name) => state.rigs.find((r) => r.name === name);
  const ev = (rig, level, message, ts = T()) => rig.events.unshift({ id: Math.random(), rig_id: rig.id, ts, level, message });
  ev(R("Big_Smoke"), "info", "Registered with the dashboard as Big_Smoke", mondayOnePm - 100);
  ev(R("Big_Smoke"), "info", "Agent 1.1.0 started", mondayOnePm - 90);
  ev(R("Little_Smoke"), "info", "Agent 1.1.0 started", mondayOnePm - 520);
  ev(R("Little_Smoke"), "warn", "Miner exited (code 1), restarting", T() - 5 * 3600);
  ev(R("ONDA"), "warn", "Miner exited (code -9), restarting", T() - 3 * 3600 - 900);
  ev(R("rig-3f9a1c"), "info", "Registered with the dashboard as rig-3f9a1c", T() - 290);
  ev(R("rig-3f9a1c"), "info", "Joined Tailscale", T() - 280);
  audit("login");
  audit("register_rig", "rig-3f9a1c", "home-network", "192.168.0.84");

  const fsOf = (rig) => state.fs.find((f) => f.id === rig.fsId);
  const gpuHr = (rig, g) => {
    if (!rig.running || !rig.online || !g.hr) return 0;
    let f = 1;
    if (g.vendor === "nvidia" && g.power_default) f *= Math.min(1.02, Math.pow((g.power_limit || g.power_default) / g.power_default, 0.35));
    if (g.vendor === "nvidia" && g.core) f *= Math.min(1.06, Math.pow(g.core / 1850, 0.6));
    return g.hr * f * r1(0.99, 1.01);
  };
  const hrNow = (rig) => (rig.running && rig.online ? (rig.gpus.length ? rig.gpus.reduce((s, g) => s + (g._hr || 0), 0) : rig.baseHr) : 0);
  function minerLog(rig) {
    const ts = new Date().toTimeString().slice(0, 8);
    if (!rig.running) return ["miner stopped from the dashboard"];
    if (rig.miner === "SRBMiner-MULTI") return Array.from({ length: 10 }, (_, i) => i % 2 === 0
      ? `[${ts}] ` + rig.gpus.map((g, n) => `GPU${n}: ${((g._hr || 0) / 1e12).toFixed(2)} TH/s`).join("  ")
      : `[${ts}] pearlhash  Total: ${(rig.baseHr / 1e12 * r1(0.99, 1.01)).toFixed(2)} TH/s  A:${5519 + i} R:15  prl-us.kryptex.network:7048`);
    if (rig.miner === "krig-miner") return Array.from({ length: 10 }, (_, i) => i % 2 === 0
      ? `[${ts}] krig  GPU0 47.2 TH/s  T:57C P:118W`
      : `[${ts}] krig  Total: ${(rig.baseHr / 1e12 * r1(0.99, 1.01)).toFixed(1)} TH/s  accepted ${120 + i}  rejected 0`);
    return Array.from({ length: 10 }, (_, i) => i % 2 === 0
      ? `${ts} INFO - GPU #0: P106-100 - 9.63 MH/s, [T:55C, P:96W, F:62%, E:100kH/W]`
      : `${ts} INFO - Total: ${(rig.baseHr / 1e6 * r1(0.99, 1.01)).toFixed(2)} MH/s  Shares/min: 2.1`);
  }
  function jitter(rig) {
    for (const g of rig.gpus) {
      g.temp = Math.max(38, Math.min(84, g.temp + r1(-0.5, 0.5)));
      g.power = g.power_limit ? Math.min(g.power_limit, Math.max(50, g.power + r1(-2, 2))) : Math.max(40, g.power + r1(-1.5, 1.5));
      if (rig.af && rig.af.enabled && g.vendor === "nvidia" && g.fan !== 0) {
        const want = rig.af.min_fan + (g.temp - (rig.af.target - 8)) * (rig.af.max_fan - rig.af.min_fan) / 12;
        g.fan = Math.round(Math.max(rig.af.min_fan, Math.min(rig.af.max_fan, want)));
      }
    }
  }
  const cmpV = (a, b) => { const x = a.split(".").map(Number), y = b.split(".").map(Number); for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] - y[i]; return 0; };
  function view(rig) {
    const now = T();
    if (rig.online) jitter(rig);
    for (const g of rig.gpus) g._hr = gpuHr(rig, g);
    const fs = fsOf(rig);
    const rep = rig.online || rig.lastSeen ? {
      agent_version: rig.version, hostname: rig.name.replace(/_/g, ""), boot_time: rig.boot, manage_miner: true, cooldown: false, hashrate: hrNow(rig),
      gpus: rig.online ? rig.gpus.map((g) => ({ ...g, hashrate: rig.running ? g._hr : 0, hr: undefined, _hr: undefined, temp: Math.round(g.temp), power: Math.round(g.power * 10) / 10 })) : [],
      miner: { running: rig.running, uptime: rig.running ? now - rig.minerStart : 0, restarts: 1, name: rig.miner, log: rig.online ? minerLog(rig) : [] },
      flightsheet: fs ? { name: fs.name, coin: fs.coin } : null, system: { ...rig.sys, load: rig.sys.load.map((x) => Math.max(0, x + r1(-0.05, 0.05))) },
    } : {};
    const alerts = [];
    if (!rig.online) alerts.push({ level: "crit", msg: rig.lastSeen ? `Offline for ${Math.floor((now - rig.lastSeen) / 3600)}h` : "Never connected" });
    else {
      for (const g of rep.gpus) {
        if (g.temp >= 80) alerts.push({ level: "crit", msg: `GPU${g.index} at ${g.temp}°C` });
        if (g.fan === 0 && g.temp > 50) alerts.push({ level: "warn", msg: `GPU${g.index} fan at 0%` });
      }
      if (!rig.running) alerts.push({ level: "warn", msg: "Miner is not running" });
    }
    return { id: rig.id, name: rig.name, online: rig.online, last_seen: rig.online ? now - r1(1, 9) : rig.lastSeen || null,
      report: rep, alerts, outdated: !!(rep.agent_version && cmpV(rep.agent_version, state.latest) < 0) };
  }
  function history(rig, hours) {
    const now = T(), start = Math.max(now - hours * 3600, rig.minerStart || now), pts = [];
    if (!rig.online && !rig.lastSeen) return pts;
    const end = rig.online ? now : rig.lastSeen, n = 240;
    for (let i = 0; i <= n; i++) {
      const ts = start + (end - start) * i / n;
      const dip = Math.abs(ts - (now - 5 * 3600)) < 400 && rig.name === "Little_Smoke";
      const power = rig.gpus.reduce((s, g) => s + g.power, 0) * r1(0.96, 1.02);
      pts.push({ ts, hashrate: dip ? 0 : rig.baseHr * r1(0.975, 1.015), power: dip ? power * 0.3 : power, max_temp: Math.max(...rig.gpus.map((g) => g.temp), 0) + r1(-2, 1.5) });
    }
    return pts;
  }
  const finish = (cmd, ok, result, after) => setTimeout(() => { cmd.status = ok ? "done" : "failed"; cmd.result = result; if (after) after(); }, 700);
  function queue(rig, type, params) {
    const cmd = { id: nextCmd++, rig_id: rig.id, type, params: JSON.stringify(params || {}), status: "sent", created: T(), result: "", by_user: "pound" };
    rig.commands.unshift(cmd); return cmd;
  }
  function runCommand(rig, type, params) {
    const cmd = queue(rig, type, params);
    if (!rig.online) { cmd.status = "pending"; return cmd; }
    if (type === "restart_miner") finish(cmd, true, "miner restarting", () => { rig.minerStart = T(); rig.running = true; ev(rig, "info", "Command restart_miner: ok"); });
    else if (type === "stop_miner") finish(cmd, true, "miner stopped", () => { rig.running = false; ev(rig, "info", "Command stop_miner: ok"); });
    else if (type === "start_miner") finish(cmd, true, "miner starting", () => { rig.running = true; rig.minerStart = T(); ev(rig, "info", "Command start_miner: ok"); });
    else if (type === "reboot") finish(cmd, true, "rebooting in 5s", () => {
      rig.online = false; rig.lastSeen = T(); ev(rig, "info", "Command reboot: ok");
      setTimeout(() => { rig.online = true; rig.boot = T(); rig.minerStart = T(); rig.running = true; ev(rig, "info", `Agent ${rig.version} started`); }, 15000);
    });
    else if (type === "update_agent") finish(cmd, true, `installed ${state.latest}, restarting`, () => { const o = rig.version; rig.version = state.latest; ev(rig, "info", `Updated ${o} -> ${state.latest}`); });
    else finish(cmd, true, `(demo) ${type}`);
    return cmd;
  }
  function fsCmd(f) {
    if (f.miner === "custom") return f.custom;
    const b = "/opt/smokeos/miners/";
    return ({ krig: `${b}krig/krig-miner --url ${f.pool} --user ${f.login}`, srbminer: `${b}srbminer/SRBMiner-MULTI --disable-cpu --algorithm ${f.algo} --pool ${f.pool} --wallet ${f.login} --api-enable --api-port 21550`,
      "t-rex": `${b}t-rex/t-rex -a ${f.algo} -o ${f.pool} -u ${f.login} -p x --api-bind-http 127.0.0.1:4067` }[f.miner]) + (f.extra ? " " + f.extra : "");
  }
  function applyFs(rig, f) {
    rig.fsId = f.id;
    const cmd = queue(rig, "apply_flightsheet", { name: f.name });
    finish(cmd, true, `flight sheet '${f.name}' applied, miner restarting:\n${fsCmd(f)}`, () => {
      rig.miner = { krig: "krig-miner", srbminer: "SRBMiner-MULTI", "t-rex": "t-rex", custom: "custom" }[f.miner];
      rig.minerStart = T(); rig.running = true; ev(rig, "info", "Command apply_flightsheet: ok");
    });
  }
  function cleanFs(b) {
    const f = {}; for (const k of ["name", "coin", "miner", "pool", "login", "algo", "extra", "custom"]) f[k] = String(b[k] || "").trim();
    if (!f.name) throw new Error("Give the flight sheet a name (max 40 characters)");
    if (!MINERS[f.miner]) throw new Error("Pick a miner");
    if (f.miner === "custom") { if (!f.custom) throw new Error("Enter the full miner command"); }
    else { if (!f.pool || !f.login) throw new Error("Pool and wallet/login are required"); if (MINERS[f.miner].algo && !f.algo) throw new Error(`${MINERS[f.miner].label} needs an algorithm (e.g. pearlhash, progpowz)`); }
    return f;
  }
  function applyOC(rig, profile) {
    const prev = rig.oc || {}; rig.oc = profile;
    const cmd = queue(rig, "apply_oc", { profile }), lines = [];
    for (const g of rig.gpus) {
      const o = profile[String(g.index)] || {}, was = prev[String(g.index)] || {};
      if (g.vendor !== "nvidia") { lines.push(`GPU${g.index} ${g.name}: AMD tuning not supported yet (monitor only)`); continue; }
      const res = [], want = (k) => (o[k] ? "set" : was[k] ? "reset" : null);
      if (want("power_limit")) { const pl = o.power_limit || g.power_default; g.power_limit = pl; res.push(`PL ${pl}W${o.power_limit ? "" : " (stock)"} ok`); }
      if (want("core_lock")) { if (o.core_lock) { g.core = o.core_lock; res.push(`core lock ${o.core_lock} ok`); } else { g.core = 1850; res.push("core unlock (stock) ok"); } }
      if (want("core_offset")) res.push(o.core_offset ? `core ${o.core_offset > 0 ? "+" : ""}${o.core_offset} ok` : "core offset stock ok");
      if (want("mem_offset")) { g.mem = 7000 + (o.mem_offset || 0) / 2; res.push(o.mem_offset ? `mem ${o.mem_offset > 0 ? "+" : ""}${o.mem_offset} ok` : "mem offset stock ok"); }
      if (want("fan")) { g.fan = o.fan || 50; res.push(o.fan ? `fan ${o.fan}% ok` : "fan auto ok"); }
      lines.push(`GPU${g.index} ${g.name}: ` + (res.join(", ") || "stock (nothing changed)"));
    }
    finish(cmd, true, lines.join("\n"), () => ev(rig, "info", "Command apply_oc: ok"));
  }

  // ---------------- pretend shell
  const shells = {};
  function shellOut(sh, text) { sh.out.push(btoa(unescape(encodeURIComponent(text.replace(/\n/g, "\r\n"))))); }
  function fakeRun(sh, line) {
    const rig = sh.rig, c = line.trim(), [cmd] = c.split(/\s+/);
    const gpus = rig.gpus.filter((g) => g.vendor === "nvidia");
    const out = {
      "": "",
      help: "Demo shell. Try: nvidia-smi, uptime, uname -a, df -h, free -h, ls /opt/smokeos/miners, cat /etc/smokeos/agent.json, systemctl status smokeos-agent, exit",
      uptime: ` ${new Date().toTimeString().slice(0, 8)} up 2 days, 11:${String(new Date().getMinutes()).padStart(2, "0")},  1 user,  load average: ${rig.sys.load.join(", ")}`,
      uname: `Linux ${rig.name.replace(/_/g, "")} ${rig.sys.kernel} #147-Ubuntu SMP x86_64 GNU/Linux`,
      df: `Filesystem      Size  Used Avail Use% Mounted on\n/dev/sdb4       ${rig.sys.disk_total_gb}G   ${rig.sys.disk_total_gb - rig.sys.disk_free_gb}G  ${rig.sys.disk_free_gb}G  ${Math.round(100 * (1 - rig.sys.disk_free_gb / rig.sys.disk_total_gb))}% /\ntmpfs           256M     0  256M   0% /tmp`,
      free: `               total        used        free\nMem:           ${(rig.sys.ram_total_mb / 1024).toFixed(1)}Gi       ${((rig.sys.ram_total_mb - rig.sys.ram_free_mb) / 1024).toFixed(1)}Gi       ${(rig.sys.ram_free_mb / 1024).toFixed(1)}Gi\nSwap:             0B          0B          0B`,
      ls: c.includes("miners") ? "krig  srbminer  t-rex" : "agent.json  oc.json  flightsheet.json",
      whoami: "root", hostname: rig.name.replace(/_/g, ""),
      cat: c.includes("agent.json") ? JSON.stringify({ rig_name: rig.name, server_urls: [state.serverUrl], lan_url: "http://192.168.0.50:8420", manage_miner: true, auto_update: true, token: "smk_••••••" }, null, 2)
        : c.includes("release") ? "1.1.0" : `cat: ${c.split(/\s+/)[1] || ""}: No such file or directory`,
      systemctl: `● smokeos-agent.service - SmokeOS rig agent\n     Loaded: loaded (/etc/systemd/system/smokeos-agent.service; enabled)\n     Active: active (running) since Mon 13:00:02; 2 days ago\n   Main PID: 812 (python3)`,
      "nvidia-smi": gpus.length ? `+-----------------------------------------------------------------------------+\n| NVIDIA-SMI 580.178.04     Driver Version: 580.178.04     CUDA Version: 13.0 |\n|-------------------------------+----------------------+----------------------+\n` +
        gpus.map((g) => `| ${String(g.index).padEnd(2)} ${g.name.padEnd(26)} | ${g.bus.padEnd(20)} |\n| ${String(g.fan).padStart(3)}%  ${String(Math.round(g.temp)).padStart(3)}C  P2  ${String(Math.round(g.power)).padStart(4)}W / ${String(g.power_limit || g.power_default).padStart(3)}W | ${String(g.core).padStart(5)} MHz core       |`).join("\n") +
        `\n+-----------------------------------------------------------------------------+` : "No NVIDIA GPU found",
    };
    if (cmd === "exit") { shellOut(sh, "logout\n"); sh.closed = true; return; }
    if (cmd === "clear") shellOut(sh, "\x1b[2J\x1b[H");
    else if (cmd in out) shellOut(sh, out[cmd] + (out[cmd] ? "\n" : ""));
    else shellOut(sh, `${cmd}: not available in the demo shell (type help)\n`);
    shellOut(sh, sh.prompt);
  }
  function shellInput(sh, chars) {
    for (const ch of chars) {
      if (ch === "\r" || ch === "\n") { shellOut(sh, "\n"); fakeRun(sh, sh.line); sh.line = ""; }
      else if (ch === "\x7f" || ch === "\b") { if (sh.line) { sh.line = sh.line.slice(0, -1); shellOut(sh, "\b \b"); } }
      else if (ch === "\x03") { shellOut(sh, "^C\n" + sh.prompt); sh.line = ""; }
      else if (ch >= " ") { sh.line += ch; shellOut(sh, ch); }
    }
  }
  class FakeEventSource {
    constructor(url) {
      this.readyState = 1; this.listeners = {};
      this.sh = shells[url.split("/")[3]];
      this.t = setInterval(() => {
        const sh = this.sh; if (!sh) return;
        while (sh.out.length) { const d = sh.out.shift(); if (this.onmessage) this.onmessage({ data: JSON.stringify({ d }) }); }
        if (sh.closed) { (this.listeners.closed || []).forEach((f) => f({})); this.close(); }
      }, 40);
    }
    addEventListener(n, f) { (this.listeners[n] = this.listeners[n] || []).push(f); }
    close() { clearInterval(this.t); this.readyState = 2; }
  }
  window.EventSource = FakeEventSource;

  // ---------------- API router
  async function api(path, opts = {}) {
    await new Promise((r) => setTimeout(r, 90));
    const m = (opts.method || "GET").toUpperCase(), body = opts.body ? JSON.parse(opts.body) : {};
    const url = new URL(path, "https://demo.local"), p = url.pathname;
    const rigOf = (id) => { const r = state.rigs.find((x) => x.id === Number(id)); if (!r) throw new Error("Rig not found"); return r; };
    let k;
    if (p === "/api/rigs" && m === "GET") return { rigs: state.rigs.map(view), user: "pound", now: T(), version: state.server, latest: state.latest };
    if (p === "/api/rigs" && m === "POST") {
      const name = String(body.name || "").trim();
      if (!/^[A-Za-z0-9_ -]{1,40}$/.test(name)) throw new Error("Name: letters, numbers, - and _ only (max 40)");
      if (state.rigs.some((r) => r.name === name)) throw new Error("A rig with that name already exists");
      state.rigs.push({ id: nextRig++, name, online: false, version: "", gpus: [], oc: {}, af: {}, sys: sys({}), events: [], commands: [], baseHr: 0 });
      audit("add_rig", name);
      return { id: nextRig - 1, name, token: "smk_demo_" + Math.random().toString(36).slice(2, 14) + Math.random().toString(36).slice(2, 14) };
    }
    if ((k = p.match(/^\/api\/rigs\/(\d+)\/history$/))) return { points: history(rigOf(k[1]), Number(url.searchParams.get("hours") || 24)) };
    if ((k = p.match(/^\/api\/rigs\/(\d+)\/log$/))) { const r = rigOf(k[1]); return { commands: r.commands.slice(0, 25), events: r.events.slice(0, 50) }; }
    if ((k = p.match(/^\/api\/rigs\/(\d+)\/oc$/))) {
      const r = rigOf(k[1]);
      if (m === "GET") return { profile: r.oc };
      const prof = {};
      for (const [i, v] of Object.entries(body.profile || {})) {
        const g = {};
        for (const [key, lo, hi] of [["core_lock", 300, 3200], ["core_offset", -1000, 1000], ["mem_offset", -4000, 4000], ["power_limit", 50, 600], ["fan", 20, 100]]) {
          const val = Number(v[key]) || 0;
          if (val && (val < lo || val > hi)) throw new Error(`GPU${i} ${key} must be 0 (stock) or ${lo}–${hi}`);
          if (val) g[key] = val;
        }
        prof[i] = g;
      }
      applyOC(r, prof); audit("overclock", `${r.name}: ${JSON.stringify(prof)}`);
      return { ok: true, profile: prof };
    }
    if ((k = p.match(/^\/api\/rigs\/(\d+)\/autofan$/))) {
      const r = rigOf(k[1]);
      if (m === "GET") return { autofan: { enabled: false, target: 65, min_fan: 40, max_fan: 100, ...r.af } };
      if (!(body.target >= 40 && body.target <= 85 && body.min_fan >= 0 && body.min_fan <= body.max_fan && body.max_fan <= 100)) throw new Error("Target 40-85°C, and min fan ≤ max fan (0-100%)");
      r.af = { enabled: !!body.enabled, target: body.target, min_fan: body.min_fan, max_fan: body.max_fan };
      const cmd = queue(r, "set_autofan", r.af);
      finish(cmd, true, r.af.enabled ? `autofan on: target ${r.af.target}°C, fan ${r.af.min_fan}-${r.af.max_fan}%` : "autofan off (cards back to their own fan curve)");
      audit("autofan", r.name); return { autofan: r.af };
    }
    if ((k = p.match(/^\/api\/rigs\/(\d+)\/flightsheet$/))) return { flightsheet_id: rigOf(k[1]).fsId || null };
    if ((k = p.match(/^\/api\/rigs\/(\d+)\/shell$/))) {
      const r = rigOf(k[1]);
      if (!/^\d{6}$/.test(String(body.code || "").replace(/\s/g, ""))) throw new Error("Demo: type any 6 digits as the 2FA code");
      const sid = "demo" + Math.random().toString(36).slice(2, 10);
      const prompt = `\x1b[1;32mroot@${r.name.replace(/_/g, "")}\x1b[0m:\x1b[1;34m~\x1b[0m# `;
      shells[sid] = { rig: r, out: [], line: "", closed: false, prompt };
      setTimeout(() => shellOut(shells[sid], `SmokeOS 1.1.0 (Ubuntu 24.04) on ${r.name}\nThis is the demo shell: try nvidia-smi, uptime, df -h or help.\n\n` + prompt), 600);
      audit("shell_open", r.name); ev(r, "info", "Shell opened from the dashboard");
      return { sid };
    }
    if ((k = p.match(/^\/api\/shell\/([^/]+)\/input$/))) {
      const sh = shells[k[1]]; if (!sh) throw new Error("shell not found");
      if (body.d) shellInput(sh, decodeURIComponent(escape(atob(body.d))));
      return { ok: true, agent: true };
    }
    if ((k = p.match(/^\/api\/shell\/([^/]+)\/close$/))) { const sh = shells[k[1]]; if (sh && !sh.closed) { sh.closed = true; ev(sh.rig, "info", "Shell closed"); } return { ok: true }; }
    if ((k = p.match(/^\/api\/rigs\/(\d+)\/command$/))) { const r = rigOf(k[1]); const c = runCommand(r, body.type, body.params); audit("command", `${r.name}: ${body.type}`); return { id: c.id }; }
    if ((k = p.match(/^\/api\/rigs\/(\d+)\/rotate$/))) { const r = rigOf(k[1]); audit("rotate_token", r.name); return { token: "smk_demo_" + Math.random().toString(36).slice(2, 20) }; }
    if ((k = p.match(/^\/api\/rigs\/(\d+)$/)) && m === "DELETE") { const r = rigOf(k[1]); state.rigs = state.rigs.filter((x) => x !== r); audit("delete_rig", r.name); return { ok: true }; }
    if ((k = p.match(/^\/api\/rigs\/(\d+)$/)) && m === "PATCH") {
      const r = rigOf(k[1]), name = String(body.name || "").trim();
      if (!/^[A-Za-z0-9_-]{1,40}$/.test(name)) throw new Error("Name: letters, numbers, - and _ only (max 40)");
      if (state.rigs.some((x) => x !== r && x.name === name)) throw new Error("Another rig already has that name");
      audit("rename_rig", `${r.name} -> ${name}`); r.name = name; return { ok: true };
    }
    if (p === "/api/flightsheets" && m === "GET") return {
      flightsheets: state.fs.map((f) => ({ ...f, rigs: state.rigs.filter((r) => r.fsId === f.id).map((r) => r.name) })),
      miners: MINERS,
    };
    if (p === "/api/flightsheets" && m === "POST") {
      const f = cleanFs(body); if (state.fs.some((x) => x.name === f.name)) throw new Error("A flight sheet with that name already exists");
      f.id = nextFs++; state.fs.push(f); audit("flightsheet_create", f.name); return { id: f.id };
    }
    if ((k = p.match(/^\/api\/flightsheets\/(\d+)\/apply$/))) {
      const f = state.fs.find((x) => x.id === Number(k[1])), names = [];
      for (const id of body.rig_ids || []) { const r = state.rigs.find((x) => x.id === id); if (r) { applyFs(r, f); names.push(r.name); } }
      audit("flightsheet_apply", `${f.name} -> ${names.join(", ")}`); return { applied: names };
    }
    if ((k = p.match(/^\/api\/flightsheets\/(\d+)$/))) {
      const i = state.fs.findIndex((x) => x.id === Number(k[1])); if (i < 0) throw new Error("Not found");
      if (m === "DELETE") { audit("flightsheet_delete", state.fs[i].name); state.rigs.forEach((r) => { if (r.fsId === state.fs[i].id) r.fsId = null; }); state.fs.splice(i, 1); return { ok: true }; }
      const f = { ...cleanFs(body), id: state.fs[i].id }; state.fs[i] = f;
      const users = state.rigs.filter((r) => r.fsId === f.id); users.forEach((r) => applyFs(r, f));
      audit("flightsheet_update", f.name); return { ok: true, reapplied: users.length };
    }
    if (p === "/api/updates") return {
      server_version: state.server, latest: state.latest, error: null, last_check: T() - 600, checking: false, notes: state.notes,
      repo: "pound/smokeos", signed: true, rigs: state.rigs.map((r) => ({ id: r.id, name: r.name, version: r.version || null, online: r.online })),
    };
    if (p === "/api/updates/check") return { ok: true };
    if (p === "/api/updates/rigs") {
      let n = 0;
      for (const r of state.rigs) if (r.version && cmpV(r.version, state.latest) < 0) { runCommand(r, "update_agent"); n++; }
      audit("update_rigs", `${n} rigs -> ${state.latest}`); return { queued: n };
    }
    if (p === "/api/updates/server") { setTimeout(() => { state.server = state.latest; }, 3000); audit("update_server", `${state.server} -> ${state.latest}`); return { ok: true, version: state.latest }; }
    if (p === "/api/home" && m === "GET") return { ...state.home };
    if (p === "/api/home") {
      if ("auto_add" in body) state.home.auto_add = !!body.auto_add;
      if ("public_url" in body) state.home.public_url = String(body.public_url).trim();
      if (body.tailscale_authkey) state.home.tailscale_key_set = true;
      audit("home_settings"); return { ...state.home };
    }
    if (p === "/api/farm") return { farm_key: state.farmKey };
    if (p === "/api/farm/rotate") { state.farmKey = "farm_" + Math.random().toString(36).slice(2, 14) + Math.random().toString(36).slice(2, 14); audit("rotate_farm_key"); return { farm_key: state.farmKey }; }
    if (p === "/api/audit") return { audit: state.audit.slice(0, 100) };
    if (p === "/api/logout") throw new Error("Logging out is turned off in the demo");
    throw new Error("Not available in the demo");
  }
  return { api, serverUrl: state.serverUrl };
})();
const api = DEMO.api;
