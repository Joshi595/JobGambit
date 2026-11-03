"""The skill map: the skills, tools, methods, domains and certificates a person's field is about, with the names job
postings use for them (in English and the target countries' languages), so score.py can tell what a posting asks for
and which of it the person has.

One AI run (standard level: what a CV shows needs a careful reader) makes the map from the CV, the person's titles
and search words, and the tool-like words their stored postings ask for most. What the person has is never the AI's word: code decides it (has: named on the CV
1, shown by one of its entries 0.6, else 0), again on every CV change, and the person can flip any of them (Profile ›
Your CV). The map is made again only when what it is made from changes (titles, search words, skills, entry titles,
target languages); the new run replaces the old map, keeping what the person set themselves.
"""
import hashlib
import json
import re
from datetime import date

from pydantic import Field

from jobgambit.core import profile as pf, score
from jobgambit.core.profile import Model

KINDS = ("skill", "tool", "language", "method", "domain", "certificate")
GENERIC = 0.25  # a method or domain named in more postings than this says nothing about fit (tools and languages always stay)
SAMPLE = 2000  # stored postings read to tell that
STATE = {"error": ""}  # why the last attempt failed, for the search log


class Concept(Model):
    id: str = Field(description="Short, lowercase, unique, e.g. mpc or sensor_fusion")
    kind: str = Field(description="skill, tool, language (a programming language), method, domain or certificate")
    name: str = Field(description="How a CV names it")
    aliases: list[str] = Field(description="Every name job postings use for it: English and the given languages, acronyms in their usual case")
    implied_by: list[str] = Field(default_factory=list, description="Ids of CV entries or lines that show it without naming it")


class Map(Model):
    concepts: list[Concept]


def read() -> dict:
    return pf.read("skill_map") or {}


def languages(prefs: dict) -> list[str]:
    """English and the official languages of the countries the person applies in."""
    from babel.languages import get_official_languages
    return list(dict.fromkeys(["en", *(l for t in prefs.get("targets") or [] for l in get_official_languages(t.get("code", ""), de_facto=True))]))


def key(profile: dict, prefs: dict) -> str:
    """What the map is made from, in the parts that change what it should hold (a typo fixed in a bullet costs no run)."""
    entries = [e.get("title", "") for sec in ("experience", "projects", "volunteering", "courses") for e in profile.get(sec) or []]
    parts = [sorted(prefs.get("titles") or []), sorted(w for ws in (prefs.get("keywords") or {}).values() for w in ws),
             sorted(s for items in (profile.get("skills") or {}).values() for s in items), sorted(entries), languages(prefs)]
    return hashlib.sha1(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()[:12]


def ids(node) -> set[str]:
    """Every id in the CV (entries, lines, degrees)."""
    if isinstance(node, dict):
        return ({node["id"]} if isinstance(node.get("id"), str) else set()) | {i for v in node.values() for i in ids(v)}
    return {i for v in node for i in ids(v)} if isinstance(node, list) else set()


def have(profile: dict, concepts: list[dict]) -> dict[str, float]:
    """Code's own judgement: a concept named on the CV (its name or an alias) 1, shown by a CV entry or line still there
    0.6 (score.IMPLIED), else 0."""
    on_cv = score.Index({c["id"]: [c["name"], *c.get("aliases", [])] for c in concepts}).find(score.cv_text(profile))
    there = ids(profile)
    return {c["id"]: 1.0 if c["id"] in on_cv else score.IMPLIED if set(c.get("implied_by") or []) & there else 0.0 for c in concepts}


def concepts(profile: dict, stored: dict | None = None):
    """What score.py matches on: the map's concepts with what the person has (their own choice wins), and every CV skill
    the map doesn't name (had). -> (Index, {id: have}, {id: name}, whether there is a map)."""
    cs = (read() if stored is None else stored).get("concepts") or []
    found = have(profile, cs)
    names = {c["id"]: [c["name"], *c.get("aliases", [])] for c in cs}
    label = {c["id"]: c["name"] for c in cs}
    val = {c["id"]: float(c["set"]) if c.get("set") is not None else found[c["id"]] for c in cs}
    known = {" ".join(score.norm(a).split()) for ns in names.values() for a in ns}
    for items in (profile.get("skills") or {}).values():
        for item in items:
            al = score.aliases(str(item))
            if al and not any(" ".join(score.norm(a).split()) in known for a in al):
                names["cv:" + item], label["cv:" + item], val["cv:" + item] = al, str(item), 1.0
    return score.Index(names), val, label, bool(cs)


def shown(profile: dict | None = None) -> list[dict]:
    """The map for Profile › Your CV: each concept with what the person has, had ones first."""
    profile = profile if profile is not None else pf.profile().model_dump()
    cs = read().get("concepts") or []
    found = have(profile, cs)
    out = [{"id": c["id"], "name": c["name"], "kind": c.get("kind", ""), "found": found[c["id"]],
            "have": float(c["set"]) if c.get("set") is not None else found[c["id"]], "set": c.get("set")} for c in cs]
    return sorted(out, key=lambda c: (-c["have"], c["name"].lower()))


def flip(cid: str, value: float | None):
    """The person's own say on one concept: 1 (has it), 0 (doesn't), None (as the CV shows)."""
    stored = read()
    for c in stored.get("concepts") or []:
        if c["id"] == cid:
            c["set"] = value
            pf.write("skill_map", stored)
            return True
    return False


def ensure(profile: dict | None = None, prefs: dict | None = None, force: bool = False) -> dict:
    """The stored map, made (or made again and merged in) when what it is made from changed; force: now. Without an AI
    app, or when it fails, the old map (or none) stays, and it isn't tried again that day: scoring works without one."""
    profile = profile if profile is not None else pf.profile().model_dump()
    prefs = prefs if prefs is not None else pf.Preferences.model_validate(pf.read("preferences")).model_dump()
    stored, k = read(), key(profile, prefs)
    if not force and (stored.get("hash") == k or stored.get("tried") == f"{k} {date.today()}"):
        return stored
    if not (profile.get("skills") or profile.get("experience") or profile.get("projects")):
        return stored  # nothing to make it from yet
    from jobgambit.core import ai
    try:
        new = build(profile, prefs)
    except ai.AIError as e:
        STATE["error"] = str(e)[:200]
        pf.write("skill_map", {**stored, "tried": f"{k} {date.today()}"})
        return read()
    STATE["error"] = ""
    out = {"hash": k, "made": date.today().isoformat(), "concepts": merge(stored.get("concepts") or [], new)}
    pf.write("skill_map", out)
    return out


def merge(old: list[dict], new: list[dict]) -> list[dict]:
    """The new run's concepts. One already there (same id or name) keeps its id, name, older names and the person's own
    choice; one the new run left out stays only when the person set it themselves (Profile chips)."""
    key = lambda c: " ".join(score.norm(c["name"]).split())
    by = {}
    for c in old:
        by[c["id"]] = by[key(c)] = c
    out = {}
    for c in new:
        same = by.get(c["id"]) or by.get(key(c))
        cid = same["id"] if same else c["id"]
        prev = out.get(cid) or same
        if prev:
            c = {**c, "id": cid, "name": prev["name"], "aliases": list(dict.fromkeys([*prev.get("aliases", []), *c["aliases"]])),
                 "implied_by": list(dict.fromkeys([*(out[cid]["implied_by"] if cid in out else []), *c.get("implied_by", [])])),
                 **({"set": prev["set"]} if prev.get("set") is not None else {})}
        out[cid] = c
    return [*out.values(), *(c for c in old if c["id"] not in out and c.get("set") is not None)]


def asked(limit: int = 150) -> list[str]:
    """The tool-like words (PyTorch, AUTOSAR, ROS2) the postings on the Board name most, for the AI to cover; not
    headings in capitals (ABOUT, BENEFITS) or legal forms."""
    from collections import Counter
    from jobgambit.core.check import tech_tokens
    count = Counter()
    for text in posting_texts(("new", "shortlisted")):
        count.update({t for t in tech_tokens(text) if len(t) > 1 and not (t.isupper() and t.isalpha() and len(t) > 4)
                      and t.lower() not in ("gmbh", "mbh", "ag", "kg", "se", "co")})
    return [t for t, n in count.most_common(limit) if n >= 3]


def posting_texts(statuses=("new", "filtered", "shortlisted")):
    """Up to SAMPLE stored postings' texts (open ones by default), newest first."""
    from jobgambit.core.data import db, home
    with db() as con:
        keys = [r[0] for r in con.execute(f"select key from jobs where status in ({','.join('?' * len(statuses))}) order by first_seen desc",
                                          statuses)]
    n = 0
    for k in keys:
        path = home() / "jobs" / "desc" / f"{k}.txt"
        if path.exists():
            yield path.read_text(encoding="utf-8")
            n += 1
            if n >= SAMPLE:
                return


def build(profile: dict, prefs: dict) -> list[dict]:
    """One AI run (standard level) over the whole fact sheet: the concepts, cleaned, without the generic ones."""
    from babel import Locale
    from jobgambit.core import apply, prompts
    try:
        facts = apply.factsheet(profile)
    except Exception:  # a CV not saved through the app yet (no ids): its text alone
        facts = score.cv_text(profile)
    langs = languages(prefs)
    wants = (f"titles: {', '.join(prefs.get('titles') or [])}; search words: "
             + ", ".join(w for ws in (prefs.get("keywords") or {}).values() for w in ws))
    got = ask(prompts.build("skill_map", factsheet=facts, wants=wants,
                            languages=", ".join(Locale("en").languages.get(l, l) for l in langs),
                            asked=apply.untrusted(", ".join(asked()))), prefs.get("agent", ""))
    return generic(clean(got.concepts))


def ask(prompt: str, agent: str) -> Map:
    from jobgambit.core import ai
    return ai.answer(prompt, Map, ai.chosen(agent, "standard"), timeout=900)


def clean(concepts: list[Concept]) -> list[dict]:
    """Ids unique and lowercase, kinds from KINDS, names that can never match (CV, IT...) left out."""
    out, seen = [], set()
    for c in concepts:
        cid = re.sub(r"[^a-z0-9_+#]+", "_", c.id.lower()).strip("_") or re.sub(r"\W+", "_", c.name.lower())
        names = [a.strip() for a in dict.fromkeys([c.name, *c.aliases]) if len(a.strip()) > 1 and a.strip().lower() not in score.STOP]
        if not names or cid in seen:
            continue
        seen.add(cid)
        out.append({"id": cid, "kind": c.kind if c.kind in KINDS else "skill", "name": c.name.strip(),
                    "aliases": [a for a in names if a != c.name.strip()], "implied_by": list(dict.fromkeys(c.implied_by))})
    return out


def generic(concepts: list[dict]) -> list[dict]:
    """Methods and domains found in more than GENERIC of the stored postings go: they'd match almost anything."""
    texts = list(posting_texts())
    if len(texts) < 50:
        return concepts
    index = score.Index({c["id"]: [c["name"], *c["aliases"]] for c in concepts if c["kind"] in ("method", "domain")})
    count = {}
    for t in texts:
        for cid in index.find(t):
            count[cid] = count.get(cid, 0) + 1
    return [c for c in concepts if count.get(c["id"], 0) <= GENERIC * len(texts)]
