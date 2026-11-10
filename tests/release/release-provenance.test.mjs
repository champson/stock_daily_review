import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync,mkdirSync,writeFileSync,readFileSync,rmSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { prepareRelease } from '../../scripts/prepare-site-release.mjs';
import { validateRelease } from '../../scripts/sync-sites-source.mjs';
function fixture(){
  const root=mkdtempSync(path.join(tmpdir(),'site-release-test-'));
  mkdirSync(path.join(root,'.openai'));mkdirSync(path.join(root,'config'));
  writeFileSync(path.join(root,'.openai/hosting.json'),JSON.stringify({project_id:'test-site',r2:'BUCKET',d1:null}));
  writeFileSync(path.join(root,'config/deployment.json'),JSON.stringify({github_repository:'test/example',production_branch:'main'}));
  writeFileSync(path.join(root,'.gitignore'),'dist/\n');writeFileSync(path.join(root,'source.txt'),'deployed source');
  for(const args of [['init','-q'],['add','.'],['-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','source']])execFileSync('git',args,{cwd:root,stdio:'ignore'});
  const output=path.join(mkdtempSync(path.join(tmpdir(),'site-release-artifact-')),'site-release.tar');
  return {root,output,build(){mkdirSync(path.join(root,'dist/.openai'),{recursive:true});mkdirSync(path.join(root,'dist/server'),{recursive:true});writeFileSync(path.join(root,'dist/.openai/hosting.json'),readFileSync(path.join(root,'.openai/hosting.json')));writeFileSync(path.join(root,'dist/server/index.js'),'export default {fetch(){return new Response("ok")}}');}};
}
function cleanup(f){rmSync(f.root,{recursive:true,force:true});rmSync(path.dirname(f.output),{recursive:true,force:true});}
test('release records exact committed source and rejects archive tampering',()=>{const f=fixture();try{const result=prepareRelease({...f,check(){}});assert.equal(validateRelease(f.root,f.output,result.release).commit_sha,result.commit_sha);const entries=execFileSync('tar',['-tf',f.output],{encoding:'utf8',stdio:['ignore','pipe','pipe']}).split('\n');assert.ok(entries.includes('.openai/hosting.json'));assert.ok(entries.includes('dist/server/index.js'));assert.ok(!entries.includes('server/index.js'));writeFileSync(f.output,'tampered');assert.throws(()=>validateRelease(f.root,f.output,result.release),/checksum/);}finally{cleanup(f)}});
test('uncommitted source cannot become a production artifact',()=>{const f=fixture();try{writeFileSync(path.join(f.root,'source.txt'),'uncommitted');assert.throws(()=>prepareRelease({...f,check(){}}),/clean Git/);}finally{cleanup(f)}});
test('a source change during build is rejected',()=>{const f=fixture();try{const build=f.build;assert.throws(()=>prepareRelease({...f,check(){},build(){build();writeFileSync(path.join(f.root,'source.txt'),'changed during build')}}),/clean Git/);}finally{cleanup(f)}});
test('a mismatched built Site ID cannot be packaged',()=>{const f=fixture();try{const build=f.build;assert.throws(()=>prepareRelease({...f,check(){},build(){build();writeFileSync(path.join(f.root,'dist/.openai/hosting.json'),'{"project_id":"different-site"}')}}),/identity/);}finally{cleanup(f)}});
