import test from 'node:test';
import assert from 'node:assert/strict';
import {validateSectorBoard} from '../../app/lib/sector-board-validation.mjs';
function fixture(){
 const end='2026-11-18',history=Array.from({length:300},(_,i)=>({date:new Date(Date.parse(end)-(299-i)*86400000).toISOString().slice(0,10),close:100+i*.1}));
 let ema;const calculated=history.map(h=>{const x=Math.log(h.close);ema=ema===undefined?x:2/21*x+19/21*ema;return {date:h.date,value:100*(x-ema)}});
 const dates=history.slice(-10).map(h=>h.date).reverse(),row={name:'Test',official_name:'Test index',code:'TEST',issuer:'CSI',currency:'CNY',secid:'2.TEST',identity_url:'https://example.org/index',price_basis:'price index',supplemental:false,date:end,status:'available',reason:null,history,values:calculated.slice(-10).reverse(),close:129.9,change_pct:(129.9/129.8-1)*100,provenance:{provider:'eastmoney',source_url:'https://push2his.eastmoney.com/api/qt/stock/kline/get',source_name:'Test',fetched_at:'2026-11-19T08:00:00Z'}};
 return {version:1,date:end,status:'available',method:'ln-close-minus-ema20-ln-close-v1',formula_url:'https://xueqiu.com/3559889031/354857867',period:20,minimum_history:250,observations:[],provenance:[],groups:[{key:'CN',name:'China',dates,previous_date:dates[1],rows:[row],sample_size:1,configured_count:1,status:'available',observations:[]}]};
}
test('validates seeded log EMA independently with newest date first',()=>{const b=fixture();validateSectorBoard(b,b.date)});
test('rejects swapped dates, log-price EMA alternatives, stale data and forged counts',()=>{
 for(const mutate of [b=>b.groups[0].dates.reverse(),b=>b.groups[0].rows[0].values.reverse(),b=>b.groups[0].rows[0].values[0].value+=.01,b=>b.groups[0].rows[0].history.pop(),b=>b.groups[0].rows[0].history[0].close=0,b=>b.groups[0].rows[0].close=100,b=>b.groups[0].rows[0].change_pct=0,b=>b.groups[0].rows[0].currency='HKD',b=>b.groups[0].sample_size=0,b=>b.groups[0].rows.push(b.groups[0].rows[0]),b=>b.method='log-of-ema',b=>b.minimum_history=20,b=>b.groups[0].rows[0].date='2026-11-17']){
  const b=fixture();mutate(b);assert.throws(()=>validateSectorBoard(b,b.date),/板块/);
 }
 const b=fixture();assert.throws(()=>validateSectorBoard(b,'2026-11-19'),/板块/);
});
test('unavailable rows retain reasons and nulls rather than fabricated zero values',()=>{
 const b=fixture(),g=b.groups[0],r=g.rows[0];b.status=g.status=r.status='unavailable';g.sample_size=0;
 Object.assign(r,{close:null,change_pct:null,history:[],values:[],reason:'source unavailable',provenance:null});
 validateSectorBoard(b,b.date);r.close=0;assert.throws(()=>validateSectorBoard(b,b.date),/板块/);
});

test('HK history must cover every independently recorded market session, including warmup',()=>{
 const b=fixture(),g=b.groups[0],r=g.rows[0];g.key='HK';r.currency='HKD';g.history_dates=r.history.map(h=>h.date);
 validateSectorBoard(b,b.date);
 const original=r.history[5];r.history.splice(5,1);
 assert.throws(()=>validateSectorBoard(b,b.date),/板块/);
 r.history.splice(5,0,original);g.history_dates.reverse();assert.throws(()=>validateSectorBoard(b,b.date),/板块/);
});
