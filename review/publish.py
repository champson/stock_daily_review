"""通过隐藏 stdin 接收 Site service token；凭据不落盘。"""
import json
import sys
import urllib.request
import urllib.error
from pathlib import Path
from .analysis import validate_rows, LABELS


def publish(url, token, report):
    if not url.startswith("https://") or not token:
        raise ValueError("需要此私有 Site 的 HTTPS 地址与 service access token")
    if report.get("status") == "unavailable" or report.get("skipped"):
        raise ValueError("无有效行情或休市时，不写入报告")
    for kind in LABELS:
        section = report["sections"][kind]
        valid, rejected = validate_rows(section["rows"], report["date"], kind)
        if rejected or section["sample_size"] != len(valid):
            raise ValueError("发布前行情校验失败")
    headers = {"OAI-Sites-Authorization": "Bearer " + token, "Content-Type": "application/json"}
    request = urllib.request.Request(url.rstrip("/") + "/api/reports", json.dumps(report, ensure_ascii=False, allow_nan=False).encode(), headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            saved = json.load(response)["saved"]
        request = urllib.request.Request(url.rstrip("/") + "/api/reports?date=" + report["date"], headers={"OAI-Sites-Authorization": "Bearer " + token})
        with urllib.request.urlopen(request, timeout=45) as response:
            readback = json.load(response)["report"]
    except urllib.error.HTTPError as exc:
        raise ValueError(f"Site HTTP {exc.code}；检查服务访问与 Cloud 网络策略") from None
    except urllib.error.URLError:
        raise ValueError("Site 连接失败") from None
    if readback != report:
        raise ValueError("报告写入后读回不一致；保留存档并检查并发更新")
    return saved


def main():
    # 实际执行时用 exec_command(tty:true) 启动，然后用 write_stdin 发送输入。
    if sys.stdin.isatty():
        import termios
        attrs = termios.tcgetattr(sys.stdin.fileno())
        attrs[3] &= ~termios.ECHO
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, attrs)
    print("Ready for private Site publishing JSON on stdin (input is hidden).", flush=True)
    try:
        options = json.loads(sys.stdin.readline())
        report = json.loads(Path(options["report_path"]).read_text())
        saved = publish(options["url"], options["token"], report)
        print(json.dumps({"saved": saved, "verified": True}, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, OSError):
        print("发布失败；请检查行情格式、私有站点访问及网络配置。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
