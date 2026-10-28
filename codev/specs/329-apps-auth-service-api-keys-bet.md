# Specification: Service API keys and the WhatsApp token exchange (apps/auth)

## Problem Statement

apps/auth knows only one kind of caller: a person who signs in. The only way into apps/api is a user JWT minted from a live Better Auth session. Nothing can act on behalf of a user who never signs in, and apps/auth has no way for a service to prove who it is.

The WhatsApp service (spec 321, PR #327) needs exactly that. A WhatsApp user is identified only by Meta's business-scoped user id (BSUID). They never visit a sign-in page, yet their messages must reach apps/api as an ordinary user, with threads, limits and deletion, under one stable UUID. Spec 321 assumes an apps/auth endpoint that takes a BSUID and returns a JWT. The endpoint does not exist, and the builder has run nothing against a live apps/auth. #314 (MCP) needs the same service-identity primitive later.

Two things are missing:

1. **Service identity**: a credential a service holds, issued and revoked by an operator, hashed at rest, scoped per service and per action, with its own throttle.
2. **A delegation rule**: which users a given service may mint tokens for. For WhatsApp the rule is "only users reachable through a `whatsapp` account row, keyed by BSUID, by a key owned by the WhatsApp service". A WhatsApp key must never yield a token for an email user, an admin, a system identity, a service user or another provider's user.

Affected: the WhatsApp service and its users (no path into apps/api today), the operator (who will hold a new credential type), and the security model of apps/auth (a new credential, table and public route).

## Current State

- **Callers.** apps/auth serves Better Auth's router at `/api/auth/*`, the account endpoints at `/api/account/*` (bound to the caller's own JWT and a live `sid`), `/health` and a diag route. There is no service credential of any kind. The leaderboard uses a static shared secret on apps/api; MCP (`/v2/mcp-complete`) has none (#314).
- **Tokens.** apps/api accepts only apps/auth's EdDSA JWT (15 minutes, payload `{sub, sid}` plus `iss`, `aud`, `iat`, `exp`), verified against the public JWKS. On first use, `requireUser` → `resolveUser` fetches `GET /api/account/me` with that JWT, which requires the `sid` to name a live row in `auth.session`, and then inserts `public.users` under the same UUID. A bare signature with no session behind it is therefore useless: first use fails.
- **Reserved addresses.** `@system.ansari.chat` is reserved. Sign-up refuses it with the same 409 as a taken address; the reset sender skips it silently. The `user.create.before` database hook refuses it on every path, because spec 300 says the auth database never holds one (system identities live only as app rows found by `system_key`). apps/api's `resolveUser` refuses a system-domain address and nothing else, so a second reserved domain passes apps/api's first use unchanged. There is no second reserved domain.
- **Accounts.** `auth.account` carries `unique(user_id, provider_id)` (one row per provider per user). There is no `unique(provider_id, account_id)`. Only the `credential` provider exists today (no social providers are configured).
- **Blocked paths.** `BLOCKED_AUTH_PATHS` (the admin plugin's whole surface, change-email, update-user, change-password) goes into `disabledPaths`, which better-auth matches exactly. apps/auth also blocks every case and prefix variant before the handler. Both are pinned by tests over `ADMIN_PATHS`.
- **Rate limits.** `ROUTER_RULES` are all `false`: apps/auth sees apps/api's egress IP on every legacy call, so per-IP rules there would throttle everyone together. Routes outside Better Auth's handler (the account routes) carry no per-IP rule at all; they have a per-user budget.
- **Operator scripts.** `grant-admin.ts` shows an account, then acts only on an explicit `--confirm <id>`. It never prints secrets, and runs from the auth image or from a checkout with the auth `DATABASE_URL`. No script refuses to run outside the image, and none prints the database host. The issue's "refuse to run outside the auth image" describes a guard that does not exist; a marker could not prove the location anyway. This spec drops it (a correction to the issue, directed by the security architect).
- **Better Auth's logger.** `createAuth` sets no `logger`, so better-auth logs error objects as they are.
- **`@better-auth/api-key` 1.7.7.** Peer `better-auth ^1.7.7`. Read from the published package and **proven against pglite** (throwaway probe in the specify phase, with the plugin wired to the real `auth` schema):
  - The `apikey` model: `configId` (default `"default"`), `name`, `start`, `referenceId` (the owning user's id, since `references` defaults to `"user"`), `prefix`, `key` (SHA-256, base64url), `refillInterval`, `refillAmount`, `lastRefillAt`, `enabled`, `rateLimitEnabled`, `rateLimitTimeWindow`, `rateLimitMax`, `requestCount`, `remaining`, `lastRequest`, `expiresAt`, `createdAt`, `updatedAt`, `permissions` (JSON text), `metadata` (JSON text, kept only with `enableMetadata: true`). The owner column is `referenceId`, not `userId` as older docs show.
  - HTTP endpoints: `/api-key/create`, `/api-key/get`, `/api-key/list`, `/api-key/update`, `/api-key/delete`. `verifyApiKey` and `deleteAllExpiredApiKeys` are `serverOnly`, with no route.
  - `enableSessionForAPIKeys` defaults to `false`. Only when it is true does a before-hook turn an `x-api-key` header into a session on every Better Auth route.
  - `verifyApiKey` order: hash found → `enabled` → `expiresAt` → `permissions` → exhausted → consume `remaining` and a rate-limit slot, atomically against the row. Probe results:

    | Case | Result | Row |
    |---|---|---|
    | `remaining` reaches 0, **no refill** | refused `USAGE_EXCEEDED` | **deleted** on the next verify |
    | `remaining` reaches 0, **with refill** | refused `USAGE_EXCEEDED` until the interval passes, then refilled (first refill from a null `lastRefillAt` and later cycles both work) | kept |
    | per-window rate limit exceeded | refused `RATE_LIMITED` | kept |
    | expired | refused `KEY_EXPIRED` | **deleted** |
    | wrong permission | refused `KEY_NOT_FOUND`, `remaining` untouched | kept |

  - Every failure is caught and returned as `{ valid: false, error }`. An error that is not an `APIError`, such as a database driver error, comes back as `INVALID_API_KEY`, the same code as an unknown key. **An outage is therefore indistinguishable from a bad key** in the result. Before returning, the plugin logs the caught error object through better-auth's logger, raw driver errors included.
  - `start` keeps the first 6 characters in clear (with the prefix `wa_`: the prefix plus 3 random characters). A default key is the prefix plus 64 random letters.

## Desired State

### 1. Service identity

- `packages/auth` carries `@better-auth/api-key` pinned to the core version (1.7.7). Its `apikey` table is written by hand in `schema.ts`. It mirrors the plugin's model exactly, with the column names better-auth's generator would produce, in the existing `auth` schema style (uuid ids, `timestamptz`, `reference_id` a uuid referencing `auth.users` with cascade delete). The schema comment records the departures, as it does for the other tables. It ships as migration `0001`, generated, reviewed as SQL and applied by hand at deploy. Better Auth never changes the database shape.
- **A service is one auth user**, created only by an operator script. Its address is `service-<service name>@noreply.askansari.ai` (Part 3; e.g. `service-whatsapp@noreply.askansari.ai`), and it has no credential, role `user`, and exactly one account row `{ providerId: 'service', accountId: <service name> }`. That row is how the service user is resolved (never by email), as system identities are resolved by `system_key`. One user per service: `whatsapp` now; later `mcp-faithful-answers`, and the leaderboard if it migrates. A service user cannot sign in: there is no `service` provider in Better Auth, and it has no credential.
- **Permissions are fixed per service, in code.** The script has no free-form permission input: the `whatsapp` service always gets `{ whatsapp: ['mint'] }`, and `mcp-faithful-answers` will get `{ mcp: ['complete'] }` (#314). A key's permission and its owner always agree.
- **Keys are issued, listed, rotated and revoked only by operator scripts**, in `grant-admin.ts`'s posture:
  - refuse to run without a database URL;
  - show the service account first, and act only on an explicit `--confirm <id>` of that account;
  - print the database host before any write;
  - log which key id they acted on;
  - print nothing secret, except the new key exactly once at creation.

  Each key has a per-service prefix, a name, `metadata` carrying the partner's client id, a per-window rate limit (`rateLimitTimeWindow`, `rateLimitMax`, required, no defaults), a daily budget (`remaining` with `refillAmount` and `refillInterval`, Open Question 3) and an expiry. **The script refuses `remaining` without a refill**: proven above, that configuration deletes the key the first time a burst exhausts it. Rotate creates a second live key for the same service, so the consumer can switch without downtime; revoke deletes one by id. The plugin pruning an expired key on verify is accepted: the scripts' key-id logging leaves the trail.
- **No HTTP surface.** The five `/api-key/*` paths join `BLOCKED_AUTH_PATHS` and are blocked like the admin paths, in every case and prefix variant. `enableSessionForAPIKeys` stays `false`, so a key is never a session anywhere on the router.
- **Verification is `auth.api.verifyApiKey`, in-process**, with the route's required permission passed in. **The route then also binds the key's `referenceId` to its own service user** (resolved by the `service` account row). A key for another service fails twice over: by permission and by owner. No verify route at the public origin is built here; #314 adds one.
- **Better Auth's logger is redacted.** `createAuth` configures better-auth's `logger` so an error argument is reduced to `{name, code}` and nothing else, keeping the plugin's catch-and-log from emitting driver errors. This applies to every better-auth log, which is the existing rule ("never log a raw driver error") made true for the library too.

### 2. The WhatsApp token exchange: `POST /api/whatsapp/session`

Mounted outside Better Auth's handler path, like the account routes, so no router per-IP rule sees it (the WhatsApp service calls from a Railway egress IP). `ROUTER_RULES` stays all `false`. The key's rate limit and daily budget are the throttle.

**Contract** (replaces the assumption in spec 321 and the `X-Whatsapp-Api-Key` header in #327):

| | |
|---|---|
| Request | `Authorization: Bearer <api key>`; JSON body `{ "bsuid": "<BSUID>" }` |
| 200 | `{ "token": "<JWT>" }`: the same EdDSA 15-minute `{sub, sid}` token apps/auth issues every client, signed from a live session of the BSUID's user |
| 400 | `{ "message": "Invalid request" }`: body not JSON, `bsuid` missing, not a string or not matching the BSUID pattern |
| 401 | `{ "message": "Unauthorized" }`, byte-identical for **every** key refusal: missing, malformed, unknown, revoked, disabled, expired, budget-exhausted, rate-limited, wrong permission, or owned by another service |
| 403 | `{ "message": "Forbidden" }`: the BSUID's user is banned |
| 500 | `{ "message": "Internal error" }` for anything else, including an auth-database outage. Never 401, so the caller does not read an outage as a revoked key |

Every `valid: false` from the plugin, including its own 429s, maps to the one 401. Because the plugin reports an outage as `INVALID_API_KEY`, the route **checks that the database answers before it returns 401**; if that check fails, the answer is 500.

**Per request:**

1. Verify the key with permission `whatsapp: mint`. A refusal → database check → 401 (or 500). The key's `referenceId` must be the WhatsApp service user, else 401.
2. Validate the body and the BSUID pattern → 400. The key comes first, so an unauthenticated caller learns nothing about the body rules. A phone number is never accepted, stored or logged.
3. Look up `auth.account` where `provider_id = 'whatsapp'` and `account_id = <bsuid>`. The provider is fixed in code; the lookup never goes through email.
4. No row: create the user and the account row `{ providerId: 'whatsapp', accountId: <bsuid>, userId }` in **one transaction**. The user has no credential, `registered_via = 'whatsapp'`, the display name `WhatsApp user`, and the address `whatsapp-<user id>@noreply.askansari.ai`. The exchange generates the user's UUID itself before the insert, and writes the id and the address together in the transaction. The address is therefore unique by construction (ids are the primary key), and carries neither the BSUID nor a phone number. A new `unique(provider_id, account_id)` makes a BSUID map to exactly one user under concurrency: the losing request rolls back and uses the winner's user.
5. If the user is banned (the admin plugin's `banned`, honouring `banExpires`), delete all their sessions and answer 403.
6. **Exactly one session per WhatsApp user.** Session work for one user is serialized (a lock on that user's row, held for the step). A live session that outlives the JWT about to be minted (it expires more than one JWT lifetime from now) is reused. Otherwise the user's sessions are deleted and one new session is created. The session carries no IP address and no user agent. Concurrent requests for one user therefore end with one session, and a token is never signed from a session that dies before the token does.
7. Sign the JWT from that session (the jwt plugin's issuer, audience, lifetime and `{sub, sid}` payload) and return it.

The WhatsApp service caches the token per BSUID for under 15 minutes and calls apps/api with it. apps/api resolves the user on first use exactly as for a web user (`GET /api/account/me` with the token, `public.users` under the same UUID). **apps/api gains no WhatsApp code** (spec 321 success criterion).

### 3. A non-interactive address domain

A second reserved domain, **`@noreply.askansari.ai`** (owner decision 1), for service users and WhatsApp users. The kind of account is a prefix of the local part, followed by its id or name:

| Account | Address | How many |
|---|---|---|
| WhatsApp user | `whatsapp-<user id>@noreply.askansari.ai` | one per person who messages Ansari on WhatsApp |
| Service user | `service-<service name>@noreply.askansari.ai`, e.g. `service-whatsapp@…`, later `service-mcp-faithful-answers@…` | one per service that holds an API key |

Prefixes keep each kind together in listings and logs, and let a `LIKE 'whatsapp-%'` count use the email index. The reserved check looks only at the domain, and nothing ever finds a user by email, so the prefix carries no security weight. Ordinary `@askansari.ai` addresses (staff, admins) are not reserved and keep working.

The reserved domain is:

- refused by sign-up with the same 409 as a taken address (no oracle);
- skipped by the reset-request sender with the same silent success as an unknown address (no mail attempt);
- never created by any Better Auth path: the `user.create.before` hook refuses both reserved domains unconditionally;
- created only by the exchange and by the service-user script, which write their rows directly in a transaction and never pass through a Better Auth path.

A user in this domain has no password, so they cannot sign in anywhere. A WhatsApp user's only account row is `whatsapp`; a service user's only one is `service`. `GET /api/account/me` answers for them only with their own JWT and a live `sid`, as for everyone. The system domain keeps every rule it has today; the auth database still never holds a system-domain address.

## Success Criteria

**Service keys**
- [ ] `@better-auth/api-key` is pinned to exactly the installed `better-auth` version (1.7.7); core is not bumped.
- [ ] The `apikey` table mirrors the plugin's model with generator column names, in the `auth` schema (uuid id, `timestamptz`, `reference_id` uuid → `auth.users` on delete cascade). Migration `0001` is the reviewed `drizzle-kit generate` output, numbered by main. The schema tests cover the table.
- [ ] Each of `/api-key/create`, `/api-key/get`, `/api-key/list`, `/api-key/update`, `/api-key/delete` returns the blocked response over HTTP, in every case and prefix variant, as the admin paths do. `/api-key/verify` has no route.
- [ ] `enableSessionForAPIKeys` is `false`. A valid key sent as `x-api-key` and as a bearer to `/api/auth/get-session` and `/api/auth/token` yields no session and no token.
- [ ] A stored key row holds only the hash; the raw key, and any random part of it, appears nowhere in the database (`start` not stored).
- [ ] The create script prints the raw key exactly once. List, rotate and revoke print ids, names, prefixes, permissions, limits and dates, never a key or a hash.
- [ ] Every script refuses to run without a database URL, shows the account and writes only with a matching `--confirm <id>` (a mismatch writes nothing), prints the database host before writing, and logs the key id it acted on.
- [ ] The create script refuses `remaining` without `refillAmount` and `refillInterval`, and refuses a missing rate limit.
- [ ] The service-user script creates `service-<service name>@noreply.askansari.ai` and its `service` account row in one transaction, and refuses an existing service name.
- [ ] Rotate leaves two live keys, both verifying. Revoke makes the revoked key fail at the exchange with the 401 body on the next request.
- [ ] Exhausting a refilled budget refuses the key and keeps the row; after the refill interval the same key verifies again (pins the probe result).

**Exchange: key refusals** (each returns the identical 401 body, and none creates a user, account or session)
- [ ] no `Authorization` header; non-Bearer scheme; empty bearer;
- [ ] unknown key; revoked key; disabled key; expired key;
- [ ] budget exhausted; per-window rate limit exceeded;
- [ ] key with `{ mcp: ['complete'] }` only; key with no permissions;
- [ ] a key carrying `{ whatsapp: ['mint'] }` but owned by a different service user (seeded directly, since the script cannot produce it).

**Exchange: identity**
- [ ] A new BSUID creates exactly one user, one `whatsapp` account row and one session. It returns a JWT that verifies against apps/auth's JWKS with the same `alg`, `iss`, `aud` and lifetime as a `/api/auth/token` JWT, payload `{sub, sid}` naming that user and session.
- [ ] The created user's address is exactly `whatsapp-<sub>@noreply.askansari.ai`, where `<sub>` is the token's subject.
- [ ] The same BSUID twice returns the same `sub` and the same `sid`; the user has one session.
- [ ] A session expiring within one JWT lifetime is replaced: the old one is deleted, and the token's `sid` names the new one.
- [ ] Concurrent first requests for one BSUID produce one user and one account row, and all return a token for that user. Concurrent requests for an existing user leave exactly one session. The tests exercise the conflict and lock paths themselves (pglite serializes connections, so sequential calls would prove nothing).
- [ ] **Delegation boundary.** Seed an admin, an email user, a `google` user whose `account_id` is BSUID-shaped, and a service user. Passing the admin's and email user's ids and emails as `bsuid` is refused with 400. Passing the `google` user's `account_id` returns a token for a new WhatsApp user, never the seeded one. Passing the service name is refused with 400, or reaches no service user. No exchange response ever carries the `sub` of a user without a `whatsapp` account row.
- [ ] A banned WhatsApp user gets 403, and all their sessions are deleted; a JWT minted before the ban then fails `GET /api/account/me`. An expired ban (`banExpires` in the past) is not refused.
- [ ] Database stopped under a running apps/auth (production-shaped: `NODE_ENV=production`) → the exchange answers 500 for a valid key and for an unknown key, never 401. A request with no bearer at all is 401 without touching the database.

**Exchange: privacy and logging**
- [ ] A phone-number-shaped `bsuid` (E.164 with and without `+`, digits only) is refused with 400, after the key and before any lookup or log.
- [ ] No created row (user email and name, session IP and user agent, account) contains the BSUID except `account.account_id`, and none contains a phone number.
- [ ] No log line, alert or response from the exchange, the scripts or better-auth's logger contains a raw key, a key hash, a BSUID or a driver error object; errors log `{name, code}` only. A test captures `console.*` across every refusal, failure and outage case and asserts none of those values appears.

**Reserved domain**
- [ ] Sign-up with a non-interactive-domain address (any case, surrounding spaces) returns the same 409 body as a taken address.
- [ ] Near misses are not reserved: `someone@askansari.ai`, `x@noreply.askansari.ai.example.com` and `x@notnoreply.askansari.ai` sign up normally.
- [ ] A reset request for an existing non-interactive-domain user returns the same response as an unknown address, and the mailer is never called.
- [ ] The `user.create.before` hook refuses both reserved domains on every Better Auth path.
- [ ] Sign-in with a non-interactive-domain user's address fails exactly as for an unknown address.

**End to end**
- [ ] In the existing apps/api contract tests (which start a real apps/auth), a JWT from the exchange passes `requireUser`: JWKS verification, first-use `GET /api/account/me`, and a `public.users` row under the same UUID with `registered_via = 'whatsapp'`.
- [ ] apps/api's source gains no WhatsApp-specific code.
- [ ] Staging: the WhatsApp builder's smoke test (a phone-visible and a phone-hidden user) passes against staging apps/auth and apps/api with a key issued by the create script.

**Unchanged**
- [ ] `ROUTER_RULES` are all `false`; no per-IP rule applies to the exchange.
- [ ] Every existing apps/auth, packages/auth and apps/api test passes unedited, except tests that enumerate the blocked paths or the schema, which gain the new entries.
- [ ] No new environment variable. If one appears, it is added to `turbo.json` `globalEnv` by the four-way derivation and reviewed by main.

## Constraints

- **Risk tier High** (auth model, new table, new credential type): porch's consultation on spec, plan and PR, plus the security architect's 3-way review at each gate. The owner answers decisions 2–4 at the spec-approval gate (decision 1 is answered).
- **Pin, do not bump.** The plugin's newest release requires core 1.7.14; bumping core is a separate, joint change.
- **Schema discipline.** `schema.ts` is hand-written; the table is added there in its style. `drizzle-kit generate` → reviewed SQL → applied by a human with `psql -v ON_ERROR_STOP=1 --single-transaction` against the auth database. Never `db:push`. Main numbers migrations: `architect:main` is messaged before the journal entry is committed. Migration before code at deploy.
- **Two databases.** Everything here is in the auth database and apps/auth. apps/api changes only in tests. No query crosses the two.
- **Tokens.** The exchange's JWT must be indistinguishable from any other apps/auth JWT, and must be signed from a real, live session.
- **Client IP.** No per-IP rule on the exchange; `ROUTER_RULES` stays all `false`.
- **No secret leaves its moment.** Never log a raw key, a key hash, a raw driver error, a BSUID or a phone number. Outside the creating process, the raw key exists only in the create script's single output.
- **Sequencing.** develop is frozen for apps/auth and packages/auth until the spec 300 production cutover and its contract PR land. Spec, plan and implementation proceed; the PR does not merge before then and expects a rebase. The migration is `0001` in `packages/auth/drizzle`, unrelated to the contract PR's app-database migration.
- **Process.** Explicit `git add` only; no AI attribution; commits `[Spec 329][Phase: x] type: message`; `.env` files are never read.

## Assumptions

- The spec 300 cutover lands without changing `createAuth`'s plugin list, the account-route mounting or `BLOCKED_AUTH_PATHS` in a way that conflicts with this design. The rebase reconciles details.
- A BSUID is `<two-letter country code>.<digits>` (seen in #327: `US.13491208655302741918`, `EG.1234567890123456789`). The pattern below is the contract unless the WhatsApp builder shows a BSUID it refuses.
- BSUIDs are stable per user per business portfolio. A user hiding or revealing their phone number does not change their BSUID.
- The WhatsApp service holds one key at a time (two during rotation) and is the only consumer of `whatsapp: mint`.
- PR #327 changes its header to `Authorization: Bearer` and its error handling to the shapes above (including 403 for a banned user). Spec 321 is not on develop, so its amendment lands on #327's branch, coordinated with that builder.
- `@better-auth/api-key` 1.7.7 behaves as probed. The tests pin every behaviour the design relies on (no HTTP surface, no session for keys, hashing, permission before budget, refilled keys kept, outage reported as `INVALID_API_KEY`), so a later bump that changes one fails loudly.

## Solution Approaches

### Approach 1 (recommended): plugin for keys; identity rows written directly in one transaction

Keys come from `@better-auth/api-key` (hashing, permissions, budgets, atomic usage accounting), with its HTTP surface blocked and its logger redacted. The exchange verifies with `auth.api.verifyApiKey`, binds the owner, then creates the WhatsApp user and account row in its own Drizzle transaction in `packages/auth`, the same pattern as `banUserByEmail`'s update plus session delete. It manages the one session under a per-user lock and signs with the jwt plugin. The service-user script writes its user and `service` row the same way.

Because nothing here calls `internalAdapter.createUser`, the `user.create.before` hook needs no "who is calling" exception. It refuses **both** reserved domains on every path it sees. That is stronger than the issue's wording ("permitted by the database hook only when the creating context is the exchange route or an operator script"), with the same outcome: no Better Auth path can create such an address, and there is no context check to get wrong.

- **Pros:** user and account are atomic in one real transaction (provable against pglite). The reserved-domain hook stays unconditional. The delegation rule is one parameterized lookup with the provider fixed in code, plus an owner check. No credential crypto is maintained here.
- **Cons:** the exchange writes `auth.users` and `auth.account` rows itself, so it must match Better Auth's row shape. The hand-written schema pins it, and tests read the rows back through Better Auth. Sessions keep Better Auth's token shape.
- **Risk:** low to medium. The concurrency paths need deliberate tests.

### Approach 2: plugin for keys; identity through Better Auth's internal adapter with a context marker

As Approach 1, but users and accounts are created through `internalAdapter.createUser` / `createAccount`. The hook permits the non-interactive domain only when a marker set by the exchange or the script is present in its context.

- **Pros:** every row is written by Better Auth's own code.
- **Cons:** the internal adapter's calls are not one transaction, so a crash between user and account leaves an orphan user that no BSUID reaches. The hook's safety rests on a marker no HTTP path can forge, a subtle invariant across better-auth upgrades.
- **Risk:** medium.

### Approach 3: hand-rolled service keys (no plugin)

A small table of hashed keys with permissions and limits, verified by apps/auth's own code.

- **Pros:** no dependency; no surface to block; outage and refusal are distinguishable by construction.
- **Cons:** re-implements the atomic accounting, expiry, refill and rotation the plugin already does and tests. Diverges from the issue's design and from #314's expected consumer model.
- **Risk:** medium (new credential code). Rejected. The plugin's two weaknesses found here (outage as `INVALID_API_KEY`, raw error logging) are closed by the database check and the redacted logger.

**Recommendation: Approach 1.**

## Open Questions

**Critical: owner decisions, answered at the spec-approval gate (decision 1 answered; 2–4 have a recommendation, not settled)**

1. ~~The non-interactive domain name.~~ **Decided by the owner (2026-10-28):** `@noreply.askansari.ai`, a fixed reserved subdomain with the account kind as a local-part prefix (`whatsapp-<user id>`, `service-<service name>`). This follows the common convention (GitHub's `users.noreply.github.com`, Apple's `privaterelay.appleid.com`). A hostname per user (e.g. `whatsapp-<id>.askansari.ai`) was considered and rejected: the reserved check would have to pattern-match hostnames instead of testing one fixed suffix, and the id would sit in a DNS label. The bare `@askansari.ai` was rejected because it would refuse real staff addresses.
2. **Store the BSUID raw in `account.account_id`, or HMAC it with an apps/auth secret.** Recommendation: raw. The BSUID is already Meta's business-scoped pseudonym and is needed for lookup, as any OAuth provider id is. HMAC defends against correlation after a database leak, but costs a new secret, a new env var and a rotation story that re-keys every row.
3. **The WhatsApp key's limits**, sized from the legacy WhatsApp service's traffic. The probe shows a refilled daily budget is safe: an exhausted key is refused and kept, and verifies again after the refill. So the decision is two numbers: a per-window ceiling (`rateLimitMax` per `rateLimitTimeWindow`, e.g. per minute) and a daily budget (`refillAmount` per 24 h). Recommendation: both, at about twice the legacy peak. The create script requires both explicitly (no defaults), and the values are recorded in the runbook. One exchange per BSUID per 15 minutes means the budget scales with active conversations, not messages. If the owner prefers a single cap, the per-window ceiling alone is safe; a budget without refill is not offered, because it destroys the key when exhausted.
4. **The leaderboard: migrate from its static secret now or later.** Recommendation: later. Nothing forces it, and this spec ships one consumer.

**Resolved in this revision** (by the security architect's direction or by builder decision; recorded so the plan does not re-decide them)

5. Throttle statuses: every key refusal, including the plugin's 429s, is the one 401 (architect).
6. Banned user: 403 `{ "message": "Forbidden" }`, so the WhatsApp service can tell "this user is banned" from "my key is bad". The caller is a trusted service holding a valid key, so this is no public oracle.
7. Order: key first, then body.
8. Image guard: dropped. The scripts follow `grant-admin.ts`'s posture plus a printed database host (architect).
9. BSUID pattern: `^[A-Z]{2}\.[0-9]{10,32}$`, matching both samples in #327. A bare phone number (E.164, with or without `+`) cannot match, because it has no country-code-and-dot prefix. The pattern cannot stop a caller that deliberately disguises one, which is why the WhatsApp service's own "no phone number" test is part of the contract. The WhatsApp builder is asked to confirm it against real webhooks before the PR.
10. `start` is not stored.

**Nice-to-know**

11. **Session sliding.** The reused session is 30 days and slides daily on use. A WhatsApp user active at least monthly keeps one session indefinitely; a quiet one gets a fresh one. No change.
12. **Deleting a WhatsApp user.** `DELETE /api/account` with the user's own JWT (held by the WhatsApp service) cascades the account row away, so the next message from that BSUID creates a new user. This is intended; a user-facing delete flow for WhatsApp is out of scope.
13. **New-user volume as a signal.** A leaked key could create users up to its budget. An alert on WhatsApp user creations per window would surface that; it is a plan-level option, not a requirement.

## Test Scenarios

**Functional (pglite: packages/auth and apps/auth suites)**
1. Happy path: new BSUID → 200; token verifies; `sub` and `sid` match the created user and session; rows as specified.
2. Repeat: same BSUID → same `sub`, same `sid`, one session.
3. Near-expiry session: the user's only session expires in under 15 minutes → it is deleted, a new one is created, and its id is the `sid`.
4. Races, through test seams (pglite serializes connections): a second insert for the same BSUID lands between lookup and insert, and one user survives with both callers getting its token. Two session steps for one user run concurrently, leaving one session.
5. Every key refusal in the success criteria → the identical 401 body, no rows written.
6. Owner binding: a `whatsapp: mint` key owned by another service user → 401.
7. Delegation boundary with the seeded admin, email user, `google` user and service user.
8. Banned WhatsApp user → 403, sessions deleted, an earlier JWT dead at `/api/account/me`; an expired ban is let through.
9. Body errors after a valid key: non-JSON, missing `bsuid`, non-string, empty, too long, lower-case country code, phone-shaped → 400.
10. Blocked surface: the five `/api-key/*` paths in mixed case, with trailing slashes and suffixes → blocked; `/api-key/verify` → 404.
11. Keys are never sessions: a valid key as `x-api-key` and as a bearer at `/get-session` and `/token` → no session, no JWT.
12. Reserved domain: sign-up 409 identical to taken; reset request identical to unknown, mailer not called; hook refusal on a Better Auth path; sign-in identical to unknown.
13. Scripts: no database URL → refused; show without `--confirm` → nothing written; a mismatched `--confirm` → nothing written. Create prints the key once and the row holds only the hash. `remaining` without refill → refused. List prints no secrets. Rotate → two live keys. Revoke → next exchange 401. The database host is printed before each write, and the key id is logged.
14. Refilled budget: exhaust → refused, row kept; advance past the interval → verifies.

**Non-functional**
15. Log capture across scenarios 1–14 (console and better-auth's logger): no raw key, hash, BSUID, phone-shaped value or driver error object.
16. Outage (production-shaped): database stopped under a running apps/auth → 500, never 401, for a valid key and for an unknown key.
17. End to end in apps/api's contract harness: exchange token → `requireUser` → `public.users` row.
18. Staging smoke (manual, WhatsApp builder): phone-visible and phone-hidden users get answers.

## Risks and Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| A WhatsApp key mints a token for a non-WhatsApp user | Low | Critical | Provider fixed in code; lookup only on `(provider_id='whatsapp', account_id)`; key owner bound to the WhatsApp service user; the BSUID pattern refuses UUIDs and emails; delegation-boundary tests |
| An outage answered as 401, so the WhatsApp service treats its key as revoked | Medium | High | Database check before every 401; production-shaped outage test |
| The plugin logs a raw driver error | High without mitigation | High | Better Auth's logger redacted to `{name, code}`; log-capture test through an outage |
| A burst deletes the production key | High if `remaining` were set without refill (probed) | High | The script refuses `remaining` without a refill; refilled exhaustion pinned as refused-and-kept |
| A leaked key creates many users | Low | High | Per-window ceiling and daily budget; rotate and revoke scripts; created users are inert; optional creation alert (Open Question 13) |
| The plugin's HTTP surface reachable through a variant path | Low | Critical | Exact plus case-insensitive prefix blocking as for the admin paths, with variant tests; `enableSessionForAPIKeys` pinned false with a test |
| A token signed from a session that dies first, failing apps/api's first use | Medium | Medium | Reuse only sessions that outlive the JWT; end-to-end test through `requireUser` |
| Concurrent requests create two users for one BSUID, or several sessions for one user | Medium | High | `unique(provider_id, account_id)` with rollback on conflict; per-user lock around the session step; seam-driven concurrency tests |
| Migration `0001` collides with the cutover's numbering, or ships unapplied | Medium | High | Main numbers it before the journal is committed; human-applied before deploy; deploy order |
| Rebase conflicts after the spec 300 cutover | High | Low | The PR waits for the cutover; tests pin behaviour, not line positions |
| A plugin or core bump changes deletion, permission order, error mapping or the endpoint list | Medium | Medium | Each relied-on behaviour is pinned by a test; the plugin is pinned to the core version |
| A phone number reaches apps/auth through a misbehaving caller | Low | High | The pattern cannot match a bare phone number; refused before lookup or log; the "no phone number" test extends to the exchange |

## References

- Issue #329 (this spec); spec 321 / PR #327 (WhatsApp, the first consumer); issue #314 (MCP, the second consumer)
- `codev/specs/300-better-auth-migration.md`: two databases, reserved addresses, account endpoints, rate limits and client IP
- `codev/resources/arch.md`: Authentication & Authorization
- `codev/resources/lessons-learned.md`: Better Auth migration (spec 300); Auth hardening (spec 4)
- `@better-auth/api-key` 1.7.7 package source (`dist/index.mjs`: schema, endpoints, `validateApiKey`, `consumeRemaining`, `verifyApiKey`'s catch), and the specify-phase pglite probe whose results are tabled above
- `docs/self-hosting.md` (to be updated: service keys, scripts, deploy order)
