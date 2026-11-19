import test from 'node:test';
import assert from 'node:assert/strict';
import {buildSepaFocus} from '../../app/lib/sepa-focus.mjs';

// Includes a weekend and a deliberately closed weekday in the last window.
const dates = ['2026-10-14','2026-10-15','2026-10-16','2026-10-19','2026-10-20',
  '2026-10-21','2026-10-22','2026-10-23','2026-10-26','2026-10-27',
  '2026-10-28','2026-10-29','2026-10-30','2026-11-02','2026-11-03',
  '2026-11-04','2026-11-05','2026-11-06','2026-11-09','2026-11-10',
  '2026-11-11','2026-11-12','2026-11-13','2026-11-17','2026-11-18'];
const stock = (symbol, date, changes = {}) => ({symbol, category:'B', trend:{status:'pass'},
  vcp:{state:'breakout', metrics:{setup_active:true}, breakout:{date,volume_confirmed:true}}, ...changes});
const daily = rows => ({date:'2026-11-18',sepa:{date:'2026-11-18',rows},
  market_data:{calendar_basis:{date:'2026-11-18',bias25_dates:dates}}});

test('recent window includes the report day and the previous four official sessions', () => {
  const r=buildSepaFocus(daily([stock('boundary','2026-11-11'),stock('old','2026-11-10'),
    stock('today','2026-11-18'),stock('closed','2026-11-16'),stock('future','2026-11-19')]));
  assert.deepEqual(r.window_dates,dates.slice(-5));
  assert.equal(r.rows.boundary.state,'recent');
  assert.equal(r.rows.boundary.age_sessions,4);
  assert.equal(r.rows.today.age_sessions,0);
  assert.equal(r.rows.old.state,'older');
  assert.equal(r.rows.closed.state,'unknown');
  assert.equal(r.rows.future.state,'unknown');
  assert.equal(r.counts.focus,2);
});

test('only active qualified setups enter focus; an old breakout with a fresh retest stays old', () => {
  const ready=stock('ready',null,{category:'A',vcp:{state:'ready',metrics:{setup_active:true}}});
  const old=stock('retest','2026-10-30');old.vcp.state='retest';
  const failed=stock('failed','2026-11-18');failed.vcp.state='failed';
  const unconfirmed=stock('unconfirmed','2026-11-18');unconfirmed.vcp.breakout.volume_confirmed=false;
  const inactive=stock('inactive','2026-11-18');inactive.vcp.metrics.setup_active=false;
  const weak=stock('weak','2026-11-18',{category:'C'});
  const r=buildSepaFocus(daily([ready,old,failed,unconfirmed,inactive,weak]));
  assert.equal(r.rows.ready.state,'ready');
  assert.equal(r.rows.retest.state,'older');
  assert.equal(r.counts.focus,1);
  for (const symbol of ['failed','unconfirmed','inactive','weak']) assert.equal(r.rows[symbol].state,'other');
});

test('missing, mismatched or invalid calendars never guess freshness from candle counts', () => {
  for (const change of [d=>delete d.market_data,d=>d.market_data.calendar_basis.date='2026-11-17',
    d=>d.market_data.calendar_basis.bias25_dates=[],d=>d.market_data.calendar_basis.bias25_dates=[...dates].reverse()]) {
    const d=daily([stock('recent','2026-11-17')]);change(d);
    const r=buildSepaFocus(d);
    assert.equal(r.calendar_status,'unavailable');assert.equal(r.counts.focus,0);
    assert.equal(r.rows.recent.state,'unknown');
  }
  assert.equal(buildSepaFocus(null).counts.focus,0);
});
