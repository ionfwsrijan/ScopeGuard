"""The one-command demo must actually execute the whole workflow."""

from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from scopeguard.demo import run_demo
from scopeguard.models import PacketStatus
from scopeguard.service import ScopePaths, ScopeService


def test_demo_runs_end_to_end_and_writes_a_transcript(repo_root: Path, tmp_path: Path) -> None:
    home = tmp_path / "demo-home"
    transcript = tmp_path / "transcript.md"
    output = io.StringIO()
    text = run_demo(
        root=repo_root,
        home=home,
        write=transcript,
        console=Console(file=output, width=120),
    )

    assert "every 'one more thing' gets a price" in text
    assert "## 1. A fixed-fee engagement gets four 'small' asks" in text
    assert "## 4. Red Team breaks the packet" in text
    assert "## 5. Revision invalidates every earlier signature" in text
    assert "## 8. Clerk seals a two-party change order" in text
    assert "## 9. The client countersigns with a key the issuer never holds" in text
    assert "## 10. Verification is adversarial, not decorative" in text
    assert "## 12. The audit chain closes" in text

    written = transcript.read_text(encoding="utf-8")
    assert written.startswith("# Scope Guard - demo transcript")
    assert text in written
    receipt_file = tmp_path / "receipt.json"
    assert receipt_file.exists()
    assert not (home / "keys" / "client-ed25519.pem").exists()
    assert (home / "clients" / "northwind-2026.json").exists()

    # The demo leaves a real, verifiable ledger behind.
    paths = ScopePaths.discover(root=repo_root, home=home)
    with ScopeService.open(paths) as service:
        packet = service.store.latest_packet("northwind-2026")
        assert packet.version == 2
        assert packet.status == PacketStatus.SEALED
        receipt = service.store.latest_receipt("northwind-2026")
        assert receipt.acceptance is not None
        assert receipt.acceptance.issuer == "Jordan Reyes"
        result = service.verify(
            receipt,
            require_unexpired=True,
            require_acceptance=True,
            pinned_key=True,
            pinned_client_key=True,
        )
        assert result.ok is True
        assert len(receipt.body.line_items) == 4
        assert receipt.body.total == 42240.0
        assert receipt.body.discount_total == -5760.0
        assert service.store.verify_audit()[0] is True


def test_demo_without_a_transcript_still_runs(repo_root: Path, tmp_path: Path) -> None:
    text = run_demo(
        root=repo_root,
        home=tmp_path / "home",
        console=Console(file=io.StringIO(), width=120),
    )
    assert len(text) > 2_000
    assert "Change order" in text
    assert "SCOPE ACCEPT Jordan Reyes" not in text
    assert "client machine" in text
    assert "deleted" in text
