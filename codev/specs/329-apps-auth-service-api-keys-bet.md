# Specification: Service API keys and the WhatsApp token exchange (apps/auth)

## Problem Statement

apps/auth knows only one kind of caller: a person who signs in. The only way into apps/api is a user JWT minted from a live Better Auth session. Nothing can act on behalf of a user who never signs in, and apps/auth has no way for a service to prove who it is.

The WhatsApp service (spec 321, PR #327) needs exactly that. A WhatsApp user is identified only by Meta's business-scoped user id (BSUID). They never visit a sign-in page, yet their messages must reach apps/api as an ordinary user, with threads, limits and deletion, under one stable UUID. Spec 321 assumes an apps/auth endpoint that takes a BSUID and returns a JWT. The endpoint does not exist, and the builder has run nothing against a live apps/auth. #314 (MCP) needs the same service-identity primitive later.

Two things are missing:

1. **Service identity**: a credential a service holds, issued and revoked by an operator, hashed at rest, scoped per service and per action, with its own rate limit and daily budget.
2. **A delegation rule**: which users a given service may mint tokens for. For WhatsApp the rule is "only users reachable through a `whatsapp` account row, keyed by BSUID". A WhatsApp key must never yield a token for an email user, an admin, a system identity or another provider's user.

Affected: the WhatsApp service and its users (no path into apps/api today), the operator (who will hold a new credential type), and the security model of apps/auth (a new credential, table and public route).

## Current State

- **Callers.** apps/auth serves Better Auth's router at `/api/auth/*`, the account endpoints at `/api/account/*` (bound to the caller's own JWT and a live `sid`), `/health` and a diag route. There is no service credential of any kind. The leaderboard uses a static shared secret on apps/api; MCP (`/v2/mcp-complete`) has none (#314).
- **Tokens.** apps/api accepts only apps/auth's EdDSA JWT (15 minutes, payload `{sub, sid}` plus `iss`, `aud`, `iat`, `exp`), verified against the public JWKS. On first use, `requireUser` → `resolveUser` fetches `GET /api/account/me` with that JWT, which requires the `sid` to name a live row in `auth.session`, and then inserts `public.users` under the same UUID. A bare signature with no session behind it is therefore useless: first use fails.
- **Reserved addresses.** `@system.ansari.chat` is reserved. Sign-up refuses it with the same 409 as a taken address; the reset sender skips it silently. The `user.create.before` database hook refuses it on every path, because spec 300 says the auth database never holds one (system identities live only as app rows found by `system_key`). apps/api's `resolveUser` also refuses a system-domain address. There is no second reserved domain.
- **Accounts.** `auth.account` carries `unique(user_id, provider_id)` (one credential row per user). There is no `unique(provider_id, account_id)`. Only the `credential` provider exists today (no social providers are configured).
- **Blocked paths.** `BLOCKED_AUTH_PATHS` (the admin plugin's whole surface, change-email, update-user, change-password) goes into `disabledPaths`, which better-auth matches exactly. apps/auth also blocks every case and prefix variant before the handler. Both are pinned by tests over `ADMIN_PATHS`.
- **Rate limits.** `ROUTER_RULES` are all `false`: apps/auth sees apps/api's egress IP on every legacy call, so per-IP rules there would throttle everyone together. Routes outside Better Auth's handler (the account routes) carry no per-IP rule at all; they have a per-user budget.
- **Operator scripts.** `grant-admin.ts` and `ban-user.ts` show an account, act on it, never print secrets, and run from the auth image or a checkout with the auth `DATABASE_URL`. Neither refuses to run outside the image. The issue's "same shape … refuse to run outside the auth image" describes a guard that does not exist yet.
- **`@better-auth/api-key` 1.7.7** (verified from the published package, peer `better-auth ^1.7.7`), the facts the design rests on:
  - Its `apikey` model holds `configId` (default `"default"`), `name`, `start` (the first characters, stored by default), `referenceId` (the owning user's id; `references` defaults to `"user"`), `prefix`, `key` (the SHA-256 hash, base64url), `refillInterval`, `refillAmount`, `lastRefillAt`, `enabled`, `rateLimitEnabled`, `rateLimitTimeWindow`, `rateLimitMax`, `requestCount`, `remaining`, `lastRequest`, `expiresAt`, `createdAt`, `updatedAt`, `permissions` (JSON text), `metadata` (JSON text, honoured only with `enableMetadata: true`). The owner column is `referenceId`, not `userId` as older docs show.
  - HTTP endpoints: `/api-key/create`, `/api-key/get`, `/api-key/list`, `/api-key/update`, `/api-key/delete`. `verifyApiKey` and `deleteAllExpiredApiKeys` are `serverOnly` and have no route.
  - `enableSessionForAPIKeys` defaults to `false`. Only when it is true does a before-hook turn an `x-api-key` header into a session for every Better Auth route.
  - `verifyApiKey` checks, in order: the hash is found; `enabled`; `expiresAt` (an expired key is **deleted** and refused); `permissions` (missing or insufficient is refused before any budget is spent); `remaining === 0` with no refill (the key is **deleted** and refused with 429 `USAGE_EXCEEDED`). Only then does it consume `remaining` and a rate-limit slot, atomically against the row. Errors are logged through better-auth's logger, with the error object only.
  - Default key: 64 random letters plus the configured prefix.

## Desired State

### 1. Service identity

- `packages/auth` carries `@better-auth/api-key` pinned to the core version (1.7.7). Its `apikey` table is written by hand in the existing `auth` schema style (uuid ids, `timestamptz`) and ships as migration `0001`, generated, reviewed as SQL and applied by hand at deploy. Better Auth never changes the database shape.
- **A service is one auth user**, created only by an operator script: an address under the non-interactive domain (Part 3), no credential, no account row, role `user`. One user per service: `whatsapp` now; later `mcp-faithful-answers`, and the leaderboard if it migrates.
- **Keys are issued, listed, rotated and revoked only by operator scripts**, which follow the existing scripts' shape: show the service account, act on its id, print nothing secret except the new key exactly once at creation. Each key has a per-service prefix, a name, `permissions`, `metadata` carrying the partner's client id, a per-key rate limit (`rateLimitEnabled`, `rateLimitTimeWindow`, `rateLimitMax`), a daily budget (`remaining`, `refillAmount`, `refillInterval`) and an expiry. Rotate creates a second live key for the same service so the consumer can switch without downtime; revoke deletes one. The scripts refuse to run outside the auth image (Open Question 8).
- **No HTTP surface.** The five `/api-key/*` paths join `BLOCKED_AUTH_PATHS` and are blocked like the admin paths, in every case and prefix variant. `enableSessionForAPIKeys` stays `false`, so a key is never a session anywhere on the router.
- **Verification is `auth.api.verifyApiKey`, in-process**, with the route's required permission passed in. Permissions are per service and per action: `{ whatsapp: ['mint'] }` for the exchange, `{ mcp: ['complete'] }` reserved for #314. A key valid for one service fails at another's route. No verify route at the public origin is built here; #314 adds one.

### 2. The WhatsApp token exchange: `POST /api/whatsapp/session`

Mounted outside Better Auth's handler path, like the account routes, so no router per-IP rule sees it (the WhatsApp service calls from a Railway egress IP). `ROUTER_RULES` stays all `false`. The key's own rate limit and daily budget are the throttle.

**Contract** (replaces the assumption in spec 321 and the `X-Whatsapp-Api-Key` header in #327):

| | |
|---|---|
| Request | `Authorization: Bearer <api key>`; JSON body `{ "bsuid": "<BSUID>" }` |
| 200 | `{ "token": "<JWT>" }`: the same EdDSA 15-minute `{sub, sid}` token apps/auth issues every client, signed from a live session of the BSUID's user |
| 400 | `{ "message": … }`: body not JSON, `bsuid` missing, not a string or not matching the agreed BSUID pattern |
| 401 | `{ "message": "Unauthorized" }`, byte-identical for a missing, malformed, unknown, revoked, disabled, expired, exhausted or wrong-permission key |
| 403 | the BSUID's user is banned (Open Question 6) |
| 429 | the key's per-window rate limit is exceeded (Open Question 5) |
| 500 | `{ "message": "Internal error" }` for anything else, including an auth-database outage (never 401, so the caller does not read an outage as a revoked key) |

The key is checked before the body (Open Question 7), so an unauthenticated caller learns nothing about the body rules.

**Per request:**

1. Verify the key with permission `whatsapp: mint`.
2. Validate the BSUID against the agreed pattern. A phone number is never accepted, never stored and never logged.
3. Look up `auth.account` where `provider_id = 'whatsapp'` and `account_id = <bsuid>`. The lookup never goes through email.
4. No row: create the user (a non-interactive-domain address that contains neither the BSUID nor any phone number; no credential; `registered_via = 'whatsapp'`; a derived display name) and the account row `{ providerId: 'whatsapp', accountId: <bsuid>, userId }` in **one transaction**. A new `unique(provider_id, account_id)` guarantees a BSUID maps to exactly one user under concurrency; the request that loses the race rolls back its user and uses the winner's.
5. The user is banned: delete their sessions, refuse (step 7 is never reached).
6. Reuse the user's live session when it outlives the JWT about to be minted (it expires more than one JWT lifetime from now); otherwise create one. One session per WhatsApp user, so sessions do not grow per message, and a token is never signed from a session that dies before the token does.
7. Sign the JWT from that session and return it.

The WhatsApp service caches the token per BSUID for under 15 minutes and calls apps/api with it. apps/api resolves the user on first use exactly as for a web user (`GET /api/account/me` with the token, `public.users` under the same UUID). **apps/api gains no WhatsApp code** (spec 321 success criterion).

### 3. A non-interactive address domain

A second reserved domain (name: Open Question 1; working name `@no-login.ansari.chat`) for service users and WhatsApp users:

- refused by sign-up with the same 409 as a taken address (no oracle), and by the reset-request sender with the same silent success as an unknown address (no mail attempt);
- never created by any Better Auth HTTP path: the `user.create.before` hook refuses it;
- created only by the exchange and by the service-user operator script, neither of which goes through a Better Auth HTTP path.

A user in this domain has no password and no account row except (for WhatsApp users) the `whatsapp` row, so they cannot sign in anywhere. `GET /api/account/me` answers for them only with their own JWT and a live `sid`, as for everyone. The system domain keeps every rule it has today; the auth database still never holds a system-domain address.

## Success Criteria

**Service keys**
- [ ] `@better-auth/api-key` is pinned to exactly the installed `better-auth` version (1.7.7); core is not bumped.
- [ ] The `apikey` table is in the `auth` schema (uuid id, `timestamptz` columns, `reference_id` a uuid referencing `auth.users` with cascade delete), and migration `0001` is the reviewed `drizzle-kit generate` output. The schema parity tests cover the new table.
- [ ] Each of `/api-key/create`, `/api-key/get`, `/api-key/list`, `/api-key/update`, `/api-key/delete` returns the blocked response over HTTP, in every case and prefix variant, as the admin paths do. `/api-key/verify` has no route.
- [ ] `enableSessionForAPIKeys` is `false`. A test sends a valid key as `x-api-key` and as a bearer to `/api/auth/get-session` and `/api/auth/token` and gets no session and no token.
- [ ] A stored key row holds only the hash: the raw key appears nowhere in the database (including `start`) after creation.
- [ ] The create script prints the raw key exactly once. List, rotate and revoke print ids, names, prefixes, permissions, budgets and dates, never a key or a hash.
- [ ] Every script refuses to run outside the auth image, exiting non-zero before connecting to the database.
- [ ] Rotate leaves two live keys for the service, both verifying. Revoke makes the revoked key fail at the exchange with the 401 body on the next request.

**Exchange: key states** (each returns the identical 401 body, and none creates a user, account or session)
- [ ] no `Authorization` header; non-Bearer scheme; empty bearer;
- [ ] unknown key; revoked (deleted) key; disabled key; expired key;
- [ ] key whose budget is exhausted (with and without refill configured; Open Question 5 decides whether the refill case is 401 or 429);
- [ ] key with `{ mcp: ['complete'] }` only; key with no permissions.

**Exchange: identity**
- [ ] A new BSUID creates exactly one user, one `whatsapp` account row and one session, and returns a JWT that verifies against apps/auth's JWKS with the same `alg`, `iss`, `aud` and lifetime as a `/api/auth/token` JWT, payload `{sub, sid}` naming that user and session.
- [ ] The same BSUID twice returns tokens with the same `sub` and the same `sid`. The session count for the user stays 1.
- [ ] A session that expires within one JWT lifetime is not reused; a new one is created.
- [ ] Two first requests for one BSUID racing produce one user and one account row, and both return a token for that user. The test exercises the conflict path itself, not just sequential calls.
- [ ] **Delegation boundary.** With a seeded admin, a seeded email user and a seeded user of another provider (`provider_id = 'google'`, `account_id` shaped like a BSUID): passing the admin's and email user's ids (and emails) as `bsuid` is refused with 400; passing the other provider's `account_id` as `bsuid` returns a token whose `sub` is a new WhatsApp user, never the seeded user. No exchange response ever carries the `sub` of a user without a `whatsapp` account row.
- [ ] A service user (which has no account row) is never reachable by any BSUID.
- [ ] A banned WhatsApp user is refused, and their sessions are deleted. A JWT minted before the ban then fails `GET /api/account/me` (dead `sid`).

**Exchange: privacy and logging**
- [ ] A phone-number-shaped `bsuid` (E.164 with and without `+`, or digits only) is refused with 400 before any lookup or log.
- [ ] No created row (user email, name, session IP and user agent, account) contains the BSUID except `account.account_id`, and none contains a phone number.
- [ ] No log line, alert or error response from the exchange or the scripts contains a raw key, a key hash, a BSUID or a driver error object. Errors log `{name, code}` only. A test captures `console.*` and better-auth's logger across every key-state and failure case and asserts none of those values appears.

**Reserved domain**
- [ ] Sign-up with a non-interactive-domain address (any case, surrounding spaces) returns the same 409 body as a taken address.
- [ ] A reset request for an existing non-interactive-domain user returns the same response as an unknown address, and the mailer is never called.
- [ ] The `user.create.before` hook refuses a non-interactive-domain address on every Better Auth path. The exchange and the service-user script create one.
- [ ] Sign-in with a non-interactive-domain user's address fails exactly as for an unknown address.

**End to end**
- [ ] In the existing apps/api contract tests (which start a real apps/auth), a JWT from the exchange passes `requireUser`: JWKS verification, first-use `GET /api/account/me`, and a `public.users` row created under the same UUID with `registered_via = 'whatsapp'`.
- [ ] apps/api's source gains no WhatsApp-specific code.
- [ ] Staging: the WhatsApp builder's smoke test (a phone-visible and a phone-hidden user) passes against staging apps/auth and apps/api with a key issued by the create script.

**Unchanged**
- [ ] `ROUTER_RULES` are all `false`; no per-IP rule applies to the exchange.
- [ ] Every existing apps/auth, packages/auth and apps/api test passes unedited, except tests that enumerate the blocked paths or the schema, which gain the new entries.
- [ ] No new environment variable, or, if one appears, it is added to `turbo.json` `globalEnv` by the four-way derivation and reviewed by main.

## Constraints

- **Risk tier High** (auth model, new table, new credential type): 3-way consultation on spec, plan and PR. The owner's decisions are presented to them for approval.
- **Pin, do not bump.** The plugin's newest release requires core 1.7.14; bumping core is a separate, joint change.
- **Schema discipline.** `packages/auth/src/schema.ts` is hand-written; the table is added there in its style. `drizzle-kit generate` → reviewed SQL → applied by a human with `psql -v ON_ERROR_STOP=1 --single-transaction` against the auth database. Never `db:push`. Main numbers migrations: the journal entry is coordinated with `architect:main` before it is committed. The deploy order (migration before code) applies.
- **Two databases.** Everything here is in the auth database and apps/auth. apps/api changes only in tests. No query crosses the two.
- **Tokens.** The exchange's JWT must be indistinguishable from any other apps/auth JWT, and must be signed from a real, live session (apps/api's first use needs a live `sid`).
- **Client IP.** No per-IP rule on the exchange; `ROUTER_RULES` stays all `false`.
- **No secret leaves its moment.** Never log a raw key, a raw driver error, a BSUID or a phone number. The raw key exists outside the creating process only in the create script's single output.
- **Sequencing.** develop is frozen for apps/auth and packages/auth until the spec 300 production cutover and its contract PR land. Spec, plan and implementation proceed; the PR does not merge before then and expects a rebase. The migration is `0001` in `packages/auth/drizzle`, unrelated to the contract PR's app-database migration.
- **Process.** Explicit `git add` only; no AI attribution; commits `[Spec 329][Phase: x] type: message`; `.env` files are never read.

## Assumptions

- The spec 300 cutover lands without changing `createAuth`'s plugin list, the account-route mounting or `BLOCKED_AUTH_PATHS` in a way that conflicts with this design. The rebase may reconcile details.
- A BSUID is a short ASCII string of the form `<two-letter country code>.<digits>` (seen in #327: `US.13491208655302741918`, `EG.1234567890123456789`). The exact pattern is agreed with the WhatsApp builder (Open Question 9).
- BSUIDs are stable per user per business portfolio. A user hiding or revealing their phone number does not change their BSUID.
- The WhatsApp service holds one key at a time (two during rotation) and is the only consumer of `whatsapp: mint`.
- PR #327 changes its header to `Authorization: Bearer` and its error handling to the shapes above. Spec 321's amendment lands on its own branch, coordinated with that builder, since spec 321 is not on develop.
- `@better-auth/api-key` 1.7.7's behaviour (above) is what ships. The tests pin the parts the design relies on (no HTTP surface, no session for keys, hashing, permission-before-budget), so a later bump that changes them fails loudly.

## Solution Approaches

### Approach 1 (recommended): plugin for keys, exchange writes identity rows directly in one transaction

Keys come from `@better-auth/api-key` (hashing, permissions, budgets, atomic usage accounting) with its HTTP surface blocked. The exchange verifies with `auth.api.verifyApiKey`, then creates the WhatsApp user and account row with its own Drizzle transaction in `packages/auth` (the same pattern as `banUserByEmail`'s update plus session delete). It reuses or creates the session, and signs with the jwt plugin.

Because the exchange never calls `internalAdapter.createUser`, the `user.create.before` hook needs no "who is calling" exception. It simply refuses **both** reserved domains on every path it sees. Every Better Auth path runs through it; the exchange and the service-user script do not. This is stronger than the issue's wording ("permitted by the database hook only when the creating context is the exchange route or an operator script"), with the same outcome: no Better Auth HTTP path can create the address, and there is no context check to get wrong.

- **Pros:** the user and account are atomic in one real transaction (provable against pglite); the reserved-domain hook stays an unconditional rule; the delegation rule is one parameterized query on `(provider_id, account_id)` with the provider fixed in code; no new credential crypto to maintain.
- **Cons:** the exchange writes `auth.users` and `auth.account` rows itself, so it must match Better Auth's row shape (the hand-written schema already pins it, and tests read the rows back through Better Auth). The session may be created through `internalAdapter.createSession` so its token shape stays Better Auth's.
- **Risk:** low to medium. The concurrency path needs a deliberate test, because pglite serializes connections.

### Approach 2: plugin for keys, identity through Better Auth's internal adapter with a context marker

As Approach 1, but users and accounts are created through `internalAdapter.createUser` / `createAccount`. The `user.create.before` hook permits the non-interactive domain only when a marker (set by the exchange or the script) is present in the hook's context.

- **Pros:** every row is written by Better Auth's own code.
- **Cons:** the internal adapter's calls are not one transaction, so a crash between user and account leaves an orphan user that no BSUID reaches. The hook's safety then rests on a marker that no HTTP path can forge, which is a subtle invariant across better-auth upgrades. This is weaker than Approach 1's unconditional refusal.
- **Risk:** medium.

### Approach 3: hand-rolled service keys (no plugin)

A small table of hashed keys with permissions and budgets, verified by apps/auth's own code.

- **Pros:** no dependency, and no blocked-surface to police.
- **Cons:** re-implements atomic budget and rate-limit accounting, expiry, refill and rotation that the plugin already does and tests. It diverges from the issue's design and from #314's expected consumer model.
- **Risk:** medium (new crypto-adjacent code). Rejected.

**Recommendation: Approach 1.**

## Open Questions

**Critical (owner decisions; recommendation given, not settled)**

1. **The non-interactive domain name.** Recommendation: `@no-login.ansari.chat`. It says what the address is, sits under a domain the project controls, and cannot be confused with `@system.ansari.chat`.
2. **Store the BSUID raw in `account.account_id`, or HMAC it with an apps/auth secret.** Recommendation: raw. The BSUID is already Meta's business-scoped pseudonym and is needed for lookup, as any OAuth provider id is. HMAC defends against correlation after a database leak, but costs a new secret, a rotation story that re-keys every row, and a new env var. Revisit if a second identifier ever lands in the auth database beside it.
3. **Daily budget and per-window limit for the WhatsApp key**, sized from the legacy WhatsApp service's traffic. Recommendation: the create script requires both explicitly (no defaults), so the sizing is a deliberate operator act, recorded in the runbook. A starting point is about twice the legacy peak per minute and per day. One exchange per BSUID per 15 minutes means the budget scales with active conversations, not messages.
4. **The leaderboard: migrate from its static secret now or later.** Recommendation: later. Nothing forces it, and this spec ships one consumer.

**Important (shape the design; builder recommendation, for the architect or owner)**

5. **Throttle statuses.** The issue lists "budget-exhausted" under the single 401. The plugin itself returns 429 for an exhausted key and for a rate-limit hit. Recommendation: per-window rate limit → 429 with `Retry-After`, so the WhatsApp service backs off instead of treating its key as revoked. An exhausted daily budget follows the issue (401), unless the owner prefers 429 there too.
6. **The banned-user response.** The issue says "refused" but gives no status. Recommendation: 403 `{ message }`, distinct from 401, so the WhatsApp service can tell "this user is banned" (a reply to the user, or none) from "my key is bad" (page the operator). The caller is a trusted service holding a valid key, so this is no oracle to the public.
7. **Order of checks.** Recommendation: key first, then body, so an unauthenticated caller cannot probe the BSUID rules. The cost is that a valid key's budget is spent on a malformed body. The plugin consumes budget at verification.
8. **How "refuse to run outside the auth image" is detected.** No existing script has this guard. Recommendation: a marker file written by the auth Dockerfile at build time. A file needs no new environment variable or `turbo.json` change, and cannot be set by accident in a shell. The guard runs before the database connection. Also: should `grant-admin.ts` and `ban-user.ts` gain it? Recommendation: not in this spec (they are documented to run from a checkout too).
9. **The exact BSUID pattern**, agreed with the WhatsApp builder (#327). Proposal: `^[A-Z]{2}\.[0-9]{10,32}$`, matching both samples in #327. Meta's documentation is sparse here, so the pattern is a contract both sides test against. A bare phone number (E.164, with or without `+`) can never match, because it has no country-code-and-dot prefix. The pattern cannot stop a caller that deliberately disguises one, which is why the WhatsApp service's own "no phone number" test is part of the contract.

**Nice-to-know**

10. **Should the plugin store `start`?** It keeps the first characters of the key in clear. With a per-service prefix those are mostly the prefix. Recommendation: turn it off; the prefix and name identify a key in the list output.
11. **Session sliding.** The reused session is 30 days and slides daily on use. A WhatsApp user active at least once a month keeps one session indefinitely; one who goes quiet gets a fresh one. No change recommended.
12. **Deleting a WhatsApp user.** `DELETE /api/account` with the user's own JWT (held by the WhatsApp service) cascades the account row away, so the next message from that BSUID creates a new user. This is the intended outcome; a user-facing delete flow for WhatsApp is out of scope.

## Test Scenarios

**Functional (pglite, `pnpm --filter @ansari/auth test` and apps/auth tests)**
1. Happy path: new BSUID → 200, token verifies, `sub` and `sid` match the created user and session, rows as specified.
2. Repeat: same BSUID → same `sub`, same `sid`, one session.
3. Near-expiry session: the user's only session expires in less than 15 minutes → a new session is created and its id is the `sid`.
4. Race: the conflict path on `unique(provider_id, account_id)`. Force a second insert for the same BSUID between lookup and insert (a seam, since pglite serializes); one user survives, and both callers get its token.
5. Every key state listed in the success criteria → identical 401 body, and no rows written.
6. Rate limit hit → the decided status (Open Question 5); budget exhausted → the decided status.
7. Delegation boundary: seeded admin, email user and `google` user, as in the success criteria.
8. Banned WhatsApp user → refused, sessions deleted, an earlier JWT dead at `/api/account/me`.
9. Body errors: non-JSON, missing `bsuid`, non-string, empty, too long, lower-case country code, phone-shaped → 400 (after a valid key).
10. Blocked surface: the five `/api-key/*` paths in mixed case, with trailing slashes and with suffixes → blocked; `/api-key/verify` → 404.
11. Keys never sessions: a valid key as `x-api-key` and as a bearer at `/get-session` and `/token` → no session, no JWT.
12. Reserved domain: sign-up 409 identical to taken; reset request identical to unknown, mailer not called; hook refusal on a Better Auth path; sign-in identical to unknown.
13. Scripts: create (prints once; row holds the hash only), list (no secrets), rotate (two live keys), revoke (next exchange 401); each refuses outside the image.

**Non-functional**
14. Logging capture across scenarios 1–13: no raw key, hash, BSUID, phone-shaped value or driver error object in any output.
15. Outage: the auth database stopped under a running apps/auth → the exchange answers 500, never 401 (production-shaped: `NODE_ENV=production`).
16. End to end in apps/api's contract harness: exchange token → `requireUser` → `public.users` row.
17. Staging smoke (manual, WhatsApp builder): phone-visible and phone-hidden users get answers.

## Risks and Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| A WhatsApp key mints a token for a non-WhatsApp user | Low | Critical | Provider fixed in code; lookup only on `(provider_id='whatsapp', account_id)`; the BSUID pattern refuses UUIDs and emails; delegation-boundary tests with seeded admin, email and other-provider users |
| A leaked key creates unlimited users | Low | High | Per-key rate limit and daily budget; rotate and revoke scripts; created users are inert (no credential, no sign-in); an alertable count of new WhatsApp users is a plan consideration |
| The plugin's HTTP surface reachable through a variant path | Low | Critical | Same exact plus case-insensitive prefix blocking as the admin paths, with variant tests; `enableSessionForAPIKeys` pinned false with a test |
| A raw key or BSUID in logs (including better-auth's logger) | Medium | High | `{name, code}`-only error logging; log-capture test across every case; the create script's single print is the only output of the key |
| Token signed from a session that dies first, so apps/api's first use fails | Medium | Medium | Reuse only sessions that outlive the JWT; end-to-end test through `requireUser` |
| Concurrent first requests create two users for one BSUID | Medium | High | `unique(provider_id, account_id)` plus transaction rollback on conflict; a deliberate conflict-path test |
| Migration `0001` collides with the cutover's numbering, or is shipped unapplied | Medium | High | Main numbers it before the journal is committed; human-applied before deploy; the parity tests and deploy order apply |
| Rebase conflicts after the spec 300 cutover | High | Low | The PR waits for the cutover; the rebase is expected; the tests pin the behaviour, not line positions |
| A future plugin or core bump changes deletion-on-expiry, permission order or the endpoint list | Medium | Medium | Tests pin each behaviour relied on; the plugin is pinned to the core version |
| A phone number reaches apps/auth through a misbehaving caller | Low | High | The BSUID pattern cannot match a phone number; refused before lookup or log; the "no phone number" test extends to the exchange |

## References

- Issue #329 (this spec); spec 321 / PR #327 (WhatsApp, the first consumer); issue #314 (MCP, the second consumer)
- `codev/specs/300-better-auth-migration.md`: two databases, reserved addresses, account endpoints, rate limits and client IP
- `codev/resources/arch.md`: Authentication & Authorization
- `codev/resources/lessons-learned.md`: Better Auth migration (spec 300); Auth hardening (spec 4)
- `@better-auth/api-key` 1.7.7 package source (`dist/index.mjs`): schema, endpoints, `validateApiKey` order
- `docs/self-hosting.md` (to be updated: service keys, scripts, deploy order)
