"""The judge's first run must not land on an empty ledger."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scopeguard import cli
from scopeguard.demo import run_demo
from scopeguard.service import ScopePaths, ScopeService, fixed_clock

ROOT = Path(__file__).resolve().parents[1]


def test_serve_auto_selects_an_existing_demo_ledger(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    demo_home = root / ".scopeguard" / "demo"
    demo_home.mkdir(parents=True)
    (demo_home / "ledger.sqlite3").write_bytes(b"")
    args = argparse.Namespace(root=str(root), home=None)
    assert cli._serve_home(args) == demo_home


def test_serve_leaves_an_empty_checkout_alone(tmp_path: Path) -> None:
    args = argparse.Namespace(root=str(tmp_path), home=None)
    assert cli._serve_home(args) is None


def test_serve_honours_an_explicit_home(tmp_path: Path) -> None:
    explicit = tmp_path / "elsewhere"
    args = argparse.Namespace(root=str(tmp_path), home=str(explicit))
    assert cli._serve_home(args) == explicit


def test_serve_demo_builds_the_ledger_before_serving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    built: list[Path] = []

    def fake_demo(*, root: Path, home: Path, write: Path | None, console: object) -> str:
        built.append(home)
        return "built"

    captured: dict[str, object] = {}

    def fake_create_app(*, root: Path, home: Path | None, fixture: str) -> object:
        captured["home"] = home
        return object()

    def fake_run(app: object, *, host: str, port: int, log_level: str) -> None:
        captured["port"] = port

    monkeypatch.setattr(cli, "run_demo", fake_demo)
    monkeypatch.setattr("scopeguard.api.app.create_app", fake_create_app)
    monkeypatch.setattr("uvicorn.run", fake_run)

    home = tmp_path / "demo-home"
    code = cli.main(
        [
            "--root",
            str(ROOT),
            "--home",
            str(home),
            "serve",
            "--demo",
            "--reset",
            "--port",
            "9999",
        ]
    )
    assert code == 0
    assert built == [home]
    assert captured["home"] == home
    assert captured["port"] == 9999
    assert "Building the demo ledger" in capsys.readouterr().out


def test_serve_without_a_ledger_prints_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("SCOPE_HOME", raising=False)
    monkeypatch.setattr("scopeguard.api.app.create_app", lambda **_: object())
    monkeypatch.setattr("uvicorn.run", lambda *_, **__: None)
    code = cli.main(
        ["--root", str(tmp_path), "--home", str(tmp_path / "empty-home"), "serve", "--port", "9999"]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "The ledger is empty" in out
    assert "scopeguard demo --reset" in out
    assert "scopeguard serve --demo" in out


def test_the_demo_ledger_is_usable_without_a_home_argument(tmp_path: Path) -> None:
    home = tmp_path / "demo-home"
    run_demo(root=ROOT, home=home, write=None, console=cli.Console())
    paths = ScopePaths.discover(root=ROOT, home=home, fixture="northwind")
    service = ScopeService.open(
        paths, now=fixed_clock(datetime(2026, 11, 4, 12, 0, tzinfo=UTC))
    )
    try:
        assert service.store.list_receipts("northwind-2026")
    finally:
        service.close()
