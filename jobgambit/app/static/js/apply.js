// The apply flow (review, documents, finish). fileUrl is shared with applications.js.
const fileUrl = path => `/api/file?path=${encodeURIComponent(path)}&t=${TOKEN}`;
// Tokens per AI call (the ai_calls ledger), shared with applications.html and Settings › AI.
window.stepName = s => { const k = `apply.u.${s}`, v = t(k); return v === k ? s : v; };
window.kTok = n => n == null ? "–" : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
const TOKS = ["input", "fresh", "cache_write", "cached", "output", "thinking", "cost_usd", "unknown_costs", "incomplete"];
window.usageTotal = rows => rows.reduce((a, u) => ({ ...a, ...Object.fromEntries(TOKS.map(k => [k, a[k] +
  (k === "unknown_costs" ? u.unknown_costs ?? Number(u.cost_status === "unknown") :
   k === "incomplete" ? u.incomplete ?? Number(["partial", "unknown"].includes(u.usage_status)) : u[k] || 0)])) }),
  { step: "total", total: true, ...Object.fromEntries(TOKS.map(k => [k, 0])) });
window.usageCost = u => u.cost_usd == null ? "?" : "$" + u.cost_usd.toFixed(2) +
  (u.unknown_costs || u.cost_status === "unknown" ? " + ?" : "");
// What changed in a line, word by word: [{t, op: "" | "add" | "del", b}] (longest common subsequence of the words).
// **bold** marks are read (b), never shown. Shown with x-text spans: no HTML from a line ever reaches the page.
window.wordDiff = (a = "", b = "") => {
  // words, spaces and punctuation each a token: "Python." and "Python" share the word, only the dot differs
  const tok = s => { let bold = false; return s.split(/(\s+|[.,;:!?()])/).filter(Boolean).flatMap(w => w.split("**").flatMap((p, i) => {
    if (i) bold = !bold; return p ? [{ t: p, b: bold }] : []; })); };
  const x = tok(a), y = tok(b), L = Array.from({ length: x.length + 1 }, () => new Uint16Array(y.length + 1));
  for (let i = x.length - 1; i >= 0; i--) for (let j = y.length - 1; j >= 0; j--)
    L[i][j] = x[i].t === y[j].t ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out = []; let i = 0, j = 0;
  while (i < x.length || j < y.length)
    if (i < x.length && j < y.length && x[i].t === y[j].t) { out.push({ ...y[j++], op: "" }); i++; }
    else if (i < x.length && (j === y.length || L[i + 1][j] >= L[i][j + 1])) out.push({ ...x[i++], op: "del" });
    else out.push({ ...y[j++], op: "add" });
  return out;
};

document.addEventListener("alpine:init", () => {
  Alpine.data("applyFlow", () => ({
    STEPS: ["review", "ready", "sent"],
    key: "", job: null, stage: "", fit: null, prop: null, files: [], usage: [], folder: "", docs: [], saving: null, checks: null, design: "", loading: true, again: null,
    busy: "", err: "", dec: {}, applied: null, missing: [], rule: "", ruleFor: "cv_rules", ruleNote: "", designs: [], looks: [], method: "company website", note: "", done: null, ask: "", changes: [], redo: "",
    edits: {}, diffs: [], byId: {},  // review blocks shown as text boxes (by block id); the Documents step's last changes; cards by id
    head: { subject: "", salutation: "" },  // the letter's subject and salutation, editable at the top of its block
    applicants: null,  // how many applied (LinkedIn postings), asked once a day
    LANGS: ["en", "de", "fr", "es", "it", "nl", "pt", "pl", "sv", "da"],
    async init() {
      this.key = Alpine.store("app").applyKey;
      this.loading = true; this.err = "";
      try {
        const [s, d] = await Promise.all([api(`/api/apply/${this.key}`), api("/api/designs")]);
        this.designs = d.designs;
        this.looks = Object.keys(d.presets);
        this.load(s);
        this.design = s.design || d.current;
      } catch (e) { this.err = e.message; }
      this.loading = false;
      api("/api/setup/ready").then(r => { this.missing = r.missing; }).catch(() => {});
      if (this.job) api(`/api/jobs/${this.key}/applicants`, { method: "POST" }).then(r => { this.applicants = r.applicants; }).catch(() => {});
      this.$watch("dec", () => this.saveChoices());
      this.$watch("head", () => this.saveChoices());
      this.makeCurrent();  // the folder holds this application's files only (none until its PDFs are made)
      if (this.stage === "drafting") return this.waitDraft();
      const fresh = ["new", "fit", "blocked"].includes(this.stage);  // "fit": a work folder from before the fit check was dropped
      if (fresh && !this.applied) this.draft();  // already applied: never a second application
    },
    async waitDraft() {  // opened while a draft is being written (another tab, a reload): wait for it, never a second run
      this.busy = t("apply.drafting");
      while (this.stage === "drafting") {
        await new Promise(r => setTimeout(r, 5000));
        try { this.load(await api(`/api/apply/${this.key}`)); } catch (e) { this.err = e.message; break; }
      }
      this.busy = "";
      if (this.stage === "new") this.draft();
    },
    makeCurrent() {
      api(`/api/apply/${this.key}/current`, { method: "POST" }).then(r => { if (r.warning) this.err = r.warning; }).catch(() => {});
    },
    stepNo() { return this.done ? 2 : Math.max(0, this.STEPS.indexOf(this.stage)); },
    load(s) {
      if (s.job) this.job = s.job;
      if ("applied" in s) this.applied = s.applied;
      this.stage = s.stage; this.fit = s.fit ?? this.fit; this.prop = s.proposal ?? this.prop;
      this.files = s.files || []; this.usage = s.usage || []; this.checks = s.checks || null; this.folder = s.folder || ""; this.docs = s.documents || [];
      if (this.prop) this.resetDecisions(s.choices);
    },
    resetDecisions(saved = null) {  // saved: the choices made before leaving the page
      this.dec = {}; this.edits = {};
      const all = [...this.prop.items, ...this.prop.letter], m = this.prop.letter_meta || {};
      this.byId = Object.fromEntries(all.map(c => [c.id, c]));
      for (const i of all)  // a flagged line starts on Before: only the person can confirm it
        this.dec[i.id] = saved?.[i.id] || { decision: i.flag ? "reject" : "accept", text: i.new, comment: "" };
      this.head = { subject: saved?.subject?.text ?? m.subject ?? "", salutation: saved?.salutation?.text ?? m.salutation ?? "" };
    },
    saveChoices() {  // each click is kept (debounced), so stopping half-way loses nothing
      clearTimeout(this.saving);
      if (this.stage !== "review") return;
      const choices = { ...this.dec, ...Object.fromEntries(this.headItems().map(h => [h.id, h])) };
      this.saving = setTimeout(() => api(`/api/apply/${this.key}/choices`, { method: "PUT", json: { choices } })
        .then(r => { if (r.terms && this.prop) this.prop.terms = r.terms; }).catch(() => {}), 600);  // the keyword bar these choices make
    },
    async step(label, fn) {
      this.busy = label; this.err = ""; this.again = () => this.step(label, fn);
      try { await fn(); } catch (e) { this.err = e.message; }
      this.busy = "";
      api(`/api/apply/${this.key}`).then(s => { this.usage = s.usage || []; }).catch(() => {});  // tokens of the step just run
      window.scrollTo(0, 0);
    },
    draft(wish = {}) {  // wish: {note, language} for this draft only
      this.step(t("apply.drafting"), async () => {
        this.prop = await api(`/api/apply/${this.key}/draft`, { json: wish });
        this.stage = "review"; this.resetDecisions();
      });
    },
    langName(code) { try { return new Intl.DisplayNames([document.documentElement.lang], { type: "language" }).of(code); } catch { return code; } },
    startOver(note = "", language = "") {  // throw this draft away and write a new one (one more AI run)
      if (!confirm(t("apply.start_over_confirm"))) return;
      clearTimeout(this.saving);
      this.step(t("apply.drafting"), async () => {
        await api(`/api/apply/${this.key}`, { method: "DELETE" });
        this.files = []; this.checks = null; this.changes = []; this.redo = ""; this.ask = "";
        this.prop = await api(`/api/apply/${this.key}/draft`, { json: { note, language } });
        this.stage = "review"; this.resetDecisions();
      });
    },
    card(id) { return this.byId[id]; },
    todo(s) { return this.prop.items.filter(c => c.group === s.id && !c.same); },  // what a block asks the person
    title(s) { return s.kind === "entry" ? s.title : t("apply.sec." + s.kind); },
    editing(s) { return !!this.prop.final || !!this.edits[s.id]; },  // Change text: every line is a text box from the start
    asks(id) { const c = this.card(id); return !!c && (!c.same || this.dec[id].text !== c.new); },  // a choice (a no-op the person edited is one)
    toggles(s, a) { return this.asks(a.card) && !this.editing(s); },  // a click switches this line
    orderOff(s) { return !!s.order && this.dec[s.order]?.decision === "reject"; },
    chosen(s, a) {  // which side of a row will print: "now" (After), "was" (Before) or "same" (nothing to choose)
      if (this.asks(a.card)) return this.dec[a.card].decision === "reject" ? "was" : "now";
      if (s.kind === "projects" && s.order && s.before.find(b => b.id === a.id).text !== a.text) return this.orderOff(s) ? "was" : "now";
      return a.auto ? "now" : "same";
    },
    spans(text) { return wordDiff("", text).map(w => ({ ...w, op: "" })); },  // a line as it prints: bold, nothing marked
    cells(s, k) {  // one row's [Before, After] words: removed words marked on the left, added ones on the right
      const was = s.before[k].text, a = s.after[k], now = a.card ? this.dec[a.card].text : a.text;
      if (!this.asks(a.card) && !a.auto) return [this.spans(was), this.spans(now)];
      const d = wordDiff(was, now);
      return [this.side(d, "was"), this.side(d, "now")];
    },
    toggle(id) { this.dec[id].decision = this.dec[id].decision === "reject" ? "accept" : "reject"; },
    blockState(s) {  // "accept" (Keep after), "reject" (Keep before) or "" (some of each)
      const d = this.todo(s).map(c => this.dec[c.id].decision === "reject" ? "reject" : "accept");
      return d.every(x => x === d[0]) ? d[0] : "";
    },
    setBlock(s, d) { for (const c of this.prop.items) if (c.group === s.id) this.dec[c.id].decision = d; },
    counts() {  // the footer: new text, kept as before, for AI to rewrite, nothing to choose
      const n = { a: 0, r: 0, c: 0, u: 0 };
      for (const c of [...this.prop.items, ...this.prop.letter]) {
        const d = this.dec[c.id];
        n[c.same ? "u" : d.comment.trim() ? "c" : d.decision === "reject" ? "r" : "a"]++;
      }
      return n;
    },
    headItems() {  // subject and salutation, when edited at the top of the letter
      const m = this.prop.letter_meta || {};
      return ["subject", "salutation"].filter(k => this.head[k].trim() && this.head[k] !== (m[k] || ""))
        .map(k => ({ id: k, decision: "edit", text: this.head[k].trim(), comment: "" }));
    },
    // a diff read as one whole sentence: "now" (what the document will say, added words marked) or "was" (the
    // original, removed words marked). Both in one line jumble the two sentences.
    side(diff, which) {  // a space is marked only between two marked words: the mark covers exactly the changed words
      const s = diff.filter(w => w.op !== (which === "now" ? "del" : "add"));
      return s.map((w, i) => w.op && !w.t.trim() && !(s[i - 1]?.op && s[i + 1]?.op) ? { ...w, op: "" } : w);
    },
    said(diff, which) { return this.side(diff, which).some(w => w.t.trim()); },
    // The posting's keywords (parts/keywords.html), as the server matched them against the CV that will print: in the
    // CV, they have it (the CV doesn't say it yet), related experience, not in their profile.
    kwGroups(terms = []) { return ["cv", "add", "related", "missing"].map(id => ({ id, terms: terms.filter(k => k.group === id) })).filter(g => g.terms.length); },
    askable(k) { return this.stage === "review" && k.group === "add" && !!this.dec[k.card]; },
    kwAsk(k) { return t("apply.kw.ask", { term: k.term }); },
    asked(k) { return !!this.dec[k.card]?.comment.includes(this.kwAsk(k)); },
    askAdd(k) {  // one click: a comment that sends the line whose fact names it to the revise run (a second click takes it back)
      const d = this.dec[k.card], ask = this.kwAsk(k);
      d.comment = this.asked(k) ? d.comment.replace(ask, "").trim() : [d.comment.trim(), ask].filter(Boolean).join(" ");
      const g = this.card(k.card)?.group;
      if (g) this.edits[g] = true;  // its block shows the comment
    },
    changedCount() { return this.finalItems().length; },
    finalItems() {  // Change text: only what the person changed (an edit) or asked AI about (a comment) goes back
      return [...[...this.prop.items, ...this.prop.letter].flatMap(c => {
        const d = this.dec[c.id];
        return d.comment.trim() ? [{ id: c.id, decision: "revise", text: c.new, comment: d.comment }]
             : d.text !== c.new ? [{ id: c.id, decision: "edit", text: d.text.trim(), comment: "" }] : [];
      }), ...this.headItems()];
    },
    editText() {
      this.step(t("working"), async () => {
        this.prop = await api(`/api/apply/${this.key}/edit`, { method: "POST" });
        this.stage = "review"; this.resetDecisions();
        for (const d of Object.values(this.dec)) d.decision = "accept";  // flags don't apply to the final text
      });
    },
    submit(nothing = false) {
      clearTimeout(this.saving);  // a late choices save would bring back what this submit decides
      const items = this.prop.final ? (nothing ? [] : this.finalItems()) : [...[...this.prop.items, ...this.prop.letter].map(card => {
        const d = this.dec[card.id];  // a line with a comment goes to AI; Keep before sends the original back
        const decision = d.comment.trim() ? "revise" : d.decision === "reject" ? "reject" : d.text !== card.new ? "edit" : "accept";
        return { id: card.id, decision, text: decision === "reject" ? card.old : d.text, comment: d.comment };
      }), ...this.headItems()];
      this.step(t("apply.building"), async () => {
        const r = await api(`/api/apply/${this.key}/decide`, { json: { items } });
        if (r.stage === "review") { this.prop = r.proposal; this.resetDecisions(); this.stage = "review"; }
        else { this.stage = "ready"; this.files = r.files; this.checks = r.checks; this.folder = (await api(`/api/apply/${this.key}`)).folder;
               if (r.warning) this.err = r.warning; }
      });
    },
    changeDocs() {  // the AI box: the request (empty = fix what the checks found) is applied and the PDFs rebuilt
      this.step(t("apply.building"), async () => {
        const r = await api(`/api/apply/${this.key}/change`, { json: { request: this.ask } });
        if (r.restart) return setTimeout(() => this.startOver(this.ask, r.restart));  // another language: a new draft in it
        this.files = r.files; this.checks = r.checks; this.diffs = r.diffs || [];
        this.changes = [...(r.refused || []), ...(this.diffs.length ? [] : [t("apply.docs_ai_none")])];  // refused: it would claim a skill
        this.ask = ""; if (r.warning) this.err = r.warning;
      });
    },
    rebuild() {
      this.step(t("apply.building"), async () => {
        const r = await api(`/api/apply/${this.key}/build`, { json: { design: this.design } });
        this.files = r.files; this.checks = r.checks;
      });
    },
    async addRule() {  // "always do this": one line added to the CV or letter instructions, used from the next application on
      if (!this.rule.trim()) return;
      try {
        await api("/api/instructions", { json: { kind: this.ruleFor, line: this.rule.trim() } });
        this.rule = ""; this.ruleNote = t("apply.rule_added");
      } catch (e) { this.ruleNote = e.message; }
    },
    pause() {  // Stop for now: the drafts are kept; opening the job again continues here
      api(`/api/apply/${this.key}/pause`, { method: "POST" }).then(() => Alpine.store("app").show("board")).catch(e => { this.err = e.message; });
    },
    openFolder() { api(`/api/apply/${this.key}/folder`, { method: "POST" }).catch(e => { this.err = e.message; }); },
    fixAt(k) {  // where a missing setup step is fixed: [tab, sub]
      return ({ basics: ["profile"], targets: ["settings", "search"], ai: ["help"], writing: ["profile", "writing"] })[k] || ["profile", "details"];
    },
    pdf(name) { return fileUrl(`work/${this.key}/${name}`) + `&v=${Date.now()}`; },
    finish(status) {
      this.step(t("working"), async () => {
        this.done = await api(`/api/apply/${this.key}/finish`, { json: { status, method: this.method, note: this.note } });
        Alpine.store("app").show("applications");
      });
    },
  }));

});
