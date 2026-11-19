// Lecturer console: engine switch, on/off, microphone -> WebSocket, live preview.
const $ = (id) => document.getElementById(id);
const store = {
  get() { try { return JSON.parse(localStorage.getItem("cc-console") || "{}"); } catch { return {}; } },
  set(v) { try { localStorage.setItem("cc-console", JSON.stringify({ ...store.get(), ...v })); } catch {} },
};

const S = {
  info: null, sessionId: null, feed: null, ws: null, ctx: null, stream: null, node: null,
  running: false, wantOn: false, retry: 0, pingTimer: null, state: "idle", patchTimer: null,
};
const renderer = new CaptionFeed.Renderer($("caps"));

function settings() {
  const engine = document.querySelector('input[name="engine"]:checked')?.value || "local";
  return {
    engine,
    local_model: $("local-model").value,
    language: $("language").value,
    polish: $("polish").checked,
    glossary: $("glossary").value.split("\n").map((x) => x.trim()).filter(Boolean),
  };
}

function notice(text, kind = "info") {
  const g = $("gate");
  g.hidden = !text;
  g.className = "notice " + kind;
  g.textContent = text || "";
}

// ------------------------------------------------------------------ engines UI
function renderEngines(info) {
  const box = $("engines");
  box.textContent = "";
  const saved = store.get();
  const d = info.defaults;
  const wanted = saved.engine || d.engine;
  for (const e of info.engines) {
    if (e.id === "mistral" && !e.available) continue; // optional extra: only shown once MISTRAL_API_KEY is set
    const label = document.createElement("label");
    label.className = "eng" + (e.available ? "" : " disabled");
    const input = Object.assign(document.createElement("input"), { type: "radio", name: "engine", value: e.id, disabled: !e.available });
    input.checked = e.id === wanted && e.available;
    const body = document.createElement("span");
    const b = document.createElement("b"); b.textContent = e.label;
    const s = document.createElement("small");
    s.textContent = e.available ? e.privacy : `Not available: ${e.reason}`;
    body.append(b, s);
    label.append(input, body);
    box.appendChild(label);
  }
  if (!box.querySelector("input:checked")) {
    const first = box.querySelector("input:not(:disabled)");
    if (first) first.checked = true;
  }
  const sel = $("local-model");
  sel.textContent = "";
  const local = info.engines.find((e) => e.id === "local");
  for (const m of local.models) {
    const o = document.createElement("option");
    o.value = m.id;
    o.disabled = !m.available;
    o.textContent = `${m.label} · ${m.size}` + (m.available ? "" : " (not installed)");
    sel.appendChild(o);
  }
  sel.value = local.models.some((m) => m.id === (saved.local_model || d.local_model) && m.available) ? (saved.local_model || d.local_model) : (local.models.find((m) => m.available)?.id || "");
  $("language").value = saved.language || d.language;
  $("polish").checked = !!(saved.polish ?? d.polish) && info.polish.available;
  $("polish").disabled = !info.polish.available;
  $("polish").closest("label").hidden = !info.polish.available; // optional extra: only shown once ANTHROPIC_API_KEY is set
  $("polish-hint").hidden = !info.polish.available;
  $("polish-hint").textContent = info.polish.available ? info.polish.privacy : `Not available: ${info.polish.reason}`;
  $("glossary").value = (saved.glossary || d.glossary || []).join("\n");
  updateEngineUi();
}

function updateEngineUi() {
  const s = settings();
  $("local-model-row").hidden = s.engine !== "local";
  const e = S.info.engines.find((x) => x.id === s.engine);
  $("engine-hint").textContent = "";
}

function onSettingsChange() {
  updateEngineUi();
  const s = settings();
  store.set(s);
  if (!S.sessionId) return;
  clearTimeout(S.patchTimer);
  S.patchTimer = setTimeout(async () => {
    const res = await fetch(`/api/captions/session/${S.sessionId}/settings`, {
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
async function ensureSession() {
  if (S.sessionId) return true;
  const res = await fetch("/api/captions/session", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ settings: settings() }),
  });
  if (!res.ok) { notice(await errorMessage(res), "error"); return false; }
  S.sessionId = (await res.json()).session_id;
  const url = `${location.origin}/overlay?session=${S.sessionId}`;
  $("overlay-link").href = url;
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
  document.body.dataset.state = !S.wantOn ? "off" : st.state === "listening" ? "live" : st.state === "loading" ? "loading" : st.state === "error" ? "error" : "off";
}

// ------------------------------------------------------------------ microphone + ingest
async function listMics() {
  const sel = $("mic");
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput");
  const keep = sel.value || store.get().mic || "";
  sel.textContent = "";
  sel.appendChild(Object.assign(document.createElement("option"), { value: "", textContent: "Default microphone" }));
  devices.forEach((d, i) => sel.appendChild(Object.assign(document.createElement("option"), { value: d.deviceId, textContent: d.label || `Microphone ${i + 1}` })));
  sel.value = devices.some((d) => d.deviceId === keep) ? keep : "";
}

async function openMic() {
  S.stream = await navigator.mediaDevices.getUserMedia({
    audio: { deviceId: $("mic").value ? { exact: $("mic").value } : undefined, channelCount: 1, echoCancellation: false, noiseSuppression: true, autoGainControl: true },
  });
  await listMics();
  S.ctx = new AudioContext({ latencyHint: "interactive" });
  await S.ctx.audioWorklet.addModule("/static/js/pcm-worklet.js");
  const src = S.ctx.createMediaStreamSource(S.stream);
  S.node = new AudioWorkletNode(S.ctx, "pcm-processor");
  const mute = S.ctx.createGain(); mute.gain.value = 0;
  src.connect(S.node).connect(mute).connect(S.ctx.destination);
  S.node.port.onmessage = (m) => {
    const lvl = Math.min(1, m.data.level * 6);
    $("meter").style.width = Math.round(lvl * 100) + "%";
    if (S.ws && S.ws.readyState === WebSocket.OPEN && S.ingestReady) S.ws.send(m.data.pcm);
  };
  S.stream.getAudioTracks()[0].addEventListener("ended", () => { if (S.wantOn) { notice("The microphone was disconnected.", "error"); stop(); } });
}

function closeMic() {
  S.stream?.getTracks().forEach((t) => t.stop());
  S.node?.disconnect();
  S.ctx?.close().catch(() => {});
  S.stream = S.node = S.ctx = null;
  $("meter").style.width = "0";
}

async function openIngest() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  S.ingestReady = false;
  const ws = new WebSocket(`${proto}://${location.host}/api/captions/ws/ingest/${S.sessionId}`);
  S.ws = ws;
  ws.binaryType = "arraybuffer";
  ws.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.type === "ready") { S.ingestReady = true; S.retry = 0; ws.send(JSON.stringify({ type: "start" })); }
    else if (ev.type === "start_failed") { notice(ev.message, "error"); stop(); }
    else if (ev.type === "pong") showStats(ev.stats);
  };
  ws.onclose = (e) => {
    S.ingestReady = false;
    if (!S.wantOn || S.ws !== ws) return;
    if (e.code === 4409) { notice("This session is already running in another window.", "error"); stop(); return; }
    if (e.code === 4404) { notice("The session no longer exists. Please start again.", "error"); S.sessionId = null; stop(); return; }
    S.retry += 1;
    if (S.retry > 8) { notice("Lost connection to the server.", "error"); stop(); return; }
    $("state-detail").textContent = "Reconnecting…";
    setTimeout(() => S.wantOn && openIngest(), Math.min(500 * 2 ** S.retry, 8000));
  };
  clearInterval(S.pingTimer);
  S.pingTimer = setInterval(() => ws.readyState === 1 && ws.send(JSON.stringify({ type: "ping" })), 4000);
}

function showStats(st) {
  if (!st) return;
  const parts = [];
  if (st.avg_final_latency != null) parts.push(`Avg. delay ${st.avg_final_latency} s`);
  if (st.utterances != null) parts.push(`${st.utterances} sentences`);
  if (st.backlog) parts.push(`Backlog ${st.backlog}`);
  if (st.backlog >= 3) parts.push("⚠ This model is too slow for real time on this computer – choose Parakeet, or use the GPU.");
  $("stats").textContent = parts.join(" · ");
}

async function start() {
  if (S.wantOn) return;
  notice("");
  S.wantOn = true;
  setState({ state: "loading", message: "Opening microphone…" });
  try {
    if (!(await ensureSession())) { S.wantOn = false; setState({ state: "idle" }); return; }
    await openMic();
    S.running = true;
    await openIngest();
  } catch (e) {
    notice(e.name === "NotAllowedError" ? "Microphone access was denied. Please allow it in the browser." : "Start failed: " + (e.message || e), "error");
    stop();
  }
}

function stop() {
  S.wantOn = false;
  S.running = false;
  clearInterval(S.pingTimer);
  if (S.ws && S.ws.readyState === 1) S.ws.send(JSON.stringify({ type: "stop" }));
  const ws = S.ws; S.ws = null;
  setTimeout(() => ws && ws.close(), 400);
  closeMic();
  setState({ state: "idle", message: "" });
}

// ------------------------------------------------------------------ wiring
async function endSession() {
  if (!S.sessionId || !confirm("End this session? The caption screen will disconnect and the transcript will be gone.")) return;
  stop();
  await fetch(`/api/captions/session/${S.sessionId}`, { method: "DELETE" });
  S.feed?.close(); S.feed = null; S.sessionId = null;
  renderer.reset(); $("share").hidden = true; $("stats").textContent = "";
}

async function exportTranscript() {
  const fmt = $("export-format").value;
  const res = await fetch(`/api/captions/session/${S.sessionId}/transcript?format=${fmt}`);
  if (!res.ok) { notice(await errorMessage(res), "error"); return; }
  const blob = await res.blob();
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `captions.${fmt}` });
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
  renderEngines(S.info);
  $("app").hidden = false;
  if (navigator.mediaDevices?.enumerateDevices) listMics().catch(() => {});
  if (!navigator.mediaDevices?.getUserMedia) notice("This browser or connection does not allow microphone access (HTTPS or localhost required).", "error");

  $("power").onclick = () => (S.wantOn ? stop() : start());
  for (const id of ["language", "polish", "local-model"]) $(id).addEventListener("change", onSettingsChange);
  $("engines").addEventListener("change", onSettingsChange);
  $("glossary").addEventListener("input", onSettingsChange);
  $("mic").addEventListener("change", () => { store.set({ mic: $("mic").value }); if (S.wantOn) { closeMic(); openMic().catch((e) => notice(e.message, "error")); } });
  $("clear").onclick = async () => { if (S.sessionId) await fetch(`/api/captions/session/${S.sessionId}/clear`, { method: "POST" }); else renderer.reset(); };
  $("copy-link").onclick = (e) => copy($("overlay-link").href, e.target);
  $("copy-obs").onclick = (e) => copy($("overlay-link").href + "&theme=transparent&controls=0&hide=0", e.target);
  $("export").onclick = exportTranscript;
  $("end").onclick = endSession;
  window.addEventListener("beforeunload", (e) => { if (S.wantOn) { e.preventDefault(); e.returnValue = ""; } });
}
init();
