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
