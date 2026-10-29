import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync, realpathSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {test} from 'node:test';

const root = fileURLToPath(new URL('../', import.meta.url));
const manifest = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
const gate = readFileSync(join(root, 'scripts/check-release.mjs'), 'utf8');
const packages = ['react-mcp-stack', 'core', 'components', 'mcp', 'react'];

// Synthetic release inputs, plus the actual deploy alias and release-check program. The fake
// pnpm bypasses registry access only; the real checker still decides whether deployment may start.
function runAlias(target: string, sdk: string) {
  const repo = realpathSync(mkdtempSync(join(tmpdir(), 'ansari-deploy-gate-')));
  const app = join(repo, 'apps/mcp');
  const bin = join(repo, 'bin');
  mkdirSync(join(app, 'scripts'), {recursive: true});
  mkdirSync(join(app, 'src'), {recursive: true});
  mkdirSync(bin);
  const executable = (name: string, body: string) => writeFileSync(join(bin, name), `#!/bin/sh\nset -eu\n${body}\n`, {mode: 0o755});
  try {
    writeFileSync(join(app, 'package.json'), JSON.stringify({...manifest, version: '0.16.6', dependencies: {'@saga-a2ui/react-mcp-stack': sdk}}));
    writeFileSync(join(app, 'src/version.ts'), "export const APP_VERSION = '0.16.6';\n");
    writeFileSync(join(app, 'scripts/check-release.mjs'), gate);
    writeFileSync(join(repo, 'pnpm-workspace.yaml'), 'packages:\n  - apps/*\n');
    writeFileSync(join(repo, 'pnpm-lock.yaml'), [
      "lockfileVersion: '9.0'", 'importers:', '  apps/mcp:', '    dependencies:',
      "      '@saga-a2ui/react-mcp-stack':", `        specifier: ${sdk}`, `        version: ${sdk}`,
      'packages:', ...packages.flatMap(name => [`  '@saga-a2ui/${name}@${sdk}':`, `    resolution: {integrity: sha512-${name}fixture==}`]), '',
    ].join('\n'));
    executable('pnpm', `case "$1" in
  check:release) shift; exec "$DEPLOY_TEST_NODE" "$DEPLOY_TEST_APP/scripts/check-release.mjs" "$@" --no-registry ;;
  build:worker) printf 'build\\n' >> "$DEPLOY_TEST_LOG" ;;
  *) exit 90 ;;
esac`);
    executable('wrangler', `printf 'wrangler %s\\n' "$*" >> "$DEPLOY_TEST_LOG"`);
    executable('git', "printf '0123456789abcdef0123456789abcdef01234567\\n'");
    const log = join(repo, 'calls.log');
    writeFileSync(log, '');
    const result = spawnSync('sh', ['-c', manifest.scripts[`deploy:worker:${target}`]], {
      cwd: app, encoding: 'utf8', env: {...process.env, PATH: `${bin}:${process.env.PATH}`,
        DEPLOY_TEST_NODE: process.execPath, DEPLOY_TEST_APP: app, DEPLOY_TEST_LOG: log},
    });
    return {status: result.status, output: result.stdout + result.stderr, calls: readFileSync(log, 'utf8')};
  } finally { rmSync(repo, {recursive: true, force: true}); }
}

for (const target of ['beta', 'production']) {
  test(`${target} deploy alias stops before build or Wrangler when its release gate fails`, () => {
    const invalid = target === 'production' ? '0.7.6-rc.1' : '0.0.0-candidate.gdeadbeef1234';
    const result = runAlias(target, invalid);
    assert.notEqual(result.status, 0);
    assert.match(result.output, target === 'production' ? /prerelease/ : /private SAGA candidate/);
    assert.equal(result.calls, '');
  });
  test(`${target} deploy alias checks inputs then reaches only the fake build and deploy`, () => {
    const result = runAlias(target, target === 'beta' ? '0.7.6-rc.1' : '0.7.6');
    assert.equal(result.status, 0, result.output);
    assert.match(result.output, new RegExp(`Release inputs for ${target}`));
    assert.match(result.calls, new RegExp(`^build\\nwrangler deploy --env ${target} --var ANSARI_RELEASE:0123456789abcdef0123456789abcdef01234567\\n$`));
  });
}
