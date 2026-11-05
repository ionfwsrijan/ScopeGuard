# Contributing

Vouch is a hackathon project with production-shaped bones. The contribution rules
are short because the design is strict.

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\python -m pip install -e ".[dev]"
# macOS/Linux: .venv/bin/python -m pip install -e ".[dev]"
```

## Before opening a pull request

```bash
ruff check src tests
ruff format --check src tests
mypy
python -m pytest -q
vouch demo --reset --no-write
```

All four must pass. CI runs the same commands on Windows and Linux across Python
3.11–3.13.

## Rules of the codebase

1. **The deterministic core does not import web or model libraries.**
   `assess.py`, `challenge.py`, `policy.py`, and `receipt.py` must stay pure.
2. **Every decision cites its evidence and policy.** No free-floating numbers.
3. **Approvals bind to a packet hash.** Never satisfy the gate with a stale or
   unbound approval.
4. **The receipt is canonical.** Do not change serialization without a version
   bump and a migration note.
5. **The demo must stay deterministic.** Freeze the clock; do not add network
   calls to the demo path.
6. **Say what is simulated.** Update `docs/VALIDITY.md` when the boundary moves.

## Adding a claim type

1. Add a value to `ClaimType` and a branch in `assess.py`.
2. Add a finding code in `challenge.py` if the new type can be overclaimed.
3. Add roles to `approval_matrix` and an approver to the fixture.
4. Add tests for approve, narrow, refuse, and escalate paths.
5. Update `docs/POLICY.md`.

## Adding a Switch operation

1. Add a typed method to `SwitchClient`.
2. Add a mocked-transport test that asserts the path, headers, and payload.
3. If the Clerk needs it, add it to the narrow `RoomClient` protocol.
4. Update `docs/SWITCH.md`.

## Reporting a security issue

Do not open a public issue. Describe the impact and a reproduction to the
maintainer directly. The threat model in `docs/VALIDITY.md` lists what is in
scope.
