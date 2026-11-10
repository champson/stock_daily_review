import test from 'node:test';
import assert from 'node:assert/strict';
import {buildMarketMatrix} from '../../app/lib/market-matrix.ts';

const day='2026-11-10';
function quote(symbol,close=100,previous_close=100,extra={}) {
  return {symbol,date:day,close,previous_close,...extra};
}
function report(stocks=[],indices=[],convertibles) {
  return {date:day,sections:{stocks:{rows:stocks,universe_complete:false},indices:{rows:indices}},
    ...(convertibles?{market_data:{convertibles:{rows:convertibles,mapping_filter_date:day}}}: {})};
}
function calendarBasis(date=day,bias25_dates=[]) {
  return {date,anchors:date===day?{year:'2025-12-31',month:'2026-10-30',week:'2026-11-06'}:
    {year:'2025-12-31',month:'2025-12-31',week:'2025-12-31'},bias25_dates,observations:[],provenance:[]};
}
function withCalendar(r,basis=calendarBasis()) {
  r.market_data={...r.market_data,calendar_basis:basis};return r;
}
function get(matrix,key){const row=matrix.rows.find(r=>r.key===key);assert.ok(row,key);return row;}
function near(actual,expected){assert.ok(Math.abs(actual-expected)<1e-8,actual+' ≠ '+expected);}

test('six bins partition the same sample, retaining zero and exact boundaries',()=>{
  const changes=[11,10,5,0,-5,-10,-11], stocks=changes.map((pct,i)=>quote('s'+i,100+pct));
  const input=report(stocks),before=JSON.stringify(input);
  const matrix=buildMarketMatrix(input);
  assert.equal(get(matrix,'stock_total').values.day,7);
  assert.equal(get(matrix,'stock_up').values.day,3);
  assert.equal(get(matrix,'stock_down').values.day,3);
  assert.equal(get(matrix,'stock_flat').values.day,1);
  const expected=[1,1,1,1,1,2];
  const bins=['bin_gt10','bin_5_10','bin_0_5','bin_m5_0','bin_m10_m5','bin_le_m10'];
  bins.forEach((key,i)=>near(get(matrix,key).values.day,expected[i]/7*100));
  near(bins.reduce((sum,key)=>sum+get(matrix,key).values.day,0),100);
  near(get(matrix,'stock_up_ratio').values.day,3/7*100);
  near(get(matrix,'stock_mean').values.day,0);
  assert.equal(get(matrix,'stock_median').values.day,0);
  assert.equal(JSON.stringify(input),before);
  assert.match(get(matrix,'stock_total').label,/有效样本/);
  assert.equal(matrix.rows.some(row=>/我的实盘/.test(row.label)),false);
});

test('raw closes preserve a small return even when legacy change_pct was rounded to zero',()=>{
  const matrix=buildMarketMatrix(report([quote('tiny',100.000001,100,{change_pct:0})]));
  assert.equal(get(matrix,'stock_up').values.day,1);
  assert.equal(get(matrix,'stock_flat').values.day,0);
  assert.ok(get(matrix,'stock_mean').values.day>0);
});

test('last-bit division errors do not move an exact threshold to the neighboring bin',()=>{
  const matrix=buildMarketMatrix(report([quote('ten',9.9,9),quote('five',1.05,1),quote('minusFive',0.95,1)]));
  near(get(matrix,'bin_5_10').values.day,100/3);
  near(get(matrix,'bin_0_5').values.day,100/3);
  near(get(matrix,'bin_m10_m5').values.day,100/3);
  assert.equal(get(matrix,'bin_gt10').values.day,0);
  assert.equal(get(matrix,'bin_m5_0').values.day,0);
});

test('legacy reports return available daily values and explicit null history, never copied day returns',()=>{
  const matrix=buildMarketMatrix(report([quote('s',110)]));
  const row=get(matrix,'stock_mean');
  assert.equal(row.values.day,10);
  assert.deepEqual([row.values.year,row.values.month,row.values.week],[null,null,null]);
  assert.match(row.reasons.year,/复权/);
  assert.equal(get(matrix,'bond_mean').values.day,null);
  assert.equal(get(matrix,'special_microcap').values.day,null);
  assert.match(get(matrix,'special_microcap').reasons.day,/待核验/);
  assert.equal(buildMarketMatrix(null),null);
  assert.equal(buildMarketMatrix({date:'2026-02-30'}),null);
});

test('cross-period stock samples require explicitly adjusted history and a pre-period anchor',()=>{
  const history=[{date:'2025-12-31',close:80},{date:'2026-10-30',close:90},
    {date:'2026-11-06',close:100},{date:day,close:110}];
  const adjusted=quote('adjusted',110,100,{history,history_adjustment:'qfq'});
  const raw=quote('raw',110,100,{history,price_basis:'不复权'});
  const ipo=quote('ipo',110,100,{history:[{date:day,close:110}],history_adjustment:'qfq'});
  const matrix=buildMarketMatrix(withCalendar(report([adjusted,raw,ipo])));
  const row=get(matrix,'stock_mean');
  near(row.values.year,37.5);near(row.values.month,110/90*100-100);near(row.values.week,10);
  assert.deepEqual(row.coverage,{year:1,month:1,week:1,day:3});
  assert.match(row.reasons.year,/有效样本 1 \/ 3/);
});

test('holiday period anchors use last close before calendar start, not fixed trading-day offsets',()=>{
  const date='2026-01-05';
  const index={symbol:'sh000001',date,close:120,previous_close:100,history:[
    {date:'2025-12-30',close:90},{date:'2025-12-31',close:100},{date,close:120},
  ]};
  const matrix=buildMarketMatrix(withCalendar({date,sections:{indices:{rows:[index]}}},calendarBasis(date)));
  const row=get(matrix,'index_sh000001');
  for (const key of ['year','month','week','day']) near(row.values[key],20);
  assert.equal(row.bias25,null);
  assert.match(row.reasons.bias25,/缺少经交易日历核验/);
});

test('incorrect historical dates, repeated dates, stale endpoints and mismatched closes cannot create returns',()=>{
  const valid=[{date:'2026-11-06',close:100},{date:day,close:110}];
  for(const history of [
    [...valid].reverse(),[valid[0],valid[0],valid[1]],
    [{date:'2026-02-30',close:100},valid[1]],
    [{date:'2026-11-06',close:100}],
    [valid[0],{date:day,close:111}],
    [valid[0],{date:day,close:0}],
  ]) {
    const matrix=buildMarketMatrix(withCalendar(report([], [quote('sh000001',110,100,{history})])));
    const row=get(matrix,'index_sh000001');
    assert.equal(row.values.week,null);assert.ok(row.reasons.week);assert.equal(row.bias25,null);
    assert.equal(row.values.day,10);
  }
});

test('a close within20 days of the period start cannot replace the exact calendar anchor',()=>{
  const matrix=buildMarketMatrix(withCalendar(report([], [quote('sh000001',110,100,{history:[
    {date:'2026-10-20',close:80},{date:day,close:110},
  ]})])));
  const row=get(matrix,'index_sh000001');
  assert.equal(row.values.month,null);assert.equal(row.values.week,null);
  assert.match(row.reasons.month,/2026-10-30/);assert.match(row.reasons.week,/2026-11-06/);
  assert.equal(row.values.day,10);
});

test('BIAS25 includes exactly the latest25 closes and current day',()=>{
  const history=Array.from({length:26},(_,i)=>{
    const date=new Date(Date.parse(day)-(25-i)*86400000).toISOString().slice(0,10);
    return {date,close:i===0?9999:i+100};
  });
  const basis=calendarBasis(day,history.slice(-25).map(h=>h.date));
  const matrix=buildMarketMatrix(withCalendar(report([], [quote('sh000001',125,124,{history})]),basis));
  const row=get(matrix,'index_sh000001');
  near(row.bias25,(125/113-1)*100);
  assert.equal(row.reasons.bias25,null);
});

test('legacy histories cannot produce cross-period returns or BIAS without a calendar basis',()=>{
  const history=Array.from({length:30},(_,i)=>({date:new Date(Date.parse(day)-(29-i)*86400000).toISOString().slice(0,10),close:100}));
  const matrix=buildMarketMatrix(report([], [quote('sh000001',100,100,{history})]));
  const row=get(matrix,'index_sh000001');
  assert.equal(row.values.month,null);assert.match(row.reasons.month,/缺少经交易日历核验的周期锚点/);
  assert.equal(row.bias25,null);assert.match(row.reasons.bias25,/缺少经交易日历核验的最近25/);
  assert.equal(row.values.day,0);
});

test('BIAS refuses a missing required trading day even with more than25 other historical closes',()=>{
  const history=Array.from({length:30},(_,i)=>({date:new Date(Date.parse(day)-(29-i)*86400000).toISOString().slice(0,10),close:100+i}));
  const basis=calendarBasis(day,history.slice(-25).map(h=>h.date));
  const missingDate=basis.bias25_dates[7];
  const quoteWithGap=quote('sh000001',129,128,{history:history.filter(h=>h.date!==missingDate)});
  const matrix=buildMarketMatrix(withCalendar(report([], [quoteWithGap]),basis));
  assert.equal(get(matrix,'index_sh000001').bias25,null);
  assert.ok(get(matrix,'index_sh000001').reasons.bias25.includes(missingDate));
  for(const dates of [basis.bias25_dates.slice(1),[...basis.bias25_dates].reverse(),[...basis.bias25_dates.slice(0,24),basis.bias25_dates[23]]]) {
    const invalid=buildMarketMatrix(withCalendar(report([], [quoteWithGap]),{...basis,bias25_dates:dates}));
    assert.equal(get(invalid,'index_sh000001').bias25,null);
  }
});

test('duplicate, stale and invalid current quotes are removed; no-return sample is null rather than zero',()=>{
  const stocks=[quote('dup',101),quote('dup',102),quote('stale',103,100,{date:'2026-11-09'}),
    quote('bad',NaN),quote('negative',-1),quote('zero',0),quote('noprev',100,null),quote('valid',100)];
  const matrix=buildMarketMatrix(report(stocks));
  assert.equal(get(matrix,'stock_total').values.day,1);
  assert.equal(get(matrix,'stock_flat').values.day,1);
  assert.match(get(matrix,'stock_mean').reasons.day,/1 \/ 2/);
  const none=buildMarketMatrix(report([quote('noprev',100,null)]));
  assert.equal(get(none,'stock_total').values.day,null);
  assert.equal(get(none,'stock_mean').coverage.day,0);
});

test('convertibles use dated eligible quotes and deduplicate linked underlying stocks',()=>{
  const bonds=[
    quote('b1',110,100,{underlying_symbol:'s1'}),
    quote('b2',120,100,{underlying_symbol:'s1'}),
    quote('b3',90,100,{underlying_symbol:'s2'}),
    quote('future',100,100,{underlying_symbol:'s3',listing_date:'2026-11-11'}),
    quote('delisted',100,100,{underlying_symbol:'s3',delist_date:day}),
    quote('upcoming',100,100,{underlying_symbol:'s3',status:'upcoming'}),
  ];
  const matrix=buildMarketMatrix(report([quote('s1',110),quote('s2',90),quote('s3',120)],[],bonds));
  near(get(matrix,'bond_mean').values.day,20/3);
  assert.equal(get(matrix,'bond_median').values.day,10);
  assert.equal(get(matrix,'underlying_mean').values.day,0);
  assert.equal(get(matrix,'underlying_mean').coverage.day,2);
  assert.equal(get(matrix,'underlying_median').values.day,0);
  assert.equal(get(matrix,'bond_mean').values.year,null);
  const stale=report([quote('s1',110)],[],bonds);stale.market_data.convertibles.mapping_filter_date='2026-11-09';
  assert.equal(get(buildMarketMatrix(stale),'bond_mean').values.day,null);
});
