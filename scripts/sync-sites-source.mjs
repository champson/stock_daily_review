import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { requireCleanSource, git } from './build-info.mjs';

export function validateRelease(root,archive,releasePath){
  const current=requireCleanSource(root);
  const release=JSON.parse(readFileSync(releasePath,'utf8'));
  if(release.schema_version!==1||release.dirty!==false||release.project_id!==current.project_id||release.commit_sha!==current.commit_sha||release.tree_sha!==current.tree_sha||release.github_repository!==current.github_repository)throw new Error('Release provenance does not match this committed Site source.');
  if(path.basename(archive)!==release.archive_file)throw new Error('Archive file name mismatch.');
  const hash=createHash('sha256').update(readFileSync(archive)).digest('hex');
  if(hash!==release.archive_sha256)throw new Error('Archive checksum mismatch.');
  return release;
}
function authenticatedGit(root,credential,args){
  const env={...process.env,GIT_TERMINAL_PROMPT:'0'};
  if(credential.auth_mode!=='work_proxy'){
    if(credential.auth_mode!=='http_extra_header'||!credential.token)throw new Error('Unsupported Site source authentication.');
    const count=Number(env.GIT_CONFIG_COUNT||0);
    env['GIT_CONFIG_KEY_'+count]='http.'+credential.remote_url+'.extraheader';
    env['GIT_CONFIG_VALUE_'+count]='Authorization: Bearer '+credential.token;
    env.GIT_CONFIG_COUNT=String(count+1);
  }
  const p=spawnSync('git',args,{cwd:root,env,encoding:'utf8',stdio:['ignore','pipe','pipe']});
  if(p.status!==0)throw new Error('Site Git operation failed; refresh source access and inspect for concurrent source changes.');
  return p.stdout.trim();
}
export function syncSource({root=process.cwd(),archive,releasePath,credential}){
  const release=validateRelease(root,archive,releasePath);
  const deployment=JSON.parse(readFileSync(path.join(root,'config/deployment.json'),'utf8'));
  const origin=git(root,'remote','get-url','origin').replace(/\.git$/, '');
  if(!["https://github.com/"+deployment.github_repository,"git@github.com:"+deployment.github_repository].includes(origin))throw new Error('Origin is not the configured GitHub repository.');
  const fetched=spawnSync('git',['fetch','origin','refs/heads/'+deployment.production_branch+':refs/remotes/origin/'+deployment.production_branch],{cwd:root,stdio:'inherit'});
  if(fetched.status!==0||git(root,'rev-parse','refs/remotes/origin/'+deployment.production_branch)!==release.commit_sha)throw new Error('Release is not the current GitHub production branch; fetch and rebuild.');
  const url=new URL(credential.remote_url);
  if(url.protocol!=='https:'||!['git.chatgpt-team.site','eu.git.chatgpt-team.site','us.git.chatgpt-team.site','git.chatgpt.site','eu.git.chatgpt.site','us.git.chatgpt.site','git.appgardenapps.dev','eu.git.appgardenapps.dev','us.git.appgardenapps.dev','git.appgardenapps.com'].includes(url.hostname)||url.username||url.password||credential.repository!==release.project_id||!credential.branch)throw new Error('Source credential does not identify this Site.');
  authenticatedGit(root,credential,['fetch',credential.remote_url,'refs/heads/'+credential.branch]);
  const remote=git(root,'rev-parse','FETCH_HEAD');
  const ancestry=spawnSync('git',['merge-base','--is-ancestor',remote,release.commit_sha],{cwd:root,stdio:'ignore'});
  if(ancestry.status!==0)throw new Error('Sites has source changes absent from GitHub; reconcile them before publishing.');
  authenticatedGit(root,credential,['push',credential.remote_url,release.commit_sha+':refs/heads/'+credential.branch]);
  const pushed=authenticatedGit(root,credential,['ls-remote',credential.remote_url,'refs/heads/'+credential.branch]).split(/\s/)[0];
  if(pushed!==release.commit_sha)throw new Error('Site source changed concurrently; recheck before saving.');
  return {project_id:release.project_id,commit_sha:release.commit_sha,archive:path.resolve(archive),archive_sha256:release.archive_sha256};
}
async function main(){
  // Disable terminal echo before accepting the short-lived credential.
  if(process.stdin.isTTY)spawnSync('stty',['-echo'],{stdio:['inherit','ignore','ignore']});
  console.log('Ready for Site source synchronization JSON on stdin (input is hidden).');
  let input='';
  for await(const chunk of process.stdin){input+=chunk;if(input.includes('\n'))break;}
  const options=JSON.parse(input.split('\n')[0]);
  const ai=process.argv.indexOf('--archive'),ri=process.argv.indexOf('--release');
  if(ai<0||ri<0)throw new Error('--archive and --release are required.');
  console.log(JSON.stringify(syncSource({archive:process.argv[ai+1],releasePath:process.argv[ri+1],credential:options.credential})));
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url))main().catch(error=>{console.error(error.message);process.exitCode=1;});
