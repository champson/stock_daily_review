import test from 'node:test';
import assert from 'node:assert/strict';
import {createHmac} from 'node:crypto';
import {authorizeReportWrite} from '../../app/lib/write-auth.ts';

// Offline fixture only. This is not a platform credential or a report write.
const serviceToken='placeholder-service-token';
const derivedToken=createHmac('sha256',serviceToken).update('stock-review:write:v1','utf8').digest('hex');
const vector='f5951bd3ce60150cbf248ccda7d3b43a2acd3a97904eade3d0cfcbdb485e6fa2';
function request(token) {
  return new Request('https://stock-review.example/api/reports',{
    method:'POST',body:'body must remain unread',
    headers:token===undefined?{}:{'X-Review-Write-Token':token},
  });
}

test('missing or malformed server configuration fails closed before body consumption',async()=>{
  for(const configured of [undefined,null,'','not-a-token']) {
    const incoming=request(derivedToken);
    const denied=authorizeReportWrite(incoming,configured);
    assert.equal(denied.status,503);
    assert.equal(incoming.bodyUsed,false);
    assert.equal((await denied.json()).error,'报告写入授权尚未配置');
  }
});

test('missing, wrong, and incorrectly encoded credentials are rejected without reading body',async()=>{
  for(const supplied of [undefined,'a'.repeat(64),serviceToken,derivedToken.toUpperCase(),derivedToken.slice(1)]) {
    const incoming=request(supplied);
    const denied=authorizeReportWrite(incoming,derivedToken);
    assert.equal(denied.status,401);
    assert.equal(incoming.bodyUsed,false);
    assert.equal((await denied.json()).error,'报告写入未授权');
  }
});

test('only the expected derived token authorizes the write',()=>{
  assert.equal(derivedToken,vector);
  const incoming=request(derivedToken);
  assert.equal(authorizeReportWrite(incoming,derivedToken),null);
  assert.equal(incoming.bodyUsed,false);
});
