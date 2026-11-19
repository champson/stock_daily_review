"""CLI for reproducible closing-date screening and market return snapshots."""

import argparse
import csv
import json
import sys
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

from .report import build_report, read_json, validate_date, write_json
from .sources import load_candidates


ROOT = Path(__file__).resolve().parents[2]
CSV_COLUMNS = ("symbol", "name", "date", "close", "category", "trend_status", "market_rs",
               "vcp_state", "vcp_score", "contractions", "last_drawdown_pct", "pivot", "risk_level",
               "breakout_date", "volume_ratio", "reason")


def _spreadsheet_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return "'" + value
    return value


def _write_csv(path, report):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for item in report["rows"]:
            trend = item.get("trend", {})
            vcp = item.get("vcp", {})
            breakout = vcp.get("breakout") or {}
            swings = vcp.get("swings", [])
            reasons = item.get("reason") or "；".join([*trend.get("reasons", []), *vcp.get("reasons", [])])
            row = {"symbol": item["symbol"], "name": item["candidate"].get("name"), "date": report["date"],
                   "close": item.get("close"), "category": item["category"], "trend_status": trend.get("status"),
                   "market_rs": (item.get("rs") or {}).get("percentile"), "vcp_state": vcp.get("state"),
                   "vcp_score": vcp.get("score"),
                   "contractions": len(swings), "last_drawdown_pct": swings[-1].get("drawdown_pct") if swings else None,
                   "pivot": vcp.get("pivot"), "risk_level": vcp.get("risk_level"),
                   "breakout_date": breakout.get("date"), "volume_ratio": breakout.get("volume_ratio"),
                   "reason": reasons}
            writer.writerow({key: _spreadsheet_cell(value) for key, value in row.items()})


def _output_directory(path, work):
    """Publish one complete output directory, preserving any previous run."""
    path = path.resolve()
    if path.exists():
        raise ValueError(f"输出目录已存在：{path}。请使用新的目录。")
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path.parent, prefix=".sepa-") as staging:
        directory = Path(staging) / "result"
        directory.mkdir(mode=0o700)
        result = work(directory)
        directory.rename(path)
    return result


def _run(args):
    day = validate_date(args.date)
    if not args.symbols and args.csv is None:
        raise ValueError("没有候选股票，请提供 --symbols 或 --csv")
    config = read_json(args.config)

    def generate(path):
        inputs = path / "inputs"
        inputs.mkdir()
        captures = {}
        if args.csv is not None:
            captures["csv"] = inputs / "candidates.csv"
            copyfile(args.csv, captures["csv"])
        if args.symbols:
            captures["symbols"] = inputs / "symbols.json"
            write_json(captures["symbols"], args.symbols)
        if args.market_returns is not None:
            captures["market_returns"] = inputs / "market-returns.json"
            copyfile(args.market_returns, captures["market_returns"])
        candidates = load_candidates(args.symbols or (), csv_path=captures.get("csv"), as_of=day)
        if not candidates:
            raise ValueError("候选 CSV 无有效股票")
        report = build_report(day, candidates, config, path, price_dir=args.price_dir,
                              market_returns=captures.get("market_returns"), charts=args.charts)
        report["inputs"] = {key: file.relative_to(path).as_posix() for key, file in captures.items()}
        write_json(path / "report.json", report)
        _write_csv(path / "ranking.csv", report)
        return report

    result = _output_directory(args.output_dir, generate)
    summary = {"date": day, "status": result["status"], "rows": len(result["rows"]),
               "groups": {key: len(symbols) for key, symbols in result["groups"].items()},
               "output_dir": str(args.output_dir.resolve())}
    print(json.dumps(summary, ensure_ascii=False))
    return 2 if result["status"] == "unavailable" else 0


def _market_returns(args):
    from .calendar_mcp import default_calendar_adapter
    from .market import collect_market_returns

    day = validate_date(args.date)
    adapter = default_calendar_adapter(args.calendar_archive_dir)

    def generate(path):
        result = collect_market_returns(adapter, day)
        write_json(path / "market-returns.json", result)
        return result

    result = _output_directory(args.output_dir, generate)
    print(json.dumps({"date": day, "status": result["status"], "rows": len(result["rows"]),
                      "file": str(args.output_dir.resolve() / "market-returns.json")}, ensure_ascii=False))
    return 2 if result["status"] == "unavailable" else 0


def _calendar_import(args):
    from .calendar_mcp import import_manifest, import_snapshot

    if args.manifest:
        imported = import_manifest(args.manifest, args.archive_dir)
    else:
        imported = [import_snapshot(args.snapshot, args.archive_dir)]
    print(json.dumps({"status": "available", "months": len(imported),
                      "archive_dir": str(args.archive_dir.resolve())}, ensure_ascii=False))
    return 0


def _candidates(args):
    from .daxi import load_daxi_candidates

    day = validate_date(args.date)

    def capture(path):
        result = load_daxi_candidates(day)
        write_json(path / "candidates.json", {key: result[key] for key in ("date", "source", "rows")})
        write_json(path / "provider-response.json", result["raw"])
        return result

    result = _output_directory(args.output_dir, capture)
    print(json.dumps({"status": "available", "date": day, "rows": len(result["rows"]),
                      "output_dir": str(args.output_dir.resolve())}, ensure_ascii=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="A 股盘后趋势与 VCP 二筛")
    commands = parser.add_subparsers(required=True)
    run = commands.add_parser("run", help="分析指定股票并生成结构化报告")
    run.add_argument("--date", required=True, help="报告交易日 YYYY-MM-DD（北京时间）")
    run.add_argument("--symbols", nargs="+", help="例如 002100 sh600519")
    run.add_argument("--csv", type=Path, help="候选 CSV，包含 symbol 或 code 列")
    run.add_argument("--config", type=Path, default=ROOT / "config" / "sepa.json")
    run.add_argument("--price-dir", type=Path, help="确定性重放，读取该目录下带明确日期和口径的证券 JSON")
    run.add_argument("--market-returns", type=Path, help="同一报告日且可追溯的市场收益快照")
    run.add_argument("--charts", action="store_true", help="生成日线、成交量和波段标注 PNG")
    run.add_argument("--output-dir", type=Path, required=True, help="新输出目录")
    run.set_defaults(execute=_run)

    market = commands.add_parser("market-returns", help="从四个精确交易日价格采集市场收益快照")
    market.add_argument("--date", required=True, help="报告交易日 YYYY-MM-DD（北京时间）")
    market.add_argument("--output-dir", type=Path, required=True, help="新输出目录")
    market.add_argument("--calendar-archive-dir", type=Path, help="MCP 月历归档目录，默认仓库中的官方归档")
    market.set_defaults(execute=_market_returns)

    calendar = commands.add_parser("calendar-import", help="核验并归档通过 MCP 取得的深交所完整自然月月历")
    snapshots = calendar.add_mutually_exclusive_group(required=True)
    snapshots.add_argument("--snapshot", type=Path, help="A股 MCP trading_calendar 的 JSON 结果")
    snapshots.add_argument("--manifest", type=Path, help="多个 MCP 月历结果的清单")
    calendar.add_argument("--archive-dir", required=True, type=Path)
    calendar.set_defaults(execute=_calendar_import)

    candidates = commands.add_parser("candidates", help="验证大虾皮 VCP 鉴权并私有归档候选快照")
    candidates.add_argument("--date", required=True, help="已收盘的报告交易日 YYYY-MM-DD（北京时间）")
    candidates.add_argument("--output-dir", type=Path, required=True, help="新建的私有输出目录")
    candidates.set_defaults(execute=_candidates)
    args = parser.parse_args(argv)
    try:
        return args.execute(args)
    except (ValueError, RuntimeError, OSError, ImportError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
