import hashlib
import hmac
import io
import json
import unittest
from unittest.mock import Mock, patch

from review.analysis import LABELS, analyze
from review.publish import derive_write_token, publish


class PublishAuthorizationTests(unittest.TestCase):
    def test_derivation_matches_independent_javascript_vector(self):
        self.assertEqual(
            derive_write_token("placeholder-service-token"),
            "f5951bd3ce60150cbf248ccda7d3b43a2acd3a97904eade3d0cfcbdb485e6fa2",
        )
        self.assertEqual(len(derive_write_token("placeholder-service-token")), 64)
        self.assertEqual(
            derive_write_token("占位-token"),
            hmac.new("占位-token".encode("utf-8"), b"stock-review:write:v1", hashlib.sha256).hexdigest(),
        )

    def test_empty_service_token_cannot_derive_authorization(self):
        for token in (None, "", False):
            with self.subTest(token=token), self.assertRaises(ValueError):
                derive_write_token(token)

    def test_publish_sends_separate_derived_authorization_and_preserves_readback(self):
        sections = {kind: analyze(kind, []) for kind in LABELS}
        sections["indices"] = analyze("indices", [{"symbol": "fixture", "date": "2026-10-13", "close": 1}])
        report = {"date": "2026-10-13", "status": "partial", "sections": sections}
        responses = [
            io.BytesIO(json.dumps({"saved": {"key": "fixture-only"}}).encode()),
            io.BytesIO(json.dumps({"report": report}).encode()),
        ]
        opener = Mock()
        opener.open.side_effect = responses
        with patch("urllib.request.build_opener", return_value=opener):
            result = publish("https://stock-review.example", "placeholder-service-token", report)
        self.assertEqual(result, {"key": "fixture-only"})
        post_request = opener.open.call_args_list[0].args[0]
        headers = {name.lower(): value for name, value in post_request.header_items()}
        self.assertEqual(headers["oai-sites-authorization"], "Bearer placeholder-service-token")
        self.assertEqual(headers["x-review-write-token"], derive_write_token("placeholder-service-token"))
        self.assertNotEqual(headers["x-review-write-token"], "placeholder-service-token")
        self.assertEqual(post_request.get_method(), "POST")
        read_request = opener.open.call_args_list[1].args[0]
        self.assertEqual(read_request.get_method(), "GET")
        self.assertEqual(read_request.full_url, "https://stock-review.example/api/reports?date=2026-10-13")
        self.assertIsNone(read_request.get_header("X-review-write-token"))


if __name__ == "__main__":
    unittest.main()
