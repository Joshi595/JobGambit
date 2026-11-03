"""Setup wizard and profile: import a CV, edit the profile, preferences, form answers and writing style."""
import json
import shutil
from pathlib import Path

from babel import Locale
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from jobgambit.core import ai, alerts, apply, cvimport, mail, prompts, render, score, secrets, skillmap
from jobgambit.core import profile as pf

router = APIRouter(prefix="/api")
EXAMPLES = Path(__file__).parent.parent / "examples"
NOT_COUNTRIES = {"EU", "EZ", "UN", "QO", "XA", "XB", "ZZ"}
SIGN_IN = {"claude": ["claude"], "codex": ["codex", "login"], "antigravity": ["agy"]}
MAX_UPLOAD = 15 * 1024 * 1024  # a CV file is rarely over 2 MB
SCAN = ("\n\nThis CV has no text in it (a scan or a photo): it is the file {name} in the current folder. Read that file and "
        "take every fact from it.")


def agent():
    return ai.chosen(pf.read("preferences").get("agent", ""))


async def run_ai(prompt, model, timeout=600, files=None):
    """The AI call runs in a worker thread so the app stays responsive. files: {name: bytes} for the AI to read itself
    (a scanned CV), its prompt then names them."""
    if files:
        prompt += "".join(SCAN.format(name=n) for n in files)
    try:
        return (await run_in_threadpool(ai.answer, prompt, model, agent(), timeout, files=files)).model_dump()
    except ai.AIError as e:
        raise HTTPException(502, str(e))


async def upload(request: Request) -> bytes:
    """An uploaded file's bytes, at most MAX_UPLOAD."""
    big = HTTPException(413, "This file is over 15 MB. Save a smaller copy (PDF or Word), or paste the text of your CV below.")
    if int(request.headers.get("content-length") or 0) > MAX_UPLOAD:
        raise big
    data = await request.body()
    if len(data) > MAX_UPLOAD:
        raise big
    return data


async def cv_file(data: bytes) -> tuple[str, dict]:
    """A CV file -> (its text, {}), or for a scan or photo the chosen AI can read itself: ("", {file name: bytes}).
    Anything else: a 422 that says what to do instead (the page opens its paste box)."""
    try:
        text = await run_in_threadpool(cvimport.cv_text, data)
    except cvimport.Unreadable as e:
        raise HTTPException(422, str(e))
    if cvimport.enough(text):
        return text, {}
    ext = cvimport.kind(data)
    if ext not in cvimport.PICTURES | {".pdf"}:
        raise HTTPException(422, "There is too little text in this file for a CV. Try the PDF or Word version." + cvimport.PASTE)
    try:
        a = agent()
    except ai.AIError as e:
        raise HTTPException(502, str(e))
    if ext not in a.reads:
        raise HTTPException(422, f"This file is a scan or a photo, with no text in it, and {a.label} can't read those here "
                                 "(Claude can). Upload a PDF or Word file with text instead." + cvimport.PASTE)
    return "", {"cv" + ext: data}


NOTHING = "No name, job or education could be read from this file. Try the PDF or Word version of your CV." + cvimport.PASTE


def found(profile: dict, nothing: str = NOTHING) -> dict:
    """An import with no name, no job and no education in it read nothing usable (garbled text, the wrong file)."""
    if not (profile["basics"]["name"].strip() or profile["experience"] or profile["education"]):
        raise HTTPException(422, nothing)
    return profile


@router.get("/state")
def state():
    prefs = pf.Preferences.model_validate(pf.read("preferences")).model_dump()
    countries = {k: v for k, v in Locale("en").territories.items() if len(k) == 2 and k.isalpha() and k not in NOT_COUNTRIES}
    return {"profile": pf.profile().model_dump(), "preferences": prefs, "answers": pf.read("answers"),
            "style": pf.read("style"), "lessons": pf.read("lessons"), "letter": pf.read("letter"), "cv_rules": pf.read("cv_rules"), "default_cv_rules": prompts.load("default_cv_rules"), "default_style": prompts.load("default_style"), "agents": ai.installed(), "tested": ai.tested(),
            "countries": dict(sorted(countries.items(), key=lambda kv: kv[1])), "bands": score.BANDS,
            "examples": sorted(p.name for p in EXAMPLES.iterdir() if p.is_dir()), "features": features()}


def features() -> dict:
    out = {}
    return out


@router.put("/data/{kind}")
async def save(kind: str, request: Request):
    if kind not in {*pf.FILES, *pf.TEXTS}:
        raise HTTPException(404)
    body = await request.json()
    try:
        pf.write(kind, body["text"] if kind in pf.TEXTS else body)
    except ValueError as e:  # pydantic validation
        raise HTTPException(422, str(e))
    if kind in ("profile", "preferences"):  # what the thumbnails show (preferences: the target country)
        render.warm_thumbs()
    if kind == "profile" and pf.read("preferences").get("setup_done"):  # what the CV has changes every fit score
        from jobgambit.app import settings_api
        settings_api.refresh_later(False)
    return {"ok": True}


@router.get("/skillmap")
def skill_map():
    """Profile › Your CV: the skills JobGambit matches postings on, and which of them the person has."""
    return {"concepts": skillmap.shown(), "made": skillmap.read().get("made", ""), "error": skillmap.STATE["error"]}


@router.put("/skillmap/{cid}")
async def flip_skill(cid: str, request: Request):
    """{have: 1 | 0 | null}: the person's own say on one skill (null: as the CV shows)."""
    value = (await request.json()).get("have")
    if not skillmap.flip(cid, None if value is None else float(bool(value))):
        raise HTTPException(404, "That skill isn't in the map any more. Reload the page.")
    from jobgambit.app import settings_api
    settings_api.refresh_later(False)
    return {"ok": True}


@router.post("/skillmap/rebuild")
async def rebuild_skill_map():
    """The map made again now (one AI run, economy level), merged with what's there."""
    await run_in_threadpool(skillmap.ensure, None, None, True)
    if skillmap.STATE["error"]:
        raise HTTPException(502, skillmap.STATE["error"])
    from jobgambit.app import settings_api
    settings_api.refresh_later(False)
    return skill_map()


@router.get("/answers/suggested")
def suggested_answers():
    """Answers that follow from the CV and preferences, for questions not answered yet. Shown, never saved by themselves."""
    prefs = pf.Preferences.model_validate(pf.read("preferences")).model_dump()
    return pf.suggested_answers(pf.read("profile"), prefs, pf.read("answers"))


@router.post("/instructions")
async def add_instruction(request: Request):
    """One line added to the CV instructions (cv_rules) or the letter instructions (style); in the letter instructions
    above the banned-phrases list."""
    b = await request.json()
    kind, line = b.get("kind"), " ".join(str(b.get("line") or "").split())
    if kind not in ("cv_rules", "style") or not line:
        raise HTTPException(422, "Write the instruction first.")
    text = pf.read(kind) or (prompts.load("default_cv_rules") if kind == "cv_rules" else prompts.load("default_style"))
    banned = pf.banned_section(text)
    pf.write(kind, text[:len(text) - len(banned)].rstrip() + f"\n- {line}\n" + (f"\n{banned}" if banned else ""))
    return {"ok": True}


@router.patch("/answers")
async def patch_answers(request: Request):
    """{question: answer} merged into the saved form answers; an empty answer removes the question. Merged, not
    replaced, so an answer saved meanwhile stays."""
    answers = pf.read("answers") or {}
    for k, v in (await request.json()).items():
        if str(v).strip():
            answers[k] = v
        else:
            answers.pop(k, None)
    pf.write("answers", answers)
    return {"ok": True}


@router.post("/import")
async def import_file(request: Request, name: str):
    """Raw file body. A ZIP = LinkedIn data export, .json = JSON Resume (both mapped without AI), anything else = a CV
    file for the AI."""
    data = await upload(request)
    linkedin, resume = cvimport.kind(data) == ".zip", name.lower().endswith(".json")
    if linkedin or resume:
        try:
            draft = await run_in_threadpool(cvimport.from_linkedin if linkedin else cvimport.from_json_resume, data)
            draft = pf.Profile.model_validate(draft).model_dump()
        except cvimport.Unreadable as e:
            raise HTTPException(422, str(e))
        except (ValueError, AttributeError, TypeError, KeyError):  # broken JSON, or parts of the wrong kind
            raise HTTPException(422, str(cvimport.Unreadable("linkedin" if linkedin else "json")))
        return {"profile": found(draft)}
    text, files = await cv_file(data)
    return {"profile": found(await run_ai(prompts.build("import_cv", cv=text), pf.Profile, files=files))}


@router.post("/import-text")
async def import_text(request: Request):
    text = (await request.json()).get("text", "")
    if not cvimport.enough(text):
        raise HTTPException(422, "That is too short for a CV. Paste the whole text.")
    return {"profile": found(await run_ai(prompts.build("import_cv", cv=text), pf.Profile),
                             "No name, job or education could be read from this text. Paste your whole CV, with your name and jobs.")}


class Gap(pf.Model):
    id: str
    question: str
    about: str


class Gaps(pf.Model):
    questions: list[Gap]


class Merged(pf.Model):
    profile: pf.Profile
    changes: list[str]


@router.get("/profile/asked")
def asked():
    """Skills postings asked for most that the CV lacks (no AI)."""
    from jobgambit.core import check
    return check.asked_missing()


@router.post("/profile/gaps")
async def gaps(request: Request):
    """Up to 5 optional questions whose answers would make the CV stronger."""
    body = await request.json()
    profile = body.get("profile") or pf.profile().model_dump()
    return await run_ai(prompts.build("gaps", profile=json.dumps(profile, ensure_ascii=False)[:20000]), Gaps)


async def merge(kind: str, info: str, profile: dict | None, files=None) -> dict:
    old = profile or pf.profile().model_dump()
    out = await run_ai(prompts.build("merge", profile=json.dumps(old, ensure_ascii=False), kind=kind, info=info), Merged, 900, files)
    new = pf.keep_existing(old, out["profile"])
    return {"profile": pf.Profile.model_validate(new).model_dump(), "changes": out["changes"]}


@router.post("/profile/merge")
async def merge_text(request: Request):
    """New information in the person's words (answers to the gap questions, or "what's new") -> an updated draft, not saved."""
    b = await request.json()
    if not (b.get("text") or "").strip():
        raise HTTPException(422, "Nothing to add.")
    return await merge(b.get("kind", "what's new"), b["text"], b.get("profile"))


@router.post("/profile/merge-file")
async def merge_file(request: Request):
    """A newer CV file -> an updated draft of the existing profile (nothing already there is lost)."""
    text, files = await cv_file(await upload(request))
    return await merge("a newer CV", text, None, files)


@router.post("/example/{name}")
def load_example(name: str):
    src = EXAMPLES / Path(name).name
    if not src.is_dir():
        raise HTTPException(404)
    if (pf.folder() / pf.FILES["profile"]).exists():  # an example is for trying the app, never over someone's own CV
        raise HTTPException(409, "You already have a CV here. An example would replace it, so it wasn't loaded.")
    for f in src.iterdir():
        shutil.copy(f, pf.folder() / f.name)
    render.warm_thumbs()
    return {"ok": True}


@router.post("/suggest/targets")
async def suggest_targets(request: Request):
    body = await request.json()
    p = pf.profile()
    summary = json.dumps({"headline": p.basics.headline, "roles": [e.title for e in p.experience],
                          "education": [e.degree for e in p.education], "skills": p.skills,
                          "languages": [l.model_dump() for l in p.languages]}, ensure_ascii=False)
    return await run_ai(prompts.build("targets", profile=summary, countries=", ".join(body.get("countries", [])),
                                job_types=", ".join(body.get("job_types", [])), wishes=body.get("wishes", "")),
                  pf.Targets)


@router.post("/suggest/style")
async def suggest_style(request: Request):
    body = await request.json()
    return await run_ai(prompts.build("style", answers=json.dumps(body.get("answers", {}), ensure_ascii=False),
                                sample=body.get("sample", "")), pf.Style)


class Opening(pf.Model):
    style: str
    text: str


class Openings(pf.Model):
    openings: list[Opening]


@router.post("/suggest/openings")
async def suggest_openings(request: Request):
    """Three cover-letter openings in clearly different styles, built from the CV, so a person with no letters of their
    own can pick the voice they like."""
    body = await request.json()
    p = pf.profile()
    facts = json.dumps({"headline": p.basics.headline, "summaries": p.summaries,
                        "experience": [{"title": e.title, "org": e.org, "lines": [b.text for b in e.bullets[:3]]} for e in p.experience[:4]],
                        "education": [e.degree for e in p.education], "skills": p.skills}, ensure_ascii=False)[:12000]
    return await run_ai(prompts.build("openings", profile=facts, tone=body.get("tone", ""), length=body.get("length", "")), Openings)


@router.post("/extract-text")
async def extract_text(request: Request):
    """A letter the person wrote (PDF, Word, text) -> its text, as a writing sample."""
    data = await upload(request)
    try:
        text = await run_in_threadpool(cvimport.cv_text, data)
    except cvimport.Unreadable as e:
        raise HTTPException(422, str(e).replace(cvimport.PASTE, " Or paste the text."))
    if not text.strip():
        raise HTTPException(422, "No readable text in this file. Try the Word version, or paste the text.")
    return {"text": text}


@router.get("/setup/missing")
def setup_missing():
    """What's still not set up, from the actual state (so it works for installs that finished setup long ago),
    minus what the person dismissed. In the order setup asks."""
    prefs = pf.Preferences.model_validate(pf.read("preferences")).model_dump()
    style = pf.read("style").strip()
    done = {"details": bool(pf.read("answers")),
            "style": bool(style) and style != prompts.load("default_style").strip() and bool(pf.read("cv_rules").strip()),
            "design": prefs["design"] != "classic",
            "email": mail.configured(),
            "alerts": bool(alerts.urls()) or (prefs["alert_email"] and mail.configured()),
            "search": bool(prefs["search_time"]),
            "documents": bool(apply.own_documents())}
    return {"missing": [k for k, ok in done.items() if not ok and k not in prefs["setup_hidden"]]}


@router.get("/setup/ready")
def setup_ready():
    """The minimum to apply that's still missing (the Apply page lists it first; also for installs set up long ago)."""
    b, prefs = pf.profile().basics, pf.Preferences.model_validate(pf.read("preferences"))
    done = {"basics": bool(b.name and b.email), "targets": bool(prefs.targets), "start": bool(prefs.start.strip()),
            "permit": all(c.permit.strip() for c in prefs.targets), "ai": any(ai.installed().values()),
            "writing": bool(pf.read("cv_rules").strip() and pf.read("style").strip()),
            "documents": bool(apply.own_documents()) or "documents" in prefs.setup_hidden}  # or "I'll add them later"
    return {"missing": [k for k, ok in done.items() if not ok]}


@router.get("/export/json-resume")
def export_json_resume():
    return cvimport.to_json_resume(pf.profile().model_dump())


@router.post("/ai/sign-in/{name}")
def sign_in(name: str):
    a = ai.AGENTS.get(name)
    if not a or not a.path():
        raise HTTPException(404, "not installed")
    from jobgambit.core.system import open_terminal
    cmd = [a.path(), *SIGN_IN[name][1:]]
    try:
        open_terminal(cmd)
    except OSError as e:  # no terminal app found (a minimal Linux desktop)
        raise HTTPException(500, f"Couldn't open a terminal window ({e}). Open one yourself and run: {' '.join(cmd)}")
    return {"ok": True}
