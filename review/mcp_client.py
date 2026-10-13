"""Streamable HTTP MCP 客户端；保留环境代理和 TLS 校验。"""
import json
import urllib.request
import urllib.error


class MCPError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class MCPClient:
    def __init__(self, url, token=None, timeout=45, auth_header="Authorization", auth_prefix="Bearer "):
        if not url.startswith("https://"):
            raise MCPError("远程 MCP 必须使用 HTTPS")
        self.url, self.token, self.timeout = url, token, timeout
        self.auth_header, self.auth_prefix = auth_header, auth_prefix
        self.opener = urllib.request.build_opener(NoRedirect())
        self.session, self.version, self.sequence = None, "2024-11-05", 0

    def _send(self, method, params, notification=False):
        self.sequence += 1
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            payload["id"] = self.sequence
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.token:
            headers[self.auth_header] = self.auth_prefix + self.token
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if method != "initialize":
            headers["MCP-Protocol-Version"] = self.version
        request = urllib.request.Request(self.url, json.dumps(payload).encode(), headers, method="POST")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.headers.get("Mcp-Session-Id"):
                    self.session = response.headers["Mcp-Session-Id"]
                if notification or response.status == 202:
                    return None
                if "text/event-stream" in response.headers.get("Content-Type", ""):
                    event, message, received = [], None, 0
                    for line in response:
                        received += len(line)
                        if received > 20_000_000:
                            raise MCPError("MCP 响应超过大小限制")
                        line = line.decode("utf-8").rstrip("\r\n")
                        if line.startswith("data:"):
                            event.append(line[5:].lstrip())
                        elif not line and event:
                            candidate = json.loads("\n".join(event))
                            event = []
                            if candidate.get("id") == payload["id"]:
                                message = candidate
                                break
                    if message is None:
                        raise MCPError("MCP SSE 未返回对应请求结果")
                else:
                    raw = response.read(20_000_001)
                    if len(raw) > 20_000_000:
                        raise MCPError("MCP 响应超过大小限制")
                    message = json.loads(raw)
        except urllib.error.HTTPError as exc:
            raise MCPError(f"MCP HTTP {exc.code}；检查 Cloud 网络策略和认证") from None
        except urllib.error.URLError:
            raise MCPError("MCP 连接失败；检查服务地址和 Cloud 网络策略") from None
        if message.get("id") != payload["id"]:
            raise MCPError("MCP 响应 ID 不匹配")
        if "error" in message:
            raise MCPError(f"MCP {method} 返回协议错误 {message['error'].get('code', 'unknown')}")
        return message.get("result")

    def initialize(self):
        result = self._send("initialize", {"protocolVersion": self.version, "capabilities": {}, "clientInfo": {"name": "stock-review", "version": "0.1.0"}})
        self.version = result.get("protocolVersion", self.version)
        self._send("notifications/initialized", {}, notification=True)
        return result

    def list_tools(self):
        result, cursor = [], None
        for _ in range(100):
            page = self._send("tools/list", {"cursor": cursor} if cursor else {})
            result.extend(page.get("tools", []))
            next_cursor = page.get("nextCursor")
            if not next_cursor:
                return result
            if next_cursor == cursor:
                raise MCPError("MCP 工具分页游标未前进")
            cursor = next_cursor
        raise MCPError("MCP 工具分页超过限制")

    def call_tool(self, name, arguments):
        result = self._send("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise MCPError(f"数据工具 {name} 返回错误")
        if isinstance(result.get("structuredContent"), (dict, list)):
            return result["structuredContent"]
        blocks = [item["text"] for item in result.get("content", []) if item.get("type") == "text"]
        if len(blocks) != 1:
            raise MCPError(f"数据工具 {name} 未返回单一 JSON 数据；需配置专用适配器")
        try:
            return json.loads(blocks[0])
        except json.JSONDecodeError:
            raise MCPError(f"数据工具 {name} 返回非 JSON 数据；需配置专用适配器") from None
