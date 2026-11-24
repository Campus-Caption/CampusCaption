// Lecturer console: local model choice, on/off, one or two microphones -> WebSocket (one per mic), live preview.
const $ = (id) => document.getElementById(id);
const store = {
  get() { try { return JSON.parse(localStorage.getItem("cc-console") || "{}"); } catch { return {}; } },
  set(v) { try { localStorage.setItem("cc-console", JSON.stringify({ ...store.get(), ...v })); } catch {} },
};

const S = {
  info: null, sessionId: null, feed: null,
  running: false, wantOn: false, state: "idle", patchTimer: null,
  // one entry per microphone: stream, audio graph, ingest socket
  mics: { 1: { n: 1, retry: 0, stats: null }, 2: { n: 2, retry: 0, stats: null } },
};
const MIC_DEFAULT_LABELS = ["Lecturer", "Audience"];
const renderer = new CaptionFeed.Renderer($("caps"));

function settings() {
  return {
    local_model: $("local-model").value,
    language: $("language").value,
    polish: $("polish").checked,
    speakers: [1, 2].map((n) => $(`label-${n}`).value.trim() || MIC_DEFAULT_LABELS[n - 1]),
  };
}

// ------------------------------------------------------------------ lecturer secret
// Kept in sessionStorage only (gone when the tab closes), sent as a header on every control request.
const secret = {
  get() { try { return sessionStorage.getItem("cc-secret") || ""; } catch { return ""; } },
  set(v) { try { v ? sessionStorage.setItem("cc-secret", v) : sessionStorage.removeItem("cc-secret"); } catch {} },
  ask(again) {
    const v = prompt(again ? "Wrong lecturer password. Try again:" : "Lecturer password (LECTURER_SECRET on the server):");
    if (v === null) return false;
    secret.set(v.trim());
    return true;
  },
};

// fetch() for control endpoints: adds the secret and, on 401, asks for it and retries.
async function api(url, opts = {}) {
  for (let attempt = 0; ; attempt++) {
    const headers = { ...(opts.headers || {}) };
    const s = secret.get();
    if (s) headers["X-Lecturer-Secret"] = s;
    const res = await fetch(url, { ...opts, headers });
    if (res.status !== 401 || attempt >= 3 || !secret.ask(attempt > 0 || !!s)) return res;
  }
}

function notice(text, kind = "info") {
  const g = $("gate");
  g.hidden = !text;
  g.className = "notice " + kind;
  g.textContent = text || "";
}

// ------------------------------------------------------------------ settings UI
function renderSettings(info) {
  const saved = store.get();
  const d = info.defaults;
  const sel = $("local-model");
  sel.textContent = "";
  const models = info.models;
  for (const m of models) {
    const o = document.createElement("option");
    o.value = m.id;
    o.disabled = !m.available;
    o.textContent = `${m.label}, ${m.size}` + (m.available ? "" : " (not installed)");
    sel.appendChild(o);
  }
  const wanted = saved.local_model || d.local_model;
  sel.value = models.some((m) => m.id === wanted && m.available) ? wanted : (models.find((m) => m.available)?.id || "");
  $("model-hint").textContent = sel.value ? info.privacy : "No local model installed: pip install -r requirements-local.txt";
  $("language").value = saved.language || d.language;
  const labels = saved.speakers || d.speakers || MIC_DEFAULT_LABELS;
  [1, 2].forEach((n) => ($(`label-${n}`).value = labels[n - 1] || MIC_DEFAULT_LABELS[n - 1]));
  setSecondMic(!!saved.mic2On, false);
  $("polish").checked = !!(saved.polish ?? d.polish) && info.polish.available;
  $("polish").disabled = !info.polish.available;
  $("polish").closest("label").hidden = !info.polish.available; // optional extra: only shown once ANTHROPIC_API_KEY is set
  $("polish-hint").hidden = !info.polish.available;
  $("polish-hint").textContent = info.polish.available ? info.polish.privacy : `Not available: ${info.polish.reason}`;
  const an = info.analysis || { available: false, reason: "" };
  $("analyse").checked = an.available && saved.analyse !== false;
  $("analyse").disabled = !an.available;
  $("analyse-privacy").textContent = an.available
    ? an.privacy + " Audio is never sent."
    : "AI analysis needs ANTHROPIC_API_KEY in .env. Without it you still get the numbers (duration, pace, questions).";
}

function onSettingsChange() {
  const s = settings();
  store.set(s);
  if (!S.sessionId) return;
  clearTimeout(S.patchTimer);
  S.patchTimer = setTimeout(async () => {
    const res = await api(`/api/captions/session/${S.sessionId}/settings`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(s),
    });
    if (!res.ok) notice(await errorMessage(res), "error");
    else notice("");
  }, 350);
}

async function errorMessage(res) {
  try { const d = await res.json(); return d.detail?.message || (typeof d.detail === "string" ? d.detail : "Error " + res.status); }
  catch { return "Error " + res.status; }
}

// ------------------------------------------------------------------ session + feed
const overlayUrl = (extra = "") => `${location.origin}/overlay?session=${S.sessionId}${extra}`;

// The caption screen can be opened before captions start: the first click creates the session.
// The window is opened synchronously (so pop-up blockers allow it) and pointed at the session once it exists.
async function openCaptionScreen(e) {
  if (S.sessionId) return; // the link already points at the session
  e.preventDefault();
  const win = window.open("about:blank", "_blank");
  if (await ensureSession()) {
    if (win) win.location.href = overlayUrl(); else location.assign(overlayUrl());
  } else if (win) {
    win.close();
  }
}
async function ensureSession() {
  if (S.sessionId) return true;
  const res = await api("/api/captions/session", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ settings: settings() }),
  });
  if (!res.ok) { notice(await errorMessage(res), "error"); return false; }
  S.sessionId = (await res.json()).session_id;
  $("overlay-link").href = overlayUrl();
  $("share").hidden = false;
  S.feed = CaptionFeed.connect(S.sessionId, { onEvent: onFeed });
  return true;
}

function onFeed(ev) {
  if (renderer.apply(ev)) return;
  if (ev.type === "snapshot") setState(ev.status);
  else if (ev.type === "status") setState(ev);
  else if (ev.type === "error" && !ev.soft) notice(ev.message, "error");
}

function setState(st) {
  if (!st) return;
  S.state = st.state;
  const names = { idle: "Ready", loading: "Loading model…", listening: "Live", error: "Error" };
  $("state-text").textContent = S.wantOn || st.state !== "idle" ? (names[st.state] || st.state) : "Off";
  $("state-detail").textContent = st.message || "";
  const p = $("progress");
  p.hidden = !(st.state === "loading" && typeof st.progress === "number" && st.progress > 0);
  p.value = st.progress || 0;
  const btn = $("power");
  btn.classList.toggle("on", S.running && st.state === "listening");
  btn.classList.toggle("busy", S.wantOn && st.state === "loading");
  btn.classList.toggle("off", !S.wantOn);
  $("power-label").textContent = !S.wantOn ? "Start captions" : st.state === "loading" ? "Preparing…" : "Stop captions";
  btn.setAttribute("aria-pressed", String(S.wantOn));
  runClock(S.wantOn && st.state === "listening");
  document.body.dataset.state = !S.wantOn ? "off" : st.state === "listening" ? "live" : st.state === "loading" ? "loading" : st.state === "error" ? "error" : "off";
}

// ------------------------------------------------------------------ microphones + ingest
// Each microphone gets its own audio graph and its own ingest socket (?mic=1 / ?mic=2), so the server runs one
// recognizer per mic and labels the captions with that mic's speaker name.
const secondMicOn = () => !$("mic-row-2").hidden;
const activeMics = () => (secondMicOn() ? [1, 2] : [1]);

async function listMics() {
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput");
  const saved = store.get();
  for (const n of [1, 2]) {
    const sel = $(`mic-${n}`);
    const keep = sel.value || saved[`mic${n}`] || (n === 1 ? saved.mic : "") || "";
    sel.textContent = "";
    sel.appendChild(Object.assign(document.createElement("option"), { value: "", textContent: "Default microphone" }));
    devices.forEach((d, i) => sel.appendChild(Object.assign(document.createElement("option"), { value: d.deviceId, textContent: d.label || `Microphone ${i + 1}` })));
    sel.value = devices.some((d) => d.deviceId === keep) ? keep : "";
  }
  checkSameDevice();
}

function checkSameDevice() {
  const same = secondMicOn() && $("mic-1").value === $("mic-2").value;
  $("mic-hint").textContent = same ? "Both rows use the same microphone. Pick a different device for microphone 2." : "";
}

function setSecondMic(on, live = true) {
  $("mic-row-2").hidden = !on;
  $("add-mic").hidden = on;
  store.set({ mic2On: on });
  checkSameDevice();
  if (!live || !S.wantOn) return;
  if (on) startMic(2).catch((e) => notice("Microphone 2: " + micError(e), "error"));
  else stopMic(2);
}

function micError(e) {
  return e.name === "NotAllowedError" ? "Microphone access was denied. Please allow it in the browser." : (e.message || String(e));
}

async function openMic(n) {
  const m = S.mics[n];
  const dev = $(`mic-${n}`).value;
  m.stream = await navigator.mediaDevices.getUserMedia({
    audio: { deviceId: dev ? { exact: dev } : undefined, channelCount: 1, echoCancellation: false, noiseSuppression: true, autoGainControl: true },
  });
  await listMics();
  m.ctx = new AudioContext({ latencyHint: "interactive" });
  await m.ctx.audioWorklet.addModule("/static/js/pcm-worklet.js");
  const src = m.ctx.createMediaStreamSource(m.stream);
  m.node = new AudioWorkletNode(m.ctx, "pcm-processor");
  const mute = m.ctx.createGain(); mute.gain.value = 0;
  src.connect(m.node).connect(mute).connect(m.ctx.destination);
  m.node.port.onmessage = (ev) => {
    $(`meter-${n}`).style.width = Math.round(Math.min(1, ev.data.level * 6) * 100) + "%";
    if (m.ws && m.ws.readyState === WebSocket.OPEN && m.ready) m.ws.send(ev.data.pcm);
  };
  m.stream.getAudioTracks()[0].addEventListener("ended", () => {
    if (!S.wantOn || S.mics[n].stream !== m.stream) return;
    notice(`Microphone ${n} was disconnected.`, "error");
    if (n === 1) stop(); else stopMic(2);
  });
}

function closeMic(n) {
  const m = S.mics[n];
  m.stream?.getTracks().forEach((t) => t.stop());
  m.node?.disconnect();
  m.ctx?.close().catch(() => {});
  m.stream = m.node = m.ctx = null;
  $(`meter-${n}`).style.width = "0";
}

async function openIngest(n) {
  const m = S.mics[n];
  const proto = location.protocol === "https:" ? "wss" : "ws";
  m.ready = false;
  const ws = new WebSocket(`${proto}://${location.host}/api/captions/ws/ingest/${S.sessionId}?mic=${n}`);
  m.ws = ws;
  ws.binaryType = "arraybuffer";
  ws.onopen = () => ws.send(JSON.stringify({ type: "auth", secret: secret.get() }));
  ws.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    if (ev.type === "ready") { m.ready = true; m.retry = 0; ws.send(JSON.stringify({ type: "start" })); }
    else if (ev.type === "start_failed") { notice(ev.message, "error"); stop(); }
    else if (ev.type === "pong") { m.stats = ev.stats; showStats(); }
  };
  ws.onclose = (e) => {
    m.ready = false;
    if (!S.wantOn || m.ws !== ws) return;
    if (e.code === 4401) {
      if (secret.ask(true)) { setTimeout(() => S.wantOn && openIngest(n), 0); return; }
      notice("Lecturer password required.", "error"); stop(); return;
    }
    if (e.code === 4409) { notice(`Microphone ${n} of this session is already running in another window.`, "error"); stop(); return; }
    if (e.code === 4404) { notice("The session no longer exists. Please start again.", "error"); S.sessionId = null; stop(); return; }
    m.retry += 1;
    if (m.retry > 8) { notice("Lost connection to the server.", "error"); stop(); return; }
    $("state-detail").textContent = "Reconnecting…";
    setTimeout(() => S.wantOn && m.ws === ws && openIngest(n), Math.min(500 * 2 ** m.retry, 8000));
  };
  clearInterval(m.pingTimer);
  m.pingTimer = setInterval(() => ws.readyState === 1 && ws.send(JSON.stringify({ type: "ping" })), 4000);
}

async function startMic(n) {
  await openMic(n);
  await openIngest(n);
}

function stopMic(n) {
  const m = S.mics[n];
  clearInterval(m.pingTimer);
  if (m.ws && m.ws.readyState === 1) m.ws.send(JSON.stringify({ type: "stop" }));
  const ws = m.ws; m.ws = null; m.ready = false; m.stats = null;
  setTimeout(() => ws && ws.close(), 400);
  closeMic(n);
  showStats();
}

function showStats() {
  const box = $("stats");
  box.textContent = "";
  let slow = false;
  for (const n of activeMics()) {
    const st = S.mics[n].stats;
    if (!st) continue;
    const p = [];
    if (st.avg_final_latency != null) p.push(`delay ${st.avg_final_latency} s`);
    if (st.utterances != null) p.push(`${st.utterances} ${st.utterances === 1 ? "sentence" : "sentences"}`);
    if (st.backlog) p.push(`${st.backlog} waiting`);
    if (p.length) box.append(Object.assign(document.createElement("span"), { textContent: (secondMicOn() ? `Mic ${n}: ` : "") + p.join(", ") }));
    slow ||= st.backlog >= 3;
  }
  if (slow) box.append(Object.assign(document.createElement("span"), { className: "warn", textContent: "This model is too slow for live captions on this computer. Choose Nemotron or Parakeet, or use a GPU." }));
}

// Time on air, shown next to the Live lamp.
function runClock(on) {
  const el = $("clock");
  if (!on) { clearInterval(S.clockTimer); S.clockTimer = null; S.liveSince = null; el.hidden = true; return; }
  if (S.clockTimer) return;
  S.liveSince = Date.now();
  const tick = () => {
    const s = Math.floor((Date.now() - S.liveSince) / 1000);
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = String(s % 60).padStart(2, "0");
    el.textContent = h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
    el.dateTime = `PT${s}S`;
  };
  tick(); el.hidden = false;
  S.clockTimer = setInterval(tick, 1000);
}

async function start() {
  if (S.wantOn) return;
  notice("");
  S.wantOn = true;
  setState({ state: "loading", message: "Opening microphone…" });
  try {
    if (!(await ensureSession())) { S.wantOn = false; setState({ state: "idle" }); return; }
    for (const n of activeMics()) await openMic(n); // ask for every mic before streaming any
    S.running = true;
    for (const n of activeMics()) await openIngest(n);
  } catch (e) {
    notice("Start failed: " + micError(e), "error");
    stop();
  }
}

function stop() {
  S.wantOn = false;
  S.running = false;
  [1, 2].forEach(stopMic);
  setState({ state: "idle", message: "" });
}

// ------------------------------------------------------------------ wiring
async function endSession() {
  if (!S.sessionId) return;
  const ai = !!S.info.analysis?.available && $("analyse").checked;
  const ask = ai
    ? "End this session? Captions stop, the caption screen disconnects, and Claude analyses the transcript."
    : "End this session? Captions stop and the caption screen disconnects.";
  if (!confirm(ask)) return;
  stop();
  const sid = S.sessionId;
  Report.loading(ai);
  let transcript = "", result = null;
  try {
    const t = await api(`/api/captions/session/${sid}/transcript`);
    if (t.ok) transcript = await t.text();
    const r = await api(`/api/captions/session/${sid}/analysis?ai=${ai}`, { method: "POST" });
    result = r.ok ? await r.json() : { error: await errorMessage(r) };
  } catch (e) {
    result = { error: "The analysis could not be loaded: " + (e.message || e) };
  } finally {
    // the session (and its transcript) is deleted on the server either way; the browser keeps the copy above
    await api(`/api/captions/session/${sid}`, { method: "DELETE" }).catch(() => {});
    S.feed?.close(); S.feed = null; S.sessionId = null;
    renderer.reset(); $("share").hidden = true; $("stats").textContent = "";
    $("overlay-link").href = "/overlay";
  }
  Report.show(result, transcript);
}

async function exportTranscript() {
  const res = await api(`/api/captions/session/${S.sessionId}/transcript`);
  if (!res.ok) { notice(await errorMessage(res), "error"); return; }
  const blob = await res.blob();
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "captions.txt" });
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

async function copy(text, btn) {
  try { await navigator.clipboard.writeText(text); const t = btn.textContent; btn.textContent = "Copied ✓"; setTimeout(() => (btn.textContent = t), 1500); }
  catch { prompt("Copy link:", text); }
}

async function init() {
  const res = await fetch("/api/captions/engines");
  if (!res.ok) { notice(await errorMessage(res), "error"); return; }
  S.info = await res.json();
  if (S.info.auth_required && !secret.get()) secret.ask(false);
  renderSettings(S.info);
  $("app").hidden = false;
  if (navigator.mediaDevices?.enumerateDevices) listMics().catch(() => {});
  if (!navigator.mediaDevices?.getUserMedia) notice("This browser or connection does not allow microphone access (HTTPS or localhost required).", "error");

  $("power").onclick = () => (S.wantOn ? stop() : start());
  for (const id of ["language", "polish", "local-model"]) $(id).addEventListener("change", onSettingsChange);
  for (const n of [1, 2]) {
    $(`mic-${n}`).addEventListener("change", async () => {
      store.set({ [`mic${n}`]: $(`mic-${n}`).value });
      checkSameDevice();
      if (S.wantOn && (n === 1 || secondMicOn())) {
        closeMic(n);
        openMic(n).catch((e) => notice(`Microphone ${n}: ` + micError(e), "error"));
      }
    });
    $(`label-${n}`).addEventListener("input", onSettingsChange);
  }
  $("add-mic").onclick = () => { setSecondMic(true); $("mic-2").focus(); };
  $("remove-mic").onclick = () => { setSecondMic(false); $("add-mic").focus(); };

  $("clear").onclick = async () => { if (S.sessionId) await api(`/api/captions/session/${S.sessionId}/clear`, { method: "POST" }); else renderer.reset(); };
  $("overlay-link").addEventListener("click", openCaptionScreen);
  $("copy-link").onclick = async (e) => { if (await ensureSession()) copy(overlayUrl(), e.target); };
  $("export").onclick = exportTranscript;
  $("end").onclick = endSession;
  $("analyse").addEventListener("change", () => store.set({ analyse: $("analyse").checked }));
  $("close-report").onclick = () => { $("report").hidden = true; };
  $("dl-analysis").onclick = () => Report.download("markdown");
  $("dl-transcript").onclick = () => Report.download("transcript");
  window.addEventListener("beforeunload", (e) => { if (S.wantOn) { e.preventDefault(); e.returnValue = ""; } });
}
init();
