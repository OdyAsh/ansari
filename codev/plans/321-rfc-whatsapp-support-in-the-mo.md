# Plan: WhatsApp support in the monorepo

**Specification**: [codev/specs/321-rfc-whatsapp-support-in-the-mo.md](../specs/321-rfc-whatsapp-support-in-the-mo.md)

## Executive Summary

Follow the spec's recommended Approach 1: import the existing Python WhatsApp service into the monorepo as a separate app (`apps/whatsapp`), then replace its legacy user handling with the `apps/auth` token exchange, then add the one-thread-per-user lifecycle, then finish environment behaviour (maintenance mode, `!d ` filter) and deployment docs. The Baked Decisions in the spec are fixed and not re-opened here. The `/whatsapp` contract is taken as given (`POST /api/whatsapp/session`, `AUTH_WHATSAPP_API_KEY`, `{ bsuid }` in, `{ token }` out).

Testing strategy, used by every phase:
1. **Automated:** pytest with mock Meta, mock `apps/auth` and mock `apps/api` clients (the legacy base/real/mock service pattern). No network, no Meta.
2. **Local end to end:** the service runs locally, exposed to Meta through a zrok tunnel, with test messages prefixed `!d `. The `zrok.exe` binary is used for this (see "Local testing with zrok" below).
3. **Staging:** a final smoke test only, after the phases are merged and deployed.

## Phases (Machine Readable)

```json
{
  "phases": [
    {"id": "import_service", "title": "Import the Python service as is"},
    {"id": "auth_token_exchange", "title": "Replace legacy user handling with the apps/auth token exchange"},
    {"id": "thread_lifecycle", "title": "One thread per user with expiry"},
    {"id": "environments_and_docs", "title": "Maintenance mode, staging filter, local testing and docs"}
  ]
}
```

## Phase Breakdown

### Phase 1: Import the Python service as is

**Dependencies**: None

#### Objective

Bring `ansari-whatsapp` into the monorepo as `apps/whatsapp` with minimal changes, so it builds, lints and passes its existing tests there. Behaviour is unchanged at this point.

#### Files to Create / Modify

- `apps/whatsapp/` (source, `pyproject.toml`, `uv.lock`, `pytest.ini`, `Dockerfile`, tests, `.env.example`), copied from the legacy repo without its logs, `tmp/` files, personal scripts, the `zrok.exe` binary and generated artifacts.
- Monorepo wiring only where needed (workspace and CI entries), following how other apps are registered.

#### Deliverables

- [ ] `apps/whatsapp` present and runnable with `uv` and `ruff`.
- [ ] Legacy tests pass with `MOCK_META_API=True` and `MOCK_ANSARI_CLIENT=True`.
- [ ] `.env.example` contains no secrets.
- [ ] Tests for this phase (the imported suite).

#### Acceptance Criteria

- [ ] `uv sync`, `ruff check`, `ruff format --check` and `pytest tests/ -v` pass in `apps/whatsapp`.
- [ ] Existing monorepo build and tests are unaffected.

#### Test Plan

Run the imported test suite in mock mode. Manually start the service and call the health check (`GET /`) and the webhook verification handshake (`GET /whatsapp/v2`).

### Phase 2: Replace legacy user handling with the apps/auth token exchange

**Dependencies**: Phase 1

#### Objective

Identify users by BSUID only. The service gets a JWT from `apps/auth` and calls `apps/api` with it. No phone number or BSUID is sent to `apps/api` or stored by this app.

#### Files to Create / Modify

- In `apps/whatsapp`: a new auth client (base, real and mock, like the other services) behind the service provider; the webhook parser and conversation manager updated to key on BSUID and drop phone-number registration and the phone-hidden notice; the Ansari client updated to call `apps/api` with the JWT instead of legacy registration endpoints.
- New settings: auth base URL and `AUTH_WHATSAPP_API_KEY`, validated in config and listed in `.env.example`.
- Confirm in `apps/api` which existing endpoints the service needs (chat streaming, thread create or read, history) and use them as they are. If a needed endpoint is missing, record it as a finding instead of widening this phase.

#### Deliverables

- [ ] Auth client with mock implementation.
- [ ] Phone-visible and phone-hidden payloads both handled; the BSUID (`user_id` in the payload) is the only identity used.
- [ ] Failure of the token exchange sends the user a failure notice, is logged, and Meta still gets "OK".
- [ ] Logs and errors contain no phone numbers.
- [ ] Tests for this phase.

#### Acceptance Criteria

- [ ] Same BSUID yields a token request each time with the same input; a different BSUID is a different user (verified through the mock).
- [ ] No outgoing call to `apps/auth` or `apps/api` carries a phone number (asserted in tests).
- [ ] Streaming answers are still aggregated and split for WhatsApp as before.

#### Test Plan

Unit tests for the auth client (success, 400, 401, 500, timeout) and for webhook parsing of both user types. Service-level tests with all three mocks for the full message flow. Local end-to-end run with the zrok tunnel and `!d ` once `apps/auth` offers the endpoint; until then, only the mock-based tests apply.

### Phase 3: One thread per user with expiry

**Dependencies**: Phase 2

#### Objective

Each user has exactly one thread. If the last activity is older than the global expiry (3 hours), the old thread and its messages are deleted and a new thread starts.

#### Files to Create / Modify

- In `apps/whatsapp`: the conversation manager's thread logic and the Ansari client's thread calls; expiry setting in config (global, default 3 hours).
- `apps/api` only if the existing endpoints cannot read the thread's last activity or delete a thread with its messages; any such change is kept minimal and noted in the review.

#### Deliverables

- [ ] Expired-thread replacement; non-expired threads reused.
- [ ] Tests for this phase.

#### Acceptance Criteria

- [ ] Message within the expiry reuses the thread and keeps context.
- [ ] Message after the expiry deletes the old thread and messages and creates a new thread.
- [ ] A user never ends up with two threads, including when two messages arrive close together (verified in tests, with the behaviour documented).

#### Test Plan

Unit tests with a controllable clock for the boundary (just inside, just outside the expiry) and the first-ever message. Local end-to-end check with a short expiry value set through config.

### Phase 4: Maintenance mode, staging filter, local testing and docs

**Dependencies**: Phase 3

#### Objective

Finish environment behaviour and make the service testable and deployable by others.

#### Files to Create / Modify

- In `apps/whatsapp`: maintenance behaviour driven by the same global flag as `apps/api` (`MAINTENANCE_MODE`) instead of the WhatsApp-specific flag, keeping the notice message; keep the staging `!d ` filter; `README.md` and `tests/README.md` for the app (both still reference legacy files that were not imported).
- Deployment notes: per-environment Meta credentials and secrets, and the deploy steps for staging and production, following the monorepo's existing deployment docs.
- Findings from the Phase 2 review to resolve here: (a) apps/api reports a failed or empty answer as an inline `Error: ...` at the end of a 200 stream, which the client currently relays to the user unlogged, so detect it, log it and send a failure notice; (b) deploy blocker to confirm before the staging smoke test: the service now requires `from_user_id` in every webhook, and sending to a BSUID (`recipient`) is untested against Meta's live API (Meta's docs disagree on when it is supported), so check the webhook Graph API version and test a real send; (c) `README.md` still documents the six deleted legacy endpoints.
- Deploy-path items found in the Phase 1 review: bump the uv version in the `Dockerfile` (it pins 0.5.11 against a newer lockfile format) and make its second `uv sync` use `--frozen`; decide whether to keep `/sentry-debug` and `clean_logs.sh`; ask an admin to make the `whatsapp` CI job a required check on `develop`.

#### Deliverables

- [ ] Maintenance flag reuse with tests.
- [ ] `!d ` filter kept, with tests (ignored in staging, processed otherwise).
- [ ] README covering setup, tests, and local testing with zrok.
- [ ] Staging smoke-test checklist.

#### Acceptance Criteria

- [ ] With the flag on, users get the notice and no chat call is made.
- [ ] In staging mode, `!d `-prefixed messages are ignored and others processed.
- [ ] A new developer can run the service locally and receive a test message following only the README.

#### Test Plan

Unit tests for both behaviours. Manual: local run through zrok with `!d `, then staging smoke test (a normal message answered, a `!d ` message ignored by staging and answered locally).

## Local testing with zrok

- The legacy flow stays: `zrok reserve public localhost:8001 -n <ZROK_SHARE_TOKEN>`, with the Meta test app's webhook URL pointing at the reserved share.
- A copy of `zrok.exe` has been placed in the worktree's `tmp/` folder, which is git-ignored, so the command runs there (`tmp\zrok.exe`) and the 86 MB binary never gets committed. The original stays in `ansari-whatsapp`.
- Cleaner alternatives, not used by default: install zrok once on the PATH (for example through a package manager) so no copy is needed per worktree; or keep one shared tools folder outside the repos and put it on the PATH.
- Local runs use the Meta test app's credentials and the shared test number, so messages meant for local must start with `!d ` to keep staging from answering them.

## Risks and Mitigation

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| `apps/auth` endpoint is not available when Phase 2 is built | Medium | Medium | Mock auth client; local end to end deferred until it exists |
| `apps/api` lacks a thread read or delete endpoint needed in Phase 3 | Medium | Medium | Check early in Phase 2; add the smallest change and note it |
| Concurrent messages create two threads for one user | Medium | Low | Cover in tests and document the chosen behaviour |
| Imported code carries secrets, logs or personal files | Low | High | Copy only source, tests and config examples; review the diff before committing |
| Shared test number makes local and staging answer the same message | Medium | Low | `!d ` filter kept and tested |

## Documentation Updates

- New `apps/whatsapp/README.md`.
- Deployment and environment notes for the WhatsApp service in the monorepo docs.
- `codev/resources` arch notes only if the review finds a lasting system-shape fact.
