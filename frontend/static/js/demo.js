const statusEl = document.getElementById("status");
const captionEl = document.getElementById("caption");
const startBtn = document.getElementById("start");
let sessionId = null;
let timer = null;

async function startSession() {
  const res = await fetch("/api/captions/session", { method: "POST" });
  const data = await res.json();
  sessionId = data.session_id;
  statusEl.textContent = "Session: " + sessionId;
  if (timer) clearInterval(timer);
  timer = setInterval(poll, 1500);
  poll();
}

async function poll() {
  if (!sessionId) return;
  const res = await fetch("/api/captions/session/" + sessionId + "/latest");
  const data = await res.json();
  captionEl.textContent = data.text;
}

startBtn.addEventListener("click", startSession);
