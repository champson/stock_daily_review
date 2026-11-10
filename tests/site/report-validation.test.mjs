import test from 'node:test';
import assert from 'node:assert/strict';
import {kinds, validDate, validateReport} from '../../app/lib/report-validation.ts';

// 仅在测试内使用，绝不发布到报告存档。
function fixture() {
  const sections=Object.fromEntries(kinds.map(kind=>[kind,{status:'unavailable',rows:[],sample_size:0,metrics:{},observations:[]} ]));
  sections.indices={status:'available',rows:[{symbol:'fixture',date:'2026-10-13',close:1}],sample_size:1,metrics:{},observations:[]};
  return {schema_version:1,date:'2026-10-13',generated_at:'2026-10-13T18:00:00+08:00',source:'通达信 MCP',timezone:'Asia/Shanghai',status:'partial',sections};
}

function sectorFixture() {
  const r=fixture();
  r.market_data={sector_indices:{date:r.date,status:'available',configured_count:1,sample_size:1,
    previous_trading_date:'2026-10-12',observations:[],provenance:[],rows:[{
      symbol:'sh000986',name:'中证全指能源指数',date:r.date,previous_date:'2026-10-12',
      close:110,previous_close:100,change_pct:10,
    }]}};
  return r;
}
test('accepts dated industry indices and unavailable supplementary data without changing legacy reports',()=>{
  validateReport(sectorFixture());
  const empty=sectorFixture().market_data.sector_indices;empty.rows=[];empty.sample_size=0;empty.status='unavailable';empty.previous_trading_date=null;
  validateReport({...fixture(),market_data:{sector_indices:empty}});
  validateReport(fixture());
});
test('rejects stale sector quotes, incorrect return anchors, duplicates and inconsistent calculated returns',()=>{
  for(const mutate of [
    s=>{s.date='2026-10-12'},
    s=>{s.rows[0].date='2026-10-12'},
    s=>{s.rows[0].previous_date='2026-10-09'},
    s=>{s.previous_trading_date='2026-09-01';s.rows[0].previous_date='2026-09-01'},
    s=>{s.rows[0].change_pct=11},
    s=>{s.rows[0].previous_close=0},
    s=>{s.rows.push({...s.rows[0]});s.sample_size=2;s.configured_count=2},
    s=>{s.configured_count=0},
  ]) {
    const r=sectorFixture();mutate(r.market_data.sector_indices);assert.throws(()=>validateReport(r));
  }
});
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
test('validates optional convertible quotes and mapping dates without requiring extras in legacy reports',()=>{
  const r=fixture();
  r.market_data={convertibles:{status:'partial',sample_size:1,mapping_filter_date:r.date,observations:[],rows:[{symbol:'sz123001',underlying_symbol:'sz300001',date:r.date,close:110,previous_close:100}]}};
  validateReport(r);
  const stale=structuredClone(r);stale.market_data.convertibles.mapping_filter_date='2026-10-12';assert.throws(()=>validateReport(stale),/映射日期/);
  const duplicate=structuredClone(r);duplicate.market_data.convertibles.rows.push({...duplicate.market_data.convertibles.rows[0]});duplicate.market_data.convertibles.sample_size=2;assert.throws(()=>validateReport(duplicate),/可转债行情/);
  const future=structuredClone(r);future.market_data.convertibles.rows[0].listing_date='2026-10-14';assert.throws(()=>validateReport(future),/上市范围/);
  const noPrice=structuredClone(r);noPrice.market_data.convertibles.rows[0].close=0;assert.throws(()=>validateReport(noPrice),/可转债行情/);
  const unavailable=fixture();unavailable.market_data={convertibles:{status:'unavailable',rows:[],sample_size:0,observations:[]}};validateReport(unavailable);
});
test('validates exact dated calendar anchors and a complete ordered25-day window',()=>{
  const r=fixture(), dates=Array.from({length:25},(_,i)=>new Date(Date.parse(r.date)-(24-i)*86400000).toISOString().slice(0,10));
  r.market_data={calendar_basis:{date:r.date,anchors:{year:'2025-12-31',month:'2026-09-30',week:'2026-10-09'},bias25_dates:dates,observations:[],provenance:[]}};
  validateReport(r);
  const stale=structuredClone(r);stale.market_data.calendar_basis.date='2026-10-12';assert.throws(()=>validateReport(stale),/交易日历/);
  const insidePeriod=structuredClone(r);insidePeriod.market_data.calendar_basis.anchors.month='2026-10-01';assert.throws(()=>validateReport(insidePeriod),/周期锚点/);
  const missingPeriod=structuredClone(r);delete missingPeriod.market_data.calendar_basis.anchors.year;assert.throws(()=>validateReport(missingPeriod),/周期锚点/);
  const duplicate=structuredClone(r);duplicate.market_data.calendar_basis.bias25_dates[10]=dates[9];assert.throws(()=>validateReport(duplicate),/窗口日期/);
  const short=structuredClone(r);short.market_data.calendar_basis.bias25_dates.pop();assert.throws(()=>validateReport(short),/基准格式/);
  const wrongEnd=structuredClone(r);wrongEnd.market_data.calendar_basis.bias25_dates=dates.map(d=>new Date(Date.parse(d)-86400000).toISOString().slice(0,10));assert.throws(()=>validateReport(wrongEnd),/窗口末日/);
  const unavailable=structuredClone(r);unavailable.market_data.calendar_basis.anchors={year:null,month:null,week:null};unavailable.market_data.calendar_basis.bias25_dates=[];validateReport(unavailable);
});

function periodFixture() {
  const r=fixture(),anchors={year:'2025-12-31',month:'2026-09-30',week:'2026-10-09'};
  r.sections.stocks={status:'partial',rows:[{symbol:'s',date:r.date,close:55,previous_close:50}],sample_size:1,metrics:{},observations:[]};
  r.market_data={calendar_basis:{date:r.date,anchors,bias25_dates:[],observations:[],provenance:[]},
    convertibles:{status:'partial',sample_size:1,mapping_filter_date:r.date,observations:[],rows:[{symbol:'b',date:r.date,close:110,previous_close:100,underlying_symbol:'s'}]},
    period_prices:{version:1,date:r.date,method:'tdx-affine-qfq',anchors:{...anchors},observations:[],provenance:[],
      stocks:{status:'partial',sample_size:1,observations:[],rows:[{symbol:'s',close:55,
        year:{date:anchors.year,raw_close:100,adjusted_close:50},month:null,week:{date:anchors.week,raw_close:55,adjusted_close:55}}]},
      convertibles:{status:'available',sample_size:1,observations:[],rows:[{symbol:'b',close:110,
        year:{date:anchors.year,raw_close:100},month:{date:anchors.month,raw_close:105},week:{date:anchors.week,raw_close:110}}]}}};
  return r;
}
test('validates compact adjusted stock and raw bond endpoints without adding daily histories',()=>{
  const r=periodFixture();validateReport(r);assert.equal('history' in r.sections.stocks.rows[0],false);
  const allMissing=periodFixture();allMissing.market_data.period_prices.stocks.status='unavailable';
  for(const period of ['year','month','week'])allMissing.market_data.period_prices.stocks.rows[0][period]=null;
  validateReport(allMissing);
  const unavailable=periodFixture();delete unavailable.market_data.calendar_basis;
  unavailable.market_data.period_prices.anchors={year:null,month:null,week:null};
  for(const kind of ['stocks','convertibles'])unavailable.market_data.period_prices[kind]={status:'unavailable',rows:[],sample_size:0,observations:['source down']};
  validateReport(unavailable);validateReport(fixture());
});
test('rejects stale, unknown-method, mismatched or duplicate compact endpoint data',()=>{
  for(const mutate of [
    p=>{p.date='2026-10-12'},p=>{p.version=2},p=>{p.method='raw-close'},
    p=>{p.anchors.year='2025-12-30'},p=>{p.anchors.month=null},
    p=>{p.stocks.rows[0].close=54},p=>{p.convertibles.rows[0].close=109},
    p=>{p.stocks.rows[0].symbol='unmatched'},
    p=>{p.stocks.rows.push({...p.stocks.rows[0]});p.stocks.sample_size=2},
    p=>{p.convertibles.rows.push({...p.convertibles.rows[0]});p.convertibles.sample_size=2},
    p=>{p.stocks.rows[0].year.date='2026-10-14'},
    p=>{p.stocks.rows[0].year.adjusted_close=0},p=>{p.stocks.rows[0].year.adjusted_close=true},
    p=>{p.convertibles.rows[0].year.raw_close=0},p=>{delete p.stocks.rows[0].month},
    p=>{p.stocks.sample_size=0},p=>{p.stocks.status='unknown'},
  ]) {
    const r=periodFixture();mutate(r.market_data.period_prices);assert.throws(()=>validateReport(r),/区间端点/);
  }
});

function specialFixture() {
  const r=fixture();r.market_data={special_indices:{date:r.date,status:'available',sample_size:1,observations:[],provenance:[],errors:[],rows:[{
    matrix_key:'special_convertible_equal_weight',symbol:'jsl_cb_equal_weight',name:'集思录转债等权',provider:'jisilu',
    date:r.date,close:110,previous_close:100,history:[{date:'2025-12-31',close:90},{date:r.date,close:110}],
  }]}};return r;
}
test('validates explicit special index series and dated source failures without requiring them in old reports',()=>{
  validateReport(specialFixture());
  const failed=fixture();failed.market_data={special_indices:{date:failed.date,status:'unavailable',sample_size:0,rows:[],observations:[],provenance:[],
    errors:[{matrix_key:'special_convertible_equal_weight',message:'source unavailable',category:'source_failed'}]}};
  validateReport(failed);validateReport(fixture());
});
test('rejects unknown, duplicate or stale special index identifiers and invalid history endpoints',()=>{
  for(const mutate of [
    s=>{s.date='2026-10-12'},s=>{s.rows[0].date='2026-10-12'},s=>{s.rows[0].matrix_key='unknown'},
    s=>{s.rows.push({...s.rows[0]});s.sample_size=2},s=>{s.rows[0].provider=''},
    s=>{s.rows[0].previous_close=-1},s=>{s.rows[0].history=[]},
    s=>{s.rows[0].history.reverse()},s=>{s.rows[0].history[0].date='2026-02-30'},
    s=>{s.rows[0].history.at(-1).date='2026-10-14'},s=>{s.rows[0].history.at(-1).close=111},
    s=>{s.errors=[{matrix_key:'unknown',message:'bad',category:'source_failed'}]},
  ]) {
    const r=specialFixture();mutate(r.market_data.special_indices);assert.throws(()=>validateReport(r),/特色指数/);
  }
});
