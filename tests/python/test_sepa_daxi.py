"""Synthetic candidate-protocol examples only: no live publisher-schema claim."""
import http.client
import io
import json
import os
import unittest
import urllib.error
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from review.mcp_client import NoRedirect
from review.sepa.daxi import ENDPOINT, CLIENT_VERSION, SCHEMA_STATUS, DaxiDataError, load_daxi_candidates

DAY = "2026-11-18"
TOKEN = "fixture-not-a-real-daxi-token"
NOW = datetime(2026, 11, 19, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def synthetic_contract(rows=None, day=DAY):
    # This inner structure is an explicit hypothesis awaiting a real response.
    return {"errCode": 0, "data": {"date": day, "rows": [] if rows is None else rows}}


class Response(io.BytesIO):
    status = 200
    headers = {"Content-Type": "application/json; charset=utf-8"}


def response(payload):
    return Response(json.dumps(payload, ensure_ascii=False).encode())


class DaxiCandidateContractTests(unittest.TestCase):
    def call(self, payload, **kwargs):
        opener = Mock()
        opener.open.return_value = response(payload)
        with patch.dict(os.environ, {"DAXIAPI_TOKEN": TOKEN}), \
                patch("review.sepa.daxi._now_shanghai", return_value=NOW), \
                patch("review.sepa.daxi.urllib.request.build_opener", return_value=opener) as build:
            result = load_daxi_candidates(DAY, **kwargs)
        return result, opener, build

    def test_missing_or_malformed_credential_never_opens_network(self):
        for token in ("", " ", "leading-space ", "token\ninjected", "fixture-秘密"):
            with self.subTest(token=bool(token)), patch.dict(os.environ, {"DAXIAPI_TOKEN": token}), \
                    patch("review.sepa.daxi.urllib.request.build_opener") as build, \
                    self.assertRaises(DaxiDataError) as raised:
                load_daxi_candidates(DAY)
            build.assert_not_called()
            self.assertNotIn("injected", str(raised.exception))

    def test_synthetic_success_preserves_snapshot_and_earlier_signal_distinction(self):
        payload = synthetic_contract([
            {"stockId": "600519", "name": "贵州茅台", "signal_date": "2026-11-17"},
            {"stockId": "002100", "name": "天康生物", "date": "2026-11-16"},
        ])
        result, opener, build = self.call(payload)
        self.assertEqual(result["date"], DAY)
        self.assertEqual(result["raw"], payload)
        self.assertEqual([r["symbol"] for r in result["rows"]], ["sh600519", "sz002100"])
        self.assertEqual(result["rows"][0]["signal_date"], "2026-11-17")
        self.assertEqual(result["rows"][1]["publisher_date"], "2026-11-16")
        self.assertNotIn("signal_date", result["rows"][1])
        self.assertTrue(all(r["snapshot_date"] == DAY for r in result["rows"]))
        self.assertEqual(result["source"]["schema_status"], SCHEMA_STATUS)
        self.assertEqual(result["source"]["client_version"], CLIENT_VERSION)
        self.assertFalse(result["source"]["verified_empty"])
        self.assertNotIn(TOKEN, json.dumps(result, ensure_ascii=False))
        self.assertIsInstance(build.call_args.args[0], NoRedirect)
        request = opener.open.call_args.args[0]
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(headers["authorization"], "Bearer " + TOKEN)
        self.assertEqual(json.loads(request.data), {"pattern": "vcp"})
        self.assertNotIn("date", json.loads(request.data))
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 30)

    def test_only_explicit_success_with_matching_snapshot_can_be_true_empty(self):
        result, _, _ = self.call(synthetic_contract())
        self.assertEqual(result["rows"], [])
        self.assertTrue(result["source"]["verified_empty"])
        self.assertEqual(result["source"]["snapshot_date"], DAY)
        for malformed in (None, [], {}, {"data": []}, {"errCode": 0},
                          {"errCode": 0, "data": []}, {"errCode": 0, "data": "暂无数据"},
                          {"errCode": 0, "data": {"rows": []}},
                          {"errCode": False, "data": {"date": DAY, "rows": []}},
                          {"errCode": "0", "data": {"date": DAY, "rows": []}}):
            with self.subTest(payload=malformed), self.assertRaises(ValueError):
                self.call(malformed)

    def test_stale_future_missing_or_invalid_publisher_snapshot_is_rejected(self):
        for day in ("2026-11-17", "2026-11-19", None, "2026-02-30", "11/18/2026"):
            with self.subTest(day=day), self.assertRaises(ValueError):
                self.call(synthetic_contract(day=day))

    def test_bad_candidate_dates_codes_and_conflicting_duplicates_fail_closed(self):
        examples = [
            [None], [{"stockId": 2100}], [{"stockId": "sh000001"}],
            [{"stockId": "002100", "snapshot_date": "2026-11-17"}],
            [{"stockId": "002100", "signal_date": "2026-11-19"}],
            [{"stockId": "002100", "signal_date": "2026-02-30"}],
            [{"stockId": "002100", "date": "2026-11-19"}],
            [{"stockId": "002100", "name": ""}],
            [{"stockId": "002100", "name": "甲"}, {"stockId": "002100", "name": "乙"}],
        ]
        for rows in examples:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                self.call(synthetic_contract(rows))
        result, _, _ = self.call(synthetic_contract([{"stockId": "002100"}, {"stockId": "002100"}]))
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["source"]["raw_row_count"], 2)

    def test_publisher_auth_and_failure_messages_never_echo_untrusted_text(self):
        for code in (401, 403, 429, 500, "MISSING_API_KEY"):
            with self.subTest(code=code), self.assertRaises(DaxiDataError) as raised:
                self.call({"errCode": code, "errMsg": "do-not-echo-server-text", "data": None})
            self.assertNotIn("do-not-echo-server-text", str(raised.exception))
        with self.assertRaises(DaxiDataError) as raised:
            self.call({"errCode": 0, "data": {"date": DAY, "rows": [], "debug": TOKEN}})
        self.assertEqual(raised.exception.code, "credential_reflection")
        self.assertNotIn(TOKEN, str(raised.exception))

    def test_redirect_network_and_bad_json_fail_without_credential_leak(self):
        failures = [urllib.error.HTTPError(ENDPOINT, 302, "untrusted error text", {}, io.BytesIO()),
                    urllib.error.HTTPError(ENDPOINT, 401, "untrusted error text", {}, io.BytesIO()),
                    urllib.error.URLError("untrusted error text"), TimeoutError("untrusted error text")]
        for failure in failures:
            opener = Mock()
            opener.open.side_effect = failure
            with self.subTest(kind=type(failure).__name__), patch.dict(os.environ, {"DAXIAPI_TOKEN": TOKEN}), \
                    patch("review.sepa.daxi.urllib.request.build_opener", return_value=opener), \
                    self.assertRaises(DaxiDataError) as raised:
                load_daxi_candidates(DAY)
            self.assertNotIn("untrusted error text", str(raised.exception))
            self.assertNotIn(TOKEN, str(raised.exception))
            self.assertEqual(opener.open.call_count, 1)
        for raw in (b"not json", b'{"errCode":0,"data":NaN}', b'{} {}',
                    b'{"errCode":401,"errCode":0,"data":{"date":"2026-11-18","rows":[]}}',
                    b'{"errCode":0,"data":{"date":"2026-11-17","date":"2026-11-18","rows":[]}}'):
            opener = Mock();opener.open.return_value = Response(raw)
            with patch.dict(os.environ, {"DAXIAPI_TOKEN": TOKEN}), \
                    patch("review.sepa.daxi.urllib.request.build_opener", return_value=opener), self.assertRaises(ValueError):
                load_daxi_candidates(DAY)

    def test_interrupted_body_is_closed_and_error_never_echoes_source_or_token(self):
        class InterruptedResponse(Response):
            def read(self, _limit):
                raise http.client.IncompleteRead(("untrusted-" + TOKEN).encode(), 10)

        interrupted = InterruptedResponse(b"")
        opener = Mock()
        opener.open.return_value = interrupted
        with patch.dict(os.environ, {"DAXIAPI_TOKEN": TOKEN}), \
                patch("review.sepa.daxi.urllib.request.build_opener", return_value=opener), \
                self.assertRaises(DaxiDataError) as raised:
            load_daxi_candidates(DAY)
        self.assertTrue(interrupted.closed)
        self.assertEqual(raised.exception.code, "source_unavailable")
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assertNotIn("untrusted", str(raised.exception))

    def test_future_date_and_custom_command_do_not_execute_cli_or_query_api(self):
        with patch.dict(os.environ, {"DAXIAPI_TOKEN": TOKEN}), \
                patch("review.sepa.daxi._now_shanghai", return_value=NOW), \
                patch("review.sepa.daxi.urllib.request.build_opener") as build, \
                patch("subprocess.run") as run:
            for kwargs in ({"as_of": "2026-11-20"}, {"as_of": DAY, "command": "arbitrary-cli --json"}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    load_daxi_candidates(**kwargs)
            build.assert_not_called();run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
