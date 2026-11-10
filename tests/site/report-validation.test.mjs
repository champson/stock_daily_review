import test from 'node:test';
import assert from 'node:assert/strict';
import {kinds, validDate, validateReport} from '../../app/lib/report-validation.ts';

// 仅在测试内使用，绝不发布到报告存档。
function fixture() {
  const sections=Object.fromEntries(kinds.map(kind=>[kind,{status:'unavailable',rows:[],sample_size:0,metrics:{},observations:[]} ]));
  sections.indices={status:'available',rows:[{symbol:'fixture',date:'2026-10-13',close:1}],sample_size:1,metrics:{},observations:[]};
  return {schema_version:1,date:'2026-10-13',generated_at:'2026-10-13T18:00:00+08:00',source:'通达信 MCP',timezone:'Asia/Shanghai',status:'partial',sections};
}
test('accepts a dated partial report and rejects impossible dates',()=>{
  validateReport(fixture());
  assert.equal(validDate('2026-02-30'),false);
});
test('rejects an empty report even when marked partial',()=>{
  const r=fixture();r.sections.indices={status:'unavailable',rows:[],sample_size:0,metrics:{},observations:[]};
  assert.throws(()=>validateReport(r),/没有有效行情/);
});
test('rejects stale, duplicate and non-finite rows',()=>{
  const stale=fixture();stale.sections.indices.rows[0].date='2026-10-12';assert.throws(()=>validateReport(stale));
  const duplicate=fixture();duplicate.sections.indices.rows.push({...duplicate.sections.indices.rows[0]});duplicate.sections.indices.sample_size=2;assert.throws(()=>validateReport(duplicate));
  const nonfinite=fixture();nonfinite.sections.indices.rows[0].close=NaN;assert.throws(()=>validateReport(nonfinite));
});
test('rejects a report marked unavailable',()=>{
  const r=fixture();r.status='unavailable';assert.throws(()=>validateReport(r));
});
