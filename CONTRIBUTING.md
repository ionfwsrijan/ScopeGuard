# Contributing

Scope Guard is a hackathon project with production-shaped bones. The
contribution rules are short because the design is strict.

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\python -m pip install -e ".[dev]"
# macOS/Linux: .venv/bin/python -m pip install -e ".[dev]"
```

## Before opening a pull request

```bash
ruff check .
ruff format --check .
mypy
python -m pytest -q
scopeguard demo --reset --no-write
```

All five must pass. CI runs the same checks on Windows and Linux across Python
3.11–3.13.

## Rules of the codebase

1. **The deterministic core does not import web or model libraries.**
   `assess.py`, `challenge.py`, `policy.py`, and `receipt.py` must stay pure.
2. **Every decision cites its evidence and policy.** No free-floating numbers.
3. **Signatures bind to a packet hash.** Never satisfy the gate with a stale,
   unbound, or duplicate signature.
4. **Agents never sign, seal, or countersign.** Only named humans in the
   approver map may satisfy the gate; only the Clerk may seal after the gate is
   clear; only a named client may countersign.
5. **The change order is canonical.** Do not change serialization, id
   derivation, or the acceptance payload without a version bump and a migration
   note.
6. **The demo must stay deterministic.** Freeze the clock; do not add network
   calls to the demo path.
7. **Say what is simulated.** Update `docs/VALIDITY.md` when the boundary moves.

## Adding a request type

1. Add a value to `RequestType` and a branch in `assess.py`.
2. Add a finding code in `challenge.py` if the new type can be overclaimed or
   underpriced.
3. Add roles to `approval_matrix` in `policies/scope_policy_v1.yaml` and an
   approver to the fixture.
4. Add tests for approve, narrow, refuse, and escalate paths, plus the gate.
5. Update `docs/POLICY.md` and the engagement, evidence, and Switch fixtures.
6. Re-run `scopeguard demo --reset`; the packet hash will change, so update the
   checked-in transcript and any documentation that pins the old hash.

## Adding a policy rule

1. Add the field to `policies/scope_policy_v1.yaml` and the typed model in
   `policy.py`.
2. Enforce it in exactly one layer: pricing narrows, the Red Team blocks.
   Do not duplicate the same control in both without a reason.
3. Add a finding code with a machine-readable remediation if the rule can fail.
4. Add boundary tests at the threshold, one step below, and one step above.
5. Update the threshold and behavior tables in `docs/POLICY.md`.

## Adding a Switch operation

1. Add a typed method to `SwitchClient`.
2. Add a mocked-transport test that asserts the path, headers, and payload.
3. If the Clerk needs it, add it to the narrow `RoomClient` protocol.
4. Update `docs/SWITCH.md`.

## Changing the change-order format

A change order is a trust boundary. Any change to `ReceiptBody`, canonical
serialization, id derivation, or the acceptance message must:

1. Bump the format or policy version.
2. Keep old artifacts verifiable, or document the break explicitly.
3. Add a tamper test that fails closed.
4. Update `docs/ARCHITECTURE.md`, `docs/VALIDITY.md`, and the verification
   table.

## Reporting a security issue

Do not open a public issue. Describe the impact and a reproduction to the
maintainer directly. The threat model in `docs/VALIDITY.md` lists what is in
scope.
