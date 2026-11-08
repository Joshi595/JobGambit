"""Run one AI step on the person's own AI plan, through that vendor's command-line tool.

Answer runs: the app sends the whole prompt on stdin, the AI gets no tools and must answer
with JSON matching a pydantic model. The app validates the answer and retries once.
"""
import glob
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import date, timedelta
from pydantic import BaseModel, ValidationError

LIMIT = re.compile(r"usage limit|session limit|weekly limit|hit your \S+ limit|\d+-hour limit|rate.?limit|limit reached|quota|"
                   r"too many requests", re.I)
SIGNED_OUT = re.compile(r"not (?:signed|logged) in|log ?in\b|sign ?in\b|api key|unauthori[sz]ed|\b401\b|"
                        r"(?:token|session|login) (?:has )?expired", re.I)
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
PICTURES = frozenset({".png", ".jpg", ".gif", ".webp"})


class AIError(Exception):
    pass


class UsageLimit(AIError):
    """The person's plan limit is reached; the queue should wait for the reset."""


class BrowserPaused(AIError):
    """The browser guard saved a stalled tab. Submission state must be checked before another send."""


def problem(agent, said: str) -> AIError:
    """What the AI app said went wrong, with what to do first for the ones people meet: the plan's limit, not signed in."""
    if LIMIT.search(said):
        return UsageLimit(f"Your {agent.label} plan's usage limit is reached. Try again after it resets, or sign in with "
                          f"another plan (Help › Sign in). {agent.label} said: {said}")
    if SIGNED_OUT.search(said):
        return AIError(f"{agent.label} isn't signed in. Open Help, press Sign in next to {agent.name}, log in once, then try "
                       f"again. {agent.label} said: {said}")
    return AIError(f"{agent.label}: {said}")


def places() -> list[str]:
    """Where AI apps' installers put their programs. JobGambit may have started with an older PATH (its installer starts
    it, an AI app installed since isn't on it): these are looked in too."""
    home = os.path.expanduser("~")
    dirs = [os.path.join(home, ".local", "bin"), "/opt/homebrew/bin", "/usr/local/bin"]
    if sys.platform == "win32":
        dirs += [os.path.join(os.environ[k], *sub) for k, sub in (("APPDATA", ["npm"]), ("LOCALAPPDATA", ["Microsoft", "WinGet", "Links"]))
                 if os.environ.get(k)]
        try:  # the user's PATH as it is now, not as it was when JobGambit started
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                dirs += os.path.expandvars(winreg.QueryValueEx(key, "Path")[0]).split(os.pathsep)
        except OSError:
            pass
    return dirs


def find(exe: str) -> str | None:
    return shutil.which(exe) or shutil.which(exe, path=os.pathsep.join(places()))


def stop(p: subprocess.Popen) -> str:
    """Ends a run that took too long, with every program it started, and returns what it printed. On Windows a .cmd
    launcher's node.exe outlives the launcher and holds the pipes open: waiting for them would wait forever."""
    if sys.platform == "win32":
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, creationflags=NO_WINDOW)
        except OSError:
            pass
    p.kill()
    try:
        return p.communicate(timeout=5)[0] or ""
    except subprocess.TimeoutExpired:
        return ""


class Agent:
    name = exe = label = ""
    level = "standard"  # economy | standard | premium (Settings -> AI), mapped to a model where the CLI allows it
    answer_env: dict = {}  # extra environment for answer runs
    reads: frozenset = frozenset()  # files it can read itself (a scanned CV), by extension

    def path(self) -> str | None:
        return find(self.exe)

    def command(self, schema: dict, step: str = "", folder: str = "") -> list[str]:
        """folder: the run's own folder, with only the files given to run() in it."""
        raise NotImplementedError

    def reply(self, stdout: str, folder: str) -> str:
        """The answer's text: what the CLI printed (Codex: the file it was told to write it to)."""
        return stdout

    def extract(self, stdout: str) -> dict:
        return json.loads(stdout)

    def usage(self, stdout: str) -> dict:
        """Tokens and cost of one call, when the CLI reports them."""
        return {}

    def steps(self, stdout: str) -> list[dict]:
        """One entry per API call inside the run, when the CLI reports them."""
        return []

    def failed(self, stdout: str) -> str:
        """An answer that is an error report (the call still used the plan)."""
        return ""


    def call(self, args: list[str], prompt: str, timeout: int, step: str, key: str, env: dict | None = None, cwd: str | None = None,
             mode: str = "", pause_file: str = "") -> str:
        """One CLI call, recorded in the ai_calls ledger whatever happens (a failed call uses plan limits too). Output that
        isn't valid UTF-8 is read anyway; an error says what the CLI said, as its own result line says it (Claude)."""
        start, out, err = time.time(), "", ""
        try:
            p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
                                 env={**os.environ, **env} if env else None, cwd=cwd)
            try:
                if not pause_file:
                    out, stderr = p.communicate(prompt, timeout=timeout)
                else:
                    first, deadline = True, time.monotonic() + timeout
                    while True:
                        try:
                            out, stderr = p.communicate(prompt if first else None, timeout=min(1, max(0.01, deadline - time.monotonic())))
                            break
                        except subprocess.TimeoutExpired:
                            first = False
                            if os.path.isfile(pause_file):
                                out, err = stop(p), "Browser paused after repeated failures without progress"
                                raise BrowserPaused(err)
                            if time.monotonic() >= deadline:
                                raise
            except subprocess.TimeoutExpired as e:
                out, err = stop(p), f"no answer within {timeout} s"
                if not out and e.output:
                    out = e.output.decode("utf-8", errors="replace") if isinstance(e.output, bytes) else e.output
                raise AIError(f"{self.label} didn't answer within {timeout} seconds. Try again; if that happens again, "
                              "check it with Help › Test.")
            if pause_file and os.path.isfile(pause_file):
                err = "Browser paused after repeated failures without progress"
                raise BrowserPaused(err)
            if p.returncode or not out.strip():
                err = self.failed(out) or (stderr or out).strip()[-500:] or "no answer"
                raise problem(self, err)
            return out
        except OSError as e:  # the program is gone, or isn't one
            err = str(e)
            raise AIError(f"{self.label} couldn't be started ({e}). Install it again (Help shows how), then press "
                          "Check again.") from e
        finally:
            system_prompt = ""
            try:
                if "--system-prompt-file" in args:
                    with open(args[args.index("--system-prompt-file") + 1], encoding="utf-8") as f:
                        system_prompt = f.read()
                elif "--system-prompt" in args:
                    system_prompt = args[args.index("--system-prompt") + 1]
            except (OSError, IndexError):
                logging.getLogger(__name__).warning("Could not capture AI system prompt")
            schema_chars = len(args[args.index("--json-schema") + 1]) if "--json-schema" in args else 0
            record(step, key, self, out, time.time() - start, err, prompt=prompt, mode=mode,
                   system_prompt=system_prompt, schema_chars=schema_chars)

    def run(self, prompt: str, schema: dict, timeout: int = 600, step: str = "", key: str = "", files: dict | None = None) -> dict:
        """files: {name: bytes} put in the run's folder for the AI to read itself (only the kinds it reads)."""
        exe = self.path()
        if not exe:
            raise AIError(f"{self.label} isn't installed on this computer. Install it (Help shows how), then press Check again.")
        # an empty folder to start in: an AI that has tools (Codex, Antigravity) finds nothing of the person's around it.
        # A program still holding a file in it after a timeout doesn't turn the cleanup into an error.
        with tempfile.TemporaryDirectory(prefix="jobgambit-ai-", ignore_cleanup_errors=True) as folder:
            for name, data in (files or {}).items():
                with open(os.path.join(folder, name), "wb") as f:
                    f.write(data)
            out = self.call([exe, *self.command(schema, step, folder)], prompt, timeout, step, key, self.answer_env, cwd=folder)
            out = self.reply(out, folder)
        try:
            return self.extract(out)
        except AIError:
            raise
        except Exception as e:  # not JSON, or JSON of another kind
            raise AIError(f"{self.label} answered something that isn't the JSON asked for ({type(e).__name__}: {e}). "
                          "Try again; if that happens again, check it with Help › Test.") from e


class Claude(Agent):
    name, exe, label = "claude", "claude", "Claude"
    # An answer run is one turn on a prompt never sent again: a cache write would cost twice the plain input
    # (measured 2026-10-21: $0.060 cached vs $0.031 uncached for the same 7k-token prompt).
    answer_env = {"DISABLE_PROMPT_CACHING": "1"}
    reads = PICTURES | {".pdf"}

    def path(self):
        # ponytail: falls back to the VS Code extension's bundled binary (Joshi's PC); real users install the CLI.
        found = super().path() or sorted(glob.glob(os.path.expanduser(
            "~/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude*")))
        return found if isinstance(found, str) else (found[-1] if found else None)

    def command(self, schema, step="", folder=""):
        # No tools, no MCP, none of the person's own settings, a one-line system prompt (the playbook carries the
        # instructions): one lean model call. Standard is Sonnet throughout, the CV and letter too: with the CV rules of
        # 2026-11-06 a blind comparison found them as good as Opus's (5 ties of 5) at 56% less. Premium is Opus throughout.
        # A file to read (a scanned CV) in its folder: the Read tool only, which reads nothing outside that folder unasked.
        model = {"economy": "haiku", "premium": "opus"}.get(self.level) or "sonnet"
        tools = "Read" if folder and os.listdir(folder) else ""
        effort = ["--effort", "medium"] if step in {"Review", "Change", "Fill"} else []
        return ["-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages", "--json-schema", json.dumps(schema), "--tools", tools,
                "--system-prompt", "Answer only with JSON matching the given schema.",
                "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence", *effort, "--model", model]

    def extract(self, stdout):
        out = result(stdout)
        if out.get("is_error"):
            raise problem(self, str(out.get("result")))
        return out["structured_output"]

    def failed(self, stdout):
        out = result(stdout)
        return str(out.get("result"))[:500] if out.get("is_error") else ""

    def usage(self, stdout):
        r, steps = result(stdout), self.steps(stdout)
        models = r.get("modelUsage") or {}
        if not models and not steps:
            return {}
        sums = {k: sum(s[k] or 0 for s in steps) for k in ("fresh", "cache_write", "cached", "output", "thinking")}
        if not steps or any(s["thinking"] is None for s in steps):
            sums["thinking"] = None
        if models:
            name = max(models, key=lambda m: models[m].get("costUSD") or 0)
            sums.update({k: sum(v.get(field, 0) for v in models.values()) for k, field in
                         (("fresh", "inputTokens"), ("cache_write", "cacheCreationInputTokens"),
                          ("cached", "cacheReadInputTokens"), ("output", "outputTokens"))})
            final_usage = r.get("usage") if isinstance(r.get("usage"), dict) else {}
            thinking = (final_usage.get("output_tokens_details") or {}).get("thinking_tokens")
            model_thinking = [v.get("thinkingTokens") for v in models.values()]
            if all(v is not None for v in model_thinking):
                sums["thinking"] = sum(model_thinking)
            elif thinking is not None:
                sums["thinking"] = thinking
            cost = r.get("total_cost_usd")
            if cost is None and all(v.get("costUSD") is not None for v in models.values()):
                cost = sum(v["costUSD"] for v in models.values())
        else:  # a run cut short: what its finished API calls reported
            name, cost = steps[-1]["model"], r.get("total_cost_usd")
        cache_5m = sum(v.get("cacheCreationEphemeral5mInputTokens", 0) for v in models.values()) if models else sum(s["cache_5m"] for s in steps)
        cache_1h = sum(v.get("cacheCreationEphemeral1hInputTokens", 0) for v in models.values()) if models else sum(s["cache_1h"] for s in steps)
        return {"model": name, "input": sums["fresh"] + sums["cache_write"] + sums["cached"], **sums,
                "cost_usd": round(cost, 6) if cost is not None else None,
                "usage_status": "complete" if models and sums["thinking"] is not None else "partial", "cost_status": "reported" if cost is not None else "unknown",
                "cache_5m": cache_5m, "cache_1h": cache_1h, "api_calls": len(steps),
                "turns": r.get("num_turns") or len(steps), "api_seconds": round((r.get("duration_api_ms") or 0) / 1000, 1),
                "models": {m: {"in": v.get("inputTokens", 0), "cache_write": v.get("cacheCreationInputTokens", 0),
                               "cached": v.get("cacheReadInputTokens", 0), "out": v.get("outputTokens", 0),
                               "thinking": v.get("thinkingTokens"), "cache_5m": v.get("cacheCreationEphemeral5mInputTokens"),
                               "cache_1h": v.get("cacheCreationEphemeral1hInputTokens"), "cost": v.get("costUSD")} for m, v in models.items()}}

    def steps(self, stdout):
        """One entry per API call of a stream-json run: its tokens, the tools it called, and result_chars, the size of
        the tool results it read (what a page snapshot cost)."""
        out, by_id, pending, active, deltas = [], {}, 0, {}, set()

        def update(s, u):
            for key, field in (("fresh", "input_tokens"), ("cache_write", "cache_creation_input_tokens"),
                               ("cached", "cache_read_input_tokens"), ("output", "output_tokens")):
                if field in u:
                    s[key] = max(s[key], u[field] or 0)
            thinking = (u.get("output_tokens_details") or {}).get("thinking_tokens")
            if thinking is not None:
                s["thinking"] = max(s["thinking"] or 0, thinking)
            split = u.get("cache_creation") or {}
            for key, field in (("cache_5m", "ephemeral_5m_input_tokens"), ("cache_1h", "ephemeral_1h_input_tokens")):
                s[key] = max(s[key], split.get(field) or 0)

        def entry(mid, model, u):
            nonlocal pending
            if mid and mid in by_id:
                s = by_id[mid]
            else:
                s = {"n": len(out) + 1, "message_id": mid or "", "model": model, "tools": [], "result_chars": pending,
                     **{k: 0 for k in ("fresh", "cache_write", "cached", "output", "thinking", "cache_5m", "cache_1h")},
                     "usage_status": "partial"}
                s["thinking"] = None
                out.append(s)
                pending = 0
                if mid:
                    by_id[mid] = s
            update(s, u)
            return s

        for r in lines(stdout):
            if r.get("type") == "stream_event":
                event = r.get("event") or {}
                lane = r.get("parent_tool_use_id") or "main"
                if event.get("type") == "message_start":
                    m = event.get("message") or {}
                    active[lane] = entry(m.get("id"), m.get("model", ""), m.get("usage") or {})
                elif event.get("type") == "message_delta" and lane in active:
                    update(active[lane], event.get("usage") or {})
                    if "output_tokens" in (event.get("usage") or {}):
                        deltas.add(active[lane]["n"])
                elif event.get("type") == "message_stop" and lane in active:
                    s = active.pop(lane)
                    if s["n"] in deltas and s["thinking"] is not None:
                        s["usage_status"] = "complete"
                continue
            msg = r.get("message") if isinstance(r.get("message"), dict) else {}
            blocks = [b for b in msg.get("content") or [] if isinstance(b, dict)] if isinstance(msg.get("content"), list) else []
            if r.get("type") == "assistant" and msg.get("usage"):
                tools = [b.get("name", "") for b in blocks if b.get("type") == "tool_use"]
                s = entry(msg.get("id"), msg.get("model", ""), msg["usage"])
                s["tools"] = list(dict.fromkeys(s["tools"] + tools))
            elif r.get("type") == "user":
                for b in blocks:
                    if b.get("type") == "tool_result":
                        c = b.get("content")
                        pending += len(c) if isinstance(c, str) else sum(len(x.get("text") or "") for x in c or [] if isinstance(x, dict))
        # Old logs have only preliminary assistant usage. A model with exactly one message can be recovered exactly;
        # multiple messages can only be declared complete when their totals already reconcile. Never spread a residual.
        completed = result(stdout)
        for model, totals in (completed.get("modelUsage") or {}).items():
            group = [s for s in out if s["model"] == model]
            fields = {"fresh": "inputTokens", "cache_write": "cacheCreationInputTokens", "cached": "cacheReadInputTokens", "output": "outputTokens"}
            if len(group) == 1:
                for key, field in fields.items():
                    group[0][key] = totals.get(field, 0)
                if "thinkingTokens" in totals:
                    group[0]["thinking"] = totals["thinkingTokens"]
                elif len(completed.get("modelUsage") or {}) == 1:
                    final_usage = completed.get("usage") or {}
                    if isinstance(final_usage, dict):
                        group[0]["thinking"] = (final_usage.get("output_tokens_details") or {}).get("thinking_tokens", group[0]["thinking"])
                group[0]["cache_5m"] = totals.get("cacheCreationEphemeral5mInputTokens", group[0]["cache_5m"])
                group[0]["cache_1h"] = totals.get("cacheCreationEphemeral1hInputTokens", group[0]["cache_1h"])
                group[0]["usage_status"] = "complete" if group[0]["thinking"] is not None else "partial"
            elif group and all(s["thinking"] is not None for s in group) and all(sum(s[key] for s in group) == totals.get(field, 0) for key, field in fields.items()):
                for s in group:
                    s["usage_status"] = "complete"
        return out



def _pairs(s: dict) -> bool:
    """A free-form dict (dict[str, X]): an object with no properties."""
    return s.get("type") == "object" and "properties" not in s


def strict(schema: dict) -> dict:
    """The schema as OpenAI's strict mode wants it (Codex): every object closed with every property required (optional
    ones nullable), a free-form dict as a list of {key, value} pairs, no defaults. loosen() turns the answer back."""
    def walk(s):
        if not isinstance(s, dict):
            return s
        if "$ref" in s:
            return {"$ref": s["$ref"]}  # no sibling keywords next to a $ref
        # "title" / "default" are dropped as keywords, never as field names (a custom section's title)
        s = {k: {p: walk(x) for p, x in v.items()} if k == "properties" else walk(v) if isinstance(v, dict)
             else [walk(x) for x in v] if k == "anyOf" else v for k, v in s.items() if k not in ("default", "title")}
        if _pairs(s):
            value = s["additionalProperties"] if isinstance(s.get("additionalProperties"), dict) else {"type": "string"}
            return {"type": "array", **({"description": s["description"]} if "description" in s else {}),
                    "items": {"type": "object", "properties": {"key": {"type": "string"}, "value": value},
                              "required": ["key", "value"], "additionalProperties": False}}
        if s.get("type") == "object":
            req = set(s.get("required", []))
            s["properties"] = {k: v if k in req else {"anyOf": [v, {"type": "null"}]} for k, v in s["properties"].items()}
            s["required"], s["additionalProperties"] = list(s["properties"]), False
        return s
    out = walk(schema)
    if "$defs" in schema:
        out["$defs"] = {k: walk(v) for k, v in schema["$defs"].items()}
    return out


def loosen(value, schema: dict, defs: dict | None = None):
    """A strict() answer back in the original schema's shape: pairs -> dict, null optional fields left out."""
    defs = schema.get("$defs", {}) if defs is None else defs
    if "$ref" in schema:
        schema = defs[schema["$ref"].split("/")[-1]]
    if "anyOf" in schema and value is not None:
        kind = {dict: ("object",), list: ("array", "object")}.get(type(value), ())
        schema = next((b for b in schema["anyOf"] if "$ref" in b or b.get("type") in kind), {})
        return loosen(value, schema, defs) if schema else value
    if _pairs(schema) and isinstance(value, list):
        sub = schema.get("additionalProperties") if isinstance(schema.get("additionalProperties"), dict) else {}
        return {p["key"]: loosen(p["value"], sub, defs) for p in value}
    if schema.get("type") == "object" and isinstance(value, dict):
        req, props = set(schema.get("required", [])), schema.get("properties", {})
        return {k: loosen(v, props.get(k, {}), defs) for k, v in value.items() if v is not None or k in req}
    if schema.get("type") == "array" and isinstance(value, list):
        return [loosen(v, schema.get("items", {}), defs) for v in value]
    return value


class Codex(Agent):
    """Unverified: not installed on the dev machine yet (see docs/spike.md). Codex answers in OpenAI's strict mode,
    which refuses optional fields and free-form dicts: the schema goes through strict() and the answer through loosen()."""
    name, exe, label = "codex", "codex", "ChatGPT (Codex)"
    reads = PICTURES  # attached with -i
    ANSWER = "answer.json"  # in the run's folder: the final message alone, whatever else it prints

    def command(self, schema, step="", folder=""):
        # One file per schema, named by its content: reused across calls, so files don't pile up. In the data folder,
        # not the shared temp folder, where another user of the computer could put a file of that name first.
        from jobgambit.core.data import home
        text = json.dumps(schema, sort_keys=True)
        f = home() / "cache" / f"schema-{hashlib.sha1(text.encode()).hexdigest()[:12]}.json"
        if not f.exists():
            f.parent.mkdir(exist_ok=True)
            f.write_text(text, encoding="utf-8")
        pictures = [os.path.join(folder, n) for n in sorted(os.listdir(folder)) if os.path.splitext(n)[1] in self.reads] if folder else []
        # -i first: it takes several files and would take the prompt's "-" for one
        return ["exec", *(["-i", ",".join(pictures)] if pictures else []), "--output-schema", str(f), "--sandbox", "read-only",
                "--skip-git-repo-check", *(["--output-last-message", os.path.join(folder, self.ANSWER)] if folder else []), "-"]

    def reply(self, stdout, folder):
        try:
            with open(os.path.join(folder, self.ANSWER), encoding="utf-8", errors="replace") as f:
                return f.read() or stdout
        except OSError:
            return stdout

    def run(self, prompt, schema, timeout=600, step="", key="", files=None):
        out = super().run(prompt, strict(schema), timeout, step, key, files)
        try:
            return loosen(out, schema)
        except Exception as e:  # an answer in neither shape
            raise AIError(f"{self.label} gave an answer JobGambit can't read: {e}") from e


class Antigravity(Agent):
    """Unverified. Reported to print nothing without a terminal: may need a pseudo-terminal."""
    name, exe, label = "antigravity", "agy", "Google (Antigravity)"

    def command(self, schema, step="", folder=""):
        return ["-p", "--output-format", "json", "--json-schema", json.dumps(schema)]

    def extract(self, stdout):
        return json.loads(stdout)["structured_output"]


AGENTS = {a.name: a for a in (Claude(), Codex(), Antigravity())}


def installed() -> dict[str, bool]:
    return {name: bool(a.path()) for name, a in AGENTS.items()}


def chosen(name: str = "", level: str = "") -> Agent:
    """The person's chosen AI (and cost level from their settings), else the first one installed."""
    if not level:
        from jobgambit.core import profile as pf
        level = pf.read("preferences").get("cost_level", "standard")
    for a in ([AGENTS[name]] if name in AGENTS else []) + list(AGENTS.values()):
        if a.path():
            a.level = level
            return a
    raise AIError("No AI is installed. Open Help to set one up.")


def answer(prompt: str, model: type[BaseModel], agent: Agent, timeout: int = 600, key: str = "", files: dict | None = None) -> BaseModel:
    """key: the job this call is for, so the ledger can add up each application (a retry is a second call).
    files: {name: bytes} for the AI to read itself (see Agent.run)."""
    schema = model.model_json_schema()
    for attempt in range(2):
        try:
            return model.model_validate(agent.run(prompt, schema, timeout, model.__name__, key, files))
        except ValidationError as e:
            if attempt:
                wrong = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:3])
                raise AIError(f"{agent.label} answered twice in a shape JobGambit can't use. Try again; if that happens "
                              f"again, check it with Help › Test or choose another AI in Settings › AI. ({wrong})")
            prompt += f"\n\nYour previous answer did not match the required JSON schema:\n{e}\nAnswer again."


TESTED = "ai_tested.json"  # Help › Test's last result per AI app: one still in beta says "run Test first" until it passed


def tested() -> dict:
    from jobgambit.core.data import home
    try:
        return json.loads((home() / TESTED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def remember_test(name: str, ok: bool):
    from jobgambit.core.data import home
    (home() / TESTED).write_text(json.dumps({**tested(), name: ok}), encoding="utf-8")


# ---------------------------------------------------------------- the ledger: every AI call, kept to see where tokens go
BATCH = ""  # a batch of AI calls in progress (its start time) tags them
# ponytail: one module value, as one batch goes at a time (under its own lock); per-thread if batches ever overlap
LOG_DAYS = 30  # run logs (the prompt and the CLI's whole output) are kept this long, on this computer only


def lines(out: str) -> list[dict]:
    """The CLI's output as JSON objects: stream-json gives one per line, json one in all."""
    rows = []
    for line in (out or "").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def result(out: str) -> dict:
    """The run's final report (answer, totals per model, turns); {} when the run was cut short."""
    return next((r for r in reversed(lines(out)) if r.get("type") in ("result", None)), {})


def record(step, key, agent, out, seconds, error="", prompt="", mode="", outcome="", batch="", system_prompt="", schema_chars=0):
    """agent: the Agent (or its name, then without token numbers); out: the CLI's output; prompt: what was sent.
    The prompt's part sizes come from the last prompts.build() in this thread."""
    from jobgambit.core import prompts
    parts, prompts.LAST.parts = getattr(prompts.LAST, "parts", None), None  # one prompt's parts never stick to the next
    repair_reason, prompts.LAST.repair_reason = getattr(prompts.LAST, "repair_reason", ""), ""
    try:
        from jobgambit.core.data import db
        real = out and not isinstance(agent, str)
        u, steps = (agent.usage(out), agent.steps(out)) if real else ({}, [])
        error = error or (agent.failed(out) if real else "")
        answer = result(out).get("structured_output") if real else None
        outcome = outcome or str((answer or {}).get("status") or "" if isinstance(answer, dict) else "") or ("error" if error else "ok")
        values = {"step": step, "job_key": key, "agent": agent if isinstance(agent, str) else agent.name,
                  "model": u.get("model", ""), **{k: u.get(k, 0) for k in
                  ("input", "fresh", "cache_write", "cached", "output", "thinking", "turns", "api_seconds", "cache_5m", "cache_1h", "api_calls")},
                  "cost_usd": u.get("cost_usd", 0 if isinstance(agent, str) else None),
                  "usage_status": u.get("usage_status", "not_applicable" if isinstance(agent, str) else "unknown"),
                  "cost_status": u.get("cost_status", "reported" if isinstance(agent, str) else "unknown"),
                  "seconds": round(seconds, 1), "error": error, "prompt_chars": len(prompt or "") + len(system_prompt or ""),
                  "system_chars": len(system_prompt or ""), "user_chars": len(prompt or ""), "schema_chars": schema_chars,
                  "parts": json.dumps(parts) if parts else None, "models": json.dumps(u["models"]) if u.get("models") else None,
                  "mode": mode, "outcome": outcome, "batch": batch or BATCH, "repair_reason": repair_reason}
        with db() as con:
            call = con.execute(f"insert into ai_calls({','.join(values)}) values ({','.join('?' for _ in values)})", tuple(values.values())).lastrowid
            save_steps(con, call, steps)
            if out or prompt or system_prompt:
                con.execute("update ai_calls set log=? where id=?", (write_log(call, prompt, out, system_prompt), call))
    except Exception as e:
        # A broken ledger must be visible, but must never turn a successful send into a retry.
        logging.getLogger(__name__).warning("AI usage could not be recorded (%s)", type(e).__name__)


def save_steps(con, call, steps):
    fields = ("n", "result_chars", "fresh", "cache_write", "cached", "output", "thinking", "model", "message_id", "usage_status", "cache_5m", "cache_1h")
    con.executemany(f"insert into ai_steps(call_id,tools,{','.join(fields)}) values ({','.join('?' for _ in range(len(fields) + 2))})",
                    [(call, ",".join(s["tools"]), *(s[k] for k in fields)) for s in steps])


def write_log(call: int, prompt: str, out: str, system_prompt: str = "") -> str:
    """<data>/logs/ai/<day>/<call>.prompt.txt and .jsonl: what was sent and all the CLI said, to work out later where
    tokens went. Kept LOG_DAYS days; never sent anywhere. Returns the log's path in the data folder ('' if not written)."""
    try:
        from jobgambit.core import secrets
        from jobgambit.core.data import home
        root = home() / "logs" / "ai"
        day = root / date.today().isoformat()
        day.mkdir(parents=True, exist_ok=True)
        (day / f"{call}.prompt.txt").write_text(secrets.redact(prompt or ""), encoding="utf-8", newline="")
        if system_prompt:
            (day / f"{call}.system.txt").write_text(secrets.redact(system_prompt), encoding="utf-8", newline="")
        (day / f"{call}.jsonl").write_text(secrets.redact(out or ""), encoding="utf-8", newline="")
        cutoff = (date.today() - timedelta(days=LOG_DAYS)).isoformat()
        for d in root.iterdir():
            if d.is_dir() and d.name < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        return f"logs/ai/{day.name}/{call}.jsonl"
    except Exception:
        return ""


def calls(key: str) -> list[dict]:
    """One application's AI calls, oldest first."""
    from jobgambit.core.data import db
    with db() as con:
        return [dict(r) for r in con.execute("select * from ai_calls where job_key=? order by id", (key,))]
