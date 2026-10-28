import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { resolve } from 'path';

// Pin for issue #330: the root `pnpm test` runs every package's vitest suite at
// once under turbo, and each suite defaults to (cores - 1) workers, so a 14-core
// machine ran ~50 forks and pglite setup hooks crossed the 30 s deadline under
// ordinary load. The root script caps each suite's pool instead of raising
// deadlines. This only pins that the cap is there; the real proof is the load
// harness in codev/reviews/330 (28 busy loops on 14 cores: red without the cap,
// green with it). The root package.json is in this task's turbo inputs, so
// removing the flag busts the cache and this file runs.

const rootPackageJson = resolve(__dirname, '../../../package.json');

describe('root test script', () => {
  it('caps vitest workers with a number', () => {
    const { scripts } = JSON.parse(readFileSync(rootPackageJson, 'utf8')) as {
      scripts: Record<string, string>;
    };

    expect(scripts.test).toMatch(/^turbo run test -- .*--maxWorkers=\d+\b/);
  });
});
