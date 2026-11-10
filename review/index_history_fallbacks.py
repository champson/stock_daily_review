"""Dated public index history fallbacks; no provider credentials or new dependency.

Protocol references, pinned rather than tracking upstream main:
https://github.com/akfamily/akshare/blob/1aef113ff143df4802a61e33ea2e3933839595e7/akshare/index/index_stock_zh.py
https://github.com/akfamily/akshare/blob/1aef113ff143df4802a61e33ea2e3933839595e7/akshare/bond/bond_convert.py
https://github.com/akfamily/akshare/blob/1aef113ff143df4802a61e33ea2e3933839595e7/docs/data/bond/bond.md

Only source protocols are used here, not the upstream package.  Eastmoney's
index market is explicit: CSI2000 is 2.932000; BSE50 is 0.899050.  An index
provider's market ID must not be inferred from a stock-code prefix.
"""
import http.client
import json
import math
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from urllib.parse import urlencode


MAX_RESPONSE_BYTES = 10 * 1024 * 1024
REQUEST_TIMEOUT = 30
EASTMONEY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
JISILU_URL = "https://www.jisilu.cn/webapi/cb/index_history/"
INDEX_MARKETS = {
    "sh932000": {"code": "932000", "market": 2, "name": "中证2000"},
    "bj899050": {"code": "899050", "market": 0, "name": "北证50"},
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        # These are public APIs, but a login/HTML redirect is not index history.
        return None


def _day(value):
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("历史日期必须为 YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("历史日期无效") from None
    if parsed.isoformat() != value:
        raise ValueError("历史日期必须为 YYYY-MM-DD")
    return value


def _range(start, end):
    start, end = _day(start), _day(end)
    if start > end:
        raise ValueError("历史开始日期晚于结束日期")
    return start, end


def _positive(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("指数收盘价无效")
    try:
        number = float(value)
    except (ValueError, OverflowError):
        raise ValueError("指数收盘价无效") from None
    if not math.isfinite(number) or number <= 0:
        raise ValueError("指数收盘价必须为正有限数")
    return number


def _volume(value):
    # Invalid/missing volume cannot erase a valid dated price.  Zero is valid.
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _read_json(url, params=None):
    target = url + ("?" + urlencode(params) if params else "")
    request = urllib.request.Request(target, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Referer": url,
    })
    try:
        # build_opener retains the inherited HTTP(S) proxy and default TLS trust.
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise RuntimeError(f"指数历史 HTTP {status}")
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise RuntimeError("指数历史响应超过10MiB限制")
        payload = json.loads(body.decode("utf-8-sig"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"指数历史 HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, http.client.HTTPException,
            UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise RuntimeError("指数历史请求或JSON解析失败：" + type(exc).__name__) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("指数历史未返回JSON对象")
    return payload, target, datetime.now(timezone.utc).isoformat()


def _frame(rows, name, source, url, fetched_at, price_basis):
    return {
        "name": name, "rows": [dict(row, source=source, source_url=url,
                                    fetched_at=fetched_at) for row in rows],
        # Eastmoney index volume's unit has not been independently verified.
        # Jisilu's field called volume is turnover in RMB 100m, not volume.
        "volume_unit": "source", "price_basis": price_basis,
    }


def fetch_index_history(symbol, start, end):
    """Return strict unadjusted daily history for CSI2000 or BSE50.

    Empty, stale, duplicate, unordered and out-of-request-range results fail;
    the requested end must be present.  Caller supplies an official trading day.
    """
    start, end = _range(start, end)
    if not isinstance(symbol, str) or symbol not in INDEX_MARKETS:
        raise ValueError("未配置该指数的已核验备用市场代码")
    spec = INDEX_MARKETS[symbol]
    payload, url, fetched_at = _read_json(EASTMONEY_URL, {
        "secid": f"{spec['market']}.{spec['code']}",
        "fields1": "f1,f2,f3,f4,f5",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "klt": "101", "fqt": "0", "beg": start.replace("-", ""),
        "end": end.replace("-", ""),
    })
    if "rc" in payload and (type(payload["rc"]) is not int or payload["rc"] != 0):
        raise RuntimeError("东方财富指数历史返回错误码")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("东方财富指数历史缺少data对象")
    if "code" in data and (isinstance(data["code"], bool) or str(data["code"]) != spec["code"]):
        raise RuntimeError("东方财富返回了不同指数代码")
    for key in ("market", "mkt"):
        if key in data and (isinstance(data[key], bool) or str(data[key]) != str(spec["market"])):
            raise RuntimeError("东方财富返回了不同指数市场")
    if "name" in data and (not isinstance(data["name"], str)
                           or spec["name"] not in "".join(data["name"].split())):
        raise RuntimeError("东方财富返回了不同指数名称")
    raw = data.get("klines")
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("东方财富指数历史为空")
    rows, previous = [], ""
    try:
        for line in raw:
            if not isinstance(line, str):
                raise ValueError("指数K线记录不是字符串")
            fields = line.split(",")
            if len(fields) != 8:
                raise ValueError("指数K线字段数量改变")
            stamp = _day(fields[0])
            if stamp < start or stamp > end or stamp <= previous:
                raise ValueError("指数历史日期越界、乱序或重复")
            previous = stamp
            rows.append({"date": stamp, "close": _positive(fields[2]),
                         "volume": _volume(fields[5])})
    except (ValueError, TypeError, OverflowError) as exc:
        raise RuntimeError("东方财富指数历史校验失败：" + str(exc)) from exc
    if rows[-1]["date"] != end:
        raise RuntimeError("东方财富指数历史未覆盖请求截止日")
    return _frame(rows, spec["name"], "eastmoney", url, fetched_at,
                  "东方财富不复权指数日线")


def fetch_jsl_equal_weight(start, end):
    """Jisilu's named convertible-bond equal-weight index, not stock means.

    The endpoint returns full history.  price_dt/price are date/index points;
    avg_price and mid_price are bond price statistics and must not replace price.
    """
    start, end = _range(start, end)
    payload, url, fetched_at = _read_json(JISILU_URL)
    if "code" in payload and (type(payload["code"]) is not int or payload["code"] != 200):
        raise RuntimeError("集思录转债等权历史返回错误码")
    raw = payload.get("data")
    if isinstance(raw, dict):
        dates, prices = raw.get("price_dt"), raw.get("price")
        if (not isinstance(dates, list) or not dates or not isinstance(prices, list)
                or any(not isinstance(values, list) or len(values) != len(dates)
                       for values in raw.values())):
            raise RuntimeError("集思录历史列数组缺失、类型改变或长度不一致")
        # The live API returns a column-oriented table.  Only index points are
        # reconstructed: avg_price/mid_price are not returns, volume is turnover.
        raw = [{"price_dt": dates[i], "price": prices[i]} for i in range(len(dates))]
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("集思录转债等权历史为空或格式改变")
    rows, seen = [], set()
    try:
        for item in raw:
            if not isinstance(item, dict) or "price_dt" not in item or "price" not in item:
                raise ValueError("集思录历史缺少price_dt/price")
            stamp = _day(item["price_dt"])
            if stamp in seen:
                raise ValueError("集思录历史日期重复")
            seen.add(stamp)
            rows.append({"date": stamp, "close": _positive(item["price"]),
                         "volume": None})
    except (ValueError, TypeError, OverflowError) as exc:
        raise RuntimeError("集思录转债等权历史校验失败：" + str(exc)) from exc
    rows = sorted((row for row in rows if start <= row["date"] <= end), key=lambda row: row["date"])
    if not rows or rows[-1]["date"] != end:
        raise RuntimeError("集思录转债等权历史未覆盖请求截止日")
    return _frame(rows, "集思录转债等权", "jisilu", url, fetched_at,
                  "集思录转债等权指数点位")
