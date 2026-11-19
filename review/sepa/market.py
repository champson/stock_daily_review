"""Dated, auditable market-sample returns from four bulk official packets.

This adapter deliberately does not claim an exchange listing master or a
historical investable-universe reconstruction.  Its scope is the named,
actively traded Shanghai/Shenzhen A-share number-range sample in the exact
report-date packet, intersected with three exact historical closes.  Callers
must opt in before using that sample's percentiles as an RS proxy.

Sources can be supplied by an offline adapter/cache.  Calendar gaps never fall
back to weekdays, nearby price packets, or approximate calendar-day offsets.
"""
import calendar
import hashlib
import json
import math
import re
from collections import Counter
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from ..period_data import official_events
from ..tdx_adjustments import adjusted_anchor

HORIZONS = {"63d": 63, "126d": 126, "252d": 252}
MAX_LOOKBACK_DAYS = 560
METHOD = "tdx-affine-qfq"
SOURCE_KIND = "tdx_official_packages_gbbq"
UNIVERSE_ID = "tdx_active_a_share_sample"
UNIVERSE_LABEL = "沪深A股号段当日具名有成交、精确历史锚点可比样本"
PREFIXES = {"sh": ("600", "601", "603", "605", "688", "689"),
            "sz": ("000", "001", "002", "003", "300", "301")}
CALENDAR_URL = "https://www.szse.cn/api/report/exchange/onepersistenthour/monthList"
HOLIDAY_URL = "https://www.tdx.com.cn/url/holiday/"
GBBQ_URL = "https://www.tdx.com.cn/products/data/data/dbf/gbbq.zip"
FAILURES = (RuntimeError, ValueError, KeyError, TypeError, OSError, ArithmeticError)


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("交易日期必须为 YYYY-MM-DD")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("交易日期不是有效 ISO 日期")
    return parsed


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _stamp(value):
    if not isinstance(value, str):
        raise ValueError("来源获取时间缺失")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("来源获取时间必须包含时区")
    return parsed


def _official_month(value, year, month):
    if not isinstance(value, dict):
        raise ValueError("官方月历格式无效")
    url = value.get("source_url")
    parsed = urlsplit(url) if isinstance(url, str) else None
    if parsed is None or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("月历来源不是已核验官方入口")
    base = parsed._replace(query="").geturl()
    if base not in (CALENDAR_URL, HOLIDAY_URL):
        raise ValueError("月历来源不是已核验官方入口")
    query = parse_qs(parsed.query)
    if "month" in query and query["month"] not in ([f"{year:04d}-{month:02d}"], [f"{year:04d}-{month}"]):
        raise ValueError("官方月历来源日期与请求月份不一致")
    if not isinstance(value.get("source"), str) or not value["source"].strip():
        raise ValueError("官方月历缺少来源名称")
    _stamp(value.get("fetched_at"))
    expected = {date(year, month, n).isoformat()
                for n in range(1, calendar.monthrange(year, month)[1] + 1)}
    rows = value.get("rows")
    if (not isinstance(rows, list) or len(rows) != len(expected)
            or any(not isinstance(row, dict) or not isinstance(row.get("is_open"), bool)
                   or row.get("date") not in expected for row in rows)
            or {row["date"] for row in rows} != expected):
        raise ValueError("官方月历缺日、重复、错月或开市标记异常")
    return {row["date"]: row["is_open"] for row in rows}


def collect_return_anchors(adapter, as_of):
    """Return dates exactly 63/126/252 official trading sessions before as_of.

    Incomplete archived calendars produce partial/unavailable anchors and an
    explicit issue.  A known anchor remains useful evidence, but callers must
    not create a three-horizon ranking until every anchor is verified.
    """
    day = _date(as_of)
    result = {"date": as_of, "status": "unavailable", "anchors": dict.fromkeys(HORIZONS),
              "observations": [], "provenance": []}
    months = {}

    def opened(cursor):
        key = (cursor.year, cursor.month)
        if key not in months:
            value = adapter.calendar_month(*key)
            months[key] = _official_month(value, *key)
            result["provenance"].append({"dataset": "sepa_rs_calendar",
                "calendar_month": f"{key[0]:04d}-{key[1]:02d}",
                **{field: value[field] for field in ("source", "source_url", "fetched_at")}})
            if value.get("warning"):
                result["observations"].append(str(value["warning"]))
        return months[key][cursor.isoformat()]

    try:
        if not opened(day):
            raise ValueError("报告日未获官方月历确认为开市日")
        count = 0
        for offset in range(1, MAX_LOOKBACK_DAYS + 1):
            cursor = day - timedelta(days=offset)
            if opened(cursor):
                count += 1
                for horizon, sessions in HORIZONS.items():
                    if count == sessions:
                        result["anchors"][horizon] = cursor.isoformat()
                if count == max(HORIZONS.values()):
                    break
        if not all(result["anchors"].values()):
            raise ValueError("已核验官方月历不足252个历史交易日")
    except FAILURES as exc:
        result["observations"].append("RS精确交易日锚点未完全核验：" + str(exc)[:240])
    count = sum(anchor is not None for anchor in result["anchors"].values())
    result["status"] = "available" if count == len(HORIZONS) else "partial" if count else "unavailable"
    return result


def _packet(adapter, stamp):
    rows = adapter.package(stamp)
    if not isinstance(rows, list) or not rows:
        raise ValueError("官方精确日包为空或格式无效")
    expected_url = "https://www.tdx.com.cn/products/data/data/g4day/" + stamp.replace("-", "") + ".zip"
    index = {}
    for row in rows:
        if (not isinstance(row, dict) or row.get("date") != stamp
                or row.get("market") not in ("sh", "sz", "bj")
                or not isinstance(row.get("code"), str) or not re.fullmatch(r"\d{6}", row["code"])
                or row.get("source_url") != expected_url):
            raise ValueError("官方日包日期、证券代码或来源URL不匹配")
        _stamp(row.get("fetched_at"))
        symbol = row["market"] + row["code"]
        if symbol in index:
            raise ValueError("官方日包证券代码重复")
        index[symbol] = row
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False,
                                      allow_nan=False, separators=(",", ":")).encode()).hexdigest()
    sources = adapter.provenance("sepa_rs_prices", rows)
    if not isinstance(sources, list) or not sources:
        raise ValueError("官方日包来源凭据缺失")
    provenance = [{**source, "trading_date": stamp, "record_count": len(rows),
                   "snapshot_sha256": digest} for source in sources]
    return index, provenance


def _eligible(row):
    market = row["market"]
    return (market in PREFIXES and row["code"].startswith(PREFIXES[market])
            and isinstance(row.get("name"), str) and bool(row["name"].strip())
            and all(_positive(row.get(field)) for field in ("close", "volume", "amount")))


def _events(adapter, as_of):
    events, provenance = official_events(adapter)
    if (not isinstance(events, dict) or not events or not isinstance(provenance, dict)
            or provenance.get("provider") != "tdx_official_gbbq"
            or provenance.get("source_url") != GBBQ_URL
            or not isinstance(provenance.get("archive_sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", provenance["archive_sha256"])
            or not isinstance(provenance.get("record_count"), int)
            or isinstance(provenance["record_count"], bool) or provenance["record_count"] <= 0):
        raise ValueError("官方完整GBBQ事件包来源未通过核验")
    if _stamp(provenance.get("fetched_at")).astimezone(ZoneInfo("Asia/Shanghai")).date() < _date(as_of):
        raise ValueError("GBBQ事件快照早于报告日，无法证明覆盖报告日前全部除权")
    return events, {**provenance, "dataset": "sepa_rs_adjustment_events"}


def collect_market_returns(adapter, as_of):
    """Build a JSON-safe market-sample input using <=4 packets and one GBBQ.

    Every returned row has all three comparable, finite percentage returns.
    Missing historic prices, unknown issuer events or unsupported actions exclude
    that security; they are never treated as unchanged prices or zero returns.
    The full report-date eligible denominator is kept for downstream coverage.
    """
    _date(as_of)
    universe = {"date": as_of, "id": UNIVERSE_ID, "label": UNIVERSE_LABEL,
                "scope": "market_sample", "complete": False, "vetted": False,
                "point_in_time": False, "eligible_count": 0, "ranking_count": 0,
                "listing_roster_verified": False,
                "point_in_time_basis": "精确日期日包样本，非历史挂牌名册重建"}
    result = {"version": 1, "date": as_of, "universe": UNIVERSE_LABEL,
              "market_universe": universe, "input_count": 0, "sample_size": 0,
              "status": "unavailable", "method": METHOD, "source_kind": SOURCE_KIND,
              "price_basis": "通达信GBBQ仿射前复权，精确锚点归一到报告日原始收盘；不预先舍入",
              "return_unit": "percent", "anchors": dict.fromkeys(HORIZONS),
              "coverage": dict.fromkeys(HORIZONS, 0), "rows": [], "provenance": [],
              "observations": ["只覆盖沪深A股号段当日具名且有成交的官方日包样本；未核对交易所挂牌全名册，不标为全市场。",
                               "北交所除权覆盖尚未验收，暂不进入RS比较样本；停牌、无成交及无名记录亦排除。"]}
    basis = collect_return_anchors(adapter, as_of)
    result["anchors"] = basis["anchors"]
    result["observations"].extend(basis["observations"])
    result["provenance"].extend(basis["provenance"])
    if basis["status"] != "available":
        result["observations"].append("三个精确交易日锚点未齐备，不生成可排名的三周期市场收益输入。")
        return result
    try:
        current, sources = _packet(adapter, as_of)
        result["provenance"].extend(sources)
        eligible = {symbol: row for symbol, row in current.items() if _eligible(row)}
        result["input_count"] = universe["eligible_count"] = len(eligible)
        if not eligible:
            raise ValueError("报告日没有具名且有成交的有效沪深A股号段样本")
    except FAILURES as exc:
        result["observations"].append("报告日官方样本不可用：" + str(exc)[:240])
        return result
    prices, failed = {}, False
    for stamp in sorted(set(basis["anchors"].values())):
        try:
            prices[stamp], sources = _packet(adapter, stamp)
            result["provenance"].extend(sources)
        except FAILURES as exc:
            failed = True
            result["observations"].append(stamp + " 官方历史日包不可用：" + str(exc)[:240])
    if failed:
        result["observations"].append("历史价格包未齐备，不以邻近日或缺失价格构造收益。")
        return result
    try:
        events, provenance = _events(adapter, as_of)
        result["provenance"].append(provenance)
    except FAILURES as exc:
        result["observations"].append("官方除权依据不可用：" + str(exc)[:240] + "；未假设复权因子为1。")
        return result
    excluded = Counter()
    for symbol, current in sorted(eligible.items()):
        issuer = events.get(symbol)
        if (not isinstance(issuer, list) or not issuer
                or any(not isinstance(event, dict)
                       or event.get("market") != symbol[:2] or event.get("code") != symbol[2:] for event in issuer)):
            excluded["missing_or_mismatched_issuer"] += 1
            continue
        returns = {}
        for horizon, stamp in basis["anchors"].items():
            raw = prices[stamp].get(symbol, {}).get("close")
            if not _positive(raw):
                excluded["missing_" + horizon] += 1
                continue
            try:
                adjusted = adjusted_anchor(raw, stamp, as_of, issuer)
                value = (current["close"] / adjusted - 1) * 100
                if not _positive(adjusted) or not math.isfinite(value):
                    raise ValueError("复权价格或收益不是有效有限值")
                returns[horizon] = value
                result["coverage"][horizon] += 1
            except FAILURES:
                excluded["invalid_adjustment_" + horizon] += 1
        if len(returns) == len(HORIZONS):
            result["rows"].append({"symbol": symbol, "name": current["name"].strip(),
                                   "date": as_of, "returns_pct": returns})
    result["sample_size"] = len(result["rows"])
    universe["ranking_count"] = result["sample_size"]
    universe["vetted"] = universe["point_in_time"] = bool(result["sample_size"])
    result["status"] = ("available" if result["sample_size"] == result["input_count"]
                        else "partial" if result["sample_size"] else "unavailable")
    result["excluded"] = dict(excluded)
    result["observations"].append(f"当日号段有成交具名样本 {result['input_count']} 个，三个精确锚点与完整发行人事件均可比的交集 {result['sample_size']} 个。")
    result["observations"].append("vetted/point_in_time仅表示来源、证券映射和日期快照核验；不证明历史挂牌全名册或存续偏差已消除。")
    return result
