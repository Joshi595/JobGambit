"""Apply to one posting: tailored CV + letter (one tailor run, one reviewer run), review cards,
the person's decisions, PDFs with checks, archive. Ported from the original pipeline's apply.py.

Work files live in <data>/work/<job key>/ until the application is archived in <data>/applications/.
"""
import json
import os
import re
import shutil
import threading
from datetime import date
from pathlib import Path

from ruamel.yaml import YAML

from rapidfuzz import fuzz

from jobgambit.core import ai, check, posting, prompts, render, score, skillmap, sources
from jobgambit.core import profile as pf
from jobgambit.core.data import db, home
from jobgambit.core.profile import Field, Model

MAX_PROJECTS = 3
ENTRY_SECTIONS = ("experience", "projects", "volunteering", "courses")


# ---------------------------------------------------------------- what the AI answers
class Job(Model):
    """What the tailor run reads off the posting; stored as fit.json for the later steps."""
    company: str
    role: str
    employment_type: str
    ref_id: str
    deadline: str
    country: str
    language: str
    blockers: list[str]
    contact_name: str


class BulletEdit(Model):
    id: str
    new: str
    reason: str
    keywords: list[str]
    flag: bool


class Order(Model):
    key: str
    ids: list[str]


class SkillEdit(Model):
    category: str
    items: str


class Letter(Model):
    subject: str
    salutation: str
    recipient: list[str]
    paragraphs: list[str]
    sources: list[list[str]] = Field(description="per paragraph, the fact-sheet ids it is built on ([] when none)")


class Tailor(Model):
    job: Job
    summary: str
    summary_reason: str
    bullets: list[BulletEdit]
    orders: list[Order]
    skills: list[SkillEdit]
    letter: Letter


class Edit(Model):
    id: str
    new: str
    reason: str
    note: str = ""
    flag: bool = False


class Review(Model):
    edits: list[Edit]
    missed: list[str]
    stretches: list[str]


class Revision(Model):
    edits: list[Edit]


class Change(Revision):
    language: str = Field(description="ISO 639-1 code of the language the person wants the documents in: the current one "
                                      "unless they ask for another")


class Lessons(Model):
    markdown: str


# ---------------------------------------------------------------- work folder
def work(key: str):
    """The work folder, created if missing (for writing). workdir(): the same path, never created (for reading)."""
    path = workdir(key)
    path.mkdir(parents=True, exist_ok=True)
    return path


def workdir(key: str) -> Path:
    """The work folder without creating it (for reading). A key is letters, digits, _ and - only: never a path ("..")."""
    if not re.fullmatch(r"[\w-]+", key or ""):
        raise ValueError(f"not a job key: {key[:40]!r}")
    return home() / "work" / key


def current() -> Path:
    """The fixed upload folder, "Current application" in the JobGambit folder (it comes with the repo; its contents
    are git-ignored): everything the person uploads when applying. Their own documents (certificates, transcripts,
    references…, added once during setup) stay; the CV and letter of the application open right now are swapped in and
    out. A test home keeps its own; installed as a package (no repo), Documents/JobGambit holds it."""
    if os.environ.get("JOBGAMBIT_HOME"):
        return home() / "Current application"
    repo = Path(__file__).resolve().parents[2]
    if (repo / "pyproject.toml").exists():
        return repo / "Current application"
    # ponytail: ~/Documents, not the Windows known-folder API; a Documents folder moved to OneDrive gets a local one.
    return Path.home() / "Documents" / "JobGambit" / "Current application"


def placed() -> list[str]:
    """The files the app put in the upload folder: only these are ever replaced or removed there."""
    p = home() / "current.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def show_files(folder: Path) -> str:
    """These PDFs (a work or archive folder) become the current application's; the previous application's go.
    Best effort: a PDF open in another program is locked on Windows; then a short message comes back, nothing breaks."""
    out = current()
    try:
        out.mkdir(parents=True, exist_ok=True)
        for name in placed():
            (out / name).unlink(missing_ok=True)
        names = [pdf.name for pdf in folder.glob("*.pdf")]
        for name in names:
            shutil.copy2(folder / name, out / name)
        (home() / "current.json").write_text(json.dumps(names), encoding="utf-8")
        return ""
    except OSError as e:
        return f"Couldn't update the Current application folder ({e.strerror}). Close the PDF if it is open, then try again."


def clear_current(folder: Path):
    """Sent: its CV and letter leave the upload folder (if they are still the ones there); the person's documents stay."""
    names = sorted(p.name for p in folder.glob("*.pdf"))
    if names and names == sorted(placed()):
        for name in names:
            try:
                (current() / name).unlink(missing_ok=True)
            except OSError:
                pass  # open in a PDF viewer: it stays; the next application replaces it
        (home() / "current.json").write_text("[]", encoding="utf-8")


def own_documents() -> list[str]:
    """What the person keeps in the upload folder besides the current CV and letter."""
    out, mine = current(), set(placed())
    return sorted(p.name + ("/" if p.is_dir() else "") for p in out.iterdir()
                  if p.name not in mine and p.name != "README.md") if out.is_dir() else []  # README: comes with the repo


def load(key, name, default=None):
    p = workdir(key) / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def dump(key, name, data):
    (work(key) / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def state(key) -> dict:
    s = load(key, "state", {"stage": "new"})
    if s["stage"] == "drafting" and key not in RUNNING:  # the app stopped mid-draft: start again
        s["stage"] = "new"
    s["fit"] = load(key, "fit")
    s["proposal"] = load(key, "proposal")
    s["choices"] = load(key, "choices")  # the review cards' choices so far: leaving the page keeps them
    if s["proposal"] and s["stage"] == "review":
        p, acc = pf.profile().model_dump(), load(key, "accepted", {})
        if "sections" not in s["proposal"]:  # written before the side-by-side Review
            blocks(key, s["proposal"], p, acc)
        s["proposal"]["terms"] = keyword_bar(key, s["proposal"], p, acc, s["choices"])
    s["files"] = sorted(p.name for p in workdir(key).glob("*.pdf"))
    s["usage"] = ai.calls(key)
    s["folder"] = str(current()) if s["files"] else ""
    s["documents"] = own_documents()
    return s


def set_stage(key, stage, **extra):
    s = load(key, "state", {})
    s.update(stage=stage, **extra)
    dump(key, "state", s)


# ---------------------------------------------------------------- facts the AI may use
def entries(p: dict) -> dict:
    out = {e["id"]: e for sec in ENTRY_SECTIONS for e in p.get(sec, [])}
    out.update({e["id"]: e for c in p.get("custom", []) for e in c.get("entries", [])})
    return out


def bullets(p: dict) -> dict:
    return {b["id"]: b["text"] for e in entries(p).values() for b in e.get("bullets", [])}


def factsheet(p: dict) -> str:
    out = []
    for sec in (*ENTRY_SECTIONS, "custom"):
        items = p.get(sec, []) if sec != "custom" else [e for c in p.get("custom", []) for e in c.get("entries", [])]
        for e in items:
            out.append(f"## [{e['id']}] {e.get('title', '')} | {e.get('org', '')} | {e.get('start', '')}–{e.get('end', '')} ({sec})")
            out += [f"- [{b['id']}] {b['text']}" for b in e.get("bullets", [])]
    out.append("## Education")
    out += [f"- {e.get('degree')}, {e.get('school')}, {e.get('start')}–{e.get('end')}. {e.get('details', '')}" for e in p.get("education", [])]
    out.append("## Skills")
    out += [f"- {k}: {', '.join(v)}" for k, v in p.get("skills", {}).items()]
    out.append("## Languages\n- " + "; ".join(f"{l['name']}: {l.get('level', '')}" for l in p.get("languages", [])))
    for sec in ("certifications", "awards", "publications"):
        if p.get(sec):
            out.append(f"## {sec.title()}")
            out += [f"- {i['name']}, {i.get('issuer', '')} {i.get('date', '')}" for i in p[sec]]
    if p.get("summaries"):
        out.append("## Profile texts\n" + "\n".join(f"- {v}" for v in p["summaries"].values()))
    return "\n".join(out)


def variants_text(limit=3) -> str:
    path = pf.folder() / "variants.yaml"
    lib = YAML(typ="safe").load(path) if path.exists() else {}
    return "\n".join(f"[{bid}] " + " | ".join(v["text"] for v in vs[-limit:]) for bid, vs in (lib or {}).items()) or "(none yet)"


def style_text() -> str:
    style = (pf.read("style") or prompts.load("default_style")).split("## Banned phrases")[0].strip()
    own = pf.read("letter").strip()  # Profile › Cover letter
    return style + ("\n\n## The person's own letter\nFollow its structure and voice; take facts only from the fact sheet.\n\n" + own if own else "")


def letter_size(pr: pf.Preferences | None = None) -> dict:
    """The letter's length as every writing prompt words it (Profile › Writing › Letter length)."""
    if (pr or prefs()).letter_length == "short":
        return {"paragraphs": "4", "words": "300-350", "fit": "it must fit one page"}
    return {"paragraphs": "5", "words": "450-500", "fit": "it fills one page with substance: never more than one page, never half empty"}


STORIES = 4000  # characters of the person's stories.md the tailor run reads


def stories_text() -> str:
    """Their own stories (profile/stories.md, optional), for the letter's one detail only they could write; '' without."""
    text = pf.read("stories").strip()
    if not text:
        return ""
    if len(text) > STORIES:
        text = text[:STORIES].rsplit("\n\n", 1)[0]  # whole paragraphs only
    return ("Their own stories, in their words (for the letter's one detail only they could write: use at most one detail "
            "from here, and only what the facts above also support):\n" + text + "\n")


def cv_rules() -> str:
    return pf.read("cv_rules").strip() or prompts.load("default_cv_rules").strip()  # Profile › Writing › CV instructions


def prefs() -> pf.Preferences:
    return pf.Preferences.model_validate(pf.read("preferences"))


def agent():
    return ai.chosen(prefs().agent)


def untrusted(text: str) -> str:
    """A posting or mail can't close (or reopen) its own fence, not even with a tag built from what one removal leaves."""
    while True:
        cleaned = re.sub(r"<\s*/?\s*untrusted\s*>", "", text, flags=re.I)
        if cleaned == text:
            return text
        text = cleaned


# ---------------------------------------------------------------- keywords: what the posting asks, what the CV says
def known(p: dict) -> dict:
    """The skill map against this CV (skillmap.concepts): index, have (1 named on the CV, 0.6 shown by one of its
    entries, 0 not in the profile), names, shows (the entries that show each), on_cv (the concepts the CV names)."""
    stored = skillmap.read()
    index, have, names, _ = skillmap.concepts(p, stored)
    where = {i: " · ".join(filter(None, [e.get("org"), e.get("title")])) for e in entries(p).values()
             for i in [e["id"], *(b["id"] for b in e.get("bullets", []))]}
    where.update({e["id"]: e.get("degree", "") for e in p.get("education", []) if e.get("id")})
    shows = {c["id"]: list(dict.fromkeys(where[i] for i in c.get("implied_by") or [] if where.get(i))) for c in stored.get("concepts") or []}
    return {"index": index, "have": have, "names": names, "shows": shows, "on_cv": index.find(score.cv_text(p))}


def keywords(desc: str, p: dict, cv: str = "", k: dict | None = None) -> list[dict]:
    """The posting's skills, tools, methods… (the skill map's concepts, found as the fit score finds them, in its
    requirements, tasks and nice-to-haves; never its company story): [{id, term (the posting's own word), name, have,
    must (a requirement), related (entries that show it), in_cv (cv, the text the CV prints, names it), group}],
    requirements first. group: cv (in the CV), add (they have it, the CV doesn't say it), related (one of their entries
    shows it, nothing names it: never claimed), missing (not in their profile: never claimed)."""
    k = k or known(p)
    out, on_cv = {}, k["index"].find(cv)
    for w, u in score.units(desc):
        for cid, said in k["index"].hits(u).items():
            t = out.setdefault(cid, {"id": cid, "term": said, "name": k["names"][cid], "have": k["have"][cid], "must": False})
            t["must"] = t["must"] or w >= score.WEIGHT["none"]
    words = {}  # one posting word two concepts share ("GPU", "DSGVO") shows once, as the CV best covers it
    for t in sorted(out.values(), key=lambda t: (t["id"] not in on_cv, -t["have"])):
        t.update(in_cv=t["id"] in on_cv, related=k["shows"].get(t["id"], []) if 0 < t["have"] < 1 else [])
        t["group"] = "cv" if t["in_cv"] else "add" if t["have"] >= 1 else "related" if t["have"] else "missing"
        if (w := words.setdefault(t["term"].lower(), t)) is not t:
            w["must"] = w["must"] or t["must"]
    return sorted(words.values(), key=lambda t: not t["must"])


def printed(cv: dict) -> str:
    """What a CV (cv_data) prints, for its keywords: one profile text, the projects shown, education for this job type."""
    return score.cv_text({**cv, "summaries": {"": cv.get("summary") or next(iter((cv.get("summaries") or {}).values()), "")}})


def unnamed(text: str, k: dict) -> list[str]:
    """The skills a CV text names that the person's CV doesn't and they don't have (or one of their entries only
    shows): the AI may never claim them."""
    odd = [c for c in k["index"].find(text) - k["on_cv"] if k["have"].get(c, 0) < 1]
    return sorted((k["names"][c] + (f" (related: {k['shows'][c][0]})" if k["shows"].get(c) and k["have"][c] else "") for c in odd), key=str.lower)


def ground(items: list, p: dict, k: dict | None = None):
    """What a tailored line may claim: a skills line only the person's own skills of its category (reordered or fewer);
    no bigger role than its master line (flag_scope); no skill the CV doesn't name. The last two are flagged: such a
    line stays as before unless the person confirms it."""
    for i in items:
        cat = i["id"].removeprefix("skills_")
        if i["id"].startswith("skills_") and cat in (p.get("skills") or {}):
            extra = [x for x in split_items(i["new"]) if not own_skills(p["skills"][cat], x)]
            kept = own_skills(p["skills"][cat], i["new"]) or list(map(str, p["skills"][cat]))  # none recognised: never an empty line
            i["new"] = f"{cat}: {', '.join(kept)}"
            i["reason"] = f"Not in your skills, so left out: {', '.join(extra)}. {i['reason']}".strip() if extra else i["reason"]
    flag_scope(items, bullets(p))
    k = k or known(p)
    for i in items:
        odd = [] if "order" in i or i.get("unnamed") else unnamed(i["new"], k)
        if odd:
            i.update(flag=True, unnamed=odd, reason=f"Names what your CV doesn't: {', '.join(odd)}. {i['reason']}".strip())


def chosen(prop: dict, acc: dict, choices: dict | None = None) -> dict:
    """The accepted lines the Review's choices so far make, as decide() will store them: earlier rounds, the automatic
    decisions, then each card as the person set it (not set yet: as proposed, a flagged one as before)."""
    out = {**acc, **{d["id"]: {k: d[k] for k in ("text", "order") if k in d} for d in prop.get("auto", [])}}
    for c in prop.get("items", []):
        d = (choices or {}).get(c["id"]) or {"decision": "reject" if c.get("flag") else "accept", "text": c["new"]}
        if d.get("decision") == "reject":
            out.pop(c["id"], None)
        else:
            out[c["id"]] = {"text": d.get("text", c["new"]), **({"order": c["order"]} if "order" in c else {})}
    return out


def keyword_bar(key: str, prop: dict, p: dict, acc: dict, choices: dict | None = None) -> list[dict]:
    """keywords() against the CV as these choices would print it (never the letter). A term they have that the CV
    doesn't say gets the card to ask: the one whose own line names it, else the profile text."""
    job, fit, k = posting.get(key) or {}, load(key, "fit") or {}, known(p)
    terms = keywords(job.get("description", ""), p, printed(cv_data(p, chosen(prop, acc, choices), job_kind(job, fit))), k)
    src = {**bullets(p), **{f"skills_{c}": ", ".join(map(str, v)) for c, v in (p.get("skills") or {}).items()}}
    cards = [c for c in prop.get("items", []) if "order" not in c]
    other = "summary" if any(c["id"] == "summary" for c in cards) else ""
    for t in terms:
        if t["group"] == "add":
            t["card"] = next((c["id"] for c in cards if t["id"] in k["index"].find(src.get(c["id"], c["old"]))), other)
    return terms


def save_choices(key: str, choices: dict) -> list[dict]:
    """The Review's choices so far (picked up again after leaving the page) -> the keyword bar they make."""
    dump(key, "choices", choices)
    prop = load(key, "proposal") or {}
    return keyword_bar(key, prop, pf.profile().model_dump(), load(key, "accepted", {}), choices) if prop else []


def term_lists(terms: list[dict], k: dict, other_language: bool) -> tuple[str, str]:
    """The tailor run's two lists: what the posting asks that the person has, and what they don't (never claimed).
    other_language: the documents aren't in the posting's language, so each term comes with its other names."""
    def line(t):
        also = [n for n in k["index"].names.get(t["id"], []) if n.lower() != t["term"].lower()][:4] if other_language else []
        return (f"- {t['term']}" + (f" (also: {', '.join(also)})" if also else "") + ("" if t["must"] else " (nice to have)")
                + (f" (related: {', '.join(t['related'])})" if t["related"] else ""))
    return ("\n".join(map(line, (t for t in terms if t["have"] >= 1))) or "(none found)",
            "\n".join(map(line, (t for t in terms if t["have"] < 1))) or "(none found)")


# ---------------------------------------------------------------- already applied
def _company(name) -> str:
    return sources.slug(re.sub(sources.LEGAL, "", name or "", flags=re.I), 60)


def _title(t) -> str:
    return " ".join(re.findall(r"\w+", sources.clean_title(t or "").lower()))


def applied_index():
    """Sent applications (not the ones saved for later), by job key, by company and by title, for applied_to()."""
    with db() as con:
        rows = [dict(r) for r in con.execute("select a.id, a.job_key, a.company, a.role, a.date_applied, a.status, j.location"
                                             " from applications a left join jobs j on j.key = a.job_key where a.status != 'pending'")]
    by_company, by_title = {}, {}
    for r in rows:
        by_company.setdefault(_company(r["company"]), []).append(r)
        by_title.setdefault(_title(r["role"]), []).append(r)
    return {r["job_key"]: r for r in rows if r["job_key"]}, by_company, by_title


def applied_to(job: dict, index=None) -> dict | None:
    """The application already sent for this job: the same posting, or the same job found on another site (same
    company, near-identical title; or the same title at another name of that employer, as the Board's duplicates:
    Mercedes-Benz / Mercedes-Benz Tech Innovation). None when there is none."""
    from jobgambit.core.search import same_company
    by_key, by_company, by_title = index or applied_index()
    if job.get("key") in by_key:
        return by_key[job["key"]]
    title = _title(job.get("title"))
    for a in by_company.get(_company(job.get("company")), []) if title else []:
        if fuzz.token_sort_ratio(title, _title(a["role"])) >= 95:  # punctuation and word order differ; an extra word is another job
            return a
    return next((a for a in by_title.get(title, []) if title and same_company(job, a)), None)


# ---------------------------------------------------------------- steps
LETTER_FILL = 0.85  # a standard letter fills at least this much of its one page
RESUMABLE = ("new", "shortlisted", "filtered", "paused")  # f"in {RESUMABLE}" is the SQL list; statuses an application in progress moves to 'applying' from
RUNNING, LOCK = set(), threading.Lock()  # applications being drafted right now: never two runs (and two bills) for one


def availability(pr: pf.Preferences) -> str:
    """What setup step 1 asked students (hours, studies, internship, thesis), for the tailor run."""
    grad = str((pf.read("answers") or {}).get("Expected graduation") or "").strip()
    parts = [pr.hours and f"{pr.hours} hours per week during the semester", grad and f"studies until {grad}",
             pr.mandatory_internship and "the internship must be a mandatory one (Pflichtpraktikum) in their studies",
             pr.internship_months and f"an internship of up to {pr.internship_months} months",
             pr.thesis_level and f"{pr.thesis_level} thesis"]
    return "; ".join(p for p in parts if p) or "not stated"


def discard(key: str):
    """Throw an application in progress away (Start over / Discard draft): its work folder, its uploads, its open
    questions; the job goes back to the Board as new. The AI ledger keeps its rows: that money was spent."""
    with LOCK:
        if key in RUNNING:
            raise ai.AIError("This application is being written right now. Wait until it's ready, then start over.")
    clear_current(workdir(key))
    shutil.rmtree(workdir(key), ignore_errors=True)
    shutil.rmtree(home() / "uploads" / key, ignore_errors=True)
    with db() as con:
        con.execute("delete from needs where job_key=?", (key,))
        con.execute("update jobs set status='new' where key=? and status in ('applying', 'paused')", (key,))


def draft(key: str, depth: str = "", stop_on_blocker: bool = False, note: str = "", language: str = "") -> dict:
    """Tailor run (which also reads the job's details off the posting) + reviewer run -> proposal cards (CV and letter).
    stop_on_blocker: a blocker found by the tailor run ends here, before the reviewer run.
    note: the person's wish for this draft only (Start over, the Apply page's AI box); language: overrides doc_language."""
    done = applied_to({**(posting.get(key) or {}), "key": key})
    if done:
        raise ai.AIError(f"You already applied to this job on {done['date_applied']}. It's in My applications.")
    with LOCK:
        if key in RUNNING:
            raise ai.AIError("This application is already being written. It shows here as soon as it's ready.")
        RUNNING.add(key)
    resume = load(key, "state", {}).get("stage") == "blocked" and load(key, "tailor")  # wanted anyway: its tailor run is paid for
    try:
        set_stage(key, "drafting")
        return _draft(key, depth, stop_on_blocker, resume, note.strip(), language)
    except BaseException:
        if load(key, "state", {}).get("stage") == "drafting":
            set_stage(key, "blocked" if resume else "new")
        raise
    finally:
        RUNNING.discard(key)


def _draft(key: str, depth: str, stop_on_blocker: bool, resume=None, note: str = "", language: str = "") -> dict:
    job = posting.get(key)
    with db() as con:  # Board › In progress, until it is archived
        con.execute(f"update jobs set status='applying' where key=? and status in {RESUMABLE}", (key,))
    p, pr = pf.profile().model_dump(), prefs()
    want = language or pr.doc_language
    permits = "; ".join(f"{c.code}: {c.permit or 'unknown'}" + (" (needs sponsorship)" if c.needs_sponsorship else "") for c in pr.targets)
    style = style_text()
    lessons, k = pf.read("lessons") or "(none yet)", known(p)
    has, lacks = term_lists(keywords(job["description"], p, "", k), k, want != "match" and want != score.posting_language(job["description"]))
    t = resume or ai.answer(prompts.build(
        "tailor", factsheet=factsheet(p), style=style, lessons=lessons, variants=variants_text(), has=has, lacks=lacks,
        cv_rules=cv_rules(), stories=stories_text(), language="the language the posting is written in" if want == "match" else want,
        languages=", ".join(f"{l['name']} {l.get('level', '')}" for l in p.get("languages", [])),
        posting=untrusted(job["description"]), max_projects=str(MAX_PROJECTS), **letter_size(pr),
        start=pr.start or "to be agreed", permits=permits or "not stated", availability=availability(pr),
        note=note or "(nothing)"), Tailor, agent(), 900, key).model_dump()
    dump(key, "tailor", t)
    fit = {**t["job"], "score": job.get("score")}
    dump(key, "fit", fit)
    with db() as con:
        con.execute("update jobs set company=coalesce(nullif(company, ''), ?), title=coalesce(nullif(title, ''), ?),"
                    " deadline=coalesce(nullif(deadline, ''), ?), country=coalesce(nullif(country, ''), ?) where key=?",
                    (fit["company"], fit["role"], fit["deadline"], fit["country"], key))
    if stop_on_blocker and fit["blockers"]:
        set_stage(key, "blocked")
        return {"stage": "blocked", "blockers": fit["blockers"]}
    lang = fit["language"] or ("en" if want == "match" else want)  # what the tailor wrote in (a note may ask another)
    items = cards(t, p, {})
    letter = t["letter"]
    draft_text = "\n".join(f"{i['id']}: {i['old']} → {i['new']}" for i in items) + \
                 f"\n\nsubject: {letter['subject']}\nsalutation: {letter['salutation']}\n" + \
                 "\n".join(f"p{n}: {x}" for n, x in enumerate(letter["paragraphs"], 1))
    rev = ai.answer(prompts.build("review", factsheet=factsheet(p), style=style, cv_rules=cv_rules(), draft=draft_text,
                                  lessons=lessons, permits=permits or "not stated",
                                  availability=availability(pr), start=pr.start or "to be agreed",
                                  posting=untrusted(job["description"])), Review, agent(), 900, key).model_dump()
    dump(key, "review", rev)
    merge_review(items, letter, rev["edits"], bullets(p))
    ground(items, p, k)
    proposal = {"items": items, "letter": [dict(id=f"p{n}", section=f"Paragraph {n}", old="", new=x, keywords=[], flag=False, reason="")
                                           for n, x in enumerate(letter["paragraphs"], 1)],
                "letter_meta": {m: letter[m] for m in ("subject", "salutation", "recipient", "paragraphs")},
                "missed": rev["missed"], "stretches": rev["stretches"], "language": lang}
    proposal = blocks(key, apply_depth(proposal, depth or pr.review_depth), p, {})
    dump(key, "proposal", proposal)
    (work(key) / "choices.json").unlink(missing_ok=True)
    set_stage(key, "review", language=lang)
    return proposal


def based(p: dict, ids: list) -> list[dict]:
    """A letter paragraph's sources as the person wrote them: whole CV lines (or entries) with where they are from
    (ids no longer in the CV are left out)."""
    ents = entries(p)
    owner = {b["id"]: (e, b["text"]) for e in ents.values() for b in e.get("bullets", [])}
    name = lambda e: " · ".join(filter(None, [e.get("org"), e.get("title")]))
    return [{"where": name(owner[i][0]), "text": re.sub(r"\*\*", "", owner[i][1])} if i in owner else {"where": "", "text": name(ents[i])}
            for i in ids or [] if i in owner or i in ents]


def label(text, n=6):
    return " ".join(re.sub(r"\*\*", "", text).split()[:n]) + "…"


def cards(t: dict, p: dict, accepted: dict) -> list[dict]:
    ents, bl = entries(p), bullets(p)
    items = []
    if t.get("summary"):
        old = accepted.get("summary", {}).get("text") or next(iter(p.get("summaries", {}).values()), "")
        items.append(dict(id="summary", section="Profile", old=old, new=t["summary"], reason=t.get("summary_reason", ""), keywords=[], flag=False))
    for o in t.get("orders", []):
        key, new = o["key"], o["ids"]
        if key == "skills":
            old, show, name = list(p.get("skills", {})), (lambda x: x), "Skills · category order"
        elif key == "projects":
            old, show, name = [x["id"] for x in p.get("projects", [])], (lambda x: ents[x]["title"]), f"Projects · the {MAX_PROJECTS} shown, in order"
        elif key in ents:
            old, show, name = [b["id"] for b in ents[key]["bullets"]], (lambda x: label(bl[x])), f"{ents[key].get('title')} · line order"
        else:
            continue
        new = [i for i in new if i in old]
        fmt = lambda seq: "  ".join(f"{n}. {show(i)}" for n, i in enumerate(seq, 1))
        left = [i for i in old if i not in new[:MAX_PROJECTS]] if key == "projects" else []
        items.append(dict(id=f"order_{key}", section=name, old=fmt(old), order=new, keywords=[], flag=False,
                          new=fmt(new[:MAX_PROJECTS] if key == "projects" else new) + (("   Left out: " + ", ".join(map(show, left))) if left else ""),
                          reason="Order only. To change it, use the comment box."))
    for e in t.get("bullets", []):
        if e["id"] not in bl:
            continue
        owner = next(x for x in ents.values() if any(b["id"] == e["id"] for b in x.get("bullets", [])))
        items.append(dict(id=e["id"], section=f"{owner.get('org') or owner.get('title')} · {'remove' if not e['new'] else 'line'}",
                          old=accepted.get(e["id"], {}).get("text") or bl[e["id"]], new=e["new"], reason=e.get("reason", ""),
                          keywords=e.get("keywords", []), flag=bool(e.get("flag"))))
    for s in t.get("skills", []):
        cat = s["category"]
        if cat in p.get("skills", {}):
            items.append(dict(id=f"skills_{cat}", section=f"Skills · {cat}", old=f"{cat}: {', '.join(p['skills'][cat])}",
                              new=f"{cat}: {s['items']}", reason="", keywords=[], flag=False))
    return items


def split_items(text: str) -> list[str]:
    """A skills line's items: split at commas outside brackets ("Reinforcement Learning (PPO, SAC)" is one)."""
    return [x.strip() for x in re.split(r",(?![^()]*\))", str(text).split(": ", 1)[-1]) if x.strip()]


def own_skills(have: list, text: str) -> list[str]:
    """The items of a skills line that are the person's own skills in this category, in the line's order and as the
    profile spells them: a skills edit (the AI's or anyone's) may reorder or leave out skills, never add one."""
    key = lambda x: "".join(score.norm(x).split())  # spaces aside: "ROS 2" is "ROS2"
    mine = {key(n): s for s in map(str, have) for n in [s, *score.aliases(s)]}
    return list(dict.fromkeys(mine[key(x)] for x in split_items(text) if key(x) in mine))


# Opening-verb scope tiers: 1 took part, 2 did the work, 3 owned it (EN + DE).
SCOPE = {**dict.fromkeys("contributed helped supported assisted unterstützte half wirkte".split(), 1),
         **dict.fromkeys("built implemented designed developed created entwickelte implementierte baute konzipierte erstellte".split(), 2),
         **dict.fromkeys("led owned architected drove spearheaded headed managed leitete verantwortete führte".split(), 3)}


def scope(text):
    w = re.findall(r"[^\W\d_]+", text.lower())[:1]
    return SCOPE.get(w[0], 0) if w else 0


def flag_scope(items, bl):
    """A line that opens with a bigger role than its master text ("Led" for "Built") is flagged."""
    for i in items:
        o, n = (scope(bl[i["id"]]), scope(i["new"])) if i["id"] in bl and i["new"] else (0, 0)
        if n > o and (n == 3 or o == 1) and not i.get("scope"):
            i.update(flag=True, scope=True, reason=f"Bigger role than your profile says ({bl[i['id']].split()[0].strip('*')}). {i['reason']}".strip())


def reviewed(e) -> str:
    """The reviewer's note as the person reads it (its internal category, e.g. 'keyword', stays out)."""
    note = (e.get("note") or "").strip().rstrip(".")
    return f"Reviewer: {note}." if note else "Sharpened by the reviewer."


def merge_review(items, letter, edits, bl):
    by = {i["id"]: i for i in items}
    for e in edits:
        if re.fullmatch(r"p\d+", e["id"]) and int(e["id"][1:]) <= len(letter["paragraphs"]):
            letter["paragraphs"][int(e["id"][1:]) - 1] = e["new"]
        elif e["id"] in by and "order" not in by[e["id"]]:
            by[e["id"]].update(new=e["new"], flag=by[e["id"]]["flag"] or e.get("flag", False),
                               reason=" ".join(filter(None, [reviewed(e), by[e["id"]]["reason"]])))
        elif e["id"] in bl:
            items.append(dict(id=e["id"], section="Added by the reviewer", old=bl[e["id"]], new=e["new"], keywords=[],
                              flag=e.get("flag", False), reason=reviewed(e)))


def plain(text: str) -> str:
    return " ".join(re.sub(r"\*\*", "", text or "").split())


def same(c: dict) -> bool:
    """A text card that changes nothing a reader sees: the same words, only bold moved, or skills only reordered."""
    if c["id"].startswith("skills_"):
        items = lambda s: sorted(split_items(plain(s)))
        return items(c["old"]) == items(c["new"])
    return plain(c["old"]) == plain(c["new"])


def align(before: list, after: list) -> list:
    """Rows for two versions of a list of ids: the after order, an id only in before put back below every id that came
    before it there."""
    rows = list(after)
    for n, i in enumerate(before):
        if i not in rows:
            rows.insert(max((rows.index(x) + 1 for x in before[:n]), default=0), i)
    return rows


def moved(before: list, after: list) -> dict:
    """The ids that changed place, "up" or "down": all but the longest run that kept its order (one line moved to the
    end is one move, not every other line moving up; on a tie, the line that went up is the one marked)."""
    new = [i for i in after if i in before]
    old = [i for i in before if i in after]
    run = [[k] for k in range(len(new))]  # ponytail: O(n²) longest increasing run; an entry has a handful of lines
    for k in range(len(new)):
        for j in range(k):
            if old.index(new[j]) < old.index(new[k]) and len(run[j]) + 1 > len(run[k]):
                run[k] = run[j] + [k]
    keep = {new[k] for k in max(reversed(run), key=len, default=[])}
    return {i: "up" if new.index(i) < old.index(i) else "down" for i in new if i not in keep}


def sections(prop: dict, p: dict, acc: dict) -> list[dict]:
    """The Review blocks: one per part of the CV the cards can change (profile text, each entry, the projects shown,
    skills), in printed order, every line of it: before (the CV as it stands; the profile itself for Change text) and
    after (every card and automatic decision as proposed), row by row. Each card gets its block's id (group); no-op
    cards are marked same, and so is a card no block holds: its line was deleted from the profile since, nothing prints it."""
    cards = {i["id"]: i for i in prop.get("items", [])}
    for c in cards.values():  # until a block below holds it
        c.update(group="", same=True)
    before = {} if prop.get("final") else acc
    after = {**acc, **{d["id"]: {k: d[k] for k in ("text", "order") if k in d} for d in prop.get("auto", [])},
             **{i["id"]: {"text": i["new"], **({"order": i["order"]} if "order" in i else {})} for i in cards.values()}}
    b_cv, a_cv = cv_data(p, before), cv_data(p, after)
    autos = {d["id"] for d in prop.get("auto", [])}
    out = []

    def block(bid, kind, title, b_rows, a_rows, order="", **extra):
        bt, at = dict(b_rows), dict(a_rows)
        move, rows = moved(list(bt), list(at)), align(list(bt), list(at))
        if order in cards:  # the projects shown are also a choice of which; an entry's removed lines have cards of their own
            cards[order].update(group=bid, same=not move and (kind != "projects" or set(bt) == set(at)))
        for i in rows:
            if i in cards:
                cards[i].update(group=bid, same=not prop.get("final") and same(cards[i]))
        out.append({"id": bid, "kind": kind, "title": title, **({"order": order} if order in cards else {}), **extra,
                    "before": [{"id": i, "text": bt.get(i, "")} for i in rows],
                    "after": [{"id": i, "text": at.get(i, ""), **({"card": i} if i in cards else {}),
                               **({"moved": move[i]} if i in move else {}), **({"auto": True} if i in autos and i not in cards else {})}
                              for i in rows]})

    def entry(e, title, printed=True):
        mine = [b["id"] for b in e.get("bullets", [])] + [f"order_{e['id']}"]
        if printed or any(i in cards for i in mine):
            block(e["id"], "entry", title, [(b["id"], b["text"]) for b in entry_lines(e, before)],
                  [(b["id"], b["text"]) for b in entry_lines(e, after)], f"order_{e['id']}", **({} if printed else {"out": True}))

    name = lambda *parts: " · ".join(filter(None, parts))
    text = lambda cv: cv.get("summary") or next(iter(p.get("summaries", {}).values()), "")
    for sec in render.ORDER:
        if sec == "summary" and (text(b_cv) or text(a_cv)):
            block("summary", "summary", "", [("summary", text(b_cv))], [("summary", text(a_cv))])
        elif sec == "projects":
            shown = [x["id"] for x in a_cv["projects"]]
            was = [x["id"] for x in b_cv["projects"]]
            if "order_projects" in cards or was != shown:
                title = {x["id"]: x.get("title", "") for x in p.get("projects", [])}
                block("projects", "projects", "", [(i, title[i]) for i in was], [(i, title[i]) for i in shown], "order_projects")
            ents = {x["id"]: x for x in p.get("projects", [])}
            for i in align(was, shown) + [i for i in ents if i not in was and i not in shown]:
                entry(ents[i], name(ents[i].get("title"), ents[i].get("org")), i in shown)
        elif sec in ENTRY_SECTIONS:
            for e in p.get(sec, []):
                entry(e, name(e.get("org"), e.get("title")))
        elif sec == "custom":
            for c in p.get("custom", []):
                for e in c.get("entries", []):
                    entry(e, name(c.get("title"), e.get("title")))
        elif sec == "skills" and p.get("skills"):
            row = lambda cv: [(f"skills_{k}", f"{k}: {', '.join(v)}") for k, v in cv["skills"].items()]
            block("skills", "skills", "", row(b_cv), row(a_cv), "order_skills")
    return out


def blocks(key: str, prop: dict, p: dict, acc: dict) -> dict:
    """The proposal with its Review blocks and each letter paragraph's sources (from the tailor run), also for a
    proposal written before either existed."""
    src = ((load(key, "tailor") or {}).get("letter") or {}).get("sources") or []
    for c in prop.get("letter", []):
        n = int(c["id"][1:])
        c.setdefault("based", based(p, src[n - 1] if n <= len(src) else []))
    prop["sections"] = sections(prop, p, acc)
    prop["terms"] = keyword_bar(key, prop, p, acc)
    return prop


def auto_decisions(items):
    """Every card accepted, except flagged ones: those keep the profile's wording (only the person can confirm them).
    auto: nobody chose it (never an approved wording); order: a new line or project order goes with its card."""
    return [dict(id=i["id"], decision="reject" if i.get("flag") else "accept", text=i["old"] if i.get("flag") else i["new"], comment="",
                 auto=True, **({"order": i["order"]} if "order" in i else {})) for i in items]


def apply_depth(proposal, depth):
    """'big': small unflagged line edits are pre-accepted and hidden; 'none': nothing is shown."""
    if depth == "all":
        return proposal
    hide = [i for i in proposal["items"] if depth == "none" or not (i.get("flag") or i["id"] == "summary" or not i["new"])]
    proposal["auto"] = auto_decisions(hide)
    proposal["items"] = [i for i in proposal["items"] if i not in hide]
    if depth == "none":
        proposal["auto"] += auto_decisions(proposal["letter"])
        proposal["letter"] = []
    return proposal


def decide(key: str, decisions: list[dict]) -> dict:
    """Store the person's decisions. Cards marked revise go to a revise run and come back as new cards."""
    prop = load(key, "proposal")
    (work(key) / "choices.json").unlink(missing_ok=True)
    with db() as con:  # a paused application that moves on is in progress again
        con.execute(f"update jobs set status='applying' where key=? and status in {RESUMABLE}", (key,))
    decisions = decisions + prop.get("auto", [])
    rounds = load(key, "rounds", [])
    rounds.append(decisions)
    dump(key, "rounds", rounds)
    acc = load(key, "accepted", {})
    meta = {i["id"]: i for i in prop["items"] + prop["letter"]}
    letter = load(key, "letter") or dict(prop["letter_meta"])  # the full draft, also when no paragraph is shown for review
    paras = {f"p{n}": x for n, x in enumerate(letter["paragraphs"], 1)}
    revise = []
    for d in decisions:
        if d["decision"] == "revise":
            revise.append(d)
        elif d["id"] in paras:
            paras[d["id"]] = "" if d["decision"] == "reject" else d["text"]
        elif d["id"] in ("subject", "salutation"):  # edited at the top of the letter's review
            letter[d["id"]] = d["text"].strip() or letter.get(d["id"], "")
        elif d["decision"] in ("accept", "edit"):
            src = meta.get(d["id"], d)  # an automatic decision's card is hidden (review depth big, none): it brings its order
            acc[d["id"]] = {"text": d["text"], **({"order": src["order"]} if "order" in src else {})}
        elif d["decision"] == "reject":
            acc.pop(d["id"], None)
    letter["paragraphs"] = [paras[k] for k in sorted(paras, key=lambda k: int(k[1:]))]  # "" = rejected, kept so p-ids stay stable
    dump(key, "accepted", acc)
    dump(key, "letter", letter)
    if not revise:
        return build(key)
    job, p = posting.get(key), pf.profile().model_dump()
    todo = "\n".join(f"{r['id']}: {meta[r['id']]['new']} | comment: {r.get('comment') or '(none)'}" for r in revise if r["id"] in meta)
    rev = ai.answer(prompts.build("revise", factsheet=factsheet(p), style=style_text(), cv_rules=cv_rules(), cards=todo, **letter_size(),
                                  posting=untrusted(job["description"]), language=prop["language"]), Revision, agent(), key=key).model_dump()
    fixed = {e["id"]: e for e in rev["edits"]}
    prop["items"] = [{**meta[r["id"]], "new": fixed.get(r["id"], {}).get("new", meta[r["id"]]["new"]),
                      "reason": "revised: " + fixed.get(r["id"], {}).get("reason", "")} for r in revise if r["id"] in meta and not r["id"] in paras]
    prop["letter"] = [{**meta[r["id"]], "new": fixed.get(r["id"], {}).get("new", meta[r["id"]]["new"])} for r in revise if r["id"] in paras]
    ground(prop["items"], p)
    prop["auto"] = []
    prop.pop("final", None)  # a Change-text comment comes back as a normal card: accept or reject the rewrite
    dump(key, "proposal", blocks(key, prop, p, acc))
    set_stage(key, "review")
    return {"stage": "review", "proposal": prop}


def edit_cards(key: str) -> dict:
    """Late changes from the Documents step: every line the PDFs show now, as cards to edit. Edits rebuild without AI
    (decide -> build); a comment sends only that card to the revise run."""
    final = final_cards(key)
    dump(key, "proposal", final)
    (work(key) / "choices.json").unlink(missing_ok=True)
    set_stage(key, "review")
    return final


def change(key: str, request: str) -> dict:
    """The Documents step's AI box: one run turns the person's request (empty: fix what the checks found) into edits of
    the lines the PDFs show; they are applied and the PDFs rebuilt right away. Change text shows the result line by line."""
    final, s, job, p = final_cards(key), load(key, "state", {}), posting.get(key), pf.profile().model_dump()
    lines = final["items"] + final["letter"]
    problems = [f for r in (s.get("checks") or {}).values() for f in r.get("fails", [])]
    rev = ai.answer(prompts.build(
        "change", factsheet=factsheet(p), style=style_text(), cv_rules=cv_rules(), **letter_size(),
        cards="\n".join(f"{c['id']} ({c['section']}): {c['new']}" for c in lines), problems="\n".join(problems) or "(none)",
        request=request.strip() or "Fix the problems the checks found.", posting=untrusted(job["description"]),
        language=final["language"]), Change, agent(), key=key).model_dump()
    if rev["language"] and rev["language"].lower() != final["language"]:  # subject, salutation, every line: a new draft
        return {"restart": rev["language"].lower()}
    by, k = {c["id"]: c for c in lines}, known(p)
    odd = {e["id"]: unnamed(e["new"], k) for e in rev["edits"] if e["id"] in by and not re.fullmatch(r"p\d+", e["id"])}
    edits = [e for e in rev["edits"] if e["id"] in by and not odd.get(e["id"])]  # a CV line never names a skill the profile doesn't
    dump(key, "proposal", final)
    r = decide(key, [{"id": e["id"], "decision": "edit", "text": e["new"], "auto": True} for e in edits])  # the AI's words: never an approved wording
    return {**r, "changes": [e["reason"] for e in edits],
            "refused": [f"Not changed: {by[i]['section']} would name {', '.join(n)}, which your profile doesn't." for i, n in odd.items() if n],
            # what changed, line by line: the page shows each as a word diff
            "diffs": [{"id": e["id"], "section": by[e["id"]]["section"], "old": by[e["id"]]["new"], "new": e["new"], "reason": e["reason"]}
                      for e in edits]}


def final_cards(key: str) -> dict:
    s, fit, job, prop = load(key, "state", {}), load(key, "fit"), posting.get(key), load(key, "proposal")
    p, acc = pf.profile().model_dump(), load(key, "accepted", {})
    cv = cv_data(p, acc, job_kind(job, fit))
    card = lambda i, sec, text: dict(id=i, section=sec, old="", new=text, reason="", keywords=[], flag=False)
    items = [card("summary", "Summary", cv.get("summary") or next(iter(p.get("summaries", {}).values()), ""))]
    for sec in ENTRY_SECTIONS:
        items += [card(b["id"], " · ".join(filter(None, [e.get("org"), e.get("title")])), b["text"]) for e in cv.get(sec, []) for b in e["bullets"]]
    items += [card(b["id"], " · ".join(filter(None, [c.get("title"), e.get("title")])), b["text"]) for c in cv["custom"] for e in c["entries"] for b in e["bullets"]]
    items += [card(f"skills_{k}", "Skills", f"{k}: {', '.join(v)}") for k, v in cv["skills"].items()]
    letter = load(key, "letter") or {}
    final = {"items": items, "letter": [card(f"p{n}", f"Paragraph {n}", x) for n, x in enumerate(letter.get("paragraphs", []), 1) if x],
             "letter_meta": letter, "missed": [], "stretches": [], "final": True, "auto": [],
             "language": s.get("language", prop.get("language", "en") if prop else "en")}
    return blocks(key, final, p, acc)


# ---------------------------------------------------------------- build
def job_kind(job: dict, fit: dict) -> str:
    """The job type a CV is for (some education shows only for some types, e.g. student jobs)."""
    from jobgambit.core.score import Matcher
    return job.get("type") or Matcher({}, {}).kind(f"{job.get('title', '')} {fit.get('employment_type', '')} {fit.get('role', '')}",
                                                   fit.get("country", ""))


def entry_lines(e: dict, acc: dict) -> list[dict]:
    """An entry's lines as the CV prints them: accepted wording and line order, removed lines gone."""
    text = lambda b: acc[b["id"]]["text"] if b["id"] in acc else b["text"]
    bl = [dict(id=b["id"], text=text(b)) for b in e.get("bullets", []) if text(b)]
    want = acc.get(f"order_{e['id']}", {}).get("order")
    if want:
        bl.sort(key=lambda b: want.index(b["id"]) if b["id"] in want else len(want))
    return bl


def cv_data(p: dict, acc: dict, kind: str = "") -> dict:
    def entry(e):
        return {**{k: v for k, v in e.items() if k != "bullets"}, "bullets": entry_lines(e, acc)}

    cv = {**p, **{sec: [entry(e) for e in p.get(sec, [])] for sec in ENTRY_SECTIONS}}
    cv["custom"] = [{**c, "entries": [entry(e) for e in c.get("entries", [])]} for c in p.get("custom", [])]
    want = acc.get("order_projects", {}).get("order")
    if want:
        cv["projects"].sort(key=lambda x: want.index(x["id"]) if x["id"] in want else len(want))
    cv["projects"] = cv["projects"][:MAX_PROJECTS]
    skills = {k: own_skills(v, acc[f"skills_{k}"]["text"]) if f"skills_{k}" in acc else v for k, v in p.get("skills", {}).items()}
    want = acc.get("order_skills", {}).get("order")
    if want:
        skills = {k: skills[k] for k in want + [k for k in skills if k not in want]}
    cv["skills"] = skills
    cv["education"] = [e for e in p.get("education", []) if not e.get("only_for") or kind in e["only_for"]]
    if "summary" in acc:
        cv["summary"] = acc["summary"]["text"]
    return cv


def filename(name: str, kind: str, company: str) -> str:
    clean = lambda s, n=40: re.sub(r"[^\w]+", "_", s, flags=re.UNICODE).strip("_")[:n].strip("_")  # Windows paths max 260
    return f"{clean(name, 30)}_{kind}_{clean(company, 24)}.pdf"


def placement(pdf: bytes, projects: list[dict]) -> tuple[int, int, int]:
    """How a CV PDF sits on its pages: (projects split across a page break, layout problems, pages). Lower is better."""
    from io import BytesIO
    from pypdf import PdfReader
    texts = [" ".join((page.extract_text() or "").split()) for page in PdfReader(BytesIO(pdf)).pages]

    def pages_of(project):  # the pages its bullets are on, found by their first words
        starts = [" ".join(b["text"].replace("**", "").split()[:4]) for b in project.get("bullets") or [] if b.get("text")]
        return {n for n, t in enumerate(texts) for s in starts if s and s in t}
    return sum(len(pages_of(p)) > 1 for p in projects), len(check.layout(BytesIO(pdf))[0]), len(texts)


def tidy(cv: dict, draw, limit: int) -> tuple[dict, bytes]:
    """The CV with its projects in the order that places them best: within the page limit, none cut by a page break and
    no layout problems if some order manages that, the space at a page's end used by a project that fits it. The
    tailored order wins ties. Projects only: experience stays in time order. draw(cv) -> PDF; at most 6 orders (3 projects)."""
    import itertools
    pdf = draw(cv)
    rate = lambda pdf, order: (lambda s: (s[2] > limit, s[0] + s[1]))(placement(pdf, order))
    best, order = rate(pdf, cv["projects"]), cv["projects"]
    for other in list(itertools.permutations(cv["projects"]))[1:] if best != (False, 0) else []:
        candidate = draw({**cv, "projects": list(other)})
        if (r := rate(candidate, list(other))) < best:
            best, order, pdf = r, list(other), candidate
            if best == (False, 0):
                break
    return {**cv, "projects": order}, pdf


def build(key: str, design: str = "") -> dict:
    """PDFs from the accepted state, then the checks. Over the page limit: the last project is left out once."""
    s, fit, job = load(key, "state", {}), load(key, "fit"), posting.get(key)
    p = pf.profile().model_dump()
    look = design or s.get("design") or prefs().design  # a saved look (preset) or a design id
    design, settings = render.resolve(look)
    lang, country = s.get("language", "en"), fit.get("country", "")
    pages = render.page_limit(design, settings, country)
    cv = cv_data(p, load(key, "accepted", {}), job_kind(job, fit))
    for old in work(key).glob("*.pdf"):
        old.unlink()
    name = p["basics"].get("name") or "CV"
    cv_path = work(key) / filename(name, "CV", fit["company"])
    pdf = render.cv_pdf(cv, design, lang, country, settings=settings)
    from io import BytesIO
    from pypdf import PdfReader
    if len(PdfReader(BytesIO(pdf)).pages) > pages and len(cv["projects"]) > 1:
        cv["projects"] = cv["projects"][:-1]
        pdf = render.cv_pdf(cv, design, lang, country, settings=settings)
    cv, pdf = tidy(cv, lambda c: render.cv_pdf(c, design, lang, country, settings=settings), pages)  # a clean page break
    cv_path.write_bytes(pdf)
    letter = load(key, "letter") or {}
    letter_path = work(key) / filename(name, "Cover_Letter", fit["company"])
    letter_path.write_bytes(render.letter_pdf(letter, p["basics"], design, lang, country, settings=settings))
    k, text = known(p), printed(cv)  # the CV as printed: its skills and tools from the profile only, never the posting's
    checks = {"cv": {**check.check("", pdf=cv_path, max_pages=pages), "terms": keywords(job["description"], p, text, k)},
              "letter": check.check("", job["description"], pdf=letter_path, max_pages=1,
                                    fill=0 if prefs().letter_length == "short" else LETTER_FILL)}
    if odd := unnamed(text, k):
        checks["cv"]["warns"].append("names skills your profile doesn't: " + ", ".join(odd))
    set_stage(key, "ready", design=look, checks=checks)
    return {"stage": "ready", "files": [cv_path.name, letter_path.name], "checks": checks}


# ---------------------------------------------------------------- finish
def add_variants(acc, bl, source, rounds=()):
    """The lines the person accepted (or wrote) on Review become approved wordings, reused in later applications. What
    nobody chose (review depth big or none, automatic runs and their fixes) never does."""
    mine = {d["id"]: d.get("text") for r in rounds for d in r if d.get("decision") in ("accept", "edit") and not d.get("auto")}
    path = pf.folder() / "variants.yaml"
    lib = YAML(typ="safe").load(path) if path.exists() else {}
    lib = lib or {}
    for bid, v in acc.items():
        if bid in bl and v["text"] and v["text"] == mine.get(bid) and v["text"] != bl[bid] and v["text"] not in [x["text"] for x in lib.get(bid, [])]:
            lib.setdefault(bid, []).append({"text": v["text"], "source": source, "date": date.today().isoformat()})
    out = YAML()
    out.width = 4096
    with path.open("w", encoding="utf-8") as f:
        f.write("# Wordings you approved in past applications (reused in new ones).\n")
        out.dump(lib, f)


def finish(key: str, status: str = "applied", method: str = "", note: str = "", when: str = "", clear: bool = True) -> dict:
    """Archives the application. clear: a sent one's CV and letter leave the upload folder (off for runs that never
    put them there)."""
    fit, job = load(key, "fit"), posting.get(key)
    fit = {**fit, "company": job.get("company") or fit["company"], "role": job.get("title") or fit["role"]}  # as the Board shows it
    p = pf.profile().model_dump()
    when = when or date.today().isoformat()
    slug = lambda x, n: re.sub(r"[^\w]+", "-", x, flags=re.UNICODE).strip("-")[:n].strip("-")  # Windows paths max 260
    with db() as con:  # one saved for later (pending): that row and folder, never a second one
        pending = con.execute("select id, folder from applications where job_key=? and status='pending' order by id desc", (key,)).fetchone()
    folder = home() / "applications" / f"{when}_{slug(fit['company'], 28)}_{slug(fit['role'], 32)}"
    if pending and pending["folder"]:
        folder = home() / "applications" / pending["folder"]
    elif folder.exists():
        folder = folder.with_name(folder.name + "_" + key[:4])
    learn(key)
    if clear and status != "pending":
        clear_current(work(key))
    shutil.copytree(work(key), folder, dirs_exist_ok=True)
    (folder / "posting.md").write_text(f"# {fit['role']} at {fit['company']}\n- URL: {job.get('url') or 'pasted'}\n\n{job['description']}\n",
                                       encoding="utf-8")
    acc = load(key, "accepted", {})
    add_variants(acc, bullets(p), f"{fit['company']} – {fit['role']}", load(key, "rounds", []))
    with db() as con:
        if pending:
            con.execute("update applications set status=?, method=?, note=?, date_applied=? where id=?",
                        (status, method, note, when, pending["id"]))
            app_id = pending["id"]
        else:
            app_id = con.execute("insert into applications(job_key, company, role, date_applied, status, method, folder, note, data)"
                                 " values (?,?,?,?,?,?,?,?,?)",
                                 (key, fit["company"], fit["role"], when, status, method, folder.name, note,
                                  json.dumps({"score": fit["score"], "country": fit["country"], "url": job.get("url", ""),
                                              # how many had applied when this one went (LinkedIn postings), for the drawer
                                              "applicants_when_applied": json.loads(job.get("data") or "{}").get("applicants")}))).lastrowid
        con.execute("update jobs set status='applied' where key=?", (key,))
        if status != "pending":  # sent from the Apply page: off Needs you too
            con.execute("update needs set done=1 where job_key=?", (key,))
    shutil.rmtree(work(key), ignore_errors=True)
    return {"id": app_id, "folder": folder.name}


def learn(key: str):
    """Corrections the person made (edits, rejections, revise comments) become lessons for the next application."""
    rounds = load(key, "rounds", [])
    prop = load(key, "proposal") or {}
    meta = {i["id"]: i for i in prop.get("items", []) + prop.get("letter", [])}
    corr = [f"{d['id']}: proposed {meta.get(d['id'], {}).get('new', '')!r} → {d['decision']} {d.get('text', '')!r} {d.get('comment', '')}"
            for r in rounds for d in r if d["decision"] in ("edit", "reject", "revise") and (d.get("comment") or d["decision"] == "edit")]
    if not corr:
        return
    try:
        out = ai.answer(prompts.build("lessons", lessons=pf.read("lessons") or "(none)", corrections="\n".join(corr)), Lessons, agent(), key=key)
        pf.write("lessons", out.markdown)
    except ai.AIError:
        pass  # lessons are a bonus; never block archiving
