"""Recompute verified archived inputs with the current engine, without a fetch.

Keep the source archive immutable and write a separate dated revision. This
also prevents a cached older engine result from silently replacing a correction.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory


def replay_archive(directory, output_root, *, charts=False, now=None, allow_unranked=False):
    from .daily import _existing, run_daily
    from .mcp_server import SepaService, Settings
    directory = Path(directory).resolve()
    record = json.loads((directory / "run.json").read_text())
    day = record["date"]
    _existing(directory, day, allow_unranked=allow_unranked)
    report = SepaService(Settings(archive_root=directory.parent)).report(day)
    month = json.loads((directory / "calendar-month.json").read_text())
    candidates = json.loads((directory / "inputs/daxi-candidates.json").read_text())

    class ArchivedCalendar:
        def calendar_month(self, year, month_number):
            if f"{year:04d}-{month_number:02d}" != day[:7]:
                raise ValueError("重放日历不包含请求月份")
            return deepcopy(month)

    with TemporaryDirectory(prefix="sepa-replay-") as temporary:
        prices = Path(temporary)
        for row in report["rows"]:
            quote = row.get("quote")
            if not quote:
                continue
            path = (directory / quote["raw_capture_path"]).resolve()
            if not path.is_relative_to(directory):
                raise ValueError("重放行情路径超出归档")
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != quote["raw_capture_sha256"]:
                raise ValueError("重放行情哈希不一致")
            (prices / (row["symbol"] + ".json")).write_bytes(raw)
        return run_daily(day, output_root, config_path=directory / "inputs/config.json",
            price_dir=prices, market_returns=directory / "inputs/market-returns.json",
            charts=charts, calendar_factory=lambda _: ArchivedCalendar(),
            candidate_loader=lambda _: deepcopy(candidates), now=now,
            allow_unranked=allow_unranked,
            replay_of={"report_sha256": record["artifacts"]["report.json"]["sha256"],
                       "analysis_version": record.get("analysis_version", 1)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="以已校验归档重放 SEPA，不重新采集行情")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    result = replay_archive(args.archive, args.output_root)
    print(json.dumps({key: result.get(key) for key in
                     ("date", "status", "analysis_version", "output_dir")}, ensure_ascii=False))
    raise SystemExit(0 if result["status"] in {"completed", "completed_empty", "already_completed"} else 1)
