"""Private, idempotent POSIX daily job for the shared SEPA report engine.

The final dated directory is committed only after a verified closed-day skip,
a verified empty candidate result, or a usable report. Failed attempts remain
private under .attempts and can be retried without replacing successful history.
"""
from contextlib import contextmanager
from datetime import datetime, time, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
from zoneinfo import ZoneInfo
from .vcp import ANALYSIS_VERSION

ROOT = Path(__file__).resolve().parents[2]
SHANGHAI = ZoneInfo("Asia/Shanghai")
FINISHED = {"completed", "completed_empty", "skipped"}


def _now(value=None):
    value = datetime.now(SHANGHAI) if value is None else value
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("日任务时间必须包含时区")
    return value.astimezone(SHANGHAI)


def _default_root():
    return Path(os.environ.get("SEPA_ARCHIVE_ROOT", str(ROOT / "work" / "sepa" / "archive")))


def _private_json(path, value):
    from .report import write_json
    write_json(path, value)
    path.chmod(0o600)


def _seal_private(directory):
    for item in directory.rglob("*"):
        if item.is_symlink():
            raise ValueError("日任务产物不能包含符号链接")
        item.chmod(0o700 if item.is_dir() else 0o600)
    directory.chmod(0o700)


def _fingerprints(directory):
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("日任务产物不能包含符号链接")
        if path.is_file() and path != directory / "run.json":
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(256 * 1024), b""):
                    digest.update(chunk)
            result[path.relative_to(directory).as_posix()] = {"sha256": digest.hexdigest(),
                                                              "bytes": path.stat().st_size}
    return result


def _existing(directory, day, allow_unranked=False):
    from .report import read_json
    record = read_json(directory / "run.json")
    if (not isinstance(record, dict) or record.get("schema_version") != 1
            or record.get("date") != day or record.get("status") not in FINISHED
            or not isinstance(record.get("artifacts"), dict)
            or record["artifacts"] != _fingerprints(directory)):
        raise ValueError("已存在日期目录缺少可核验的成功记录；拒绝覆盖历史，请人工检查")
    required = {"calendar-month.json"}
    if record["status"] != "skipped":
        required.update({"report.json", "ranking.csv", "inputs/config.json", "inputs/daxi-candidates.json"})
    if not required <= record["artifacts"].keys():
        raise ValueError("已存在日期目录缺少完成任务所需的证据文件；拒绝覆盖历史")
    if record.get("candidate_count", 0) and not allow_unranked and record.get("market_rs_status") != "available":
        raise ValueError("已存在研究报告未通过市场 RS 核验，不能当作定时筛选完成；请检查归档或明确使用 --allow-unranked")
    return {"schema_version": 1, "date": day, "status": "already_completed",
            "analysis_version": record.get("analysis_version"),
            "completed_status": record["status"], "report_status": record.get("report_status"),
            "candidate_count": record.get("candidate_count"), "output_dir": str(directory)}


@contextmanager
def _day_lock(root, day):
    # Kernel-owned locks are released even when a Cron process crashes.
    try:
        import fcntl
    except ImportError:
        raise RuntimeError("日任务锁需要支持 fcntl 的 POSIX 环境") from None
    directory = root / ".locks"
    directory.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(directory / (day + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _default_adapter(directory):
    from .calendar_mcp import default_calendar_adapter
    return default_calendar_adapter(archive_directory=directory)


def _calendar(adapter, day):
    from .market import _official_month
    parsed = datetime.fromisoformat(day)
    month = adapter.calendar_month(parsed.year, parsed.month)
    opened = _official_month(month, parsed.year, parsed.month)[day]
    return month, {"date": day, "is_open": opened,
                   **{key: month[key] for key in ("source", "source_url", "fetched_at")},
                   "warning": month.get("warning")}


def _candidates(payload, day):
    from .sources import normalize_symbol
    from .report import validate_date
    if (not isinstance(payload, dict) or payload.get("date") != day
            or not isinstance(payload.get("source"), dict)
            or not isinstance(payload.get("rows"), list)
            or not isinstance(payload.get("raw"), dict)):
        raise ValueError("大细候选返回缺少同日数据、来源或原始来源证明")
    rows, seen = payload["rows"], set()
    if not rows and payload["source"].get("verified_empty") is not True:
        raise ValueError("大细空候选未经成功响应核验，不能当作当日无信号")
    for row in rows:
        if not isinstance(row, dict) or normalize_symbol(row.get("symbol")) != row.get("symbol"):
            raise ValueError("大细候选必须使用规范的 A 股股票代码")
        if row["symbol"] in seen:
            raise ValueError("大细候选证券代码重复")
        seen.add(row["symbol"])
        if row.get("signal_date") is not None and validate_date(row["signal_date"]) > day:
            raise ValueError("候选信号日期超过复盘日")
    return rows


def run_daily(day=None, output_root=None, *, config_path=None, price_dir=None,
              market_returns=None, charts=True, calendar_archive_dir=None,
              calendar_factory=None, candidate_loader=None, market_collector=None,
              report_builder=None, allow_unranked=False, now=None, replay_of=None):
    """Run once and return a metadata-only result; dependencies may be injected.

    Partial individual-quote coverage is preserved when the independent market
    RS is available. Unavailable RS or an entirely unavailable report remains
    retryable; research runs can explicitly opt into allow_unranked.
    Injected adapters/loaders are for deterministic tests or explicitly supplied
    local services; the production defaults never invent candidates or dates.
    """
    from .report import validate_date
    from .__main__ import _output_directory
    stamp = _now(now)
    day = validate_date(day or stamp.date().isoformat())
    if day > stamp.date().isoformat():
        raise ValueError("日任务日期不能晚于北京时间今天")
    root = Path(output_root) if output_root is not None else _default_root()
    root = root.resolve()
    for exposed in (ROOT / "public", ROOT / "dist", ROOT / ".git"):
        if root == exposed.resolve() or exposed.resolve() in root.parents:
            raise ValueError("私有日报归档不能放入站点公开目录、构建目录或Git元数据目录")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = root / day
    archives = Path(calendar_archive_dir) if calendar_archive_dir is not None else ROOT / "config" / "calendars"
    with _day_lock(root, day) as locked:
        if not locked:
            return {"date": day, "status": "busy", "output_dir": str(target)}
        if target.exists():
            try:
                existing = _existing(target, day, allow_unranked=allow_unranked)
                record = json.loads((target / "run.json").read_text())
                if record.get("candidate_count", 0) and record.get("analysis_version") != ANALYSIS_VERSION:
                    from .replay import replay_archive
                    return replay_archive(target, root / ".revisions" / f"vcp-{ANALYSIS_VERSION}",
                                          charts=charts, now=now, allow_unranked=allow_unranked)
                return existing
            except (ValueError, OSError, TypeError) as exc:
                return {"date": day, "status": "failed", "stage": "existing_output",
                        "error_type": type(exc).__name__, "error": str(exc), "output_dir": str(target)}
        attempt_id = stamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + secrets.token_hex(4)
        attempt_root = root / ".attempts"
        attempt_root.mkdir(mode=0o700, exist_ok=True)
        attempt_root.chmod(0o700)
        attempts = attempt_root / day
        attempts.mkdir(mode=0o700, exist_ok=True)
        attempt = attempts / attempt_id

        def generate(directory):
            from .report import read_json, require_config, build_report
            from .__main__ import _write_csv
            record = {"schema_version": 1, "date": day, "attempt_id": attempt_id,
                      "analysis_version": ANALYSIS_VERSION,
                      "started_at": stamp.isoformat(), "timezone": "Asia/Shanghai",
                      "status": "failed", "stage": "calendar", "entrypoint": "review.sepa.daily",
                      "allow_unranked": bool(allow_unranked)}
            if replay_of is not None:
                record["replay_of"] = replay_of
            try:
                adapter = (calendar_factory or _default_adapter)(archives)
                month, proof = _calendar(adapter, day)
                _private_json(directory / "calendar-month.json", month)
                record["calendar"] = proof
                if not proof["is_open"]:
                    record.update(status="skipped", reason="官方完整月历确认休市")
                    return record
                if day == stamp.date().isoformat() and stamp.time().replace(tzinfo=None) < time(15, 15):
                    raise ValueError("当日尚未到北京时间15:15，不生成未完成收盘日报")
                record["stage"] = "configuration"
                config_file = Path(config_path) if config_path is not None else ROOT / "config" / "sepa.json"
                config_raw = config_file.read_bytes()
                inputs = directory / "inputs"
                inputs.mkdir(mode=0o700)
                (inputs / "config.json").write_bytes(config_raw)
                config = read_json(inputs / "config.json")
                require_config(config)
                record["config_sha256"] = hashlib.sha256(config_raw).hexdigest()
                record["stage"] = "candidates"
                loader = candidate_loader
                if loader is None:
                    from .daxi import load_daxi_candidates
                    loader = load_daxi_candidates
                source = loader(day)
                candidates = _candidates(source, day)
                _private_json(inputs / "daxi-candidates.json", source)
                record.update(candidate_count=len(candidates), candidate_source=source["source"])
                market_path = None
                if candidates:
                    record["stage"] = "market_returns"
                    market_path = inputs / "market-returns.json"
                    if market_returns is not None:
                        market_path.write_bytes(Path(market_returns).read_bytes())
                    else:
                        collector = market_collector
                        if collector is None:
                            from .market import collect_market_returns
                            collector = collect_market_returns
                        _private_json(market_path, collector(adapter, day))
                record["stage"] = "analysis"
                report = (report_builder or build_report)(day, candidates, config, directory,
                    price_dir=price_dir, market_returns=market_path, charts=charts and bool(candidates))
                report["inputs"] = {"config": "inputs/config.json", "candidates": "inputs/daxi-candidates.json"}
                if market_path is not None:
                    report["inputs"]["market_returns"] = "inputs/market-returns.json"
                if not candidates:
                    report.setdefault("observations", []).append("大细成功响应已核验当日候选为空；未采集个股日线或市场收益。")
                _private_json(directory / "report.json", report)
                _write_csv(directory / "ranking.csv", report)
                record["report_status"] = report["status"]
                record["market_rs_status"] = report.get("market_rs", {}).get("status")
                if report["status"] == "unavailable":
                    raise ValueError("全部候选行情不可用；保留失败审计产物，允许同日重试")
                if candidates and not allow_unranked and report.get("market_rs", {}).get("status") != "available":
                    raise ValueError("独立市场 RS 尚未通过覆盖与范围核验；保留诊断产物，允许同日重试。研究重放可显式使用 --allow-unranked。")
                if report["status"] not in {"available", "partial"}:
                    raise ValueError("共享引擎返回未知报告状态")
                record.update(available_quote_count=sum(isinstance(row.get("quote"), dict) for row in report["rows"]),
                              unavailable_count=len(report["groups"].get("unavailable", [])),
                              insufficient_count=len(report["groups"].get("insufficient", [])),
                              status="completed" if candidates else "completed_empty",
                              groups={key: len(symbols) for key, symbols in report["groups"].items()})
                return record
            except Exception as exc:
                record.update(status="failed", error_type=type(exc).__name__,
                              error=str(exc).replace("\r", " ").replace("\n", " ")[:500])
                from .daxi import DaxiDataError
                if isinstance(exc, DaxiDataError):
                    record["error_code"] = exc.code
                return record
            finally:
                record["finished_at"] = _now(now).isoformat()
                record["output_dir"] = str(target if record["status"] in FINISHED else attempt)
                record["artifacts"] = _fingerprints(directory)
                _private_json(directory / "run.json", record)
                _seal_private(directory)

        record = _output_directory(attempt, generate)
        if record["status"] in FINISHED:
            attempt.rename(target)
        return record


def main(argv=None):
    parser = argparse.ArgumentParser(description="北京时间盘后大细候选 SEPA/VCP 私有日任务")
    parser.add_argument("--date", help="YYYY-MM-DD，默认北京时间今天")
    parser.add_argument("--output-root", type=Path, default=_default_root(), help="私有归档根，默认 SEPA_ARCHIVE_ROOT 或 work/sepa/archive")
    parser.add_argument("--config", type=Path, help="可选阈值配置，默认 config/sepa.json")
    parser.add_argument("--price-dir", type=Path, help="明确日期与复权口径的离线个股快照目录")
    parser.add_argument("--market-returns", type=Path, help="同日离线市场收益快照")
    parser.add_argument("--calendar-archive-dir", type=Path, help="已核验官方月历归档目录，默认 config/calendars")
    parser.add_argument("--no-charts", action="store_true", help="不生成 PNG")
    parser.add_argument("--allow-unranked", action="store_true", help="仅供研究：允许市场 RS 不足的诊断报告成为本日完成产物")
    args = parser.parse_args(argv)
    try:
        record = run_daily(args.date, args.output_root, config_path=args.config, price_dir=args.price_dir,
            market_returns=args.market_returns, charts=not args.no_charts,
            calendar_archive_dir=args.calendar_archive_dir, allow_unranked=args.allow_unranked)
        summary = {key: record[key] for key in ("date", "status", "report_status", "market_rs_status", "candidate_count", "available_quote_count", "unavailable_count", "insufficient_count", "groups", "stage", "error_type", "output_dir") if key in record}
        print(json.dumps(summary, ensure_ascii=False, allow_nan=False))
        if record["status"] == "failed":
            print(record.get("error", "日任务失败"), file=sys.stderr)
            return 1
        return 0
    except (ValueError, OSError, RuntimeError, ImportError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
