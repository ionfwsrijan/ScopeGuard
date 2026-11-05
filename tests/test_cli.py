"""The command line surface, exercised as a user would run it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vouch.cli import build_parser, main

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> int:
    return main(list(args))


def base(home: Path, *extra: str) -> list[str]:
    return [
        "--root",
        str(ROOT),
        "--home",
        str(home),
        "--as-of",
        "2026-11-04",
        "--json",
        *extra,
    ]


def test_parser_names_every_command() -> None:
    parser = build_parser()
    choices = parser._subparsers._group_actions[0].choices  # type: ignore[union-attr]
    assert {
        "init",
        "ingest",
        "assess",
        "challenge",
        "revise",
        "status",
        "approve",
        "sync",
        "seal",
        "verify",
        "verify-audit",
        "handoff",
        "switch-check",
        "export",
        "demo",
        "serve",
        "clerk",
    } <= set(choices)


def test_version_exits_cleanly(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        run("--version")
    assert info.value.code == 0
    assert "vouch 1.0.0" in capsys.readouterr().out


def test_unknown_deal_is_a_user_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(*base(tmp_path / "home", "status")) == 2
    assert "no deal in the ledger" in capsys.readouterr().err


def test_full_cli_workflow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    messages = ROOT / "fixtures" / "acme" / "switch_messages.yaml"
    exported = tmp_path / "receipt.json"

    assert run(*base(home, "init")) == 0
    assert run(*base(home, "ingest")) == 0
    assert run(*base(home, "assess")) == 0
    assert run(*base(home, "challenge")) == 0
    assert run(*base(home, "revise")) == 0
    assert run(*base(home, "challenge")) == 0
    assert run(*base(home, "sync", "--messages", str(messages))) == 0
    assert run(*base(home, "status")) == 0
    assert run(*base(home, "seal", "--export", str(exported))) == 0
    assert exported.exists()

    assert (
        run(*base(home, "verify", "--receipt", "latest", "--require-unexpired", "--pinned-key"))
        == 0
    )
    assert run(*base(home, "verify-audit")) == 0
    assert run(*base(home, "handoff")) == 0
    assert run(*base(home, "export", "--receipt")) == 0
    assert run(*base(home, "export", "--packet")) == 0
    assert run(*base(home, "export")) == 0

    output = capsys.readouterr().out
    assert "vch_" in output
    assert "SSO: beta available by 2026-12-15" in output or "beta available" in output


def test_cli_records_one_named_approval(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    assert run(*base(home, "ingest")) == 0
    assert run(*base(home, "assess")) == 0
    capsys.readouterr()
    assert (
        run(
            *base(
                home,
                "approve",
                "--claim",
                "DISC",
                "--role",
                "AE",
                "--actor",
                "Priya",
                "--phrase",
                "APPROVE ACME-2026 DISC AS AE",
            )
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["phrase"] == "APPROVE ACME-2026 DISC AS AE"
    assert payload["status"] == "valid"


def test_cli_rejects_a_bad_phrase(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    assert run(*base(home, "ingest")) == 0
    assert run(*base(home, "assess")) == 0
    assert (
        run(
            *base(
                home,
                "approve",
                "--claim",
                "DISC",
                "--role",
                "AE",
                "--actor",
                "Priya",
                "--phrase",
                "APPROVE ACME-2026 DISC AS VP_SALES",
            )
        )
        == 2
    )
    assert "approval phrase does not match" in capsys.readouterr().err


def test_cli_seal_before_the_gate_is_a_user_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    assert run(*base(home, "ingest")) == 0
    assert run(*base(home, "assess")) == 0
    assert run(*base(home, "challenge")) == 0
    assert run(*base(home, "seal")) == 2
    assert "cannot be sealed" in capsys.readouterr().err


def test_cli_verification_detects_a_tampered_export(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    messages = ROOT / "fixtures" / "acme" / "switch_messages.yaml"
    exported = tmp_path / "receipt.json"
    for command in [
        ("ingest",),
        ("assess",),
        ("challenge",),
        ("revise",),
        ("challenge",),
        ("sync", "--messages", str(messages)),
        ("seal", "--export", str(exported)),
    ]:
        assert run(*base(home, *command)) == 0
    payload = json.loads(exported.read_text(encoding="utf-8"))
    payload["body"]["commitments"][0]["commitment"] = "SSO generally available tomorrow."
    exported.write_text(json.dumps(payload), encoding="utf-8")
    capsys.readouterr()
    assert run(*base(home, "verify", "--receipt", str(exported))) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "body_hash" in payload["summary"]


def test_cli_json_is_machine_readable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    assert run(*base(home, "ingest")) == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["deal"]["id"] == "acme-2026"
    assert payload["evidence"] == 4


def test_cli_switch_check_without_environment_is_a_user_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("SWITCH_API_ENDPOINT", "SWITCH_API_URL", "SWITCH_API_TOKEN", "SWITCH_AGENT_ID"):
        monkeypatch.delenv(name, raising=False)
    assert run(*base(tmp_path / "home", "switch-check")) == 2
    assert "missing Switch environment" in capsys.readouterr().err
