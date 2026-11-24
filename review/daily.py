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


def _sepa_failure_message(run):
    # Only fixed descriptions may reach the public report, never provider text.
    candidate_errors = {
        "missing_credentials": "运行环境未配置大虾皮访问凭据，请检查定时任务的 Cloud 环境绑定",
        "invalid_credentials": "大虾皮访问凭据格式无效，请检查运行环境配置",
        "authentication_failed": "大虾皮认证或账户访问权限校验失败",
        "rate_limited": "大虾皮接口限流，需要稍后重试",
        "source_unavailable": "大虾皮接口连接失败或超时",
        "publisher_error": "大虾皮接口未成功返回数据",
        "snapshot_date_mismatch": "大虾皮返回的数据日期与报告日不一致",
        "unsupported_schema": "大虾皮响应格式未通过校验",
    }
    stages = {"calendar": "官方交易日历校验未完成",
              "configuration": "SEPA 配置校验未完成",
              "candidates": "候选数据校验未完成",
              "market_returns": "独立市场 RS 数据采集未完成",
              "analysis": "独立行情或分析结果未通过完整性校验",
              "existing_output": "历史 SEPA 归档校验未通过"}
    detail = (candidate_errors.get(run.get("error_code"))
              if run.get("stage") == "candidates" else None)
    detail = detail or stages.get(run.get("stage"), "运行未完成")
    return "SEPA 本日未完成：" + detail + "；成功历史已保留，完整诊断见本次私有运行归档。"


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
            result["sepa"] = unavailable(report["date"], stamp, _sepa_failure_message(run))
        else:
            result["sepa"] = export_report(run["output_dir"])
            if result["sepa"]["date"] != report["date"]:
                raise ValueError("SEPA 与市场复盘日期不一致")
    except (ValueError, OSError, RuntimeError, ImportError, KeyError) as exc:
        # Do not include arbitrary provider payloads or credentials in the Site.
        result["sepa"] = unavailable(report["date"], stamp,
            "SEPA 本日未完成（" + type(exc).__name__ + "）；请查看本次私有运行诊断，成功历史已保留。")
    if result["sepa"]["status"] == "unavailable":
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
