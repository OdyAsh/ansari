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

### Gate before phase 5: rerun on real Postgres, 2026-10-28

At the security architect's request, every gate-before-phase-5 probe was rerun
on real Postgres (a fresh `postgres:18` container on 127.0.0.1) before any phase 5
code, with the output pasted verbatim. Probes 2, 4a and the session-token probe
had only run on pglite; `packages/auth/scripts/probes/library-probes.ts` now runs them
against Postgres too. It prints claim names, headers and shapes, never a token.
The setup shared with probe 4 (URL guard, schema reset, env) moved to
`packages/auth/scripts/probes/probe-db.ts`. Both scripts are run as
`pnpm --filter ansari-auth exec tsx ../../packages/auth/scripts/probes/<script>.ts postgres://postgres:probe@127.0.0.1:55432/postgres`.

Probes 2, 4a and session token:

```
Probes 2, 4a and session token against Postgres 18.6 (Debian 18.6-1.pgdg13+2) at 127.0.0.1 (2026-10-28T08:12:18.312Z)
PASS  2 signJWT({sub, sid, iat, exp}) matches a /api/auth/token JWT; the passed exp is honoured
      /token  header {"alg":"EdDSA","kid":"5dcdae02-16d0-45b1-ae45-a4a5f2bc2ce4"}  claims [aud,exp,iat,iss,sid,sub]
      signJWT header {"alg":"EdDSA","kid":"5dcdae02-16d0-45b1-ae45-a4a5f2bc2ce4"}  claims [aud,exp,iat,iss,sid,sub]
      iss: /token https://auth.probe.example | signJWT https://auth.probe.example
      aud: /token ansari-api-probe | signJWT ansari-api-probe
      exp - iat: /token 900 s | signJWT 900 s
      sub = user id: true; sid = session id: true
      passed exp 1793176038 -> signed exp 1793176038; passed iat 1793175138 -> signed iat 1793175138
PASS  2 without iat the claim set differs (why the exchange passes it)
      signJWT({sub, sid}) claims [aud,exp,iss,sid,sub]
PASS  4a sign-up with phone_number is refused or ignored, and nothing is stored
      sign-up status 400, body {"message":"phone_number is not allowed to be set","code":"FIELD_NOT_ALLOWED"}
      users rows for the email: 0; phone_number stored: null
PASS  4a with phone_number set in the row, it is absent from sign-in and get-session
      sign-in 200: user keys [banExpires,banReason,banned,createdAt,email,emailVerified,first_name,id,image,last_name,name,registered_via,role,updatedAt]; contains the number: false; contains "phone": false
      get-session 200: user keys [banExpires,banReason,banned,createdAt,email,emailVerified,first_name,id,image,last_name,name,registered_via,role,updatedAt]; contains the number: false; contains "phone": false
PASS  session token: generateRandomString(32, a-z, A-Z, 0-9) has the shape createSession writes
      createSession row token: length 32, matches ^[A-Za-z0-9]{32}$: true
      generator, 1000 samples: all match: true; distinct characters seen: 62 of 62
      without the explicit alphabet, 200 samples contain '-' or '_': true

5/5 steps as expected.
```

Probe 4 (the refactored script, same steps as the phase 1 run):

```
Probe 4 against Postgres 18.6 (Debian 18.6-1.pgdg13+2) at 127.0.0.1 (2026-10-28T08:12:23.915Z)
PASS  A same-transaction session insert under FOR NO KEY UPDATE: committed without waiting; 1 session
PASS  B concurrent ban waits for the exchange, then deletes its session: ban waited: true; ban finished after exchange commit: true; sessions left: 0
PASS  C session insert from another connection does not wait on FOR NO KEY UPDATE: insert completed while the lock was held
PASS  C' contrast: FOR UPDATE blocks another connection's session insert (the hazard): other connection's insert: 57014 (57014 = cancelled by statement_timeout, i.e. it waited)
PASS  D lock_timeout turns a held lock into 55P03: waiting exchange got: 55P03
(node:5862) DeprecationWarning: Calling client.query() when the client is already executing a query is deprecated and will be removed in pg@9.0. Use async/await or an external async flow control mechanism instead.
(Use `node --trace-deprecation ...` to show where the warning was created)
PASS  E1 signing AFTER commit, pool max 2: 10/10 finished
PASS  E2 signing INSIDE the transaction, pool max 2, no connect timeout: deadline: the run HUNG (10000 ms), as expected for signing inside
PASS  E2 signing INSIDE the transaction, pool max 2, connect timeout 2000 ms: 1/10 finished; failures: Error (loud failure, no hang)

8/8 steps as expected.
```

The pglite pinning tests for the same probes, run in the same session:
`tests/config/api-key.test.ts` 16/16 passed.

**Gate before phase 5:** probe 2 (claim set, `alg`, `iss`, `aud` and the 900 s lifetime equal
`/api/auth/token`'s; the passed `exp` and `iat` are honoured; without `iat` the claim set lacks it),
probe 4a (refused 400 `FIELD_NOT_ALLOWED`, nothing stored, absent from sign-in and get-session),
the session-token probe, and every step of probe 4 (A, B, C, C', D, E1, E2 ×2) match the plan.

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

### Phase 5: probe 4 rerun against the real functions (real Postgres), 2026-10-28

Steps A to E unchanged; F1 to F4 drive `resolveOrCreateWhatsappUser` and
`issueWhatsappToken` themselves, on a pool of 2 with a 2 s connect timeout, against a fresh
`postgres:18` container. The first run showed F3 as `Error` after 2017 ms: the
probe's own reader took the top-level code, and drizzle wraps the driver error, so
the SQLSTATE is on `cause`. The reader was fixed (the route in phase 6 reads it the same
way, as `redactError` does) and the run repeated:

```
Probe 4 against Postgres 18.6 (Debian 18.6-1.pgdg13+2) at 127.0.0.1 (2026-10-28T08:29:35.379Z)
PASS  A same-transaction session insert under FOR NO KEY UPDATE: committed without waiting; 1 session
PASS  B concurrent ban waits for the exchange, then deletes its session: ban waited: true; ban finished after exchange commit: true; sessions left: 0
PASS  C session insert from another connection does not wait on FOR NO KEY UPDATE: insert completed while the lock was held
PASS  C' contrast: FOR UPDATE blocks another connection's session insert (the hazard): other connection's insert: 57014 (57014 = cancelled by statement_timeout, i.e. it waited)
PASS  D lock_timeout turns a held lock into 55P03: waiting exchange got: 55P03
(node:45541) DeprecationWarning: Calling client.query() when the client is already executing a query is deprecated and will be removed in pg@9.0. Use async/await or an external async flow control mechanism instead.
(Use `node --trace-deprecation ...` to show where the warning was created)
PASS  E1 signing AFTER commit, pool max 2: 10/10 finished
PASS  E2 signing INSIDE the transaction, pool max 2, no connect timeout: deadline: the run HUNG (10000 ms), as expected for signing inside
PASS  E2 signing INSIDE the transaction, pool max 2, connect timeout 2000 ms: 1/10 finished; failures: Error (loud failure, no hang)
PASS  F1 real functions: 10 concurrent exchanges for one new BSUID: distinct users: 1; distinct sids: 1; whatsapp rows: 1; sessions: 1
PASS  F2 real functions: another connection's open session insert does not block the exchange: exchange outcome while the insert was open: issued
PASS  F3 real functions: a held ban update fails the exchange with 55P03 within its lock_timeout: exchange got 55P03 after 2013 ms (lock_timeout 2000 ms); after the ban committed: banned
PASS  F4 real functions: 10 concurrent new users on a pool of 2 (sign after commit): 10/10 issued

12/12 steps as expected.
```

### Phase 6: production-shaped outage run (real Postgres), 2026-10-28

`apps/auth/scripts/probes/exchange-outage-probe.ts`: apps/auth's real app with `NODE_ENV=production` and the real node-postgres pool (`AUTH_POOL_MAX` 10, 5 s connect timeout) against a `postgres:18` container started without `--rm`, which the probe stops and starts under the running service. Command:

```
docker run -d --name probe-pg-329-outage -e POSTGRES_PASSWORD=probe -p 55433:5432 postgres:18
pnpm --filter ansari-auth exec tsx scripts/probes/exchange-outage-probe.ts postgres://postgres:probe@127.0.0.1:55433/postgres probe-pg-329-outage
docker rm -f probe-pg-329-outage
```

Output, verbatim:

```
Postgres 18.6 (Debian 18.6-1.pgdg13+2); container probe-pg-329-outage
PASS  1a boot assertion, schema present: assertSchema → true
PASS  1b new BSUID, valid key: 200 in 80 ms (expected 200)
whatsapp exchange failed: { name: 'Error', code: '55P03' }
PASS  2  user row held FOR UPDATE elsewhere: 500 in 2027 ms (expected 500 within lock_timeout 2000 ms + slack)
[Better Auth] ERROR: Failed to validate API key: { name: 'APIError' }
PASS  3a unknown key, database up (primes the relation check): 401 in 9 ms (expected 401)
      docker stop probe-pg-329-outage
auth database: idle client error { name: 'error', code: '57P01' }
auth database: idle client error { name: 'error', code: '57P01' }
[Better Auth] ERROR: Failed to validate API key: { name: 'Error', code: 'ECONNREFUSED' }
PASS  3b unknown key inside APIKEY_RELATION_CHECK_TTL (stated residual): 401 in 7 ms (expected 401 or 500)
[Better Auth] ERROR: Failed to validate API key: { name: 'Error', code: 'ECONNREFUSED' }
whatsapp exchange failed: { name: 'Error', code: 'ECONNREFUSED' }
PASS  3c valid key after the TTL: 500 in 5 ms (expected 500)
[Better Auth] ERROR: Failed to validate API key: { name: 'Error', code: 'ECONNREFUSED' }
whatsapp exchange failed: { name: 'Error', code: 'ECONNREFUSED' }
PASS  3d unknown key after the TTL: 500 in 5 ms (expected 500)
auth boot: schema check failed, starting anyway { name: 'Error', code: 'ECONNREFUSED' }
PASS  4  boot assertion, database down: assertSchema → true (expected true: start, /health reports)
health: auth database unreachable Error
PASS  4b /health with the database down: 503
      docker start probe-pg-329-outage
health: auth database unreachable Error
PASS  5a /health recovers without a restart: 200
PASS  5b valid key after recovery: 200 in 31 ms (expected 200)
[Better Auth] ERROR: Failed to validate API key: { name: 'APIError' }
PASS  5c unknown key after recovery: 401 in 5 ms (expected 401)
ALL PASS
```

Read: after `APIKEY_RELATION_CHECK_TTL` both keys get 500, never 401 (3c, 3d); inside it the unknown key gets the stated 401 residual (3b). A user row held `FOR UPDATE` from another connection is a 500 at 2027 ms (`55P03`, the 2 s `lock_timeout`), not a hang (2). Every log line is `{name, code}`. Boot with the database down starts (4) and `/health` recovers without a restart (5a). The plan's "a lock held from another connection → 500 within the timeout" is owned by this run, since pglite has one connection.

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

### Phase 7: one unidentified `@ansari/auth#test` failure inside porch's check (not reproduced)

After review iteration 1 (docs and apps/api tests only; no packages/auth file changed), porch's `tests` check failed once with `@ansari/auth#test ... exited (1)` after 78.9 s, and porch's truncated output did not name the test. The suite passed alone right after (246 passed, 1 skipped), and the full root command, rerun with its log kept, passed (`@ansari/auth` 246 + 1 skipped, `ansari-api` 1132 + 3 skipped, exit 0). Load averages at the rerun's start: 134 (1 min), 280 (5 min), 252 (15 min). That is consistent with the load timeouts below, but I am not naming a cause I did not see. Nothing was skipped or changed to get past it.

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

- **Review iteration 1 (Codex, REQUEST_CHANGES):** the first version buffered every line and printed them after the run, so the database host reached the screen only after the write. Each line is now printed as it is produced, and the host line is printed before the script connects. The per-subcommand host test asserts the call order (`print` before `connect`). Restoring the buffered printing turns 6 tests red. The Gemini lane was skipped (its CLI exited 3).
- **Review iteration 2 (Codex, REQUEST_CHANGES): the raw key could reach Sentry as a breadcrumb.** The script used `initSentry` with Sentry's default integrations and printed with `console.log`. The Console integration records every console line as a breadcrumb, and the breadcrumb trail rides on the next event. The line printed just before the operator event is the raw key. The original test turned default integrations off, and that hid the leak.
  - Fixed two ways, each enough on its own. The script now has its own Sentry options (`SCRIPT_SENTRY_OPTIONS`: `maxBreadcrumbs: 0`, `beforeBreadcrumb` returns null), and it prints through `process.stdout.write`, which Sentry does not instrument.
  - New test, `apps/auth/tests/service-keys-sentry.test.ts`. Default integrations stay ON; only the transport is a spy. The test runs create-key once with the script's printer and once printing through `console.log`.
  - Negative test: with the breadcrumb options removed, the `console.log` case leaks the key and goes red, and the stdout case still passes. So Codex's leak was real, and either guard alone stops it.
  - Lesson: a Sentry test with `defaultIntegrations: false` cannot show what production Sentry sends.
- Green per package: `@ansari/auth` 220 + 1 skipped (the `DEPLOYED_THROUGH` parity check, pending the rebase), `ansari-auth` 338 (after iteration 2), `ansari-api` 1113 + 3 skipped. Root typecheck and lint pass.

## Phase 5: WhatsApp identity and session core (notes)

- `packages/auth/src/whatsapp.ts`: `resolveOrCreateWhatsappUser` (fixed-provider lookup; user and `whatsapp` row in one transaction; a unique violation on `account_provider_id_account_id_unique`, read from the error or its cause, rolls back and returns the winner) and `issueWhatsappToken` (`decideUnderLock` in one transaction, then `signJWT` after the commit). Outcomes are typed: `issued`, `banned`, `ineligible` (with a reason: `accounts`, `address`, `registered_via`, `admin`), `user-gone`. Timeouts, outages and signing failures throw (the route answers 500).
- **Stricter than the spec, recorded:** eligibility requires the address to be exactly `whatsapp-<this user's id>@noreply.askansari.ai`, not just any address under the domain. Nothing legitimate writes another noreply address for a WhatsApp user.
- Lifetimes moved to `src/lifetimes.ts` (`JWT_LIFETIME_SECONDS`, `SESSION_LIFETIME_SECONDS`, `SESSION_UPDATE_AGE_SECONDS`), so `createAuth` and the exchange read one source; `JWT_EXPIRATION` is still `'15m'`, derived. `limits.ts` gains `WHATSAPP_EXCHANGE_PER_USER` (6 per 15 min, in `LONGEST_WINDOW`) and, in ms, `SESSION_MARGIN`, `EXCHANGE_LOCK_TIMEOUT`, `EXCHANGE_STATEMENT_TIMEOUT`.
- `tests/whatsapp.test.ts`, 26 tests on pglite. The session-token pin checks 20 written tokens, not one: a single token from the unrestricted generator lacks `-`/`_` about a third of the time.
- **Mutation-tested**, each red, then restored (26/26 green): dropping the BSUID re-check, dropping the ban's session delete, the unrestricted generator, no margin, no admin check, clearing the phone when absent, and signing inside the transaction. The last one hangs pglite outright (one connection: signJWT waits on the open transaction, the pool-starvation hazard in miniature). It was run again against the source-level block alone, which goes red both for a wrapper that signs inside `db.transaction` and for a `signJWT` inside `decideUnderLock`.
- Verified: @ansari/auth 246 + 1 skipped, ansari-auth 338, root typecheck and lint pass.

## Pre-existing: Sentry request data

Found in phase 6, 2026-10-28; reported to the security architect, who owns the fix as a separate develop PR (not widened here). apps/auth's `Sentry.init` on develop sets only `sendDefaultPii: false`, with default integrations. Sentry's HTTP/request-data integration still attaches the in-flight request to any event raised while a request is handled: its headers, `Authorization` included, and its body.

**Reproduction** (pglite through `startService`, a spy transport, `SERVICE_SENTRY_OPTIONS` as `initSentry` uses them, 40 failed sign-ins so `SIGN_IN_FAILED_ALERT` (30/min) fires once). The test, verbatim, to drop into `apps/auth/tests/` (its own file: the Console integration patches `console` process-wide). `SERVICE_SENTRY_OPTIONS` is new in phase 6; on develop, spread `{ sendDefaultPii: false }` instead:

```ts
// Pre-existing (spec 300): Sentry's request-data integration attaches the
// in-flight request's body and headers to an alert event raised during sign-in.
import * as Sentry from '@sentry/node';
import { afterAll, expect, it } from 'vitest';

import { SERVICE_SENTRY_OPTIONS, sendFailedSignInAlert } from '../src/alerts';
import { signIn, signUp, startService, type Service } from './helpers/service';

const envelopes: unknown[] = [];
let service: Service | undefined;

afterAll(async () => {
  await service?.close();
  await Sentry.close();
});

it('a failed-sign-in alert event carries no password and no Authorization header', async () => {
  Sentry.init({
    dsn: 'https://public@o0.ingest.sentry.io/0',
    environment: 'test',
    ...SERVICE_SENTRY_OPTIONS,
    transport: () => ({
      send: async (envelope) => {
        envelopes.push(envelope);
        return {};
      },
      flush: async () => true,
    }),
  });
  service = await startService({ failedSignInAlert: sendFailedSignInAlert });
  await signUp(service, 'probe@example.com');
  // SIGN_IN_FAILED_ALERT is 30 per minute: 40 failures cross it once.
  for (let i = 0; i < 40; i++) {
    await signIn(service, `probe${i}@example.com`, 'ProbePasswordMarker-123', {
      headers: { authorization: 'Bearer SESSIONTOKENMARKER' },
    });
  }
  await Sentry.flush(2000);
  const events = envelopes.map((e) => (e as [unknown, [unknown, { request?: Record<string, unknown> }][]])[1][0][1]);
  process.stdout.write(`EVENTS ${events.length}\n`);
  for (const event of events) process.stdout.write(`REQUEST ${JSON.stringify(event.request)}\n`);
  const text = JSON.stringify(envelopes);
  expect(text).not.toContain('ProbePasswordMarker');
  expect(text).not.toContain('SESSIONTOKENMARKER');
}, 60_000);
```

**Output** (run 2026-10-28 on this branch; `sentry-trace` and `baggage` values elided). One event, and the assertion fails:

```
EVENTS 1
REQUEST {"headers":{"host":"127.0.0.1:53108","connection":"keep-alive","origin":"http://localhost:3100","authorization":"Bearer SESSIONTOKENMARKER","content-type":"application/json","accept":"*/*","accept-language":"*","sec-fetch-mode":"cors","user-agent":"node","accept-encoding":"gzip, deflate","sentry-trace":"…","baggage":"…","content-length":"68"},"method":"POST","url":"http://127.0.0.1:53108/api/auth/sign-in/email","cookies":{},"data":"{\"email\":\"probe29@example.com\",\"password\":\"ProbePasswordMarker-123\"}"}
   × a failed-sign-in alert event carries no password and no Authorization header 5307ms
AssertionError: expected '[[{"event_id":"33cc86cf54ef4943a92f1a…' not to contain 'ProbePasswordMarker'
```

Fields found: `event.request.data`, the whole sign-in body (the **email and the password** of the request that crossed the threshold); `event.request.headers.authorization`, the bearer as sent. The list-walk alert is raised in the same request context, so the same applies to it.

**The exchange's scrub and its negative control** (phase 6, in this PR): `scrubExchangeEvent` (`beforeSend`) reduces an event from `/api/whatsapp/session` to its method and path. `apps/auth/tests/whatsapp-session-sentry.test.ts`, "carry no body, Authorization header, BSUID, phone number or key", passes with it. With `beforeSend` removed from `SERVICE_SENTRY_OPTIONS`, the same test fails: the envelope's `request.headers.authorization` held `Bearer wa_<the raw key>`. The exchange's body was not attached in that run, because the alert fires during the key check, before the body is read; the scrub drops `data` anyway.

## Phase 6: the exchange route, boot assertion and alerts (notes)

- **Route** `apps/auth/src/routes/whatsapp.ts`, mounted ahead of `corsFor`; it answers every request to its path (other methods, `OPTIONS` and sub-paths get a bare 404), so nothing reaches `corsFor`. Order: bearer (missing or malformed → 401 with no query), `verifyApiKey` with `whatsapp: mint`, the relation check on every plugin refusal (cached for `APIKEY_RELATION_CHECK_TTL`), the service-user owner binding, `Content-Type`, `express.json` with `EXCHANGE_BODY_LIMIT` and a route-local error handler, the sanity bounds, resolve, `consumeWindow` on the pool, `issueWhatsappToken`.
- **Boot** `apps/auth/src/boot.ts`, `assertSchema` before `listen()`: refuses only on `42P01` or an empty catalog result. The catalog lookup lives in packages/auth (`hasAuthUniqueConstraint`), because apps/auth has no `drizzle-orm` dependency.
- **Alerts** in `alerts.ts`: per-key window and daily volume read from the verified key row (`requestCount`; `refillAmount − remaining` since `lastRefillAt`). `USAGE_EXCEEDED` / `RATE_LIMITED`, 401 volume, ineligible user and unresolvable service user count in in-process windows of `EXCHANGE_ALERT_WINDOW` (5 min). Events carry numbers, the refusal code and an 8-character sha256 prefix of the key id; the ineligible user's id goes to one `console.warn` line. One constant beyond the plan's list, `EXCHANGE_ALERT_WINDOW`, names that window. The plugin's rate-limit window resets after a gap longer than the window, not on a fixed clock, so the window alert is deduplicated by time since it last fired (one key window).
- **Sentry**: `beforeSend: scrubExchangeEvent`, through the new `SERVICE_SENTRY_OPTIONS` that `initSentry` spreads. The scrub is exchange-only, as planned; the pre-existing leak on other paths is recorded above and owned by the security architect.
- **Tests**: `whatsapp-session.test.ts` (75), `whatsapp-session-sentry.test.ts` (1, with the negative control above), `boot.test.ts` (6), and a route inventory plus a `ROUTER_RULES` pin in `public-face.test.ts`. "User deleted between resolve and lock" over HTTP uses a pglite trigger on the user's `rate_limit` row, which is written after the resolve and before the lock. The lock timeout and the stopped database are the real-Postgres run above.
- **Mutation testing** (12 mutants on the route, app and alerts): 11 red. M2 (relation check before the bearer check) first survived, because a cached check from earlier tests hid the query; the zero-query test now moves the clock past the TTL first, and M2 is red. M7 (no explicit `Content-Type` check) is an equivalent mutant: body-parser 2.x skips a non-JSON body, and the bounds then answer the same 400. The check stays as the plan's explicit guard.
- **Review iteration 1** (Codex, REQUEST_CHANGES): the boot check matched the constraint by name only, and Postgres allows that name on another table. It now binds the table and the exact column order, with three spoofed-constraint tests (red against the old query) and a rerun of the production-shaped probe (13/13). Response in `codev/projects/329-*/329-phase_6-iter1-rebuttals.md`.
- **Verified**: `ansari-auth` 433 tests (430 before review iteration 1), `@ansari/auth` 246 + 1 skipped, root `pnpm typecheck` and `pnpm lint`, `npm run build`. `ansari-api`'s full run had 12 files fail on 30 s hook timeouts at load average about 65; all 12 pass in isolation (95 + 94 tests). Nothing in apps/api changed.

## Phase 7: end-to-end contract test, source scan and documentation (notes)

- **End to end** `apps/api/tests/contract/whatsapp-exchange.test.ts`: the real apps/auth over pglite, a service user and a `wa_` key created in process, an exchange with a BSUID and a phone number, then apps/api's real `GET /api/v2/threads` with the token. 200; the first-use `GET /api/account/me` created `public.users` under the token's `sub` with `whatsapp-<sub>@noreply.askansari.ai` and `registered_via = 'whatsapp'`; a second exchange for the same BSUID reaches the same single row. A dump of every app-database table and both responses contains neither the phone number nor the BSUID. Only the app `db` handle and Sentry are mocked.
- **First-use refusal is system-only** `apps/api/tests/auth/resolve-user.test.ts`: a profile planted as `whatsapp-<id>@noreply.askansari.ai` with `registered_via = 'whatsapp'` is accepted and its row created, with no alert. Mutation: `resolve-user.ts` tightened to also refuse `@noreply.askansari.ai` → this test red (1 failed, 16 passed); restored → 17 passed.
- **Source scan 5** (`apps/api/tests/source-scan/`): `whatsapp`, case-insensitive, comments included, over `apps/api/src/**` and `apps/api/lib/**` by path, tests excluded. Hit count: **0 hits in 65 files**. Negative tests: `if (registeredVia === 'whatsapp') return;` in a fixture path under each scanned directory is caught; the same line at `apps/api/tests/…`, `apps/api/db/…`, `apps/api/library/…`, `apps/auth/src/…` and `apps/whatsapp/lib/…` is not. A real planted file (`apps/api/lib/zz-planted.ts`, one comment line) turned the tree test red (1 hit in 66 files); removed → green.
- **Docs**: `docs/cutover/service-keys-runbook.md` (new), `docs/self-hosting.md` (new section; its Boot checks paragraph corrected: the boot now also checks the schema), `codev/resources/arch.md` (a service-keys paragraph; the first-use refusal stated as system-domain only, with why), `packages/auth/README.md` (`auth.apikey`, `phone_number`, the new constraint, `0001`). Fixed everywhere, as found: `grant-admin.ts` was described as refusing only the system domain in `arch.md` and `RELEASE.md`; it refuses both reserved domains since phase 3.
- **Doc tests**: `cutover-docs.test.ts` gains 10 runbook tests, each reading the code it describes: the script's six subcommands (from `USAGE`), the issuance flags (from `WHATSAPP_KEY_INITIAL`), the six alert kinds (from the `ExchangeAlert` union), the ineligible-user log line verbatim (from `INELIGIBLE_USER_MESSAGE`), the secret-scanning table equal to `SECRET_SCANNING_PATTERNS` `.source`, the key log the script's banner names, and no bare `auth-journal-applied:` line. Negative run: a 63-letter pattern and a deleted `unauthorized-volume` row → 2 red; restored → green. `self-hosting-docs.test.ts` gains 3 (route, script, runbook, `limits.ts` values, migration before code).
- **Key log location** (the spec named it, the plan did not place it): a table at the end of the runbook, appended by PR. Ids are not secrets; the section forbids keys, hashes, BSUIDs and phone numbers.
- **No new environment variable**, four ways: no Zod schema field, no static `process.env.X`, no dynamic `process.env[` and no documented variable were added in this phase (the diff touches tests and docs only). `turbo.json` is unchanged: apps/api's test task already hashes `packages/auth/src/**` (global), `apps/auth/src/**`, `apps/auth/scripts/**` and `docs/**`, which is everything the new tests read.
- **Contract summary** sent to the security architect for the #327 builder (2026-10-28): header, body and bounds, every status and what the consumer does with it, no per-message retry on 500, re-exchange once on an apps/api 401, the spec 321 amendment on #327's branch, and the staging smoke recorded as a comment on PR #327.
- **Green command** (2026-10-28, root `npm test -- --exclude='**/e2e/**'`, exit 0): `ansari-api` 83 files, 1131 passed + 3 skipped (cache miss, executed); `@ansari/auth` 17 files, 246 passed + 1 skipped (`AUTH_DEPLOYED_THROUGH` still `null`, by design; cache miss); `ansari-auth` 16 files, 433 passed (cache hit: no apps/auth or packages/auth source changed this phase); `ansari-frontend` 60 files, 774 passed (cache hit). This time apps/api's full suite ran with no 30 s timeouts. Also: `npm run build` green; apps/api `typecheck` clean and `lint` 0 errors (5 pre-existing warnings).
- **Review iteration 1** (Codex, REQUEST_CHANGES): the docs named `WHATSAPP_KEY_BOUNDS` without its ranges. Both docs now state them, and the tests compute the expected text from the constant (a negative run with a wrong bound in each doc → 2 red). Response in `codev/projects/329-*/329-phase_7-iter1-rebuttals.md`.
