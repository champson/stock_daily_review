export const kinds = ['indices','stocks','etfs','futures','options'];
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
  if (!value || value.schema_version!==1 || !validDate(value.date) || !['a-stock-data','通达信 MCP','a-stock-data / 通达信 MCP'].includes(value.source) || !['complete','partial'].includes(value.status) || value.timezone!=='Asia/Shanghai') throw new Error('报告格式无效');
  if(typeof value.generated_at!=='string' || Number.isNaN(Date.parse(value.generated_at)) || Date.parse(value.generated_at)>Date.now()+600000) throw new Error('生成时间无效');
  let samples=0;
  for (const kind of kinds) {
    const s=value.sections?.[kind];
    if(!s || !['available','partial','unavailable'].includes(s.status) || !Array.isArray(s.rows) || !Array.isArray(s.observations) || typeof s.metrics!=='object' || s.metrics===null || s.sample_size!==s.rows.length || s.rows.length>20000) throw new Error('模块格式无效：'+kind);
    const symbols=new Set<string>();
    for (const row of s.rows) {
      if(row.date!==value.date || typeof row.symbol!=='string' || !row.symbol || symbols.has(row.symbol) || typeof row.close!=='number' || row.close<0 || (kind!=='options'&&row.close===0)) throw new Error('行情日期或代码无效');
      symbols.add(row.symbol);
      if(kind==='options' && !['call','put'].includes(row.option_type))throw new Error('期权类型无效');
    }
    samples+=s.rows.length;
  }
  if(!samples)throw new Error('没有有效行情，不写入报告');
}
