"""Fit estimate for a posting at no AI cost: how much of what the posting asks for the person has, how close its
title is to what they look for, and what stands in the way.

The posting is read line by line (units): its requirements count fully, its tasks and nice-to-haves half, its company
story and benefits not at all. Each line's skills are found by their names (Index: the skill map's concepts and the
CV's own skills, in English and the target countries' languages); a line scores the share of them the person has
(skillmap: named on the CV 1, shown by a CV entry 0.6, else 0). Warnings (a senior role, years asked, a language, the
distance) multiply the score last, so a perfect fit with a warning still shows lower. `why` says how many requirements
are met, what is missing and what stands in the way. Any profession, any country: the person's own titles, search
words and CV are all it knows.
"""
import math
import re
from datetime import date
from functools import lru_cache

from jobgambit.core import countries

VERSION = 2  # of the formula: postings judged by an older one are judged again, once (search.migrate)
BANDS = {"strong": 50, "good": 35, "possible": 25}  # the Board's bands (board.js gets them from /api/state): on real data (2026-10-30)
# strong is about the top 5% of open postings and good the top 20%; below possible, a posting is weak by keywords
MAX_AGE = 60  # days since posting: an older one is likely a ghost posting (unless its deadline is still ahead)
WEIGHT = {"req": 1.0, "task": 0.5, "nice": 0.5, "about": 0.0, "none": 0.8}  # how much a posting's line counts, by its section
UNKNOWN = (0.3, 0.5)  # a line with no known skill in it: with a skill map (likely outside the person's field), without one
IMPLIED = 0.6  # a skill a CV entry shows without naming it
ROLE = (1.0, 0.7, 0.4)  # in the posting's title: a target title, a search word, a skill they have (or one of the first two in its requirements)
ROLE_SHARE = 0.5  # how much the title decides: a posting whose title fits nothing tops out at 1 - this
PRIOR = 1.0  # a posting counts this many more lines met half-way: a few lines can't make a perfect fit (title only: 0.5)
PENALTY = {"senior": .5, "years3": .4, "years2": .15, "language": .1, "far": .1}  # each times the person's weight for it; language:
# .3 cost 0.028 AUC on real applications (2026-10-30: most asked for German, applied to anyway), .1 is the most within 0.01
PREFERRED = 5  # points for a company the person prefers
MAX_UNITS = 30  # lines of a posting read at most, the heaviest first
SENIOR = r"\b(senior|sr|lead|principal|staff|head|chief|director|expert|leitung|leiter|teamleiter)\b"
YEARS = r"(?<!\d)(\d{1,2})\s?\+?(?:\s*(?:-|–|bis|to)\s*\d{1,2})?\s*(?:years|jahre|ans|años|anni|jaar)"
STUDENT_WORK = re.compile(r"working student|werkstudent|studentische|student (?:assistant|job|team|research)|university team|"
                          r"hiwi|formula student|racing team", re.I)  # experience that counts half
# Worldwide job-type words (title, lower case). Packs add local ones (e.g. Werkstudent).
TYPES = {"internship": ["intern", "internship", "stage", "stagiaire", "prácticas", "tirocinio", "stagiair"],
         "student": ["working student", "student job", "student assistant", "student research", "part-time student"],
         "apprenticeship": ["apprentice", "apprenticeship", "alternance"],
         "thesis": ["thesis", "mémoire", "tesis"],
         "phd": ["phd", "doctoral", "doctorate", "doktorand", "promotions", "zur promotion", "postdoc", "research associate", "research assistant",
                 "research scientist", "research engineer", "researcher", "wissenschaftliche", "wissenschaftlicher", "akademische mitarbeiter"],
         "graduate": ["graduate programme", "graduate program", "trainee"]}
# Words put in front of the person's titles and keywords to search for a job type (packs add local ones, e.g. Werkstudent).
SEARCH_WORDS = {"student": ["working student"], "internship": ["internship"], "thesis": ["thesis"],
                "apprenticeship": ["apprenticeship"], "graduate": ["graduate programme"]}
FULL = {"full_time", "part_time"}  # a research or graduate job also counts for these
RESEARCH_ORG = (r"universit|hochschule|\b(tu|th|rwth|kit|tum|lmu|eth|epfl|dlr|mpi|inria|cnrs|embl)\b|fraunhofer|"
                r"max.planck|helmholtz|dfki|leibniz|\binstitute?\b|\bforschungs|research (centre|center)")
LEVEL_OK = re.compile(r"\b(b2|c1|c2|native|mother|fluent|full professional|bilingual|muttersprach|fließend|courant|nativo)", re.I)
DEADLINE_WORDS = (r"(?:deadline|closing date|apply by|applications? (?:close|by)|bewerbungsfrist|bewerbungsschluss|einsendeschluss|"
                  r"bewerbung\w* (?:\w+ ){0,5}?bis(?: zum| spätestens)?|date limite|fecha l[ií]mite|scadenza|sluitingsdatum)")
# A short line that opens a section of a posting, by what it says (first match wins). Bullets are lost in stored texts,
# so a short requirement line can look like a heading: these are words such lines rarely use.
HEADINGS = [(kind, re.compile(rx, re.I)) for kind, rx in [
    ("nice", r"nice.to.have|\bbonus|pluspunkt|wünschenswert|von vorteil|preferred (qualif|skill|experience)|desirable|good to have"),
    ("none", r"about the (job|role|position)|über die stelle|stellenbeschreibung|job description"),
    ("about", r"über uns|about us|wer wir sind|who we are|was wir (dir |ihnen )?bieten|(was|das) bieten wir|wir bieten|we offer|our offer|"
              r"what.?s in it for you|why you.?ll love|your perks|"
              r"unser angebot|benefit|perks|vorteile|^(warum|why)\b|\bkontakt\b|\bcontact\b|ansprechpartner|bewerbung|how to apply|"
              r"interessiert|interesse geweckt|interested|(das|unser|über das) unternehmen|about the company|kultur|culture|"
              r"gehalt|salary|vergütung|compensation|chancengleich|equal opportunit|diversity|vielfalt|hinweis"),
    ("task", r"aufgabe|tätigkeit|\btasks?\b|responsib|you.?ll do|you will do|your role|deine rolle|ihre rolle|the role|mission|"
             r"erwartet (dich|sie)|(dich|sie) erwartet|duties|verantwortungsbereich|your impact|day.to.day|you.?ll be doing|"
             r"was du (bewegst|machst|tust)|(dein|ihr|your) job|objective|scope"),
    ("req", r"\bprofile?\b|anforderung|voraussetzung|qualifi|mitbring|bringst|bringen sie|you bring|you.?ll bring|requirement|"
            r"looking for|suchen wir|wir suchen|your background|^skills$|skill.?set|(your|deine|key|required|technical) skills|"
            r"überzeug|begeisterst|auszeichne|zeichnet|expect|erwarten wir|wir erwarten|uns wünschen|must.have|who you are|about you")]]
NICE = re.compile(r"nice.to.have|\ba plus\b|\bein plus\b|von vorteil|wünschenswert|\bideal(ly|erweise)\b|preferred|desirable|"
                  r"\bbonus\b|advantage|vorteilhaft|pluspunkt|beneficial|gerne gesehen|\boptional\b|wäre schön|would be great", re.I)
# A line about these alone says nothing about skills: soft skills, spoken languages, hours, enrolment, degree, licence,
# and the paperwork around a job (start, duration, place, how to apply, equal opportunities)
NEUTRAL = re.compile(r"team|kommunikat|communicat|motiv|selbstständig|selbständig|eigenverantwort|eigenständig|independen|proaktiv|"
                     r"startdatum|start date|dauer|duration|arbeitszeit|teilzeit|vollzeit|full.?time|part.?time|befrist|location|standort|"
                     r"bewerb|apply|application|upload|unterlagen|lebenslauf|anschreiben|cover letter|consent|datenschutz|privacy polic|"
                     r"data protection|diversity|vielfalt|chancengleich|equal opportunit|inclusi|inklusi|disabilit|behinder|compliance|"
                     r"proactiv|zuverlässig|reliab|flexib|engagement|engagiert|leidenschaft|passion|begeister|neugier|curious|"
                     r"curiosity|analytisch|analytic|strukturiert|structured|lernbereit|eager|interpersonal|organi[sz]ation|organisiert|"
                     r"sorgfalt|sorgfält|gewissenhaft|detail|problem.?solv|lösungsorientiert|kreativ|creativ|mindset|attitude|hands.on|"
                     r"spaß|freude|\bfun\b|englisch|english|deutsch|german|französisch|french|sprach|language|fließend|fluent|"
                     r"muttersprach|native|stunden|hours|per week|pro woche|semester|eingeschrieben|immatrikul|enrol|student|studium|"
                     r"studierst|studiengang|studienfach|degree|abschluss|bachelor|master|diplom|promotion|phd|doktor|führerschein|"
                     r"driving licen|fahrerlaubnis|reisebereit|travel|arbeitserlaubnis|work permit|visa\b|aufenthalt", re.I)
SENTENCE = re.compile(r"(?<=[.!?;])\s+(?=[A-ZÄÖÜ0-9])")
RAW = re.compile(r"[\w+#]+")  # the words of a raw text, C++ and C# whole
STOP = {"cv", "it", "hr", "r&d", "go"}  # never a skill's name: "send your CV", "it", "Go-live"
GENDER = re.compile(r"(?: [mwfdxi]){2,3}(?= )| all genders?(?= )| gn(?= )")  # (m/w/d), once norm()'d


def norm(text):
    return " " + re.sub(r"[^\w+#]+", " ", str(text or "").lower()) + " "


def phrase(words: str) -> str:
    """A regex for a (norm'd) phrase as loose_hit matches it."""
    return " ".join(re.escape(w) if len(w) < 7 else re.escape(w[:max(6, len(w) - 3)]) + r"\w*" for w in words.split())


def loose_hit(words: str, title: str) -> bool:
    """A search phrase in a (norm'd) title: its words side by side, each at the start of a title word, word endings
    aside (Softwareentwickler ~ Softwareentwicklung, Engineer ~ Engineering); words under 7 letters must match whole."""
    return bool(words.split()) and bool(re.search(rf"(?<= ){phrase(words)}(?= )", title))


def any_of(phrases) -> re.Pattern | None:
    """One regex for several phrases (as loose_hit matches each): a posting is read once, whatever their number."""
    ps = sorted({p for p in phrases if p.split()}, key=len, reverse=True)
    return re.compile(r"(?<= )(?:" + "|".join(phrase(p) for p in ps) + r")(?= )") if ps else None


def aliases(item: str) -> list[str]:
    """The names a CV skill goes by: "Model Predictive Control (MPC)" -> Model Predictive Control, MPC; "MATLAB/Simulink"
    -> MATLAB, Simulink. What's in brackets only when it names something (a capital or a digit), so "Vehicle Control
    (longitudinal/lateral)" stays one skill."""
    inner = [p for groups in re.findall(r"\(([^)]*)\)|\[([^\]]*)\]", item) for g in groups for p in re.split(r"[,;/:]", g)]
    main = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", item).split("/")
    out = [p.strip() for p in main] + [p.strip() for p in inner if re.search(r"[A-Z0-9]", p)]
    return [p for p in dict.fromkeys(out) if len(p) > 1 and p.lower() not in STOP]


class Index:
    """Finds concepts in a text by any of their names, in one pass over its words (an n-gram dictionary, not a regex per
    name: a rescore reads some 40,000 postings). A name of 3 characters or fewer counts only as a whole word written so,
    or in capitals ("KI" is not "parking", "AWS" not "laws"); longer ones ignore case and punctuation, several words must
    stand side by side, and one word of 6 letters or more also counts at the start or end of a compound
    ("Bildverarbeitungs|algorithmen", "Service|robotik")."""

    def __init__(self, names: dict[str, list[str]]):
        self.names = names  # every name of each concept
        self.short, self.words, self.first, self.ends, self.cache = {}, {}, {}, {}, {}
        for cid, ns in names.items():
            for n in ns:
                n = str(n or "").strip()
                if len(n) < 2 or n.lower() in STOP:
                    continue
                if len(n) <= 3:
                    for v in {n, n.upper()}:
                        self.short.setdefault(v, set()).add(cid)
                    continue
                ws = norm(n).split()
                if not ws:
                    continue
                self.words.setdefault(" ".join(ws), set()).add(cid)
                if len(ws) > 1:
                    self.first[ws[0]] = max(self.first.get(ws[0], 1), len(ws))
                elif len(ws[0]) >= 6 and ws[0].isalpha():
                    self.ends.setdefault(ws[0], set()).add(cid)
        self.lens = sorted({len(w) for w in self.ends})

    def find(self, text: str) -> set[str]:
        return set(self.hits(text))

    def hits(self, text: str) -> dict[str, str]:
        """Each concept found, with the words that named it the first time, as the text writes them (a compound: the
        whole word)."""
        found, raw = {}, RAW.findall(str(text or ""))
        for tok in raw:
            for c in self.short.get(tok, ()):
                found.setdefault(c, tok)
        ws = norm(text).split()
        said = raw if len(raw) == len(ws) else ws  # its own spelling, where its words line up with the lower-cased ones
        for i, w in enumerate(ws):
            for c in self.words.get(w, ()):
                found.setdefault(c, said[i])
            for n in range(2, self.first.get(w, 1) + 1):
                for c in self.words.get(" ".join(ws[i:i + n]), ()):
                    found.setdefault(c, " ".join(said[i:i + n]))
            if len(w) > 6 and self.lens:
                for c in self.compound(w):
                    found.setdefault(c, said[i])
        return found

    def compound(self, w: str) -> set[str]:
        if w not in self.cache:
            got = set()
            for n in self.lens:
                if n >= len(w):
                    break
                got |= self.ends.get(w[:n], set()) | self.ends.get(w[-n:], set())
            self.cache[w] = got
        return self.cache[w]


def heading(line: str, stop: bool = False) -> str:
    """The kind of section a short line opens ("Dein Profil" -> req, "Wir bieten" -> about), '' for any other line.
    stop: a full stop may end it too, on a line of at most 4 words (postings whose headings end with one: "Deine Aufgaben.";
    "Kenntnisse in Docker sind von Vorteil." stays a line)."""
    bare = line.strip().rstrip(" :?!" + ("." if stop else "")).strip()
    if not bare or len(bare) > 70 or len(bare.split()) > (4 if stop else 7) or bare.endswith(".") or "," in bare:
        return ""
    return next((kind for kind, rx in HEADINGS if rx.search(bare)), "")


def units(desc: str) -> list[tuple[float, str]]:
    """(weight, line) for what a posting asks and tells, from its raw text: under a requirements heading 1, tasks and
    nice-to-haves 0.5 (a "nice to have" line anywhere too), company story and benefits 0; a posting without such headings
    0.8 a line. With requirement or task headings, what comes before the first one is the company's story. Long lines
    are split into sentences; the same line counts once; at most MAX_UNITS, the heaviest first."""
    lines = [re.sub(r"^[\s\-–•·*▪◦●✓✔►>#]+|\*\*", "", line).strip() for line in str(desc or "").splitlines()]
    kinds = [heading(line) for line in lines]
    dotted = [heading(line, stop=True) if line.endswith(".") else k for line, k in zip(lines, kinds)]
    stops = sum(a != b for a, b in zip(dotted, kinds)) >= 2  # its headings end with a full stop: else "Warum wir?" would
    kinds = dotted if stops else kinds                         # swallow the rest as company story ("Deine Aufgaben.")
    section = "about" if {"req", "task"} & set(kinds) else "none"
    out, seen = [], set()
    for line, kind in zip(lines, kinds):
        if kind and not (stops and line.endswith(".") and kind == section):  # "Du bringst Erfahrung mit." under "Dein
            section = kind                                                  # Profil." is a requirement, not a heading
            continue
        for s in SENTENCE.split(line) if len(line) > 300 else [line]:
            key = " ".join(norm(s).split())
            w = WEIGHT[section]
            if len(key) < 2 or key in seen or not w:
                continue
            seen.add(key)
            out.append((min(w, WEIGHT["nice"]) if NICE.search(s) else w, s))
    return sorted(out, key=lambda u: -u[0])[:MAX_UNITS]


def cv_text(profile: dict) -> str:
    """The CV as it shows: summaries, entry titles and lines, education, skills, certificates (never tags or ids)."""
    entries = [e for sec in ("experience", "projects", "volunteering", "courses") for e in profile.get(sec) or []]
    entries += [e for c in profile.get("custom") or [] for e in c.get("entries") or []]
    out = [x for e in entries for x in (e.get("title", ""), *(b.get("text", "") for b in e.get("bullets") or []))]
    out += [str(s) for s in (profile.get("summaries") or {}).values()]
    out += [f"{e.get('degree', '')}. {e.get('details', '')}" for e in profile.get("education") or []]
    out += [s for cat, items in (profile.get("skills") or {}).items() for s in (cat, *items)]
    out += [i.get("name", "") for i in profile.get("certifications") or []]
    return "\n".join(map(str, out))


def fresh(job: dict) -> bool:
    """Posted within MAX_AGE days: older ones are likely ghost postings. first_seen catches a missing posted date."""
    from jobgambit.core import sources
    return all(sources.posted_ok(job.get(k) or "", MAX_AGE) for k in ("posted", "first_seen"))


@lru_cache
def language_codes() -> dict:
    """'german' / 'deutsch' / 'de' -> 'de' (English and native names, from Babel's CLDR data)."""
    from babel import Locale
    out = {}
    for code, name in Locale("en").languages.items():
        if len(code) != 2:
            continue
        out[name.lower()] = out[code] = code
        try:
            out[Locale.parse(code).get_display_name(code).lower()] = code
        except Exception:
            pass
    return out


@lru_cache
def cities():
    """city name (and alternate names) -> (lat, lon, country), the most populous place per name. GeoNames data (CC-BY)."""
    import geonamescache
    out = {}
    for c in geonamescache.GeonamesCache().get_cities().values():
        for name in [c["name"], *(c.get("alternatenames") or [])]:
            key = name.lower()
            if key and (key not in out or c["population"] > out[key][3]):
                out[key] = (c["latitude"], c["longitude"], c["countrycode"], c["population"])
    return out


def place(text):
    """First known city in a location string: (lat, lon, country) or None."""
    for part in re.split(r"[,/;|()]+| - ", str(text or "")):
        hit = cities().get(part.strip().lower())
        if hit:
            return hit
    return None


def km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def posting_language(text):
    if len(text) < 200:
        return ""
    from langdetect import DetectorFactory, detect_langs
    DetectorFactory.seed = 0
    try:
        best = detect_langs(text[:3000])[0]
    except Exception:
        return ""
    return best.lang if best.prob >= 0.9 else ""  # unsure is "", never a penalty


def deadline(text):
    import dateparser
    m = re.search(DEADLINE_WORDS + r"\W{0,3}(.{6,40})", text, re.I)
    if not m:
        return ""
    found = re.search(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\.? \w+ \d{4}|\w+ \d{1,2},? \d{4}", m.group(1))
    when = dateparser.parse(found.group(0), settings={"PREFER_DAY_OF_MONTH": "first", "DATE_ORDER": "DMY"}) if found else None
    return when.date().isoformat() if when and when.year >= date.today().year - 1 else ""


class Matcher:
    """Built once per search from the person's preferences and profile. The skills it matches on are read at the first
    posting it judges (Matcher({}, {}) only tells job types apart)."""

    def __init__(self, prefs: dict, profile: dict):
        self.titles = {t.lower() for t in prefs.get("titles", []) if t.strip()}
        self.keywords = {k.lower() for words in prefs.get("keywords", {}).values() for k in words if k.strip()}
        self.profile, self.found = profile, None
        self.job_types = set(prefs.get("job_types") or ["full_time"])
        self.countries = [t["code"] for t in prefs.get("targets") or [] if t.get("code")]  # a posting with no country: theirs
        codes = language_codes()
        self.speaks = {codes.get(l["name"].lower()) for l in profile.get("languages", []) if LEVEL_OK.search(l.get("level", "") or "")} - {None}
        self.speaks_any = {codes.get(l["name"].lower()) for l in profile.get("languages", [])} - {None}
        self.years = experience_years(profile)
        self.places = [p for p in (place(x) for x in prefs.get("places", [])) if p]
        self.radius = prefs.get("radius_km") or 50
        self.remote = prefs.get("remote", "any")
        self.prefer = [c.lower() for c in prefs.get("prefer_companies") or []]
        self.avoid = [c.lower() for c in prefs.get("avoid_companies") or []]
        self.exclude = [w.lower() for w in prefs.get("exclude_words") or []]
        self.w = {"skills": 1.0, "location": 1.0, "seniority": 1.0, "language": 1.0, **(prefs.get("weights") or {})}
        # job-type words (Werkstudent, PhD...) say nothing about the work: left out of titles and phrases before comparing
        words = sorted({w for ws in self.rules().values() for w in ws}, key=len, reverse=True)
        self.typed = re.compile(r"(?<= )(?:" + "|".join(re.escape(w) + (r"\w*" if len(w) > 6 else "") for w in words) + r")(?= )")
        self.title_rx, self.keyword_rx = any_of(map(self.clean, self.titles)), any_of(map(self.clean, self.keywords))
        self.either_rx = any_of(map(self.clean, self.titles | self.keywords))
        self.cv = {}

    def rules(self, country="") -> dict[str, list[str]]:
        """Job type -> its title words: the worldwide ones and the country pack's (the posting's country, else theirs)."""
        rules = {k: list(v) for k, v in TYPES.items()}
        for cc in [country] if country else getattr(self, "countries", []):
            for k, words in (countries.pack(cc).get("job_types") or {}).items():
                rules.setdefault(k, []).extend(words)
        return rules

    def kind(self, title, country="", company=""):
        t = norm(title)
        found = next((k for k, words in self.rules(country).items() if any(f" {w} " in t or (len(w) > 6 and w in t) for w in words)), "full_time")
        if found == "full_time" and re.search(RESEARCH_ORG, company or "", re.I):  # universities and research institutes
            return "phd"
        return found

    def clean(self, text: str) -> str:
        """norm()'d, without job-type words and (m/w/d)."""
        return " " + " ".join(GENDER.sub(" ", self.typed.sub(" ", norm(text))).split()) + " "

    def skills(self):
        """(Index, have per concept, its name, whether a skill map is there): read once, at the first posting."""
        if self.found is None:
            from jobgambit.core import skillmap
            self.found = skillmap.concepts(self.profile)
        return self.found

    def role(self, title: str, raw_title: str, scored: list) -> float:
        """How the posting's title fits what the person looks for (one hit only, the best)."""
        index, have, _, _ = self.skills()
        if self.title_rx and self.title_rx.search(title):
            return ROLE[0]
        if self.keyword_rx and self.keyword_rx.search(title):
            return ROLE[1]
        asked = " ".join(norm(u) for w, _, _, u in scored if w >= WEIGHT["task"])  # requirements and tasks: a generic title
        if any(have.get(c) for c in index.find(raw_title)) or (self.either_rx and self.either_rx.search(asked)):
            return ROLE[2]
        return 0.0

    def assess(self, j: dict, desc: str = "", sim=None) -> tuple[dict, str | bool]:
        """sim: (similarity, closest CV entry, fit) from a second, meaning-based match; None = keywords only."""
        index, have, names, mapped = self.skills()
        title, text = norm(j["title"]), norm(desc)
        scored = []  # (weight, share of its skills the person has, the skills, the line)
        for w, u in units(desc):
            found = index.find(u)
            if not found and (NEUTRAL.search(u) or not re.search(r"[^\W\d_]{2}", u)):
                continue  # only soft skills, languages, hours, a degree, paperwork, or no words at all (an id, a date): left out
            scored.append((w, sum(have[c] for c in found) / len(found) if found else UNKNOWN[0 if mapped else 1], found, u))
        weight = sum(w for w, *_ in scored) + PRIOR
        cov = (sum(w * s for w, s, _, _ in scored) + 0.5 * PRIOR) / weight if weight else 0.5
        role = self.role(self.clean(j["title"]), j["title"], scored)
        kw = 100 * cov ** self.w["skills"] * (1 - ROLE_SHARE + ROLE_SHARE * role)  # a title-only posting tops out at 50
        must = [(s, found, u) for w, s, found, u in scored if w >= WEIGHT["none"]]
        missing = [names[c] for c in sorted({c for _, found, _ in must for c in found if not have[c]}, key=lambda c: names[c].lower())]
        if not mapped:  # no skill map: the tool-like words of the requirements none of the person's skills is named in
            from jobgambit.core.check import tech_tokens
            missing = sorted({t for _, found, u in must if not found for t in tech_tokens(u)}, key=str.lower)
        named = [s for s, found, _ in must if found]  # only lines that name a skill: "Experience in software development" can't be judged
        why = ([f"meets {sum(s >= 0.6 for s in named)} of {len(named)} skill requirement{'s' * (len(named) != 1)}"] if named else []) + (
            ["missing: " + ", ".join(missing[:4])] if missing else [])
        base, close = kw, False
        kind = self.kind(j["title"], j.get("country", ""), j.get("company", ""))
        word = lambda w, text: re.search(rf"(?<![a-zäöüß]){re.escape(w)}(?![a-zäöüß])", text.lower())  # "MAN" is not "Germany"
        if any(word(c, j.get("company", "")) for c in self.prefer):
            base = min(100, base + PREFERRED)
            why.append("preferred company")
        warn, m_sen, m_lang, m_far = [], 1.0, 1.0, 1.0  # multipliers, applied last: a warning always lowers the score
        if re.search(SENIOR, title) and self.years < 5:
            m_sen = 1 - PENALTY["senior"] * self.w["seniority"]
            warn.append("senior role")
        asked = max((int(m[1]) for m in re.finditer(YEARS, text) if int(m[1]) <= 15
                     and re.search(r"experience|erfahrung|expérience|experiencia", text[max(0, m.start() - 60):m.end() + 60])), default=0)
        if asked >= self.years + 2:
            m_sen = min(m_sen, 1 - PENALTY["years3" if asked >= self.years + 3 else "years2"] * self.w["seniority"])
            warn.append(f"asks {asked}+ years")
        lang = posting_language(desc)
        if lang and self.speaks_any and lang not in self.speaks and lang not in self.speaks_any:
            warn.append(f"posting in {lang}")
        for language, pattern in (countries.pack(j.get("country", "")).get("language_required") or {}).items():
            code = language_codes().get(language.lower())
            if code not in self.speaks and re.search(pattern, text):
                warn.append(f"{language} required")
        if any(w.startswith("posting in") or w.endswith("required") for w in warn):
            m_lang = 1 - PENALTY["language"] * self.w["language"]  # once, however many
        if self.places and self.remote != "remote":
            here = place(j.get("location", ""))
            if here and min(km(here, p) for p in self.places) > self.radius and "remote" not in title + norm(j.get("location")):
                m_far = 1 - PENALTY["far"] * self.w["location"]
                warn.append("far away")
        score = max(0, round(base * m_sen * m_lang * m_far))
        if not (cov == 1 and role == 1 and not warn):  # 100 means all of it: never from smoothing or a bonus
            score = min(score, 99)
        when = deadline(desc)
        fields = {"score": score, "type": kind, "language": lang, "deadline": when, "why": " · ".join(why + [f"⚠ {w}" for w in warn])}
        today = date.today().isoformat()
        if when and when < today:
            return fields, "expired"
        if any(word(c, j.get("company", "")) for c in self.avoid) or any(word(w, j["title"]) for w in self.exclude):
            return fields, "excluded"
        if kind not in self.job_types and not (kind in ("graduate", "phd") and self.job_types & FULL):  # research jobs show under Jobs if not picked
            return fields, "type"
        if not fresh(j) and not (when and when >= today):  # a ghost posting: still listed, long filled
            return fields, "old"
        if not role and not any(s >= 0.6 for _, s, _, _ in scored) and not close:  # off-topic: no title, no requirement, no likeness
            return fields, "junk"
        return fields, "weak" if kw < BANDS["possible"] and not close else False  # weak by keywords, and not like their work


def experience_years(profile) -> float:
    """Years of work experience from the profile's dated jobs (overlaps counted once). Working-student and student-team
    months count half, so "asks 3 years" compares fairly."""
    def month(v, end=False):
        v = str(v or "")
        if v.lower() == "present":
            return date.today().year * 12 + date.today().month
        m = re.match(r"(\d{4})(?:-(\d{2}))?", v)
        return int(m[1]) * 12 + (int(m[2]) if m[2] else (12 if end else 1)) if m else None
    months = {}
    for e in profile.get("experience", []):
        a, b = month(e.get("start")), month(e.get("end"), True)
        if a and b:
            w = 0.5 if STUDENT_WORK.search(" ".join(str(e.get(k) or "") for k in ("title", "org", "unit"))) else 1.0
            for m in range(a, b + 1):
                months[m] = max(months.get(m, 0), w)
    return sum(months.values()) / 12
