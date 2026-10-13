import {readReport} from '../../lib/reports';
export const dynamic='force-dynamic';
export async function GET(){try{const {report,dates}=await readReport();return Response.json({latest:dates[0]||null,status:(report as any)?.status||'awaiting_data',source:'通达信 MCP',schedule:{planned_time:'18:00',timezone:'Asia/Shanghai',enabled:null}})}catch{return Response.json({status:'storage_unavailable'},{status:503})}}
