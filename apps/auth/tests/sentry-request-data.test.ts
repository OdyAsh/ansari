// Issue #333: Sentry's request-data integration attaches the in-flight
// request's body, headers, cookies and query string to an alert event raised
// while that request is handled, here the sign-in that crosses a threshold.
// Production-shaped: default integrations on, only the transport replaced. Its
// own file: the Console integration patches `console` process-wide.
//
// Assertions name which fixture leaked, never its value.
import * as Sentry from '@sentry/node';
import { afterAll, expect, it } from 'vitest';

import { limits } from '@ansari/auth';

import {
  FAILED_SIGN_IN_MESSAGE,
  LIST_WALK_MESSAGE,
  SERVICE_SENTRY_OPTIONS,
  sendFailedSignInAlert,
  sendListWalkAlert,
} from '../src/alerts';
import { send, signUp, startService } from './helpers/service';

const FIXTURES = {
  password: 'ProbePasswordMarker-123',
  emailInBody: 'probe-mailbox-marker',
  bearer: 'SESSIONTOKENMARKER',
  cookie: 'COOKIEVALUEMARKER',
  query: 'QUERYVALUEMARKER',
};

type CapturedEvent = { message?: string; request?: Record<string, unknown> };

afterAll(async () => {
  await Sentry.close();
});

/**
 * Walk LIST_WALK_ALERT.distinctEmails emails to their per-email lock: that
 * crosses SIGN_IN_FAILED_ALERT on the way and the list-walk threshold at the
 * end, so each alert fires once, inside a sign-in request.
 */
async function captureAlertEnvelopes(options: Sentry.NodeOptions) {
  const envelopes: unknown[] = [];
  Sentry.init({
    dsn: 'https://public@o0.ingest.sentry.io/0',
    environment: 'test',
    ...options,
    transport: () => ({
      send: async (envelope) => {
        envelopes.push(envelope);
        return {};
      },
      flush: async () => true,
    }),
  });
  const service = await startService({ alert: sendListWalkAlert, failedSignInAlert: sendFailedSignInAlert });
  try {
    await signUp(service, `${FIXTURES.emailInBody}@example.com`);
    for (let i = 0; i < limits.LIST_WALK_ALERT.distinctEmails; i++) {
      for (let n = 0; n < limits.SIGN_IN_PER_EMAIL.max; n++) {
        await send(service.publicUrl, `/api/auth/sign-in/email?probe=${FIXTURES.query}`, {
          body: { email: `${FIXTURES.emailInBody}-${i}@example.com`, password: FIXTURES.password },
          headers: {
            authorization: `Bearer ${FIXTURES.bearer}`,
            cookie: `probe=${FIXTURES.cookie}`,
          },
        });
      }
    }
    await Sentry.flush(2000);
  } finally {
    await service.close();
    await Sentry.close();
  }
  const events = envelopes
    .flatMap((e) => (e as [unknown, [{ type: string }, CapturedEvent][]])[1])
    .filter(([header]) => header.type === 'event')
    .map(([, event]) => event);
  const text = JSON.stringify(envelopes);
  const leaked = Object.entries(FIXTURES)
    .filter(([, value]) => text.includes(value))
    .map(([name]) => name);
  return { events, leaked };
}

it('a failed-sign-in alert event carries no password, no email from the body and no Authorization header', async () => {
  const { events, leaked } = await captureAlertEnvelopes(SERVICE_SENTRY_OPTIONS);
  expect(events.map((e) => e.message).sort()).toEqual([FAILED_SIGN_IN_MESSAGE, LIST_WALK_MESSAGE].sort());
  for (const event of events) {
    expect(Object.keys(event.request ?? {}).sort()).toEqual(['method', 'url']);
    expect(event.request?.method).toBe('POST');
    expect(event.request?.url).toMatch(/\/api\/auth\/sign-in\/email$/);
  }
  expect(leaked).toEqual([]);
}, 120_000);

it('negative control: without the scrub, the same alert events carry the body, headers, cookies and query', async () => {
  const { events, leaked } = await captureAlertEnvelopes({ ...SERVICE_SENTRY_OPTIONS, beforeSend: undefined });
  expect(events).toHaveLength(2);
  for (const event of events) {
    expect(Object.keys(event.request ?? {})).toEqual(expect.arrayContaining(['data', 'headers']));
  }
  expect(leaked.sort()).toEqual(Object.keys(FIXTURES).sort());
}, 120_000);

it("leaves Sentry's data collection at its private defaults", async () => {
  Sentry.init({ dsn: 'https://public@o0.ingest.sentry.io/0', environment: 'test', ...SERVICE_SENTRY_OPTIONS });
  try {
    expect(Sentry.getClient()?.getDataCollectionOptions()).toMatchObject({
      userInfo: false,
      httpBodies: [],
      genAI: { inputs: false, outputs: false },
      databaseQueryData: false,
    });
  } finally {
    await Sentry.close();
  }
});
