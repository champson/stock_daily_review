"""Deterministic full-job tests: all external data here is explicitly mocked."""
import calendar
import contextlib
import io
import json
from datetime import date, datetime, timedelta
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from review.sepa.daily import _day_lock, main, run_daily

DAY = "2026-11-18"
NOW = datetime(2026, 11, 19, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def month_fixture(year, month, opened=True):
    return {"source": "深交所（模拟归档）", "source_url": "https://www.szse.cn/api/report/exchange/onepersistenthour/monthList",
            "fetched_at": "2026-11-19T18:00:00+08:00",
            "rows": [{"date": date(year, month, day).isoformat(), "is_open": opened and date(year, month, day).weekday() < 5}
                     for day in range(1, calendar.monthrange(year, month)[1] + 1)]}


class CalendarFixture:
    def __init__(self, opened=True):
        self.opened = opened
        self.month_calls = []
    def calendar_month(self, year, month):
        self.month_calls.append((year, month))
        return month_fixture(year, month, self.opened)


def candidates(empty=False):
    rows = [] if empty else [{"symbol": "sz002100", "name": "=PRIVATE_TEST_NAME", "source": "daxiapi"}]
    return {"date": DAY, "source": {"provider": "daxiapi", "verified_empty": empty,
            "schema_status": "mock_only"}, "rows": rows,
            "raw": {"errCode": 0, "data": {"date": DAY, "rows": [] if empty else [{"stockId": "002100"}]}}}


def market_fixture(as_of=DAY, available=True):
    universe = {"id": "synthetic", "label": "独立模拟市场样本", "date": as_of,
                "scope": "market_sample", "vetted": available, "point_in_time": available,
                "complete": False, "eligible_count": 2}
    return {"date": as_of, "market_universe": universe,
            "rows": [{"symbol": symbol, "date": as_of,
                      "returns_pct": {"63d": score, "126d": score, "252d": score}}
                     for symbol, score in (("sz002100", 20), ("sh600519", 10))] if available else [],
            "provenance": [{"source": "offline integration fixture"}]}


def fixtures(root, with_price=True):
    config = json.loads((Path(__file__).resolve().parents[2] / "config" / "sepa.json").read_text())
    config["trend"]["rs_min_samples"] = 2
    path = root / "config.json"
    path.write_text(json.dumps(config))
    prices = root / "prices"
    prices.mkdir()
    if with_price:
        stamps, day = [], date.fromisoformat(DAY)
        while len(stamps) < 320:
            if day.weekday() < 5:
                stamps.append(day.isoformat())
            day -= timedelta(days=1)
        bars = []
        for index, stamp in enumerate(reversed(stamps)):
            close = 10 + index * .025
            bars.append({"date": stamp, "open": close, "high": close + .03,
                         "low": close - .03, "close": close, "volume": 10000})
        (prices / "sz002100.json").write_text(json.dumps({"symbol": "sz002100", "date": DAY,
            "adjustment": "qfq", "volume_unit": "shares", "source": "synthetic test prices", "bars": bars}))
    return {"config_path": path, "price_dir": prices}


class DailyJobTests(unittest.TestCase):
    def call(self, root, **kwargs):
        adapter = kwargs.pop("adapter", CalendarFixture())
        return run_daily(DAY, root / "archive", calendar_factory=lambda directory: adapter,
            candidate_loader=kwargs.pop("candidate_loader", lambda day: candidates()),
            market_collector=kwargs.pop("market_collector", lambda instance, day: market_fixture(day)),
            charts=False, now=NOW, **kwargs)

    def test_closed_official_month_skips_before_candidates_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            loader, collector = Mock(side_effect=AssertionError("must not run")), Mock(side_effect=AssertionError("must not run"))
            result = self.call(root, adapter=CalendarFixture(False), candidate_loader=loader, market_collector=collector)
            self.assertEqual(result["status"], "skipped")
            target = root / "archive" / DAY
            first = (target / "run.json").read_bytes()
            again = self.call(root, candidate_loader=loader, market_collector=collector)
            self.assertEqual(again["status"], "already_completed")
            self.assertEqual((target / "run.json").read_bytes(), first)
            loader.assert_not_called(); collector.assert_not_called()
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)

    def test_older_engine_replays_verified_inputs_without_network_or_overwriting_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self.call(root, **fixtures(root))
            self.assertEqual(result["status"], "completed")
            original = Path(result["output_dir"])
            record = json.loads((original / "run.json").read_text())
            record["analysis_version"] = 1
            (original / "run.json").write_text(json.dumps(record))
            before = {p.name: p.read_bytes() for p in (original / "run.json", original / "report.json")}
            no_network = Mock(side_effect=AssertionError("replay must use archived inputs"))
            with patch("review.sepa.daily._default_adapter", no_network):
                corrected = self.call(root, candidate_loader=no_network, market_collector=no_network)
                self.assertEqual(corrected["status"], "completed")
                self.assertEqual(corrected["analysis_version"], 2)
                self.assertIn(".revisions/vcp-2", corrected["output_dir"])
                self.assertEqual(corrected["replay_of"]["report_sha256"], record["artifacts"]["report.json"]["sha256"])
                again = self.call(root, candidate_loader=no_network, market_collector=no_network)
                self.assertEqual(again["status"], "already_completed")
                self.assertEqual(again["output_dir"], corrected["output_dir"])
            no_network.assert_not_called()
            self.assertEqual(before, {p.name: p.read_bytes() for p in (original / "run.json", original / "report.json")})

    def test_bad_calendar_is_retryable_failure_without_candidate_pull(self):
        adapter = CalendarFixture()
        adapter.calendar_month = Mock(return_value={"rows": []})
        loader = Mock(side_effect=AssertionError("no candidate request"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.call(root, adapter=adapter, candidate_loader=loader)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["stage"], "calendar")
            self.assertFalse((root / "archive" / DAY).exists())
            self.assertTrue((Path(result["output_dir"]) / "run.json").is_file())
            loader.assert_not_called()

    def test_missing_token_or_unavailable_candidates_never_means_empty_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = fixtures(root)
            error = Mock(side_effect=ValueError("缺少大细服务 Token"))
            result = self.call(root, candidate_loader=error, **options)
            self.assertEqual((result["status"], result["stage"]), ("failed", "candidates"))
            self.assertIn("Token", result["error"])
            self.assertFalse((root / "archive" / DAY).exists())
            self.assertFalse((Path(result["output_dir"]) / "report.json").exists())

    def test_verified_empty_is_complete_without_price_or_market_fetch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = fixtures(root, with_price=False)
            collector = Mock(side_effect=AssertionError("no market request"))
            result = self.call(root, candidate_loader=lambda day: candidates(True), market_collector=collector, **options)
            self.assertEqual(result["status"], "completed_empty")
            self.assertEqual(result["candidate_count"], 0)
            report = json.loads((root / "archive" / DAY / "report.json").read_text())
            self.assertEqual(report["rows"], [])
            self.assertTrue(all(not symbols for symbols in report["groups"].values()))
            collector.assert_not_called()

    def test_actual_missing_credential_retains_machine_readable_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            root = Path(directory)
            from review.sepa.daxi import load_daxi_candidates
            collector = Mock(side_effect=AssertionError("no market request"))
            result = self.call(root, candidate_loader=load_daxi_candidates,
                               market_collector=collector, **fixtures(root))
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["stage"], "candidates")
            self.assertEqual(result["error_code"], "missing_credentials")
            record = json.loads((Path(result["output_dir"]) / "run.json").read_text())
            self.assertEqual(record["error_code"], "missing_credentials")
            self.assertFalse((root / "archive" / DAY).exists())
            collector.assert_not_called()

    def test_unverified_empty_is_failure_and_can_be_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = fixtures(root)
            wrong = candidates(True); wrong["source"]["verified_empty"] = False
            result = self.call(root, candidate_loader=lambda day: wrong, **options)
            self.assertEqual(result["status"], "failed")
            evidence = (Path(result["output_dir"]) / "run.json").read_bytes()
            retry = self.call(root, candidate_loader=lambda day: candidates(True), **options)
            self.assertEqual(retry["status"], "completed_empty")
            self.assertEqual((Path(result["output_dir"]) / "run.json").read_bytes(), evidence)

    def test_shared_engine_full_pipeline_retains_private_inputs_and_no_fake_qualification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = fixtures(root)
            adapter = CalendarFixture()
            def collect(instance, day):
                self.assertIs(instance, adapter)
                return market_fixture(day)
            result = self.call(root, adapter=adapter, market_collector=collect, **options)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["report_status"], "partial")
            target = root / "archive" / DAY
            report = json.loads((target / "report.json").read_text())
            self.assertEqual(report["rows"][0]["trend"]["status"], "pass")
            self.assertEqual(report["groups"]["A"], [])
            self.assertEqual(report["groups"]["B"], [])
            self.assertEqual(report["rows"][0]["candidate"]["source"], "daxiapi")
            raw = json.loads((target / "inputs" / "daxi-candidates.json").read_text())
            self.assertEqual(raw["raw"], candidates()["raw"])
            self.assertTrue((target / report["rows"][0]["quote"]["raw_capture_path"]).is_file())
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            for path in target.rglob("*"):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)
            again = self.call(root, candidate_loader=Mock(side_effect=AssertionError("must not rerun")), **options)
            self.assertEqual(again["status"], "already_completed")

    def test_unranked_market_diagnostic_remains_failed_until_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = fixtures(root)
            result = self.call(root, market_collector=lambda instance, day: market_fixture(day, False), **options)
            self.assertEqual(result["status"], "failed")
            self.assertIn("市场 RS", result["error"])
            self.assertTrue((Path(result["output_dir"]) / "report.json").exists())
            self.assertFalse((root / "archive" / DAY).exists())
            retry = self.call(root, **options)
            self.assertEqual(retry["status"], "completed")

    def test_allow_unranked_is_an_explicit_research_override(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.call(root, market_collector=lambda instance, day: market_fixture(day, False),
                               allow_unranked=True, **fixtures(root))
            self.assertEqual(result["status"], "completed")
            self.assertTrue(result["allow_unranked"])
            self.assertEqual(result["report_status"], "partial")

    def test_all_quotes_unavailable_stay_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = fixtures(root, with_price=False)
            result = self.call(root, **options)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["report_status"], "unavailable")
            self.assertFalse((root / "archive" / DAY).exists())

    def test_same_day_concurrency_is_nonblocking_and_does_not_pull_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive"; archive.mkdir()
            loader = Mock(side_effect=AssertionError("no candidate request"))
            with _day_lock(archive, DAY):
                result = self.call(root, candidate_loader=loader)
            self.assertEqual(result["status"], "busy")
            loader.assert_not_called()

    def test_modified_success_artifact_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = fixtures(root)
            self.call(root, **options)
            target = root / "archive" / DAY / "report.json"
            target.write_text('{"tampered":true}')
            result = self.call(root, **options)
            self.assertEqual((result["status"], result["stage"]), ("failed", "existing_output"))
            self.assertEqual(target.read_text(), '{"tampered":true}')

    def test_beijing_default_day_and_archive_directory_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            factory = Mock(return_value=CalendarFixture(False))
            archive = root / "official-calendars"
            result = run_daily(output_root=root / "archive", calendar_factory=factory,
                               calendar_archive_dir=archive,
                               now=datetime(2026, 11, 18, 23, tzinfo=ZoneInfo("UTC")))
            self.assertEqual(result["date"], "2026-11-19")
            factory.assert_called_once_with(archive)

    def test_research_unranked_result_is_not_reused_as_verified_daily_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = fixtures(root)
            research = self.call(root, market_collector=lambda instance, day: market_fixture(day, False),
                                 allow_unranked=True, **options)
            self.assertEqual(research["status"], "completed")
            default = self.call(root, **options)
            self.assertEqual((default["status"], default["stage"]), ("failed", "existing_output"))
            explicit = self.call(root, allow_unranked=True, **options)
            self.assertEqual(explicit["status"], "already_completed")

    def test_private_archive_cannot_be_created_in_site_public_output(self):
        from review.sepa.daily import ROOT
        for directory in (ROOT / "public" / "private-daily", ROOT / "dist" / "private-daily"):
            with self.subTest(directory=directory), self.assertRaisesRegex(ValueError, "私有日报归档"):
                run_daily(DAY, directory, now=NOW)
            self.assertFalse(directory.exists())

    def test_default_adapter_uses_shared_archive_aware_factory(self):
        from review.sepa.daily import _default_adapter
        expected = object()
        with patch("review.sepa.calendar_mcp.default_calendar_adapter", return_value=expected) as factory:
            self.assertIs(_default_adapter(Path("private/calendars")), expected)
        factory.assert_called_once_with(archive_directory=Path("private/calendars"))

    def test_main_prints_only_metadata_not_private_candidates(self):
        summary = {"date": DAY, "status": "completed", "candidate_count": 1,
                   "candidate_source": {"raw": "PRIVATE_TEST_NAME"}, "output_dir": "/private/archive"}
        output = io.StringIO()
        with patch("review.sepa.daily.run_daily", return_value=summary) as job, contextlib.redirect_stdout(output):
            self.assertEqual(main(["--date", DAY, "--output-root", "/private/archive", "--no-charts", "--allow-unranked"]), 0)
        self.assertNotIn("PRIVATE_TEST_NAME", output.getvalue())
        self.assertFalse(job.call_args.kwargs["charts"])
        self.assertTrue(job.call_args.kwargs["allow_unranked"])
        json.loads(output.getvalue())

    def test_main_failure_is_nonzero_with_explicit_error(self):
        result = {"date": DAY, "status": "failed", "stage": "candidates", "error": "缺少大细Token"}
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("review.sepa.daily.run_daily", return_value=result), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(main(["--no-charts"]), 1)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "failed")
        self.assertIn("Token", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
