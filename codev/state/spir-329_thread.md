# spir-329 thread: service API keys + WhatsApp token exchange (issue #329)

## 2026-10-28: specify

- Read @better-auth/api-key 1.7.7 from the published tarball. Facts that differ from the issue/docs:
  owner column is `referenceId` (plus `configId`), not `userId`; `enableMetadata` defaults false;
  verify DELETES expired keys and exhausted no-refill keys; permission check precedes budget spend;
  plugin returns 429 for exhausted/rate-limited; verifyApiKey + deleteAllExpiredApiKeys are serverOnly.
- No existing operator script has a "refuse outside the auth image" guard; the spec raises its mechanism as an open question
  (a Dockerfile marker file is recommended, so no new env var is needed).
- apps/api resolveUser refuses only @system.ansari.chat, so the new no-login domain passes first use unchanged.
- Recommended approach: the exchange writes user+account in its own Drizzle tx, so the user.create.before hook refuses BOTH
  reserved domains unconditionally (stronger than the issue's "permit by context").
- PR #327 BSUIDs look like `US.13491208655302741918`; the pattern is proposed, to be agreed with the WhatsApp builder.

## 2026-10-28: spec iteration 1 (codex REQUEST_CHANGES; gemini skipped, agy not installed)

- Probed api-key 1.7.7 against pglite (throwaway test, reverted): remaining→0 WITHOUT refill DELETES the key on the next verify;
  WITH refill it is refused and kept, and the refill works (first one from a null lastRefillAt, and later cycles); rate-limited is kept; expired is deleted;
  a wrong permission spends no budget.
- verifyApiKey catches everything: a DB driver error comes back as INVALID_API_KEY (indistinguishable from an unknown key) and is logged raw
  through better-auth's logger. The spec adds a DB check before any 401 (outage → 500) and redacts better-auth's logger to {name, code}.
- Architect directions folded in: every valid:false → one 401; image guard dropped (scripts follow grant-admin's posture + print the DB host);
  schema mirrors the plugin model with generator column names.
- Codex points folded in: key referenceId bound to the WhatsApp service user (resolved by a `service` account row, never by email);
  one session per user under a per-user lock, near-expiry sessions replaced; wa-<random uuid>@domain address; 403 for banned.

## 2026-10-28: owner decision 1

- Domain: `@noreply.askansari.ai` (fixed reserved subdomain). Addresses use a kind prefix: `whatsapp-<user id>@…` (the user's own UUID, chosen by the exchange
  before insert, so the address is unique by construction) and `service-<service name>@…`. Considered and rejected: a hostname per user, and the bare @askansari.ai.

## 2026-10-28: spec iteration 2 (security architect's consolidated review: codex + claude, REQUEST_CHANGES)

- Folded in B1-B17. Notable: eligibility re-checked under the user-row lock (sole whatsapp row, noreply domain, registered_via, not admin → else 500 + alert);
  lock first, then ban/eligibility, session and signJWT in one transaction; relation-level pre-401 check + boot assertion; revoke = disable;
  no expiry on production keys; per-user budget (429) keyed by user id; blocked paths derived from apiKey().endpoints + prefix rule.
- Found while revising: FOR UPDATE on users conflicts with the KEY SHARE lock that the session insert's FK takes. If createSession ran on another connection,
  the request would block on its own lock. better-auth 1.7.7 has runWithTransaction/getCurrentAdapter; the plan must prove this on real Postgres.
- Corrected my own claim: LIKE 'whatsapp-%' cannot use the default-collation email btree; removed.
- signJWT is serverOnly, so /token's TOKEN_EXCHANGE_PER_SESSION is bypassed; the per-user budget replaces it.

## 2026-10-28: spec iteration 3 (owner D1-D4 + security review round 2)

- Owner: no BSUID regex (sanity bounds), BSUID raw, optional phone_number stored on auth.users (input:false, returned:false, never in /me),
  session row written directly under FOR NO KEY UPDATE. Decisions 3 and 4 are still open.
- Round 2 fixes: exchange mounted before corsFor; boot assertion only on definite 42P01/missing constraint (unreachable DB still starts);
  consumeWindow on the pool outside the tx (else refusals roll it back); explicit iat/exp to signJWT (signJWT omits iat otherwise);
  7 endpoints / 5 routed (serverOnly have no path); lock/statement timeouts; alerts counts-only, user id in log line.

## 2026-10-28: plan drafted

- Seven phases: (1) plugin, schema, migration 0001 and the blocked surface, plus probes (the plugin and its route block share one commit, so no commit exposes /api-key/*);
  (2) redacted logger as its own commit; (3) noreply domain; (4) operator scripts; (5) identity and session core in packages/auth;
  (6) route, boot assertion, alerts; (7) e2e, source scan, docs.
- Decision 3: formulas in P/D/U/N (legacy peak msg/min, daily msgs, DAU, instances), PROVISIONAL values, wide create-script bounds; the data was requested from the owner.
- No committed real-Postgres test infra exists (spec 300's Docker boot test was a manual run), so probe 4 and the outage run are manual against postgres:18, with output in the review.
- auth-journal-parity asserts a "single 0000" journal and schema tests assert six tables; both change in phase 1. AUTH_DEPLOYED_THROUGH stays main's.

## 2026-10-28: implement phase 1 (plugin, schema, 0001, blocked surface, probes)

- Main approved via security: migration tag 0001_api_key_phone (additive only); AUTH_DEPLOYED_THROUGH bumps to 0001 at the rebase, with a second
  auth-journal-applied line in RELEASE.md; merge precondition: owner applies 0001 to STAGING before merge, and to PRODUCTION before promote
  (duplicate check, apply, verify). Builders cannot afx send architect:main (router refuses); relay via security.
- Probe 4 on real Postgres 18.6: all 8 steps as planned, incl. the FOR UPDATE hazard reproduced (C'), and pool starvation: sign-after-commit 10/10 on a pool of 2,
  sign-inside hangs (no connect timeout) or fails loudly (timeout). Output is in codev/reviews/329-*.md.
- Probe 4a surprise (benign): sign-up with phone_number is REFUSED 400 FIELD_NOT_ALLOWED, not silently ignored; nothing stored.
- The probe script lives in packages/auth/scripts/probes (apps/auth has no direct pg/drizzle dep), a deviation from the plan path.
- The prefix rule was proven load-bearing by removing it (2 tests go red).

## 2026-10-28: phases 2 and 3

- Phase 2 (redacted logger) approved. The flaky secrets/copy hooks got their own 100 s deadline (measured: max 47 s, N=6; 30 s red 4/5 and 3/5, 100 s green 5/5).
  The shared machine (load 20-90 from other builders and simulators) makes the full root run red from timeouts in 5/6 runs across packages:
  evidence in the review for main's monorepo-wide follow-up. Per-package runs are the reliable signal here.
- Skipped porch's context-refresh task at the phase_3 boundary (a second porch next moved on).
- Phase 3: isReservedAddress covers @noreply.askansari.ai; isSystemAddress is untouched. The reset sender skips reserved addresses; grant-admin refuses noreply.
  The spec's "surrounding spaces → 409" doesn't match the guard order (validation first → 400 for all); the test asserts identical answers instead.

## 2026-10-28: phase 4 (service users and key scripts)

- packages/auth/src/service-users.ts (SERVICES, SECRET_SCANNING_PATTERNS, createServiceUser in one tx, findServiceUser none/one/many, listServiceKeys without the hash).
  limits.ts gains WHATSAPP_KEY_INITIAL (60/60 s, 3000/24 h) and WHATSAPP_KEY_BOUNDS, both in ms, as the plugin stores them.
- apps/auth/scripts/service-keys.ts: create-service | create-key | list | rotate | update-limits | revoke. grant-admin posture. Exit 2 = nothing written, 3 = written but the Sentry record was not sent (banner).
  create-service confirms with the service name, since there is no id yet.
- urlHost moved to apps/auth/scripts/url-host.ts (+ urlHostPort).
- Surprise: apps/api's source scan forbids literal DB URLs in apps/auth scripts. The demo harness now uses setAuthEnv's DATABASE_URL instead.
- Phase 4 review iter 1 (codex): the host line printed after the write (buffered output). Lines now stream, and the host line comes before connect().
- Phase 4 review iter 2 (codex): a REAL leak. Sentry's default Console integration turned the console.log'd raw key into a breadcrumb on the operator event.
  The test used defaultIntegrations:false and so hid it. Fix: the script's own Sentry options (no breadcrumbs) + stdout.write printing; test with production defaults.
  Gemini lane skipped both rounds (agy exit 3).
