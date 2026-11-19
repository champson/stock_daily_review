"""Industry/theme index panel: natural-log EMA20, never ETF proxies.

Formula: https://xueqiu.com/3559889031/354857867 (the final executable formula).
Source histories are price-index points in each index's published currency.
"""
import json
import math
import re
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .index_history_fallbacks import _read_json, EASTMONEY_URL

METHOD = "ln-close-minus-ema20-ln-close-v1"
FORMULA_URL = "https://xueqiu.com/3559889031/354857867"
MIN_HISTORY = 250
DISPLAY_DAYS = 10
MAX_HISTORY = 640


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def log_ema_series(history):
    """Seed with first ln(close); emit only after 250 observed daily bars."""
    result, previous, ema = [], "", None
    for i, item in enumerate(history):
        stamp, close = item.get("date"), item.get("close")
        if (not isinstance(stamp, str) or date.fromisoformat(stamp).isoformat() != stamp
                or stamp <= previous or not positive(close)):
            raise ValueError("板块历史日期乱序、重复或收盘价无效")
        previous = stamp
        value = math.log(close)
        ema = value if ema is None else (2 / 21) * value + (19 / 21) * ema
        result.append({"date": stamp, "value": 100 * (value - ema) if i + 1 >= MIN_HISTORY else None})
    return result


def fetch_history(spec, start, end):
    """Explicit verified secid and name; no stock-prefix inference."""
    market, code = spec["secid"].split(".", 1)
    payload, url, fetched_at = _read_json(EASTMONEY_URL, {
        "secid": spec["secid"], "fields1": "f1,f2,f3,f4,f5",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58", "klt": "101", "fqt": "0",
        "beg": start.replace("-", ""), "end": end.replace("-", ""),
    })
    data = payload.get("data")
    if payload.get("rc", 0) != 0 or not isinstance(data, dict):
        raise ValueError("指数历史源未返回有效数据")
    if str(data.get("code")) != code or str(data.get("market")) != market:
        raise ValueError("指数历史返回代码或市场与配置不一致")
    name = data.get("name")
    if name not in spec["source_names"]:
        raise ValueError(f"指数历史返回名称未通过核验：{name}")
    raw = data.get("klines")
    if not isinstance(raw, list) or not raw:
        raise ValueError("指数历史为空")
    history, previous = [], ""
    for line in raw:
        parts = line.split(",") if isinstance(line, str) else []
        if len(parts) != 8:
            raise ValueError("指数历史字段格式改变")
        stamp = parts[0]
        if date.fromisoformat(stamp).isoformat() != stamp or not start <= stamp <= end or stamp <= previous:
            raise ValueError("指数历史日期越界、乱序或重复")
        close = float(parts[2])
        if not positive(close):
            raise ValueError("指数收盘价无效")
        previous = stamp
        history.append({"date": stamp, "close": close})
    return history[-MAX_HISTORY:], {"provider": "eastmoney", "source_url": url,
                                    "fetched_at": fetched_at, "source_name": name}


@lru_cache(maxsize=2000)
def hk_calendar(day):
    year = date.fromisoformat(day).year
    path = Path(__file__).parents[1] / "config" / "hk-calendars" / f"{year}.json"
    if not path.exists():
        raise ValueError(f"{year} 年港交所交易日历尚未核验")
    data = json.loads(path.read_text())
    if data.get("year") != year or data.get("source") != "HKEX" or not data.get("source_url", "").startswith("https://www.hkex.com.hk/"):
        raise ValueError("港交所年度日历归档无效")
    holidays = data.get("holidays")
    if not isinstance(holidays, list) or len(holidays) < 10 or len(set(holidays)) != len(holidays):
        raise ValueError("港交所年度日历不完整")
    if any(date.fromisoformat(d).year != year for d in holidays):
        raise ValueError("港交所年度日历错年")
    return {"date": day, "open": date.fromisoformat(day).weekday() < 5 and day not in holidays,
            "source": data["source"], "source_url": data["source_url"], "fetched_at": data["fetched_at"]}


CSI_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
CNI_URL = "https://hq.cnindex.com.cn/market/market/getIndexDailyDataWithDataFormat"


def fetch_hk_history(spec, start, end, sessions):
    """Use issuer data for CSI/CNI: Eastmoney omits HK-only trading days."""
    if spec["issuer"] == "HSI":
        history, provenance = fetch_history(spec, start, end)
    else:
        csi = spec["issuer"] == "CSI"
        params = {"indexCode": spec["code"], "startDate": start.replace("-", "") if csi else start,
                  "endDate": end.replace("-", "") if csi else end}
        if not csi:
            params["frequency"] = "day"
        payload, url, fetched_at = _read_json(CSI_URL if csi else CNI_URL, params)
        if str(payload.get("code")) != "200":
            raise ValueError("指数公司历史接口返回错误码")
        history = []
        if csi:
            raw = payload.get("data")
            if not isinstance(raw, list) or not raw:
                raise ValueError("中证指数历史为空")
            for p in raw:
                if p.get("indexCode") != spec["code"] or p.get("indexNameCnAll") != spec["official_name"]:
                    raise ValueError("中证指数返回身份与配置不符")
                stamp = str(p.get("tradeDate", ""))
                if not re.fullmatch(r"\d{8}", stamp):
                    raise ValueError("中证指数日期格式无效")
                history.append({"date": stamp[:4]+"-"+stamp[4:6]+"-"+stamp[6:], "close": p.get("close")})
            source_name = raw[0]["indexNameCnAll"]
        else:
            data = payload.get("data")
            if not isinstance(data, dict) or data.get("indexCode") != spec["code"] or data.get("indexName") not in spec["source_names"]:
                raise ValueError("国证指数返回身份与配置不符")
            fields, raw = data.get("item"), data.get("data")
            if not isinstance(fields, list) or not isinstance(raw, list) or not raw or "timestamp" not in fields or "close" not in fields:
                raise ValueError("国证指数历史格式改变或为空")
            for p in raw:
                if not isinstance(p, list) or len(p) != len(fields):
                    raise ValueError("国证指数日线字段不完整")
                history.append({"date": p[fields.index("timestamp")], "close": float(p[fields.index("close")])})
            history.reverse()  # the documented issuer endpoint returns descending dates
            source_name = data["indexName"]
        provenance = {"provider": "csindex" if csi else "cnindex", "source_url": url,
                      "fetched_at": fetched_at, "source_name": source_name}
    previous = ""
    for h in history:
        stamp = h["date"]
        if (not isinstance(stamp, str) or date.fromisoformat(stamp).isoformat() != stamp
                or not start <= stamp <= end or stamp <= previous or not positive(h["close"])):
            raise ValueError("港股指数历史日期或收盘无效")
        previous = stamp
    # Some issuer feeds include holiday observations. They do not count as EMA sessions.
    expected = [d for d in sessions if history and d >= history[0]["date"]]
    by_date = {r["date"]: r for r in history}
    if not expected or any(d not in by_date for d in expected):
        raise ValueError("港股指数预热历史存在交易日缺口，不能跳过缺日计算EMA")
    return [by_date[d] for d in expected][-MAX_HISTORY:], provenance


def session_window(day, calendar):
    sessions, proofs = [], {}
    for offset in range(65):
        stamp = (date.fromisoformat(day) - timedelta(days=offset)).isoformat()
        entry = calendar(stamp)
        if entry.get("date") != stamp or type(entry.get("open")) is not bool:
            raise ValueError("板块交易日历格式无效")
        proofs[entry["source_url"]] = {k: entry[k] for k in ("source", "source_url", "fetched_at")}
        if entry["open"]:
            sessions.append(stamp)
            if len(sessions) == DISPLAY_DAYS + 1:
                return sessions, list(proofs.values())
    raise ValueError("没有取得完整的最近10个交易日及前收盘基准日")


def empty_board(day, message):
    return {"version": 1, "date": day, "status": "unavailable", "method": METHOD,
            "formula_url": FORMULA_URL, "period": 20, "minimum_history": MIN_HISTORY,
            "groups": [], "observations": [message], "provenance": []}


def collect_sector_board(adapter, day):
    specs = adapter.settings.get("sector_board", [])
    if not specs:
        return empty_board(day, "板块观察池尚未配置。")
    seen = set()
    for item in specs:
        if (not isinstance(item, dict) or item.get("group") not in ("CN", "HK")
                or not re.fullmatch(r"\d+\.[A-Za-z0-9]+", item.get("secid", ""))
                or not all(isinstance(item.get(k), str) and item[k] for k in ("name", "official_name", "code", "issuer", "currency", "identity_url"))
                or not isinstance(item.get("source_names"), list) or not item["source_names"]
                or item["secid"] in seen):
            raise ValueError("板块指数配置身份无效或重复")
        seen.add(item["secid"])
    board = empty_board(day, "按指数本币价格点位计算；观察池包含行业与主题，不代表全部行业，不按个股等权。")
    start = (date.fromisoformat(day) - timedelta(days=1000)).isoformat()
    for key, name, calendar in (("CN", "A 股行业与主题", adapter.calendar), ("HK", "港股行业与主题", hk_calendar)):
        group_specs = [s for s in specs if s["group"] == key]
        if not group_specs:
            continue
        group = {"key": key, "name": name, "dates": [], "previous_date": None,
                 "configured_count": len(group_specs), "sample_size": 0, "status": "unavailable", "history_dates": [],
                 "rows": [], "observations": []}
        board["groups"].append(group)
        try:
            window, provenance = session_window(day, calendar)
            group["dates"] = window[:DISPLAY_DAYS]  # latest on the left
            group["previous_date"] = window[1]
            board["provenance"].extend(dict(p, dataset="sector_board_calendar", group=key) for p in provenance)
        except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
            window = []
            group["observations"].append(f"交易日历缺失：{exc}")

        history_start = max(start, "2025-01-01") if key == "HK" else start
        if window and key == "HK":
            cursor = date.fromisoformat(history_start)
            while cursor.isoformat() <= window[0]:
                entry = calendar(cursor.isoformat())
                if entry["open"]:
                    group["history_dates"].append(entry["date"])
                proof = dict({k: entry[k] for k in ("source", "source_url", "fetched_at")}, dataset="sector_board_calendar", group=key)
                if proof not in board["provenance"]:
                    board["provenance"].append(proof)
                cursor += timedelta(days=1)

        def collect(spec):
            row = {k: spec[k] for k in ("name", "official_name", "code", "issuer", "currency", "identity_url", "secid")}
            row.update(supplemental=bool(spec.get("supplemental")), date=group["dates"][0] if group["dates"] else None,
                       status="unavailable", close=None, change_pct=None, values=[], history=[],
                       reason=None, provenance=None, price_basis="本币价格指数（不复权，不含分红再投资）")
            if not window:
                row["reason"] = "缺少本市场交易日历，未计算。"
                return row
            try:
                if key == "HK":
                    history, provenance = fetch_hk_history(spec, history_start, window[0], group["history_dates"])
                else:
                    history, provenance = fetch_history(spec, start, window[0])
                points = {h["date"]: h["close"] for h in history}
                if any(stamp not in points for stamp in window):
                    raise ValueError("指数日线未覆盖本市场最近10个交易日及前收盘基准；未用旧值填补")
                series = {h["date"]: h["value"] for h in log_ema_series(history)}
                values = [{"date": stamp, "value": series[stamp]} for stamp in group["dates"]]
                if any(p["value"] is None for p in values):
                    raise ValueError(f"EMA预热不足：每个展示日需要至少{MIN_HISTORY}根有效日线")
                row.update(status="available", close=points[window[0]],
                           change_pct=(points[window[0]] / points[window[1]] - 1) * 100,
                           values=values, history=history, provenance=provenance)
            except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
                row["reason"] = str(exc)
            return row

        with ThreadPoolExecutor(max_workers=3) as pool:
            group["rows"] = list(pool.map(collect, group_specs))
        group["sample_size"] = sum(r["status"] == "available" for r in group["rows"])
        if group["sample_size"]:
            group["status"] = "available" if group["sample_size"] == group["configured_count"] else "partial"
        if group["dates"] and group["dates"][0] != day:
            group["observations"].append(f"报告日该市场休市；最新交易日为 {group['dates'][0]}。")
    counts = [g["sample_size"] for g in board["groups"]]
    if any(counts):
        board["status"] = "available" if all(g["status"] == "available" for g in board["groups"]) else "partial"
    return board
