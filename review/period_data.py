"""Bulk period anchors: dated TDX prices plus a verified complete GBBQ event file.

The compact result deliberately is not a daily-history series. It supplies only
the official calendar anchors needed by the matrix and cannot be used for MA.
"""
import hashlib
import math
import re
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


PERIODS = ("year", "month", "week")
GBBQ_URL = "https://www.tdx.com.cn/products/data/data/dbf/gbbq.zip"
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
METHOD = "tdx-affine-qfq"


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _day(value):
    return isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) and date.fromisoformat(value).isoformat() == value


def _error(exc):
    return f"{type(exc).__name__}：{str(exc)[:240]}"


def _group():
    return {"status": "unavailable", "sample_size": 0, "universe_complete": False,
            "period_coverage": dict.fromkeys(PERIODS, 0), "rows": [], "observations": []}


def empty_period_prices(day, basis=None, message=None):
    anchors = {p: None for p in PERIODS}
    if isinstance(basis, dict) and basis.get("date") == day and isinstance(basis.get("anchors"), dict):
        anchors = {p: basis["anchors"].get(p) for p in PERIODS}
    return {"version": 1, "date": day, "anchors": anchors, "method": METHOD,
            "stocks": _group(), "convertibles": _group(), "provenance": [],
            "observations": [message] if message else []}


def _anchors(day, basis):
    parsed = date.fromisoformat(day)
    if not isinstance(basis, dict) or basis.get("date") != day or not isinstance(basis.get("anchors"), dict):
        return {p: None for p in PERIODS}
    cutoffs = {"year": date(parsed.year, 1, 1), "month": parsed.replace(day=1),
               "week": parsed - timedelta(days=parsed.weekday())}
    result = {p: basis["anchors"].get(p) for p in PERIODS}
    for period, anchor in result.items():
        if anchor is not None and (not _day(anchor) or date.fromisoformat(anchor) >= cutoffs[period]):
            raise ValueError(f"{period} 周期锚点未通过日期核验")
    return result


def _current(rows, day, observations):
    """Retain valid current core quotes; reject every copy of a duplicate symbol."""
    if not isinstance(rows, list):
        observations.append("当前有效样本不是数组，不能计算跨期收益。")
        return {}
    valid, rejected = [], 0
    for row in rows:
        if (not isinstance(row, dict) or row.get("date") != day
                or not isinstance(row.get("symbol"), str)
                or not re.fullmatch(r"(?:sh|sz|bj)\d{6}", row["symbol"])
                or not _positive(row.get("close"))):
            rejected += 1
            continue
        valid.append(row)
    counts = Counter(row["symbol"] for row in valid)
    duplicates = sum(count > 1 for count in counts.values())
    if rejected:
        observations.append(f"剔除 {rejected} 条日期、代码或当前收盘价无效的样本。")
    if duplicates:
        observations.append(f"剔除 {duplicates} 个重复代码的全部当前样本。")
    return {row["symbol"]: row for row in valid if counts[row["symbol"]] == 1}


def _package_index(rows, day, wanted):
    if not isinstance(rows, list) or not rows:
        raise ValueError("官方锚点盘后包为空或格式无效")
    seen, result = set(), {}
    for row in rows:
        if (not isinstance(row, dict) or row.get("date") != day
                or row.get("market") not in ("sh", "sz", "bj")
                or not isinstance(row.get("code"), str) or not re.fullmatch(r"\d{6}", row["code"])):
            raise ValueError("锚点盘后包混入其他日期或无效证券代码")
        symbol = row["market"] + row["code"]
        if symbol in seen:
            raise ValueError("锚点盘后包证券代码重复")
        seen.add(symbol)
        if symbol in wanted and _positive(row.get("close")):
            result[symbol] = row["close"]
    return result


def official_events(adapter):
    """Download once per run; never turn failure into an empty/identity event set."""
    key, error_key = ("period_gbbq",), ("period_gbbq_error",)
    if key in adapter.cache:
        return adapter.cache[key]
    if error_key in adapter.cache:
        raise ValueError(adapter.cache[error_key])
    try:
        request = urllib.request.Request(GBBQ_URL, headers={"User-Agent": "stock-daily-review/1.0"})
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = response.read(MAX_ARCHIVE_BYTES + 1)
            modified = response.headers.get("Last-Modified")
        if len(payload) > MAX_ARCHIVE_BYTES:
            raise ValueError("官方除权事件压缩包超过大小限制")
        from .tdx_adjustments import decode_gbbq_zip
        events = decode_gbbq_zip(payload)
        if not isinstance(events, list) or not events:
            raise ValueError("官方完整除权事件包没有有效记录，不能假设所有证券无除权")
        grouped = defaultdict(list)
        for event in events:
            grouped[event["market"] + event["code"]].append(event)
        provenance = {"dataset": "period_adjustment_events", "source": "通达信官网",
                      "provider": "tdx_official_gbbq", "source_url": GBBQ_URL,
                      "fetched_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                      "archive_sha256": hashlib.sha256(payload).hexdigest(), "record_count": len(events),
                      "latest_event_date": max(event["date"] for event in events),
                      "price_basis": "通达信GBBQ仿射前复权（报告日归一，未四舍五入）"}
        if modified:
            provenance["last_modified"] = modified
        adapter.cache[key] = (dict(grouped), provenance)
        return adapter.cache[key]
    except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
        adapter.cache[error_key] = "官方除权事件包不可用：" + _error(exc)
        raise ValueError(adapter.cache[error_key]) from None


def _finish(group):
    group["sample_size"] = len(group["rows"])
    slots = sum(group["period_coverage"].values())
    if slots:
        group["status"] = "available" if slots == 3 * group["sample_size"] else "partial"
    group["observations"].append("各周期有效价格样本：" + "，".join(
        f"{period} {group['period_coverage'][period]} / {group['sample_size']}" for period in PERIODS) + "。")


def collect_period_prices(adapter, day, basis, current_stocks, convertibles):
    """Use at most three unique historical packages and one complete event archive."""
    result = empty_period_prices(day)
    result["anchors"] = anchors = _anchors(day, basis)
    stocks_group, bonds_group = result["stocks"], result["convertibles"]
    stocks = _current(current_stocks, day, stocks_group["observations"])
    bonds = _current(convertibles, day, bonds_group["observations"])
    if not any(anchors.values()):
        result["observations"].append("缺少经官方交易日历核验的年/月/周期初锚点，未请求历史价格。")
        return result
    if not stocks and not bonds:
        result["observations"].append("没有可匹配的当日A股或可转债有效样本，未请求历史价格。")
        return result
    if isinstance(basis.get("provenance"), list):
        result["provenance"].extend({**p, "dataset": "period_calendar_basis"}
                                    for p in basis["provenance"] if isinstance(p, dict))
    wanted = set(stocks) | set(bonds)
    prices = {}
    for stamp in sorted({a for a in anchors.values() if a is not None}):
        try:
            package = adapter.package(stamp)
            prices[stamp] = _package_index(package, stamp, wanted)
            result["provenance"].extend({**p, "trading_date": stamp}
                                        for p in adapter.provenance("period_anchor_prices", package))
        except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
            prices[stamp] = None
            result["observations"].append(f"{stamp} 官方锚点价格不可用：{_error(exc)}")
    for period, stamp in anchors.items():
        if stamp is None:
            result["observations"].append(f"{period} 周期未取得官方交易日锚点。")

    # Bond price returns need no stock adjustment; an event-file failure must not
    # discard valid bond anchors or tempt us to use identity stock factors.
    missing_bonds = Counter()
    for symbol, current in bonds.items():
        row = {"symbol": symbol, "close": current["close"], **dict.fromkeys(PERIODS)}
        for period, stamp in anchors.items():
            raw = prices.get(stamp)
            value = raw.get(symbol) if isinstance(raw, dict) else None
            if _positive(value):
                row[period] = {"date": stamp, "raw_close": value}
                bonds_group["period_coverage"][period] += 1
            else:
                missing_bonds[period] += 1
        bonds_group["rows"].append(row)
    bonds_group["observations"].append("转债按报告日与精确期初收盘报价计算价格收益，未另计已支付票息及其再投资。")
    if missing_bonds:
        bonds_group["observations"].append("未取得期初价格（含尚未上市或锚点源缺失）的转债：" +
                                           "，".join(f"{p} {missing_bonds[p]}" for p in PERIODS) + "。")
    _finish(bonds_group)

    if any(symbol.startswith("bj") for symbol in stocks):
        stocks_group["observations"].append("官方事件包对北交所股票的完整覆盖尚未核验，北交所跨期收益不参与统计。")
    events, event_source = None, None
    if any(symbol.startswith(("sh", "sz")) for symbol in stocks) and any(isinstance(p, dict) for p in prices.values()):
        try:
            events, event_source = official_events(adapter)
            result["provenance"].append(event_source)
        except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
            stocks_group["observations"].append("复权未验证：" + _error(exc) + "；未将缺失事件数据当作因子1。")
    if events is None:
        stocks_group["observations"].append("尚无完整且核验通过的除权事件依据，A股跨期价格留空。")
        _finish(stocks_group)
        return result

    from .tdx_adjustments import adjusted_anchor
    missing_stocks, missing_issuers, adjustment_failures = Counter(), Counter(), Counter()
    examples = []
    for symbol, current in stocks.items():
        row = {"symbol": symbol, "close": current["close"], **dict.fromkeys(PERIODS)}
        for period, stamp in anchors.items():
            raw = prices.get(stamp)
            value = raw.get(symbol) if isinstance(raw, dict) else None
            if not _positive(value):
                missing_stocks[period] += 1
                continue
            if symbol.startswith("bj"):
                continue
            issuer_events = events.get(symbol)
            if not isinstance(issuer_events, list) or not issuer_events:
                missing_issuers[period] += 1
                if len(examples) < 10:
                    examples.append({"symbol": symbol, "period": period,
                                     "reason": "官方除权事件包缺少证券发行人记录，不能假设无除权。"})
                continue
            try:
                adjusted = adjusted_anchor(value, stamp, day, issuer_events)
                if not _positive(adjusted):
                    raise ValueError("复权锚点不是正有限价格")
                row[period] = {"date": stamp, "raw_close": value, "adjusted_close": adjusted}
                stocks_group["period_coverage"][period] += 1
            except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
                adjustment_failures[period] += 1
                if len(examples) < 10:
                    examples.append({"symbol": symbol, "period": period, "reason": _error(exc)})
        stocks_group["rows"].append(row)
    stocks_group["observations"].append("通达信GBBQ仿射前复权，期初价格归一到报告日原始收盘口径，保留完整计算精度。")
    if missing_stocks:
        stocks_group["observations"].append("未取得期初价格（含新上市证券或锚点源缺失）的A股：" +
                                            "，".join(f"{p} {missing_stocks[p]}" for p in PERIODS) + "。")
    if missing_issuers:
        stocks_group["observations"].append("官方除权事件包缺少证券发行人记录，不能假设无除权的A股：" +
                                            "，".join(f"{p} {missing_issuers[p]}" for p in PERIODS) + "。")
    if adjustment_failures:
        stocks_group["observations"].append("除权事件不支持或复权价格无效的A股：" +
                                            "，".join(f"{p} {adjustment_failures[p]}" for p in PERIODS) + "。")
    if examples:
        stocks_group["quality_examples"] = examples
    _finish(stocks_group)
    return result
