"""Load the AI instructions (playbooks). The person may override any file by putting their own copy
in <data folder>/playbooks/."""
import re
import threading
from pathlib import Path

from jobgambit.core.data import home

SHIPPED = Path(__file__).parent.parent / "playbooks"
LAST = threading.local()  # .parts: chars per part of the last prompt this thread built, for the AI ledger
# The steps that write for the person get their note (Settings › Writing › A note for every AI step); analysis steps
# (CV import, skill map, mail triage, ranking, second look…) never do.
WRITES = {"tailor", "review", "revise", "change", "followup", "interview", "brief", "feedback"}


def load(name: str) -> str:
    own = home() / "playbooks" / f"{name}.md"
    return (own if own.exists() else SHIPPED / f"{name}.md").read_text(encoding="utf-8")


def note() -> str:
    from jobgambit.core import profile as pf
    return str(pf.read("preferences").get("instructions") or "").strip()


def build(name: str, **parts: str) -> str:
    """Shared rules + the named playbook, with {part} placeholders filled in one pass: a value is never searched for
    placeholders itself, so a CV line or a posting can't pull another part (or the posting, outside its fence) in.
    A writing step gets the person's note: in its {instructions} line when it has one, else as a last line (none: nothing)."""
    text = load("rules") + "\n" + load(name)
    if name in WRITES and "instructions" not in parts:
        parts["instructions"] = note()
        if "{instructions}" not in text and parts["instructions"]:
            text += "\nTheir own note for every step (follow it where the facts allow): {instructions}\n"
        parts["instructions"] = parts["instructions"] or "(none)"
    LAST.parts = {"playbook": len(text), **{k: len(v) for k, v in parts.items()}}
    return re.sub(r"\{(\w+)\}", lambda m: parts.get(m[1], m[0]), text)
