"""MCP transport and shared-engine checks, using declared offline test inputs."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import date, timedelta
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from review.sepa.mcp_server import ROOT, SepaService, Settings, create_server
from review.sepa.report import build_report
from review.sepa.sources import load_candidates


DAY = "2025-11-18"
HAS_MCP = importlib.util.find_spec("mcp") is not None


def write_prices(path):
    days, day = [], date.fromisoformat(DAY)
    while len(days) < 320:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day -= timedelta(days=1)
    bars = []
    for index, stamp in enumerate(reversed(days)):
        close = 10 + index * 0.025
        bars.append({"date": stamp, "open": close, "low": close - 0.03,
                     "high": close + 0.03, "close": close, "volume": 10000})
    path.write_text(json.dumps({"symbol": "sz002100", "date": DAY, "adjustment": "qfq",
                                "volume_unit": "shares", "source": "synthetic MCP test prices",
                                "bars": bars}), encoding="utf-8")


class SepaMcpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.prices = self.root / "prices" / DAY
        self.prices.mkdir(parents=True)
        write_prices(self.prices / "sz002100.json")
        self.archive = self.root / "archive" / DAY
        self.archive.mkdir(parents=True)
        self.settings = Settings(archive_root=self.archive.parent, price_root=self.prices.parent)
        self.service = SepaService(self.settings)

    def archive_report(self):
        report = self.service.screen(["002100"], DAY)
        (self.archive / "report.json").write_text(json.dumps(report), encoding="utf-8")
        return report

    def test_screen_uses_same_engine_and_does_not_write_archive(self):
        config = json.loads((ROOT / "config" / "sepa.json").read_text())
        with tempfile.TemporaryDirectory() as output:
            expected = build_report(DAY, load_candidates(["002100"], as_of=DAY), config,
                                    output, price_dir=self.prices)
        report = self.service.screen(["002100", "SZ002100"], DAY)
        self.assertEqual(report["rows"][0]["vcp"], expected["rows"][0]["vcp"])
        self.assertEqual(report["rows"][0]["trend"], expected["rows"][0]["trend"])
        self.assertEqual(report["groups"], expected["groups"])
        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual(report["groups"]["A"], [])
        self.assertEqual(report["rows"][0]["category"], "insufficient")
        self.assertNotIn("raw_capture_path", report["rows"][0]["quote"])
        self.assertNotIn("input_path", report["rows"][0]["quote"]["provenance"])
        self.assertFalse(report["execution"]["raw_captures_retained"])
        self.assertFalse((self.archive / "report.json").exists())
        json.dumps(report, allow_nan=False)

    def test_invalid_inputs_are_rejected_before_data_access(self):
        with patch("review.sepa.mcp_server.build_report") as calculate:
            for symbols, day in (([], DAY), (["002100"] * 51, DAY), (["../../secret"], DAY),
                                 ([2100], DAY), (["002100"], "9999-12-31"),
                                 (["002100"], "2025-02-30"), (["002100"], "2025-1-01")):
                with self.subTest(symbols=symbols, day=day), self.assertRaises(ValueError):
                    self.service.screen(symbols, day)
            calculate.assert_not_called()
        for limit in (0, 201, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.service.rank(DAY, limit=limit)
        with self.assertRaises(ValueError):
            self.service.rank(DAY, category="../../secret")
        for host in ("0.0.0.0", "::", "192.0.2.1", "example.com"):
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, "loopback"):
                create_server(host=host)

    def test_archive_is_date_matched_and_grouping_cannot_be_forged(self):
        report = self.archive_report()
        raw = (self.archive / "report.json").read_bytes()
        self.assertEqual(self.service.report(DAY), report)
        ranking = self.service.rank(DAY, category="insufficient", limit=1)
        self.assertEqual(ranking["rows"][0]["symbol"], "sz002100")
        self.assertEqual(ranking["total"], 1)
        self.assertTrue(ranking["read_only_archive"])
        self.assertEqual((self.archive / "report.json").read_bytes(), raw)
        for kind in ("date", "groups", "category", "symbol", "quote", "nonfinite", "rs",
                     "market_date", "rs_date", "rs_symbol", "rs_unverified"):
            broken = deepcopy(report)
            if kind == "date":
                broken["date"] = "2025-11-17"
            elif kind == "groups":
                broken["groups"]["A"] = ["sz002100"]
            elif kind == "category":
                broken["rows"][0]["category"] = "A"
                broken["groups"]["A"] = ["sz002100"]
                broken["groups"]["insufficient"] = []
            elif kind == "symbol":
                broken["rows"][0]["symbol"] = "002100"
            elif kind == "quote":
                broken["rows"][0]["quote"]["date"] = "2025-11-17"
            elif kind == "nonfinite":
                broken["rows"][0]["vcp"]["score"] = float("nan")
            elif kind == "rs":
                broken["rows"][0]["rs"] = "invalid evidence"
            elif kind == "market_date":
                broken["market_rs"]["date"] = "2025-11-17"
            else:
                broken["rows"][0]["rs"] = {"date": DAY, "symbol": "sz002100", "percentile": 99}
                if kind == "rs_date":
                    broken["rows"][0]["rs"]["date"] = "2025-11-17"
                elif kind == "rs_symbol":
                    broken["rows"][0]["rs"]["symbol"] = "sh600519"
            (self.archive / "report.json").write_text(json.dumps(broken), encoding="utf-8")
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.service.report(DAY)

    def test_missing_archive_or_configured_offline_data_never_falls_back_to_network(self):
        with self.assertRaisesRegex(ValueError, "没有"):
            self.service.report(DAY)
        with patch("review.sepa.mcp_server.build_report") as calculate:
            with self.assertRaisesRegex(ValueError, "没有"):
                self.service.screen(["002100"], "2025-11-17")
            calculate.assert_not_called()
        result = self.service.screen(["600519"], DAY)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["rows"][0]["category"], "unavailable")
        with self.assertRaisesRegex(ValueError, "无法分析"):
            self.service.analyze("600519", DAY)

    def test_dated_symlinks_cannot_escape_trusted_roots(self):
        outside = self.root / "outside.json"
        outside.write_text('{"secret":"must never be read"}', encoding="utf-8")
        (self.archive / "report.json").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "根目录"):
            self.service.report(DAY)
        (self.prices / "sz002100.json").unlink()
        (self.prices / "sz002100.json").symlink_to(outside)
        with patch("review.sepa.mcp_server.build_report") as calculate:
            with self.assertRaisesRegex(ValueError, "根目录"):
                self.service.screen(["002100"], DAY)
            calculate.assert_not_called()

    def test_third_party_candidates_keep_provenance_but_never_expose_raw(self):
        payload = {"date": DAY, "source": {"provider": "synthetic Daxi fixture"},
                   "rows": [{"symbol": "sz002100", "name": "Test", "signal_date": DAY}],
                   "raw": {"credential": "must-not-be-returned"}}

        def noisy(day):
            print("provider diagnostic")
            return deepcopy(payload)

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("review.sepa.mcp_server._load_daxi", side_effect=noisy):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = self.service.candidates(DAY)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("provider diagnostic", stderr.getvalue())
        self.assertEqual(result["rows"], payload["rows"])
        self.assertTrue(result["candidate_only"])
        self.assertEqual(len(result["provenance"]["raw_response_sha256"]), 64)
        self.assertNotIn("raw", result)
        self.assertNotIn("must-not-be-returned", json.dumps(result))
        payload["date"] = "2025-11-17"
        with patch("review.sepa.mcp_server._load_daxi", return_value=payload):
            with self.assertRaisesRegex(ValueError, "同日"):
                self.service.candidates(DAY)
        payload.update(date=DAY, rows=[])
        with patch("review.sepa.mcp_server._load_daxi", return_value=payload):
            with self.assertRaisesRegex(ValueError, "证明"):
                self.service.candidates(DAY)
            payload["source"]["verified_empty"] = True
            self.assertEqual(self.service.candidates(DAY)["rows"], [])

    @unittest.skipUnless(HAS_MCP, "optional official mcp SDK is not installed")
    def test_real_sdk_stdio_jsonrpc_tools_success_and_error(self):
        import anyio
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        expected = self.archive_report()
        env = {**os.environ, "SEPA_ARCHIVE_ROOT": str(self.archive.parent),
               "SEPA_PRICE_ROOT": str(self.prices.parent),
               "SEPA_CONFIG_FILE": str(ROOT / "config" / "sepa.json"), "DAXIAPI_TOKEN": ""}
        env.pop("SEPA_MARKET_RETURNS_ROOT", None)
        parameters = StdioServerParameters(command=sys.executable,
                                           args=["-m", "review.sepa.mcp_server"],
                                           cwd=str(ROOT), env=env)

        async def exercise():
            with anyio.fail_after(25):
                with tempfile.TemporaryFile(mode="w+") as errlog:
                    async with stdio_client(parameters, errlog=errlog) as (read, write):
                        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as session:
                            await session.initialize()
                            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                            self.assertEqual(set(tools), {"vcp_candidates", "sepa_screen", "vcp_analyze",
                                                          "sepa_rank", "sepa_report"})
                            for tool in tools.values():
                                self.assertTrue(tool.annotations.readOnlyHint)
                                self.assertFalse(tool.annotations.destructiveHint)
                                self.assertNotIn("path", tool.inputSchema.get("properties", {}))
                            for name, arguments in (("sepa_report", {"as_of": DAY}),
                                                    ("sepa_rank", {"as_of": DAY, "limit": 1}),
                                                    ("sepa_screen", {"as_of": DAY, "symbols": ["002100"]}),
                                                    ("vcp_analyze", {"as_of": DAY, "symbol": "002100"})):
                                result = await session.call_tool(name, arguments)
                                self.assertFalse(result.isError, result.content)
                                self.assertEqual(result.structuredContent["date"], DAY)
                                if name == "sepa_screen":
                                    self.assertEqual(result.structuredContent["rows"][0]["vcp"],
                                                     expected["rows"][0]["vcp"])
                            for name, arguments in (("sepa_report", {"as_of": "../secrets"}),
                                                    ("sepa_report", {"as_of": "2025-11-17"}),
                                                    ("sepa_screen", {"as_of": DAY, "symbols": []}),
                                                    ("vcp_analyze", {"as_of": DAY, "symbol": "../../secret"}),
                                                    ("vcp_candidates", {"as_of": DAY})):
                                result = await session.call_tool(name, arguments)
                                self.assertTrue(result.isError, (name, result.content))

        anyio.run(exercise)


if __name__ == "__main__":
    unittest.main()
