import type { Event, NodeOptions } from "@sentry/nextjs";

/**
 * Sentry's request-data integration attaches the in-flight request to every
 * event raised while it is handled. Strip what can carry user content or a
 * credential: the body (user messages), the Authorization header (the user's
 * JWT) and cookies (issue #333). Node lowercases incoming header names, but a
 * header object built elsewhere may not, so names are matched case-blind.
 */
export function scrubRequestEvent<E extends Event>(event: E): E {
  const request = event.request;
  if (!request) return event;
  delete request.data;
  delete request.cookies;
  if (request.headers) {
    for (const name of Object.keys(request.headers)) {
      const lower = name.toLowerCase();
      if (lower === "authorization" || lower === "cookie") delete request.headers[name];
    }
  }
  return event;
}

/**
 * The server's request-privacy options; tests spread exactly these. Transactions
 * carry the request too, and @sentry/nextjs copies each cookie onto the root
 * span as an attribute unless cookie collection is off.
 */
export const SENTRY_REQUEST_PRIVACY = {
  dataCollection: { cookies: false },
  beforeSend: scrubRequestEvent,
  beforeSendTransaction: scrubRequestEvent,
} satisfies NodeOptions;
