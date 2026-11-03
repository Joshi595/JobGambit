import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from jobgambit.core import cvimport
from jobgambit.core import profile as pf


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBGAMBIT_HOME", str(tmp_path))
    return tmp_path


def test_save_keeps_comments_and_validates(home):
    (pf.folder() / "master_cv.yaml").write_text(
        "basics:\n  name: Ana  # from passport\nexperience:\n  - id: e1  # first job\n    title: Nurse\n    bullets: []\n",
        encoding="utf-8")
    p = pf.read("profile")
    p["experience"][0]["title"] = "Senior Nurse"
    pf.write("profile", p)
    text = (pf.folder() / "master_cv.yaml").read_text(encoding="utf-8")
    assert "# from passport" in text and "# first job" in text and "Senior Nurse" in text
    assert pf.profile().experience[0].title == "Senior Nurse"
    with pytest.raises(ValueError):
        pf.write("profile", {"basics": {"name": "x"}, "nonsense": 1})


def test_parallel_reads_dont_mix_files(home):
    """The app serves requests in parallel; a shared YAML reader once returned preferences as the profile."""
    from concurrent.futures import ThreadPoolExecutor
    pf.write("profile", {"basics": {"name": "Ana"}})
    pf.write("preferences", {"home_country": "ES"})
    kinds = ["profile", "preferences"] * 40
    with ThreadPoolExecutor(16) as ex:
        out = list(ex.map(pf.read, kinds))
    assert all(("basics" in o) == (k == "profile") for k, o in zip(kinds, out))


def test_linkedin_export():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Profile.csv", "First Name,Last Name,Headline,Summary,Geo Location\nAna,Lopez,Nurse,Caring nurse,\"Madrid, Spain\"\n")
        z.writestr("Positions.csv", "Company Name,Title,Description,Location,Started On,Finished On\n"
                                    "Hospital X,Staff Nurse,\"Cared for 8 patients\nMentored students\",Madrid,Oct 2020,\n")
        z.writestr("Skills.csv", "Name\nTriage\nIV therapy\n")
    p = pf.Profile.model_validate(cvimport.from_linkedin(buf.getvalue()))
    e = p.experience[0]
    assert p.basics.name == "Ana Lopez" and e.start == "2020-10" and e.end == "present"
    assert [b.text for b in e.bullets] == ["Cared for 8 patients", "Mentored students"]
    assert p.skills == {"Skills": ["Triage", "IV therapy"]}


def test_json_resume_round_trip():
    src = {"basics": {"name": "Bo", "email": "b@x.org", "profiles": [{"network": "GitHub", "url": "github.com/bo"}]},
           "work": [{"name": "Acme", "position": "Dev", "startDate": "2021-02-01", "highlights": ["Built **X**"]}],
           "education": [{"institution": "TU", "startDate": 2016, "endDate": 2020}],  # bare years as numbers
           "skills": [{"name": "Web", "keywords": ["JS"]}]}
    p = pf.Profile.model_validate(cvimport.from_json_resume(json.dumps(src).encode()))
    assert p.experience[0].start == "2021-02" and p.basics.links == {"github": "github.com/bo"} and p.education[0].end == "2020"
    back = cvimport.to_json_resume(p.model_dump())
    assert back["work"][0]["highlights"] == ["Built X"] and back["skills"] == [{"name": "Web", "keywords": ["JS"]}]


def test_setup_api(home):
    from jobgambit.app import server
    c = TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})
    st = c.get("/api/state").json()
    assert st["preferences"]["setup_done"] is False and st["countries"]["DE"] == "Germany"
    assert "alex-engineer-de" in st["examples"]
    assert c.post("/api/example/alex-engineer-de").status_code == 200
    st = c.get("/api/state").json()
    assert st["profile"]["basics"]["name"] == "Alex Example" and st["preferences"]["setup_done"] is True
    assert c.put("/api/data/style", json={"text": "# Style"}).status_code == 200
    assert c.put("/api/data/preferences", json={"bogus": 1}).status_code == 422
    assert c.get("/api/export/json-resume").json()["basics"]["name"] == "Alex Example"
    r = c.post("/api/import?name=cv.txt", content=b"too short")
    assert r.status_code == 422
    assert c.get("/api/backup").content[:2] == b"PK"
    assert c.get("/tabs/profile.html").status_code == 200


def test_ai_update_never_drops_existing_content():
    old = {"basics": {"name": "Ana", "email": "a@x.org", "links": {"github": "gh/ana"}},
           "experience": [{"id": "e1", "title": "Nurse", "bullets": [{"id": "e1_b1", "text": "Cared for 8 patients"}]},
                          {"id": "e2", "title": "Carer", "bullets": []}],
           "skills": {"Clinical": ["Triage", "IV"]}, "languages": [{"name": "English", "level": "Native"}]}
    new = {"basics": {"name": "Ana", "email": ""},
           "experience": [{"id": "e1", "title": "Senior Nurse", "bullets": [{"id": "e1_b2", "text": "Leads a team of 6"}]}],
           "skills": {"Clinical": ["Triage"], "Software": ["Epic"]}, "languages": []}
    out = pf.keep_existing(old, new)
    assert [e["id"] for e in out["experience"]] == ["e1", "e2"] and out["experience"][0]["title"] == "Senior Nurse"
    assert [b["id"] for b in out["experience"][0]["bullets"]] == ["e1_b2", "e1_b1"]
    assert out["skills"] == {"Clinical": ["Triage", "IV"], "Software": ["Epic"]}
    assert out["languages"] == [{"name": "English", "level": "Native"}]
    assert out["basics"]["email"] == "a@x.org" and out["basics"]["links"] == {"github": "gh/ana"}


def test_applications_include_form_answers(home):
    from jobgambit.app import server
    from jobgambit.core.data import db
    (home / "applications" / "2026-10-01_Acme_Dev").mkdir(parents=True)
    (home / "applications" / "2026-10-01_Acme_Dev" / "form_answers.md").write_text("# Form answers\n- Salary: 60000\n" + "x" * 5000,
                                                                                    encoding="utf-8")
    with db() as con:
        con.execute("insert into applications(company, role, status, folder, data) values ('Acme', 'Dev', 'applied', '2026-10-01_Acme_Dev', '{}')")
        con.execute("insert into applications(company, role, status, folder, data) values ('Beta', 'QA', 'pending', null, '{}')")
    c = TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})
    rows = {r["company"]: r for r in c.get("/api/applications").json()["rows"]}
    assert rows["Acme"]["form_answers"].startswith("# Form answers\n- Salary: 60000") and len(rows["Acme"]["form_answers"]) == 4096
    assert rows["Beta"]["form_answers"] == "" and rows["Beta"]["files"] == []


def client():
    from jobgambit.app import server
    return TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})


def test_suggested_answers_for_the_example_person_and_saved_answers_win(home):
    c = client()
    assert c.post("/api/example/alex-engineer-de").status_code == 200
    s = c.get("/api/answers/suggested").json()
    assert s["Highest degree"] == "B.Sc. Computer Science"
    assert int(s["Years of experience"]) >= 6
    assert (s["Salary expectation"], s["Earliest start date"], s["Notice period"]) == ("65000-75000 EUR", "in 3 months", "3 months")
    assert s["Authorised to work in Germany?"] == "Yes" and s["Need visa sponsorship for Germany?"] == "No"
    assert s["English level"] == "C1" and s["German level"] == "Native"
    assert "Nationality" not in s and "Current university" not in s  # empty in the CV / no studies in progress: nothing guessed
    pf.write("answers", {"Notice period": "", "Old question": "x"})
    assert c.patch("/api/answers", json={"Highest degree": "Bachelor", "Old question": " "}).status_code == 200
    assert pf.read("answers") == {"Notice period": "", "Highest degree": "Bachelor"}  # merged; an empty answer removes
    s = c.get("/api/answers/suggested").json()
    assert "Highest degree" not in s and s["Notice period"] == "3 months"  # a saved answer wins, an empty one doesn't count


def test_suggested_answers_studies_in_progress():
    p = {"education": [{"degree": "M.Sc. Robotics", "school": "TU Example", "start": "2025", "end": "2099-09"},
                       {"degree": "B.Eng. Mechatronics", "school": "HS Example", "end": "2024"}]}
    prefs = {"targets": [{"code": "DE", "permit": "", "needs_sponsorship": True}, {"code": "AT", "permit": "Not sure"}]}
    s = pf.suggested_answers(p, prefs)
    assert s["Highest degree"] == "B.Eng. Mechatronics"  # the finished one
    assert (s["Current university"], s["Current degree programme"], s["Expected graduation"]) == ("TU Example", "M.Sc. Robotics", "2099-09")
    assert s["Authorised to work in Germany?"] == "No" and s["Need visa sponsorship for Germany?"] == "Yes"
    assert not any("Austria" in k for k in s)


CV = ["Ana Lopez, Madrid, ana@example.org", "Staff Nurse, Hospital X, 2020-2024: cared for 8 patients per shift",
      "Mentored 4 nursing students and led the night team", "B.Sc. Nursing, Universidad Complutense, 2016-2020",
      "Skills: triage, IV therapy, wound care, Spanish (native), English (C1)"]
ANA = {"basics": {"name": "Ana Lopez"}, "experience": [{"id": "exp1", "title": "Staff Nurse"}]}


def pdf(*pages) -> bytes:
    """A real PDF, one page per list of lines; a page with no lines has no text, like a scan."""
    objs = [b"<</Type/Catalog/Pages 2 0 R>>",
            b"<</Type/Pages/Kids[%s]/Count %d>>" % (b" ".join(b"%d 0 R" % (4 + 2 * i) for i in range(len(pages))), len(pages)),
            b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    for i, lines in enumerate(pages):
        content = ("BT /F1 11 Tf 14 TL 72 760 Td " + " ".join(f"({line}) Tj T*" for line in lines) + " ET").encode()
        objs += [b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Resources<</Font<</F1 3 0 R>>>>/Contents %d 0 R>>" % (5 + 2 * i),
                 b"<</Length %d>>stream\n%s\nendstream" % (len(content), content)]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (n, obj)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return bytes(out + b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))


def locked(data: bytes, **how) -> bytes:
    from pypdf import PdfReader, PdfWriter
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    w.encrypt(**how)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def docx(body: str, header: str = "", ns: str = "http://schemas.openxmlformats.org/wordprocessingml/2006/main") -> bytes:
    buf = io.BytesIO()
    mc = "http://schemas.openxmlformats.org/markup-compatibility/2006"
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="{ns}" xmlns:mc="{mc}"><w:body>{body}</w:body></w:document>')
        if header:
            z.writestr("word/header1.xml", f'<w:hdr xmlns:w="{ns}">{header}</w:hdr>')
    return buf.getvalue()


def p(*runs: str) -> str:
    return "<w:p>" + "".join(runs) + "</w:p>"


def test_cv_files_are_read_by_what_they_are(monkeypatch):
    plain = pdf(CV)
    assert cvimport.cv_text(plain).splitlines()[1] == CV[1]
    assert cvimport.cv_text(locked(plain, user_password="", owner_password="o", algorithm="AES-256")) == cvimport.cv_text(plain)
    with pytest.raises(cvimport.Unreadable, match="protected with a password"):
        cvimport.cv_text(locked(plain, user_password="secret", owner_password="o", algorithm="AES-256"))
    assert cvimport.cv_text(pdf([])) == ""  # a scan: no text layer
    from pypdf import PageObject
    real = PageObject.extract_text

    def flaky(page, *a, **k):
        text = real(page, *a, **k)
        if "BROKEN" in text:
            raise ValueError("bad content stream")
        return text
    monkeypatch.setattr(PageObject, "extract_text", flaky)
    assert cvimport.cv_text(pdf(CV[:2], ["BROKEN"], CV[2:])).splitlines() == CV  # one broken page costs only itself
    # Word: the header, tabs and line breaks, a text box once (not its fallback copy), Strict Open XML's namespace
    body = (p('<w:pPr><w:tabs><w:tab w:val="left" w:pos="2000"/></w:tabs></w:pPr>', "<w:r><w:t>2019-2022</w:t><w:tab/><w:t>Software Engineer</w:t></w:r>")
            + p("<w:r><w:t>Musterstrasse 1</w:t><w:br/><w:t>10115 Berlin</w:t></w:r>")
            + p('<w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing><w:txbxContent>' + p("<w:r><w:t>Box text</w:t></w:r>")
                + "</w:txbxContent></w:drawing></mc:Choice><mc:Fallback><w:pict><w:txbxContent>" + p("<w:r><w:t>Box text</w:t></w:r>")
                + "</w:txbxContent></w:pict></mc:Fallback></mc:AlternateContent></w:r>"))
    word = "2019-2022\tSoftware Engineer\nMusterstrasse 1\n10115 Berlin\nBox text"
    assert cvimport.cv_text(docx(body, header=p("<w:r><w:t>Ana Lopez · ana@example.org</w:t></w:r>"))) == "Ana Lopez · ana@example.org\n" + word
    assert cvimport.cv_text(docx(body, ns="http://purl.oclc.org/ooxml/wordprocessingml/main")) == word
    # text in Windows' own encoding, in UTF-16 (Notepad's "Unicode"), with a UTF-8 mark
    assert cvimport.cv_text("Müller, Zürich – Straße".encode("cp1252")) == "Müller, Zürich – Straße"
    assert cvimport.cv_text("Müller 日本".encode("utf-16")) == cvimport.cv_text("﻿Müller 日本".encode()) == "Müller 日本"
    assert cvimport.cv_text(b"\x89PNG\r\n\x1a\n" + bytes(40)) == ""  # a photo: the AI may read it itself
    for data, says in [(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(100), "old Word file"), (b"{\\rtf1\\ansi Ana}", "Rich Text"),
                       (b"", "empty"), (b"\x00\x01\x02 binary", "can't read this kind"), (b"%PDF-1.4 cut short", "damaged")]:
        with pytest.raises(cvimport.Unreadable, match=says):
            cvimport.cv_text(data)
    assert cvimport.enough("我" * 300) and not cvimport.enough("我" * 299)  # no spaces between words: by characters


@pytest.fixture
def fake_ai(home, monkeypatch):
    """The CV import's AI, faked: records the prompt and files it was given, answers with what ["answer"] holds."""
    from jobgambit.core import ai
    seen = {"agent": ai.Claude(), "answer": ANA}
    monkeypatch.setattr(ai, "chosen", lambda *a, **k: seen["agent"])

    def answer(prompt, model, agent, timeout=600, key="", files=None):
        seen.update(prompt=prompt, files=files)
        return model.model_validate(seen["answer"])
    monkeypatch.setattr(ai, "answer", answer)
    return seen


def test_cv_upload_reads_or_says_what_to_do(fake_ai):
    """Every kind of file someone may pick: read, or a 422 that says what to do instead and ends with the paste box
    (the page opens it). Never a bare 500."""
    from jobgambit.core import ai
    c = client()
    up = lambda data, name="cv.pdf": c.post(f"/api/import?name={name}", content=data)
    assert up(pdf(CV)).json()["profile"]["basics"]["name"] == "Ana Lopez" and CV[1] in fake_ai["prompt"] and not fake_ai["files"]
    assert up(locked(pdf(CV), user_password="", owner_password="o", algorithm="AES-256")).status_code == 200
    assert up("Müller, Zürich – Straße\n".encode("cp1252") * 10, "cv.txt").status_code == 200 and "Müller, Zürich" in fake_ai["prompt"]
    assert up(("日本語の履歴書" * 60).encode("utf-16"), "cv.md").status_code == 200 and "日本語" in fake_ai["prompt"]
    scan = pdf([])  # Claude reads a scan itself: the file is put in its run's folder and the prompt names it
    assert up(scan).status_code == 200 and fake_ai["files"] == {"cv.pdf": scan} and "the file cv.pdf" in fake_ai["prompt"]
    photo = b"\x89PNG\r\n\x1a\n" + bytes(40)
    fake_ai["agent"] = ai.Codex()  # Codex takes a photo (with -i), not a scanned PDF
    assert up(photo, "cv.png").status_code == 200 and fake_ai["files"] == {"cv.png": photo}
    assert "can't read those here" in up(scan).json()["detail"]
    fake_ai["agent"] = ai.Antigravity()
    assert "can't read those here" in up(photo, "cv.png").json()["detail"]
    fake_ai["answer"] = {"basics": {"name": ""}}  # garbled text: nothing usable came back
    assert "No name, job or education" in up(pdf(CV)).json()["detail"]
    empty_zip = io.BytesIO()
    zipfile.ZipFile(empty_zip, "w").close()
    for data, name, says in [(locked(pdf(CV), user_password="secret", owner_password="o", algorithm="AES-256"), "cv.pdf", "password"),
                             (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(100), "cv.docx", "old Word file"),
                             (b"", "cv.pdf", "empty"), (empty_zip.getvalue(), "export.zip", "isn't a LinkedIn data export"),
                             (b'{"basics": {"name": "Ana"', "resume.json", "isn't a JSON Resume"),
                             (b'["not", "a", "resume"]', "resume.json", "isn't a JSON Resume"),
                             (b"word " * 5, "cv.txt", "too little text")]:
        r = up(data, name)
        assert r.status_code == 422 and says in r.json()["detail"] and r.json()["detail"].endswith("paste the text of your CV below."), (name, r.text)
    assert up(b"x" * (15 * 1024 * 1024 + 1), "cv.txt").status_code == 413
    fake_ai["answer"] = ANA
    assert c.post("/api/import-text", json={"text": "我" * 300}).status_code == 200  # Chinese: no spaces, still a CV


def test_ai_trouble_on_import_says_what_to_do(home, monkeypatch):
    """A Claude that isn't signed in, an AI that answers in the wrong shape twice: a 502 that says what to do, not a 500."""
    import sys
    from jobgambit.core import ai

    class SignedOut(ai.Claude):  # what Claude Code prints when its login is gone
        def path(self):
            return sys.executable

        def command(self, schema, step="", folder=""):
            line = json.dumps({"type": "result", "is_error": True, "result": "Invalid API key - Please run /login"})
            return ["-c", f"print({line!r}); raise SystemExit(1)"]

    class Shapeless(SignedOut):
        def command(self, schema, step="", folder=""):
            return ["-c", "print('{\"type\": \"result\", \"structured_output\": {\"basics\": 5}}')"]
    text = " ".join(CV)
    for agent, says in [(SignedOut(), "Claude isn't signed in. Open Help, press Sign in"), (Shapeless(), "answered twice in a shape")]:
        monkeypatch.setattr(ai, "chosen", lambda *a, **k: agent)
        r = client().post("/api/import-text", json={"text": text})
        assert r.status_code == 502 and says in r.json()["detail"], r.text


def test_skills_cut_inside_brackets_and_doubled_ids_mend_themselves(home):
    """The Profile editor once cut skills at every comma, brackets or not, and an AI import can give two items one id,
    which a save folded into one. Both are put right whenever the CV is read or saved: no step for anyone."""
    broken = ["Python", "Reinforcement Learning (PPO", "SAC", "Stable-Baselines3", "Gymnasium)",
              "RAG (hybrid retrieval: dense + BM25", "RRF)", "C++ (never closed", "Docker"]
    (pf.folder() / "master_cv.yaml").write_text(
        "skills:\n  AI:\n" + "".join(f"    - '{s}'\n" for s in broken)
        + "experience:\n  - id: exp1\n    title: Nurse\n  - id: exp1\n    title: Carer\n", encoding="utf-8")
    mended = ["Python", "Reinforcement Learning (PPO, SAC, Stable-Baselines3, Gymnasium)", "RAG (hybrid retrieval: dense + BM25, RRF)",
              "C++ (never closed", "Docker"]
    assert pf.read("profile")["skills"]["AI"] == mended and pf.profile().skills["AI"] == mended
    assert [(e.id, e.title) for e in pf.profile().experience] == [("exp1", "Nurse"), ("exp1_2", "Carer")]
    for _ in range(2):  # saved over the old file: once folded both into one
        pf.write("profile", {"experience": [{"id": "e1", "title": "Nurse"}, {"id": "e1", "title": "Carer"}], "skills": {"AI": broken}})
    saved = pf._plain(pf._yaml().load(pf.folder() / "master_cv.yaml"))
    assert [(e["id"], e["title"]) for e in saved["experience"]] == [("e1", "Nurse"), ("e1_2", "Carer")] and saved["skills"]["AI"] == mended


def test_patch_preferences_merges_and_keeps_server_fields(home, monkeypatch):
    from jobgambit.app import settings_api
    calls = []
    monkeypatch.setattr(settings_api.scheduler, "disable", lambda job=None: calls.append(f"off {job}"))
    monkeypatch.setattr(settings_api.scheduler, "ensure", lambda search_time=None, mail_on=None: calls.append(search_time))
    pf.write("preferences", {"home_country": "DE", "salary": "60k",
                             "weights": {"skills": 2.0, "location": 0.5, "seniority": 1.0, "language": 1.0}})
    c = client()
    assert c.patch("/api/preferences", json={"weights": {"skills": 1.5}, "notice": "1 month"}).status_code == 200
    p = pf.read("preferences")
    assert p["weights"] == {"skills": 1.5, "location": 0.5, "seniority": 1.0, "language": 1.0}  # the rest survives
    assert (p["home_country"], p["salary"], p["notice"]) == ("DE", "60k", "1 month") and calls == []
    # a new time: the search task is made again at it (the email check is left as it is)
    assert c.patch("/api/preferences", json={"search_time": "08:30"}).status_code == 200 and calls == ["off search", "08:30"]
    assert c.patch("/api/preferences", json={"search_time": "08:30"}).status_code == 200 and calls == ["off search", "08:30"]  # unchanged
    assert c.patch("/api/preferences", json={"radius_km": "far"}).status_code == 422
    assert pf.read("preferences")["radius_km"] == 50
