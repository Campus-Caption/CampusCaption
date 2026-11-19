// Shared caption renderer + listen-feed client used by the console preview and the overlay.
// Layout rule that fixes the prototype's "text jumps over 3 lines": text is LEFT-aligned and anchored to the
// bottom; finished sentences stay where they are, only the live sentence grows, and old lines scroll away.
const CaptionFeed = (() => {
  function connect(sessionId, handlers) {
    let ws, closed = false, retry = 500, pollTimer = null;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const open = () => {
      ws = new WebSocket(`${proto}://${location.host}/api/captions/ws/listen/${sessionId}`);
      ws.onopen = () => { retry = 500; handlers.onConnection?.(true); stopPoll(); };
      ws.onmessage = (m) => { try { handlers.onEvent(JSON.parse(m.data)); } catch (e) { console.error(e); } };
      ws.onclose = (e) => {
        handlers.onConnection?.(false);
        if (closed || e.code === 4404) return;
        startPoll();
        setTimeout(open, retry);
        retry = Math.min(retry * 2, 8000);
      };
    };
    const startPoll = () => {
      if (pollTimer) return;
      pollTimer = setInterval(async () => {
        try {
          const r = await fetch(`/api/captions/session/${sessionId}`);
          if (r.ok) handlers.onEvent(await r.json());
        } catch { /* offline */ }
      }, 1500);
    };
    const stopPoll = () => { clearInterval(pollTimer); pollTimer = null; };
    open();
    return { close() { closed = true; stopPoll(); ws && ws.close(); } };
  }

  class Renderer {
    constructor(el, { maxHistory = 30 } = {}) {
      this.el = el;
      this.sentences = new Map(); // utterance id -> {text, el, live}
      this.maxHistory = maxHistory;
    }
    reset(history = [], live = null) {
      this.el.textContent = "";
      this.sentences.clear();
      history.forEach((h) => this.final(h.utterance, h.text));
      if (live) this.partial(live.utterance, live.committed, live.tentative);
      this.scroll();
    }
    _node(id) {
      let s = this.sentences.get(id);
      if (!s) {
        const p = document.createElement("p");
        p.className = "cap-line";
        this.el.appendChild(p);
        s = { el: p };
        this.sentences.set(id, s);
        while (this.el.children.length > this.maxHistory) {
          const first = this.el.firstChild;
          for (const [k, v] of this.sentences) if (v.el === first) this.sentences.delete(k);
          first.remove();
        }
      }
      return s;
    }
    partial(id, committed, tentative) {
      const s = this._node(id);
      s.el.classList.add("live");
      s.el.textContent = "";
      const a = document.createElement("span"); a.textContent = committed;
      const b = document.createElement("span"); b.className = "tentative"; b.textContent = (committed ? " " : "") + tentative;
      s.el.append(a, b);
      this.scroll();
    }
    final(id, text) {
      if (!text) { const s = this.sentences.get(id); if (s) { s.el.remove(); this.sentences.delete(id); } return; }
      const s = this._node(id);
      s.el.classList.remove("live");
      s.el.textContent = text;
      this.scroll();
    }
    revise(id, text) { const s = this.sentences.get(id); if (s) s.el.textContent = text; }
    clear() { this.reset(); }
    scroll() { this.el.scrollTop = this.el.scrollHeight; }
    /** apply one feed event; returns true if it touched the text */
    apply(ev) {
      switch (ev.type) {
        case "snapshot": this.reset(ev.history, ev.live); return true;
        case "partial": this.partial(ev.utterance, ev.committed, ev.tentative); return true;
        case "final": this.final(ev.utterance, ev.text); return true;
        case "revise": this.revise(ev.utterance, ev.text); return true;
        case "clear": this.clear(); return true;
        default: return false;
      }
    }
  }
  return { connect, Renderer };
})();
