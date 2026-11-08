"""What a CV must be, from the blind test of 2026-11-06: two pages used, the Profile written for this job."""
from io import BytesIO

from pypdf import PdfReader

from jobgambit.core import ai, apply, check, render
from jobgambit.core import profile as pf


def line(i, n=26):
    return " ".join(f"{i}word{w}" for w in range(n))


def profile(jobs=14, courses=2):
    """Enough text to reach page 2, with a course that has lines of its own."""
    return {"basics": {"name": "Sam Sample"}, "summaries": {"planning": "Motion planning profile.", "ai": "AI profile."},
            "skills": {"Tools": ["Python"]}, "education": [], "languages": [], "projects": [], "volunteering": [], "custom": [],
            "experience": [{"id": f"e{i}", "org": f"Org{i}", "title": "Engineer", "bullets": [
                {"id": f"e{i}_b{b}", "text": line(f"e{i}b{b}")} for b in range(3)]} for i in range(jobs)],
            "courses": [{"id": "c1", "title": "Self-Driving Cars", "bullets": [
                {"id": f"c1_b{b}", "text": line(f"courseline{b}", 14)} for b in range(courses)]}]}


def setup(monkeypatch, p, accepted, rounds=()):
    pf.write("profile", p)
    monkeypatch.setattr(apply.posting, "get", lambda key: {"type": "full_time", "title": "T", "description": "Python"})
    monkeypatch.setattr(apply.pf, "profile", lambda: type("P", (), {"model_dump": lambda self: p})())
    apply.dump("k", "fit", {"company": "Acme", "country": "DE"})
    apply.dump("k", "letter", {"subject": "S", "paragraphs": ["One."]})
    apply.dump("k", "accepted", accepted)
    apply.dump("k", "rounds", list(rounds))


def cv_pdf():
    return next(p for p in apply.workdir("k").glob("*_CV_*.pdf"))


def text():
    return " ".join((p.extract_text() or "") for p in PdfReader(cv_pdf()).pages)


def removed(*ids):
    return {i: {"text": ""} for i in ids}


def by_auto(*ids):
    return [[{"id": i, "decision": "accept", "text": "", "auto": True} for i in ids]]


def test_the_word_target_is_what_two_full_pages_hold():
    """67 real CVs filling page 2 to 90% or more had 940 to 1,102 words at font size 11. 638 emptied the pages."""
    pages, words = apply.cv_budget(apply.prefs(), "DE")
    assert pages == 2 and 900 <= words <= 1150


def test_a_flagged_profile_stays_the_one_written_for_this_job():
    """One flagged term used to throw the whole Profile out, and the CV printed the first one in the master CV."""
    made = apply.auto_decisions([dict(id="summary", old="Motion planning profile.", new="**AI Engineer** with LLM agents.", flag=True)])
    assert made[0]["decision"] == "accept" and made[0]["text"] == "**AI Engineer** with LLM agents."
    kept = apply.auto_decisions([dict(id="b1", old="Built", new="Led", flag=True)])
    assert kept[0]["decision"] == "reject" and kept[0]["text"] == "Built"  # a flagged line still keeps its master wording


def test_removed_lines_come_back_courses_first_while_page_two_has_room(monkeypatch):
    p = profile(jobs=5)
    setup(monkeypatch, p, removed("c1_b0", "c1_b1", "e4_b2"), by_auto("c1_b0", "c1_b1", "e4_b2"))
    built = apply.build("k")
    assert "courseline0word0" in text() and "courseline1word0" in text()
    acc = apply.load("k", "accepted")
    assert "c1_b0" not in acc and "c1_b1" not in acc  # saved, so Change text shows what the PDF shows
    assert built["checks"]["cv"]["pages"] <= 2


def test_a_line_only_comes_back_while_the_pages_hold_it(monkeypatch):
    p = profile(jobs=22)
    ids = [f"e{i}_b{b}" for i in range(22) for b in range(3)]
    setup(monkeypatch, p, removed(*ids[6:]), by_auto(*ids[6:]))
    built = apply.build("k")
    assert built["checks"]["cv"]["pages"] == 2
    assert len(apply.load("k", "accepted")) > 0  # some stay out: the CV never grows past its pages


def test_a_full_last_page_brings_nothing_back(monkeypatch):
    p = profile(jobs=9, courses=2)
    setup(monkeypatch, p, removed("c1_b0", "c1_b1"), by_auto("c1_b0", "c1_b1"))
    pdf = render.cv_pdf(apply.cv_data(p, apply.load("k", "accepted")), "classic", "en", "DE")
    assert check.filled(BytesIO(pdf), -1) >= apply.CV_FILL, "fixture: page 2 must already be full"
    apply.build("k")
    assert "c1_b0" in apply.load("k", "accepted")


def test_a_line_the_person_removed_stays_removed(monkeypatch):
    p = profile(jobs=5)
    setup(monkeypatch, p, removed("c1_b0", "c1_b1"), [[{"id": "c1_b0", "decision": "accept", "text": ""},
                                                       {"id": "c1_b1", "decision": "edit", "text": "", "auto": True, "asked": True}]])
    apply.build("k")
    assert "courseline0word0" not in text() and "courseline1word0" not in text()


def test_a_half_empty_last_page_is_reported_and_warned(monkeypatch):
    setup(monkeypatch, profile(jobs=5, courses=0), {})
    cv = apply.build("k")["checks"]["cv"]
    assert cv["pages"] == 2 and cv["fill"] < apply.CV_FILL and any("page 2 is only" in w for w in cv["warns"])


def test_a_single_page_cv_is_no_problem(monkeypatch):
    setup(monkeypatch, profile(jobs=1, courses=0), {})
    cv = apply.build("k")["checks"]["cv"]
    assert cv["pages"] == 1 and not any("full" in w for w in cv["warns"])


def test_filled_measures_any_page():
    pdf = render.cv_pdf(apply.cv_data(profile(jobs=5), {}), "classic", "en", "DE")
    assert len(PdfReader(BytesIO(pdf)).pages) == 2
    assert check.filled(BytesIO(pdf), -1) < check.filled(BytesIO(pdf), 0)


def repair(monkeypatch, checks):
    p = profile(jobs=1)
    monkeypatch.setattr(apply.pf, "profile", lambda: type("P", (), {"model_dump": lambda self: p})())
    monkeypatch.setattr(apply.posting, "get", lambda key: {"description": "testing", "country": "DE", "type": "full_time"})
    monkeypatch.setattr(apply, "agent", lambda: None)
    monkeypatch.setattr(apply, "build", lambda k: {"stage": "ready"})
    apply.dump("k", "fit", {"country": "DE"})
    apply.dump("k", "letter", {"subject": "S", "paragraphs": ["Letter."]})
    apply.set_stage("k", "ready", language="en", checks={"cv": checks})
    seen = []

    def answer(prompt, model, agent, key=""):
        seen.append(prompt)
        return model(edits=[], language="en")
    monkeypatch.setattr(ai, "answer", answer)
    apply.change("k", "")
    return seen[0]


def test_a_cv_over_its_pages_is_cut_by_what_runs_over(monkeypatch):
    prompt = repair(monkeypatch, {"fails": ["more than 2 page(s): 3"], "pages": 3, "max_pages": 2, "over_words": 120})
    assert "about 120 words" in prompt and "target at most" not in prompt


def test_a_fix_for_another_reason_never_shortens_the_cv(monkeypatch):
    prompt = repair(monkeypatch, {"fails": ["numbers not in your profile: 42"], "pages": 2, "max_pages": 2, "over_words": 0})
    assert "target at most" not in prompt and "words over" not in prompt


def test_a_cv_over_its_pages_reports_the_words_that_run_over(monkeypatch):
    p = profile(jobs=30)
    setup(monkeypatch, p, {})
    cv = apply.build("k")["checks"]["cv"]
    assert cv["pages"] > 2 and cv["over_words"] > 0
