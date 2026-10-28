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

- `apps/auth/tests/secrets.test.ts` and `apps/auth/tests/cutover/copy.test.ts` (spec 300): `beforeAll` hooks time out at 30 s when the whole monorepo's tests run in one turbo invocation (pglite cold starts under load, the class `vitest.config.ts` records as #290/#292). They pass when apps/auth runs alone (261/261) and in porch's checks. The same failure reproduces on a clean worktree of this branch's pre-change HEAD. **Not skipped:** one is the secrets test, so skipping is raised with the architect instead.
