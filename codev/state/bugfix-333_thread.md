# bugfix-333 thread

## Investigate (2026-10-28)
- Reproduced on develop with spir-329's test (spy transport, default integrations, 40 failed sign-ins): 1 event; `request` keys = headers, method, url, cookies, query_string, data; `headers.authorization` present; password marker in envelope.
- Root cause: apps/auth `initSentry` sets only `sendDefaultPii: false`, no `beforeSend`; apps/api `beforeSend` drops only `request.data`.
- #331 (builder/spir-329) still open; it adds `SERVICE_SENTRY_OPTIONS` with an exchange-only `scrubExchangeEvent`. Plan: add a general scrub here under the same `SERVICE_SENTRY_OPTIONS` name so either rebase is a one-line merge.

## Fix (2026-10-28)
- apps/auth: `scrubRequestEvent` reduces every event's `request` to `{method, url without query}`; `SERVICE_SENTRY_OPTIONS` = sendDefaultPii false + breadcrumbs off + that `beforeSend`. Same shape as #331's options; the general scrub subsumes `scrubExchangeEvent`.
- Surprise 1: with only the beforeSend scrub, the query string still reached Sentry through HTTP breadcrumbs (`breadcrumbs[].data.http.query`). Breadcrumbs off (as #331 does) closes it.
- apps/api: under `@sentry/nextjs` a plain node http server attaches no request data; the production paths are `onRequestError` (captureRequestError: all headers) and request-scope events/transactions. Test drives the real `onRequestError` export.
- Surprise 2: `@sentry/nextjs` honours only the first `Sentry.init` per process (re-init after close keeps the closed client), so the apps/api negative control is its own file.
- Surprise 3: Next's root-span hook copies cookies into transaction span attributes (`contexts.trace.data.http.request.header.cookie.<name>`), filtering only sensitive-looking names. Fixed with `dataCollection: { cookies: false }` + `beforeSendTransaction`.
- Mutations (apps/api): no dataCollection / no beforeSendTransaction / keep authorization / keep data → red. Keep cookie header → green (equivalent while cookies collection is off; strip kept as defence in depth).
- `afx send` failed: Tower not running.
