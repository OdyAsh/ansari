import assert from 'node:assert/strict';
import {test} from 'node:test';
import {existsSync, readFileSync, statSync} from 'node:fs';
import {join, relative} from 'node:path';
import {fileURLToPath} from 'node:url';

// The Docker build context (the repository root) must not carry
// credentials or local runtime state. Everything here is a synthetic marker; no real secret file is read.
const root=fileURLToPath(new URL('../',import.meta.url));
const NEVER_COPIED=['.env','.env.local','.dev.vars','.dev.vars.staging','.dev.vars.production','.wrangler/state/v3/cache.sqlite','dist-worker/index.js','node_modules/x/index.js','dist/view.html','.npmrc'];

// A minimal Docker ignore matcher: `*` stays within a path segment, `**` crosses segments, and a
// matching parent directory excludes everything beneath it.
const toRegExp=(pattern:string)=>new RegExp('^'+pattern.replace(/[.+^${}()|[\]\\]/g,'\\$&').replace(/\*\*\/?/g,'\u0000').replace(/\*/g,'[^/]*').replace(/\u0000/g,'(?:.*/)?')+'$');
const ignored=(patterns:string[],path:string)=>{
  const parts=path.split('/');
  let excluded=false;
  for(const pattern of patterns) {
    const negate=pattern.startsWith('!');
    const re=toRegExp(negate?pattern.slice(1):pattern);
    if(parts.some((_,index)=>re.test(parts.slice(0,index+1).join('/')))) excluded=!negate;
  }
  return excluded;
};
const patternsOf=(file:string)=>readFileSync(file,'utf8').split('\n').map(line=>line.trim()).filter(line=>line&&!line.startsWith('#'));

test('the Docker build context excludes the Worker secret files and local state',()=>{
  const contexts:Array<[string,string[]]>=[];
  const repoIgnore=join(root,'../../.dockerignore');
  if(existsSync(repoIgnore)&&statSync(repoIgnore).isFile()) {
    contexts.push([repoIgnore,[...NEVER_COPIED, ...NEVER_COPIED.map(name=>`apps/mcp/${name}`)]]);
  }
  for(const [file,secrets] of contexts) {
    const patterns=patternsOf(file);
    for(const secret of secrets) assert.ok(ignored(patterns,secret),`${relative(root,file)} excludes ${secret}`);
    for(const kept of ['package.json','src/node/index.ts','wrangler.jsonc']) assert.ok(!ignored(patterns,kept),`${relative(root,file)} keeps ${kept}`);
  }
});
