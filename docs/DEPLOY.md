# Public demo deployment

The repository includes a Render Blueprint for a public, read-only demo of the
synthetic Northwind engagement. It starts the deterministic demo on each
deployment and health-checks `/api/health`. It does not connect to Switch or
receive Switch credentials; the public service uses the bundled `FakeSwitch`
replay.

## Deploy

1. Sign in to [Render](https://render.com/).
2. Open the repository's [Render Blueprint deploy page](https://render.com/deploy?repo=https://github.com/ionfwsrijan/ScopeGuard).
3. Connect GitHub if Render asks, select `ionfwsrijan/ScopeGuard`, and review
   the `scopeguard-demo` web service. The Blueprint selects Render's Free plan.
4. Select **Apply** to build and deploy. Render will show the public
   `onrender.com` URL when the first deploy finishes.
5. Open the URL and check that `/api/health` reports `ok: true` and
   `audit_ok: true`.

No environment secrets are needed for the public demo. Do not add Switch API
tokens, client keys, or real customer data to this public service.

## Free-plan behavior

Render's Free web services spin down after 15 minutes without inbound traffic;
the first request after idle can take about a minute to wake the service. The
filesystem is ephemeral, so the SQLite demo ledger is rebuilt on every start
and lost on restart or deploy. This is suitable for a hackathon showcase, not
for persistent production use. Choose a paid instance and persistent storage
before using real customer data or retaining signatures.

For current behavior, see [Render's Free instance limits](https://render.com/docs/free).
