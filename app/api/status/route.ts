import {readReport} from '../../lib/reports';
import {connection} from '../../lib/connection';
export const dynamic='force-dynamic';
export async function GET(){try{const {report,dates}=await readReport();return Response.json({latest:dates[0]||null,status:(report as any)?.status||'awaiting_data',source:(report as any)?.source||'a-stock-data',source_connection:connection,schedule:{planned_time:'18:00',timezone:'Asia/Shanghai',enabled:null}},{headers:{'cache-control':'no-store'}})}catch{return Response.json({status:'storage_unavailable'},{status:503})}}
