"""Public index fallback contracts use mocked HTTP, never live market values."""
import io
import http.client
import json
import unittest
import urllib.error
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from review import index_history_fallbacks as fallback


START, END = "2025-12-01", "2026-11-09"


def bar(day=END, close="1234.5", volume="1000"):
    return f"{day},1200,{close},1250,1190,{volume},1000000,1.2"


def em_payload(**fields):
    return {"rc": 0, "data": {"code": "932000", "market": 2, "name": "中证2000",
                               "klines": [bar("2025-12-31"), bar()], **fields}}


class FakeResponse(io.BytesIO):
    status = 200


class IndexFallbackTests(unittest.TestCase):
    def request(self, function, payload, *args):
        opener = Mock()
        opener.open.return_value = FakeResponse(json.dumps(payload).encode())
        with patch("review.index_history_fallbacks.urllib.request.build_opener", return_value=opener) as build:
            result = function(*args)
        return result, opener, build

    def test_csi_market_code_and_dated_prices_are_preserved(self):
        result, opener, build = self.request(fallback.fetch_index_history, em_payload(), "sh932000", START, END)
        request = opener.open.call_args.args[0]
        query = parse_qs(urlsplit(request.full_url).query)
        self.assertEqual(query["secid"], ["2.932000"])
        self.assertEqual(query["fqt"], ["0"])
        self.assertEqual(query["beg"], ["20251201"])
        self.assertEqual(query["end"], ["20261109"])
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 30)
        self.assertIsInstance(build.call_args.args[0], fallback._NoRedirect)
        self.assertEqual([(r["date"], r["close"], r["volume"]) for r in result["rows"]],
                         [("2025-12-31", 1234.5, 1000), (END, 1234.5, 1000)])
        self.assertEqual(result["volume_unit"], "source")
        self.assertTrue(all(r["source"] == "eastmoney" and r["fetched_at"] for r in result["rows"]))

    def test_bse_uses_market_zero_without_renaming_the_source_unit(self):
        payload = {"data": {"code": "899050", "mkt": "0", "name": "北证50指数", "klines": [bar(volume="0")]}}
        result, opener, _ = self.request(fallback.fetch_index_history, payload, "bj899050", START, END)
        self.assertEqual(parse_qs(urlsplit(opener.open.call_args.args[0].full_url).query)["secid"], ["0.899050"])
        self.assertEqual(result["rows"][0]["volume"], 0)
        self.assertEqual(result["volume_unit"], "source")

    def test_em_rejects_wrong_identity_stale_duplicate_and_bad_prices(self):
        bad = [em_payload(code="000001"), em_payload(market=1), em_payload(name="上证指数"),
               em_payload(klines=[]), em_payload(klines=[bar("2026-11-06")]),
               em_payload(klines=[bar(), bar()]), em_payload(klines=[bar(), bar("2026-11-06")]),
               em_payload(klines=[bar("2025-11-30"), bar()]),
               em_payload(klines=[bar("2026-11-10")]), em_payload(klines=[bar("2026-02-30"), bar()]),
               em_payload(klines=[bar(close="0")]), em_payload(klines=[bar(close="nan")]),
               em_payload(klines=[bar(close="inf")]), em_payload(klines=["2026-11-09,1234"]),
               {"rc": 1, "data": em_payload()["data"]}, {"data": None}]
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                self.request(fallback.fetch_index_history, payload, "sh932000", START, END)

    def test_invalid_volume_becomes_null_without_erasing_valid_price(self):
        for value in ("", "-", "-1", "nan", "inf", "True"):
            with self.subTest(volume=value):
                result, _, _ = self.request(fallback.fetch_index_history, em_payload(klines=[bar(volume=value)]),
                                            "sh932000", START, END)
                self.assertEqual(result["rows"][0]["close"], 1234.5)
                self.assertIsNone(result["rows"][0]["volume"])

    def test_jsl_full_history_is_sorted_and_filtered_using_index_price(self):
        payload = {"data": [
            {"price_dt": "2026-11-10", "price": 1200, "volume": 999},
            {"price_dt": END, "price": "1100.25", "avg_price": 99999, "mid_price": 88888, "volume": 999},
            {"price_dt": "2025-12-31", "price": 1000},
            {"price_dt": "2017-12-29", "price": 1000},
        ]}
        result, opener, _ = self.request(fallback.fetch_jsl_equal_weight, payload, START, END)
        self.assertEqual(opener.open.call_args.args[0].full_url, fallback.JISILU_URL)
        self.assertEqual(result["name"], "集思录转债等权")
        self.assertEqual([(r["date"], r["close"]) for r in result["rows"]],
                         [("2025-12-31", 1000), (END, 1100.25)])
        self.assertTrue(all(r["volume"] is None and r["source"] == "jisilu" for r in result["rows"]))

    def test_jsl_rejects_duplicate_stale_unknown_and_nonfinite_history(self):
        valid = {"price_dt": END, "price": 1100}
        bad = [{"data": []}, {"data": "login"}, {"data": [valid, valid]},
               {"data": [{"price_dt": "2026-11-06", "price": 1100}]},
               {"data": [{"price_dt": END, "avg_price": 1100}]},
               {"data": [{"price_dt": "2026-02-30", "price": 1100}]},
               {"data": [{"price_dt": END, "price": True}]},
               {"data": [{"price_dt": END, "price": "Infinity"}]}]
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                self.request(fallback.fetch_jsl_equal_weight, payload, START, END)

    def test_jsl_live_columnar_shape_uses_dates_and_index_points_only(self):
        payload = {"code": 200, "msg": "success", "rules": {}, "data": {
            "price_dt": ["2026-11-10", END, "2025-12-31", "2017-12-29"],
            "price": [1200, "1100.25", 1000, 1000],
            "avg_price": [9999, 9999, 9999, 9999],
            "mid_price": [8888, 8888, 8888, 8888],
            "volume": [7777, 7777, 7777, 7777],
        }}
        result, _, _ = self.request(fallback.fetch_jsl_equal_weight, payload, START, END)
        self.assertEqual([(r["date"], r["close"]) for r in result["rows"]],
                         [("2025-12-31", 1000), (END, 1100.25)])
        self.assertTrue(all(r["volume"] is None for r in result["rows"]))

    def test_jsl_rejects_inconsistent_column_lengths_and_application_errors(self):
        for data in [
            {"price_dt": [END], "price": []},
            {"price_dt": [END], "price": [1000], "volume": []},
            {"price_dt": [END], "price": [1000], "avg_price": 1000},
            {"price_dt": [END], "avg_price": [1000]},
        ]:
            with self.subTest(data=data), self.assertRaises(RuntimeError):
                self.request(fallback.fetch_jsl_equal_weight, {"code": 200, "data": data}, START, END)
        with self.assertRaisesRegex(RuntimeError, "错误码"):
            self.request(fallback.fetch_jsl_equal_weight,
                         {"code": 401, "data": [{"price_dt": END, "price": 1000}]}, START, END)

    def test_http_limits_and_malformed_payloads_fail_explicitly(self):
        opener = Mock()
        for body in (b"<html>login</html>", b"[]", b"\xff"):
            opener.open.return_value = FakeResponse(body)
            with patch("review.index_history_fallbacks.urllib.request.build_opener", return_value=opener), \
                    self.assertRaises(RuntimeError):
                fallback.fetch_jsl_equal_weight(START, END)
        opener.open.return_value = FakeResponse(b"x" * 21)
        with patch("review.index_history_fallbacks.urllib.request.build_opener", return_value=opener), \
                patch.object(fallback, "MAX_RESPONSE_BYTES", 20), self.assertRaisesRegex(RuntimeError, "10MiB"):
            fallback.fetch_jsl_equal_weight(START, END)
        opener.open.side_effect = urllib.error.HTTPError(fallback.JISILU_URL, 302, "redirect", {}, None)
        with patch("review.index_history_fallbacks.urllib.request.build_opener", return_value=opener), \
                self.assertRaises(RuntimeError):
            fallback.fetch_jsl_equal_weight(START, END)

    def test_incomplete_http_body_is_an_explicit_optional_source_failure(self):
        class BrokenResponse(FakeResponse):
            def read(self, size=-1):
                raise http.client.IncompleteRead(b"partial", 100)

        opener = Mock()
        opener.open.return_value = BrokenResponse(b"")
        with patch("review.index_history_fallbacks.urllib.request.build_opener", return_value=opener), \
                self.assertRaisesRegex(RuntimeError, "IncompleteRead"):
            fallback.fetch_jsl_equal_weight(START, END)

    def test_invalid_dates_or_unsupported_symbol_do_not_request_network(self):
        with patch("review.index_history_fallbacks.urllib.request.build_opener") as opener:
            for args in [("sh000001", START, END), ("sh932000", "2026-02-30", END),
                         ("sh932000", END, START)]:
                with self.subTest(args=args), self.assertRaises(ValueError):
                    fallback.fetch_index_history(*args)
            opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
