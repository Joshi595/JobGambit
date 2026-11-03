import pytest

from jobgambit.core import render


@pytest.fixture(autouse=True)
def no_background_thumbs(monkeypatch):
    """A warm-up thread outliving its test would render into whatever JOBGAMBIT_HOME is set next (or the real one)."""
    monkeypatch.setattr(render, "warm_thumbs", lambda: None)




@pytest.fixture(autouse=True)
def nothing_runs_by_itself(monkeypatch):
    """The skill map is made by the person's AI app (their plan's tokens), and a CV or settings change re-judges the
    postings 20 s later in a thread: tests never start either by themselves (a thread outliving its test would run in
    whatever JOBGAMBIT_HOME is set next). A test that wants them patches them back."""
    from jobgambit.app import settings_api
    from jobgambit.core import ai, skillmap
    monkeypatch.setattr(skillmap, "ask", lambda prompt, agent: (_ for _ in ()).throw(ai.AIError("no AI app in tests")))
    monkeypatch.setattr(settings_api, "refresh_later", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def own_home(tmp_path, monkeypatch):
    """Every test gets its own data folder: nothing (e.g. the AI ledger) ever lands in the person's real one."""
    monkeypatch.setenv("JOBGAMBIT_HOME", str(tmp_path))


@pytest.fixture(autouse=True)
def no_real_tasks_or_notifications(monkeypatch):
    """Tests never create, change or delete this computer's scheduled tasks, and never pop up a notification on it."""
    from jobgambit.core import alerts, scheduler
    tasks = {}
    monkeypatch.setattr(scheduler, "current", lambda: dict(tasks))
    monkeypatch.setattr(scheduler, "_enable", lambda job, at: tasks.__setitem__(job, scheduler.line(job)))
    monkeypatch.setattr(scheduler, "_disable", lambda job: tasks.pop(job, None))
    monkeypatch.setattr(alerts, "desktop", lambda title, body: None)
