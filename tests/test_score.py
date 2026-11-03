"""The fit score (core/score.py) and the skill map it matches on (core/skillmap.py)."""
import json
from datetime import date, timedelta

from jobgambit.core import ai, score, search, skillmap, sources
from jobgambit.core import profile as pf
from jobgambit.core.data import db

ASK = skillmap.ask  # conftest keeps the AI app out of every test; the skill-map tests fake ai.answer behind it
PREFS = {"titles": ["Robotics Engineer"], "keywords": {"en": ["Motion Planning"]}, "job_types": ["full_time"], "targets": [{"code": "DE"}]}
PROFILE = {"skills": {"Control": ["Model Predictive Control (MPC)", "MATLAB/Simulink"], "Tools": ["TCP/UDP", "Linux (Ubuntu)", "C++"]},
           "languages": [{"name": "English", "level": "C2"}],
           "experience": [{"id": "e1", "title": "Robotics Engineer", "org": "Acme", "start": "2022-01", "end": "2023-12",
                           "bullets": [{"id": "e1_b1", "text": "Built motion planning in C++"}]}]}
FIT = "Your profile\nC++\nMPC\nSimulink\nUbuntu"  # four requirements the person has
JOB = {"title": "Robotics Engineer", "company": "Acme", "location": "", "country": "DE"}


def judge(desc="", m=None, **job):
    return (m or score.Matcher(PREFS, PROFILE)).assess({**JOB, **job}, desc)


def fake_map(monkeypatch, *answers):
    """The AI's skill map answers, in turn; returns the prompts it was asked and the cost levels."""
    asked, levels, answers = [], [], list(answers)
    monkeypatch.setattr(skillmap, "ask", ASK)
    monkeypatch.setattr(ai, "chosen", lambda name="", level="": levels.append(level))
    monkeypatch.setattr(ai, "answer", lambda prompt, model, agent, timeout=600, key="", files=None: asked.append(prompt) or model(concepts=answers.pop(0)))
    return asked, levels


def concept(cid, name, kind="tool", aliases=(), implied_by=()):
    return {"id": cid, "kind": kind, "name": name, "aliases": list(aliases), "implied_by": list(implied_by)}


def test_warnings_always_lower_the_score_even_at_a_perfect_fit():
    fit, off = judge(FIT)
    assert off is False and fit["score"] == 90 and "meets 4 of 4 skill requirements" in fit["why"]
    senior, _ = judge(FIT, title="Senior Robotics Engineer")
    german, _ = judge(FIT + "\nSehr gute Deutschkenntnisse erforderlich")  # the person speaks English only
    years, _ = judge(FIT + "\n6+ years of experience in robotics")  # they have 2
    both, _ = judge(FIT + "\nSehr gute Deutschkenntnisse erforderlich", title="Senior Robotics Engineer")
    assert "⚠ senior role" in senior["why"] and "⚠ German required" in german["why"] and "⚠ asks 6+ years" in years["why"]
    assert both["score"] < min(senior["score"], german["score"]) and max(senior["score"], german["score"], years["score"]) < fit["score"]
    far = score.Matcher({**PREFS, "places": ["Berlin"], "radius_km": 50, "remote": "onsite"}, PROFILE)
    assert judge(FIT, far, location="München")[0]["score"] < judge(FIT, far, location="Berlin")[0]["score"]


def test_no_100_without_all_of_it():
    """100 only when every requirement is met, the title is a target title and nothing stands in the way: smoothing and
    the preferred-company bonus never make one."""
    m = score.Matcher({**PREFS, "prefer_companies": ["Acme"]}, PROFILE)
    many = "Your profile\n" + "\n".join(f"C++ and MPC, part {i}" for i in range(20))  # smoothed: 97.6, and 5 for Acme
    assert judge(many, m)[0]["score"] == 99


def test_one_idea_counts_once():
    """robotics, robotics engineer and robotik in the title are one hit, not three."""
    once = score.Matcher({**PREFS, "keywords": {}}, PROFILE)
    thrice = score.Matcher({**PREFS, "keywords": {"en": ["Robotics"], "de": ["Robotik"]}}, PROFILE)
    assert judge(FIT, once)[0]["score"] == judge(FIT, thrice)[0]["score"]
    index = score.Index({"rob": ["Robotics", "Robotik", "Robotics Engineering"]})
    assert index.find("Robotics engineering in der Robotik") == {"rob"}


def test_a_title_hit_needs_the_words_side_by_side():
    m = score.Matcher({"titles": ["AI Engineer", "Robotics Engineer"], "job_types": ["full_time"]}, PROFILE)
    t = "Senior QA Engineer - AI & Robotics"
    assert m.role(m.clean(t), t, []) == 0 and judge("", m, title=t)[1] == "junk"
    assert m.role(m.clean("Werkstudent (m/w/d) Robotics Engineer"), "", []) == 1.0  # job-type words and (m/w/d) left out
    assert score.loose_hit("softwareentwickler", score.norm("Werkstudent Softwareentwicklung Backend"))


def test_cv_skills_are_found_by_every_name():
    index, have, names, mapped = skillmap.concepts(PROFILE)
    found = {names[c] for c in index.find("You know MPC and Simulink, TCP sockets and Ubuntu")}
    assert not mapped and found == {"Model Predictive Control (MPC)", "MATLAB/Simulink", "TCP/UDP", "Linux (Ubuntu)"}
    assert score.aliases("Vehicle Control (longitudinal/lateral)") == ["Vehicle Control"]  # brackets that only describe it
    assert score.aliases("Reinforcement Learning (PPO, SAC)") == ["Reinforcement Learning", "PPO", "SAC"]


def test_short_names_match_only_as_written():
    index = score.Index({"ai": ["KI", "AI"], "vision": ["Computer Vision", "CV"], "aws": ["AWS"], "cpp": ["C++"]})
    assert index.find("Free parking next to the office") == set()  # "KI" is not "parking"
    assert index.find("Please send your CV and certificates") == set()  # CV is never a name
    assert index.find("We follow all labour laws") == set()  # "AWS" is not "laws"
    assert index.find("Du baust KI-Agenten auf AWS in C++/Python") == {"ai", "aws", "cpp"}


def test_german_compounds_match():
    index = score.Index({"ip": ["Bildverarbeitung"], "rob": ["Robotik"], "ros": ["ROS 2"]})
    assert index.find("Entwicklung von Bildverarbeitungsalgorithmen für die Servicerobotik") == {"ip", "rob"}
    assert index.hits("Bildverarbeitungsalgorithmen mit ROS 2") == {"ip": "Bildverarbeitungsalgorithmen", "ros": "ROS 2"}  # the text's own words
    assert index.find("Erfahrung mit ROS 2") == {"ros"} and index.find("Erfahrung mit ROS und 2 Robotern") == set()  # side by side


def test_asking_for_three_things_they_have_beats_ten_that_include_them():
    three = judge("Your profile\nC++\nMPC\nSimulink")[0]["score"]
    ten = judge("Your profile\nC++\nMPC\nSimulink\nJava\nKubernetes\nAUTOSAR\nCUDA\nRust\nSAP\nTerraform")[0]["score"]
    assert three > ten


def test_a_missing_nice_to_have_counts_half():
    assert score.units("Your profile\nC++\nNice to have\nRust\nExperience with ROS is a plus") == \
        [(1.0, "C++"), (0.5, "Rust"), (0.5, "Experience with ROS is a plus")]
    pf.write("skill_map", {"concepts": [concept("rust", "Rust")]})  # a skill map: Rust is one, and the CV doesn't name it
    nice, _ = judge("Your profile\nC++\nMPC\nNice to have\nRust")
    must, _ = judge("Your profile\nC++\nMPC\nRust")
    assert nice["score"] > must["score"] and "missing: Rust" in must["why"] and "meets 2 of 3 skill requirements" in must["why"]


def test_the_reason_counts_only_lines_that_name_a_skill():
    fit, _ = judge("Your profile\nC++\nMPC\nSeveral years of experience developing software solutions")
    assert "meets 2 of 2 skill requirements" in fit["why"]


def test_headings_with_a_full_stop_are_headings_when_the_posting_writes_them_so():
    text = "Warum PCO?\nWir sind ein Team.\nDeine Aufgaben.\nBuild robots\nDein Profil.\nC++\nDu bringst Erfahrung mit."
    assert score.units(text) == [(1.0, "C++"), (1.0, "Du bringst Erfahrung mit."), (0.5, "Build robots")]
    assert score.units("Dein Profil\nC++\nDu bringst Erfahrung mit.") == [(1.0, "C++"), (1.0, "Du bringst Erfahrung mit.")]
    nice = "Deine Aufgaben.\nBuild robots\nDein Profil.\nC++\nKenntnisse in Docker sind von Vorteil.\nPython"
    assert score.units(nice) == [(1.0, "C++"), (1.0, "Python"), (0.5, "Build robots"), (0.5, "Kenntnisse in Docker sind von Vorteil.")]


def test_benefits_and_the_company_story_are_left_out():
    assert score.units("We are Acme, Python everywhere.\nYour tasks\nBuild robots\nWhat we offer\nFree Python courses") == [(0.5, "Build robots")]
    plain, _ = judge(FIT)
    assert judge(FIT + "\nBenefits\nJava training\nKubernetes days")[0]["score"] == plain["score"]
    assert score.units("Strong communication skills\nFluent German\nC++") == [(0.8, "Strong communication skills"), (0.8, "Fluent German"), (0.8, "C++")]
    assert judge("Strong communication skills\nFluent English\nC++")[0]["score"] == judge("C++")[0]["score"]  # soft skills: left out


def test_a_title_only_posting_tops_out_at_50():
    assert judge("")[0]["score"] == 50 and judge("", title="Robotics Engineer (Motion Planning)")[0]["score"] <= 50


def test_scoring_works_without_a_map_and_names_missing_tools():
    fields, off = judge("Your profile\nC++\nExperience with AUTOSAR and CUDA")
    assert skillmap.read() == {} and off is False and "missing: AUTOSAR, CUDA" in fields["why"]


def test_skill_map_have_by_code_the_new_run_wins_and_the_hash_gate(monkeypatch):
    first = [concept("mpc", "Model Predictive Control", "method", ["MPC", "modellprädiktive Regelung"]),
             concept("ros2", "ROS 2", aliases=["ROS2"], implied_by=["e1_b1"]),
             concept("autosar", "AUTOSAR", implied_by=["gone"]), concept("vision", "Computer Vision", "domain", ["CV", "Bildverarbeitung"])]
    asked, levels = fake_map(monkeypatch, first, [concept("cuda", "CUDA"), concept("mpc2", "Model Predictive Control", "method", ["MPC-Regelung"])])
    made = skillmap.ensure(PROFILE, PREFS)
    assert len(asked) == 1 and levels == ["standard"] and "Built motion planning" in asked[0] and "<untrusted>" in asked[0]
    _, have, names, mapped = skillmap.concepts(PROFILE)
    assert mapped and (have["mpc"], have["ros2"], have["autosar"], have["vision"]) == (1.0, 0.6, 0.0, 0.0)  # code decides, not the AI
    assert {c["id"]: c["aliases"] for c in made["concepts"]}["vision"] == ["Bildverarbeitung"]  # "CV" is never a name
    typo = {**PROFILE, "experience": [{**PROFILE["experience"][0], "bullets": [{"id": "e1_b1", "text": "Built motion plannning in C++"}]}]}
    skillmap.ensure(typo, PREFS)
    assert len(asked) == 1  # a typo fixed in a line: no new run
    skillmap.flip("autosar", 1)
    assert skillmap.concepts(PROFILE)[1]["autosar"] == 1.0  # the person's own say wins
    more = {**PROFILE, "skills": {**PROFILE["skills"], "GPU": ["CUDA"]}}
    again = skillmap.ensure(more, PREFS)
    ids = [c["id"] for c in again["concepts"]]
    assert len(asked) == 2 and ids == ["cuda", "mpc", "autosar"]  # the new run's map: the same name keeps its id, the person's chip stays
    assert again["concepts"][1]["aliases"] == ["MPC", "modellprädiktive Regelung", "MPC-Regelung"]
    assert skillmap.concepts(more)[1]["autosar"] == 1.0
    skillmap.ensure(more, PREFS)
    assert len(asked) == 2


def test_a_failed_map_is_not_tried_again_the_same_day(monkeypatch):
    calls = []
    monkeypatch.setattr(skillmap, "ask", lambda prompt, agent: calls.append(1) or (_ for _ in ()).throw(ai.AIError("not signed in")))
    assert skillmap.ensure(PROFILE, PREFS).get("concepts") is None and skillmap.STATE["error"] == "not signed in"
    skillmap.ensure(PROFILE, PREFS)
    assert len(calls) == 1 and judge(FIT)[0]["score"] == 90  # scoring goes on without a map


def test_rank_keeps_the_score_and_adds_a_badge(monkeypatch):
    from jobgambit.app import jobs_api
    with db() as con:
        con.execute("insert into jobs(key, title, company, url, status, score, why, data) values ('k', 'Robotics Engineer', 'Acme', "
                    "'https://x/k', 'new', 42, 'meets 2 of 3 requirements', '{}')")
    monkeypatch.setattr(ai, "answer", lambda prompt, model, agent, timeout=600, key="", files=None: model(ranking=[{"key": "k", "score": 91, "why": "close  fit"}]))
    monkeypatch.setattr(ai, "chosen", lambda *a, **k: None)
    assert search.rank() == 1
    row = jobs_api.board()[0]
    assert row["score"] == 42 and row["why"] == "meets 2 of 3 requirements" and row["ai"] == {"score": 91, "why": "close fit"}


def test_ghost_postings_go_under_less_relevant():
    old = (date.today() - timedelta(days=score.MAX_AGE + 5)).isoformat()
    assert judge(FIT, posted=old)[1] == "old" and judge(FIT, first_seen=old)[1] == "old"
    soon = (date.today() + timedelta(days=10)).strftime("%d.%m.%Y")
    assert judge(FIT + f"\nBewerbungsfrist: {soon}", posted=old)[1] is False  # its deadline is still ahead: not a ghost
    assert judge(FIT, posted=(date.today() - timedelta(days=3)).isoformat())[1] is False


def test_one_posting_at_several_names_of_one_employer_shows_once():
    m = score.Matcher(PREFS, PROFILE)
    vw = [sources.job("Robotics Engineer (m/w/d)", c, "Wolfsburg", f"https://x/{i}", "linkedin", desc=FIT)
          for i, c in enumerate(["Volkswagen Group", "Volkswagen Group (CARIAD)", "Volkswagen Algérie"])]
    agency = [sources.job("Robotics Engineer", name, city, f"https://f/{city}", "indeed", desc=FIT)
              for name, city in (("FERCHAU Berlin", "Berlin"), ("FERCHAU Engineering Hamburg", "Hamburg"))]  # one firm, two cities: two jobs
    unis = [sources.job("Robotics Engineer", name, "München", f"https://u/{i}", "indeed", desc=FIT)
            for i, name in enumerate(["TU München", "Hochschule München"])]  # two employers in one city: two jobs
    assert search.store(vw + agency + unis, m) == (5, 0)
    with db() as con:  # stored before this rule: the next rescore shows the best one only
        con.execute("insert into jobs(key, title, company, location, url, status, first_seen, why, data) values ('vw2', 'Robotics Engineer',"
                    " 'Volkswagen AG', 'Wolfsburg', 'https://x/9', 'new', ?, '', '{}')", (date.today().isoformat(),))
    pf.write("preferences", PREFS)
    pf.write("profile", PROFILE)
    search.rescore()
    with db() as con:
        got = {r["company"]: (r["status"], r["why"][:11]) for r in con.execute("select company, status, why from jobs where company like 'Volks%'")}
    assert sorted(s for s, _ in got.values()) == ["filtered", "new"] and got["Volkswagen AG"] == ("filtered", "[duplicate]")


def test_another_process_migrating_is_left_alone_and_a_dead_ones_lock_is_taken(monkeypatch):
    from jobgambit.core.data import home
    runs = []
    monkeypatch.setattr(search, "_rescore", lambda: runs.append(search.BUSY.is_set()) or 0)
    state = search.load_state()
    state["score_version"] = score.VERSION - 1
    search.save_state(state)
    lock = home() / "jobs" / "migrate.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(__import__("os").getpid()), encoding="utf-8")  # a living process holds it
    assert search.migrate() == 0 and runs == [] and lock.exists()
    lock.write_text("999999", encoding="utf-8")  # one that ended
    assert search.migrate() == 0 and runs == [True] and not lock.exists() and not search.BUSY.is_set()
    assert search.load_state()["score_version"] == score.VERSION


def test_two_migrations_at_once_judge_everything_once(monkeypatch):
    import threading
    import time
    runs = []
    monkeypatch.setattr(search, "_rescore", lambda: runs.append(1) or time.sleep(0.2) or 0)
    state = search.load_state()
    state["score_version"] = score.VERSION - 1
    search.save_state(state)
    threads = [threading.Thread(target=search.migrate) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert runs == [1] and search.load_state()["score_version"] == score.VERSION


def test_every_open_posting_is_checked_weekly(monkeypatch):
    week_ago = (date.today() - timedelta(days=8)).isoformat()
    with db() as con:
        con.executemany("insert into jobs(key, title, company, url, status, first_seen, data) values (?, 'Robotics Engineer', 'Acme', ?, 'new', ?, ?)",
                        [(f"k{i}", f"https://acme.example/{i}", week_ago, json.dumps({"checked": week_ago if i < 50 else date.today().isoformat()}))
                         for i in range(60)])
    asked = []
    monkeypatch.setattr(sources, "http", lambda url, timeout=20: asked.append(url) or (404, "", url))
    for i in range(60):
        (pf.folder().parent / "jobs" / "desc").mkdir(parents=True, exist_ok=True)
        (pf.folder().parent / "jobs" / "desc" / f"k{i}.txt").write_text(FIT, encoding="utf-8")
    search.enrich(score.Matcher(PREFS, PROFILE))
    with db() as con:
        expired = con.execute("select count(*) from jobs where status='expired'").fetchone()[0]
    assert len(asked) == 50 and expired == 50  # every one not checked for a week, not 40 a run; the others wait


def test_working_student_and_student_team_months_count_half():
    p = {"experience": [{"title": "AI Engineer (Working Student)", "start": "2024-01", "end": "2025-12"},
                        {"title": "Team Member", "org": "SAE Baja (University Team)", "start": "2019-01", "end": "2019-12"},
                        {"title": "Engineer", "start": "2025-07", "end": "2025-12"}]}  # overlaps a half month: full
    assert score.experience_years(p) == (18 * 0.5 + 6 + 12 * 0.5) / 12


def test_a_cv_saved_reaches_the_board(monkeypatch):
    from fastapi.testclient import TestClient
    from jobgambit.app import server, settings_api
    calls = []
    monkeypatch.setattr(settings_api, "refresh_later", lambda search_too, wait=20: calls.append(search_too))
    c = TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})
    pf.write("preferences", {"setup_done": True})
    assert c.put("/api/data/profile", json=PROFILE).status_code == 200 and calls == [False]  # judged again, no new search
    assert c.get("/api/state").json()["bands"] == score.BANDS


def test_the_skill_map_chips(monkeypatch):
    from fastapi.testclient import TestClient
    from jobgambit.app import server
    c = TestClient(server.app, base_url="http://127.0.0.1", headers={"x-token": server.TOKEN})
    pf.write("profile", PROFILE)
    assert c.get("/api/skillmap").json()["concepts"] == []
    fake_map(monkeypatch, [concept("cuda", "CUDA"), concept("mpc", "Model Predictive Control", "method", ["MPC"])])
    got = c.post("/api/skillmap/rebuild").json()["concepts"]
    assert [(x["name"], x["have"]) for x in got] == [("Model Predictive Control", 1.0), ("CUDA", 0.0)]  # had ones first
    assert c.put("/api/skillmap/cuda", json={"have": 1}).status_code == 200
    assert {x["id"]: (x["have"], x["set"]) for x in c.get("/api/skillmap").json()["concepts"]}["cuda"] == (1.0, 1.0)
    assert c.put("/api/skillmap/nope", json={"have": 1}).status_code == 404


def test_a_new_formula_judges_the_stored_postings_once(monkeypatch):
    calls = []
    monkeypatch.setattr(search, "_rescore", lambda: calls.append(1) or 7)
    assert search.migrate() == 7 and search.load_state()["score_version"] == score.VERSION
    assert search.migrate() == 0 and calls == [1]
