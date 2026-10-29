// Negative control for sentry-request-data.test.ts (issue #333): the same
// envelopes, without SENTRY_REQUEST_PRIVACY, carry every fixture. Its own file:
// @sentry/nextjs honours only the first init in a process.
import * as Sentry from "@sentry/nextjs";
import { afterAll, beforeAll, expect, it } from "vitest";

import { captureRequestEnvelopes, FIXTURES, initSentry } from "./request-envelopes";

beforeAll(() => initSentry({}));
afterAll(() => Sentry.close());

it("without the scrub, the same envelopes carry the bearer, cookie, body and query", async () => {
  const { leaked } = await captureRequestEnvelopes();
  // The SDK itself drops the client-IP headers while `userInfo` is off (the
  // `sendDefaultPii: false` default); sentry-request-data-collect-all.test.ts
  // proves the scrub drops them when it is on.
  expect(leaked.sort()).toEqual(Object.keys(FIXTURES).filter((name) => name !== "ip").sort());
});
