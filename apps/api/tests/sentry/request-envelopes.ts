// Shared driver for the issue #333 Sentry tests. Production-shaped: default
// integrations on, only the transport replaced, sampled as
// sentry.server.config.ts samples outside production. @sentry/nextjs honours
// only the first init in a process, so each file inits once, with its own
// options: the scrubbed run and its negative control live in separate files.
//
// Next reports every unhandled route error through `onRequestError`, which
// apps/api wires to Sentry.captureRequestError (src/instrumentation.ts); it
// attaches every request header. Events and transactions raised in a request
// scope carry the request data the scope holds, body and query included.
import * as Sentry from "@sentry/nextjs";

import { onRequestError } from "@/instrumentation";

export const FIXTURES = {
  bearer: "USERJWTMARKER",
  cookie: "COOKIEVALUEMARKER",
  body: "USERMESSAGEMARKER",
  query: "USERQUESTIONMARKER",
  ip: "203.0.113.77",
};

const HEADERS = {
  host: "api.example.test",
  authorization: `Bearer ${FIXTURES.bearer}`,
  cookie: `probe=${FIXTURES.cookie}`,
  "x-real-ip": FIXTURES.ip,
  "x-forwarded-for": FIXTURES.ip,
  "content-type": "application/json",
};

type Item = [{ type: string }, { request?: Record<string, unknown> }];

const envelopes: unknown[] = [];

export function initSentry(options: Sentry.NodeOptions): void {
  Sentry.init({
    dsn: "https://public@o0.ingest.sentry.io/0",
    environment: "test",
    tracesSampleRate: 1.0,
    ...options,
    transport: () => ({
      send: async (envelope) => {
        envelopes.push(envelope);
        return {};
      },
      flush: async () => true,
    }),
  });
}

/** Every envelope item an unhandled route error and an in-request event produce, and which fixtures they hold (by name). */
export async function captureRequestEnvelopes() {
  envelopes.length = 0;
  await onRequestError(
    new Error("probe: unhandled in a route"),
    { path: "/api/v2/threads", method: "POST", headers: HEADERS },
    { routerKind: "App Router", routePath: "/api/v2/threads", routeType: "route" }
  );
  Sentry.withIsolationScope((scope) => {
    scope.setSDKProcessingMetadata({
      normalizedRequest: {
        method: "POST",
        url: `https://api.example.test/api/v2/mcp-complete?q=${FIXTURES.query}`,
        query_string: `q=${FIXTURES.query}`,
        headers: HEADERS,
        data: JSON.stringify({ message: FIXTURES.body }),
      },
    });
    Sentry.startSpan({ name: "POST /api/v2/mcp-complete", forceTransaction: true }, () => {
      Sentry.captureException(new Error("probe: raised inside a request"));
    });
  });
  await Sentry.flush(2000);
  const items = envelopes
    .flatMap((e) => (e as [unknown, Item[]])[1])
    .filter(([header]) => header.type !== "client_report");
  const text = JSON.stringify(envelopes);
  const leaked = Object.entries(FIXTURES)
    .filter(([, value]) => text.includes(value))
    .map(([name]) => name);
  return { items, leaked };
}
