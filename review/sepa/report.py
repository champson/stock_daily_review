"""Run independently sourced, date-matched SEPA and VCP checks per candidate."""

import hashlib
import json
from datetime import date
from pathlib import Path

from .sources import load_bars, normalize_symbol
from .trend import analyze_trend, build_market_rs
from .vcp import analyze_vcp


def read_json(path):
    def reject_nonfinite(value):
        raise ValueError(f"JSON 中有非有限数值 {value}")

    return json.loads(Path(path).read_text(encoding="utf-8-sig"), parse_constant=reject_nonfinite)


def write_json(path, payload):
    with Path(path).open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")


def validate_date(value):
    try:
        if date.fromisoformat(value).isoformat() == value:
            return value
    except (TypeError, ValueError):
        pass
    raise ValueError("报告日期必须为 YYYY-MM-DD")


def load_market_rs(path, as_of, trend_config):
    snapshot = read_json(path)
    if not isinstance(snapshot, dict) or snapshot.get("date") != as_of:
        raise ValueError("市场收益快照必须为同报告日的 JSON 对象")
    rows = snapshot.get("rows")
    universe = snapshot.get("market_universe")
    if not isinstance(rows, list) or not isinstance(universe, dict):
        raise ValueError("市场收益快照缺少 rows 或 market_universe")
    if any(not isinstance(row, dict) or normalize_symbol(row.get("symbol")) != row.get("symbol") for row in rows):
        raise ValueError("市场收益快照必须使用带市场前缀的规范 A 股代码")
    result = build_market_rs(as_of, rows, {**trend_config, "market_universe": universe})
    context = {key: value for key, value in result.items() if key != "rows"}
    context.update(snapshot_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                   source_provenance=snapshot.get("provenance", []),
                   source_observations=snapshot.get("observations", []),
                   anchors=snapshot.get("anchors"))
    evidence = {key: result[key] for key in ("universe", "coverage")}
    return context, {row["symbol"]: {**row, **evidence} for row in result["rows"]}


def classify(trend, vcp):
    if trend["status"] == "fail":
        return "D"
    if trend["status"] != "pass":
        return "insufficient"
    if vcp["state"] in {"failed", "invalidated", "expired"}:
        return "D"
    if vcp["state"] == "incomplete":
        return "insufficient"
    metrics = vcp["metrics"]
    if metrics.get("price_qualified") and not metrics.get("volume_data_complete"):
        return "insufficient"
    if vcp["state"] == "unconfirmed_breakout" and (vcp.get("breakout") or {}).get("volume_ratio") is None:
        return "insufficient"
    if vcp["state"] in {"ready", "breakout", "retest"}:
        if metrics.get("price_qualified") and metrics.get("volume_shrinking"):
            return "A" if vcp["state"] == "ready" else "B"
    return "C"


def require_config(config):
    if not isinstance(config, dict) or set(config) != {"history_bars", "trend", "vcp"}:
        raise ValueError("配置需要 history_bars、trend 和 vcp")
    count = config["history_bars"]
    if type(count) is not int or not 253 <= count <= 640:
        raise ValueError("history_bars 必须在 253 至 640 之间")
    if not isinstance(config["trend"], dict) or not isinstance(config["vcp"], dict):
        raise ValueError("trend 和 vcp 必须为对象")
    # Resolve invalid parameter values up front, including when all quotes fail.
    analyze_trend([], config["trend"])
    analyze_vcp([], config["vcp"])


def _rank_order(row):
    percentile = (row.get("rs") or {}).get("percentile")
    return percentile is None, -(percentile or 0), row["symbol"]


def build_report(as_of, candidates, config, output_dir, *, price_dir=None,
                 market_returns=None, charts=False):
    require_config(config)
    as_of = validate_date(as_of)
    directory = Path(output_dir)
    if market_returns is None:
        market, rs_rows = {"status": "unavailable", "date": as_of,
                           "observations": ["未提供同日市场收益快照，市场 RS 检查标记为数据不足。"]}, {}
    else:
        market, rs_rows = load_market_rs(market_returns, as_of, config["trend"])

    if charts:
        from .chart import write_chart
        (directory / "charts").mkdir(parents=True, exist_ok=True)

    rows = []
    for candidate in candidates:
        symbol = candidate["symbol"]
        try:
            instrument = load_bars(symbol, as_of, price_dir, config["history_bars"],
                                   capture_dir=directory / "quotes")
        except (ValueError, OSError, RuntimeError, ImportError) as exc:
            rows.append({"candidate": candidate, "symbol": symbol, "category": "unavailable",
                         "reason": str(exc), "error_type": type(exc).__name__})
            continue

        trend = analyze_trend(instrument["bars"], {**config["trend"], "as_of": as_of, "symbol": symbol},
                              rs_rows.get(symbol))
        vcp = analyze_vcp(instrument["bars"], {**config["vcp"], "as_of": as_of})
        quote_meta = {key: value for key, value in instrument.items() if key != "bars"}
        if "raw_capture_path" in quote_meta:
            quote_meta["raw_capture_path"] = str(Path(quote_meta["raw_capture_path"]).relative_to(directory))
        quote_meta["count"] = len(instrument["bars"])
        quote_meta["first_bar_date"] = instrument["bars"][0]["date"]
        row = {"candidate": candidate, "symbol": symbol, "close": instrument["bars"][-1]["close"],
               "category": classify(trend, vcp), "quote": quote_meta, "trend": trend, "vcp": vcp,
               "rs": rs_rows.get(symbol)}
        if charts:
            chart_path = Path("charts") / f"{symbol}.png"
            write_chart(directory / chart_path, instrument, row)
            row["chart"] = chart_path.as_posix()
        rows.append(row)

    grouped = {category: [] for category in ("A", "B", "C", "D", "insufficient", "unavailable")}
    for row in rows:
        grouped[row["category"]].append(row)
    for members in grouped.values():
        members.sort(key=_rank_order)
    status = "available"
    if grouped["unavailable"] or grouped["insufficient"]:
        status = "partial" if len(grouped["unavailable"]) < len(rows) else "unavailable"

    return {"schema_version": 1, "date": as_of, "status": status,
            "config": config, "market_rs": market,
            "ranking_method": "同一阶段按可用的市场 RS 百分位排序，同值按股票代码排序。",
            "groups": {category: [row["symbol"] for row in members] for category, members in grouped.items()},
            "rows": [row for members in grouped.values() for row in members]}
