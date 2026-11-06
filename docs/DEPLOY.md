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

## Optional keep-awake heartbeat

The [heartbeat Gist](https://gist.github.com/HRIDYANSHU054/6d1bd393a6737d394d037f611085ded0)
targets a Node app with separate frontend and backend URLs. Scope Guard is a
single Python service, so its equivalent pings the Render-provided
`RENDER_EXTERNAL_URL` health endpoint once every 12 minutes. The feature is
included but **off by default**; no `node-cron`, second service, or manual URL
is needed.

To enable it for a hackathon/demo window:

1. In Render, open the `scopeguard-demo` service.
2. Open **Environment** and change `ENABLE_HEARTBEAT` from `false` to `true`.
3. Save the change and allow Render to redeploy.
4. In **Logs**, confirm `Render keep-alive enabled (12-minute interval)` and
   later `Render keep-alive ping succeeded`.
5. To let the service sleep again, change `ENABLE_HEARTBEAT` back to `false`
   and redeploy.

This keeps the free service active and can consume roughly 720 of the account's
750 monthly free instance hours for a full month. Render pools those hours
across the workspace; exhausting them suspends free services until the next
month. Enable it only if the reduced cold starts are worth using that quota.
Render documents the [free service limits](https://render.com/docs/free) and
the automatic [`RENDER_EXTERNAL_URL`](https://render.com/docs/environment-variables).

## Free-plan behavior

Render's Free web services spin down after 15 minutes without inbound traffic;
the first request after idle can take about a minute to wake the service. The
filesystem is ephemeral, so the SQLite demo ledger is rebuilt on every start
and lost on restart or deploy. This is suitable for a hackathon showcase, not
for persistent production use. Choose a paid instance and persistent storage
before using real customer data or retaining signatures.

For current behavior, see [Render's Free instance limits](https://render.com/docs/free).
