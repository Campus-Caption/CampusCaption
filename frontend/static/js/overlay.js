const params = new URLSearchParams(location.search);
const sessionId = params.get("session");
const line = document.getElementById("line");

async function poll() {
  if (!sessionId) {
    line.textContent = "Untertitel erscheinen hier… (?session=… setzen)";
    return;
  }
  try {
    const res = await fetch("/api/captions/session/" + sessionId + "/latest");
    const data = await res.json();
    line.textContent = data.text || "…";
  } catch (e) {
    line.textContent = "Verbindung zum Server…";
  }
}

setInterval(poll, 1000);
poll();
