# 301: Implementation plan

Protocol: ASPIR; final upstream merge remains a maintainer decision.

```json
{"phases":[{"id":"transfer","title":"Import application and workspace integration","status":"completed"},{"id":"review-corrections","title":"Align workspace, preserve MCP compatibility and enforce release/licence boundaries","status":"in-progress"},{"id":"qualification","title":"Qualify destination revision and prepare contribution PR","status":"pending"}]}
```

## Transfer

Copy only application source, assets, tests and build scripts from qualified source tag `ansari-public-sdk-qualified-2026-10-21`. Exclude operational records and private deployment adapters. Use the existing `apps/*` workspace enrollment, preserve root overrides and add the supported Markdown adapter pin. Use the TypeScript catalog, one root lockfile and package-local Turbo runtime environment declarations. Keep the new listener opt-in for local development. Use the repository-root Docker context and a path-filtered MCP CI job, without a second application lockfile.

Verify dependency resolution, content/license audit, build/typecheck, server/browser suite and no-network Docker smoke. Consult independently before committing.

## Qualification

Push the reviewed contribution branch to the contributor fork. Build the exact commit using public packages and deploy to the existing authorized DEV target. Preserve the private operational adapter outside this repository. Reuse the owner-accepted existing host baseline with its original identities and limitations. Validate the changed boundaries proportionately; do not repeat three-host testing for metadata or mechanical changes. Record new failures and recovery accurately. Update the existing contribution with current-head checks and independent review; upstream merge remains the maintainer's decision.

Use the SAGA CLI’s qualified Hono starter as a separate reference. Mount the upstream stateless MCP request handler on Hono with official Host/Origin middleware. Extract startup-only view preparation, remove session/replay and per-call lifetime workarounds, and qualify modern and older-host traffic plus the existing UI/container path.

## Review corrections

Keep the contribution in one PR. First move the package to `apps/mcp`, use the root lockfile/Docker context, enroll shared lint/catalog tools and scope the dedicated workflow. Then preserve legacy tool discovery and input limits, propagate the request signal and replace configured allowlists. Finally enforce deployment release gates, retain distributed licence notices and update current stable-version/evidence prose. Preserve the already-merged stable 0.7.6 dependency change and existing host receipts.
