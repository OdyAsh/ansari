# Specification: Service API keys and the WhatsApp token exchange (apps/auth)

## Problem Statement

apps/auth knows only one kind of caller: a person who signs in. The only way into apps/api is a user JWT minted from a live Better Auth session. Nothing can act on behalf of a user who never signs in, and apps/auth has no way for a service to prove who it is.

The WhatsApp service (spec 321, PR #327) needs exactly that. A WhatsApp user is identified only by Meta's business-scoped user id (BSUID). They never visit a sign-in page, yet their messages must reach apps/api as an ordinary user (threads, limits, deletion) under one stable UUID. Spec 321 assumes an apps/auth endpoint that takes a BSUID and returns a JWT. The endpoint does not exist, and the builder has run nothing against a live apps/auth. #314 (MCP) needs the same service-identity primitive later.

Two things are missing:

1. **Service identity**: a credential a service holds, issued and revoked by an operator, hashed at rest, scoped per service and per action, with its own throttle.
2. **A delegation rule**: which users a given service may mint tokens for. For WhatsApp the rule is "only a user whose sole account row is `whatsapp`, keyed by BSUID, and only for a key owned by the WhatsApp service". A WhatsApp key must never yield a token for an email user, an admin, a system identity, a service user, another provider's user, or a user who has gained any second account row or role.

Affected: the WhatsApp service and its users (no path into apps/api today), the operator (who will hold a new credential type), and the security model of apps/auth (a new credential, table and public route).

## Current State

- **Callers.** apps/auth serves Better Auth's router at `/api/auth/*`, the account endpoints at `/api/account/*` (bound to the caller's own JWT and a live `sid`), `/health` and a diag route. `corsFor(CORS_ORIGIN)` is mounted globally, ahead of every route. `express.json` is not global. There is no service credential of any kind. The leaderboard (the IslamicMMLU benchmark harness, driving `POST /v1/chat/completions` on apps/api) uses a static shared secret; MCP (`/v2/mcp-complete`) has none (#314).
- **Tokens.** apps/api accepts only apps/auth's EdDSA JWT (15 minutes, payload `{sub, sid}` plus `iss`, `aud`, `iat`, `exp`), verified against the public JWKS. On first use, `requireUser` → `resolveUser` fetches `GET /api/account/me` with that JWT, which requires the `sid` to name a live row in `auth.session`, and then inserts `public.users` under the same UUID. A bare signature with no session behind it is therefore useless: first use fails.
- **Sessions are credentials.** `auth.session.token` is unique, and the bearer plugin accepts an *unsigned* session token as a bearer (kept deliberately for the compatibility layer). A session token is therefore a 30-day account credential that can reach `/api/auth/token`, `/api/account/*` and `DELETE /api/account`. The jwt plugin's `/token` endpoint is wrapped by apps/auth's perimeter hook (`TOKEN_EXCHANGE_PER_SESSION`, a `rate_limit` row per session). Its `signJWT` is `serverOnly` and has no route.
- **Reserved addresses.** `@system.ansari.chat` is reserved through `isReservedAddress`, which today is just `isSystemAddress`:
  - sign-up refuses it with the same 409 as a taken address;
  - the reset sender skips it silently;
  - the `user.create.before` hook refuses it on every path (spec 300: the auth database never holds one).

  apps/api keeps its **own** copy of `isSystemAddress` / `SYSTEM_EMAIL_DOMAIN` and refuses only the system domain at first use. `grant-admin.ts` refuses the system domain and nothing else.
- **Accounts.** `auth.account` carries `unique(user_id, provider_id)`. There is no `unique(provider_id, account_id)`. Only the `credential` provider exists today.
- **Blocked paths.** `BLOCKED_AUTH_PATHS` goes into `disabledPaths`, which better-auth matches exactly. apps/auth's `blocked-paths` middleware canonicalizes the path, then gives **prefix** matching only to `/api/auth/admin`; every other blocked path is matched exactly. `ADMIN_PATHS` is derived from `admin().endpoints`, so a plugin bump cannot silently publish a route.
- **Limits.** `ROUTER_RULES` are all `false` (apps/auth sees apps/api's egress IP on legacy calls). Routes outside Better Auth's handler carry no per-IP rule. The account routes have a per-user budget (`ACCOUNT_ENDPOINT`, through `consumeWindow`). `packages/auth/src/limits.ts` is the single home of limit numbers.
- **Alerts.** `apps/auth/src/alerts.ts` sends Sentry events that carry counts and hash prefixes, never identifiers.
- **Boot.** apps/auth runs no schema assertion at boot. `users.ts`'s header mentions one, but none exists.
- **Operator scripts.** `grant-admin.ts` shows an account, then acts only on an explicit `--confirm <id>`; it prints no secrets and runs from the auth image or a checkout with the auth `DATABASE_URL`. No script refuses to run outside the image, and none prints the database host. The issue's "refuse to run outside the auth image" describes a guard that does not exist, and a marker could not prove it. This spec drops it (correction directed by the security architect).
- **Better Auth's logger.** `createAuth` sets no `logger`, so better-auth logs error objects as they are.
- **Better Auth transactions.** better-auth 1.7.7's internal adapter resolves its adapter through `getCurrentAdapter`, and `runWithTransaction` binds internal-adapter calls to an open transaction.
- **`@better-auth/api-key` 1.7.7.** Peer `better-auth ^1.7.7`. Read from the published package and **proven against pglite** (throwaway specify-phase probe, the plugin wired to the real `auth` schema):
  - The `apikey` model: `configId` (default `"default"`), `name`, `start`, `referenceId` (owning user's id; `references` defaults to `"user"`), `prefix`, `key` (SHA-256, base64url), `refillInterval`, `refillAmount`, `lastRefillAt`, `enabled`, `rateLimitEnabled`, `rateLimitTimeWindow`, `rateLimitMax`, `requestCount`, `remaining`, `lastRequest`, `expiresAt`, `createdAt`, `updatedAt`, `permissions` (JSON text), `metadata` (JSON text, kept only with `enableMetadata: true`). The owner column is `referenceId`, not `userId`.
  - HTTP endpoints: `/api-key/create`, `/api-key/get`, `/api-key/list`, `/api-key/update`, `/api-key/delete`. `verifyApiKey` and `deleteAllExpiredApiKeys` are `serverOnly`.
  - `enableSessionForAPIKeys` defaults to `false`; only when true does an `x-api-key` header become a session on Better Auth's routes.
  - `startingCharactersConfig.shouldStore` exists in the 1.7.7 types (default `true`: `start` keeps the first 6 characters, i.e. the prefix plus 3 random characters with `wa_`).
  - `verifyApiKey` order: hash found → `enabled` → `expiresAt` → `permissions` → exhausted → consume `remaining` and a rate-limit slot, atomically against the row.

    | Case | Result | Row |
    |---|---|---|
    | `remaining` reaches 0, **no refill** | refused `USAGE_EXCEEDED` | **deleted** on the next verify |
    | `remaining` reaches 0, **with refill** | refused `USAGE_EXCEEDED` until the interval passes, then refilled (first cycle from a null `lastRefillAt`, and later cycles) | kept |
    | per-window rate limit exceeded | refused `RATE_LIMITED` | kept |
    | expired | refused `KEY_EXPIRED` | **deleted** |
    | disabled | refused | kept |
    | wrong permission | refused `KEY_NOT_FOUND`, `remaining` untouched | kept |

  - Every failure is caught and returned as `{ valid: false, error }`. An error that is not an `APIError` (a driver error, or a missing `auth.apikey` relation) comes back as `INVALID_API_KEY`, the same code as an unknown key. Before returning, the plugin logs the caught error object raw through better-auth's logger.
  - The default key is the prefix plus 64 random letters.

## Desired State

### 1. Service identity

**The table.** `packages/auth` carries `@better-auth/api-key` pinned to the core version (1.7.7). Its `apikey` table is hand-written in `schema.ts`:
- it mirrors the plugin's model exactly, with the column names better-auth's generator would produce;
- it follows the `auth` schema style (uuid ids, `timestamptz`, `reference_id` a uuid referencing `auth.users` with cascade delete);
- the schema comment records the departures, as for the other tables.

It ships as migration `0001`, generated, reviewed as SQL, applied by hand before code, and numbered by main. The same migration adds `unique(provider_id, account_id)` to `auth.account`. Better Auth never changes the database shape. At boot, apps/auth asserts that `auth.apikey` exists and refuses to start if it does not (the #165 class: code shipped before its migration).

**A service is one auth user**, created only by an operator script:
- address `service-<service name>@noreply.askansari.ai` (Part 3), no credential, role `user`;
- exactly one account row `{ providerId: 'service', accountId: <service name> }`, which is how it is resolved (never by email);
- one user per service: `whatsapp` now; later `mcp-faithful-answers`, and the leaderboard if it migrates;
- it cannot sign in: there is no `service` provider in Better Auth, and it has no credential.

**Permissions are fixed per service, in code.** The script takes no free-form permissions. The `whatsapp` service gets `{ whatsapp: ['mint'] }`, and `mcp-faithful-answers` will get `{ mcp: ['complete'] }` (#314).

**Keys are created in-process with `auth.api.createApiKey`**, server-side, with the service user's id. They are never inserted by hand, so the plugin's own hashing, `permissions` and `metadata` handling apply. `enableMetadata: true`; `startingCharactersConfig.shouldStore: false`. The plan's first probe proves this path: a script-created key verifies at the exchange, its row holds exactly the hash, and no column holds any substring of the raw key.

**Key settings.** Each key has:
- a per-service prefix (`wa_` for WhatsApp) and a name;
- `metadata` carrying the partner's client id;
- a per-window rate limit (`rateLimitTimeWindow`, `rateLimitMax`) and a daily budget (`remaining` with `refillAmount` and `refillInterval` of 24 h), both required with no defaults. The create script validates them against bounds declared in `limits.ts`, the single home of limit numbers. Values are chosen by the operator from owner decision 3.
- **no expiry.** A production credential must not die silently, so rotation is driven by the runbook. The create script offers no expiry option.

The script refuses `remaining` without a refill: probed above, that configuration deletes the key the first time a burst exhausts it. **Budgets are per key, not per service:** while two keys are live during a rotation, the service's ceiling doubles. The runbook says so, and rotation overlap is kept short.

**Operator scripts** (create service user, create key, list, rotate, revoke) follow `grant-admin.ts`'s posture:
- refuse to run without a database URL;
- show the account, then write only with a matching `--confirm <id>` (a mismatch writes nothing);
- before any write, print the database **hostname and port only**, never the URL, user, password, query string or a URL parse error;
- log the key id acted on;
- print nothing secret, except the new key exactly once at creation;
- **revoke sets `enabled = false` and keeps the row** (refused and kept, as probed), so the trail survives. Rotate creates a second live key; the old one is revoked once the consumer has switched.

Every create, rotate and revoke also sends a durable Sentry event (service, key id, name, action, no key material), so the record does not live only in a terminal.

**Where the raw key lives.** In exactly two places, briefly in one and durably in the other:
- the create script's single output, in the operator's shell inside the auth service;
- the consumer's secret variable (the WhatsApp service's Railway variable), into which the operator pastes it directly.

It is never put in chat, tickets, docs, git or logs. apps/auth stores only its hash.

**No HTTP surface.**
- The blocked paths are derived from `apiKey().endpoints`, exactly as `ADMIN_PATHS` derives from `admin().endpoints`, with an assertion that the derived list has length 5 today; a plugin bump that adds an endpoint fails loudly.
- They join `BLOCKED_AUTH_PATHS`, and the middleware gains a `/api/auth/api-key` **prefix** rule like `/api/auth/admin`'s, so every canonicalized variant and suffix is blocked.
- `enableSessionForAPIKeys` stays `false`: a key is never a session anywhere on the router.

**Verification is `auth.api.verifyApiKey`, in-process**, with the route's required permission. The route also binds the key's `referenceId` to its own service user, resolved by the `service` account row:
- a key for another service fails twice over, by permission and by owner;
- if the WhatsApp service user cannot be resolved, or resolves to more than one user, that is a misconfiguration: 500 plus an alert, never 401.

No verify route at the public origin is built here; #314 adds one.

**Better Auth's logger is redacted, not silenced.** `createAuth` configures better-auth's `logger` so an argument that is an error object is reduced to exactly `{name, code}`, while the level, the message and non-error arguments survive. This closes the plugin's catch-and-log path. It changes every better-auth log line in apps/auth, so it lands as its own commit.

### 2. The WhatsApp token exchange: `POST /api/whatsapp/session`

Mounted outside Better Auth's handler path, like the account routes, so no router per-IP rule sees it (the WhatsApp service calls from a Railway egress IP). `ROUTER_RULES` stays all `false`.
- **No CORS.** No response from the exchange, preflight included, carries `Access-Control-Allow-Origin` for any origin. Browser use is structurally impossible; the global `corsFor` does not apply to this route.
- **No other credential.** A session token or a JWT presented as the bearer is just an unknown key (401). There is no fallback to user authentication, and the route is not reachable under `/api/auth/*`.
- **Body parsing** is route-local, mounted after the key check, with a strict byte limit, so an unauthenticated or oversized body costs no parsing.

**Contract** (replaces the assumption in spec 321 and the `X-Whatsapp-Api-Key` header in #327):

| | |
|---|---|
| Request | `Authorization: Bearer <api key>`; JSON body `{ "bsuid": "<BSUID>" }` |
| 200 | `{ "token": "<JWT>" }`: the same EdDSA 15-minute `{sub, sid}` token apps/auth issues every client, signed from a live session of the BSUID's user |
| 400 | `{ "message": "Invalid request" }`: body not JSON, too large, `bsuid` missing, not a string or not matching the BSUID pattern |
| 401 | `{ "message": "Unauthorized" }`, byte-identical for **every** key refusal: missing, malformed, unknown, revoked, disabled, expired, budget-exhausted, rate-limited, wrong permission, or owned by another service |
| 403 | `{ "message": "Forbidden" }`: the BSUID's user is banned |
| 429 | `{ "message": "Too many requests" }` with `X-Retry-After`: this one user's exchange budget is spent (the key itself is fine) |
| 500 | `{ "message": "Internal error" }` for anything else: database outage, `auth.apikey` missing, service user unresolvable, an ineligible user behind a `whatsapp` row, the user deleted mid-exchange |

No response echoes any input.

**Contract duties of the consumer (#327):** send `Authorization: Bearer`; cache a token per BSUID for under 15 minutes; on a 401 **from apps/api**, re-exchange once, because a session can die after signing (ban, deletion), and the token's `sid` then fails first use. Treat 401 from the exchange as "my key is bad" (page the operator), 403 as "this user is banned", 429 as "slow this user down" and 500 as "apps/auth is failing". Spec 321 is not on develop, so its amendment lands on #327's branch, coordinated with that builder.

**Outage versus refusal.** The plugin reports a database failure as `INVALID_API_KEY`, the same code as an unknown key. So before any 401 the route checks that `auth.apikey` itself answers: a query on that relation, not `select 1`, which would pass with the migration unapplied. If the check fails, the answer is 500. To keep unknown-key floods from doubling database work, a successful check is cached in-process for a few seconds. A sustained 401 volume per window raises an alert.

**Accepted residuals:**
- an outage that begins between the verify and the check (or inside the cache window) is misclassified as 401;
- a database that recovers between them turns an outage into a 401;
- an unknown-key flood still costs one plugin lookup per request (no budget can attach to a key that does not exist), and is bounded by the cache and surfaced by the 401-volume alert.

**Per request:**

1. **Key.** Verify with permission `whatsapp: mint` → on refusal, the relation check → 401 (or 500). Bind `referenceId` to the WhatsApp service user (unresolvable → 500 + alert; another owner → 401).
2. **Body.** Parse (route-local, byte-limited) and validate the BSUID pattern → 400. A phone number is never accepted, stored or logged.
3. **Resolve or create.** Look up `auth.account` where `provider_id = 'whatsapp'` and `account_id = <bsuid>`. The provider is fixed in code; the lookup never goes through email. With no row, create the user and the account row in **one transaction**:
   - the exchange generates the user's UUID itself;
   - the address is `whatsapp-<user id>@noreply.askansari.ai`, the display name `WhatsApp user`, `registered_via = 'whatsapp'`, and there is no credential;
   - the account row is `{ providerId: 'whatsapp', accountId: <bsuid>, userId }`.

   `unique(provider_id, account_id)` makes a BSUID map to exactly one user under concurrency: the losing request rolls back and uses the winner's user.
4. **Lock, then decide.** In one transaction:
   - **Lock** the user's row (`SELECT … FOR UPDATE`).
   - **Re-read under the lock.** The user gone → 500 (deleted mid-exchange; the next message creates a new user).
   - **Eligibility**, all required: the user's *only* account row is this `whatsapp` row; the address is under `@noreply.askansari.ai`; `registered_via = 'whatsapp'`; `role` is not `admin`. A violation → 500 plus a Sentry alert (user id only), never a token.
   - **Ban** (admin plugin `banned`, honouring `banExpires`) → delete all the user's sessions, commit, 403.
   - **Per-user budget** (`consumeWindow`, keyed by the user id, never the BSUID; numbers in `limits.ts`) → 429 when spent.
   - **Session.** Reuse the user's live session only if it outlives the token being signed: its `expiresAt` is at least the token's exact `exp` plus a safety margin. Otherwise delete the user's sessions and create one with `internalAdapter.createSession`, the internal-adapter precedent in `routes/account.ts`. The session token comes from better-auth's own generator, is never returned, logged or sent to Sentry, and the session carries no IP address and no user agent.
   - **Sign** the JWT with the jwt plugin's server-side `signJWT`, payload `{sub, sid}`, with the plugin's issuer, audience and lifetime.
   - **Commit**, then return the token.

   The session write and the signing run inside that same transaction (better-auth's transaction context), so a concurrent ban or deletion cannot slip between the decision and the session. **Hazard the plan must rule out:** `FOR UPDATE` conflicts with the `KEY SHARE` lock that the session insert's foreign key takes. If the insert ran on another connection, the request would wait on its own lock. The plan proves, against a real Postgres, that the session write shares the locking transaction. If better-auth's transaction context cannot carry it, the builder raises the alternative with the architect before choosing one.
   - **`/token` is not used.** Signing bypasses `/token`'s perimeter hook, so `TOKEN_EXCHANGE_PER_SESSION` and its `rate_limit` row do not apply. The per-user budget above replaces them for this route.

The invariant is: **at signing time, the session outlives the token.** A ban, deletion or revocation after signing can still kill the session; the token then fails apps/api's first use, and the consumer re-exchanges once.

The WhatsApp service caches the token per BSUID for under 15 minutes and calls apps/api with it. apps/api resolves the user on first use exactly as for a web user. **apps/api gains no WhatsApp code** (spec 321 success criterion).

**Accepted oracle residual.** A holder of the WhatsApp key can confirm whether a given BSUID has used Ansari, and whether that user is banned, both from the responses and from latency (new, existing and refused take different times). Guessed BSUIDs also create durable, inert users. This is accepted because the WhatsApp service is the delegation authority: it already holds every BSUID it would ask about. BSUIDs are not enumerable, so this is targeted confirmation, not harvesting. The mitigations are detection, not prevention: per-key request-volume alerts, `USAGE_EXCEEDED` and `RATE_LIMITED` alerts (otherwise invisible, since they collapse to 401), and rotation and revocation. No constant-time padding is built.

### 3. A non-interactive address domain

A second reserved domain, **`@noreply.askansari.ai`** (owner decision 1), for service users and WhatsApp users. The account kind is a prefix of the local part:

| Account | Address | How many |
|---|---|---|
| WhatsApp user | `whatsapp-<user id>@noreply.askansari.ai` | one per person who messages Ansari on WhatsApp |
| Service user | `service-<service name>@noreply.askansari.ai`, e.g. `service-whatsapp@…`, later `service-mcp-faithful-answers@…` | one per service that holds an API key |

Prefixes keep each kind together in listings and logs. The reserved check looks only at the domain, and nothing ever finds a user by email, so the prefix carries no security weight. Ordinary `@askansari.ai` addresses (staff, admins) are not reserved.

**Implementation boundary:** `isReservedAddress` is extended to cover both domains. `isSystemAddress` and `SYSTEM_EMAIL_DOMAIN` are not widened: they name the system-domain invariant, apps/api keeps its own copy, and existing tests assert them.

The reserved domain is:
- refused by sign-up with the same 409 as a taken address (response shape; see the timing residual below);
- skipped by the reset-request sender with the same response as an unknown address (no mail attempt);
- never created by any Better Auth path: the `user.create.before` hook refuses both reserved domains unconditionally;
- refused by `grant-admin.ts`, like the system domain;
- created only by the exchange and the service-user script, which write their rows directly in a transaction.

A user in this domain has no password, so they cannot sign in anywhere. A WhatsApp user's only account row is `whatsapp`; a service user's is `service`. `GET /api/account/me` answers for them only with their own JWT and a live `sid`. The system domain keeps every rule it has today.

**Timing residual:** sign-in and reset for an existing passwordless noreply user and for an unknown address give the same response shape, but are not guaranteed to take the same time. Only response equivalence is promised. The addresses are server-generated and not guessable from anything public, which bounds the value of the difference.

## Success Criteria

**Service keys and scripts**
- [ ] `@better-auth/api-key` is pinned to exactly the installed `better-auth` version (1.7.7); core is not bumped.
- [ ] The `apikey` table mirrors the plugin's model with generator column names, in the `auth` schema (uuid id, `timestamptz`, `reference_id` uuid → `auth.users` on delete cascade). Migration `0001` (with `unique(provider_id, account_id)`) is the reviewed `drizzle-kit generate` output, numbered by main. The schema tests cover both.
- [ ] apps/auth refuses to boot when `auth.apikey` is absent.
- [ ] The blocked list is derived from `apiKey().endpoints` and asserted to have length 5. Each path, in every canonicalized variant (case, trailing slash, percent-encoding, doubled slash, dot segments) and with any suffix, gets the block response at `/api/auth/api-key…`. `/api-key/verify` has no route.
- [ ] `enableSessionForAPIKeys` is `false`. A valid key sent as `x-api-key` and as a bearer to `/api/auth/get-session` and `/api/auth/token` yields no session and no token.
- [ ] A key created by the script verifies at the exchange. Its row holds the hash, and **no column of the row contains any substring (length ≥ 4) of the raw key** (`start` not stored, via `startingCharactersConfig.shouldStore: false`).
- [ ] The create script prints the raw key exactly once. List, rotate and revoke print ids, names, prefixes, permissions, limits and dates, never a key or a hash.
- [ ] Every script refuses to run without a database URL, writes only with a matching `--confirm <id>`, and logs the key id acted on. Before any write it prints the hostname and port only; tested with a URL carrying a user, a password and a query string, none of which appears in the output.
- [ ] The create script refuses `remaining` without a refill, a missing rate limit, values outside the `limits.ts` bounds, and any expiry option.
- [ ] The service-user script creates `service-<name>@noreply.askansari.ai` and its `service` account row in one transaction, and refuses an existing service name.
- [ ] Rotate leaves two live keys, both verifying. Revoke sets `enabled = false` and keeps the row; the revoked key then fails at the exchange with the 401 body.
- [ ] Create, rotate and revoke each send one Sentry event with service, key id, name and action, and no key material.
- [ ] Exhausting a refilled budget refuses the key and keeps the row; after the refill interval, the same key verifies again.
- [ ] `grant-admin.ts` refuses a `@noreply.askansari.ai` address, as it refuses the system domain.

**Exchange: key refusals** (identical 401 body; no user, account or session created)
- [ ] no `Authorization` header (answered without touching the database); non-Bearer scheme; empty bearer; a session token as the bearer; a JWT as the bearer;
- [ ] unknown key; revoked (disabled) key; expired key (seeded);
- [ ] budget exhausted; per-window rate limit exceeded;
- [ ] key with `{ mcp: ['complete'] }` only; key with no permissions;
- [ ] a `{ whatsapp: ['mint'] }` key owned by a different service user (seeded directly; the script cannot produce it).

**Exchange: failures that are never 401**
- [ ] `auth.apikey` absent (migration unapplied) → 500, never 401, for both a valid key and an unknown key.
- [ ] Database stopped under a running apps/auth (production-shaped, `NODE_ENV=production`) → 500, never 401, for a valid key and an unknown key.
- [ ] WhatsApp service user missing → 500 plus an alert.
- [ ] The resolved user deleted between lookup and lock → 500, no session row, no token.

**Exchange: identity and delegation**
- [ ] A new BSUID creates exactly one user (address exactly `whatsapp-<sub>@noreply.askansari.ai`), one `whatsapp` account row and one session. It returns a JWT that verifies against apps/auth's JWKS with the same `alg`, `iss`, `aud` and lifetime as a `/api/auth/token` JWT, payload `{sub, sid}` naming that user and session.
- [ ] The same BSUID twice returns the same `sub` and the same `sid`; the user has one session.
- [ ] For every returned token, the session's `expiresAt` ≥ the token's `exp` + the margin. A session that would fail this is replaced (old one deleted, `sid` names the new one).
- [ ] **Only a user whose sole account row is `whatsapp` is ever minted for.** Each of these gets 500 and an alert, never a token:
  - a `whatsapp` row attached to an email user (one with a `credential` row);
  - a `whatsapp` row attached to a service user;
  - a `whatsapp` row attached to an admin;
  - a WhatsApp user later promoted to admin (seeded, since `grant-admin.ts` refuses);
  - a WhatsApp user whose address or `registered_via` was changed.
- [ ] Passing an admin's or email user's id or email as `bsuid` is refused with 400. A `google` user whose `account_id` is BSUID-shaped is never reached (a new WhatsApp user is created instead).
- [ ] A banned WhatsApp user gets 403, all their sessions are deleted, and a JWT minted before the ban then fails `GET /api/account/me`. An expired ban is not refused.
- [ ] The per-user budget returns 429 with `X-Retry-After` once spent; another user under the same key is unaffected; the `rate_limit` key contains the user id, not the BSUID.
- [ ] **Concurrency, scoped to what each test proves.** On pglite, which serializes connections:
  - a forced conflict on `unique(provider_id, account_id)` leaves one user, and both callers get its token;
  - a ban landing between the account lookup and the lock is seen under the lock (403, no session).

  A source-level assertion pins that the eligibility re-read, the ban check, the session write and the signing all run inside the transaction that holds `FOR UPDATE`. The plan's real-Postgres probe shows the session write does not wait on that lock. Concurrent messages for one user are exercised in the staging smoke.

**Exchange: privacy, logging, transport**
- [ ] A phone-number-shaped `bsuid` (E.164 with and without `+`, digits only) → 400, after the key and before any lookup or log.
- [ ] No created row (user email and name, session IP and user agent, account, `rate_limit`) contains the BSUID except `account.account_id`, and none contains a phone number.
- [ ] Across every refusal, failure and outage case, `console.*` and better-auth's logger contain no raw key, key hash, session token, BSUID or driver error object.
- [ ] Better Auth's logger: a non-error log survives with its level and message; a known driver error passed to it is reduced to exactly `{name, code}`; a near miss (a plain object with `name`/`code`-like fields, and a non-error string argument) survives unchanged. This criterion cannot be met by disabling the logger.
- [ ] Sentry events from the exchange carry no request body and no `Authorization` header (tested on a captured event).
- [ ] No response from the exchange echoes any part of its input.
- [ ] No response from the exchange, `OPTIONS` preflight included, carries `Access-Control-Allow-Origin` for any origin, including the configured `CORS_ORIGIN`.
- [ ] A body over the byte limit → 400 (after the key), and a body sent with no key is never parsed.

**Alerts** (through `alerts.ts`: counts only, no identifiers)
- [ ] Per-key exchange request volume over a threshold per window.
- [ ] `USAGE_EXCEEDED` and `RATE_LIMITED` refusals (one event per window each).
- [ ] 401 volume per window over a threshold.
- [ ] Eligibility violations and an unresolvable service user (each occurrence).

**Reserved domain**
- [ ] Sign-up with a `@noreply.askansari.ai` address (any case, surrounding spaces) returns the same 409 body as a taken address.
- [ ] Near misses are not reserved: `someone@askansari.ai`, `x@noreply.askansari.ai.example.com` and `x@notnoreply.askansari.ai` sign up normally.
- [ ] A reset request for an existing noreply user returns the same response as an unknown address, and the mailer is never called.
- [ ] The `user.create.before` hook refuses both reserved domains on every Better Auth path.
- [ ] Sign-in with a noreply user's address returns the same response as an unknown address (shape only; see the timing residual).
- [ ] `isSystemAddress` and `SYSTEM_EMAIL_DOMAIN` are unchanged, and their existing tests pass unedited.

**End to end**
- [ ] In the existing apps/api contract tests (which start a real apps/auth), a JWT from the exchange passes `requireUser`: JWKS verification, first-use `GET /api/account/me`, and a `public.users` row under the same UUID with `registered_via = 'whatsapp'`.
- [ ] apps/api's source gains no WhatsApp-specific code.
- [ ] Staging smoke, run and signed off by the WhatsApp builder (#327) against staging apps/auth and apps/api with a key issued by the create script: a phone-visible user, a phone-hidden user, and several concurrent messages from one user (one user, one session afterwards). This is a pre-production gate, not a merge gate for this PR.

**Documentation**
- [ ] `docs/self-hosting.md` covers the new route, the service-user and key scripts, the `limits.ts` bounds, and the migration-before-code order.
- [ ] The runbook covers:
  - issuance (service user, then key, inside the auth service);
  - the handoff: paste straight into the consumer's Railway variable, never anywhere else;
  - rotation overlap and the doubled ceiling while two keys are live;
  - revocation (`enabled = false`), and pruning of expired rows (none expected, since production keys have no expiry);
  - budget sizing (owner decision 3);
  - the pre-apply check that `auth.account` holds no duplicate `(provider_id, account_id)` before `0001`, noting that adding the constraint takes an ACCESS EXCLUSIVE lock on a table every sign-in reads (apply in a quiet window);
  - the alert meanings.
- [ ] `codev/resources/arch.md` (Authentication & Authorization) describes service keys, the exchange and the noreply domain.

**Unchanged**
- [ ] `ROUTER_RULES` are all `false`; no per-IP rule applies to the exchange.
- [ ] Every existing apps/auth, packages/auth and apps/api test passes unedited, except tests that enumerate the blocked paths, the schema or the reserved check, which gain the new entries.
- [ ] No new environment variable. If one appears, it is added to `turbo.json` `globalEnv` by the four-way derivation and reviewed by main.

## Constraints

- **Risk tier High** (auth model, new table, new credential type): porch's consultation on spec, plan and PR, plus the security architect's 3-way review at each gate. The owner answers decisions 2–4 at the spec-approval gate (decision 1 is answered).
- **Pin, do not bump.** The plugin's newest release requires core 1.7.14; bumping core is a separate, joint change.
- **Schema discipline.** `schema.ts` is hand-written; `drizzle-kit generate` → reviewed SQL → applied by a human with `psql -v ON_ERROR_STOP=1 --single-transaction` against the auth database. Never `db:push`. `architect:main` is messaged before the journal entry is committed. Migration before code at deploy.
- **Two databases.** Everything here is in the auth database and apps/auth. apps/api changes only in tests. No query crosses the two.
- **Tokens.** The exchange's JWT must be indistinguishable from any other apps/auth JWT, and signed from a real session that outlives it at signing time.
- **Client IP.** No per-IP rule on the exchange; `ROUTER_RULES` stays all `false`.
- **Where secrets live.** The raw key lives in the consumer's secret variable and, once, in the create script's output; apps/auth holds its hash. Session tokens created by the exchange never leave apps/auth. Never log a raw key, a key hash, a session token, a raw driver error, a BSUID or a phone number.
- **Limits.** Every number lives in `packages/auth/src/limits.ts`.
- **Sequencing.** develop is frozen for apps/auth and packages/auth until the spec 300 production cutover and its contract PR land. Spec, plan and implementation proceed; the PR does not merge before then and expects a rebase. The migration is `0001` in `packages/auth/drizzle`.
- **Scope guard.** #314's verify route does not creep in. The leaderboard is not migrated.
- **Process.** Explicit `git add` only; no AI attribution; commits `[Spec 329][Phase: x] type: message`; `.env` files are never read.

## Assumptions

- The spec 300 cutover lands without changing `createAuth`'s plugin list, the account-route mounting or `BLOCKED_AUTH_PATHS` in a way that conflicts with this design. The rebase reconciles details.
- A BSUID is `<two-letter country code>.<digits>` (seen in #327: `US.13491208655302741918`, `EG.1234567890123456789`). The pattern below is the contract unless the WhatsApp builder shows a real BSUID it refuses.
- BSUIDs are stable per user per business portfolio. Hiding or revealing a phone number does not change a BSUID.
- The WhatsApp service holds one key at a time (two during rotation) and is the only consumer of `whatsapp: mint`.
- PR #327 adopts the contract above (header, error handling, re-exchange on an apps/api 401).
- `@better-auth/api-key` 1.7.7 behaves as probed, and better-auth 1.7.7's transaction context carries internal-adapter writes. The plan probes the parts not yet proven: `createApiKey` from a script, `shouldStore: false`, `signJWT`'s claims, the logger redaction, and `createSession` inside the locking transaction on a real Postgres. Tests pin every behaviour relied on, so a later bump that changes one fails loudly.

## Solution Approaches

### Approach 1 (recommended): plugin for keys; identity rows written directly; one locking transaction for the decision

Keys come from `@better-auth/api-key` (hashing, permissions, budgets, atomic usage accounting), created in-process by the scripts, with its HTTP surface blocked by derivation and prefix, and its logger redacted. The exchange:
- verifies the key and binds its owner;
- creates the WhatsApp user and account row in its own transaction, the pattern `banUserByEmail` uses;
- in one locking transaction: re-reads eligibility and ban, applies the per-user budget, manages the one session through `internalAdapter.createSession`, and signs with `signJWT`.

The `user.create.before` hook refuses both reserved domains unconditionally. That is stronger than the issue's "permitted only when the creating context is the exchange or a script", with the same outcome and no context check to get wrong.

- **Pros:**
  - user and account are atomic;
  - the hook stays unconditional;
  - the delegation rule is a fixed-provider lookup plus an owner check plus an eligibility check under a lock;
  - no credential crypto is maintained here.
- **Cons:**
  - the exchange writes `auth.users` and `auth.account` rows itself, so it must match Better Auth's row shape (pinned by the hand-written schema and read back through Better Auth in tests);
  - the session write must share the locking transaction (the hazard above).
- **Risk:** medium. The plan's first phase probes the unproven library paths before anything is built on them.

### Approach 2: identity through Better Auth's internal adapter with a context marker

Users and accounts are created through `internalAdapter.createUser` / `createAccount`, and the hook permits the noreply domain only when a marker set by the exchange or the script is present.

- **Pros:** every row is written by Better Auth's code.
- **Cons:**
  - user and account are not one transaction unless wrapped in the same transaction context;
  - the hook's safety rests on a marker no HTTP path can forge, a subtle invariant across upgrades.
- **Risk:** medium. Rejected in favour of an unconditional hook.

### Approach 3: hand-rolled service keys (no plugin)

A small table of hashed keys with permissions and limits, verified by apps/auth's own code.

- **Pros:** no dependency and no surface to block; outage and refusal are distinguishable by construction.
- **Cons:**
  - re-implements the atomic accounting, refill and rotation the plugin already does and tests;
  - diverges from the issue and from #314's consumer model.
- **Risk:** medium (new credential code). Rejected. The plugin's weaknesses found here (outage reported as `INVALID_API_KEY`, raw error logging, deletion on no-refill exhaustion) are closed by the relation check, the redacted logger and the script's refill rule, with the residuals stated.

**Recommendation: Approach 1.**

## Open Questions

**Critical: owner decisions at the spec-approval gate**

1. ~~The non-interactive domain name.~~ **Decided by the owner (2026-10-28):** `@noreply.askansari.ai`, a fixed reserved subdomain with the account kind as a local-part prefix (`whatsapp-<user id>`, `service-<service name>`). This follows the common convention (GitHub's `users.noreply.github.com`, Apple's `privaterelay.appleid.com`). Considered and rejected:
   - a hostname per user (e.g. `whatsapp-<id>.askansari.ai`): the reserved check would have to pattern-match hostnames instead of testing one fixed suffix, and the id would sit in a DNS label;
   - the bare `@askansari.ai`: it would refuse real staff addresses.
2. **Store the BSUID raw in `account.account_id`, or HMAC it with an apps/auth secret.** Recommendation: raw. The BSUID is already Meta's business-scoped pseudonym and is needed for lookup, as any OAuth provider id is. HMAC defends against correlation after a database leak, but costs a new secret, a new env var and a rotation story that re-keys every row.
3. **The WhatsApp key's limits**, sized from the legacy WhatsApp service's traffic. The decision is two numbers per key:
   - a per-window ceiling (`rateLimitMax` per `rateLimitTimeWindow`, e.g. per minute);
   - a daily budget (`refillAmount` per 24 h).

   Recommendation: both, at about twice the legacy peak, sized for instances × restarts, since the consumer's 15-minute token cache is per process. A refilled budget is safe: exhaustion refuses and keeps the key. The `limits.ts` bounds and the per-user exchange budget are set in the plan from the same traffic data. Budgets are per key, so rotation briefly doubles them.
4. **The leaderboard: migrate from its static secret now or later.** Recommendation: later. Nothing forces it, and this spec ships one consumer.

**Resolved** (by the security architect's direction or by builder decision; recorded so the plan does not re-decide them)

5. Every key refusal, including the plugin's 429s, is the one 401 (architect).
6. Banned user: 403 `{ "message": "Forbidden" }`, so the consumer can tell "this user is banned" from "my key is bad". This is an accepted oracle toward a trusted key holder.
7. Order: key, then body (route-local parsing after the key).
8. Image guard dropped. The scripts follow `grant-admin.ts`'s posture plus hostname-and-port output (architect).
9. BSUID pattern `^[A-Z]{2}\.[0-9]{10,32}$`, matching both samples in #327. A bare phone number (E.164, with or without `+`) cannot match. The pattern cannot stop a caller that deliberately disguises one, which is why the WhatsApp service's own "no phone number" test is part of the contract. The WhatsApp builder confirms it against real webhooks before the PR.
10. `start` not stored (`shouldStore: false`).
11. Revoke disables and keeps the row. Production keys have no expiry.
12. A per-user exchange budget inside the key's bound, keyed by user id, with its 429.

**Nice-to-know**

13. **Session sliding.** The reused session is 30 days and slides daily on use. An active user keeps one session; a quiet one gets a fresh one.
14. **Deleting a WhatsApp user.** `DELETE /api/account` with the user's own JWT cascades the account row away, so the next message from that BSUID creates a new user. This is intended; a user-facing delete flow for WhatsApp is out of scope.
15. **New-user volume.** An alert on WhatsApp user creations per window would add a second signal beside per-key volume. Plan-level option.

## Test Scenarios

**Probes first (plan phase 1, before anything is built on them)**
1. `auth.api.createApiKey` from a script context: the key verifies; `shouldStore: false` leaves no key substring in any column; `permissions` and `metadata` round-trip.
2. `signJWT` server-side: the claims match a `/api/auth/token` JWT, and `exp` is readable for the session comparison.
3. The redacted logger: non-error logs survive; error objects are reduced to `{name, code}`.
4. Real Postgres: `internalAdapter.createSession` inside the transaction that holds `FOR UPDATE` on the user completes, without waiting on its own lock.

**Functional (pglite: packages/auth and apps/auth suites)**
5. Happy path: new BSUID → 200; the token verifies; `sub`/`sid` match; rows as specified; address exact.
6. Repeat: same BSUID → same `sub`, same `sid`, one session.
7. Session margin: a session whose `expiresAt` < token `exp` + margin is replaced.
8. Conflict: a forced second insert for the same BSUID → one user, both callers get its token.
9. Ban under the lock: a ban committed between lookup and lock → 403, no session.
10. Deleted mid-exchange: the user removed between lookup and lock → 500, no session.
11. Every key refusal → the identical 401 body, no rows.
12. Owner binding: a `whatsapp: mint` key under another service user → 401. Service user missing → 500 + alert.
13. Eligibility matrix: `whatsapp` row on an email, service or admin user; a promoted WhatsApp user; an altered address or `registered_via` → 500 + alert, no token.
14. Delegation inputs: admin and email-user ids and emails as `bsuid` → 400; a BSUID-shaped `google` `account_id` → a new WhatsApp user.
15. Ban: 403, sessions deleted, an earlier JWT dead at `/api/account/me`; an expired ban is let through.
16. Per-user budget: 429 with `X-Retry-After`; another user unaffected; the `rate_limit` key holds the user id.
17. Body: non-JSON, oversized, missing, non-string, empty, too long, lower-case country code, phone-shaped → 400 (after a valid key). No key → never parsed.
18. Transport: no CORS allow-origin on any response or preflight; a session token or JWT as the bearer → 401; not reachable under `/api/auth/*`.
19. Blocked surface: the derived list has length 5; every variant and suffix under `/api/auth/api-key` is blocked; `/api-key/verify` → 404.
20. Keys are never sessions: a valid key as `x-api-key` and as a bearer at `/get-session` and `/token` → nothing.
21. Reserved domain: sign-up 409 identical to taken; near misses sign up; reset identical to unknown, mailer not called; hook refusal; sign-in identical to unknown; `grant-admin.ts` refuses noreply; `isSystemAddress` unchanged.
22. Scripts: no database URL; show without confirm; mismatched confirm; hostname-and-port only (URL with userinfo and query); create prints once; refuses no-refill, out-of-bounds and expiry; list has no secrets; rotate gives two live keys; revoke disables and keeps; Sentry events with no key material.
23. Refilled budget: exhaust → refused, row kept; past the interval → verifies.
24. Source-level: the eligibility re-read, ban check, session write and signing sit inside the `FOR UPDATE` transaction.

**Non-functional**
25. Log and Sentry capture across 5–23: no raw key, hash, session token, BSUID, phone-shaped value or driver error object; Sentry events carry no body or `Authorization`.
26. Outages: `auth.apikey` absent → 500, and boot refuses; database stopped under a running apps/auth (`NODE_ENV=production`) → 500, never 401.
27. Alerts fire for per-key volume, `USAGE_EXCEEDED`, `RATE_LIMITED`, 401 volume, eligibility violations and a missing service user.
28. End to end in apps/api's contract harness: exchange token → `requireUser` → `public.users` row.
29. Staging smoke (WhatsApp builder signs off): phone-visible, phone-hidden, concurrent messages from one user.

## Risks and Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| A WhatsApp key mints for a non-WhatsApp user (a `whatsapp` row on another user, a promotion, a future social provider) | Low | Critical | Fixed-provider lookup; owner binding; **eligibility under the lock** (sole `whatsapp` row, noreply domain, `registered_via`, not admin), else 500 + alert; `grant-admin.ts` refuses noreply; eligibility matrix tests |
| Ban or deletion races token issuance | Medium | High | Lock first, re-read under it; session and signing in the same transaction; consumer re-exchanges once on an apps/api 401 |
| The session write waits on its own `FOR UPDATE` (self-deadlock) | Medium | High | Session write in the locking transaction via better-auth's transaction context; real-Postgres probe before building; escalate to the architect if infeasible |
| A session token (30-day bearer credential) leaks | Low | Critical | Better-auth's generator; never returned, logged or sent to Sentry; in the log-capture assertions |
| An outage or unapplied migration answered as 401 | Medium | High | Relation-level check before 401; boot assertion; tests for an absent relation and a stopped database; residual window stated |
| An unknown-key flood amplifies database work | Medium | Medium | Cached relation check; 401-volume alert; residual stated |
| The plugin logs raw driver errors | High without mitigation | High | Redacted logger (not disabled), with near-miss tests |
| A burst deletes the production key | High if `remaining` were set without refill (probed) | High | The script refuses no-refill; refilled exhaustion pinned as refused-and-kept; no expiry; revoke disables and keeps |
| One looping user exhausts the key for everyone | Medium | High | Per-user budget inside the key's bound (429) |
| A leaked key confirms BSUID membership and bans, or creates users | Low | High | Accepted residual toward the delegation authority; per-key volume, exhaustion and rate-limit alerts; rotate and revoke |
| The plugin surface reachable through a variant or a new endpoint | Low | Critical | Derived list asserted at length 5; `/api/auth/api-key` prefix rule; `enableSessionForAPIKeys` pinned false |
| CORS lets a browser call the exchange | Low | Medium | No allow-origin on any exchange response, tested |
| A raw key mishandled at handoff | Medium | High | The runbook's direct-to-variable handoff; printed once; Sentry events carry no key material |
| Migration `0001` collides, ships unapplied, or locks `auth.account` at a bad moment | Medium | High | Main numbers it; boot assertion; pre-apply duplicate check and quiet-window guidance in the runbook |
| Rebase conflicts after the spec 300 cutover | High | Low | The PR waits; tests pin behaviour |
| A plugin or core bump changes relied-on behaviour | Medium | Medium | Each behaviour pinned by a test; plugin pinned to core |
| A phone number reaches apps/auth | Low | High | The pattern cannot match a bare number; refused before lookup or log |

## References

- Issue #329 (this spec); spec 321 / PR #327 (WhatsApp, first consumer); issue #314 (MCP, second consumer)
- `codev/specs/300-better-auth-migration.md`: two databases, reserved addresses, account endpoints, rate limits and client IP
- `codev/resources/arch.md`: Authentication & Authorization
- `codev/resources/lessons-learned.md`: Better Auth migration (spec 300); Auth hardening (spec 4)
- `@better-auth/api-key` 1.7.7 package source (`dist/index.mjs`: schema, endpoints, `validateApiKey`, `consumeRemaining`, `verifyApiKey`'s catch), and the specify-phase pglite probe whose results are tabled above
- better-auth 1.7.7 `db/internal-adapter.mjs` (`getCurrentAdapter`, `runWithTransaction`) and `plugins/jwt` (`signJWT`, server-only)
- Security architect's consolidated spec review, iteration 2 (inputs kept in `codev/projects/329-apps-auth-service-api-keys-bet/`)
