"""Turn what the person already has into a draft profile: a CV file, a LinkedIn data export or a JSON Resume.

LinkedIn and JSON Resume map field by field (no AI). A CV file becomes text here; the AI structures it.
"""
import codecs
import csv
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
from jobgambit.core.ai import PICTURES  # noqa: E402 (a photo of a CV: no text in it, but an AI that reads pictures can take it)
PASTE = " Or paste the text of your CV below."
SAY = {  # what to do instead, by what the file turned out to be
    "": "JobGambit can't read this kind of file. Save your CV as PDF or Word (.docx), then upload that.",
    "empty": "This file is empty. Choose your CV file again.",
    ".doc": "This is an old Word file (.doc), or a Word file with a password. In Word, use Save as and pick Word (.docx) or "
            "PDF, without a password, then upload that.",
    ".rtf": "This is a Rich Text file (.rtf). Open it in Word or LibreOffice, use Save as and pick Word (.docx) or PDF, "
            "then upload that.",
    ".odt": "This is an OpenDocument file (.odt). In LibreOffice or Word, use Save as and pick Word (.docx) or PDF, then "
            "upload that.",
    ".zip": "This is a ZIP archive, not a CV. Upload the CV file itself (PDF or Word).",
    "password": "This PDF is protected with a password. Open it, save or print it as a new PDF without a password, then "
                "upload that.",
    "damaged": "This file seems damaged. Open it and save it again as PDF or Word (.docx), then upload that.",
    "linkedin": "This ZIP isn't a LinkedIn data export (there is no Profile.csv or Positions.csv in it). Upload your CV file "
                "itself, or the export LinkedIn sends you (Settings › Data privacy › Get a copy of your data).",
    "json": "This JSON file isn't a JSON Resume JobGambit can read. Upload your CV as PDF or Word instead.",
}


class Unreadable(Exception):
    """A file no CV can be taken from; the message says what to do instead."""

    def __init__(self, what: str):
        super().__init__(SAY[what] + PASTE)


def kind(data: bytes) -> str:
    """What a file really is, from its first bytes (its name can say otherwise): .pdf .docx .odt .zip .doc .rtf, one of
    PICTURES, .txt, or '' for anything else."""
    if b"%PDF" in data[:1024]:
        return ".pdf"
    if data.startswith((b"PK\x03\x04", b"PK\x05\x06")):  # an empty ZIP starts with its (empty) directory
        try:
            names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
        except zipfile.BadZipFile:
            return ".docx"  # most likely a Word file cut short: reading it says it's damaged
        return ".docx" if "word/document.xml" in names else ".odt" if "content.xml" in names else ".zip"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    for sign, ext in ((b"\xd0\xcf\x11\xe0", ".doc"), (b"{\\rtf", ".rtf"), (b"\x89PNG", ".png"), (b"\xff\xd8\xff", ".jpg"),
                      (b"GIF8", ".gif")):
        if data.startswith(sign):
            return ext
    return ".txt" if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) or b"\0" not in data[:8192] else ""


def cv_text(data: bytes) -> str:
    """A CV file's text, whatever its name says: PDF (pypdf), Word (its XML), plain text. '' for a photo, or a PDF with
    no text in it (a scan). Anything else raises Unreadable, saying what to do instead."""
    if not data.strip():
        raise Unreadable("empty")
    ext = kind(data)
    if ext in PICTURES:
        return ""
    read = {".pdf": _pdf, ".docx": _docx, ".txt": _txt}.get(ext)
    if not read:
        raise Unreadable(ext if ext in SAY else "")
    try:
        return read(data).strip()
    except Unreadable:
        raise
    except Exception as e:  # a damaged file: pypdf's, zipfile's and the XML parser's errors alike
        raise Unreadable("damaged") from e


def enough(text: str) -> bool:
    """Long enough to be a CV: 30 words, or 300 characters for writing without spaces between words (Chinese, Japanese)."""
    return len(text.split()) >= 30 or len("".join(text.split())) >= 300


def _pdf(data: bytes) -> str:
    """pypdf with its crypto extra: a PDF protected against copying or printing (AES) reads like any other."""
    from pypdf import PdfReader
    from pypdf.errors import FileNotDecryptedError

    def page(p):
        try:
            return (p.extract_text() or "").strip()
        except FileNotDecryptedError:
            raise
        except Exception:  # one broken page costs that page, not the CV
            return ""
    try:
        return "\n".join(t for t in map(page, PdfReader(io.BytesIO(data)).pages) if t)
    except FileNotDecryptedError:  # it needs a password to open
        raise Unreadable("password")


def _docx(data: bytes) -> str:
    """Word: its headers (contact details are often there), the body, then its footers."""
    z = zipfile.ZipFile(io.BytesIO(data))
    extra = sorted(n for n in z.namelist() if re.fullmatch(r"word/(header|footer)\d*\.xml", n))
    parts = [n for n in extra if "/header" in n] + ["word/document.xml"] + [n for n in extra if "/footer" in n]
    return "\n".join(dict.fromkeys(t for t in (_word_xml(z.read(n)) for n in parts) if t))  # a first-page header repeats the others


def _word_xml(xml: bytes) -> str:
    """One part of a Word file as text: a line per paragraph, tabs and line breaks kept. Tags go by their local name, so
    a Strict Open XML file reads too. Skipped: a text box's fallback copy (mc:Fallback repeats the box) and paragraph
    properties (their tab stops are w:tab as well)."""
    out = []

    def walk(el):
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("Fallback", "pPr", "rPr"):
            return
        out.append((el.text or "") if tag == "t" else "\t" if tag == "tab" else "\n" if tag in ("br", "cr") else "")
        for child in el:
            walk(child)
        if tag == "p":
            out.append("\n")
    walk(ET.fromstring(xml))
    return "".join(out).strip()


def _txt(data: bytes) -> str:
    """UTF-8 (with or without its mark), UTF-16 (by its mark), else Windows' own encoding (Notepad's "ANSI", cp1252)."""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", "replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def _month(value: str) -> str:
    """LinkedIn 'Oct 2022' / '2022' / ISO '2022-10-01' -> '2022-10' / '2022'."""
    value = str(value or "").strip()  # a bare year may come as a number
    if not value:
        return ""
    if re.match(r"\d{4}-\d{2}", value):
        return value[:7]
    m = re.match(r"([A-Za-z]{3})\w* (\d{4})", value)
    return f"{m[2]}-{MONTHS.get(m[1].lower(), 1):02d}" if m else value[:4]


def _bullets(text: str, prefix: str) -> list[dict]:
    lines = [l.strip(" -•*\t") for l in re.split(r"\n+", text or "") if l.strip(" -•*\t")]
    return [{"id": f"{prefix}_b{i}", "text": line, "tags": []} for i, line in enumerate(lines, 1)]


def from_linkedin(data: bytes) -> dict:
    """LinkedIn: Settings > Data privacy > Get a copy of your data. The ZIP holds one CSV per section."""
    z = zipfile.ZipFile(io.BytesIO(data))
    names = {n.rsplit("/", 1)[-1].lower(): n for n in z.namelist()}
    if not {"profile.csv", "positions.csv"} & set(names):
        raise Unreadable("linkedin")

    def rows(name):
        if name.lower() not in names:
            return []
        text = z.read(names[name.lower()]).decode("utf-8-sig", "replace")
        if text.startswith("Notes:"):  # some exports start with a notes paragraph before the header
            text = text.split("\n\n", 1)[-1]
        return list(csv.DictReader(io.StringIO(text)))

    me = (rows("Profile.csv") or [{}])[0]
    email = next((r.get("Email Address", "") for r in rows("Email Addresses.csv") if r.get("Primary") == "Yes"), "")
    phone = next((r.get("Number", "") for r in rows("PhoneNumbers.csv")), "")
    p = {"basics": {"name": f"{me.get('First Name', '')} {me.get('Last Name', '')}".strip(),
                    "headline": me.get("Headline", ""), "location": me.get("Geo Location", ""),
                    "email": email, "phone": phone, "links": {}},
         "summaries": {"main": me["Summary"]} if me.get("Summary") else {}}
    p["experience"] = [{"id": f"exp{i}", "title": r.get("Title", ""), "org": r.get("Company Name", ""),
                        "location": r.get("Location", ""), "start": _month(r.get("Started On")),
                        "end": _month(r.get("Finished On")) or "present", "bullets": _bullets(r.get("Description"), f"exp{i}")}
                       for i, r in enumerate(rows("Positions.csv"), 1)]
    p["education"] = [{"id": f"edu{i}", "degree": r.get("Degree Name", ""), "school": r.get("School Name", ""),
                       "start": _month(r.get("Start Date")), "end": _month(r.get("End Date")),
                       "details": " ".join(filter(None, [r.get("Notes"), r.get("Activities")]))}
                      for i, r in enumerate(rows("Education.csv"), 1)]
    p["projects"] = [{"id": f"proj{i}", "title": r.get("Title", ""), "link": r.get("Url", ""),
                      "start": _month(r.get("Started On")), "end": _month(r.get("Finished On")),
                      "bullets": _bullets(r.get("Description"), f"proj{i}")}
                     for i, r in enumerate(rows("Projects.csv"), 1)]
    p["certifications"] = [{"id": f"cert{i}", "name": r.get("Name", ""), "issuer": r.get("Authority", ""),
                            "date": _month(r.get("Started On")), "link": r.get("Url", "")}
                           for i, r in enumerate(rows("Certifications.csv"), 1)]
    skills = [r.get("Name", "") for r in rows("Skills.csv") if r.get("Name")]
    p["skills"] = {"Skills": skills} if skills else {}
    p["languages"] = [{"name": r.get("Name", ""), "level": r.get("Proficiency", "")} for r in rows("Languages.csv")]
    return p


def from_json_resume(data: bytes) -> dict:
    """jsonresume.org schema -> profile."""
    r = json.loads(data)
    b = r.get("basics", {})
    loc = b.get("location", {})
    links = {(x.get("network") or "link").lower(): x.get("url", "") for x in b.get("profiles", [])}
    if b.get("url"):
        links["website"] = b["url"]

    def entries(items, prefix, title, org):
        return [{"id": f"{prefix}{i}", "title": x.get(title, ""), "org": x.get(org, ""), "location": x.get("location", ""),
                 "start": _month(x.get("startDate")), "end": _month(x.get("endDate")) or "present", "link": x.get("url", ""),
                 "bullets": ([{"id": f"{prefix}{i}_s", "text": x["summary"], "tags": []}] if x.get("summary") else [])
                 + [{"id": f"{prefix}{i}_b{j}", "text": h, "tags": []} for j, h in enumerate(x.get("highlights", []), 1)]}
                for i, x in enumerate(items, 1)]

    return {
        "basics": {"name": b.get("name", ""), "headline": b.get("label", ""), "email": b.get("email", ""),
                   "phone": b.get("phone", ""), "links": links,
                   "location": ", ".join(filter(None, [loc.get("city"), loc.get("countryCode")]))},
        "summaries": {"main": b["summary"]} if b.get("summary") else {},
        "experience": entries(r.get("work", []), "exp", "position", "name"),
        "volunteering": entries(r.get("volunteer", []), "vol", "position", "organization"),
        "projects": entries(r.get("projects", []), "proj", "name", "entity"),
        "education": [{"id": f"edu{i}", "degree": " ".join(filter(None, [e.get("studyType"), e.get("area")])),
                       "school": e.get("institution", ""), "start": _month(e.get("startDate")),
                       "end": _month(e.get("endDate")), "details": e.get("score", "")}
                      for i, e in enumerate(r.get("education", []), 1)],
        "skills": {s.get("name", "Skills"): s.get("keywords", []) for s in r.get("skills", [])},
        "languages": [{"name": l.get("language", ""), "level": l.get("fluency", "")} for l in r.get("languages", [])],
        "certifications": [{"id": f"cert{i}", "name": c.get("name", ""), "issuer": c.get("issuer", ""),
                            "date": _month(c.get("date")), "link": c.get("url", "")}
                           for i, c in enumerate(r.get("certificates", []), 1)],
        "awards": [{"id": f"award{i}", "name": a.get("title", ""), "issuer": a.get("awarder", ""), "date": _month(a.get("date"))}
                   for i, a in enumerate(r.get("awards", []), 1)],
        "publications": [{"id": f"pub{i}", "name": x.get("name", ""), "issuer": x.get("publisher", ""),
                          "date": _month(x.get("releaseDate")), "link": x.get("url", "")}
                         for i, x in enumerate(r.get("publications", []), 1)],
        "interests": [i.get("name", "") for i in r.get("interests", [])],
    }


def to_json_resume(p: dict) -> dict:
    """Profile -> jsonresume.org schema, for use in other tools."""
    b = p.get("basics", {})

    def work(items, org_key, title_key):
        return [{org_key: e.get("org", ""), title_key: e.get("title", ""), "location": e.get("location", ""),
                 "startDate": e.get("start", ""), "endDate": "" if e.get("end") == "present" else e.get("end", ""),
                 "url": e.get("link", ""), "highlights": [re.sub(r"\*\*", "", x["text"]) for x in e.get("bullets", [])]}
                for e in items]

    return {
        "basics": {"name": b.get("name", ""), "label": b.get("headline", ""), "email": b.get("email", ""),
                   "phone": b.get("phone", ""), "summary": next(iter(p.get("summaries", {}).values()), ""),
                   "location": {"city": b.get("location", "")},
                   "profiles": [{"network": k, "url": v} for k, v in b.get("links", {}).items()]},
        "work": work(p.get("experience", []), "name", "position"),
        "volunteer": work(p.get("volunteering", []), "organization", "position"),
        "projects": [{"name": e.get("title", ""), "url": e.get("link", ""),
                      "highlights": [x["text"] for x in e.get("bullets", [])]} for e in p.get("projects", [])],
        "education": [{"institution": e.get("school", ""), "studyType": e.get("degree", ""),
                       "startDate": e.get("start", ""), "endDate": e.get("end", ""), "score": e.get("details", "")}
                      for e in p.get("education", [])],
        "skills": [{"name": k, "keywords": v} for k, v in p.get("skills", {}).items()],
        "languages": [{"language": l["name"], "fluency": l.get("level", "")} for l in p.get("languages", [])],
        "certificates": [{"name": c["name"], "issuer": c.get("issuer", ""), "date": c.get("date", "")}
                         for c in p.get("certifications", [])],
        "meta": {"generator": "JobGambit", "lastModified": datetime.now().isoformat(timespec="seconds")},
    }
