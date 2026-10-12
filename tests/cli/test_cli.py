"""CLI dispatch: what each command enqueues or calls (repositories and queue mocked)."""
from types import SimpleNamespace

import pytest

from reddit_signal.cli import main as cli
from reddit_signal.core.promotion import RunStats


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/db")


@pytest.fixture
def queue(monkeypatch):
    from reddit_signal import pipeline_jobs

    created = []

    def create_job(job_type, *, requested_by, request_source, params):
        created.append((job_type, params))
        return SimpleNamespace(id=200 + len(created), job_type=job_type)

    monkeypatch.setattr(pipeline_jobs, "create_job", create_job)
    return created


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["run", "daily"], ("daily", {})),
        (["run", "monthly"], ("monthly", {})),
        (["run", "monthly", "--force-promote"], ("monthly", {"force_promote": True})),
        (["run", "extraction", "--limit", "500"], ("extraction", {"limit": 500})),
        (["run", "extraction", "--since", "2026-07-01T00:00:00+00:00", "--until", "2026-09-27T00:00:00+00:00",
          "--limit", "50000", "--workers", "30"],
         ("extraction", {"mode": "backlog", "since": "2026-07-01T00:00:00+00:00",
                         "until": "2026-09-27T00:00:00+00:00", "limit": 50000, "workers": 30})),
    ],
)
def test_run_enqueues_jobs(queue, argv, expected, capsys):
    assert cli.main(argv) == 0
    assert queue == [expected]
    assert "queued" in capsys.readouterr().out


def test_backlog_range_needs_both_ends(queue):
    assert cli.main(["run", "extraction", "--since", "2026-07-01"]) == 2
    assert queue == []


def stats(run_id, clusters, candidates, members):
    return RunStats(run_id, clusters, candidates, members, clusters, clusters, 1, 1, True)


@pytest.fixture
def runs(monkeypatch):
    from reddit_signal.repositories import analysis_runs

    promoted = []
    monkeypatch.setattr(analysis_runs, "run_stats", lambda run_id: stats(18, 33, 1, 2_017))
    monkeypatch.setattr(analysis_runs, "recent_promoted_stats", lambda *, before_run_id: [
        stats(16, 672, 333, 56_231), stats(15, 683, 362, 62_649), stats(14, 1291, 937, 131_713)])
    monkeypatch.setattr(analysis_runs, "promote", promoted.append)
    return promoted


def test_promote_refuses_a_collapse(runs, capsys):
    assert cli.main(["promote", "18"]) == 1
    assert runs == []
    assert capsys.readouterr().out.count("blocked:") == 3


def test_promote_force_overrides_and_says_so(runs, capsys):
    assert cli.main(["promote", "18", "--force"]) == 0
    assert runs == [18]
    out = capsys.readouterr().out
    assert out.count("overridden by --force:") == 3 and "run 18 is live" in out


def test_missing_database_url_is_reported(monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    from reddit_signal import config

    with pytest.raises(config.ConfigError):
        cli.main(["status"])
