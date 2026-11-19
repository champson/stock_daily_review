"""One scheduled entrypoint for market review and the SEPA page."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from .sepa.daily import run_daily
from .sepa.site_report import export_report, unavailable
from .sources import build_report


def with_sepa(report, archive_root, *, calendar_archive_dir=None, sepa_runner=None):
    """Keep valid market sections when SEPA is delayed; never reuse a stale day."""
    if report.get("skipped"):
        return report
    result = dict(report)
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    try:
        run = (sepa_runner or run_daily)(report["date"], archive_root, charts=False,
                                       calendar_archive_dir=calendar_archive_dir)
        if run["status"] not in {"completed", "completed_empty", "already_completed"}:
            raise ValueError(run.get("error", "SEPA 数据尚未就绪，保留其他有效复盘模块"))
        result["sepa"] = export_report(run["output_dir"])
        if result["sepa"]["date"] != report["date"]:
            raise ValueError("SEPA 与市场复盘日期不一致")
    except (ValueError, OSError, RuntimeError, ImportError, KeyError) as exc:
        # Do not include arbitrary provider payloads or credentials in the Site.
        result["sepa"] = unavailable(report["date"], stamp,
            "SEPA 本日未完成（" + type(exc).__name__ + "）；请查看本次私有运行诊断，成功历史已保留。")
        print("SEPA 尚未完成；诊断位于私有归档 .attempts 目录。", file=sys.stderr)
    if result["sepa"]["status"] != "available" and result.get("status") == "complete":
        result["status"] = "partial"
    result["generated_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="每日多资产复盘与 SEPA 联合运行")
    parser.add_argument("--date", default=datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat())
    parser.add_argument("--config", type=Path, default=Path("config/review.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sepa-archive", type=Path, default=Path("work/sepa/archive"))
    parser.add_argument("--calendar-archive-dir", type=Path, default=Path("work/sepa/calendar"))
    args = parser.parse_args(argv)
    try:
        base = build_report(json.loads(args.config.read_text()), args.date)
        result = with_sepa(base, args.sepa_archive, calendar_archive_dir=args.calendar_archive_dir)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n")
        temporary.replace(args.output)
        print(json.dumps({"date": args.date, "status": result.get("status", "skipped"),
                          "sepa_status": result.get("sepa", {}).get("status"), "output": str(args.output)}, ensure_ascii=False))
        return 2 if result.get("status") == "unavailable" else 0
    except (ValueError, OSError, RuntimeError, ImportError, KeyError) as exc:
        print("联合复盘失败：" + type(exc).__name__ + "；未发布报告。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
