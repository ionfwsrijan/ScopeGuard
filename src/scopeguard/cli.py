"""Scope Guard command line interface."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from scopeguard import __version__
from scopeguard.demo import run_demo
from scopeguard.errors import ScopeError
from scopeguard.fixtures import load_messages
from scopeguard.models import Acceptance, Packet, Receipt
from scopeguard.receipt import (
    Keyring,
    acceptance_token,
    build_acceptance,
    load_public_key_file,
    parse_acceptance_token,
    public_key_fingerprint,
    public_key_pem,
)
from scopeguard.service import ScopePaths, ScopeService, as_of_clock
from scopeguard.switch.client import SwitchClient, SwitchConfig, messages_from_context

PALETTE = {
    "approve": "bold #1F6F6B",
    "narrow": "bold #E0A100",
    "refuse": "bold #B23A3A",
    "escalate": "bold #B23A3A",
    "block": "bold #B23A3A",
    "warn": "bold #E0A100",
    "note": "dim",
}


def _console(args: argparse.Namespace) -> Console:
    return Console()


def _paths(args: argparse.Namespace) -> ScopePaths:
    paths = ScopePaths.discover(
        root=Path(args.root),
        home=Path(args.home) if args.home else None,
        fixture=args.fixture or "northwind",
        policy=Path(args.policy) if args.policy else None,
        approvers=Path(args.approvers) if args.approvers else None,
    )
    paths.ensure()
    return paths


def _service(args: argparse.Namespace) -> ScopeService:
    moment = date.fromisoformat(args.as_of) if args.as_of else None
    return ScopeService.open(_paths(args), now=as_of_clock(moment))


def _engagement_id(args: argparse.Namespace, service: ScopeService) -> str:
    if getattr(args, "engagement", None):
        return str(args.engagement)
    engagements = service.store.list_engagements()
    if len(engagements) == 1:
        return engagements[0].id
    if not engagements:
        raise ScopeError(
            "no engagement in the ledger",
            hint="Run `scopeguard ingest --fixture northwind`.",
        )
    raise ScopeError(
        "more than one engagement in the ledger; pass --engagement",
        hint="Engagements: " + ", ".join(item.id for item in engagements),
    )


def _emit(args: argparse.Namespace, console: Console, payload: object, markdown: str = "") -> None:
    if args.json:
        console.print_json(json.dumps(payload, default=str))
    else:
        console.print(markdown or str(payload))


def _decision_table(packet: Packet) -> Table:
    table = Table(title=f"Packet v{packet.version} - {packet.status}", show_lines=True)
    table.add_column("Request", style="bold")
    table.add_column("Verdict")
    table.add_column("Priced language", overflow="fold")
    table.add_column("Amount", justify="right")
    table.add_column("Roles")
    for decision in packet.decisions:
        amount = f"{decision.amount:+,.2f}" if decision.amount is not None else "-"
        table.add_row(
            decision.request_id,
            f"[{PALETTE.get(decision.verdict, '')}]{decision.verdict}[/]",
            decision.priced_text,
            amount,
            ", ".join(decision.required_roles),
        )
    table.add_row(
        "[bold]TOTAL[/]",
        "",
        f"subtotal {packet.subtotal:,.2f} / discount {packet.discount_total:,.2f}",
        f"[bold]{packet.total:,.2f}[/]",
        f"{packet.currency}",
    )
    return table


def cmd_init(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    key = service.keyring
    stats = service.store.stats()
    if args.json:
        console.print_json(
            json.dumps(
                {
                    "ok": True,
                    "ledger": str(service.paths.db),
                    "policy": str(service.paths.policy),
                    "public_key": key.public_key_b64,
                    "stats": stats,
                }
            )
        )
    else:
        console.print(
            Panel.fit(
                "[bold #F7F3E8]Scope Guard is ready.[/]\n\n"
                f"Ledger: [bold]{service.paths.db}[/]\n"
                f"Policy: [bold]{service.paths.policy}[/]\n"
                f"Signing key: [bold]{service.paths.key}[/]\n"
                f"Public key: [dim]{key.public_key_b64}[/]",
                border_style="#1F6F6B",
            )
        )
    service.close()
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    engagement, evidence = service.ingest_fixture(args.fixture or "northwind")
    if args.json:
        _emit(
            args,
            console,
            {
                "engagement": engagement.model_dump(mode="json"),
                "evidence": len(evidence.items),
            },
        )
    else:
        console.print(
            f"[bold #1F6F6B]Ingested[/] {engagement.client} ({engagement.id}): "
            f"{len(engagement.requests)} requests, {len(evidence.items)} evidence items."
        )
        for item in evidence.items:
            console.print(f"  [dim]{item.id}[/] {item.request_id}: {item.summary}")
    service.close()
    return 0


def cmd_assess(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    engagement_id = _engagement_id(args, service)
    packet = service.assess(engagement_id, note=args.note)
    if args.json:
        _emit(args, console, packet.model_dump(mode="json"))
    else:
        console.print(_decision_table(packet))
        console.print(
            f"\n[dim]Packet hash[/] [bold]{packet.packet_hash}[/]  "
            f"[dim]status[/] [bold]{packet.status}[/]"
        )
        console.print("[dim]No change order exists yet. Humans must sign the packet hash.[/]")
    service.close()
    return 0


def cmd_challenge(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    engagement_id = _engagement_id(args, service)
    packet = service.challenge(engagement_id)
    if args.json:
        _emit(args, console, packet.model_dump(mode="json"))
    else:
        if packet.findings:
            table = Table(title=f"Red Team - packet v{packet.version}", show_lines=True)
            table.add_column("Severity")
            table.add_column("Request")
            table.add_column("Code")
            table.add_column("Finding", overflow="fold")
            for finding in packet.findings:
                table.add_row(
                    f"[{PALETTE.get(finding.severity, '')}]{finding.severity.upper()}[/]",
                    finding.request_id or "-",
                    finding.code,
                    finding.message,
                )
            console.print(table)
        else:
            console.print("[bold #1F6F6B]Red Team found no issues.[/]")
        style = "#B23A3A" if packet.has_blocks() else "#1F6F6B"
        console.print(f"\n[bold {style}]Status: {packet.status}[/]")
        if packet.has_blocks():
            console.print(
                "[dim]Run `scopeguard revise --engagement ...` to apply the Red Team remediation.[/]"
            )
    service.close()
    return 0


def cmd_revise(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    engagement_id = _engagement_id(args, service)
    packet = service.revise(engagement_id, note=args.note)
    if args.json:
        _emit(args, console, packet.model_dump(mode="json"))
    else:
        console.print(
            f"[bold #1F6F6B]Revised[/] to v{packet.version} "
            f"([bold]{packet.packet_hash[:16]}...[/]). Earlier signatures are now stale."
        )
        console.print(_decision_table(packet))
    service.close()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    engagement_id = _engagement_id(args, service)
    status = service.status(engagement_id)
    if args.json:
        _emit(
            args,
            console,
            {
                "engagement": status["engagement"].model_dump(mode="json"),
                "packet": status["packet"].model_dump(mode="json"),
                "approvals": [item.model_dump(mode="json") for item in status["approvals"]],
                "gate": status["gate"].model_dump(mode="json"),
                "receipt": status["receipt"].model_dump(mode="json") if status["receipt"] else None,
            },
        )
    else:
        console.print(_decision_table(status["packet"]))
        packet = status["packet"]
        if packet.findings:
            console.print("\n[bold]Red Team[/]")
            for finding in packet.findings:
                console.print(
                    f"  [{PALETTE.get(finding.severity, '')}]{finding.severity.upper()}[/] "
                    f"{finding.code}: {finding.message}"
                )
        console.print("\n[bold]Signatures[/]")
        approvals = status["approvals"]
        if not approvals:
            console.print("  [dim]none yet[/]")
        else:
            for item in approvals:
                style = "#1F6F6B" if item.status == "valid" else "#8A94A8"
                console.print(
                    f"  [{style}]{item.status:8}[/] {item.request_id} / {item.role}: "
                    f"{item.actor}"
                    + (f" [dim]({item.invalidated_reason})[/]" if item.invalidated_reason else "")
                )
        gate = status["gate"]
        console.print("\n[bold]Gate[/]")
        for check in gate.checks:
            console.print(
                f"  {'[#1F6F6B]PASS[/]' if check.ok else '[#B23A3A]FAIL[/]'} "
                f"{check.code}: {check.detail}"
            )
        if gate.pending:
            console.print("  [bold #E0A100]Pending:[/] " + ", ".join(gate.pending))
        receipt = status["receipt"]
        if receipt is not None:
            accepted = receipt.acceptance.issuer if receipt.acceptance else "awaiting client"
            console.print(
                f"\n[bold #1F6F6B]Change order[/] {receipt.body.receipt_id} "
                f"[dim]({accepted}; expires {receipt.body.expires_at.date()})[/]"
            )
            console.print(
                f"  Total [bold]{receipt.body.total:,.2f} {receipt.body.currency}[/] "
                f"(subtotal {receipt.body.subtotal:,.2f}, "
                f"discount {receipt.body.discount_total:,.2f})"
            )
    service.close()
    return 0


def cmd_sign(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    engagement_id = _engagement_id(args, service)
    approval = service.approve(
        engagement_id,
        args.request,
        args.role,
        args.actor,
        phrase=args.phrase,
        source="cli",
    )
    if args.json:
        _emit(args, console, approval.model_dump(mode="json"))
    else:
        console.print(
            f"[bold #1F6F6B]Signature recorded[/] {approval.request_id} / {approval.role} "
            f"by {approval.actor} against {approval.packet_hash[:16]}..."
        )
        pending = service.pending(engagement_id)
        console.print(
            "[dim]Gate clear.[/]"
            if not pending
            else f"[#E0A100]Still pending:[/] {', '.join(pending)}"
        )
    service.close()
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    engagement_id = _engagement_id(args, service)
    if args.messages:
        messages = load_messages(Path(args.messages))
        room_id = args.room_id or messages[0].room_id
    else:
        config = SwitchConfig.from_env()
        with SwitchClient(config) as client:
            payload = client.read_context(args.room_id)
            messages = messages_from_context(payload, args.room_id)
            room_id = args.room_id
    result = service.sync_messages(engagement_id, messages, room_id=room_id)
    if args.json:
        _emit(
            args,
            console,
            {
                "scanned": result.scanned,
                "recorded": result.recorded,
                "ignored": result.ignored,
                "errors": result.errors,
                "approvals": [item.model_dump(mode="json") for item in result.approvals],
            },
        )
    else:
        console.print(
            f"Scanned {result.scanned} message(s): "
            f"[bold #1F6F6B]{result.recorded} recorded[/], {result.ignored} ignored."
        )
        for item in result.approvals:
            console.print(f"  {item.request_id} / {item.role} by {item.actor}")
        for error in result.errors:
            console.print(f"  [#B23A3A]{error}[/]")
    service.close()
    return 0 if not result.errors else 1


def cmd_seal(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    engagement_id = _engagement_id(args, service)
    receipt = service.seal(engagement_id, valid_days=args.valid_days)
    destination = (
        Path(args.export)
        if args.export
        else service.paths.receipts / f"{receipt.body.receipt_id}.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(receipt.model_dump_json(indent=2), encoding="utf-8")
    service.store.audit(
        "change_order.exported",
        "scopeguard",
        {"receipt_id": receipt.body.receipt_id, "path": str(destination)},
    )
    if args.json:
        _emit(args, console, receipt.model_dump(mode="json"))
    else:
        console.print(
            Panel.fit(
                f"[bold #1F6F6B]Sealed.[/]\n\n"
                f"Change order: [bold]{receipt.body.receipt_id}[/]\n"
                f"Packet: v{receipt.body.packet_version} "
                f"`{receipt.body.packet_hash[:16]}...`\n"
                f"Subtotal: {receipt.body.subtotal:,.2f} {receipt.body.currency}\n"
                f"Discount: {receipt.body.discount_total:,.2f} {receipt.body.currency}\n"
                f"Total: [bold]{receipt.body.total:,.2f} {receipt.body.currency}[/]\n"
                f"Body hash: `{receipt.body_hash[:24]}...`\n"
                f"Expires: {receipt.body.expires_at.date().isoformat()}\n"
                f"File: {destination}",
                border_style="#1F6F6B",
            )
        )
        console.print(
            "[dim]Next: the client registers a public key and runs "
            "`scopeguard client-accept`; the issuer records it with "
            "`scopeguard countersign --acceptance <file>`.[/]"
        )
    service.close()
    return 0


def _load_acceptance(args: argparse.Namespace) -> Acceptance:
    if args.token:
        return parse_acceptance_token(args.token)
    path = Path(args.acceptance)
    if not path.exists():
        raise ScopeError(f"acceptance file not found: {path}")
    return Acceptance.model_validate_json(path.read_text(encoding="utf-8"))


def cmd_countersign(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    engagement_id = _engagement_id(args, service)
    acceptance = _load_acceptance(args)
    receipt = service.accept_client_artifact(engagement_id, acceptance)
    if args.json:
        _emit(args, console, receipt.model_dump(mode="json"))
    else:
        console.print(
            f"[bold #1F6F6B]Client accepted.[/] "
            f"{receipt.acceptance.issuer if receipt.acceptance else acceptance.issuer} "
            f"countersigned change order `{receipt.body.receipt_id}`."
        )
        console.print(
            f"[dim]Body hash {receipt.body_hash[:24]}...; client key "
            f"{public_key_fingerprint(acceptance.public_key)[:23]}...; total "
            f"{receipt.body.total:,.2f} {receipt.body.currency}.[/]"
        )
    service.close()
    return 0


def cmd_client_keygen(args: argparse.Namespace) -> int:
    console = _console(args)
    private_path = Path(args.out)
    public_path = Path(args.public_out) if args.public_out else private_path.with_suffix(".pub")
    if private_path.exists() and not args.force:
        raise ScopeError(
            f"refusing to overwrite existing key: {private_path}",
            hint="Pass --force only if you intend to replace the client identity.",
        )
    keyring = Keyring.generate(private_path)
    public_path.write_text(keyring.public_key_b64 + "\n", encoding="ascii")
    with contextlib.suppress(OSError):
        public_path.with_name(public_path.name + ".pem").write_bytes(
            public_key_pem(keyring.public_key_b64)
        )
    if args.json:
        _emit(
            args,
            console,
            {
                "private_key": str(private_path),
                "public_key": str(public_path),
                "public_key_b64": keyring.public_key_b64,
                "fingerprint": public_key_fingerprint(keyring.public_key_b64),
            },
        )
    else:
        console.print(
            Panel.fit(
                "[bold #1F6F6B]Client key created.[/]\n\n"
                f"Private key: [bold]{private_path}[/] (keep it on the client machine)\n"
                f"Public key: [bold]{public_path}[/]\n"
                f"Fingerprint: [dim]{public_key_fingerprint(keyring.public_key_b64)}[/]",
                border_style="#1F6F6B",
            )
        )
    return 0


def cmd_register_client(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    engagement_id = _engagement_id(args, service)
    public_key = load_public_key_file(Path(args.public_key))
    record = service.register_client(engagement_id, args.client, public_key, source="cli")
    if args.json:
        _emit(args, console, record)
    else:
        console.print(
            f"[bold #1F6F6B]Client registered.[/] {record['signer']} "
            f"({record['fingerprint']}) for {engagement_id}."
        )
        console.print(
            "[dim]The private key stays with the client. "
            "Acceptance is verified against this public key.[/]"
        )
    service.close()
    return 0


def cmd_client_accept(args: argparse.Namespace) -> int:
    console = _console(args)
    receipt_path = Path(args.receipt)
    if not receipt_path.exists():
        raise ScopeError(
            f"change-order file not found: {receipt_path}",
            hint="The issuer exports it with `scopeguard seal --export receipt.json`.",
        )
    receipt = Receipt.model_validate_json(receipt_path.read_text(encoding="utf-8"))
    keyring = Keyring.load(Path(args.key))
    acceptance = build_acceptance(receipt, keyring, args.client, accepted_at=None)
    out = Path(args.out) if args.out else Path(f"{receipt.body.receipt_id}.acceptance.json")
    out.write_text(acceptance.model_dump_json(indent=2), encoding="utf-8")
    token = acceptance_token(acceptance)
    if args.json:
        _emit(
            args,
            console,
            {
                "acceptance": acceptance.model_dump(mode="json"),
                "token": token,
                "out": str(out),
                "fingerprint": public_key_fingerprint(acceptance.public_key),
            },
        )
    else:
        console.print(
            Panel.fit(
                f"[bold #1F6F6B]Acceptance signed.[/]\n\n"
                f"Change order: [bold]{receipt.body.receipt_id}[/]\n"
                f"Signer: [bold]{acceptance.issuer}[/]\n"
                f"Body hash: `{acceptance.body_hash[:24]}...`\n"
                f"Fingerprint: [dim]{public_key_fingerprint(acceptance.public_key)}[/]\n"
                f"Artifact: {out}",
                border_style="#1F6F6B",
            )
        )
        console.print("\n[bold]Paste this into Switch:[/]")
        console.print(f"SCOPE ACCEPT {token}")
    return 0


def _load_receipt(service: ScopeService, value: str, engagement_id: str | None) -> Receipt:
    path = Path(value)
    if value == "latest":
        target = engagement_id or (
            service.store.list_engagements()[0].id
            if len(service.store.list_engagements()) == 1
            else None
        )
        if target is None:
            raise ScopeError("pass --engagement with --receipt latest")
        return service.store.latest_receipt(target)
    if path.exists() and path.is_file():
        return Receipt.model_validate_json(path.read_text(encoding="utf-8"))
    return service.store.load_receipt(value)


def cmd_verify(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    receipt = _load_receipt(service, args.receipt, args.engagement)
    supplied_client_key = (
        load_public_key_file(Path(args.client_key_file)) if args.client_key_file else None
    )
    result = service.verify(
        receipt,
        require_unexpired=args.require_unexpired,
        require_acceptance=args.require_acceptance,
        pinned_key=args.pinned_key,
        pinned_client_key=args.pinned_client_key,
        expected_client_public_key=supplied_client_key,
    )
    if args.json:
        _emit(args, console, result.model_dump(mode="json"))
    else:
        title = "Change order verified" if result.ok else "Change order rejected"
        style = "#1F6F6B" if result.ok else "#B23A3A"
        console.print(f"[bold {style}]{title}[/] - {result.receipt_id}")
        for check in result.checks:
            console.print(
                f"  {'[#1F6F6B]PASS[/]' if check.ok else '[#B23A3A]FAIL[/]'} "
                f"{check.code}: {check.detail}"
            )
        console.print(f"\n[dim]{result.summary}[/]")
    service.close()
    return 0 if result.ok else 1


def cmd_verify_audit(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    ok, detail = service.store.verify_audit()
    if args.json:
        _emit(args, console, {"ok": ok, "detail": detail})
    else:
        console.print(f"[{'#1F6F6B' if ok else '#B23A3A'}]{detail}[/]")
    service.close()
    return 0 if ok else 1


def cmd_handoff(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    engagement_id = _engagement_id(args, service)
    text = service.handoff_text(engagement_id)
    if args.post:
        config = SwitchConfig.from_env()
        with SwitchClient(config) as client:
            client.post_message(args.post, text)
        console.print(f"[bold #1F6F6B]Handoff posted[/] to {args.post}.")
    else:
        console.print(text)
    service.close()
    return 0


def cmd_switch_check(args: argparse.Namespace) -> int:
    console = _console(args)
    config = SwitchConfig.from_env()
    with SwitchClient(config) as client:
        result = client.check()
    if args.json:
        console.print_json(json.dumps(result))
    else:
        console.print(
            Panel.fit(
                "[bold #1F6F6B]Switch connection works.[/]\n\n"
                f"Endpoint: {result['endpoint']}\n"
                f"Agent: {result['agent_id']}\n"
                f"Operations advertised: {result['operations']}\n"
                f"read_context: {result['has_read_context']}\n"
                f"post_message: {result['has_post_message']}",
                border_style="#1F6F6B",
            )
        )
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    engagement_id = _engagement_id(args, service)
    if args.packet:
        payload = service.store.latest_packet(engagement_id).model_dump(mode="json")
    elif args.receipt:
        payload = service.store.latest_receipt(engagement_id).model_dump(mode="json")
    else:
        status = service.status(engagement_id)
        payload = {
            "engagement": status["engagement"].model_dump(mode="json"),
            "packet": status["packet"].model_dump(mode="json"),
            "receipt": status["receipt"].model_dump(mode="json") if status["receipt"] else None,
        }
    console.print_json(json.dumps(payload, indent=2, default=str))
    service.close()
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    console = Console()
    root = Path(args.root).resolve()
    home = Path(args.home) if args.home else root / ".scopeguard" / "demo"
    if args.reset and home.exists():
        shutil.rmtree(home, ignore_errors=True)
    destination = (
        None if args.no_write else Path(args.transcript or root / "demo" / "transcript.md")
    )
    text = run_demo(root=root, home=home, write=destination, console=console)
    if destination is not None:
        console.print(f"\n[dim]Transcript written to {destination} ({len(text)} chars).[/]")
    return 0


def _serve_home(args: argparse.Namespace) -> Path | None:
    """Pick the demo ledger automatically so the judge never sees an empty room."""
    if args.home:
        return Path(args.home)
    if os.environ.get("SCOPE_HOME"):
        return None  # ScopePaths.discover reads the environment
    root = Path(args.root).resolve()
    demo_home = root / ".scopeguard" / "demo"
    if (demo_home / "ledger.sqlite3").exists():
        return demo_home
    return None


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from scopeguard.api.app import create_app

    console = _console(args)
    root = Path(args.root).resolve()
    if args.demo:
        demo_home = Path(args.home) if args.home else root / ".scopeguard" / "demo"
        if args.reset and demo_home.exists():
            shutil.rmtree(demo_home, ignore_errors=True)
        console.print("[dim]Building the demo ledger before serving...[/]")
        run_demo(root=root, home=demo_home, write=None, console=console)
        home: Path | None = demo_home
    else:
        home = _serve_home(args)
    selected = ScopePaths.discover(root=root, home=home, fixture=args.fixture or "northwind")
    if not selected.db.exists():
        console.print(
            Panel.fit(
                "[bold #E0A100]The ledger is empty.[/]\n\n"
                "Run the deterministic demo first, then open the dashboard:\n\n"
                "[bold]scopeguard demo --reset[/]\n"
                "[bold]scopeguard serve[/]\n\n"
                "Or start both in one command: [bold]scopeguard serve --demo[/]",
                border_style="#E0A100",
            )
        )
    app = create_app(root=root, home=home, fixture=args.fixture or "northwind")
    host = os.environ.get("SCOPE_HOST", args.host)
    port = int(os.environ.get("SCOPE_PORT", str(args.port)))
    console.print(f"[dim]Serving {selected.db} at http://{host}:{port}[/]")
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


def cmd_clerk(args: argparse.Namespace) -> int:
    from scopeguard.switch.clerk import Clerk

    console = _console(args)
    service = _service(args)
    engagement_id = _engagement_id(args, service)
    config = SwitchConfig.from_env()
    with SwitchClient(config) as client:
        clerk = Clerk(
            service=service,
            client=client,
            engagement_id=engagement_id,
            scope_room_id=args.room_id,
            delivery_room_id=args.delivery_room_id,
            interval=args.interval,
        )
        if args.once:
            result = clerk.run_once()
            console.print(f"[bold #1F6F6B]Clerk pass complete[/] {result}")
        else:
            console.print(
                f"[bold]Clerk listening[/] on {args.room_id} every {args.interval}s. "
                "Sign with `SIGN <ENGAGEMENT> <REQUEST> AS <ROLE>`; seal with `SCOPE SEAL`."
            )
            clerk.run()
    service.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scopeguard",
        description="Every one more thing gets a price.",
    )
    parser.add_argument("--version", action="version", version=f"scopeguard {__version__}")
    parser.add_argument("--root", default=".", help="repository root (default: cwd)")
    parser.add_argument("--home", default=None, help="runtime directory (default: ~/.scopeguard)")
    parser.add_argument("--policy", default=None, help="policy YAML path")
    parser.add_argument("--approvers", default=None, help="approver map YAML path")
    parser.add_argument("--fixture", default="northwind", help="fixture name under fixtures/")
    parser.add_argument("--as-of", default=None, help="freeze the clock at YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create the ledger and signing key")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("ingest", help="load an engagement and its evidence")
    p.add_argument("--fixture", default=None, help="fixture name (default: northwind)")
    p.set_defaults(func=cmd_ingest)

    for name, handler, help_text in [
        ("assess", cmd_assess, "price every request with deterministic policy"),
        ("challenge", cmd_challenge, "run the Red Team against the latest packet"),
        ("revise", cmd_revise, "apply Red Team remediation and create a new packet"),
        ("status", cmd_status, "show packet, signatures, gate, and change order"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--engagement", default=None)
        if name in {"assess", "revise"}:
            p.add_argument("--note", default=None)
        p.set_defaults(func=handler)

    p = sub.add_parser("sign", aliases=["approve"], help="record a named human's signature")
    p.add_argument("--engagement", default=None)
    p.add_argument("--request", "--claim", dest="request", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--phrase", default=None)
    p.set_defaults(func=cmd_sign)

    p = sub.add_parser("sync", help="record signatures from Switch or a message fixture")
    p.add_argument("--engagement", default=None)
    p.add_argument("--room-id", default=None)
    p.add_argument("--messages", default=None, help="offline message fixture")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("seal", help="issue a signed change order when the gate is clear")
    p.add_argument("--engagement", default=None)
    p.add_argument("--valid-days", type=int, default=30)
    p.add_argument("--export", default=None)
    p.set_defaults(func=cmd_seal)

    p = sub.add_parser(
        "client-keygen",
        help="create a client-held Ed25519 key pair (run on the client machine)",
    )
    p.add_argument("--out", default="client-ed25519.pem", help="private key path")
    p.add_argument("--public-out", default=None, help="public key path")
    p.add_argument("--force", action="store_true", help="replace an existing key")
    p.set_defaults(func=cmd_client_keygen)

    p = sub.add_parser(
        "register-client",
        help="pin the named client's public key before acceptance",
    )
    p.add_argument("--engagement", default=None)
    p.add_argument("--client", required=True, help="named client signer")
    p.add_argument("--public-key", required=True, help="client public key file")
    p.set_defaults(func=cmd_register_client)

    p = sub.add_parser(
        "client-accept",
        help="sign an acceptance artifact with the client-held private key",
    )
    p.add_argument("--receipt", required=True, help="exported change-order JSON")
    p.add_argument("--client", required=True, help="named client signer")
    p.add_argument("--key", required=True, help="client private key")
    p.add_argument("--out", default=None, help="acceptance artifact path")
    p.set_defaults(func=cmd_client_accept)

    p = sub.add_parser(
        "countersign", help="verify and record the client's signed acceptance artifact"
    )
    p.add_argument("--engagement", default=None)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--acceptance", help="acceptance artifact JSON")
    group.add_argument("--token", help="SCOPE ACCEPT token from the Switch room")
    p.set_defaults(func=cmd_countersign)

    p = sub.add_parser("verify", help="verify a change order by id, path, or latest")
    p.add_argument("--receipt", default="latest")
    p.add_argument("--engagement", default=None)
    p.add_argument("--require-unexpired", action="store_true")
    p.add_argument("--require-acceptance", action="store_true")
    p.add_argument("--pinned-key", action="store_true", help="also require the local issuer key")
    p.add_argument(
        "--pinned-client-key",
        action="store_true",
        help="also require the client key registered for this engagement",
    )
    p.add_argument(
        "--client-key-file",
        default=None,
        help="require this client public key file (offline pin)",
    )
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("verify-audit", help="verify the append-only audit hash chain")
    p.set_defaults(func=cmd_verify_audit)

    p = sub.add_parser("handoff", help="print or post only the signed line items")
    p.add_argument("--engagement", default=None)
    p.add_argument("--post", default=None, help="Switch room id")
    p.set_defaults(func=cmd_handoff)

    p = sub.add_parser("switch-check", help="check Switch credentials and operations")
    p.set_defaults(func=cmd_switch_check)

    p = sub.add_parser("export", help="export engagement, packet, or change order JSON")
    p.add_argument("--engagement", default=None)
    group = p.add_mutually_exclusive_group()
    group.add_argument("--packet", action="store_true")
    group.add_argument("--receipt", action="store_true")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("demo", help="run the deterministic end-to-end demo")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--reset", action="store_true")
    p.add_argument("--transcript", default=None)
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("serve", help="run the local change-order web UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--fixture", default=None)
    p.add_argument(
        "--demo",
        action="store_true",
        help="build the demo ledger first (use --reset to rebuild it)",
    )
    p.add_argument("--reset", action="store_true", help="with --demo, rebuild from scratch")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("clerk", help="run the deterministic headless Switch Clerk")
    p.add_argument("--engagement", default=None)
    p.add_argument("--room-id", required=True)
    p.add_argument("--delivery-room-id", default=None)
    p.add_argument("--interval", type=float, default=3.0)
    p.add_argument("--once", action="store_true")
    p.set_defaults(func=cmd_clerk)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console(stderr=True)
    try:
        return int(args.func(args))
    except ScopeError as exc:
        console.print(f"[bold #B23A3A]{exc.code}[/]: {exc.message}")
        if exc.hint:
            console.print(f"[dim]{exc.hint}[/]")
        return 2
    except KeyboardInterrupt:
        console.print("[dim]Stopped.[/]")
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
