"""确定性指标；金额为元、涨跌幅为百分数、IV 为小数。"""
import math
from collections import defaultdict
from datetime import date

LABELS = {"indices": "指数", "stocks": "A股", "etfs": "ETF", "futures": "股指期货", "options": "期权"}
NUMERIC = {"close", "previous_close", "previous_settlement", "amount", "volume", "nav", "spot", "settlement", "open_interest", "strike", "implied_volatility"}
NONNEGATIVE = NUMERIC - {"close", "previous_close", "previous_settlement", "spot", "settlement"}


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def pct(current, previous):
    return round((current / previous - 1) * 100, 4) if number(current) and number(previous) and previous > 0 else None


def validate_rows(rows, trading_date, kind):
    date.fromisoformat(trading_date)
    if not isinstance(rows, list):
        raise ValueError("数据集必须为数组")
    seen, valid, rejected = set(), [], []
    for i, row in enumerate(rows):
        reason = None
        if not isinstance(row, dict):
            reason = "记录不是对象"
        elif row.get("date") != trading_date:
            reason = "行情日期与报告日期不同"
        elif not isinstance(row.get("symbol"), str) or not row["symbol"]:
            reason = "缺少证券代码"
        elif row["symbol"] in seen:
            reason = "代码重复"
        elif not number(row.get("close")) or row["close"] < 0 or (kind != "options" and row["close"] == 0):
            reason = "缺少有效收盘价"
        elif any(v is not None and (not number(v) or (k in NONNEGATIVE and v < 0)) for k, v in row.items() if k in NUMERIC):
            reason = "数值无效"
        elif kind == "options" and row.get("option_type") not in ("call", "put"):
            reason = "期权类型必须为 call 或 put"
        if reason:
            rejected.append({"row": i, "reason": reason})
        else:
            seen.add(row["symbol"])
            valid.append(row)
    return valid, rejected


def aggregate(rows, field):
    values = [r[field] for r in rows if number(r.get(field))]
    return {"value": sum(values) if values else None, "coverage": len(values), "total": len(rows)}


def analyze(kind, rows, complete=False):
    entries, metrics, observations = [], {}, []
    for r in rows:
        item = dict(r)
        if kind == "futures":
            item["change_pct"] = pct(r.get("close"), r.get("previous_settlement"))
            item["basis"] = round(r["close"] - r["spot"], 4) if number(r.get("close")) and number(r.get("spot")) and r.get("spot_date") == r.get("date") else None
            item["basis_pct"] = pct(r.get("close"), r.get("spot")) if r.get("spot_date") == r.get("date") else None
        else:
            item["change_pct"] = pct(r.get("close"), r.get("previous_close"))
        if kind == "etfs":
            item["premium_pct"] = pct(r.get("close"), r.get("nav")) if r.get("nav_date") == r.get("date") else None
        if kind in ("indices", "stocks", "etfs"):
            item["trend"] = historical_metrics(r)
        entries.append(item)
    if kind in ("indices", "stocks", "etfs"):
        changes = [r["change_pct"] for r in entries if number(r.get("change_pct"))]
        metrics["breadth"] = {"advances": sum(c > 0 for c in changes), "declines": sum(c < 0 for c in changes), "unchanged": sum(c == 0 for c in changes), "coverage": len(changes), "total": len(rows), "universe_complete": complete}
        metrics["amount"] = aggregate(rows, "amount")
        if changes:
            observations.append(f"有效样本 {len(changes)} 个，上涨 {sum(c > 0 for c in changes)} 个，下跌 {sum(c < 0 for c in changes)} 个。" + ("" if complete else "该统计仅代表已获取样本。"))
    if kind == "stocks":
        metrics["sectors"] = sector_metrics(entries)
        observations.append("板块涨跌为已获取成分股的等权平均，分类来自行情源；并非板块指数涨跌幅。")
    if kind == "options":
        groups = defaultdict(list)
        for r in rows:
            groups[(r.get("underlying") or "未标明标的", r.get("expiry") or "未标明到期日")].append(r)
        metrics["groups"] = []
        for (underlying, expiry), contracts in groups.items():
            group = {"underlying": underlying, "expiry": expiry, "contracts": len(contracts)}
            for field in ("volume", "open_interest"):
                calls = [r for r in contracts if r["option_type"] == "call"]
                puts = [r for r in contracts if r["option_type"] == "put"]
                c, p = aggregate(calls, field), aggregate(puts, field)
                covered = bool(calls and puts) and c["coverage"] == c["total"] and p["coverage"] == p["total"]
                group[field + "_pcr"] = round(p["value"] / c["value"], 4) if covered and c["value"] > 0 else None
                group[field + "_coverage"] = c["coverage"] + p["coverage"]
            metrics["groups"].append(group)
        observations.append("PCR 按标的与到期日分别统计；缺少认购、认沽或字段时不计算。IV 仅展示数据源提供的数值。")
    if kind == "futures":
        observations.append("涨跌幅相对前结算价；基差 = 期货收盘价 − 同日标的指数价格。")
    return {"label": LABELS[kind], "status": "available" if entries else "unavailable", "sample_size": len(entries), "universe_complete": complete, "metrics": metrics, "rows": entries, "observations": observations}


def historical_metrics(row):
    history = row.get("history")
    if not isinstance(history, list) or not history:
        return None
    try:
        dates = [date.fromisoformat(h["date"]) for h in history]
        if dates != sorted(set(dates)) or history[-1]["date"] != row["date"]:
            return None
        if any(not number(h.get("close")) or h["close"] <= 0 for h in history):
            return None
        if history[-1]["close"] != row.get("close"):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    result = {"days": len(history), "price_basis": row.get("price_basis", "数据源口径"), "ma5": None, "ma20": None, "ma60": None, "amount_ratio_5d": None}
    for window in (5, 20, 60):
        if len(history) >= window:
            result["ma" + str(window)] = round(sum(h["close"] for h in history[-window:]) / window, 4)
    if len(history) >= 6 and all(number(h.get("amount")) and h["amount"] >= 0 for h in history[-6:]):
        base = sum(h["amount"] for h in history[-6:-1]) / 5
        if base > 0:
            result["amount_ratio_5d"] = round(history[-1]["amount"] / base, 4)
    return result


def sector_metrics(entries):
    sectors = defaultdict(list)
    for row in entries:
        if isinstance(row.get("sector"), str) and row["sector"]:
            sectors[row["sector"]].append(row)
    results = []
    for sector, rows in sectors.items():
        changes = [r["change_pct"] for r in rows if number(r.get("change_pct"))]
        results.append({"sector": sector, "sample_size": len(rows), "change_coverage": len(changes), "equal_weight_change_pct": round(sum(changes) / len(changes), 4) if changes else None, "advances": sum(c > 0 for c in changes), "amount": aggregate(rows, "amount")})
    return sorted(results, key=lambda r: (r["equal_weight_change_pct"] is not None, r["equal_weight_change_pct"] or 0), reverse=True)
