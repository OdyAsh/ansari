// Negative control for sentry-request-data.test.ts (issue #333): the same
// envelopes, without SENTRY_REQUEST_PRIVACY, carry every fixture. Its own file:
// @sentry/nextjs honours only the first init in a process.
import * as Sentry from "@sentry/nextjs";
import { afterAll, beforeAll, expect, it } from "vitest";

import { captureRequestEnvelopes, FIXTURES, initSentry } from "./request-envelopes";

beforeAll(() => initSentry({}));
afterAll(() => Sentry.close());

it("without the scrub, the same envelopes carry the bearer, cookie and body", async () => {
  const { leaked } = await captureRequestEnvelopes();
  expect(leaked.sort()).toEqual(Object.keys(FIXTURES).sort());
});
