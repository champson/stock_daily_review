"""Live integration evidence for public market feeds; never commits market data.

No Site service token, write token or provider account is accepted or required.
HTTP clients inherit the execution environment proxy and keep TLS verification.
"""
import argparse
import importlib.util
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from review.a_stock_data import AStockData, records
from review.vendor import astock


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    day = date.fromisoformat(args.date).isoformat()
    args.output.mkdir(parents=True, exist_ok=True)
    config = json.loads(Path('config/review.json').read_text())
    adapter = AStockData(config['primary'])
    evidence = {'date': day, 'checks': {}, 'errors': {}}

    def save(name, value):
        (args.output / name).write_text(json.dumps(value, ensure_ascii=False, allow_nan=False))

    def check(name, callback):
        try:
            result = callback()
            evidence['checks'][name] = result
            print(json.dumps({'check': name, 'ok': True, 'summary': result}, ensure_ascii=False), flush=True)
        except Exception as exc:
            # Only exception types are logged: provider response text is untrusted
            # and no arbitrary request or credential is included in evidence.
            evidence['errors'][name] = type(exc).__name__
            print(json.dumps({'check': name, 'ok': False, 'error_type': type(exc).__name__}), flush=True)
        save('evidence.json', evidence)
        time.sleep(1)

    def calendar():
        result = adapter.calendar_basis(day)
        save('calendar-basis.json', result)
        if any(value is None for value in result['anchors'].values()):
            raise ValueError('Incomplete official period anchors')
        return {'anchors': result['anchors'], 'bias25_days': len(result['bias25_dates'])}

    check('calendar', calendar)
    anchors = evidence['checks'].get('calendar', {}).get('anchors', {})
    days = sorted(set([day, '2026-11-09', *anchors.values()]))
    for stamp in days:
        def package(stamp=stamp):
            rows = adapter.package(stamp)
            # Preserve all source rows for an independent parser/calculation check.
            save('package-' + stamp + '.json', rows)
            return {'date': stamp, 'rows': len(rows)}
        check('package-' + stamp, package)

    def gbbq():
        url = 'https://www.tdx.com.cn/products/data/data/dbf/gbbq.zip'
        with requests.get(url, timeout=(10, 45), stream=True) as response:
            response.raise_for_status()
            payload = bytearray()
            for chunk in response.iter_content(65536):
                payload.extend(chunk)
                if len(payload) > 20_000_000:
                    raise ValueError('GBBQ archive exceeds size bound')
        (args.output / 'gbbq.zip').write_bytes(payload)
        import hashlib
        return {'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest(), 'source_url': url}

    check('gbbq', gbbq)
    for symbol in ('sh600519', 'sh600036', 'sz000001', 'sh600941'):
        def qfq(symbol=symbol):
            rows = records(astock.tencent_kline(symbol, adjust='qfq', start=day[:4] + '-01-01', end=day, count=320))
            save('qfq-' + symbol + '.json', rows)
            return {'symbol': symbol, 'rows': len(rows), 'last': rows[-1]['date'] if rows else None}
        check('qfq-' + symbol, qfq)

    def bounded_json(url, params=None):
        with requests.get(url, params=params, timeout=(10, 30), stream=True,
                          headers={'User-Agent': 'Mozilla/5.0'}) as response:
            response.raise_for_status()
            payload = bytearray()
            for chunk in response.iter_content(65536):
                payload.extend(chunk)
                if len(payload) > 10_000_000:
                    raise ValueError('Response exceeds size bound')
        return json.loads(payload)

    for symbol, secid in (('sh932000', '2.932000'), ('bj899050', '0.899050')):
        def index(symbol=symbol, secid=secid):
            payload = bounded_json('https://push2his.eastmoney.com/api/qt/stock/kline/get', {
                'secid': secid, 'klt': '101', 'fqt': '0', 'lmt': '640',
                'beg': str(int(day[:4]) - 1) + '1201', 'end': day.replace('-', ''),
                'fields1': 'f1,f2,f3,f4,f5', 'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58',
            })
            save('index-' + symbol + '.json', payload)
            data = payload.get('data') or {}
            return {'symbol': symbol, 'code': data.get('code'), 'rows': len(data.get('klines') or [])}
        check('index-' + symbol, index)

    def jsl():
        payload = bounded_json('https://www.jisilu.cn/webapi/cb/index_history/')
        save('jsl-equal-weight.json', payload)
        rows = payload.get('data')
        if not isinstance(rows, list) or not rows:
            raise ValueError('No dated JSL index data')
        return {'rows': len(rows), 'columns': sorted(rows[0])}
    check('jsl-equal-weight', jsl)

    if importlib.util.find_spec('review.period_data') is not None:
        def report():
            from review.sources import build_report
            result = build_report(config, day, primary=adapter)
            save('report.json', result)
            periods = result.get('market_data', {}).get('period_prices', {})
            return {'status': result.get('status'), 'sections': {
                name: section.get('sample_size', 0) for name, section in result.get('sections', {}).items()},
                'period_stock_rows': len(periods.get('stocks', {}).get('rows', [])),
                'period_bond_rows': len(periods.get('convertibles', {}).get('rows', []))}
        check('generated-report', report)
    save('evidence.json', evidence)
    # Unavailable optional feeds remain explicit evidence; there are no fake values.
    if 'calendar' not in evidence['checks'] or 'gbbq' not in evidence['checks']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
