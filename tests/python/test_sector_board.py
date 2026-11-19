import json
import math
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from review.sector_board import (collect_sector_board, fetch_history, hk_calendar,
                                 log_ema_series, session_window, MIN_HISTORY, fetch_hk_history)

DAY = '2026-11-18'
SPEC = dict(name='测试行业', official_name='测试行业指数', code='TEST', issuer='CSI', group='CN',
            currency='CNY', secid='2.TEST', source_names=['测试行业'], identity_url='https://example.org/index')
PROVENANCE = dict(provider='eastmoney', source_url='https://push2his.eastmoney.com/api/qt/stock/kline/get',
                  fetched_at='2026-11-19T08:00:00+00:00', source_name='测试行业')


def history():
    # Synthetic prices stay inside tests; no publication/archive path.
    return [{'date': (date.fromisoformat(DAY)-timedelta(days=299-i)).isoformat(), 'close': 100+i/10} for i in range(300)]


def calendar(stamp):
    return dict(date=stamp, open=True, source='test', source_url='https://example.org/calendar', fetched_at='2026-11-19T08:00:00+00:00')


class SectorBoardTests(unittest.TestCase):
    def test_log_ema_formula_and_warmup_not_log_of_price_ema(self):
        rows=history();rows[-1]['close']=300
        result=log_ema_series(rows)
        ema=math.log(rows[0]['close']);price_ema=rows[0]['close']
        for r in rows[1:]:
            ema=ema+(2/21)*(math.log(r['close'])-ema)
            price_ema=price_ema+(2/21)*(r['close']-price_ema)
        self.assertAlmostEqual(result[-1]['value'],100*(math.log(300)-ema),places=10)
        self.assertGreater(abs(result[-1]['value']-100*math.log(300/price_ema)),1)
        self.assertIsNone(result[MIN_HISTORY-2]['value'])
        self.assertIsNotNone(result[MIN_HISTORY-1]['value'])
        scaled=log_ema_series([dict(r,close=r['close']/10000) for r in rows])
        self.assertAlmostEqual(result[-1]['value'],scaled[-1]['value'],places=10)

    def test_constant_prices_are_real_zero_not_missing(self):
        rows=[dict(r,close=1) for r in history()]
        self.assertEqual(log_ema_series(rows)[-1]['value'],0)
        for value in [0,-1,True,float('nan'),float('inf')]:
            bad=rows.copy();bad[-1]=dict(rows[-1],close=value)
            with self.assertRaises(ValueError):log_ema_series(bad)
        with self.assertRaises(ValueError):log_ema_series(rows+[rows[-1]])

    def test_official_hk_calendar_is_distinct_and_half_days_are_open(self):
        self.assertFalse(hk_calendar('2026-10-19')['open'])
        self.assertTrue(hk_calendar('2026-02-16')['open'])
        self.assertFalse(hk_calendar('2026-02-17')['open'])
        self.assertTrue(hk_calendar('2026-12-24')['open'])
        with self.assertRaises(ValueError):hk_calendar('2030-01-02')
        dates,_=session_window('2026-10-19',hk_calendar)
        self.assertEqual(dates[0],'2026-10-16')
        self.assertEqual(dates,sorted(dates,reverse=True))

    def test_collects_ten_descending_dates_and_isolates_failed_series(self):
        other=dict(SPEC,secid='2.OTHER',code='OTHER')
        adapter=SimpleNamespace(settings={'sector_board':[SPEC,other]},calendar=calendar)
        with patch('review.sector_board.fetch_history',side_effect=[(history(),PROVENANCE),ValueError('stale')]):
            board=collect_sector_board(adapter,DAY)
        group=board['groups'][0]
        self.assertEqual(board['status'],'partial')
        self.assertEqual((group['sample_size'],group['configured_count']),(1,2))
        self.assertEqual(group['dates'],sorted(group['dates'],reverse=True))
        self.assertEqual(group['dates'][0],DAY)
        self.assertEqual([v['date'] for v in group['rows'][0]['values']],group['dates'])
        self.assertIsNone(group['rows'][1]['close'])
        self.assertEqual(group['rows'][1]['values'],[])
        self.assertIn('stale',group['rows'][1]['reason'])

    def test_missing_session_stale_tail_and_short_warmup_are_not_filled(self):
        adapter=SimpleNamespace(settings={'sector_board':[SPEC]},calendar=calendar)
        h=history()
        for rows in [h[:-1],h[:-5]+h[-4:],h[-100:]]:
            with patch('review.sector_board.fetch_history',return_value=(rows,PROVENANCE)):
                board=collect_sector_board(adapter,DAY)
            self.assertEqual(board['status'],'unavailable')
            self.assertEqual(board['groups'][0]['rows'][0]['values'],[])

    def test_index_identity_strict_dates_and_close_validation(self):
        data={'code':'TEST','market':2,'name':'测试行业','klines':['2026-11-18,1,100,1,1,0,0,0']}
        def get(d):
            with patch('review.sector_board._read_json',return_value=({'rc':0,'data':d},PROVENANCE['source_url'],PROVENANCE['fetched_at'])):
                return fetch_history(SPEC,'2026-11-01',DAY)
        self.assertEqual(get(data)[0][0]['close'],100)
        for key,value in [('code','OTHER'),('market',0),('name','同名ETF'),('klines',[]),('klines',data['klines']*2),('klines',['2026-11-19,1,100,1,1,0,0,0']),('klines',['2026-11-18,1,NaN,1,1,0,0,0'])]:
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):get(dict(data,**{key:value}))

    def test_hk_official_history_filters_holidays_and_rejects_warmup_gaps(self):
        spec=dict(SPEC,group='HK',currency='HKD')
        raw=[dict(tradeDate=d,indexCode='TEST',indexNameCnAll='测试行业指数',close=c)
             for d,c in [('20261001',100),('20261002',102),('20261005',103)]]
        def get(rows):
            with patch('review.sector_board._read_json',return_value=({'code':'200','data':rows},'https://www.csindex.com.cn/csindex-home/perf/index-perf','2026-11-19T08:00:00Z')):
                return fetch_hk_history(spec,'2026-10-01','2026-10-05',['2026-10-02','2026-10-05'])
        history,source=get(raw)
        self.assertEqual([r['date'] for r in history],['2026-10-02','2026-10-05'])
        self.assertEqual(source['provider'],'csindex')
        with self.assertRaisesRegex(ValueError,'缺口'):get(raw[:2])
        wrong=[dict(r,indexCode='WRONG') for r in raw]
        with self.assertRaisesRegex(ValueError,'身份'):get(wrong)

    def test_cni_history_uses_close_and_explicit_index_identity(self):
        spec=dict(SPEC,group='HK',currency='HKD',issuer='CNI')
        data={'indexCode':'TEST','indexName':'测试行业','item':['timestamp','current','close'],
              'data':[['2026-10-05',999,103],['2026-10-02',999,102]]}
        with patch('review.sector_board._read_json',return_value=({'code':200,'data':data},'https://hq.cnindex.com.cn/market/market/getIndexDailyDataWithDataFormat','2026-11-19T08:00:00Z')):
            rows,source=fetch_hk_history(spec,'2026-10-01','2026-10-05',['2026-10-02','2026-10-05'])
        self.assertEqual([r['close'] for r in rows],[102,103])
        self.assertEqual(source['provider'],'cnindex')

    def test_reference_universe_has_distinct_identities_and_supplements(self):
        config=json.loads((Path(__file__).parents[2]/'config/review.json').read_text())['primary']['sector_board']
        self.assertEqual(len(config),44)
        self.assertEqual(sum(not x['supplemental'] for x in config),39)
        self.assertEqual(sum(x['group']=='HK' for x in config),9)
        self.assertEqual(len({x['secid'] for x in config}),44)
        by_name={x['name']:x for x in config}
        self.assertEqual(by_name['中证半导']['code'],'931865')
        self.assertEqual(by_name['半导体']['code'],'H30184')
        self.assertEqual(by_name['人工智能']['code'],'931071')
        self.assertEqual(by_name['机器人产业']['code'],'980022')
        self.assertEqual(by_name['港股通消费（国证）']['code'],'987024')
        self.assertFalse(by_name['港股通创新药（国证）']['supplemental'])
        self.assertTrue(by_name['港股通创新药（中证）']['supplemental'])
        self.assertTrue(all(x['currency']=='HKD' for x in config if x['group']=='HK'))

if __name__=='__main__':unittest.main()
