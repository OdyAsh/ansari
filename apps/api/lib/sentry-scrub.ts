import type { Event, NodeOptions } from "@sentry/nextjs";

// The client IP travels in x-real-ip (Railway's edge) and x-forwarded-for;
// `sendDefaultPii: false` means Sentry should not receive it. The SDK drops
// these itself only while `userInfo` is off, which Sentry v11 no longer defaults.
const DROPPED_HEADERS = new Set(["authorization", "cookie", "x-real-ip", "x-forwarded-for"]);
// @sentry/nextjs copies headers onto the root span as attributes
// (`http.request.header.cookie.<name>`, dashes as underscores), masking only
// sensitive-looking names.
const DROPPED_ATTRIBUTE =
  /^http\.(request\.header\.(authorization|cookie|x_real_ip|x_forwarded_for)|response\.header\.set_cookie)(\.|$)/;

function dropAttributes(data: Record<string, unknown> | undefined): void {
  if (!data) return;
  for (const key of Object.keys(data)) {
    if (DROPPED_ATTRIBUTE.test(key)) delete data[key];
  }
}

/**
 * Sentry's request-data integration attaches the in-flight request to every
 * event raised while it is handled. Strip what can carry user content or a
 * credential: the body (user messages), the query string (`/v2/mcp-complete?q=`
 * is the user's question), the Authorization header (the user's JWT),
 * cookies and the client IP (its headers and `user.ip_address`), from the
 * request and from span attributes (issue #333).
 * Node lowercases incoming header names, but a header object built elsewhere
 * may not, so names are matched case-blind.
 */
export function scrubRequestEvent<E extends Event>(event: E): E {
  const request = event.request;
  if (request) {
    delete request.data;
    delete request.cookies;
    delete request.query_string;
    if (request.url) request.url = request.url.split("?")[0];
    if (request.headers) {
      for (const name of Object.keys(request.headers)) {
        if (DROPPED_HEADERS.has(name.toLowerCase())) delete request.headers[name];
      }
    }
  }
  if (event.user) delete event.user.ip_address;
  dropAttributes(event.contexts?.trace?.data);
  for (const span of event.spans ?? []) dropAttributes(span.data);
  return event;
}

/**
 * The server's request-privacy options; tests spread exactly these. Transactions
 * carry the request too. Never set `dataCollection` partially here: any value
 * switches every category it omits to collect-all (user info, bodies, GenAI
 * inputs and outputs, database query data).
 */
export const SENTRY_REQUEST_PRIVACY = {
  beforeSend: scrubRequestEvent,
  beforeSendTransaction: scrubRequestEvent,
} satisfies NodeOptions;
