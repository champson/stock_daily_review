import {env} from 'cloudflare:workers';
import {readReport,writeReport,validDate} from '../../lib/reports';
import {authorizeReportWrite} from '../../lib/write-auth';
import {buildMarketMatrix} from '../../lib/market-matrix';
export const dynamic = 'force-dynamic';
export async function GET(request:Request) {
  const date=new URL(request.url).searchParams.get('date')||undefined;
  if(date&&!validDate(date))return Response.json({error:'报告日期无效'},{status:400});
  try{const data=await readReport(date);return Response.json({...data,market_matrix:buildMarketMatrix(data.report)},{headers:{'cache-control':'no-store'}})}catch{return Response.json({error:'报告存储暂不可用'},{status:503})}
}
// Sites service access and report-write authorization are separate permissions.
// Validate the configured write secret before consuming the request body.
export async function POST(request:Request) {
  const denied=authorizeReportWrite(request,(env as unknown as {REVIEW_WRITE_TOKEN?:string}).REVIEW_WRITE_TOKEN);
  if(denied)return denied;
  if(request.headers.get('origin') && request.headers.get('origin')!==new URL(request.url).origin)return Response.json({error:'拒绝跨站写入'},{status:403});
  if(!request.headers.get('content-type')?.includes('application/json'))return Response.json({error:'需要 JSON 请求'},{status:415});
  const text=await request.text();
  if(text.length>5_000_000)return Response.json({error:'报告超过大小限制'},{status:413});
  try {const saved=await writeReport(JSON.parse(text));return Response.json({saved});}catch{return Response.json({error:'报告校验或写入失败'},{status:400})}
}
