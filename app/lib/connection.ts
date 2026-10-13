export const connection = {
  "checked_at": "2026-10-14T04:53:44.289580+08:00",
  "source_priority": [
    "a-stock-data",
    "通达信 MCP"
  ],
  "message": "a-stock-data 主源已验证并生成真实日报；通达信 MCP 备选尚待 API Key 与官方认证格式。",
  "primary": {
    "repository": "https://github.com/simonlin1212/a-stock-data",
    "revision": "29b07d2e37005b9fd568bca1262f6e572b5f9407",
    "quote_access_verified": true,
    "calendar_verified": true,
    "calendar_source": "a-stock-data / 深交所",
    "verified_report_date": "2026-10-13",
    "coverage": {
      "indices": 8,
      "stocks": 5560,
      "etfs": 11,
      "futures": 16,
      "options": 676
    }
  },
  "fallback": {
    "server": "https://mcp.tdx.com.cn:3001/mcp",
    "initialize_verified": true,
    "tools_list_verified": true,
    "tools": [
      "tdx_wenda_quotes"
    ],
    "quote_access_verified": false,
    "auth_format_verified": false,
    "code": "MISSING_API_KEY"
  }
} as const;
