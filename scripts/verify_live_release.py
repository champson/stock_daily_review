"""验证生产提交与构建清单一致；写权限检查不创建日报。"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from review.mcp_client import NoRedirect
from review.publish import derive_write_token


def verify(url, release, token=None):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.path not in ('','/') or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError('需要get_site返回的HTTPS站点origin')
    origin=url.rstrip('/')
    opener=urllib.request.build_opener(NoRedirect())
    service={'OAI-Sites-Authorization':'Bearer '+token} if token else {}
    def request(path,body=None,headers=None):
        r=urllib.request.Request(origin+path,data=body,headers=headers or {},method='POST' if body is not None else 'GET')
        try:
            with opener.open(r,timeout=45) as response:
                return response.status,response.read()
        except urllib.error.HTTPError as e:
            return e.code,e.read()
    status,raw=request('/api/build-info',headers=service)
    if status!=200:raise ValueError('版本接口读取失败')
    actual=json.loads(raw)
    for field in ('project_id','commit_sha','tree_sha','github_repository'):
        if actual.get(field)!=release.get(field):raise ValueError('线上版本与构建清单不同：'+field)
    if actual.get('dirty') is not False:raise ValueError('线上构建来自未提交源码')
    status,body=request('/',headers=service)
    if status!=200 or '收盘之后'.encode() not in body:raise ValueError('页面检查失败')
    status,raw=request('/api/reports',headers=service)
    if status!=200:raise ValueError('历史报告读取失败')
    reports=json.loads(raw)
    rejected,_=request('/api/reports',b'{}',{'Content-Type':'application/json'})
    if rejected!=401:raise ValueError('匿名报告写入未被鉴权拒绝')
    if token:
        headers={**service,'Content-Type':'application/json','X-Review-Write-Token':derive_write_token(token)}
        status,_=request('/api/reports',b'{}',headers)
        if status!=400:raise ValueError('服务写入凭据未通过验证')
    return {'verified':True,'url':origin,'commit_sha':actual['commit_sha'],'tree_sha':actual['tree_sha'],'latest_report_date':(reports.get('report') or {}).get('date'),'report_dates':len(reports.get('dates',[])),'anonymous_write_status':rejected,'service_auth_verified':bool(token),'report_records_written':0}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',required=True);parser.add_argument('--release',required=True);parser.add_argument('--with-service-access',action='store_true');args=parser.parse_args()
    token=None
    if args.with_service_access:
        if sys.stdin.isatty():
            import termios
            attr=termios.tcgetattr(sys.stdin.fileno());attr[3]&=~termios.ECHO;termios.tcsetattr(sys.stdin.fileno(),termios.TCSANOW,attr)
        print('Ready for verification service access JSON on stdin (input is hidden).',flush=True)
        token=json.loads(sys.stdin.readline())['token']
    try:
        result=verify(args.url,json.loads(Path(args.release).read_text()),token)
        print(json.dumps(result,ensure_ascii=False))
        return 0
    except (ValueError,KeyError,urllib.error.URLError):
        print('生产版本或访问验证失败；未创建报告记录。',file=sys.stderr)
        return 1


if __name__=='__main__':sys.exit(main())
