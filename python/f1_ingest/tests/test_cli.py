"""CLI argument parsing and dry-run reporting logic (no real socket).

`run_dry_run` itself calls `client.check_live`, which is the one thing that
touches the network - so here we monkeypatch that boundary rather than
actually connecting. See f1_ingest's package docstring for why the
connection layer itself isn't unit tested.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from f1_ingest import cli


def test_parse_args_defaults() -> None:
    args = cli.parse_args([])
    assert args.out == cli.DEFAULT_OUT_DIR
    assert args.session_name is None
    assert args.dry_run is False
    assert args.no_auth is False


def test_parse_args_overrides() -> None:
    args = cli.parse_args(
        [
            "--out",
            "somewhere/else",
            "--session-name",
            "custom",
            "--dry-run",
            "--no-auth",
        ]
    )
    assert args.out == Path("somewhere/else")
    assert args.session_name == "custom"
    assert args.dry_run is True
    assert args.no_auth is True


def _fake_check_live_with_session(*, no_auth: bool, connect_timeout: float) -> dict[str, Any]:
    assert no_auth is True  # run_dry_run must always skip auth
    return {"SessionInfo": {"Meeting": {"Name": "Test Grand Prix"}, "Type": "Race"}}


def _fake_check_live_empty(*, no_auth: bool, connect_timeout: float) -> dict[str, Any]:
    assert no_auth is True  # run_dry_run must always skip auth
    return {}


def test_dry_run_reports_live_when_session_info_present(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "check_live", _fake_check_live_with_session)

    exit_code = cli.run_dry_run(connect_timeout=1.0)

    assert exit_code == 0
    assert "LIVE" in capsys.readouterr().out


def test_dry_run_reports_not_live_when_empty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "check_live", _fake_check_live_empty)

    exit_code = cli.run_dry_run(connect_timeout=1.0)

    assert exit_code == 0
    assert "NOT LIVE" in capsys.readouterr().out


def test_dry_run_does_not_crash_on_connection_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # check_live() itself never raises (connection failures are caught and
    # reported as an empty snapshot) - this locks in that contract from the
    # CLI's side too.
    monkeypatch.setattr(cli, "check_live", _fake_check_live_empty)

    exit_code = cli.run_dry_run(connect_timeout=1.0)

    assert exit_code == 0
    assert "NOT LIVE" in capsys.readouterr().out
