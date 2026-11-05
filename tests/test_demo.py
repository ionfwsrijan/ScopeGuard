"""The one-command demo must actually execute the whole workflow."""

from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from vouch.demo import run_demo
from vouch.models import PacketStatus
from vouch.service import VouchPaths, VouchService


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

    assert "Vouch - every promise, vouched for" in text
    assert "1. A deal walks into the room" in text
    assert "4. Examiner breaks the packet" in text
    assert "8. Clerk seals the receipt" in text
    assert "11. The audit chain closes" in text
    written = transcript.read_text(encoding="utf-8")
    assert written.startswith("# Vouch - demo transcript")
    assert text in written

    # The demo leaves a real, verifiable ledger behind.
    paths = VouchPaths.discover(root=repo_root, home=home)
    with VouchService.open(paths) as service:
        packet = service.store.latest_packet("acme-2026")
        assert packet.version == 2
        assert packet.status == PacketStatus.SEALED
        receipt = service.store.latest_receipt("acme-2026")
        result = service.verify(receipt, require_unexpired=False, pinned_key=True)
        assert result.ok is True
        assert len(receipt.body.commitments) == 4
        assert "DISC" in receipt.body.commitments[2].claim_id
        assert receipt.body.commitments[2].commitment.startswith("15.00%")
        assert service.store.verify_audit()[0] is True


def test_demo_without_a_transcript_still_runs(repo_root: Path, tmp_path: Path) -> None:
    text = run_demo(
        root=repo_root,
        home=tmp_path / "home",
        console=Console(file=io.StringIO(), width=120),
    )
    assert len(text) > 2_000
    assert "Receipt" in text
