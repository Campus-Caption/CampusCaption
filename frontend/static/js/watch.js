// Students' phone view.  /watch?session=ID  (from the QR code / room code)   /watch?code=K7P42Q  (code not found)
// Reuses CaptionFeed from captions-view.js. Gets the whole lecture so far once (?history=all), keeps scrollback
// across reconnects, and stops following the live line while the student scrolls up to re-read.
const $ = (id) => document.getElementById(id);
const q = new URLSearchParams(location.search);
const sid = q.get("session");

// ------------------------------------------------------------------ reading settings (this browser only)
const SIZES = ["s", "m", "l", "xl"];
const THEMES = ["dark", "light", "contrast"];
const THEME_COLOR = { dark: "#000000", light: "#ffffff", contrast: "#000000" };
let prefs = { size: "m", theme: "dark" };
try { Object.assign(prefs, JSON.parse(localStorage.getItem("cc-watch") || "{}")); } catch {}
if (!SIZES.includes(prefs.size)) prefs.size = "m";
if (!THEMES.includes(prefs.theme)) prefs.theme = "dark";

function applyPrefs() {
  document.body.dataset.size = prefs.size;
  document.body.dataset.theme = prefs.theme;
  document.querySelector('meta[name="theme-color"]').content = THEME_COLOR[prefs.theme];
  for (const b of document.querySelectorAll("#settings [data-size]")) b.setAttribute("aria-pressed", String(b.dataset.size === prefs.size));
  for (const b of document.querySelectorAll("#settings [data-theme]")) b.setAttribute("aria-pressed", String(b.dataset.theme === prefs.theme));
}
function savePrefs() { try { localStorage.setItem("cc-watch", JSON.stringify(prefs)); } catch {} }

$("settings").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.size) prefs.size = b.dataset.size;
  if (b.dataset.theme) prefs.theme = b.dataset.theme;
  savePrefs(); applyPrefs(); renderer.scroll();
});
$("b-settings").onclick = () => {
  const open = $("settings").hidden;
  $("settings").hidden = !open;
  $("b-settings").setAttribute("aria-expanded", String(open));
};
$("b-info").onclick = () => $("info").showModal?.();

// ------------------------------------------------------------------ status badge
function badge(state, text) {
  $("badge").dataset.state = state;
  $("badge-text").textContent = text;
}
let hasText = false, ended = false, lastStatus = null;
function showStatus(st) {
  if (ended || !st) return;
  lastStatus = st;
  if (st.state === "listening") badge("live", "Live captions · AI");
  else if (st.state === "loading") badge("loading", "Starting…");
  else if (st.state === "error") badge("error", "Captions interrupted");
  else badge("idle", hasText ? "Captions stopped" : "Waiting to start");
}

// ------------------------------------------------------------------ join screen
function showJoin(message = "", code = "") {
  $("watch").hidden = true;
  $("join").hidden = false;
  $("join-msg").textContent = message;
  $("join-msg").hidden = !message;
  badge("idle", "Not connected");
  if (code) $("code").value = code;
  $("code").focus();
}
$("join-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const code = $("code").value.toUpperCase().replace(/[^A-Z0-9]/g, "");
  if (code.length !== 6) { $("join-msg").hidden = false; $("join-msg").textContent = "The room code has 6 letters and digits, e.g. K7P-42Q."; return; }
  try {
    const r = await fetch(`/api/captions/join/${encodeURIComponent(code)}`);
    if (r.ok) { location.replace(`/watch?session=${encodeURIComponent((await r.json()).session_id)}`); return; }
    $("join-msg").textContent = r.status === 404 ? `No lecture with code ${code.slice(0, 3)}-${code.slice(3)} is running right now.` : "Could not join. Please try again.";
  } catch {
    $("join-msg").textContent = "No connection to the caption server. Are you on the same Wi-Fi as the lecture?";
  }
  $("join-msg").hidden = false;
});

// ------------------------------------------------------------------ captions
const capsEl = $("caps");
const renderer = new CaptionFeed.Renderer(capsEl, { maxHistory: 3000, keepOlder: true });
const NEAR_BOTTOM = 40; // px: closer than this to the end counts as "reading live"

function atBottom() { return capsEl.scrollHeight - capsEl.scrollTop - capsEl.clientHeight < NEAR_BOTTOM; }
capsEl.addEventListener("scroll", () => {
  renderer.follow = atBottom();
  $("jump").hidden = renderer.follow;
}, { passive: true });
$("jump").onclick = () => {
  renderer.follow = true;
  renderer.scroll();
  $("jump").hidden = true;
  capsEl.focus({ preventScroll: true });
};

function updateEmpty() {
  hasText = capsEl.children.length > 0;
  $("watch").classList.toggle("has-text", hasText);
}

// Keep the phone screen on while captions run (Screen Wake Lock; silently skipped where unsupported).
let wakeLock = null;
async function keepAwake() {
  if (ended || document.visibilityState !== "visible" || wakeLock || !navigator.wakeLock) return;
  try {
    wakeLock = await navigator.wakeLock.request("screen");
    wakeLock.addEventListener("release", () => { wakeLock = null; });
  } catch { wakeLock = null; }
}
document.addEventListener("visibilitychange", keepAwake);

function watch(sessionId) {
  $("join").hidden = true;
  $("watch").hidden = false;
  const feed = CaptionFeed.connect(sessionId, {
    onConnection(ok) {
      if (ended) return;
      if (!ok) badge("offline", "Reconnecting…");
      else { showStatus(lastStatus); keepAwake(); }
    },
    onEvent(ev) {
      if (ended) return;
      // Session gone: right after opening an old link -> join screen; mid-lecture (ended while offline) -> keep the text.
      if (ev.type === "error" && ev.fatal) {
        if (hasText) end();
        else { feed.close(); showJoin("This lecture has ended or the link is out of date. Enter the current room code."); }
        return;
      }
      if (renderer.apply(ev)) updateEmpty();
      if (ev.type === "snapshot") showStatus(ev.status);
      else if (ev.type === "status") showStatus(ev);
      else if (ev.type === "clear") showStatus(lastStatus);
      else if (ev.type === "ended") end();
    },
  }, { query: "history=all" });

  function end() {
    ended = true;
    feed.close();
    badge("ended", "Lecture ended");
    $("empty").textContent = "The lecture has ended.";
    wakeLock?.release().catch(() => {});
  }
}

applyPrefs();
if (sid) watch(sid);
else showJoin(q.get("code") ? `No lecture with code ${q.get("code").toUpperCase()} is running right now.` : "", q.get("code") || "");
