// Drizzle client for the Better Auth stack.
//
// Design decision #1 (see codev/plans/59-*): the db client and schema live
// INSIDE packages/auth rather than a separate packages/db, so the whole auth
// stack is one self-contained unit that #60 can relocate or merge wholesale.
// Every table it touches is in the `auth` Postgres schema (src/schema.ts); it
// shares no Drizzle code with apps/api.
//
// A node-postgres pool connects lazily, so this module can be constructed
// without opening a connection; the first query is what connects.
import { sql } from 'drizzle-orm';
import { drizzle } from 'drizzle-orm/node-postgres';
import { Pool } from 'pg';

import { getEnv } from './env';
import * as schema from './schema';

/**
 * An idle pooled client that loses its connection (a database restart,
 * failover, maintenance) makes the pool emit `error`. With no listener that
 * is an uncaught exception and the service process exits; the pool already
 * discards the dead client and the next query reconnects, so log and go on.
 * Only `{name, code}`: a driver error can carry query text and parameters.
 */
export function onPoolError(err: Error & { code?: string }): void {
  console.error('auth database: idle client error', { name: err.name, code: err.code });
}

/**
 * pg's default pool size, written down so it is a decision (spec 329). The
 * pool is shared by every route: sign-in, the JWKS, the account endpoints and
 * the WhatsApp exchange.
 */
export const AUTH_POOL_MAX = 10;

/**
 * How long a query waits for a free pooled connection before failing (pg's
 * default is forever). A starved pool is then a loud error, answered as a 500,
 * never an unbounded wait during which no statement or lock timeout can fire.
 */
export const AUTH_POOL_CONNECT_TIMEOUT_MS = 5_000;

export function createDb() {
  const pool = new Pool({
    connectionString: getEnv().DATABASE_URL,
    max: AUTH_POOL_MAX,
    connectionTimeoutMillis: AUTH_POOL_CONNECT_TIMEOUT_MS,
  });
  pool.on('error', onPoolError);
  return drizzle({ client: pool, schema });
}

export type Db = ReturnType<typeof createDb>;

/** `select 1` on the auth database; throws when it cannot answer. */
export async function pingDatabase(db: Db): Promise<void> {
  await db.execute(sql`select 1`);
}
