// Overlay for the beamer / OBS / students' second screen.
// ?session=ID  (required)  &size=40  &lines=3  &theme=dark|light|contrast|green|transparent
// &controls=0 (hide the buttons)  &hide=8 (fade out after N s without speech, 0 = never)  &off=1 (start hidden)
const q = new URLSearchParams(location.search);
const sid = q.get("session");
const stage = document.getElementById("stage");
const capsEl = document.getElementById("caps");
const hint = document.getElementById("hint");
const renderer = new CaptionFeed.Renderer(capsEl);
const THEMES = [["dark", "Dark"], ["light", "Light"], ["contrast", "Contrast"], ["green", "Green (OBS)"], ["transparent", "Transparent"]];
const LINES = [1, 2, 3, 4, 5];

let prefs = { size: 40, lines: 3, theme: "dark", on: true };
try { Object.assign(prefs, JSON.parse(localStorage.getItem("cc-overlay") || "{}")); } catch {}
if (q.get("size")) prefs.size = Math.max(16, Math.min(160, +q.get("size") || 40));
if (q.get("lines")) prefs.lines = Math.max(1, Math.min(6, +q.get("lines") || 3));
if (q.get("theme") && THEMES.some(([k]) => k === q.get("theme"))) prefs.theme = q.get("theme");
if (q.get("off") === "1") prefs.on = false;
if (q.get("controls") === "0") document.body.classList.add("nocontrols");
const hideAfter = q.has("hide") ? +q.get("hide") : 8;

function save() { try { localStorage.setItem("cc-overlay", JSON.stringify(prefs)); } catch {} }
function apply() {
  const r = document.documentElement.style;
  r.setProperty("--fs", prefs.size + "px");
  r.setProperty("--lines", prefs.lines);
  document.body.dataset.theme = prefs.theme;
  stage.classList.toggle("off", !prefs.on);
  const t = document.getElementById("b-toggle");
  t.textContent = "Captions: " + (prefs.on ? "on" : "off");
  t.setAttribute("aria-pressed", String(prefs.on));
  document.getElementById("b-lines").textContent = prefs.lines + (prefs.lines === 1 ? " line" : " lines");
  document.getElementById("b-theme").textContent = THEMES.find(([k]) => k === prefs.theme)[1];
  renderer.scroll();
}
const $ = (id) => document.getElementById(id);
$("b-toggle").onclick = () => { prefs.on = !prefs.on; save(); apply(); };
$("b-minus").onclick = () => { prefs.size = Math.max(16, prefs.size - 4); save(); apply(); };
$("b-plus").onclick = () => { prefs.size = Math.min(160, prefs.size + 4); save(); apply(); };
$("b-lines").onclick = () => { prefs.lines = LINES[(LINES.indexOf(prefs.lines) + 1) % LINES.length]; save(); apply(); };
$("b-theme").onclick = () => {
  const i = THEMES.findIndex(([k]) => k === prefs.theme);
  prefs.theme = THEMES[(i + 1) % THEMES.length][0]; save(); apply();
};
$("b-full").onclick = () => (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen?.());
$("b-pip").onclick = async () => {
  // Document Picture-in-Picture keeps the captions floating above other windows (Chrome/Edge).
  if (!window.documentPictureInPicture) { alert("Pop-out is not supported by this browser (Chrome/Edge)."); return; }
  const pip = await documentPictureInPicture.requestWindow({ width: 900, height: 260 });
  for (const l of document.querySelectorAll('link[rel="stylesheet"]')) {
    const c = pip.document.createElement("link"); c.rel = "stylesheet"; c.href = l.href; pip.document.head.appendChild(c);
  }
  pip.document.body.dataset.theme = prefs.theme;
  pip.document.body.classList.add("nocontrols");
  pip.document.documentElement.style.cssText = document.documentElement.style.cssText;
  pip.document.body.appendChild(stage);
  pip.addEventListener("pagehide", () => document.body.appendChild(stage));
};
document.addEventListener("keydown", (e) => {
  if (e.key === "+" || e.key === "=") $("b-plus").click();
  else if (e.key === "-") $("b-minus").click();
  else if (e.key === "f") $("b-full").click();
  else if (e.key === "c") $("b-toggle").click();
});

// ---- auto-hide after a pause, empty state ----
let idleTimer = null;
function activity() {
  stage.classList.remove("idle");
  clearTimeout(idleTimer);
  if (hideAfter > 0) idleTimer = setTimeout(() => stage.classList.add("idle"), hideAfter * 1000);
}
function updateEmpty() { stage.classList.toggle("empty", capsEl.children.length === 0); }

if (!sid) {
  hint.textContent = "No ?session=… in the address. Please copy the link from the console.";
} else {
  hint.textContent = "Waiting for captions…";
  CaptionFeed.connect(sid, {
    onConnection(ok) { if (!ok) hint.textContent = "Reconnecting…"; else if (!capsEl.children.length) hint.textContent = "Waiting for captions…"; },
    onEvent(ev) {
      if (renderer.apply(ev)) { activity(); updateEmpty(); if (capsEl.children.length) hint.textContent = ""; }
      else if (ev.type === "status" && ev.state === "loading") hint.textContent = ev.message || "Loading model…";
      else if (ev.type === "ended") hint.textContent = "The session has ended.";
      if (ev.type === "snapshot" && ev.status?.state === "loading") hint.textContent = ev.status.message || "";
    },
  });
}
apply(); updateEmpty();
