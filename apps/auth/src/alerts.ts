// The list-walk alert (spec 300, Client IP fallback; plan P-2). When per-email
// limits trip for many distinct emails in a short period, someone is likely
// walking an email list. The event carries counts and 8-character prefixes of
// the email hashes, never an email.
import * as Sentry from '@sentry/node';

export type ListWalkAlert = {
  /** Distinct emails that hit a per-email limit inside the window. */
  distinctEmails: number;
  /** The window, in seconds. */
  windowSeconds: number;
  /** First 8 hex characters of sha256(normalized email), a sample. */
  hashPrefixes: string[];
};

export const LIST_WALK_MESSAGE = 'auth: per-email limits tripped across many emails (list walk?)';

/**
 * Failed sign-ins over SIGN_IN_FAILED_ALERT in one window, every email
 * together (a spray under the per-email lock). Nothing is refused.
 */
export type FailedSignInAlert = {
  /** Failed sign-ins in the window, every email together. */
  failedSignIns: number;
  /** The window, in seconds. */
  windowSeconds: number;
  /** failedSignIns per minute over the window. */
  perMinute: number;
  /** Distinct emails that failed inside the window. */
  distinctEmails: number;
  /** First 8 hex characters of sha256(normalized email), a sample. */
  hashPrefixes: string[];
};

export const FAILED_SIGN_IN_MESSAGE = 'auth: failed sign-ins over the alert threshold, every email together (spray?)';

/**
 * Headers an event may keep: none is a credential, and the user agent is one
 * of the few things that tells a botnet spray from a broken client.
 */
const KEPT_HEADERS = new Set(['host', 'user-agent', 'content-type']);

/**
 * Sentry's request-data integration attaches the in-flight request (headers,
 * cookies, query string and body) to every event raised while it is handled:
 * a sign-in's email and password, a bearer. `sendDefaultPii: false` does not
 * cover bodies or the Authorization header, so every event keeps only the
 * request's method, path and the headers in KEPT_HEADERS.
 */
export function scrubRequestEvent<E extends Sentry.Event>(event: E): E {
  const request = event.request;
  if (!request) return event;
  const headers = Object.fromEntries(
    Object.entries(request.headers ?? {}).filter(([name]) => KEPT_HEADERS.has(name.toLowerCase()))
  );
  event.request = { method: request.method, url: request.url?.split('?')[0], headers };
  return event;
}

/**
 * The service's Sentry options beside the DSN and environment; tests add only a transport.
 * Breadcrumbs are off: an HTTP breadcrumb carries its request's query string,
 * and a console breadcrumb carries whatever the log line held.
 */
export const SERVICE_SENTRY_OPTIONS = {
  sendDefaultPii: false,
  maxBreadcrumbs: 0,
  beforeBreadcrumb: () => null,
  beforeSend: scrubRequestEvent,
} as const;

export function initSentry(dsn: string | undefined, environment: string): void {
  if (!dsn) return;
  Sentry.init({ dsn, environment, ...SERVICE_SENTRY_OPTIONS });
}

/** One Sentry event (plus a log line with the same fields). */
export function sendFailedSignInAlert(alert: FailedSignInAlert): void {
  console.warn(FAILED_SIGN_IN_MESSAGE, alert);
  Sentry.captureMessage(FAILED_SIGN_IN_MESSAGE, {
    level: 'warning',
    tags: { alert: 'auth-failed-sign-ins' },
    extra: { ...alert },
  });
}

/** One Sentry event (plus a log line with the same fields). */
export function sendListWalkAlert(alert: ListWalkAlert): void {
  console.warn(LIST_WALK_MESSAGE, alert);
  Sentry.captureMessage(LIST_WALK_MESSAGE, {
    level: 'warning',
    tags: { alert: 'auth-list-walk' },
    extra: { ...alert },
  });
}
