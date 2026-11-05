"""Vouch command line interface."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from vouch import __version__
from vouch.demo import run_demo
from vouch.errors import VouchError
from vouch.fixtures import load_messages
from vouch.models import Packet, Receipt
from vouch.service import VouchPaths, VouchService, as_of_clock
from vouch.switch.client import SwitchClient, SwitchConfig, messages_from_context

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


def _paths(args: argparse.Namespace) -> VouchPaths:
    paths = VouchPaths.discover(
        root=Path(args.root),
        home=Path(args.home) if args.home else None,
        fixture=args.fixture or "acme",
        policy=Path(args.policy) if args.policy else None,
        approvers=Path(args.approvers) if args.approvers else None,
    )
    paths.ensure()
    return paths


def _service(args: argparse.Namespace) -> VouchService:
    moment = date.fromisoformat(args.as_of) if args.as_of else None
    return VouchService.open(_paths(args), now=as_of_clock(moment))


def _deal_id(args: argparse.Namespace, service: VouchService) -> str:
    if getattr(args, "deal", None):
        return str(args.deal)
    deals = service.store.list_deals()
    if len(deals) == 1:
        return deals[0].id
    if not deals:
        raise VouchError("no deal in the ledger", hint="Run `vouch ingest --fixture acme`.")
    raise VouchError(
        "more than one deal in the ledger; pass --deal",
        hint="Deals: " + ", ".join(deal.id for deal in deals),
    )


def _emit(args: argparse.Namespace, console: Console, payload: object, markdown: str = "") -> None:
    if args.json:
        console.print_json(json.dumps(payload, default=str))
    else:
        console.print(markdown or str(payload))


def _decision_table(service: VouchService, packet: Packet) -> Table:
    table = Table(title=f"Packet v{packet.version} - {packet.status}", show_lines=True)
    table.add_column("Claim", style="bold")
    table.add_column("Verdict")
    table.add_column("Approved language", overflow="fold")
    table.add_column("Roles")
    for decision in packet.decisions:
        table.add_row(
            decision.claim_id,
            f"[{PALETTE.get(decision.verdict, '')}]{decision.verdict}[/]",
            decision.approved_text,
            ", ".join(decision.required_roles),
        )
    return table


def cmd_init(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    key = service.keyring
    stats = service.store.stats()
    console.print(
        Panel.fit(
            "[bold #F7F3E8]Vouch is ready.[/]\n\n"
            f"Ledger: [bold]{service.paths.db}[/]\n"
            f"Policy: [bold]{service.paths.policy}[/]\n"
            f"Signing key: [bold]{service.paths.key}[/]\n"
            f"Public key: [dim]{key.public_key_b64}[/]",
            border_style="#1F6F6B",
        )
    )
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
    service.close()
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    deal, evidence = service.ingest_fixture(args.fixture or "acme")
    if args.json:
        _emit(
            args,
            console,
            {"deal": deal.model_dump(mode="json"), "evidence": len(evidence.items)},
        )
    else:
        console.print(
            f"[bold #1F6F6B]Ingested[/] {deal.account} ({deal.id}): "
            f"{len(deal.claims)} claims, {len(evidence.items)} evidence items."
        )
        for item in evidence.items:
            console.print(f"  [dim]{item.id}[/] {item.claim_id}: {item.summary}")
    service.close()
    return 0


def cmd_assess(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    deal_id = _deal_id(args, service)
    packet = service.assess(deal_id, note=args.note)
    if args.json:
        _emit(args, console, packet.model_dump(mode="json"))
    else:
        console.print(_decision_table(service, packet))
        console.print(
            f"\n[dim]Packet hash[/] [bold]{packet.packet_hash}[/]  "
            f"[dim]status[/] [bold]{packet.status}[/]"
        )
        console.print("[dim]No commitment is approved yet. Humans must accept the packet.[/]")
    service.close()
    return 0


def cmd_challenge(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    deal_id = _deal_id(args, service)
    packet = service.challenge(deal_id)
    if args.json:
        _emit(args, console, packet.model_dump(mode="json"))
    else:
        if packet.findings:
            table = Table(title=f"Examiner - packet v{packet.version}", show_lines=True)
            table.add_column("Severity")
            table.add_column("Claim")
            table.add_column("Code")
            table.add_column("Finding", overflow="fold")
            for finding in packet.findings:
                table.add_row(
                    f"[{PALETTE.get(finding.severity, '')}]{finding.severity.upper()}[/]",
                    finding.claim_id or "-",
                    finding.code,
                    finding.message,
                )
            console.print(table)
        else:
            console.print("[bold #1F6F6B]Examiner found no issues.[/]")
        style = "#B23A3A" if packet.has_blocks() else "#1F6F6B"
        console.print(f"\n[bold {style}]Status: {packet.status}[/]")
        if packet.has_blocks():
            console.print(
                "[dim]Run `vouch revise --deal ...` to apply the Examiner's remediation.[/]"
            )
    service.close()
    return 0


def cmd_revise(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    deal_id = _deal_id(args, service)
    packet = service.revise(deal_id, note=args.note)
    if args.json:
        _emit(args, console, packet.model_dump(mode="json"))
    else:
        console.print(
            f"[bold #1F6F6B]Revised[/] to v{packet.version} "
            f"([bold]{packet.packet_hash[:16]}...[/]). Earlier approvals are now stale."
        )
        console.print(_decision_table(service, packet))
    service.close()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    deal_id = _deal_id(args, service)
    status = service.status(deal_id)
    if args.json:
        _emit(
            args,
            console,
            {
                "deal": status["deal"].model_dump(mode="json"),
                "packet": status["packet"].model_dump(mode="json"),
                "approvals": [item.model_dump(mode="json") for item in status["approvals"]],
                "gate": status["gate"].model_dump(mode="json"),
                "receipt": status["receipt"].model_dump(mode="json") if status["receipt"] else None,
            },
        )
    else:
        console.print(_decision_table(service, status["packet"]))
        packet = status["packet"]
        if packet.findings:
            console.print("\n[bold]Examiner[/]")
            for finding in packet.findings:
                console.print(
                    f"  [{PALETTE.get(finding.severity, '')}]{finding.severity.upper()}[/] "
                    f"{finding.code}: {finding.message}"
                )
        console.print("\n[bold]Approvals[/]")
        approvals = status["approvals"]
        if not approvals:
            console.print("  [dim]none yet[/]")
        else:
            for item in approvals:
                style = "#1F6F6B" if item.status == "valid" else "#8A94A8"
                console.print(
                    f"  [{style}]{item.status:8}[/] {item.claim_id} / {item.role}: {item.actor}"
                    + (f" [dim]({item.invalidated_reason})[/]" if item.invalidated_reason else "")
                )
        gate = status["gate"]
        console.print("\n[bold]Gate[/]")
        for check in gate.checks:
            console.print(
                f"  {'[#1F6F6B]PASS[/]' if check.ok else '[#B23A3A]FAIL[/]'} {check.code}: {check.detail}"
            )
        if gate.pending:
            console.print("  [bold #E0A100]Pending:[/] " + ", ".join(gate.pending))
        receipt = status["receipt"]
        if receipt is not None:
            console.print(
                f"\n[bold #1F6F6B]Receipt[/] {receipt.body.receipt_id} "
                f"[dim](expires {receipt.body.expires_at.date()})[/]"
            )
    service.close()
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    deal_id = _deal_id(args, service)
    approval = service.approve(
        deal_id,
        args.claim,
        args.role,
        args.actor,
        phrase=args.phrase,
        source="cli",
    )
    if args.json:
        _emit(args, console, approval.model_dump(mode="json"))
    else:
        console.print(
            f"[bold #1F6F6B]Approval recorded[/] {approval.claim_id} / {approval.role} "
            f"by {approval.actor} against {approval.packet_hash[:16]}..."
        )
        pending = service.pending(deal_id)
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
    deal_id = _deal_id(args, service)
    if args.messages:
        messages = load_messages(Path(args.messages))
        room_id = args.room_id or messages[0].room_id
    else:
        config = SwitchConfig.from_env()
        with SwitchClient(config) as client:
            payload = client.read_context(args.room_id)
            messages = messages_from_context(payload, args.room_id)
            room_id = args.room_id
    result = service.sync_messages(deal_id, messages, room_id=room_id)
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
            f"Scanned {result.scanned} message(s): [bold #1F6F6B]{result.recorded} recorded[/], "
            f"{result.ignored} ignored."
        )
        for item in result.approvals:
            console.print(f"  {item.claim_id} / {item.role} by {item.actor}")
        for error in result.errors:
            console.print(f"  [#B23A3A]{error}[/]")
    service.close()
    return 0 if not result.errors else 1


def cmd_seal(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    deal_id = _deal_id(args, service)
    receipt = service.seal(deal_id, valid_days=args.valid_days)
    destination = (
        Path(args.export)
        if args.export
        else service.paths.receipts / f"{receipt.body.receipt_id}.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(receipt.model_dump_json(indent=2), encoding="utf-8")
    service.store.audit(
        "receipt.exported",
        "vouch",
        {"receipt_id": receipt.body.receipt_id, "path": str(destination)},
    )
    if args.json:
        _emit(args, console, receipt.model_dump(mode="json"))
    else:
        console.print(
            Panel.fit(
                f"[bold #1F6F6B]Sealed.[/]\n\n"
                f"Receipt: [bold]{receipt.body.receipt_id}[/]\n"
                f"Packet: v{receipt.body.packet_version} `{receipt.body.packet_hash[:16]}...`\n"
                f"Body hash: `{receipt.body_hash[:24]}...`\n"
                f"Expires: {receipt.body.expires_at.date().isoformat()}\n"
                f"File: {destination}",
                border_style="#1F6F6B",
            )
        )
        console.print("[dim]Verify with `vouch verify --receipt latest`.[/]")
    service.close()
    return 0


def _load_receipt(service: VouchService, value: str, deal_id: str | None) -> Receipt:
    path = Path(value)
    if value == "latest":
        target = deal_id or (
            service.store.list_deals()[0].id if len(service.store.list_deals()) == 1 else None
        )
        if target is None:
            raise VouchError("pass --deal with --receipt latest")
        return service.store.latest_receipt(target)
    if path.exists() and path.is_file():
        return Receipt.model_validate_json(path.read_text(encoding="utf-8"))
    return service.store.load_receipt(value)


def cmd_verify(args: argparse.Namespace) -> int:
    console = _console(args)
    service = _service(args)
    receipt = _load_receipt(service, args.receipt, args.deal)
    result = service.verify(
        receipt,
        require_unexpired=args.require_unexpired,
        pinned_key=args.pinned_key,
    )
    if args.json:
        _emit(args, console, result.model_dump(mode="json"))
    else:
        title = "Receipt verified" if result.ok else "Receipt rejected"
        style = "#1F6F6B" if result.ok else "#B23A3A"
        console.print(f"[bold {style}]{title}[/] - {result.receipt_id}")
        for check in result.checks:
            console.print(
                f"  {'[#1F6F6B]PASS[/]' if check.ok else '[#B23A3A]FAIL[/]'} "
                f"{check.code}: {check.detail}"
            )
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
    deal_id = _deal_id(args, service)
    text = service.handoff_text(deal_id)
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
    deal_id = _deal_id(args, service)
    if args.packet:
        payload = service.store.latest_packet(deal_id).model_dump(mode="json")
    elif args.receipt:
        payload = service.store.latest_receipt(deal_id).model_dump(mode="json")
    else:
        status = service.status(deal_id)
        payload = {
            "deal": status["deal"].model_dump(mode="json"),
            "packet": status["packet"].model_dump(mode="json"),
            "receipt": status["receipt"].model_dump(mode="json") if status["receipt"] else None,
        }
    console.print_json(json.dumps(payload, indent=2, default=str))
    service.close()
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    console = Console()
    root = Path(args.root).resolve()
    home = Path(args.home) if args.home else root / ".vouch" / "demo"
    if args.reset and home.exists():
        shutil.rmtree(home, ignore_errors=True)
    destination = (
        None if args.no_write else Path(args.transcript or root / "demo" / "transcript.md")
    )
    text = run_demo(root=root, home=home, write=destination, console=console)
    if destination is not None:
        console.print(f"\n[dim]Transcript written to {destination} ({len(text)} chars).[/]")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from vouch.api.app import create_app

    root = Path(args.root).resolve()
    home = Path(args.home) if args.home else None
    app = create_app(root=root, home=home)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_clerk(args: argparse.Namespace) -> int:
    from vouch.switch.clerk import Clerk

    console = _console(args)
    service = _service(args)
    deal_id = _deal_id(args, service)
    config = SwitchConfig.from_env()
    with SwitchClient(config) as client:
        clerk = Clerk(
            service=service,
            client=client,
            deal_id=deal_id,
            sales_room_id=args.room_id,
            delivery_room_id=args.delivery_room_id,
            interval=args.interval,
        )
        if args.once:
            result = clerk.run_once()
            console.print(f"[bold #1F6F6B]Clerk pass complete[/] {result}")
        else:
            console.print(
                f"[bold]Clerk listening[/] on {args.room_id} every {args.interval}s. "
                "Approve with `APPROVE <DEAL> <CLAIM> AS <ROLE>`; seal with `VOUCH SEAL`."
            )
            clerk.run()
    service.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vouch",
        description="Commitment underwriting for B2B sales promises.",
    )
    parser.add_argument("--version", action="version", version=f"vouch {__version__}")
    parser.add_argument("--root", default=".", help="repository root (default: cwd)")
    parser.add_argument("--home", default=None, help="runtime directory (default: ~/.vouch)")
    parser.add_argument("--policy", default=None, help="policy YAML path")
    parser.add_argument("--approvers", default=None, help="approver map YAML path")
    parser.add_argument("--fixture", default="acme", help="fixture name under fixtures/")
    parser.add_argument("--as-of", default=None, help="freeze the clock at YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create the ledger and signing key")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("ingest", help="load a deal and its evidence")
    p.add_argument("--fixture", default=None, help="fixture name (default: acme)")
    p.set_defaults(func=cmd_ingest)

    for name, handler, help_text in [
        ("assess", cmd_assess, "build a packet with deterministic decisions"),
        ("challenge", cmd_challenge, "run the Examiner against the latest packet"),
        ("revise", cmd_revise, "apply Examiner remediation and create a new packet"),
        ("status", cmd_status, "show packet, approvals, gate, and receipt"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--deal", default=None)
        if name == "assess":
            p.add_argument("--note", default=None)
        if name == "revise":
            p.add_argument("--note", default=None)
        p.set_defaults(func=handler)

    p = sub.add_parser("approve", help="record a named human's exact approval")
    p.add_argument("--deal", default=None)
    p.add_argument("--claim", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--actor", required=True)
    p.add_argument("--phrase", default=None)
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("sync", help="record approvals from Switch or a message fixture")
    p.add_argument("--deal", default=None)
    p.add_argument("--room-id", default=None)
    p.add_argument("--messages", default=None, help="offline message fixture")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("seal", help="issue a signed receipt when the gate is clear")
    p.add_argument("--deal", default=None)
    p.add_argument("--valid-days", type=int, default=30)
    p.add_argument("--export", default=None)
    p.set_defaults(func=cmd_seal)

    p = sub.add_parser("verify", help="verify a receipt by id, path, or latest")
    p.add_argument("--receipt", default="latest")
    p.add_argument("--deal", default=None)
    p.add_argument("--require-unexpired", action="store_true")
    p.add_argument("--pinned-key", action="store_true", help="also require the local signing key")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("verify-audit", help="verify the append-only audit hash chain")
    p.set_defaults(func=cmd_verify_audit)

    p = sub.add_parser("handoff", help="print or post only the sealed commitments")
    p.add_argument("--deal", default=None)
    p.add_argument("--post", default=None, help="Switch room id")
    p.set_defaults(func=cmd_handoff)

    p = sub.add_parser("switch-check", help="check Switch credentials and operations")
    p.set_defaults(func=cmd_switch_check)

    p = sub.add_parser("export", help="export deal, packet, or receipt JSON")
    p.add_argument("--deal", default=None)
    group = p.add_mutually_exclusive_group()
    group.add_argument("--packet", action="store_true")
    group.add_argument("--receipt", action="store_true")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("demo", help="run the deterministic end-to-end demo")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--reset", action="store_true")
    p.add_argument("--transcript", default=None)
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("serve", help="run the local receipt web UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("clerk", help="run the deterministic headless Switch Clerk")
    p.add_argument("--deal", default=None)
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
    except VouchError as exc:
        console.print(f"[bold #B23A3A]{exc.code}[/]: {exc.message}")
        if exc.hint:
            console.print(f"[dim]{exc.hint}[/]")
        return 2
    except KeyboardInterrupt:
        console.print("[dim]Stopped.[/]")
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
