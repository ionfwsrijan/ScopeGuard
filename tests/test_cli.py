"""The command line surface, exercised as a user would run it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scopeguard.cli import build_parser, main

ROOT = Path(__file__).resolve().parents[1]
MESSAGES = ROOT / "fixtures" / "northwind" / "switch_messages.yaml"


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
        "sign",
        "approve",
        "sync",
        "seal",
        "client-keygen",
        "register-client",
        "client-accept",
        "countersign",
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
    assert "scopeguard 1.0.0" in capsys.readouterr().out


def test_unknown_engagement_is_a_user_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(*base(tmp_path / "home", "status")) == 2
    assert "no engagement in the ledger" in capsys.readouterr().err


def test_full_cli_workflow(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    exported = tmp_path / "receipt.json"

    assert run(*base(home, "init")) == 0
    assert run(*base(home, "ingest")) == 0
    assert run(*base(home, "assess")) == 0
    assert run(*base(home, "challenge")) == 0
    assert run(*base(home, "revise")) == 0
    assert run(*base(home, "challenge")) == 0
    assert run(*base(home, "sync", "--messages", str(MESSAGES))) == 0
    assert run(*base(home, "status")) == 0
    assert run(*base(home, "seal", "--export", str(exported))) == 0
    assert exported.exists()

    assert (
        run(
            *base(
                home,
                "verify",
                "--receipt",
                "latest",
                "--require-unexpired",
                "--pinned-key",
            )
        )
        == 0
    )
    assert run(*base(home, "verify-audit")) == 0
    assert run(*base(home, "handoff")) == 0
    assert run(*base(home, "export", "--receipt")) == 0
    assert run(*base(home, "export", "--packet")) == 0
    assert run(*base(home, "export")) == 0

    output = capsys.readouterr().out
    assert "co_" in output
    assert "42,240.00" in output


def test_cli_records_one_named_approval(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    assert run(*base(home, "ingest")) == 0
    assert run(*base(home, "assess")) == 0
    capsys.readouterr()
    assert (
        run(
            *base(
                home,
                "sign",
                "--request",
                "RATE",
                "--role",
                "ACCOUNT_LEAD",
                "--actor",
                "Dana",
                "--phrase",
                "SIGN NORTHWIND-2026 RATE AS ACCOUNT_LEAD",
            )
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["phrase"] == "SIGN NORTHWIND-2026 RATE AS ACCOUNT_LEAD"
    assert payload["status"] == "valid"


def test_cli_approve_alias_and_role_validation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    assert run(*base(home, "ingest")) == 0
    assert run(*base(home, "assess")) == 0
    capsys.readouterr()
    assert (
        run(
            *base(
                home,
                "approve",
                "--request",
                "RATE",
                "--role",
                "LEGAL",
                "--actor",
                "Iris",
            )
        )
        == 2
    )
    assert "not a required approver" in capsys.readouterr().err


def test_cli_rejects_a_bad_phrase(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    assert run(*base(home, "ingest")) == 0
    assert run(*base(home, "assess")) == 0
    assert (
        run(
            *base(
                home,
                "sign",
                "--request",
                "RATE",
                "--role",
                "ACCOUNT_LEAD",
                "--actor",
                "Dana",
                "--phrase",
                "SIGN NORTHWIND-2026 RATE AS FINANCE",
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
    exported = tmp_path / "receipt.json"
    for command in [
        ("ingest",),
        ("assess",),
        ("challenge",),
        ("revise",),
        ("challenge",),
        ("sync", "--messages", str(MESSAGES)),
        ("seal", "--export", str(exported)),
    ]:
        assert run(*base(home, *command)) == 0

    payload = json.loads(exported.read_text(encoding="utf-8"))
    payload["body"]["line_items"][0]["scope"] = "Native mobile app generally available tomorrow."
    exported.write_text(json.dumps(payload), encoding="utf-8")

    capsys.readouterr()
    assert run(*base(home, "verify", "--receipt", str(exported))) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert "body_hash" in result["summary"]


def test_cli_independent_client_acceptance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "issuer-home"
    exported = tmp_path / "receipt.json"
    client_private = tmp_path / "client" / "jordan.pem"
    client_public = tmp_path / "client" / "jordan.pub"
    acceptance_file = tmp_path / "acceptance.json"

    for command in [
        ("ingest",),
        ("assess",),
        ("challenge",),
        ("revise",),
        ("challenge",),
        ("sync", "--messages", str(MESSAGES)),
        ("seal", "--export", str(exported)),
    ]:
        assert run(*base(home, *command)) == 0
    assert exported.exists()

    assert (
        run(
            *base(
                home,
                "client-keygen",
                "--out",
                str(client_private),
                "--public-out",
                str(client_public),
            )
        )
        == 0
    )
    assert client_private.exists() and client_public.exists()
    assert not (home / "keys" / "client-ed25519.pem").exists()

    assert (
        run(
            *base(
                home,
                "register-client",
                "--client",
                "Jordan Reyes",
                "--public-key",
                str(client_public),
            )
        )
        == 0
    )
    assert (home / "clients" / "northwind-2026.json").exists()

    assert (
        run(
            *base(
                home,
                "client-accept",
                "--receipt",
                str(exported),
                "--client",
                "Jordan Reyes",
                "--key",
                str(client_private),
                "--out",
                str(acceptance_file),
            )
        )
        == 0
    )
    assert acceptance_file.exists()
    capsys.readouterr()  # discard setup output; the next command is the one under test

    assert (
        run(
            *base(
                home,
                "countersign",
                "--acceptance",
                str(acceptance_file),
            )
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["acceptance"]["issuer"] == "Jordan Reyes"
    assert payload["acceptance"]["receipt_id"] == payload["body"]["receipt_id"]

    assert (
        run(
            *base(
                home,
                "verify",
                "--receipt",
                "latest",
                "--require-acceptance",
                "--pinned-client-key",
            )
        )
        == 0
    )


def test_cli_json_is_machine_readable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    assert run(*base(home, "ingest")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["engagement"]["id"] == "northwind-2026"
    assert payload["evidence"] == 4


def test_cli_switch_check_without_environment_is_a_user_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in (
        "SWITCH_API_ENDPOINT",
        "SWITCH_API_URL",
        "SWITCH_API_TOKEN",
        "SWITCH_AGENT_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    assert run(*base(tmp_path / "home", "switch-check")) == 2
    assert "missing Switch environment" in capsys.readouterr().err


def test_serve_honors_scope_host_and_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    seen: dict[str, object] = {}

    def fake_run(app: object, *, host: str, port: int, log_level: str) -> None:
        seen.update(app=app, host=host, port=port, log_level=log_level)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("SCOPE_HOST", "0.0.0.0")
    monkeypatch.setenv("SCOPE_PORT", "9999")
    assert run(*base(tmp_path / "home", "serve")) == 0
    assert seen["host"] == "0.0.0.0"
    assert seen["port"] == 9999
    assert seen["log_level"] == "warning"
