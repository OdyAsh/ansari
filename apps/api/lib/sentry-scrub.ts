import type { Event, NodeOptions } from "@sentry/nextjs";

const CREDENTIAL_HEADERS = new Set(["authorization", "cookie"]);
// @sentry/nextjs copies headers onto the root span as attributes
// (`http.request.header.cookie.<name>`, dashes as underscores), masking only
// sensitive-looking names.
const CREDENTIAL_ATTRIBUTE =
  /^http\.(request\.header\.(authorization|cookie)|response\.header\.set_cookie)(\.|$)/;

function dropCredentialAttributes(data: Record<string, unknown> | undefined): void {
  if (!data) return;
  for (const key of Object.keys(data)) {
    if (CREDENTIAL_ATTRIBUTE.test(key)) delete data[key];
  }
}

/**
 * Sentry's request-data integration attaches the in-flight request to every
 * event raised while it is handled. Strip what can carry user content or a
 * credential: the body (user messages), the query string (`/v2/mcp-complete?q=`
 * is the user's question), the Authorization header (the user's JWT) and
 * cookies, from the request and from span attributes (issue #333).
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
        if (CREDENTIAL_HEADERS.has(name.toLowerCase())) delete request.headers[name];
      }
    }
  }
  dropCredentialAttributes(event.contexts?.trace?.data);
  for (const span of event.spans ?? []) dropCredentialAttributes(span.data);
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
