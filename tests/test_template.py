"""The Switch template is validated structurally, not just by eye."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from scopeguard.template import upstream_lint, validate_file, validate_text

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "scopeguard.template.yaml"


def document() -> dict[str, object]:
    return yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))


def test_the_shipped_template_passes_every_structural_check() -> None:
    report = validate_file(TEMPLATE)
    assert report.ok, [f"{item.code}: {item.message}" for item in report.findings]


def test_the_shipped_template_passes_switches_own_linter() -> None:
    result = upstream_lint(TEMPLATE.read_text(encoding="utf-8"), ROOT / ".reference-switch" / "core")
    assert result == "passed" or result.startswith("skipped: .reference-switch"), result


def test_a_typed_name_is_not_an_acceptance() -> None:
    data = document()
    for agent in data["agents"]:  # type: ignore[index]
        if agent["name"] == "{account}-clerk":
            agent["instructions"] = "When the client says `SCOPE ACCEPT <name>`, record it."
    report = validate_text(yaml.safe_dump(data))
    assert not report.ok
    assert "acceptance_name_only" in {item.code for item in report.findings}


def test_an_undeclared_placeholder_is_rejected() -> None:
    data = document()
    data["group"]["description"] = "Change orders for {missing_param}"  # type: ignore[index]
    report = validate_text(yaml.safe_dump(data))
    assert "placeholder_undeclared" in {item.code for item in report.findings}


def test_a_missing_delivery_room_is_rejected() -> None:
    data = document()
    data["rooms"] = [data["rooms"][0]]  # type: ignore[index]
    report = validate_text(yaml.safe_dump(data))
    codes = {item.code for item in report.findings}
    assert "room_count" in codes
    assert "link_rooms" in codes or "room_names" in codes


def test_the_client_acceptance_flow_is_documented_in_the_template() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "scopeguard client-accept" in text
    assert "SCOPE ACCEPT <token>" in text
    assert "register-client" in text
    assert "countersign" in text
    assert "SCOPE ACCEPT <name>" not in text


def test_validation_is_deterministic() -> None:
    first = copy.deepcopy(validate_file(TEMPLATE).findings)
    second = copy.deepcopy(validate_file(TEMPLATE).findings)
    assert first == second
