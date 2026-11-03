// Profile: "Your CV" (the master CV, explicit Save) and "Application details" (form answers, saves on change).
// The CV editor is also the "check every card" step of setup: then it edits a draft and nothing is saved until Save and continue.
const ENTRY_SECTIONS = ["experience", "projects", "volunteering", "courses"];
const ITEM_SECTIONS = ["certifications", "awards", "publications"];
const CV_NAV = ["contact", "summaries", "experience", "projects", "education", "skills", "languages", "certifications", "courses",
                "volunteering", "awards", "publications", "interests", "custom"];
const uid = prefix => prefix + Math.random().toString(36).slice(2, 7);
// split at commas, never one inside brackets: "Reinforcement Learning (PPO, SAC)" stays one skill
const csv = s => s.split(/,(?![^([]*[)\]])/).map(x => x.trim()).filter(Boolean);
// a letter's paragraphs: blank lines split them, single line breaks (PDF text) join
const splitParas = text => text.split(/\n\s*\n/).map(s => s.replace(/\s*\n\s*/g, " ").trim()).filter(Boolean).map(text => ({ id: uid("p"), text }));
// Questions application forms ask, by group. Salary, start, notice and permits live in preferences, shown in their groups.
const DETAIL_GROUPS = {
  personal: ["Salutation", "Gender (only where a form requires it)", "Nationality", "Driving licence"],
  permit: [],  // "Authorised to work in <country>?" per target country
  pay: ["Willing to relocate?"],
  studies: ["Highest degree", "Current university", "Current degree programme", "Expected graduation"],
  other: ["Years of experience", "How did you hear about us?"],  // plus every other saved or suggested question
};
const FROM_PREFS = /^(Salary expectation|Earliest start date|Notice period|Hours per week|Internship length \(months\)|Mandatory internship\?|Need visa sponsorship for .*)$/;  // shown as preferences

document.addEventListener("alpine:init", () => {
  Alpine.data("profileUpdate", () => ({  // keep the CV up to date: newer CV, what's new, or a few questions; shown before saving
    mode: "", news: "", questions: [], replies: {}, changes: [], draft: null, busy: "", err: "", ctl: null, pasteFor: "",
    open() { if (!this.$refs.upd.open) this.$refs.upd.showModal(); },
    reset() {  // also while it works: the request is dropped, nothing of it shows up later
      this.ctl?.abort();
      Object.assign(this, { mode: "", news: "", questions: [], replies: {}, changes: [], draft: null, busy: "", err: "" });
      this.$refs.upd.close();
    },
    async go(label, fn) {
      this.open(); this.busy = label; this.err = "";
      const ctl = this.ctl = new AbortController();
      try { await fn(ctl.signal); } catch (e) { if (!ctl.signal.aborted) this.err = e.message; }
      if (!ctl.signal.aborted) this.busy = "";
    },
    show(r) { this.draft = r.profile; this.changes = r.changes.length ? r.changes : [t("update.nothing")]; this.mode = "changes"; },
    replaced(p, from) {  // the whole CV becomes the one just read: shown first, saved only with Keep
      this.draft = p;
      this.changes = [t("update.replaced", { from, jobs: p.experience.length, edu: p.education.length })];
      this.mode = "changes";
    },
    newerCv(ev) { const f = ev.target.files[0]; ev.target.value = ""; if (f) this.file(f, "newer"); },
    replaceCv(ev) { const f = ev.target.files[0]; ev.target.value = ""; if (f) this.file(f, "replace"); },
    file(f, how) {  // a file that can't be read: its message, with a box to paste the CV's text instead
      this.pasteFor = how;
      const name = encodeURIComponent(f.name);
      this.go(t("setup.reading"), async signal => {
        try {
          if (how === "newer") this.show(await api(`/api/profile/merge-file?name=${name}`, { body: f, signal }));
          else this.replaced((await api(`/api/import?name=${name}`, { body: f, signal })).profile, f.name);
        } catch (e) { if (!signal.aborted) this.mode = "paste"; throw e; }
      });
    },
    pasteCv() {
      const text = this.news;
      this.go(t("setup.reading"), async signal => this.pasteFor === "newer"
        ? this.show(await api("/api/profile/merge", { json: { kind: "a newer CV", text }, signal }))
        : this.replaced((await api("/api/import-text", { json: { text }, signal })).profile, t("update.pasted")));
    },
    whatsNew() { this.mode = "news"; this.open(); },
    addNews() { this.go(t("setup.adding"), async signal => this.show(await api("/api/profile/merge", { json: { kind: "what's new, in their words", text: this.news }, signal }))); },
    improve() { this.go(t("setup.thinking"), async signal => { this.questions = (await api("/api/profile/gaps", { json: {}, signal })).questions; this.mode = "questions"; }); },
    addAnswers() {
      const text = this.questions.filter(q => (this.replies[q.id] || "").trim()).map(q => `Q: ${q.question}\nA: ${this.replies[q.id].trim()}`).join("\n\n");
      if (!text) return this.reset();
      this.go(t("setup.adding"), async signal => this.show(await api("/api/profile/merge", { json: { kind: "answers to questions about their CV", text }, signal })));
    },
    keep() {
      this.go(t("working"), async () => {
        await api("/api/data/profile", { method: "PUT", json: this.draft });
        this.reset();
        Alpine.store("toast").show(t("saved"));
        Alpine.store("app").show("profile");  // reload the editor with the new content
      });
    },
  }));

  Alpine.data("profileEditor", () => ({
    p: null, links: [], summaries: [], skills: [], interests: "", review: false, err: "", busy: false, open: {}, orig: null, saved: "", photoV: 0, asked: [],
    ENTRY_SECTIONS, ITEM_SECTIONS, CV_NAV,
    async init() {
      const app = Alpine.store("app");
      this.review = !!app.draft;
      try { this.load(structuredClone(Alpine.raw(app.draft) || (await api("/api/state")).profile)); }
      catch (e) { this.err = t("profile.load_failed") + " " + e.message; }
      if (!this.review) api("/api/profile/asked").then(a => this.asked = a).catch(() => {});
    },
    load(p) {
      for (const k of [...ENTRY_SECTIONS, ...ITEM_SECTIONS, "education", "languages", "custom"]) p[k] ||= [];
      p.basics ||= {};
      this.orig = structuredClone(p);
      this.p = p;
      this.links = Object.entries(p.basics.links || {}).map(([k, v]) => ({ k, v }));
      this.summaries = Object.entries(p.summaries || {}).map(([k, v]) => ({ k, v }));
      this.skills = Object.entries(p.skills || {}).map(([cat, items]) => ({ cat, items: items.join(", ") }));
      this.interests = (p.interests || []).join(", ");
      this.saved = this.snapshot();
    },
    snapshot() { return JSON.stringify([this.p, this.links, this.summaries, this.skills, this.interests]); },
    changed() { return !!this.p && this.snapshot() !== this.saved; },
    discard() { this.load(structuredClone(Alpine.raw(this.orig))); },
    dump() {
      const p = structuredClone(Alpine.raw(this.p));
      p.basics.links = Object.fromEntries(this.links.filter(l => l.k.trim()).map(l => [l.k.trim(), l.v]));
      p.summaries = Object.fromEntries(this.summaries.filter(s => s.k.trim()).map(s => [s.k.trim(), s.v]));
      p.skills = Object.fromEntries(this.skills.filter(s => s.cat.trim()).map(s => [s.cat.trim(), csv(s.items)]));
      p.interests = csv(this.interests);
      return p;
    },
    count(k) {
      if (k === "contact") return "";
      if (k === "summaries") return this.summaries.length;
      if (k === "skills") return this.skills.reduce((n, s) => n + csv(s.items).length, 0);
      if (k === "interests") return csv(this.interests).length;
      return this.p[k].length;
    },
    jump(k) { const el = document.getElementById("cv-" + k); el.scrollIntoView({ block: "start" }); el.focus({ preventScroll: true }); },
    photoUrl() { return this.p.basics.photo ? fileUrl("profile/" + this.p.basics.photo) + "&v=" + this.photoV : ""; },
    initials() { return (this.p.basics.name || "?").split(/\s+/).map(w => w[0]).slice(0, 2).join("").toUpperCase(); },
    line(e, ...keys) { return keys.map(k => k === "dates" ? [e.start, e.end].filter(Boolean).join(" – ") : e[k]).filter(Boolean).join(" · "); },
    toggle(id) { this.open[id] = !this.open[id]; },
    addEntry(list, prefix) {
      const e = { id: uid(prefix), title: "", org: "", unit: "", location: "", start: "", end: "", link: "", bullets: [] };
      list.push(e); this.open[e.id] = true;
    },
    addBullet(e) { e.bullets.push({ id: uid(e.id + "_"), text: "", tags: [] }); },
    addEducation() {
      const e = { id: uid("edu"), degree: "", school: "", location: "", start: "", end: "", details: "", only_for: [] };
      this.p.education.push(e); this.open[e.id] = true;
    },
    addItem(list, prefix) { list.push({ id: uid(prefix), name: "", issuer: "", date: "", link: "" }); },
    addCustom() { this.p.custom.push({ title: "", entries: [] }); },
    async photo(ev) {
      const f = ev.target.files[0];
      ev.target.value = "";
      if (!f) return;
      try {
        const name = (await api(`/api/upload?kind=photo&name=${encodeURIComponent(f.name)}`, { body: f })).file;
        this.p.basics.photo = this.orig.basics.photo = name;  // the upload saved it already: not an unsaved change
        this.saved = this.snapshot(); this.photoV = Date.now();
      } catch (e) { Alpine.store("toast").show(e.message, "bad"); }
    },
    move(list, i, d) { const j = i + d; if (j >= 0 && j < list.length) [list[i], list[j]] = [list[j], list[i]]; },
    async save() {
      this.busy = true;
      const p = this.dump();
      try { await api("/api/data/profile", { method: "PUT", json: p }); }
      catch (e) { this.busy = false; Alpine.store("toast").show(e.message, "bad"); return; }
      this.busy = false;
      this.load(p);
      const app = Alpine.store("app");
      app.dirty = false;
      if (this.review) {
        app.draft = null;
        app.setupStep = 2;
        app.setupPhase = "questions";
        app.show("setup");
      } else {
        Alpine.store("toast").show(t("saved"));
      }
      app.refresh();
    },
  }));

  Alpine.data("skillMap", () => ({  // Profile → Your CV → Skills: what postings are matched on; a click flips one, Rebuild asks the AI again
    list: [], busy: false, err: "",
    async init() { try { this.list = (await api("/api/skillmap")).concepts; } catch (e) { this.err = e.message; } },
    state(c) { return c.have >= 1 ? "have" : c.have > 0 ? "shown" : "not"; },
    label(c) { return t("profile.skillmap." + this.state(c)) + (c.set == null ? "" : " " + t("profile.skillmap.yours")); },
    async flip(c) {  // back to what the CV shows: no choice of their own stored
      const have = c.have >= 0.6 ? 0 : 1, value = have === c.found ? null : have;
      try { await api(`/api/skillmap/${encodeURIComponent(c.id)}`, { method: "PUT", json: { have: value } }); Object.assign(c, { have, set: value }); }
      catch (e) { Alpine.store("toast").show(e.message, "bad"); }
    },
    async rebuild() {
      this.busy = true; this.err = "";
      try { this.list = (await api("/api/skillmap/rebuild", { method: "POST" })).concepts; } catch (e) { this.err = e.message; }
      this.busy = false;
    },
  }));

  // Profile → Writing: CV instructions (cv_rules.md), letter instructions (style.md), their own letter (letter.md, paragraphs
  // split by blank lines) and lessons (lessons.md). One explicit Save for all four; Letter length is a setting, saved when picked.
  Alpine.data("writingEditor", () => ({
    paras: [], style: "", cvRules: "", lessons: "", letterLength: "full", saved: "", err: "", busy: false,
    async init() {
      try {
        const st = await api("/api/state");  // empty instructions show the defaults the AI uses then (both CV and letter)
        this.letterLength = st.preferences.letter_length;
        this.load(st.letter || "", st.style || st.default_style || "", st.cv_rules || st.default_cv_rules || "", st.lessons || "");
      }
      catch (e) { this.err = t("profile.load_failed") + " " + e.message; }
    },
    load(letter, style, cvRules, lessons) {
      Object.assign(this, { paras: splitParas(letter), style, cvRules, lessons });
      this.saved = this.snapshot();
    },
    texts() { return [this.letter(), this.style, this.cvRules, this.lessons]; },
    snapshot() { return JSON.stringify([this.paras.map(q => q.text), this.style, this.cvRules, this.lessons]); },
    changed() { return this.snapshot() !== this.saved; },
    letter() { return this.paras.map(q => q.text.trim()).filter(Boolean).join("\n\n"); },
    discard() { const [paras, ...rest] = JSON.parse(this.saved); this.load(paras.join("\n\n"), ...rest); },
    addRule(field, line) { this[field] = addRuleLine(this[field], line); },
    keepLetter(text) { if (!this.paras.length) this.paras = splitParas(text); },  // writingHelpers: a first letter becomes Your letter
    add() { this.paras.push({ id: uid("p"), text: "" }); },
    move(i, d) { const j = i + d; if (j >= 0 && j < this.paras.length) [this.paras[i], this.paras[j]] = [this.paras[j], this.paras[i]]; },
    async upload(ev) {  // text only, split by code: no AI
      const f = ev.target.files[0];
      ev.target.value = "";
      if (!f) return;
      this.busy = true;
      try { this.paras = splitParas((await api(`/api/extract-text?name=${encodeURIComponent(f.name)}`, { body: f })).text); }
      catch (e) { Alpine.store("toast").show(e.message, "bad"); }
      this.busy = false;
    },
    async save() {
      this.busy = true;
      try {
        const [letter, style, cvRules, lessons] = this.texts();
        for (const [kind, text] of [["letter", letter], ["style", style], ["cv_rules", cvRules], ["lessons", lessons]])
          await api(`/api/data/${kind}`, { method: "PUT", json: { text } });
        this.load(letter, style, cvRules, lessons);
        Alpine.store("app").dirty = false;
        Alpine.store("toast").show(t("saved"));
      } catch (e) { Alpine.store("toast").show(e.message, "bad"); }
      this.busy = false;
    },
  }));

  // Let AI help (parts/writing_helpers.html, in setup step 6 and Profile → Writing): learn the letter instructions from
  // letters they wrote or from the opening they pick, or turn four quick answers into rules through the writing Ask box.
  // The page around it has letterLength and keepLetter(text); results land in its editors (data-writing names them).
  Alpine.data("writingHelpers", () => ({
    way: "", samples: "", tone: "neutral", answers: { lead: "", always: "", never: "" }, openings: [], opening: -1, helpBusy: "", helpErr: "",
    letterField() { return this.$root.closest("[data-writing]").dataset.writing.split(",")[1]; },
    async help(label, fn) {
      this.helpBusy = label; this.helpErr = "";
      try { await fn(); } catch (e) { this.helpErr = e.message; }
      this.helpBusy = "";
    },
    letters(ev) {
      const files = [...ev.target.files];
      ev.target.value = "";
      this.help(t("helpers.reading"), async () => {
        for (const f of files) {
          const text = (await api(`/api/extract-text?name=${encodeURIComponent(f.name)}`, { body: f })).text.trim();
          if (!this.samples.trim()) await this.keepLetter(text);
          this.samples = [this.samples.trim(), text].filter(Boolean).join("\n\n---\n\n");
        }
      });
    },
    getOpenings() {
      return this.help(t("helpers.writing"), async () => {
        this.openings = (await api("/api/suggest/openings", { json: { tone: this.tone, length: t("profile.length_" + this.letterLength) } })).openings;
        this.opening = -1;
      });
    },
    makeStyle() {
      const o = this.openings[this.opening];
      const [answers, sample] = this.way === "own" ? [{ source: "letters they wrote themselves" }, this.samples.slice(0, 15000)]
        : [{ tone: this.tone, opening_they_picked: o.style }, o.text];
      return this.help(t("helpers.making"), async () => {
        this[this.letterField()] = (await api("/api/suggest/style", { json: { answers, sample } })).markdown;
        Alpine.store("toast").show(t("ask.writing_applied"));
      });
    },
    rules() {  // the writing Ask box (parts/ask.html) asks the AI and shows what would change
      const a = this.answers, said = [t("helpers.ask_tone", { v: t("helpers.tone_" + this.tone).toLowerCase() }),
        ...["lead", "always", "never"].filter(k => a[k].trim()).map(k => t("helpers.ask_" + k, { v: a[k].trim() }))];
      window.dispatchEvent(new CustomEvent("ask-writing", { detail: said.join(" ") }));
    },
  }));

  Alpine.data("appDetails", () => ({  // Profile → Application details; setup's application-details step includes the same part
    GROUPS: Object.keys(DETAIL_GROUPS), prefs: null, answers: {}, sug: {}, names: {}, extra: [], newQ: "", savedIn: "", err: "", timer: 0,
    async init() {
      try {
        const [st, sug] = await Promise.all([api("/api/state"), api("/api/answers/suggested")]);
        Object.assign(this, { prefs: st.preferences, answers: st.answers || {}, sug, names: st.countries });
      } catch (e) { this.err = t("details.load_failed") + " " + e.message; }
    },
    country(code) { return this.names[code] || code; },
    payFields() {  // what fits the kinds of jobs they look for: no notice period for students, hours for working students
      const jt = this.prefs.job_types || [], has = j => jt.includes(j);
      return ["salary", "start", ...(jt.some(j => !["student", "internship", "thesis"].includes(j)) ? ["notice"] : []),
              ...(has("student") ? ["hours"] : []), ...(has("internship") ? ["internship_months"] : [])];
    },
    keys(g) {
      if (g === "permit") return this.prefs.targets.map(c => `Authorised to work in ${this.country(c.code)}?`);
      if (g !== "other") return DETAIL_GROUPS[g];
      const placed = new Set(["personal", "permit", "pay", "studies"].flatMap(x => this.keys(x)));
      const more = [...Object.keys(this.answers), ...Object.keys(this.sug).filter(k => !FROM_PREFS.test(k)), ...this.extra];
      return [...new Set([...DETAIL_GROUPS.other, ...more])].filter(k => !placed.has(k));
    },
    removable(g, k) { return g === "other" && !DETAIL_GROUPS.other.includes(k) && !(k in this.sug); },
    suggested(k) { return !String(this.answers[k] ?? "").trim() && !!this.sug[k]; },
    pending() { return this.GROUPS.flatMap(g => this.keys(g)).filter(k => this.suggested(k)); },
    value(k) { return this.answers[k] ?? (this.suggested(k) ? this.sug[k] : ""); },
    done(g) { this.savedIn = g; clearTimeout(this.timer); this.timer = setTimeout(() => (this.savedIn = ""), 2500); },
    async put(g, json) {
      try { await api("/api/answers", { method: "PATCH", json }); this.done(g); }
      catch (e) { Alpine.store("toast").show(e.message, "bad"); }
    },
    set(g, k, v) {
      if (v.trim()) this.answers[k] = v; else delete this.answers[k];
      if (!v.trim()) this.extra = this.extra.filter(x => x !== k);
      return this.put(g, { [k]: v });
    },
    keep(g, k) { return this.set(g, k, this.sug[k]); },
    keepAll() {  // also what setup's Continue calls: every value shown gets saved
      const keys = this.pending();
      for (const k of keys) this.answers[k] = this.sug[k];
      return this.put("all", Object.fromEntries(keys.map(k => [k, this.sug[k]])));
    },
    async savePref(g, key) {
      try {
        await api("/api/preferences", { method: "PATCH", json: { [key]: Alpine.raw(this.prefs[key]) } });
        this.done(g);
        this.sug = await api("/api/answers/suggested");  // a permit changes what's suggested
        Alpine.store("app").refresh();
      } catch (e) { Alpine.store("toast").show(e.message, "bad"); }
    },
    addQuestion() {
      const q = this.newQ.trim();
      if (q && !this.keys("other").includes(q)) this.extra.push(q);
      this.newQ = "";
      this.$nextTick(() => document.getElementById("q-other-" + this.keys("other").indexOf(q))?.focus());
    },
  }));
});
