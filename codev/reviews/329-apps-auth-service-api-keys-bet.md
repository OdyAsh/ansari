# Review: Service API keys and the WhatsApp token exchange (spec 329)

> In progress. Probe outputs are recorded here as each phase runs them; the full
> review is written in the review phase.

## Probe outputs

### Phase 1: probe 4 (real Postgres), 2026-10-28

Run against a throwaway `postgres:18` container on 127.0.0.1 with
`packages/auth/scripts/probes/exchange-lock-probe.ts` (moved from the plan's
`apps/auth/scripts/probes/` because apps/auth has no direct `pg` / `drizzle-orm`
dependency). The `pg` deprecation warning comes from clients left checked out by
the deliberately hung E2 run.

```
Probe 4 against Postgres 18.6 (Debian 18.6-1.pgdg13+2) at 127.0.0.1 (2026-10-28T06:04:19.563Z)
PASS  A same-transaction session insert under FOR NO KEY UPDATE: committed without waiting; 1 session
PASS  B concurrent ban waits for the exchange, then deletes its session: ban waited: true; ban finished after exchange commit: true; sessions left: 0
PASS  C session insert from another connection does not wait on FOR NO KEY UPDATE: insert completed while the lock was held
PASS  C' contrast: FOR UPDATE blocks another connection's session insert (the hazard): other connection's insert: 57014 (57014 = cancelled by statement_timeout, i.e. it waited)
PASS  D lock_timeout turns a held lock into 55P03: waiting exchange got: 55P03
(node:76887) DeprecationWarning: Calling client.query() when the client is already executing a query is deprecated and will be removed in pg@9.0. Use async/await or an external async flow control mechanism instead.
(Use `node --trace-deprecation ...` to show where the warning was created)
PASS  E1 signing AFTER commit, pool max 2: 10/10 finished
PASS  E2 signing INSIDE the transaction, pool max 2, no connect timeout: deadline: the run HUNG (10000 ms), as expected for signing inside
PASS  E2 signing INSIDE the transaction, pool max 2, connect timeout 2000 ms: 1/10 finished; failures: Error (loud failure, no hang)
8/8 steps as expected.
```

### Phase 1: pinning probes (pglite, `packages/auth/tests/config/api-key.test.ts`)

All green (16 tests):
- Probe 1: a script-created key verifies; the raw key appears in no column; `start` is null; `key` equals `defaultKeyHasher(raw)`; permissions and metadata round-trip; no expiry.
- Probe 1b: `auth.api.updateApiKey` server-side changes `rateLimitMax`, the window and the refill, enforced at the next verify.
- Probe 2: `auth.api.signJWT` with explicit `{sub, sid, iat, exp}` has the same claim keys, `alg`, `iss`, `aud` and lifetime as a `/api/auth/token` JWT, and honours the passed `exp`. Without `iat`, the claim set lacks `iat`.
- Probe 4a: `phone_number` is absent from the sign-in and get-session responses. Sign-up with `phone_number` is **refused** with 400 `{"message":"phone_number is not allowed to be set","code":"FIELD_NOT_ALLOWED"}` and nothing is stored (the spec allowed refused or ignored).
- Session-token probe: `internalAdapter.createSession` writes a 32-character `[A-Za-z0-9]` token; `generateRandomString(32, 'a-z', 'A-Z', '0-9')` from `better-auth/crypto` produces the same shape (without the explicit alphabet it also emits `-` and `_`).
- Plugin behaviours: no-refill exhaustion deletes the key; refilled exhaustion refuses and keeps it, and verifies after the interval (both cycles); the window limit refuses and keeps; disabled is refused and kept; expired is refused and deleted; a wrong permission spends no budget; a key is never a session at `/get-session` or `/token`.

**Gates:** before phase 4 (probes 1, 1b) and before phase 5 (probes 2, 4a, session token, probe 4): all match the plan.

### Phase 1: blocked-path prefix proven load-bearing

With `API_KEY_PREFIX` removed from the middleware, the variant and absolute-form tests fail (2 failed); restored, all pass.

### Phase 1: green command, and a pre-existing timeout under combined load

The plan's green command, run in sequence (`pnpm turbo run test --force`, then `typecheck --force`, then `lint --force`), passes. Running all three as **one** parallel turbo invocation (`turbo run test typecheck lint --force`, 15 tasks at once) times out pglite `beforeAll` hooks at 30 s. It happened twice on this branch, both times in `@ansari/auth`. It also happens on a clean baseline worktree of this branch's HEAD (no code changes), there in `ansari-auth` (`secrets.test.ts`, `cutover/copy.test.ts`). It is the load-induced class `vitest.config.ts` already records (#290/#292), not something this change introduced. This phase's extra pglite file adds load, so CI's task grouping is worth watching.

### Phase 2: redacted logger

- The logger probe matches the plan (gate before phase 6). With `auth.apikey` dropped, `verifyApiKey` returns `valid: false` with `INVALID_API_KEY`, the same code as an unknown key, and the logged arguments contain no Error object, include the code `42P01`, and never carry the driver message ("does not exist") or the key.
- Proven both ways: with `logger: { disabled: true }` the non-error survival test and the outage test fail; with better-auth's default (unredacted) logger they fail too; with the redacted logger they pass.
- Real pg server errors carry `name: 'error'` (node-postgres sets it from the protocol message type), so a raw pg error is logged as `{ name: 'error', code }`.
- Residual: messages are not rewritten. The better-auth paths this service runs pass errors as arguments; the few that interpolate an error into the message string (OAuth discovery, token introspection) are not configured.
- No existing test asserted better-auth log output, so none needed editing.

## Flaky Tests

### `apps/auth/tests/secrets.test.ts` and `apps/auth/tests/cutover/copy.test.ts` (fixed with a targeted deadline)

Both are spec 300 tests (the secrets guard and the cutover copy). Their setup hooks (`beforeAll` in `secrets.test.ts`, which starts two services; `beforeEach` in `copy.test.ts`, which builds two pglite databases) crossed the 30 s `hookTimeout` when the whole monorepo's tests ran at once. They pass when apps/auth runs alone (261/261). **Not skipped** (security architect): each hook now has its own 100 s deadline, in those two files only (`chore(test)` commit in phase 2).

**Conditions: a real shared-machine run, no artificial load harness.** The machine runs other builders' sessions and iOS simulators. Load averages (1/5/15 min) sampled during the measurement: 20.75 / 50.01 / 71.33, 54.04 / 46.78 / 55.22, 22.15 / 41.07 / 51.18. Each run is a full root run with `--force --continue`, alternating `turbo run test` and `turbo run test typecheck lint`.

**Timings (N = 6, hooks given a temporary 600 s deadline and timed):** slowest per run, in ms.

| Run | Tasks | `secrets` beforeAll | `copy` beforeEach (max) | Both target files |
|---|---|---|---|---|
| 1 | test | 35,465 | 39,005 | pass |
| 2 | test, typecheck, lint | 34,839 | 36,490 | pass |
| 3 | test | 31,703 | 30,088 | pass |
| 4 | test, typecheck, lint | 42,296 | 47,098 | pass |
| 5 | test | 27,097 | 30,239 | pass |
| 6 | test, typecheck, lint | 40,547 | 43,910 | pass |

Maximum 47 s; the deadline is 100 s (about 2x, above the architect's 60 s floor).

**Proof: 30 s versus 100 s, same conditions, counted on the two target files only** (the rest of the suite is red from machine load regardless, see below):

```
before run 1 [test] deadline=30_000 secrets=RED copy=RED slowest=[copy=9296 copy=32440 ]
before run 2 [test typecheck lint] deadline=30_000 secrets=RED copy=RED slowest=[copy=7926 copy=41851 ]
before run 3 [test] deadline=30_000 secrets=RED copy=green slowest=[copy=29012 secrets=31456 ]
before run 4 [test typecheck lint] deadline=30_000 secrets=RED copy=RED slowest=[copy=10031 copy=41717 ]
before run 5 [test] deadline=30_000 secrets=green copy=green slowest=[copy=25698 secrets=26954 ]
after run 1 [test] deadline=100_000 secrets=green copy=green slowest=[copy=36150 secrets=39634 ]
after run 2 [test typecheck lint] deadline=100_000 secrets=green copy=green slowest=[secrets=47493 copy=53506 ]
after run 3 [test] deadline=100_000 secrets=green copy=green slowest=[secrets=12502 copy=15275 ]
after run 4 [test typecheck lint] deadline=100_000 secrets=green copy=green slowest=[secrets=20134 copy=21117 ]
after run 5 [test] deadline=100_000 secrets=green copy=green slowest=[copy=14771 secrets=14982 ]
```

At 30 s: `secrets` red 4/5, `copy` red 3/5. At 100 s: both green 5/5 (slowest hook 53.5 s).

### Other files that tripped 30 s timeouts in the same runs (evidence for main; not fixed here)

On the same shared machine, with no harness, **other files hit the 30 s hook or test deadline in 5 of the 6 timing runs** (runs 1, 2, 4, 5 and 6; only run 3 was clean). Extracted from the vitest failure summaries of each run's output (`<package> <file> :: <error>`):

```
== timing run 1
@ansari/auth tests/auth.http.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/auth.integration.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/config/api-key.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/config/credentials.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/config/options.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/config/rate-limit-store.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/config/rate-limit.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/config/sign-up.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/config/tokens.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/migration/auth-journal-parity.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/schema/auth-schema.test.ts :: Hook timed out in 30000ms
ansari-api tests/contract/legacy-users-contract.test.ts :: Hook timed out in 30000ms
ansari-api tests/feedback-upsert.test.ts :: Hook timed out in 30000ms
ansari-api tests/toolcalls-persistence.test.ts :: Hook timed out in 30000ms
ansari-auth tests/account.test.ts :: Hook timed out in 30000ms
ansari-auth tests/blocked-paths.test.ts :: Hook timed out in 30000ms
ansari-auth tests/client-ip.test.ts :: Hook timed out in 30000ms
ansari-auth tests/frontend-client.test.ts :: Hook timed out in 30000ms
ansari-auth tests/grant-admin.test.ts :: Hook timed out in 30000ms
ansari-auth tests/limits.test.ts :: Hook timed out in 30000ms
ansari-auth tests/public-face.test.ts :: Hook timed out in 30000ms
ansari-auth tests/reset.test.ts :: Hook timed out in 30000ms
== timing run 2
@ansari/auth tests/schema/auth-schema.test.ts :: Hook timed out in 30000ms
ansari-api tests/feedback-upsert.test.ts :: Hook timed out in 30000ms
ansari-api tests/migration/migration-schema-parity.test.ts :: Test timed out in 30000ms
ansari-auth tests/frontend-client.test.ts :: Hook timed out in 30000ms
ansari-auth tests/public-face.test.ts :: Hook timed out in 30000ms
== timing run 3
== timing run 4
@ansari/auth tests/auth.http.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/auth.integration.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/config/api-key.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/config/credentials.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/config/options.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/config/rate-limit-store.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/config/rate-limit.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/config/sign-up.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/config/tokens.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/migration/auth-journal-parity.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/schema/auth-schema.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/unit/logger.test.ts :: Hook timed out in 30000ms
ansari-api tests/auth/layer.test.ts :: Hook timed out in 30000ms
ansari-api tests/auth/require-user.test.ts :: Hook timed out in 30000ms
ansari-api tests/auth/resolve-user.test.ts :: Hook timed out in 30000ms
ansari-api tests/contract/legacy-users-contract.test.ts :: Hook timed out in 30000ms
ansari-api tests/feedback-upsert.test.ts :: Hook timed out in 30000ms
ansari-api tests/migration/migration-schema-parity.test.ts :: Test timed out in 30000ms
ansari-api tests/migration/window-migration.test.ts :: Hook timed out in 30000ms
ansari-api tests/thread-get-contract.test.ts :: Hook timed out in 30000ms
ansari-api tests/toolcalls-persistence.test.ts :: Hook timed out in 30000ms
ansari-auth tests/account.test.ts :: Hook timed out in 30000ms
ansari-auth tests/blocked-paths.test.ts :: Hook timed out in 30000ms
ansari-auth tests/client-ip.test.ts :: Hook timed out in 30000ms
ansari-auth tests/frontend-client.test.ts :: Hook timed out in 30000ms
ansari-auth tests/grant-admin.test.ts :: Hook timed out in 30000ms
ansari-auth tests/limits.test.ts :: Hook timed out in 30000ms
ansari-auth tests/public-face.test.ts :: Hook timed out in 30000ms
ansari-auth tests/reset.test.ts :: Hook timed out in 30000ms
== timing run 5
@ansari/auth tests/config/rate-limit.test.ts :: Test timed out in 30000ms
ansari-api tests/migration/window-migration.test.ts :: Test timed out in 30000ms
== timing run 6
@ansari/auth tests/auth.http.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/auth.integration.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/config/api-key.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/config/credentials.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/config/options.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/config/rate-limit-store.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/config/rate-limit.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/config/sign-up.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/config/tokens.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/migration/auth-journal-parity.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/schema/auth-schema.test.ts :: Hook timed out in 30000ms
@ansari/auth tests/unit/logger.test.ts :: Hook timed out in 30000ms
ansari-api tests/auth/layer.test.ts :: Hook timed out in 30000ms
ansari-api tests/auth/require-user.test.ts :: Hook timed out in 30000ms
ansari-api tests/auth/resolve-user.test.ts :: Hook timed out in 30000ms
ansari-api tests/contract/legacy-users-contract.test.ts :: Hook timed out in 30000ms
ansari-api tests/feedback-upsert.test.ts :: Hook timed out in 30000ms
ansari-api tests/migration/migration-schema-parity.test.ts :: Test timed out in 30000ms
ansari-api tests/toolcalls-persistence.test.ts :: Hook timed out in 30000ms
ansari-auth tests/account.test.ts :: Hook timed out in 30000ms
ansari-auth tests/blocked-paths.test.ts :: Hook timed out in 30000ms
ansari-auth tests/client-ip.test.ts :: Hook timed out in 30000ms
ansari-auth tests/frontend-client.test.ts :: Hook timed out in 30000ms
ansari-auth tests/grant-admin.test.ts :: Hook timed out in 30000ms
ansari-auth tests/limits.test.ts :: Hook timed out in 30000ms
ansari-auth tests/public-face.test.ts :: Hook timed out in 30000ms
ansari-auth tests/reset.test.ts :: Hook timed out in 30000ms
```

### Under #301's artificial load (28 busy loops on 14 cores), 3 runs, for comparison

Without `--continue`, turbo stops at the first failing package, so each run shows only the first package to fail: run 1 the frontend (`lib/worklets.test.ts`, `Test timed out in 5000ms`); run 2 apps/api (`tests/migration/window-migration.test.ts`, `Test timed out in 30000ms`); run 3 twelve `packages/auth` files. Hook times there: `secrets` 37 s, `copy` 36 s and 55 s.

```
timing run 1 exit=1 fails=[] times=[]
timing run 2 exit=1 fails=[] times=[copy=5644 copy=5992 copy=6557 copy=6943 copy=7109 copy=7159 copy=7191 copy=7625 copy=7780 copy=7803 copy=7888 copy=8016 copy=8298 copy=8468 copy=8672 copy=9121 copy=10567 copy=11835 copy=11842 copy=14900 copy=15385 copy=36043 secrets=37399 ]
timing run 3 exit=1 fails=[@ansari/auth:test:  FAIL  tests/auth.http.test.ts [ tests/auth.http.test.ts ];@ansari/auth:test:  FAIL  tests/auth.integration.test.ts [ tests/auth.integration.test.ts ];@ansari/auth:test:  FAIL  tests/config/api-key.test.ts [ tests/config/api-key.test.ts ];@ansari/auth:test:  FAIL  tests/config/credentials.test.ts [ tests/config/credentials.test.ts ];@ansari/auth:test:  FAIL  tests/config/options.test.ts [ tests/config/options.test.ts ];@ansari/auth:test:  FAIL  tests/config/rate-limit-store.test.ts [ tests/config/rate-limit-store.test.ts ];@ansari/auth:test:  FAIL  tests/config/rate-limit.test.ts [ tests/config/rate-limit.test.ts ];@ansari/auth:test:  FAIL  tests/config/sign-up.test.ts [ tests/config/sign-up.test.ts ];@ansari/auth:test:  FAIL  tests/config/tokens.test.ts [ tests/config/tokens.test.ts ];@ansari/auth:test:  FAIL  tests/migration/auth-journal-parity.test.ts > Drizzle schema vs the auth journal;@ansari/auth:test:  FAIL  tests/schema/auth-schema.test.ts [ tests/schema/auth-schema.test.ts ];@ansari/auth:test:  FAIL  tests/unit/logger.test.ts > createAuth uses the redacted logger > a non-error log survives with its level and message (a disabled logger fails this);] times=[copy=55167 ]
```

This is a monorepo-wide matter (pglite cold starts against 30 s deadlines under contention), passed to the main architect as a follow-up; this PR fixes only the two files that blocked its own green runs.

## Phase 3: reserved noreply domain (notes)

- **Spec wording versus the guard order:** the spec's "sign-up with a noreply address (any case, surrounding spaces) returns the 409 body". Validation runs first in `signUpGuard`, so an address with surrounding spaces is an invalid email (400) for every address, reserved or not. The test asserts what holds: a noreply address and a taken address answer identically, padded or not (409 unpadded, 400 padded). There is still no oracle.
- Negative-tested: with the noreply domain removed from `isReservedAddress`, 8 tests go red.
- Green per package (`@ansari/auth` 220, `ansari-auth` 263, `ansari-api` 1113 + 3 skipped); typecheck and lint pass at the root. The root test run on the shared machine (load average 92/74/60 at the time) failed only with timeouts (27 hook, 6 test), across all four packages including the untouched frontend: the monorepo-wide matter recorded under Flaky Tests.

## Phase 4: service users and key operator scripts (notes)

- **Design decisions within the plan:**
  - `create-service` has no user yet, so its `--confirm` takes the service name (`--confirm whatsapp`). Every other write confirms with the service user's id.
  - The window flag is in seconds (as every rule in `limits.ts` is) and stored in milliseconds (as the plugin stores it). The budget is `--daily` and always refills every 24 h. A bare budget cannot be expressed: `--remaining` is refused at the command line, and `checkKeyLimits` refuses a budget without `KEY_REFILL_INTERVAL` before the plugin is called.
  - `update-limits` also sets `remaining` to the new daily budget: the new budget starts now.
  - `rotate` refuses when the service has no live key (use `create-key`). It prints the old key ids and the exact revoke commands.
  - `create-service` also sends a Sentry event. The spec names create, rotate and revoke, and the plan adds update-limits. A service user is a durable change too, so every write is recorded.
  - Exit codes: 0 done, 1 refused or failed, 2 nothing written (show step, confirm mismatch), 3 written but its Sentry record not sent (banner). Without `SENTRY_DSN`, `Sentry.flush` has no client and returns false, so every write exits 3 with the banner. That is loud on purpose.
  - The script prints `Auth database: <hostname>:<port>` first on every subcommand, list included. `urlHost` moved to `apps/auth/scripts/url-host.ts` beside the new `urlHostPort`.
  - `ansari-mcp` borrows `WHATSAPP_KEY_BOUNDS` until #314 sizes its own.
- **The plan's leak test, adjusted:** the plan's URL `postgres://user:secret@db.example:6543/auth?sslmode=require` asks that the output contain none of `user`, `secret` and `sslmode`. The script's own words say "user" ("Service user for whatsapp"), so the test asserts `user:secret`, `secret`, `sslmode`, `/auth?` and `postgres://` absent. A second URL with distinctive userinfo, path and query (`opsname-zq`, `pw-zq-9`, `authdb-zq`, `verify-full`) shows that none of them is echoed. "secret" did catch one real wording problem: the handoff line said "secret store", and now says "credential store".
- **apps/api's source scan (scan 2) caught a literal database URL** in the first draft of the demo harness. The harness now uses the placeholder URL `setAuthEnv` puts in `DATABASE_URL`, and derives its leak checks from the parsed URL. The scan's exclusion list is unchanged.
- **Negative-tested** (`apps/auth/tests/service-keys.test.ts`, 73 tests). Each mutation was applied to the script alone and then reverted:

  | Mutation | Tests red |
  |---|---|
  | raw key printed twice | 3 |
  | database URL appended to the host line | 6 |
  | Sentry flush not awaited | 3 |
  | revoke leaves `enabled = true` | 1 |
  | refill interval not checked | 1 |

- **Runnable harness** (`pnpm --filter ansari-auth exec tsx scripts/probes/service-keys-demo.ts`, pglite from the journal). It runs every subcommand in the runbook's order, including a confirm mismatch, an out-of-bounds update and an expiry flag. Run 2026-10-28, summary of its 13 steps:

  ```
     7 (exit 0)
     2 (exit 1)
     4 (exit 2)
    13 Auth database: localhost:5432
     1 CHECK database printed as hostname:port only: PASS
     1 CHECK two keys created, each printed once: PASS
  ```

- Green per package: `@ansari/auth` 220 + 1 skipped (the `DEPLOYED_THROUGH` parity check, pending the rebase), `ansari-auth` 336, `ansari-api` 1113 + 3 skipped. Root typecheck and lint pass.
