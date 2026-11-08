import io

import pytest
from pypdf import PdfReader
from ruamel.yaml import YAML

from jobgambit.core import render, secrets

CV = YAML(typ="safe").load(open("jobgambit/examples/sam-nurse-uk/master_cv.yaml", encoding="utf-8"))


def text(pdf):
    return "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBGAMBIT_HOME", str(tmp_path))
    return tmp_path


def test_thirteen_designs_render():
    assert len(render.designs()) >= 13
    for d in render.designs():
        assert "Sam Sample" in text(render.cv_pdf(CV, d, "en", "GB")), d


def test_designer_settings():
    s = {"hidden": ["volunteering"], "titles": {"experience": "Work history"}, "max_items": {"experience": 1},
         "max_bullets": 1, "order": ["experience", "summary", "education", "skills"], "style": {"accent": "#ff0000"}}
    out = text(render.cv_pdf(CV, "classic", "en", "GB", settings=s))
    assert "Work history" in out and "First aid volunteer" not in out and "Care of the Elderly" not in out
    assert "student nurses per year" not in out  # only the first line of the job is kept
    assert out.index("Work history") < out.index("Registered adult nurse")


def test_presets(home):
    render.save_preset("Research look", "academic", {"hidden": ["interests"]})
    assert render.resolve("Research look") == ("academic", {"hidden": ["interests"]})
    assert render.resolve("classic") == ("classic", {}) and render.resolve("nonsense") == ("classic", {})


def test_secrets_round_trip(monkeypatch):
    store = {}
    import keyring
    monkeypatch.setattr(keyring, "get_password", lambda s, n: store.get((s, n)))
    monkeypatch.setattr(keyring, "set_password", lambda s, n, v: store.__setitem__((s, n), v))
    monkeypatch.setattr(keyring, "delete_password", lambda s, n: store.pop((s, n)))
    secrets.put("portal:example.com", "Xq9-secret-7731")
    assert secrets.get("portal:example.com") == "Xq9-secret-7731" and secrets.names() == ["portal:example.com"]
    assert secrets.redact("pw=Xq9-secret-7731") == "pw=•••"
    secrets.delete_all()
    assert secrets.get("portal:example.com") == "" and store == {}


def long_cv():
    cv = {**CV, "experience": [{**e, "title": f"{e['title']} {n}"} for n in range(6) for e in CV["experience"]]}
    return cv


def test_every_page_as_svg():
    pages = render.cv_pages(long_cv(), "classic", "en", "GB")
    assert len(pages) >= 2 and all(b"<svg" in p[:300] for p in pages)


WORDS = ("alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau upsilon "
         "phi chi psi omega ") * 3


def text_lines(svg: bytes) -> list[tuple[float, float]]:
    """(first glyph x, last glyph x) of every text run with 30+ glyphs, in page coordinates."""
    import re
    stack, out = [], []
    for m in re.finditer(rb'<g transform="(translate|matrix)\(([^)]*)\)">|</g>|<use [^>]*?x="([\d.-]+)"', svg):
        if m[1]:
            n = [float(v) for v in m[2].split()]
            stack.append({"x": n[-2] if m[1] == b"matrix" else n[0], "xs": []})
        elif m[3]:
            stack[-1]["xs"].append(sum(g["x"] for g in stack) + float(m[3]))
        elif stack:
            xs = stack.pop()["xs"]
            if len(xs) >= 30:
                out.append((xs[0], xs[-1]))
    return out


def spread(values):
    return max(values) - min(values)


def body(align, bullet=False):
    cv = {"basics": {"name": "Sam Sample"}, "summary": "" if bullet else WORDS.strip(), "projects": [], "education": [],
          "skills": {}, "languages": [], "volunteering": [], "courses": [], "custom": [],
          "experience": [{"id": "e", "org": "Org", "title": "Role", "bullets": [{"id": "b", "text": WORDS.strip()}]}] if bullet else []}
    lines = text_lines(render.cv_pages(cv, "classic", "en", "GB", settings={"style": {"align": align}})[0])
    assert len(lines) >= 3, lines
    return lines


def test_text_alignment_like_a_word_processor():
    """Left, centre, right or justified, for the Profile and the lines under each job. Headings and dates keep the design's."""
    assert render.STYLE["align"] == "left"
    left, right, centre, justify = (body(a) for a in ("left", "right", "center", "justify"))
    assert spread([s for s, _ in left]) < 0.5 and spread([e for _, e in left[:-1]]) > 12  # ragged right edge
    assert spread([s for s, _ in right]) > 12 and spread([e for _, e in right[:-1]]) < 9
    assert spread([s for s, _ in centre]) > 6 and spread([(s + e) / 2 for s, e in centre[:-1]]) < 5
    assert spread([s for s, _ in justify]) < 0.5 and spread([e for _, e in justify[:-1]]) < 9  # both edges straight
    bullets = body("justify", bullet=True)
    assert spread([e for _, e in bullets[:-1]]) < 9 < spread([e for _, e in body("left", bullet=True)[:-1]])


def test_the_letter_follows_the_alignment():
    def starts(align):
        pdf = render.letter_pdf({"subject": "S", "paragraphs": [WORDS.strip()]}, {"name": "Sam Sample"}, "classic", "en", "GB",
                                settings={"style": {"align": align}})
        xs = []
        PdfReader(io.BytesIO(pdf)).pages[0].extract_text(visitor_text=lambda t, cm, tm, f, s: xs.append(
            round(tm[4] * cm[0] + tm[5] * cm[2] + cm[4], 1)) if "alpha" in t or "psi" in t or "phi" in t or "tau" in t else None)
        return xs
    assert spread(starts("left")) < 0.5 and spread(starts("right")) > 12


def test_thumb_key_follows_what_it_shows(home):
    import os
    from jobgambit.core import profile as pf
    pf.write("preferences", {"targets": [{"code": "GB"}]})
    k = render.thumb_key("classic")
    pf.write("preferences", {"targets": [{"code": "DE"}]})
    assert render.thumb_key("classic") != k
    k = render.thumb_key("classic")
    (pf.folder() / "photo.png").write_bytes(b"x")
    assert render.thumb_key("classic") != k
    k = render.thumb_key("classic")
    yaml = render.TPL / "designs" / "classic.yaml"
    st = yaml.stat()
    try:
        os.utime(yaml, (st.st_atime, st.st_mtime + 5))
        assert render.thumb_key("classic") != k and render.thumb_key("modern") == render.thumb_key("modern")
    finally:
        os.utime(yaml, (st.st_atime, st.st_mtime))
    render.save_preset("Mine", "classic", {"style": {"accent": "#ff0000"}})
    assert render.thumb_key("Mine") != render.thumb_key("classic")


def test_thumb_and_preview_api(home):
    import shutil
    from fastapi.testclient import TestClient
    from jobgambit.app import server
    from jobgambit.core import profile as pf
    shutil.copy("jobgambit/examples/sam-nurse-uk/master_cv.yaml", pf.folder() / "master_cv.yaml")
    c = TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})
    key = c.get("/api/designs").json()["keys"]["classic"]
    r = c.get(f"/api/designs/classic/thumb.png?v={key}")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert r.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert [p.name for p in (home / "cache").iterdir()] == [f"thumb-classic-{key}.png"]
    assert c.get("/api/designs/nonsense/thumb.png").status_code == 404
    r = c.post("/api/designs/preview", json={"design": "classic", "settings": {"pages": 3}}).json()
    assert r["limit"] == 3 and len(r["pages"]) >= 1 and "<svg" in r["pages"][0][:300]
