// The exchange under the service's own Sentry options (spec 329: Sentry events
// from the exchange carry no request body, no Authorization header and no
// phone number). Default integrations stay on, as in production; only the
// transport is a spy. Its own file: the Console integration patches `console`
// for the whole process.
import * as Sentry from '@sentry/node';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { serviceUsers } from '@ansari/auth';

import { EXCHANGE_ALERT_MESSAGE, SERVICE_SENTRY_OPTIONS } from '../src/alerts';
import { WHATSAPP_SESSION_PATH } from '../src/routes/whatsapp';
import { startService, type Service } from './helpers/service';

const envelopes: unknown[] = [];
let service: Service;

beforeAll(async () => {
  Sentry.init({
    dsn: 'https://public@o0.ingest.sentry.io/0',
    environment: 'test',
    ...SERVICE_SENTRY_OPTIONS,
    transport: () => ({
      send: async (envelope) => {
        envelopes.push(envelope);
        return {};
      },
      flush: async () => true,
    }),
  });
  service = await startService();
});

afterAll(async () => {
  await Sentry.close();
  await service?.close();
});

describe('Sentry events from the exchange', () => {
  it('carry no body, Authorization header, BSUID, phone number or key', async () => {
    // A key whose owner has lost its `service` row: the route raises the
    // service-user alert while the request (with its body) is in flight.
    const owner = await serviceUsers.createServiceUser(service.db, 'whatsapp');
    const { key } = await service.auth.api.createApiKey({
      body: { userId: owner.id, prefix: 'wa_', permissions: { whatsapp: ['mint'] }, rateLimitEnabled: false },
    });
    await service.client.query('delete from auth.account where user_id = $1', [owner.id]);

    const bsuid = 'US.48151623420815162342';
    const phone = '15550001111';
    const res = await fetch(`${service.publicUrl}${WHATSAPP_SESSION_PATH}`, {
      method: 'POST',
      headers: { authorization: `Bearer ${key}`, 'content-type': 'application/json' },
      body: JSON.stringify({ bsuid, phone_number: phone }),
    });
    expect(res.status).toBe(500);
    await Sentry.flush(2000);

    const serialized = JSON.stringify(envelopes);
    expect(serialized).toContain(EXCHANGE_ALERT_MESSAGE);
    for (const needle of [bsuid, phone, key, key.slice(3, 20), 'phone_number', 'bsuid']) {
      expect(serialized).not.toContain(needle);
    }
    expect(serialized.toLowerCase()).not.toContain('authorization');
  });
});
