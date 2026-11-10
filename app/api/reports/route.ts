import {readReport,writeReport,validDate} from '../../lib/reports';
export const dynamic = 'force-dynamic';
export async function GET(request:Request) {
  const date=new URL(request.url).searchParams.get('date')||undefined;
  if(date&&!validDate(date))return Response.json({error:'报告日期无效'},{status:400});
  try{return Response.json(await readReport(date),{headers:{'cache-control':'no-store'}})}catch{return Response.json({error:'报告存储暂不可用'},{status:503})}
}
// 部署必须保持 Sites owner-private；无人值守写入依赖 Dispatch 的 service access。
// 该通道不创建用户身份或连接外部 App，不可用于 public Site。
export async function POST(request:Request) {
  if(request.headers.get('origin') && request.headers.get('origin')!==new URL(request.url).origin)return Response.json({error:'拒绝跨站写入'},{status:403});
  if(!request.headers.get('content-type')?.includes('application/json'))return Response.json({error:'需要 JSON 请求'},{status:415});
  const text=await request.text();
  if(text.length>5_000_000)return Response.json({error:'报告超过大小限制'},{status:413});
  try {const saved=await writeReport(JSON.parse(text));return Response.json({saved});}catch{return Response.json({error:'报告校验或写入失败'},{status:400})}
}
