import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import path from 'node:path';

export function git(root, ...args) {
  return execFileSync('git', args, {cwd:root, encoding:'utf8', stdio:['ignore','pipe','pipe']}).trim();
}

export function getBuildInfo(root=process.cwd()) {
  const hosting=JSON.parse(readFileSync(path.join(root,'.openai/hosting.json'),'utf8'));
  const deployment=JSON.parse(readFileSync(path.join(root,'config/deployment.json'),'utf8'));
  const commit=git(root,'rev-parse','HEAD');
  const tree=git(root,'rev-parse','HEAD^{tree}');
  if(!/^[a-f0-9]{40}$/.test(commit)||!/^[a-f0-9]{40}$/.test(tree)||!hosting.project_id)throw new Error('Missing committed Site identity.');
  return {schema_version:1,project_id:hosting.project_id,github_repository:deployment.github_repository,
    commit_sha:commit,tree_sha:tree,dirty:Boolean(git(root,'status','--porcelain')),
    source_url:'https://github.com/'+deployment.github_repository+'/commit/'+commit,
    built_at:new Date().toISOString()};
}

export function requireCleanSource(root=process.cwd()) {
  const info=getBuildInfo(root);
  if(info.dirty)throw new Error('Release requires a clean Git checkout; commit source changes first.');
  return info;
}
