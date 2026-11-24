// End-of-lecture report for the professor: exact numbers plus Claude's analysis.
// Everything from the server is rendered as text nodes (the analysis is model output, never HTML).
const Report = (() => {
  const byId = (id) => document.getElementById(id);
  let last = { result: null, transcript: "" };
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  // "claude-opus-5-5" -> "Claude Opus 5.5"
  const modelName = (id) => (id || "Claude").replace(/^claude-/, "").split("-").reduce((acc, part) =>
    /^\d+$/.test(part) && /\d$/.test(acc) ? `${acc}.${part}` : `${acc} ${part[0].toUpperCase()}${part.slice(1)}`, "Claude");

  function el(tag, props = {}, ...children) {
    const node = Object.assign(document.createElement(tag), props);
    for (const c of children.flat()) if (c != null && c !== false) node.append(c);
    return node;
  }

  function section(title, hint, ...content) {
    return el("section", { className: "r-sec" }, el("h3", {}, title), hint ? el("p", { className: "hint" }, hint) : null, ...content);
  }

  function figures(stats) {
    const lec = stats.speakers.find((s) => s.speaker === 1);
    const aud = stats.speakers.find((s) => s.speaker === 2);
    const rows = [
      ["Duration", stats.duration],
      ["Sentences", String(stats.sentences)],
      ["Words", stats.words.toLocaleString()],
      ["Speaking pace", stats.words_per_minute ? `${stats.words_per_minute} words/min` : null],
    ];
    if (aud) rows.push([`${aud.label || "Microphone 2"} sentences`, String(aud.sentences)]);
    const questions = stats.speakers.reduce((n, s) => n + s.questions, 0);
    rows.push(["Questions asked", String(questions)]);
    if (lec && aud) rows.push([`${lec.label || "Microphone 1"} share`, `${Math.round((lec.words / Math.max(1, stats.words)) * 100)} % of words`]);
    return el("dl", { className: "figures" }, rows.map(([k, v]) =>
      el("div", {}, el("dt", {}, k), v == null ? el("dd", { className: "na" }, "Too short to measure") : el("dd", {}, v))));
  }

  function loading(ai) {
    byId("report").hidden = false;
    byId("report-actions").hidden = true;
    byId("report-h").textContent = "Lecture analysis";
    byId("report-body").replaceChildren(
      el("p", { className: "r-wait" }, ai ? "Claude is analysing the lecture. This usually takes about a minute." : "Collecting the numbers…"),
      el("progress", { className: "r-progress" }),
    );
    byId("report").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function show(result, transcript) {
    last = { result, transcript };
    const body = byId("report-body");
    byId("report").hidden = false;
    if (!result || result.error) {
      body.replaceChildren(el("p", { className: "notice error" }, result?.error || "The analysis failed."));
      byId("report-actions").hidden = !transcript;
      byId("dl-analysis").hidden = true;
      return;
    }
    const a = result.analysis;
    byId("report-h").textContent = a ? a.title : "Lecture in numbers";
    byId("dl-analysis").hidden = false;
    byId("report-actions").hidden = false;
    const parts = [figures(result.stats)];
    if (!a) {
      parts.push(el("p", { className: "hint" }, result.reason || "No AI analysis."));
      body.replaceChildren(...parts);
      byId("report").focus();
      return;
    }
    parts.push(el("p", { className: "r-summary" }, a.summary));

    const left = [], right = [];
    if (a.unclear_moments.length) left.push(section("Where students may get lost", null,
      el("ul", { className: "r-moments" }, a.unclear_moments.map((m) => el("li", {},
        el("p", { className: "r-time" }, m.time),
        el("blockquote", {}, m.quote),
        el("p", {}, m.issue),
        el("p", { className: "r-fix" }, el("b", {}, "Next time: "), m.suggestion),
      )))));
    if (a.audience_questions.length) left.push(section("Questions from the audience", null,
      el("ul", { className: "r-questions" }, a.audience_questions.map((q) => el("li", { className: q.answered ? "answered" : "open" },
        el("p", { className: "r-time" }, q.time, " ", el("span", { className: "r-status" }, q.answered ? "Answered" : "Still open")),
        el("p", { className: "r-q" }, q.question),
        el("p", { className: "hint" }, q.note),
      )))));
    if (a.topics.length) left.push(section("What was covered", null,
      el("ol", { className: "r-topics" }, a.topics.map((t) => el("li", {},
        el("span", { className: "r-time" }, t.start), el("b", {}, t.title), el("span", {}, t.summary))))));

    if (a.next_lecture.length) right.push(section("For the next lecture", null,
      el("ul", { className: "r-list" }, a.next_lecture.map((s) => el("li", {}, s)))));
    if (a.review_questions.length) right.push(section("Review questions for students", null,
      el("ol", { className: "r-list" }, a.review_questions.map((s) => el("li", {}, s)))));
    if (a.key_terms.length) right.push(section("Key terms", null,
      el("dl", { className: "r-terms" }, a.key_terms.map((k) => el("div", {}, el("dt", {}, k.term), el("dd", {}, k.explanation))))));
    if (a.likely_misrecognitions.length) right.push(section("Likely caption errors", "Words the speech recognition probably got wrong.",
      el("table", { className: "r-table" },
        el("thead", {}, el("tr", {}, el("th", {}, "Time"), el("th", {}, "Captioned"), el("th", {}, "Probably meant"))),
        el("tbody", {}, a.likely_misrecognitions.map((m) => el("tr", {}, el("td", {}, m.time), el("td", {}, m.heard), el("td", {}, m.probably_meant)))))));

    parts.push(el("div", { className: "r-grid" }, el("div", {}, left), el("div", {}, right)));
    parts.push(el("p", { className: "hint r-foot" }, `Analysed by ${modelName(result.model)} from the live captions. Check it against what you actually said: the captions can contain recognition errors.`));
    body.replaceChildren(...parts);
    byId("report").focus();
  }

  function markdown() {
    const { result } = last;
    const s = result.stats, a = result.analysis;
    const out = [`# ${a ? a.title : "Lecture in numbers"}`, ""];
    out.push(`- Duration: ${s.duration}`, `- Sentences: ${s.sentences}`, `- Words: ${s.words}`);
    if (s.words_per_minute) out.push(`- Speaking pace: ${s.words_per_minute} words/min`);
    for (const sp of s.speakers) out.push(`- ${sp.label || "Microphone " + sp.speaker}: ${plural(sp.sentences, "sentence", "sentences")}, ${plural(sp.questions, "question", "questions")}`);
    if (!a) return out.join("\n") + "\n";
    out.push("", "## Summary", "", a.summary);
    const list = (title, items, fmt) => { if (items.length) out.push("", `## ${title}`, "", ...items.map(fmt)); };
    list("Where students may get lost", a.unclear_moments, (m) => `- **${m.time}** "${m.quote}"  \n  ${m.issue}  \n  Next time: ${m.suggestion}`);
    list("Questions from the audience", a.audience_questions, (q) => `- **${q.time}** ${q.question} (${q.answered ? "answered" : "still open"})  \n  ${q.note}`);
    list("What was covered", a.topics, (t) => `- **${t.start}** ${t.title}: ${t.summary}`);
    list("For the next lecture", a.next_lecture, (x) => `- ${x}`);
    list("Review questions for students", a.review_questions, (x, i) => `${i + 1}. ${x}`);
    list("Key terms", a.key_terms, (k) => `- **${k.term}**: ${k.explanation}`);
    list("Likely caption errors", a.likely_misrecognitions, (m) => `- ${m.time}: "${m.heard}" probably means "${m.probably_meant}"`);
    out.push("", `_Analysed by ${modelName(result.model)} from the live captions._`);
    return out.join("\n") + "\n";
  }

  function download(kind) {
    const text = kind === "markdown" ? markdown() : last.transcript;
    const name = kind === "markdown" ? "lecture-analysis.md" : "captions.txt";
    const type = kind === "markdown" ? "text/markdown" : "text/plain";
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([text], { type: type + ";charset=utf-8" })), download: name });
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }

  return { loading, show, download, markdown };
})();
