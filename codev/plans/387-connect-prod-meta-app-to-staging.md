# PIR Plan: Connect the production Meta app to a second staging WhatsApp service

Issue: iaser-ai/ansari#387. Related: #362, #363, #339.

## Understanding

Real users still talk to the old WhatsApp service (AWS App Runner, legacy API). The new Python service (`legacy/whatsapp`) works end to end on staging. Amr's priority is to shut the legacy services down, and he agreed to **option 3**: a second WhatsApp service in the same Railway staging environment, with its own domain and its own variables, using the production Meta app ("Ansari") and its phone number. The first staging service keeps the test app and test number for testing.

Checked, not assumed:
- The Railway project `ansari-backend` (workspace Haadi) has environments `staging` and `production`. The staging `whatsapp` service is online at `whatsapp-staging.askansari.ai`.
- Its variables (names): `AUTH_SERVER_URL`, `AUTH_WHATSAPP_API_KEY`, `BACKEND_SERVER_URL`, `DEPLOYMENT_TYPE=staging`, `ENVIRONMENT`, the four `META_*` secrets/ids, `META_REPLY_BY_PHONE=True`, `SENTRY_DSN`, `WA_MAINTENANCE_MODE=False`, `WHATSAPP_CHAT_RETENTION_HOURS=3`.
- `DEPLOYMENT_TYPE=production` (decided with the owner) changes, per `app/main.py`: the service refuses `META_REPLY_BY_PHONE` and every `MOCK_*`; `/sentry-debug` is not mounted; Sentry sampling is 0.2; Meta error logs carry no request details; the staging-only `!d ` filter is not applied.

No code change is expected. The work is configuration in Railway and Meta, plus a written record of it.

## Proposed Change

1. **Create a second Railway service** in the `staging` environment from the same repo: repo-root build, Dockerfile `legacy/whatsapp/Dockerfile`, health check `/`, watch paths as the first one. Name: `whatsapp-prod`, like `auth-prod` and `auth-db-prod`. Domain: `whatsapp.askansari.ai`, the production-style name (staging has `-staging`). It is attached today to the empty `whatsapp` service in the production environment (no deployments), so it moves from there, which needs a DNS change. Fallback if DNS is not ours to change: `whatsapp-prod.askansari.ai`.
2. **Set its variables:**
   - Same as the first service: `AUTH_SERVER_URL`, `BACKEND_SERVER_URL`, `SENTRY_DSN`, `WA_MAINTENANCE_MODE=False`, `WHATSAPP_CHAT_RETENTION_HOURS`.
   - Different: `DEPLOYMENT_TYPE=production`; `META_REPLY_BY_PHONE` not set; `META_BUSINESS_PHONE_NUMBER_ID`, `META_ACCESS_TOKEN_FROM_SYS_USER` and `META_ANSARI_APP_SECRET` all from the production Meta app (the secret must belong to the app that sends the webhooks); a new random `META_WEBHOOK_VERIFY_TOKEN`; the same `AUTH_WHATSAPP_API_KEY` as the first service for now (see Risks).
3. **Deploy and check before any switch** (see the test plan). Nothing reaches real users yet.
4. **Cutover, through the Graph API:** set an alternate callback on the production phone number (`POST /<phone number id>` with `webhook_configuration.override_callback_uri` = `https://whatsapp.askansari.ai/whatsapp/v2` and the new `verify_token`). The app's own callback in the dashboard is not touched, so the old service keeps everything the override does not cover. For `messages` webhooks, the old service stops receiving from that moment.
5. **Rollback:** one call, setting `override_callback_uri` to an empty string. Meta then sends webhooks to the app's callback (the old service) again. `GET /<phone number id>?fields=webhook_configuration` shows which URL is in effect.
6. **Record it** in the repo (below), so the setup is reviewable.

## Files to Change

- `legacy/whatsapp/README.md`: a short section on running a second instance on the production Meta app, which variables differ, and how to switch back.
- `RELEASE.md`: in the whatsapp service block, say a second instance exists in staging for the production Meta app, and list what differs.
- `legacy/whatsapp/railway.toml`: a comment that the second service uses the same build settings (the file is a record; Railway does not read it).

No application code.

## Risks & Alternatives Considered

- Risk: real users' accounts and conversations are stored in the **staging** databases until a separate production setup exists. Accepted by the issue; nobody is migrated (decision 6 in #321).
- Risk: users mid-conversation lose their old history at cutover, and the old service stops receiving. Mitigation: switch at a quiet time and keep the old URL ready for rollback.
- Risk: Sentry events from this instance are tagged `production` while the data is staging. Mitigation: note it in the README. What the `ENVIRONMENT` variable does is not checked yet.
- Risk (unverified): moving `whatsapp.askansari.ai` needs a DNS change, and I do not know who controls that DNS. Mitigation: use `whatsapp-prod.askansari.ai`, or Railway's generated domain; Meta only needs a reachable HTTPS URL, and the override can be changed with one call.
- Risk: both services share one `AUTH_WHATSAPP_API_KEY`. It works, since both talk to the same staging `apps/auth`, but: (a) the key's limits are shared (spec 329's initial values: 60 sign-ins a minute and 3,000 a day; the live values were not checked), so a test burst on the staging service can use up what real users need, and users then get "sign-in failed"; (b) if the key leaks or is rotated, both services stop; (c) `apps/auth` counts and alerts cannot tell the two apart. Mitigation: low traffic, and test bursts on the staging service are kept small. A separate key is a later improvement.
- Risk: the production access token's permissions are unverified. Mitigation: read `webhook_configuration` first, before any change.
- Alternative, option 1: point the production Meta app at the existing staging service. Rejected: it removes our test service, and the test number's credentials would have to be swapped in and out.
- Alternative, option 2: merge `develop` into `main`. Rejected: `main` is 1159 commits behind `develop`, so it is a full release, not a WhatsApp change.
- Alternative: a full production environment. Not needed to shut the legacy services down, and it needs the production auth route and key. Left for later.

## Test Plan

There is no running worktree to review: the running thing is the new Railway service. At `dev-approval` the reviewer checks that service before the cutover.

- Unit tests: none new. The existing suite (197 tests in `legacy/whatsapp`) already covers the refusal of `META_REPLY_BY_PHONE` and `MOCK_*` in production.
- Manual, before the cutover (no real users involved):
  1. The new service's health check `/` returns 200 on its domain.
  2. `GET /whatsapp/v2` with a wrong verify token returns 403, and with the right one returns the challenge.
  3. A sample webhook signed with the production app secret (as the tests do) is accepted with 200. It carries a different phone number id, so it is ignored. The dashboard's test button is no use here: it sends to the app's own callback, not the override.
  4. The logs show a clean start and the settings in effect, with no secrets.
- Manual, at the cutover (needs a go from the human, and ideally Amr and Waleed):
  5. Set the override; send a message from an allowed phone; it is answered; the logs show the whole request with no phone number, BSUID or message text.
  6. Send from a user whose phone number is hidden; it is answered.
  7. Remove the override; confirm the old service answers again. Then set it again.
- Not covered here: shutting down the old service and the legacy API (the issue's last done-when), which follows once real messages are stable.

## Decisions from the reviewer (2026-11-12)

- The cutover is done by the reviewer, who has access, through the Graph API.
- Custom domain, named like the existing pairs (the production-style name).
- One shared `AUTH_WHATSAPP_API_KEY` for now, with the consequences listed in Risks.
- Service name chosen by the same convention: `whatsapp-prod`.

Still open: who controls the DNS for `askansari.ai`.
