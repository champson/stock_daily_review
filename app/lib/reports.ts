import { env } from 'cloudflare:workers';
import { validateReport } from './report-validation';
export { kinds, validDate, validateReport } from './report-validation';
export function bucket(): R2Bucket {
  const value = (env as unknown as {BUCKET?: R2Bucket}).BUCKET;
  if (!value) throw new Error('报告存储尚未绑定');
  return value;
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
