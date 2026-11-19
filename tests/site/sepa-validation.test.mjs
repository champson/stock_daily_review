import test from 'node:test';
import assert from 'node:assert/strict';
import {validateSepa, categories} from '../../app/lib/sepa-validation.mjs';

const date='2026-11-18';
function fixture() {
  return {schema_version:1,date,generated_at:date+'T18:00:00+08:00',status:'available',candidate_count:1,
    candidate_source:{provider:'daxiapi',data_date:date,fetched_at:date+'T17:30:00+08:00',pool_complete:false},
    market_rs:{date,status:'available',coverage:{valid_count:1000,eligible_count:1000,ratio:1}},
    bar_fields:['date','open','high','low','close','volume','ma50','ma150','ma200'],observations:[],
    groups:Object.fromEntries(categories.map(k=>[k,k==='A'?1:0])),
    rows:[{symbol:'sz002100',name:'fixture',category:'A',close:11,rs_percentile:80,
      quote:{source:'tencent',adjustment:'qfq',volume_unit:'shares'},
      trend:{date,status:'pass',checks:Array.from({length:8},()=>({label:'fixture check',status:'pass'}))},
      vcp:{as_of:date,state:'ready',swings:[]},bars:[[date,10,12,9,11,10000,10,9,8]]}]};
}
test('accepts compact dated analysis, rejects stale sources, broken units and inconsistent groups',()=>{
  validateSepa(fixture(),date);
  for(const mutate of [
    r=>r.date='2026-11-17',r=>r.candidate_source.data_date='2026-11-17',
    r=>r.candidate_source.pool_complete=true,r=>r.market_rs.coverage.valid_count=900,
    r=>r.rows[0].bars[0][0]='2026-11-17',r=>r.rows[0].bars[0][4]=12,
    r=>r.rows[0].quote.volume_unit='lots',r=>r.rows[0].bars[0][5]=-1,
    r=>r.rows[0].rs_percentile=101,r=>r.groups.A=2,r=>r.rows[0].trend.status='fail',
    r=>r.rows.push(structuredClone(r.rows[0])),r=>r.rows[0].bars[0][6]=NaN,
  ]) {const r=fixture();mutate(r);assert.throws(()=>validateSepa(r,date));}
});
test('unavailable SEPA is distinguishable from an authenticated empty pool',()=>{
  const r=fixture();r.status='unavailable';r.candidate_count=null;r.rows=[];
  r.groups=Object.fromEntries(categories.map(k=>[k,0]));r.observations=['source delayed'];
  validateSepa(r,date);
  r.candidate_count=0;assert.throws(()=>validateSepa(r,date));
  const empty=fixture();empty.rows=[];empty.candidate_count=0;empty.groups=r.groups;empty.market_rs=null;
  validateSepa(empty,date);
});
