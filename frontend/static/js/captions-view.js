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
      this.sentences = new Map(); // utterance id -> {el, speaker, live, committed, tentative, text}
      this.maxHistory = maxHistory;
      this.labels = ["Lecturer", "Audience"];
      this.multi = false; // labels are shown only once a second microphone is in use
    }
    setSpeakers(settings) {
      if (!settings) return;
      if (Array.isArray(settings.speakers)) this.labels = settings.speakers;
      this.multi = !!settings.multi_mic;
      for (const s of this.sentences.values()) this._paint(s);
    }
    reset(history = [], live = null) {
      this.el.textContent = "";
      this.sentences.clear();
      history.forEach((h) => this.final(h.utterance, h.text, h.speaker));
      for (const l of [].concat(live || [])) this.partial(l.utterance, l.committed, l.tentative, l.speaker);
      this.scroll();
    }
    _node(id, speaker) {
      let s = this.sentences.get(id);
      if (!s) {
        const p = document.createElement("p");
        p.className = "cap-line";
        this.el.appendChild(p);
        s = { el: p, speaker: speaker || 1 };
        this.sentences.set(id, s);
        while (this.el.children.length > this.maxHistory) {
          const first = this.el.firstChild;
          for (const [k, v] of this.sentences) if (v.el === first) this.sentences.delete(k);
          first.remove();
        }
      }
      if (speaker) s.speaker = speaker;
      return s;
    }
    _paint(s) {
      const el = s.el;
      el.textContent = "";
      el.classList.toggle("live", !!s.live);
      el.dataset.speaker = String(s.speaker);
      if (this.multi) {
        // Colour alone is not accessible: the speaker name is always written out as text.
        const tag = document.createElement("span");
        tag.className = "cap-speaker";
        tag.textContent = `[${this.labels[s.speaker - 1] || "Mic " + s.speaker}]`;
        el.append(tag, " ");
      }
      if (s.live) {
        const a = document.createElement("span"); a.textContent = s.committed;
        const b = document.createElement("span"); b.className = "tentative"; b.textContent = (s.committed ? " " : "") + s.tentative;
        el.append(a, b);
      } else {
        el.append(s.text);
      }
    }
    partial(id, committed, tentative, speaker) {
      const s = this._node(id, speaker);
      Object.assign(s, { live: true, committed, tentative });
      this._paint(s);
      this.scroll();
    }
    final(id, text, speaker) {
      if (!text) { const s = this.sentences.get(id); if (s) { s.el.remove(); this.sentences.delete(id); } return; }
      const s = this._node(id, speaker);
      Object.assign(s, { live: false, text });
      this._paint(s);
      this.scroll();
    }
    revise(id, text) { const s = this.sentences.get(id); if (s) { s.text = text; if (!s.live) this._paint(s); } }
    clear() { this.reset(); }
    scroll() { this.el.scrollTop = this.el.scrollHeight; }
    /** apply one feed event; returns true if it touched the text */
    apply(ev) {
      switch (ev.type) {
        case "snapshot": this.setSpeakers(ev.settings); this.reset(ev.history, ev.live); return true;
        case "settings": this.setSpeakers(ev.settings); return false;
        case "partial": this.partial(ev.utterance, ev.committed, ev.tentative, ev.speaker); return true;
        case "final": this.final(ev.utterance, ev.text, ev.speaker); return true;
        case "revise": this.revise(ev.utterance, ev.text); return true;
        case "clear": this.clear(); return true;
        default: return false;
      }
    }
  }
  return { connect, Renderer };
})();
