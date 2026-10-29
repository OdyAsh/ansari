import {existsSync, readFileSync, realpathSync} from 'node:fs';
import {createRequire} from 'node:module';
import path from 'node:path';

/** Keep installed package notices in the HTML that actually carries their code. */
export function thirdPartyLicenceComment(appRoot: string): string {
  const require = createRequire(path.join(appRoot, 'package.json'));
  const packageRoot = (name: string, resolver = require): string => {
    // Some packages expose only type exports. Locate their installed manifest through
    // the dependency's normal Node search paths without importing a runtime entry.
    const manifest = (resolver.resolve.paths(name) ?? [])
      .map(directory => path.join(directory, name, 'package.json'))
      .find(file => existsSync(file));
    if (!manifest) throw new Error(`No installed package manifest for ${name}`);
    return realpathSync(path.dirname(manifest));
  };
  const stack = packageRoot('@saga-a2ui/react-mcp-stack');
  const fromStack = createRequire(path.join(stack, 'package.json'));
  const licences = new Map<string, string[]>();
  const notices: string[] = [];
  for (const name of ['core', 'components', 'mcp', 'react', 'react-mcp-stack']) {
    const directory = packageRoot(`@saga-a2ui/${name}`, fromStack);
    const manifest = JSON.parse(readFileSync(path.join(directory, 'package.json'), 'utf8'));
    const identity = `${manifest.name}@${manifest.version}`;
    const licence = readFileSync(path.join(directory, 'LICENSE'), 'utf8').trim();
    licences.set(licence, [...(licences.get(licence) ?? []), identity]);
    for (const file of ['NOTICE', 'NOTICE.md', 'THIRD_PARTY_NOTICES.md']) {
      if (existsSync(path.join(directory, file))) notices.push(`${identity}: ${file}\n\n${readFileSync(path.join(directory, file), 'utf8').trim()}`);
    }
  }
  const text = [
    ...[...licences].map(([licence, names]) => `${names.join('\n')}\n\n${licence}`),
    ...notices,
    `Ansari application (MIT)\n\n${readFileSync(path.join(appRoot, '../../LICENSE'), 'utf8').trim()}`,
    ...['NOTICE.md', 'fonts/OFL.txt', 'fonts/AUTHORS.txt', 'fonts/CONTRIBUTORS.txt'].map(file =>
      `Ansari assets: ${file}\n\n${readFileSync(path.join(appRoot, 'assets', file), 'utf8').trim()}`),
  ].join('\n\n');
  // A dependency notice must never close this comment or introduce executable HTML.
  return `<!--\nBEGIN THIRD-PARTY LICENCES\n${text.replaceAll('--', '-&#45;')}\nEND THIRD-PARTY LICENCES\n-->\n`;
}
