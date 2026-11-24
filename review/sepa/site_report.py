"""Compact derived SEPA data for the existing daily Site report.

Only independently calculated analysis and quote bars are exported. Provider
candidate responses, private paths and the event replay log stay in the archive.
"""
import hashlib
from pathlib import Path

from .mcp_server import SepaService, Settings
from .report import read_json
from .sources import _validate_bars

CATEGORIES = ("A", "B", "C", "D", "insufficient", "unavailable")
BAR_FIELDS = ["date", "open", "high", "low", "close", "volume", "ma50", "ma150", "ma200"]
UPDATE = {"expected": "交易日北京时间 17:00 左右", "source_url": "https://daxiapi.com/xingtai/vcp",
          "checked_on": "2026-11-19", "guaranteed": False}


def unavailable(day, generated_at, message):
    return {"schema_version": 1, "date": day, "generated_at": generated_at,
            "status": "unavailable", "candidate_count": None, "rows": [],
            "groups": dict.fromkeys(CATEGORIES, 0), "market_rs": None,
            "candidate_source": None, "provider_update": UPDATE, "bar_fields": BAR_FIELDS,
            "observations": [message]}


def export_report(directory):
    directory = Path(directory).resolve()
    run = read_json(directory / "run.json")
    if run.get("status") not in {"completed", "completed_empty"}:
        raise ValueError("SEPA 归档尚未成功完成")
    report = SepaService(Settings(archive_root=directory.parent)).report(run["date"])
    if report["rows"] and report["market_rs"].get("status") != "available":
        raise ValueError("SEPA 研究报告未通过正式 RS 覆盖门槛")
    rows = []
    for row in report["rows"]:
        public = {"symbol": row["symbol"], "name": row["candidate"].get("name", row["symbol"]),
                  "category": row["category"], "close": row.get("close"),
                  "rs_percentile": (row.get("rs") or {}).get("percentile"),
                  "trend": row.get("trend"), "vcp": None, "quote": None, "bars": [],
                  "reason": row.get("reason")}
        if row.get("quote"):
            quote = row["quote"]
            path = (directory / quote["raw_capture_path"]).resolve()
            if not path.is_relative_to(directory) or hashlib.sha256(path.read_bytes()).hexdigest() != quote["raw_capture_sha256"]:
                raise ValueError("SEPA 行情归档路径或哈希不一致")
            original = read_json(path)
            bars = _validate_bars(original["bars"], run["date"], original["volume_unit"])
            chart = []
            for index in range(max(0, len(bars) - 120), len(bars)):
                point = dict(bars[index])
                for window in (50, 150, 200):
                    point[f"ma{window}"] = (sum(b["close"] for b in bars[index-window+1:index+1]) / window
                                             if index + 1 >= window else None)
                chart.append([point[key] for key in BAR_FIELDS])
            public["bars"] = chart
            public["quote"] = {key: quote.get(key) for key in
                               ("source", "source_url", "fetched_at", "adjustment", "volume_unit")}
            public["vcp"] = {key: value for key, value in row["vcp"].items() if key != "events"}
        rows.append(public)
    source = run["candidate_source"]
    market = report["market_rs"]
    result = {"schema_version": 1, "date": run["date"], "generated_at": run["finished_at"],
            "status": report["status"], "candidate_count": len(rows), "rows": rows,
            "groups": {key: len(report["groups"][key]) for key in CATEGORIES},
            "market_rs": {key: market.get(key) for key in ("date", "status", "universe", "coverage", "method", "anchors")},
            "candidate_source": {"provider": "daxiapi", "data_date": source.get("data_date", source.get("snapshot_date")),
                                 "fetched_at": source["fetched_at"], "pool_complete": False,
                                 "historical_membership_verified": False},
            "provider_update": UPDATE, "bar_fields": BAR_FIELDS,
            "observations": ["候选仅覆盖大虾皮接口展示子集；数据日期不证明 VCP 信号日或历史成员资格。",
                             "RS 是沪深有成交样本内的收益百分位代理，非全市场或 IBD RS Rating。",
                             "日线为腾讯前复权，成交量统一为股；历史前复权价格可能随公司行为调整。"]}
    if source.get("retrieval_method") == "dated_sql_vcp":
        result["candidate_source"].update(retrieval_method="dated_sql_vcp",
                                          source_url="https://daxiapi.com/coze/sql_query")
        result["observations"].insert(0,
            "本日为历史补跑：候选来自大虾皮按日期查询的 isVCP=1 子集，逐条核对 fullDate；"
            "共返回 " + str(len(rows)) + " 条，不代表原盘后页面的完整名单或当时保存的快照。")
    return result
