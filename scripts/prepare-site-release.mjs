import { createHash } from 'node:crypto';
import { execFileSync, spawnSync } from 'node:child_process';
import { readFileSync, existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { requireCleanSource } from './build-info.mjs';

function command(root,name,args){
  const p=spawnSync(name,args,{cwd:root,stdio:'inherit'});
  if(p.error)throw p.error;
  if(p.status!==0)throw new Error(name+' '+args.join(' ')+' failed.');
}
export function prepareRelease({root=process.cwd(),output,check,build}={}){
  if(!output||!path.isAbsolute(output))throw new Error('--output must be an absolute archive path.');
  const before=requireCleanSource(root);
  (check||(()=>command(root,'npm',['test'])))();
  rmSync(path.join(root,'dist'),{recursive:true,force:true});
  (build||(()=>command(root,'npm',['run','build'])))();
  const after=requireCleanSource(root);
  if(before.commit_sha!==after.commit_sha||before.tree_sha!==after.tree_sha)throw new Error('Source changed during release.');
  const dist=path.join(root,'dist');
  const hosting=JSON.parse(readFileSync(path.join(dist,'.openai/hosting.json'),'utf8'));
  if(hosting.project_id!==before.project_id)throw new Error('Built Site identity does not match source.');
  if(hosting.static){
    if(!existsSync(path.join(dist,hosting.static.directory,'index.html')))throw new Error('Missing static entrypoint.');
  }else if(!existsSync(path.join(dist,'server/index.js')))throw new Error('Missing Worker entrypoint.');
  mkdirSync(path.dirname(output),{recursive:true});
  execFileSync('tar',['-cf',output,'-C',dist,'.'],{stdio:['ignore','pipe','pipe']});
  const listing=execFileSync('tar',['-tf',output],{encoding:'utf8',stdio:['ignore','pipe','pipe']}).trim().split('\n');
  if(!listing.includes('./.openai/hosting.json'))throw new Error('Release archive lacks hosting manifest.');
  const release={...before,archive_file:path.basename(output),archive_sha256:createHash('sha256').update(readFileSync(output)).digest('hex'),archive_bytes:readFileSync(output).byteLength,artifact_file_count:listing.filter(n=>!n.endsWith('/')).length};
  const releasePath=path.join(path.dirname(output),'release.json');
  writeFileSync(releasePath,JSON.stringify(release,null,2)+'\n');
  return {project_id:before.project_id,commit_sha:before.commit_sha,tree_sha:before.tree_sha,archive:output,release:releasePath,archive_sha256:release.archive_sha256};
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  try{const i=process.argv.indexOf('--output');const result=prepareRelease({output:i<0?undefined:process.argv[i+1]});console.log(JSON.stringify(result));}
  catch(error){console.error(error.message);process.exitCode=1;}
}
