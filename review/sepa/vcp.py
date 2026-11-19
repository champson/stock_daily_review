"""Causal daily-bar VCP geometry and observation states, never trade execution.

Fractal extrema are usable only after their right-hand window has closed.  The
working geometry may acquire a better same-direction extremum, but published
events contain copies and are never rewritten.  A setup freezes its geometry
and pivot before any eligible breakout bar.  All states describe completed
daily OHLCV observations, not a real-time intraday feed or an order fill.
"""
from copy import deepcopy
from collections.abc import Mapping
from datetime import date
import math
from statistics import mean, median

ANALYSIS_VERSION = 2

DEFAULTS = {
    "swing_window": 3, "min_swing_pct": 2.0,
    "min_contractions": 2, "max_contractions": 5,
    "min_base_bars": 10, "max_base_bars": 120,
    "contraction_tolerance_pct": 2.0, "max_last_drawdown_pct": 15.0,
    "volume_lookback": 20, "breakout_volume_ratio": 1.5,
    "breakout_buffer_pct": 0.0, "failure_buffer_pct": 3.0,
    "retest_tolerance_pct": 2.0, "max_signal_age_bars": 30,
    "atr_period": 14,
}
STATES = ("incomplete", "forming", "ready", "intraday_breakout",
          "unconfirmed_breakout", "breakout", "retest", "failed",
          "invalidated", "expired")


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _config(config):
    if config is not None and not isinstance(config, Mapping):
        raise ValueError("VCP config must be a mapping")
    cfg = {**DEFAULTS, **dict(config or {})}
    integer = ("swing_window", "min_contractions", "max_contractions", "min_base_bars",
               "max_base_bars", "volume_lookback", "max_signal_age_bars", "atr_period")
    for key in DEFAULTS:
        value = cfg[key]
        if not _number(value) or value < 0 or key in integer and (not isinstance(value, int) or value < 1):
            raise ValueError("invalid VCP parameter: " + key)
    if not 2 <= cfg["min_contractions"] <= cfg["max_contractions"] <= 5:
        raise ValueError("VCP requires two to five contractions")
    if cfg["min_base_bars"] > cfg["max_base_bars"] or cfg["swing_window"] > 30:
        raise ValueError("invalid VCP base or confirmation window")
    if cfg["volume_lookback"] < 2 or cfg["breakout_volume_ratio"] <= 0 or cfg["failure_buffer_pct"] >= 100:
        raise ValueError("invalid VCP volume/failure threshold")
    supplied_weights = cfg.get("score_weights", {})
    if not isinstance(supplied_weights, Mapping):
        raise ValueError("invalid VCP score weights")
    weights = {"price": 25.0, "volume": 15.0, "atr": 10.0, **supplied_weights}
    if any(not _number(v) or v < 0 for v in weights.values()) or set(weights) != {"price", "volume", "atr"} or sum(weights.values()) <= 0:
        raise ValueError("invalid VCP score weights")
    cfg["score_weights"] = weights
    return cfg


def _bars(bars, as_of=None):
    result, previous = [], ""
    if as_of is not None and (not isinstance(as_of, str) or date.fromisoformat(as_of).isoformat() != as_of):
        raise ValueError("invalid as_of date")
    for row in bars:
        stamp = row.get("date") if isinstance(row, dict) else None
        if (not isinstance(stamp, str) or date.fromisoformat(stamp).isoformat() != stamp
                or stamp <= previous or as_of is not None and stamp > as_of):
            raise ValueError("VCP bars must have unique ordered non-future dates")
        if any(not _number(row.get(k)) or row[k] <= 0 for k in ("open", "high", "low", "close")):
            raise ValueError("VCP OHLC must be positive finite numbers")
        if row["low"] > min(row["open"], row["close"]) or row["high"] < max(row["open"], row["close"]) or row["low"] > row["high"]:
            raise ValueError("inconsistent OHLC range")
        volume = row.get("volume")
        if volume is not None and (not _number(volume) or volume < 0):
            raise ValueError("VCP volume must be nonnegative or null")
        result.append(dict(row, volume=volume))
        previous = stamp
    return result


def _ranges(bars, period):
    tr, atr = [], []
    for i, bar in enumerate(bars):
        value = bar["high"] - bar["low"]
        if i:
            value = max(value, abs(bar["high"] - bars[i - 1]["close"]),
                        abs(bar["low"] - bars[i - 1]["close"]))
        tr.append(value)
        if i < period - 1:
            atr.append(None)
        elif i == period - 1:
            atr.append(mean(tr))
        else:
            atr.append((atr[-1] * (period - 1) + value) / period)
    return tr, atr


def _score(values):
    if len(values) < 2 or any(v is None for v in values) or values[0] <= 0:
        return None
    increases = sum(b > a for a, b in zip(values, values[1:]))
    return max(0.0, min(100.0, 100 * (1 - values[-1] / values[0]) - 10 * increases))


def _swings(pivots, bars, tr, atr, cfg):
    result = []
    for high, low in zip(pivots, pivots[1:]):
        if high["kind"] != "high" or low["kind"] != "low":
            continue
        drawdown = (high["price"] - low["price"]) / high["price"] * 100
        if drawdown < cfg["min_swing_pct"]:
            continue
        a, b = high["index"], low["index"]
        stage = bars[a:b + 1]
        values = [row["volume"] for row in stage if row["volume"] is not None]
        up, down = [], []
        for j in range(a, b + 1):
            if j and bars[j]["volume"] is not None:
                if bars[j]["close"] > bars[j - 1]["close"]:
                    up.append(bars[j]["volume"])
                elif bars[j]["close"] < bars[j - 1]["close"]:
                    down.append(bars[j]["volume"])
        valid_atr = [v for v in atr[a:b + 1] if v is not None]
        result.append({
            "high_date": high["date"], "high": high["price"],
            "high_confirmed_at": high["confirmed_at"],
            "low_date": low["date"], "low": low["price"],
            "low_confirmed_at": low["confirmed_at"], "confirmed_at": low["confirmed_at"],
            "drawdown_pct": drawdown, "high_index": a, "low_index": b,
            "bars": len(stage), "volume_mean": mean(values) if values else None,
            "volume_median": median(values) if values else None,
            "volume_coverage": len(values), "volume_complete": len(values) == len(stage),
            "up_day_volume_mean": mean(up) if up else None,
            "down_day_volume_mean": mean(down) if down else None,
            "true_range_mean": mean(tr[a:b + 1]),
            "true_range_pct_mean": mean(tr[j] / bars[j]["close"] * 100 for j in range(a, b + 1)),
            "atr_mean": mean(valid_atr) if valid_atr else None,
            "atr_pct_mean": mean(atr[j] / bars[j]["close"] * 100 for j in range(a, b + 1) if atr[j] is not None) if valid_atr else None,
            "atr_coverage": len(valid_atr),
            "price_range_pct": (max(row["high"] for row in stage) - min(row["low"] for row in stage)) / max(row["high"] for row in stage) * 100,
        })
    return result


def _geometry(swings, cfg):
    if not swings:
        return [], {}, ["尚无已确认的高点至低点收缩波段。"]
    chosen = swings[-cfg["max_contractions"]:]
    reasons = []
    # Prefer the longest qualifying suffix; older unrelated bases cannot supply
    # an extra contraction when the resulting span exceeds the configured base.
    for length in range(len(chosen), cfg["min_contractions"] - 1, -1):
        candidate = chosen[-length:]
        ds = [s["drawdown_pct"] for s in candidate]
        span = candidate[-1]["low_index"] - candidate[0]["high_index"] + 1
        if (cfg["min_base_bars"] <= span <= cfg["max_base_bars"]
                and ds[-1] <= cfg["max_last_drawdown_pct"] and ds[-1] < ds[0]
                and all(b <= a + cfg["contraction_tolerance_pct"] for a, b in zip(ds, ds[1:]))):
            chosen = candidate
            break
    ds = [s["drawdown_pct"] for s in chosen]
    span = chosen[-1]["low_index"] - chosen[0]["high_index"] + 1
    if len(chosen) < cfg["min_contractions"]:
        reasons.append("已确认收缩次数不足。")
    if not cfg["min_base_bars"] <= span <= cfg["max_base_bars"]:
        reasons.append("整理持续时间不在配置范围内。")
    if ds[-1] > cfg["max_last_drawdown_pct"]:
        reasons.append("最后一次回撤仍过宽。")
    if len(ds) < 2 or ds[-1] >= ds[0] or any(b > a + cfg["contraction_tolerance_pct"] for a, b in zip(ds, ds[1:])):
        reasons.append("价格回撤未在容差内总体收缩。")
    volumes = [s["volume_mean"] if s["volume_complete"] else None for s in chosen]
    volatility = [s["true_range_pct_mean"] for s in chosen]
    atr_values = [s["atr_pct_mean"] if s["atr_coverage"] == s["bars"] else None for s in chosen]
    metrics = {"contraction_count": len(chosen), "base_bars": span,
               "price_contraction_score": _score(ds), "volume_contraction_score": _score(volumes),
               "atr_contraction_score": _score(atr_values), "drawdowns_pct": ds,
               "atr_contraction_basis": "stage_mean_atr_pct", "stage_atr_pct": atr_values,
               "true_range_contraction_score": _score(volatility),
               "stage_volume_means": volumes, "stage_volume_medians": [s["volume_median"] for s in chosen],
               "stage_true_range_means": [s["true_range_mean"] for s in chosen],
               "stage_volatility_pct": volatility,
               "volume_data_complete": all(s["volume_complete"] for s in chosen),
               "volume_shrinking": all(v is not None for v in volumes) and len(volumes) >= 2 and volumes[-1] < volumes[0] and all(b <= a for a, b in zip(volumes, volumes[1:])),
               "price_qualified": not reasons}
    return chosen, metrics, reasons


def _volume_ratio(bars, index, window):
    if index < window:
        return None
    previous = [bar["volume"] for bar in bars[index - window:index]]
    current = bars[index]["volume"]
    if current is None or any(v is None for v in previous) or mean(previous) <= 0:
        return None
    return current / mean(previous)


def analyze_vcp(bars, config=None):
    """Return a JSONable point-in-time analysis and immutable observation log.

    ``config`` contains flat VCP keys from DEFAULTS.  Optional ``as_of`` rejects
    accidental future input.  Daily bars must already be closed.  ``swings``
    reports the frozen current setup, or the current forming candidate; event
    payloads retain every earlier detected setup without retrospective edits.
    """
    cfg = _config(config)
    bars = _bars(bars, cfg.get("as_of"))
    if not bars:
        return {"as_of": None, "analysis_version": ANALYSIS_VERSION,
                "state": "incomplete", "score": None, "pivot": None, "risk_level": None,
                "setup_id": None, "setup_detected_at": None, "pivot_known_at": None,
                "swings": [], "metrics": {}, "breakout": None, "breakout_history": [], "events": [],
                "reasons": ["没有可分析的完整日线。"]}
    tr, atr = _ranges(bars, cfg["atr_period"])
    events, pivots, seen_setups = [], [], set()
    active, last_setup, latest_breakout = None, None, None
    cycle_start_index = 0
    state, current_reasons, candidate_swings, candidate_metrics = "incomplete", [], [], {}

    def emit(kind, index, **payload):
        stamp = bars[index]["date"]
        events.append(deepcopy({"event_id": str(len(events)), "type": kind, "date": stamp,
                               "detect_date": stamp, "observed_at": stamp, **payload}))

    def transition(next_state, index, **extra):
        nonlocal state
        if state != next_state:
            state = next_state
            emit("state_changed", index, state=state,
                 setup_id=active["id"] if active else None, **extra)

    for i, bar in enumerate(bars):
        current_reasons = []
        if active:
            reference = active.get("breakout_index", active["detected_index"])
            if i - reference > cfg["max_signal_age_bars"]:
                transition("expired", i, reason="信号超过配置的交易日寿命。")
                last_setup, active = active, None
                current_reasons.append("信号已过期，不作为当前突破机会。")
            elif i > active["detected_index"]:
                pivot, risk = active["pivot"], active["risk_level"]
                if active.get("confirmed_breakout"):
                    failure = max(risk, pivot * (1 - cfg["failure_buffer_pct"] / 100))
                    if bar["close"] < failure:
                        transition("failed", i, reason="突破后收盘跌破枢轴失败缓冲或结构低点。")
                        last_setup, active = active, None
                    elif (i > active["breakout_index"] and bar["low"] <= pivot * (1 + cfg["retest_tolerance_pct"] / 100)
                          and bar["close"] >= pivot and (ratio := _volume_ratio(bars, i, cfg["volume_lookback"])) is not None and ratio < 1):
                        transition("retest", i, volume_ratio=ratio)
                    else:
                        transition("breakout", i)
                        if bar["close"] < pivot:
                            current_reasons.append("收盘暂低于枢轴，但尚未达到配置的突破失败缓冲。")
                elif bar["close"] < risk:
                    transition("invalidated", i, reason="突破前收盘跌破最后结构低点。")
                    last_setup, active = active, None
                elif active["armed_index"] is None:
                    transition("forming", i)
                    if bar["close"] <= pivot:
                        active["armed_index"] = i
                        emit("setup_armed", i, setup_id=active["id"], pivot=pivot)
                        transition("ready" if active["metrics"]["volume_data_complete"] else "forming", i)
                    else:
                        current_reasons.append("识别时已高于枢轴，不能追认此前突破；等待重新形成可观察的突破条件。")
                elif i > active["armed_index"]:
                    threshold = pivot * (1 + cfg["breakout_buffer_pct"] / 100)
                    if bar["close"] > threshold:
                        ratio = _volume_ratio(bars, i, cfg["volume_lookback"])
                        ambiguous = bar["low"] < risk
                        confirmed = (ratio is not None and ratio >= cfg["breakout_volume_ratio"]
                                     and active["metrics"]["volume_data_complete"] and not ambiguous)
                        extension = (bar["close"] / pivot - 1) * 100
                        quality = (min(100.0, ratio / cfg["breakout_volume_ratio"] * 100) + max(0.0, 100 - extension * 10)) / 2 if confirmed else None
                        attempt = {"date": bar["date"], "observed_at": bar["date"], "high": bar["high"], "close": bar["close"],
                                   "volume_ratio": ratio, "volume_confirmed": confirmed,
                                   "quality": quality, "quality_scope": "known_close_volume_and_extension_only",
                                   "extension_pct": extension, "pivot_known_at": active["detected_at"],
                                   "intrabar_sequence_ambiguous": ambiguous}
                        active.setdefault("first_price_break_date", bar["date"])
                        attempt["first_price_break_date"] = active["first_price_break_date"]
                        latest_breakout = attempt
                        if confirmed:
                            active["confirmed_breakout"] = True
                            active["breakout_index"] = i
                            # A confirmed departure ends this base. Later bases
                            # must earn their own contractions after departure.
                            cycle_start_index = i
                            emit("breakout_confirmed", i, setup_id=active["id"], pivot=pivot, breakout=attempt)
                            transition("breakout", i)
                        else:
                            transition("unconfirmed_breakout", i)
                            current_reasons.append("同一日同时触及结构低点与突破价，日线无法确定先后。" if ambiguous else "收盘已越过枢轴，但成交量证据不足或未达到放量阈值。")
                    elif bar["high"] > threshold:
                        transition("intraday_breakout", i)
                        current_reasons.append("当日最高价曾越过枢轴，收盘未有效站上；不等同实时信号。")
                    else:
                        transition("ready" if active["metrics"]["volume_data_complete"] else "forming", i)

        # Extrema are confirmed only after every bar in their right window.
        w, center = cfg["swing_window"], i - cfg["swing_window"]
        changed = False
        if center >= w:
            window = bars[center - w:center + w + 1]
            others = window[:w] + window[w + 1:]
            high = all(bars[center]["high"] > x["high"] for x in others)
            low = all(bars[center]["low"] < x["low"] for x in others)
            if high and low:
                emit("ambiguous_extremum", i, extreme_date=bars[center]["date"],
                     reason="同一日同时为高低极值，日线无法确定先后，不用于波段。")
            elif high or low:
                point = {"kind": "high" if high else "low", "index": center,
                         "date": bars[center]["date"], "price": bars[center]["high" if high else "low"],
                         "confirmed_at": bar["date"]}
                emit("extremum_confirmed", i, extremum=point)
                if not pivots or pivots[-1]["kind"] != point["kind"]:
                    pivots.append(point); changed = True
                elif (point["kind"] == "high" and point["price"] > pivots[-1]["price"] or
                      point["kind"] == "low" and point["price"] < pivots[-1]["price"]):
                    pivots[-1] = point; changed = True

        if changed:
            fresh_swings = [s for s in _swings(pivots, bars, tr, atr, cfg)
                            if s["high_index"] >= cycle_start_index]
            candidate_swings, candidate_metrics, candidate_reasons = _geometry(fresh_swings, cfg)
            # Evaluate a close against yesterday's frozen pivot before accepting
            # new extrema. A low confirmed on a breakout bar cannot erase that
            # breakout by lifting the pivot or disarming the existing setup.
            preserve_departure = active and (active.get("confirmed_breakout") or
                active.get("first_price_break_date") and bar["close"] > active["pivot"])
            if candidate_metrics.get("price_qualified") and not preserve_departure:
                identity = "|".join(s["high_date"] + ":" + s["low_date"] for s in candidate_swings)
                if identity not in seen_setups:
                    seen_setups.add(identity)
                    if active:
                        emit("setup_superseded", i, setup_id=active["id"], replacement_id=identity)
                    frozen = deepcopy(candidate_swings)
                    pivot, risk = frozen[-1]["high"], frozen[-1]["low"]
                    active = {"id": identity, "detected_index": i, "detected_at": bar["date"],
                              "armed_index": i if bar["close"] <= pivot else None,
                              "pivot": pivot, "risk_level": risk, "swings": frozen,
                              "metrics": deepcopy(candidate_metrics), "confirmed_breakout": False}
                    latest_breakout = None
                    state = "ready" if active["armed_index"] is not None and candidate_metrics["volume_data_complete"] else "forming"
                    emit("setup_detected", i, setup_id=identity, state=state, pivot=pivot,
                         risk_level=risk, swings=frozen, metrics=candidate_metrics)
            if not active and state not in ("expired", "failed", "invalidated"):
                transition("forming" if candidate_swings else "incomplete", i)
                current_reasons.extend(candidate_reasons)

    setup = active or last_setup
    selected = setup["swings"] if setup else candidate_swings
    metrics = deepcopy(setup["metrics"] if setup else candidate_metrics)
    if atr[-1] is not None:
        metrics.update(atr=atr[-1], atr_pct=atr[-1] / bars[-1]["close"] * 100,
                       atr_period=cfg["atr_period"])
        if cfg["atr_period"] == 14:
            metrics.update(atr14=metrics["atr"], atr14_pct=metrics["atr_pct"])
    if setup:
        metrics["distance_to_pivot_pct"] = (bars[-1]["close"] / setup["pivot"] - 1) * 100
        metrics["setup_age_bars"] = len(bars) - 1 - setup["detected_index"]
        metrics["signal_age_bars"] = len(bars) - 1 - setup.get("breakout_index", setup["detected_index"])
    metrics["setup_active"] = active is not None
    metrics.update(current_high=bars[-1]["high"], current_close=bars[-1]["close"],
                   current_volume_ratio=_volume_ratio(bars, len(bars) - 1, cfg["volume_lookback"]),
                   volume_lookback=cfg["volume_lookback"],
                   high_above_pivot=bars[-1]["high"] > setup["pivot"] if setup else None,
                   close_above_pivot=bars[-1]["close"] > setup["pivot"] if setup else None)
    if selected and not metrics.get("volume_data_complete", False):
        current_reasons.append("收缩阶段成交量缺失，成交量收缩评分不具备完整依据。")
    elif len(selected) >= 2 and metrics.get("volume_shrinking") is False:
        current_reasons.append("阶段成交量没有连续收缩，不能仅凭价格收缩视为高质量VCP。")
    terminal = {"failed": "突破后已跌破失败缓冲或结构低点；此前突破事件仍保留。",
                "invalidated": "突破前结构已被收盘跌破最后低点破坏。",
                "expired": "信号已超过配置的交易日寿命，不作为当前突破机会。"}
    if state in terminal:
        current_reasons.append(terminal[state])
    if active and active["armed_index"] is None:
        current_reasons.append("识别时已高于枢轴，不能追认此前突破；等待重新形成可观察的突破条件。")
    if not current_reasons and not setup:
        current_reasons = _geometry(_swings(pivots, bars, tr, atr, cfg), cfg)[2]
    component = [metrics.get(k) for k in ("price_contraction_score", "volume_contraction_score", "atr_contraction_score")]
    weights = [cfg["score_weights"][key] for key in ("price", "volume", "atr")]
    score = sum(v * w for v, w in zip(component, weights) if w) / sum(weights) if all(v is not None or not w for v, w in zip(component, weights)) else None
    history = [{"date": e["date"], "first_price_break_date": e["breakout"]["first_price_break_date"],
                "pivot": e["pivot"], "volume_ratio": e["breakout"]["volume_ratio"],
                "setup_id": e["setup_id"]}
               for e in events if e["type"] == "breakout_confirmed"]
    return {"as_of": bars[-1]["date"], "analysis_version": ANALYSIS_VERSION,
            "state": state, "score": score,
            "pivot": setup["pivot"] if setup else None, "risk_level": setup["risk_level"] if setup else None,
            "setup_id": setup["id"] if setup else None, "setup_detected_at": setup["detected_at"] if setup else None,
            "pivot_known_at": setup["detected_at"] if setup else None,
            "swings": deepcopy(selected), "metrics": metrics, "breakout": deepcopy(latest_breakout),
            "breakout_history": history, "events": events,
            "reasons": list(dict.fromkeys(current_reasons))}
