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
