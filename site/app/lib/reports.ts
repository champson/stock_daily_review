import { env } from 'cloudflare:workers';
export const kinds = ['indices','stocks','etfs','futures','options'];
export function bucket(): R2Bucket {
  const value = (env as unknown as {BUCKET?: R2Bucket}).BUCKET;
  if (!value) throw new Error('报告存储尚未绑定');
  return value;
}
export function validDate(value: unknown): value is string {
  return typeof value==='string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(value)) && new Date(value).toISOString().slice(0,10)===value;
}
function finiteNumbers(value: any, depth=0): boolean {
  if(depth>20)return false;
  if(typeof value==='number')return Number.isFinite(value);
  if(Array.isArray(value))return value.every(v=>finiteNumbers(v,depth+1));
  if(value&&typeof value==='object')return Object.values(value).every(v=>finiteNumbers(v,depth+1));
  return true;
}
export function validateReport(value: any): void {
  if(!finiteNumbers(value))throw new Error('数值或嵌套结构无效');
  if (!value || value.schema_version!==1 || !validDate(value.date) || value.source!=='通达信 MCP' || !['complete','partial','unavailable'].includes(value.status) || value.timezone!=='Asia/Shanghai') throw new Error('报告格式无效');
  if(typeof value.generated_at!=='string' || Number.isNaN(Date.parse(value.generated_at)) || Date.parse(value.generated_at)>Date.now()+600000) throw new Error('生成时间无效');
  for (const kind of kinds) {
    const s=value.sections?.[kind];
    if(!s || !['available','partial','unavailable'].includes(s.status) || !Array.isArray(s.rows) || !Array.isArray(s.observations) || typeof s.metrics!=='object' || s.metrics===null || s.sample_size!==s.rows.length || s.rows.length>20000) throw new Error('模块格式无效：'+kind);
    for (const row of s.rows) if(row.date!==value.date || typeof row.symbol!=='string' || !row.symbol || typeof row.close!=='number' || row.close<0 || (kind!=='options'&&row.close===0)) throw new Error('行情日期或代码无效');
  }
}
export async function listKeys(): Promise<string[]> {
  const keys: string[]=[];
  let cursor: string|undefined;
  do {
    const page=await bucket().list({prefix:'reports/',limit:1000,cursor});
    keys.push(...page.objects.map(o=>o.key));
    cursor=page.truncated?page.cursor:undefined;
  } while(cursor);
  return keys.filter(k=>/^reports\/\d{4}-\d{2}-\d{2}\/\d+-[a-f0-9]+\.json$/.test(k)).sort().reverse();
}
export async function readReport(date?: string) {
  const keys=await listKeys(),dates=[...new Set(keys.map(k=>k.split('/')[1]))];
  const key=keys.find(k=>!date||k.split('/')[1]===date);
  const object=key?await bucket().get(key):null;
  return {report:object?await object.json():null,dates};
}
export async function writeReport(report: unknown) {
  validateReport(report);
  const data=JSON.stringify(report),r=report as any;
  const hash=[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(data)))].map(x=>x.toString(16).padStart(2,'0')).join('');
  const stamp=new Date(r.generated_at).toISOString().replace(/\D/g,'');
  const key='reports/'+r.date+'/'+stamp+'-'+hash+'.json';
  await bucket().put(key,data,{httpMetadata:{contentType:'application/json; charset=utf-8'}});
  const check=await bucket().get(key);
  if(!check || await check.text()!==data) throw new Error('报告写入后校验失败');
  return {date:r.date,status:r.status,key,sha256:hash};
}
