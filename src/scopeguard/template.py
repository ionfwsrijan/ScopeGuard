"""Validate the Scope Guard Switch room template.

Upstream Switch ships an advisory linter for template documents. It is the
authority on placeholder and parameter shape, but it is deliberately permissive
about the meaning of a room. This module adds the checks Scope Guard needs:
exactly one change-order room, one linked delivery room, five named agents, and
an acceptance path that uses a client-signed token rather than a typed name.

The upstream linter is used when its package is importable. It is advisory and
versioned with Switch, so a checkout without ``.reference-switch`` still gets a
complete structural validation and an explicit "upstream lint skipped" note.
"""

from __future__ import annotations

import importlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PLACEHOLDER_RE = re.compile(r"\{(\$?[A-Za-z_][A-Za-z0-9_]*)\}")
BUILTINS = frozenset({"$creator", "$creator_email", "$date", "$timestamp", "$agents_dir"})
PARAM_TYPES = frozenset({"string", "directory", "provider", "location"})
REQUIRED_AGENTS = ("scout", "pricer", "redteam", "clerk", "delivery")
KNOWN_TOP_LEVEL = frozenset({"version", "params", "agents", "group", "rooms", "links", "kickoff"})


@dataclass(frozen=True)
class Finding:
    """One reason a template is not ready to paste into Switch."""

    code: str
    message: str


@dataclass
class TemplateReport:
    """The result of validating one template document."""

    findings: list[Finding] = field(default_factory=list)
    upstream: str = "not run"

    @property
    def ok(self) -> bool:
        return not self.findings

    def add(self, code: str, message: str) -> None:
        self.findings.append(Finding(code, message))


def _strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        found: list[str] = []
        for key, value in node.items():
            if isinstance(key, str):
                found.append(key)
            found.extend(_strings(value))
        return found
    if isinstance(node, list):
        return [text for item in node for text in _strings(item)]
    return []


def _check_params(document: dict[str, Any], report: TemplateReport) -> set[str]:
    raw = document.get("params")
    if not isinstance(raw, dict) or not raw:
        report.add("params_missing", "the template must declare a non-empty params mapping")
        return set()
    declared: set[str] = set()
    for name, spec in raw.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
            report.add("param_name", f"parameter {name!r} is not a lowercase identifier")
            continue
        if not isinstance(spec, dict):
            report.add("param_spec", f"parameter {name!r} must have a mapping spec")
            continue
        declared.add(name)
        kind = spec.get("type")
        if kind not in PARAM_TYPES:
            report.add(
                "param_type",
                f"parameter {name!r} has type {kind!r}; expected one of "
                f"{', '.join(sorted(PARAM_TYPES))}",
            )
        for required in ("label", "description"):
            value = spec.get(required)
            if not isinstance(value, str) or not value.strip():
                report.add("param_text", f"parameter {name!r} needs a {required}")
        default = spec.get("default")
        if isinstance(default, str):
            for placeholder in PLACEHOLDER_RE.findall(default):
                if placeholder not in BUILTINS and placeholder != "agent":
                    report.add(
                        "param_default_placeholder",
                        f"parameter {name!r} default uses undeclared {{{placeholder}}}",
                    )
        pattern = spec.get("pattern")
        if pattern is not None:
            try:
                re.compile(str(pattern))
            except re.error as exc:
                report.add("param_pattern", f"parameter {name!r} pattern is invalid: {exc}")
    return declared


def _check_agents(document: dict[str, Any], report: TemplateReport) -> dict[str, dict[str, Any]]:
    raw = document.get("agents")
    if not isinstance(raw, list) or not raw:
        report.add("agents_missing", "the template must create the Scope Guard agents")
        return {}
    agents: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            report.add("agent_shape", f"agent #{index + 1} must be a mapping")
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.startswith("{account}-"):
            report.add("agent_name", f"agent #{index + 1} must be named {{account}}-<role>")
            continue
        agents[name] = item
        for key in ("provider", "location", "directory"):
            value = item.get(key)
            if not isinstance(value, str) or not value.strip():
                report.add("agent_runtime", f"agent {name!r} needs {key}")
        instructions = item.get("instructions")
        if not isinstance(instructions, str) or len(instructions.strip()) < 80:
            report.add("agent_instructions", f"agent {name!r} needs real instructions")
    roles = {name.removeprefix("{account}-") for name in agents if name.startswith("{account}-")}
    missing = [role for role in REQUIRED_AGENTS if role not in roles]
    if missing:
        report.add("agent_roles", f"missing agent roles: {', '.join(missing)}")
    return agents


def _check_rooms(
    document: dict[str, Any], agents: dict[str, dict[str, Any]], report: TemplateReport
) -> list[str]:
    raw = document.get("rooms")
    if not isinstance(raw, list):
        report.add("rooms_missing", "the template must create a list of rooms")
        return []
    names: list[str] = []
    for index, room in enumerate(raw):
        if not isinstance(room, dict):
            report.add("room_shape", f"room #{index + 1} must be a mapping")
            continue
        name = room.get("name")
        if not isinstance(name, str):
            report.add("room_name", f"room #{index + 1} needs a name")
            continue
        names.append(name)
        members = room.get("agents")
        if not isinstance(members, list) or not members:
            report.add("room_agents", f"room {name!r} needs at least one agent")
        else:
            for member in members:
                if member not in agents:
                    report.add(
                        "room_unknown_agent",
                        f"room {name!r} references undeclared agent {member!r}",
                    )
        users = room.get("users")
        if not isinstance(users, list) or "{$creator}" not in users:
            report.add("room_creator", f"room {name!r} must include the creator")
        if room.get("channel_type") != "channel_private":
            report.add("room_private", f"room {name!r} must be a private channel")
        instructions = room.get("instructions")
        if not isinstance(instructions, str) or len(instructions.strip()) < 80:
            report.add("room_instructions", f"room {name!r} needs real instructions")
        if not isinstance(room.get("kickoff"), str) or not room["kickoff"].strip():
            report.add("room_kickoff", f"room {name!r} needs a kickoff message")
    if len(names) != 2:
        report.add("room_count", f"expected exactly 2 rooms, found {len(names)}")
    expected = {"scope-{account}", "delivery-{account}"}
    if set(names) != expected:
        report.add(
            "room_names",
            f"expected the rooms {sorted(expected)}, found {sorted(names)}",
        )
    return names


def _check_links(document: dict[str, Any], rooms: list[str], report: TemplateReport) -> None:
    raw = document.get("links")
    if not isinstance(raw, list) or not raw:
        report.add("links_missing", "the delivery room must be linked from the change-order room")
        return
    for link in raw:
        if not isinstance(link, dict):
            report.add("link_shape", "each link must be a mapping")
            continue
        if link.get("from") not in rooms or link.get("to") not in rooms:
            report.add("link_rooms", f"link {link!r} must join two declared rooms")
        if link.get("from") != "scope-{account}" or link.get("to") != "delivery-{account}":
            report.add(
                "link_direction",
                "the link must run from scope-{account} to delivery-{account}",
            )


def _check_placeholders(
    document: dict[str, Any], declared: set[str], report: TemplateReport
) -> None:
    body = {key: value for key, value in document.items() if key != "params"}
    agent_entries = body.pop("agents", [])
    used = {match for text in _strings(body) for match in PLACEHOLDER_RE.findall(text)}
    # Inside an agent entry, ``{agent}`` is that entry's own name.
    for entry in agent_entries if isinstance(agent_entries, list) else []:
        entry_text = _strings(entry)
        entry_used = {match for text in entry_text for match in PLACEHOLDER_RE.findall(text)}
        entry_used.discard("agent")
        used.update(entry_used)
    used -= {name for name in used if name.startswith("$")}
    for name in sorted(used - declared):
        report.add("placeholder_undeclared", f"{{{name}}} is used but not declared in params")


def _check_workflow_text(document: dict[str, Any], report: TemplateReport) -> None:
    text = "\n".join(_strings(document))
    if "scopeguard client-accept" not in text:
        report.add(
            "acceptance_client_flow",
            "the template must tell the client to run `scopeguard client-accept`",
        )
    if "SCOPE ACCEPT <token>" not in text:
        report.add(
            "acceptance_token",
            "the template must use the `SCOPE ACCEPT <token>` form, not a typed name",
        )
    if re.search(r"SCOPE ACCEPT <name>|SCOPE ACCEPT <your name>", text):
        report.add(
            "acceptance_name_only",
            "a typed name is not a client signature; use the signed token",
        )
    if "scopeguard countersign" not in text:
        report.add(
            "acceptance_issuer_flow",
            "the template must tell the issuer to record the acceptance artifact",
        )
    if "scopeguard verify" not in text:
        report.add(
            "verification_text", "the template must tell delivery to verify the change order"
        )


def validate_document(document: Any) -> TemplateReport:
    """Check a parsed template document against the Scope Guard contract."""
    report = TemplateReport()
    if not isinstance(document, dict):
        report.add("not_a_mapping", "the template must be a mapping at the top level")
        return report
    for key in sorted(document):
        if isinstance(key, str) and key not in KNOWN_TOP_LEVEL:
            report.add("unknown_top_level_key", f"{key!r} is not a recognised top-level key")
    if document.get("version") != 1:
        report.add("version", "the template must declare version: 1")
    declared = _check_params(document, report)
    agents = _check_agents(document, report)
    rooms = _check_rooms(document, agents, report)
    _check_links(document, rooms, report)
    _check_placeholders(document, declared, report)
    _check_workflow_text(document, report)
    return report


def validate_text(text: str) -> TemplateReport:
    """Parse and validate a template written as YAML."""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        report = TemplateReport()
        report.add("invalid_yaml", f"not valid YAML: {exc}")
        return report
    return validate_document(document)


def validate_file(path: Path) -> TemplateReport:
    return validate_text(path.read_text(encoding="utf-8"))


def upstream_lint(text: str, core: Path | None = None) -> str:
    """Run Switch's own advisory linter when its source checkout is available."""
    if core is None:
        core = Path(".reference-switch") / "core"
    if not core.is_dir():
        return "skipped: .reference-switch/core is not present"
    entry = str(core.resolve())
    if entry not in sys.path:
        sys.path.insert(0, entry)
    try:
        module = importlib.import_module("switch_core.template_lint")
    except Exception as exc:
        return f"skipped: {type(exc).__name__}: {exc}"
    result = module.lint_template(text)
    problems = [f"{finding.code}: {finding.message}" for finding in result.errors]
    warnings = [f"{finding.code}: {finding.message}" for finding in result.warnings]
    if problems:
        return "failed: " + "; ".join(problems)
    if warnings:
        return "passed with warnings: " + "; ".join(warnings)
    return "passed"
