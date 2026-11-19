"""Pure Trend Template checks and an independent, explicitly scoped RS proxy.

``analyze_trend`` consumes one consistently adjusted, dated OHLC series. It does
not select its own ranking population. Each of the eight checks runs with the
history it needs; a hard failure wins over missing evidence. A complete pass
also requires at least 253 bars, allowing a real 252-session return.

``build_market_rs`` ranks *all* supplied eligible market rows before any Trend
Template/candidate filtering. This is a weighted price-return percentile proxy,
not a proprietary IBD RS Rating. Inputs are ``{symbol, date, returns_pct}`` with
63d/126d/252d percentage returns. Universe proof is supplied separately through
``config.market_universe`` (``config.market`` is also accepted): date, id/label,
scope, vetted, point_in_time, complete and eligible_count. Market samples require
``config.trend.allow_market_sample_rs = True`` and remain labeled samples.

The snapshot stores method/universe/coverage once; rows contain symbol, date,
returns_pct, score, percentile and status. To check one stock, supply its row to
``analyze_trend`` together with the snapshot's universe and coverage. Setting
``config.symbol`` additionally verifies that the row belongs to that stock.

All numeric output is finite or None. Missing evidence is never replaced by 0.
The caller must vet adjustment basis, exchange sessions and source provenance;
PIT here describes the dated input population, not a claim that a vendor's
historical archive has never been revised.
"""
from collections import Counter
from collections.abc import Mapping
from datetime import date
import math


HORIZONS = {"63d": 63, "126d": 126, "252d": 252}
DEFAULT_WEIGHTS = {"63d": 0.4, "126d": 0.3, "252d": 0.3}
METHOD = "weighted_price_return_percentile_proxy_v1"


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _date(value):
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _settings(config):
    if not isinstance(config, Mapping):
        raise ValueError("config must be a mapping")
    settings = config.get("trend", config)
    if not isinstance(settings, Mapping):
        raise ValueError("config.trend must be a mapping")
    return settings


def _positive_int(settings, key, default, minimum=1):
    value = settings.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(key + " must be an integer >= " + str(minimum))
    return value


def _bounded(settings, key, default, low, high):
    value = settings.get(key, default)
    if not _number(value) or not low <= value <= high:
        raise ValueError(key + " is outside its valid range")
    return float(value)


def _rs_policy(config):
    settings = _settings(config)
    weights = settings.get("rs_weights", DEFAULT_WEIGHTS)
    if (not isinstance(weights, Mapping) or set(weights) != set(HORIZONS)
            or any(not _number(value) or not 0 <= value <= 1 for value in weights.values())
            or not math.isclose(math.fsum(weights.values()), 1.0, rel_tol=0, abs_tol=1e-10)):
        raise ValueError("rs_weights must contain 63d/126d/252d nonnegative weights summing to 1")
    if not all(weights[key] > 0 for key in HORIZONS):
        raise ValueError("all RS horizons must have a positive weight")
    minimum_ratio = _bounded(settings, "rs_min_coverage", 0.90, 0, 1)
    if minimum_ratio == 0:
        raise ValueError("rs_min_coverage must be greater than zero")
    return {"weights": {key: float(weights[key]) for key in HORIZONS},
            "minimum_ratio": minimum_ratio,
            "minimum_samples": _positive_int(settings, "rs_min_samples", 1000, minimum=2),
            "allow_sample": settings.get("allow_market_sample_rs") is True}


def _universe(raw, as_of, policy):
    fields = ("id", "label", "date", "scope", "vetted", "point_in_time", "complete",
              "eligible_count", "listing_roster_verified", "point_in_time_basis")
    def scalar(value):
        return value if value is None or isinstance(value, (str, bool)) or _number(value) else None
    universe = {key: scalar(raw.get(key)) for key in fields if key in raw} if isinstance(raw, Mapping) else {}
    reasons = []
    if not universe:
        return universe, ["缺少经核验的市场排名范围及其时点信息"]
    if universe.get("date") != as_of:
        reasons.append("市场排名范围日期与分析日期不一致")
    if universe.get("vetted") is not True or universe.get("point_in_time") is not True:
        reasons.append("市场排名范围未通过来源及历史时点核验")
    scope = universe.get("scope")
    if scope == "market_sample":
        if not policy["allow_sample"]:
            reasons.append("市场样本RS未显式启用，不能当作全市场RS")
        if universe.get("complete") is not False:
            reasons.append("市场样本必须明确标注非完整市场")
    elif scope == "market":
        if universe.get("complete") is not True:
            reasons.append("完整市场RS缺少完整证券范围证明")
    else:
        reasons.append("RS排名范围必须为独立市场或明确的市场样本，不能使用候选池")
    if raw.get("candidate_filtered") is True:
        reasons.append("RS输入已经按候选条件筛选，不能作为独立市场排名")
    count = universe.get("eligible_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 2:
        reasons.append("市场排名范围缺少有效的应覆盖证券数量")
    if not isinstance(universe.get("label"), str) or not universe["label"].strip():
        reasons.append("市场排名范围缺少可读口径标签")
    return universe, reasons


def build_market_rs(as_of, returns_rows, config=None):
    """Build a dated, coverage-gated cross-sectional RS proxy.

    Scores use 40%/30%/30% of 63/126/252-session percentage returns by default.
    Equal scores (rounded to 12 decimal percentage points for floating noise)
    receive the average ascending rank. Percentile = 100*(rank-1)/(N-1), so ties
    across the entire population receive 50, not an arbitrary winner. A partial
    or unvetted population retains auditable scores but no valid percentiles.
    """
    config = {} if config is None else config
    if not _date(as_of):
        raise ValueError("as_of must be an ISO date")
    if not isinstance(returns_rows, list):
        raise ValueError("returns_rows must be a list")
    policy = _rs_policy(config)
    raw_universe = config.get("market_universe", config.get("market", {}))
    universe, reasons = _universe(raw_universe, as_of, policy)
    symbols = Counter(row.get("symbol") for row in returns_rows
                      if isinstance(row, Mapping) and isinstance(row.get("symbol"), str))
    valid, excluded = [], []
    for item in returns_rows:
        symbol = item.get("symbol") if isinstance(item, Mapping) else None
        reason = None
        if not isinstance(symbol, str) or not symbol.strip():
            reason = "缺少证券代码"
        elif symbols[symbol] != 1:
            reason = "证券代码重复，所有重复记录均不参与排名"
        elif item.get("date") != as_of:
            reason = "收益日期与市场排名日期不一致"
        values = item.get("returns_pct") if isinstance(item, Mapping) else None
        if reason is None and (not isinstance(values, Mapping) or any(
                not _number(values.get(key)) or values[key] <= -100 for key in HORIZONS)):
            reason = "缺少有效的63/126/252交易日收益，未补零"
        if reason:
            excluded.append({"symbol": symbol if isinstance(symbol, str) else None, "reason": reason})
            continue
        score = math.fsum(values[key] * policy["weights"][key] for key in HORIZONS)
        if not math.isfinite(score):
            excluded.append({"symbol": symbol, "reason": "加权收益超出有效数值范围"})
            continue
        valid.append({"symbol": symbol, "date": as_of, "returns_pct": {key: values[key] for key in HORIZONS},
                      "score": score, "percentile": None, "status": "insufficient"})
    eligible = universe.get("eligible_count")
    eligible_ok = isinstance(eligible, int) and not isinstance(eligible, bool) and eligible >= 2
    ratio = len(valid) / eligible if eligible_ok and len(valid) <= eligible else None
    coverage = {"eligible_count": eligible if eligible_ok else None, "valid_count": len(valid),
                "ratio": ratio, "minimum_ratio": policy["minimum_ratio"],
                "minimum_samples": policy["minimum_samples"], "input_count": len(returns_rows)}
    if ratio is None:
        reasons.append("有效证券数量与排名范围的应覆盖数量无法一致核验")
    elif ratio < policy["minimum_ratio"]:
        reasons.append("市场收益覆盖比例低于配置门槛")
    if len(valid) < policy["minimum_samples"]:
        reasons.append("市场收益有效证券数量低于配置门槛")
    status = "insufficient" if reasons else "available"
    if status == "available":
        ordered = sorted(valid, key=lambda row: (round(row["score"], 12), row["symbol"]))
        index = 0
        while index < len(ordered):
            end = index + 1
            key = round(ordered[index]["score"], 12)
            while end < len(ordered) and round(ordered[end]["score"], 12) == key:
                end += 1
            average_rank = (index + 1 + end) / 2
            percentile = (average_rank - 1) / (len(ordered) - 1) * 100
            for row in ordered[index:end]:
                row["percentile"], row["status"] = percentile, "available"
            index = end
    return {"version": 1, "date": as_of, "status": status, "universe": universe, "coverage": coverage,
            "method": {"id": METHOD, "horizons": dict(HORIZONS), "weights": policy["weights"],
                       "score_unit": "percentage_points", "tie_policy": "average_rank",
                       "score_rounding_decimals_for_ties": 12,
                       "percentile_formula": "100 * (average_ascending_rank - 1) / (N - 1)",
                       "ranking_population": "all eligible supplied market rows before candidate/trend filtering"},
            "rows": sorted(valid, key=lambda row: row["symbol"]), "excluded": excluded,
            "reasons": list(dict.fromkeys(reasons))}


def _mean(closes, period):
    if len(closes) < period:
        return None
    window = closes[-period:]
    try:
        return math.fsum(value / period for value in window)
    except OverflowError:
        scale = max(window)
        return scale * min(1.0, math.fsum(value / scale for value in window) / period)


def _rs_check(evidence, as_of, config, policy):
    if not isinstance(evidence, Mapping):
        return None, ["缺少独立市场RS证据"]
    reasons = []
    if not _date(as_of):
        reasons.append("价格历史末日无法核验，不能匹配RS证据")
    if evidence.get("date") != as_of:
        reasons.append("RS证据日期与价格日期不一致")
    if config.get("symbol") is not None and evidence.get("symbol") != config["symbol"]:
        reasons.append("RS证据证券代码与分析证券不一致")
    if not isinstance(evidence.get("symbol"), str) or not evidence["symbol"]:
        reasons.append("RS证据缺少证券代码")
    if evidence.get("status") not in (None, "available"):
        reasons.append("市场RS尚未通过覆盖及范围门槛")
    universe, universe_reasons = _universe(evidence.get("universe", {}), as_of, policy)
    reasons.extend(universe_reasons)
    coverage = evidence.get("coverage")
    if not isinstance(coverage, Mapping):
        reasons.append("RS证据缺少市场覆盖信息")
    else:
        count, eligible, ratio = coverage.get("valid_count"), coverage.get("eligible_count"), coverage.get("ratio")
        if (not isinstance(count, int) or isinstance(count, bool) or count < policy["minimum_samples"]
                or not isinstance(eligible, int) or isinstance(eligible, bool) or eligible < count
                or not _number(ratio) or not policy["minimum_ratio"] <= ratio <= 1
                or eligible < 2 or not math.isclose(ratio, count / eligible, rel_tol=0, abs_tol=1e-10)
                or eligible != universe.get("eligible_count")):
            reasons.append("RS证据市场覆盖未达到门槛或数量不一致")
    percentile = evidence.get("percentile")
    if not _number(percentile) or not 0 <= percentile <= 100:
        reasons.append("RS百分位缺失或无效")
    return (float(percentile) if not reasons else None), list(dict.fromkeys(reasons))


def analyze_trend(bars, config, relative_strength=None):
    """Evaluate eight independent Trend Template checks without candidate ranking."""
    settings, policy = _settings(config), _rs_policy(config)
    minimum = _positive_int(settings, "min_history_bars", 253, minimum=253)
    slope_days = _positive_int(settings, "ma200_rising_days", 21)
    low_threshold = _bounded(settings, "min_above_52w_low_pct", 30, 0, 10000)
    high_threshold = _bounded(settings, "max_below_52w_high_pct", 25, 0, 100)
    rs_threshold = _bounded(settings, "rs_min_percentile", 70, 0, 100)
    closes, dates, history_reason = [], [], None
    if not isinstance(bars, list) or not bars:
        history_reason = "没有有效的价格历史"
    else:
        for bar in bars:
            if (not isinstance(bar, Mapping) or not _date(bar.get("date"))
                    or (dates and bar["date"] <= dates[-1])
                    or not _number(bar.get("close")) or bar["close"] <= 0):
                history_reason = "历史日期无效、重复、乱序或收盘价不是正有限数值"
                break
            dates.append(bar["date"])
            closes.append(bar["close"])
    as_of = dates[-1] if dates and history_reason is None else None
    if config.get("as_of") is not None and as_of != config["as_of"]:
        history_reason = "价格历史末日与请求分析日期不一致"
        as_of = None
    if history_reason:
        closes, dates = [], []
    current = closes[-1] if closes else None
    ma50, ma150, ma200 = (_mean(closes, period) for period in (50, 150, 200))
    returns = {key: ((current / closes[-period - 1] - 1) * 100 if len(closes) > period else None)
               for key, period in HORIZONS.items()}
    returns = {key: value if _number(value) else None for key, value in returns.items()}
    ma_path = [_mean(closes[:end], 200) for end in range(max(200, len(closes) - slope_days), len(closes) + 1)]
    positive_slopes = 0
    for previous, later in reversed(list(zip(ma_path, ma_path[1:]))):
        if later <= previous:
            break
        positive_slopes += 1
    low, high, above_low, below_high = None, None, None, None
    extrema_reason = None
    if not history_reason and len(bars) >= 252:
        window = bars[-252:]
        if all(_number(bar.get("low")) and _number(bar.get("high")) and 0 < bar["low"] <= bar["close"] <= bar["high"] for bar in window):
            low, high = min(bar["low"] for bar in window), max(bar["high"] for bar in window)
            above_low, below_high = (current / low - 1) * 100, (high - current) / high * 100
            if not _number(above_low):
                above_low = None
                extrema_reason = "52周低点价格比值超出有效数值范围"
        else:
            extrema_reason = "最近252条历史缺少有效且与收盘一致的高低价"
    else:
        extrema_reason = history_reason or "52周高低价需要252条有效历史"
    checks = []

    def check(key, label, actual, threshold, ready, passed, missing=None):
        status = "insufficient" if not ready else "pass" if passed else "fail"
        reason = (missing or history_reason or "历史不足，不能验证此条件") if not ready else None if passed else label + "未满足"
        checks.append({"id": key, "label": label, "status": status,
                       "passed": None if not ready else bool(passed), "actual": actual,
                       "threshold": threshold, "reason": reason})

    check("price_above_ma150_ma200", "现价高于MA150和MA200", {"close": current, "ma150": ma150, "ma200": ma200},
          "close > MA150 and close > MA200", ma200 is not None, ma200 is not None and current > ma150 and current > ma200,
          history_reason or "需要200条有效收盘历史")
    check("ma150_above_ma200", "MA150高于MA200", {"ma150": ma150, "ma200": ma200}, "MA150 > MA200",
          ma200 is not None, ma200 is not None and ma150 > ma200, history_reason or "需要200条有效收盘历史")
    check("ma200_rising_21", "MA200连续" + str(slope_days) + "个交易日上升", {"consecutive_positive_days": positive_slopes, "tested_slopes": max(0, len(ma_path) - 1)},
          {"required_positive_days": slope_days}, len(closes) >= 200 + slope_days, positive_slopes >= slope_days,
          history_reason or "连续MA200斜率需要" + str(200 + slope_days) + "条有效收盘历史")
    check("ma50_above_ma150_ma200", "MA50高于MA150和MA200", {"ma50": ma50, "ma150": ma150, "ma200": ma200},
          "MA50 > MA150 and MA50 > MA200", ma200 is not None, ma200 is not None and ma50 > ma150 and ma50 > ma200,
          history_reason or "需要200条有效收盘历史")
    check("price_above_ma50", "现价高于MA50", {"close": current, "ma50": ma50}, "close > MA50",
          ma50 is not None, ma50 is not None and current > ma50, history_reason or "需要50条有效收盘历史")
    check("above_52w_low", "现价较52周低点至少上涨" + str(low_threshold) + "%", {"close": current, "low_52w": low, "above_low_pct": above_low},
          {"minimum_above_low_pct": low_threshold}, above_low is not None, above_low is not None and above_low + 1e-10 >= low_threshold, extrema_reason)
    check("near_52w_high", "现价距52周高点不超过" + str(high_threshold) + "%", {"close": current, "high_52w": high, "below_high_pct": below_high},
          {"maximum_below_high_pct": high_threshold}, below_high is not None, below_high is not None and below_high <= high_threshold + 1e-10, extrema_reason)
    percentile, rs_reasons = _rs_check(relative_strength, as_of, config, policy)
    check("market_rs", "独立市场RS百分位至少" + str(rs_threshold), {"percentile": percentile},
          {"minimum_percentile": rs_threshold}, percentile is not None, percentile is not None and percentile >= rs_threshold,
          "；".join(rs_reasons))
    reasons = [item["reason"] for item in checks if item["reason"]]
    if len(closes) < minimum:
        reasons.append("完整趋势判定至少需要" + str(minimum) + "条有效历史，缺失收益不补零")
    status = ("fail" if any(item["status"] == "fail" for item in checks) else
              "insufficient" if len(closes) < minimum or any(item["status"] == "insufficient" for item in checks) else "pass")
    return {"version": 1, "date": as_of, "status": status, "checks": checks,
            "moving_averages": {"ma50": ma50, "ma150": ma150, "ma200": ma200,
                                "ma200_positive_slope_days": positive_slopes if ma200 is not None else None},
            "returns_pct": returns, "fifty_two_week": {"low": low, "high": high, "above_low_pct": above_low, "below_high_pct": below_high},
            "history": {"bars": len(closes), "minimum_bars": minimum, "price_series_valid": history_reason is None},
            "reasons": list(dict.fromkeys(reasons))}
