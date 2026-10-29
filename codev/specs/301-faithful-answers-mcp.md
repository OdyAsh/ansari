# 301: Ansari MCP application

Contribute the independently reviewed standalone Ansari application to this repository. It retrieves a complete Ansari answer and attribution, then renders it through the public saga a2ui React packages in ChatGPT, Claude and Alexa.

## Constraints

Use `apps/mcp` within the established application workspace. React only, MCP only, no SDUI or private source dependencies. Use the public saga a2ui dependencies declared in `package.json` and preserve the SAGA-owned A2UI dependency boundary. Retain Kitab fonts and their OFL notices, contributed artwork, complete answers, content policy and optional lowercase framework credit. Keep the existing API/auth/frontend development experience unchanged. No production deployment or upstream merge is included.

## Approach

Import the qualified application with minimal workspace adaptation. Reimplementing the card or adding a shared renderer would introduce unnecessary divergence. Public dependencies, a root Markdown adapter override and one root lockfile provide a reproducible consumer setup.

## Acceptance

The destination branch installs and passes application typecheck/build/server/browser checks and root-context Docker smoke checks without private package credentials. Runtime assets contain real bytes. The application owner accepts the existing host baseline recorded in the application README and linked release evidence. Preserve each observation's original revision and limits, and reuse it for unchanged behavior; metadata promotion and mechanical workspace changes do not require repeating the full host matrix. Test affected runtime and delivery behavior, adding one representative host observation only when a changed delivery/rendering boundary needs it. Fresh unrun behavior remains explicitly unverified. Host reload limitations remain explicit; desktop checks do not prove mobile reliability.

The HTTP listener uses Hono and stateless MCP by default, including stateless compatibility for older hosts. Prepare and validate the view before listening, reusing one stamped HTML/URI pair across requests. Per-request identity, budgets and cleanup remain isolated; no session map, sweeper or replay store is retained.

The package is `ansari-mcp`, exposed through `pnpm mcp`; root build/test/typecheck/coverage exclude it and root lint includes it. Keep one root lockfile. Preserve the legacy `answer_islamic_question` discovery contract with readOnlyHint/openWorldHint, validate 1–4,000 characters, and pass the bounded request signal through the upstream fetch. Configured Host/Origin lists replace local defaults. Beta/Production deployment scripts enforce their release gates; Production requires stable SDK/application inputs. Bundle applicable licence notices with the HTML and retain the artwork's explicit MIT grant.
