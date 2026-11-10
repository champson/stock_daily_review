import json
import unittest
import urllib.error
from unittest.mock import Mock, patch

from review.analysis import LABELS, analyze
from review.mcp_client import MCPClient, MCPError, NoRedirect
from review.publish import publish


class TransportTests(unittest.TestCase):
    def test_missing_api_key_is_actionable_without_echoing_server_message(self):
        client = MCPClient("https://mcp.tdx.com.cn:3001/mcp")
        response = {"isError": True, "content": [{"type": "text", "text": json.dumps({"error": {"code": "MISSING_API_KEY", "message": "private-value"}})}]}
        with patch.object(client, "_send", return_value=response), self.assertRaises(MCPError) as raised:
            client.call_tool("tdx_wenda_quotes", {"question": "上证指数"})
        self.assertEqual(raised.exception.code, "MISSING_API_KEY")
        self.assertIn("TDX_MCP_TOKEN", str(raised.exception))
        self.assertNotIn("private-value", str(raised.exception))

    def test_unknown_tool_error_does_not_echo_response(self):
        client = MCPClient("https://mcp.tdx.com.cn:3001/mcp")
        with patch.object(client, "_send", return_value={"isError": True, "content": [{"type": "text", "text": "private-value"}]}), self.assertRaises(MCPError) as raised:
            client.call_tool("tdx_wenda_quotes", {})
        self.assertNotIn("private-value", str(raised.exception))

    def test_empty_report_cannot_be_published_even_when_status_says_partial(self):
        report = {"date": "2026-10-13", "status": "partial", "sections": {kind: analyze(kind, []) for kind in LABELS}}
        with patch("urllib.request.build_opener") as opener, self.assertRaisesRegex(ValueError, "无有效行情"):
            publish("https://stock-review.example", "private-value", report)
        opener.assert_not_called()

    def test_publish_rejects_redirect_before_readback(self):
        sections = {kind: analyze(kind, []) for kind in LABELS}
        sections["indices"] = analyze("indices", [{"symbol": "fixture", "date": "2026-10-13", "close": 1}])
        report = {"date": "2026-10-13", "status": "partial", "sections": sections}
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError("https://stock-review.example/api/reports", 302, "redirect", {}, None)
        with patch("urllib.request.build_opener", return_value=opener) as create, self.assertRaisesRegex(ValueError, "Site HTTP 302"):
            publish("https://stock-review.example", "private-value", report)
        self.assertIsInstance(create.call_args.args[0], NoRedirect)
        self.assertEqual(opener.open.call_count, 1)

    def test_site_url_must_be_an_origin(self):
        for url in ("http://stock-review.example", "https://user@stock-review.example", "https://stock-review.example/path", "https://stock-review.example?next=other"):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "HTTPS 地址"):
                publish(url, "private-value", {})


if __name__ == "__main__":
    unittest.main()
