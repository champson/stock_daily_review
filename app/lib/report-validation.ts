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
function positive(value: any): value is number {
  return typeof value==='number' && Number.isFinite(value) && value>0;
}
function validatePeriodPrices(value: any, report: any): void {
  if(!value || value.version!==1 || value.date!==report.date || value.method!=='tdx-affine-qfq' || !value.anchors || typeof value.anchors!=='object' || Array.isArray(value.anchors) || !Array.isArray(value.observations) || !Array.isArray(value.provenance))throw new Error('区间端点版本、日期或复权方法无效');
  const calendar=report.market_data?.calendar_basis;
  for(const period of ['year','month','week'] as const) {
    const anchor=value.anchors[period],expected=calendar?.date===report.date?calendar.anchors[period]:null;
    if(anchor!==expected || anchor!==null && !validDate(anchor))throw new Error('区间端点与交易日历基准不一致');
  }
  for(const kind of ['stocks','convertibles'] as const) {
    const pool=value[kind];
    if(!pool || !['available','partial','unavailable'].includes(pool.status) || !Array.isArray(pool.rows) || pool.rows.length>20000 || pool.sample_size!==pool.rows.length || !Array.isArray(pool.observations))throw new Error('区间端点数据集格式无效');
    const quotes=kind==='stocks'?report.sections.stocks.rows:report.market_data?.convertibles?.rows||[];
    const current=new Map<string,any>(Array.isArray(quotes)?quotes.filter(row=>row&&typeof row.symbol==='string').map(row=>[row.symbol,row]):[]);
    const seen=new Set<string>();
    for(const row of pool.rows) {
      const quote=row&&current.get(row.symbol);
      if(!row || typeof row.symbol!=='string' || !row.symbol || seen.has(row.symbol) || !quote || !positive(row.close) || !positive(quote.close) || Math.abs(row.close-quote.close)>Math.max(1,Math.abs(quote.close))*1e-10)throw new Error('区间端点证券重复、未匹配或末价不一致');
      seen.add(row.symbol);
      for(const period of ['year','month','week'] as const) {
        const point=row[period];
        if(point===null)continue;
        if(!point || !validDate(point.date) || point.date!==value.anchors[period] || !positive(point.raw_close) || kind==='stocks'&&!positive(point.adjusted_close))throw new Error('区间端点基准日或价格无效');
      }
    }
  }
}
function validateSpecialIndices(value: any, report: any): void {
  const allowed=new Set(['special_microcap','special_ths_underlying','special_convertible_equal_weight']);
  if(!value || value.date!==report.date || !['available','partial','unavailable'].includes(value.status) || !Array.isArray(value.rows) || value.rows.length>allowed.size || value.sample_size!==value.rows.length || !Array.isArray(value.observations) || !Array.isArray(value.provenance) || value.errors!==undefined&&!Array.isArray(value.errors))throw new Error('特色指数数据集格式或日期无效');
  const keys=new Set<string>(),symbols=new Set<string>();
  for(const row of value.rows) {
    if(!row || !allowed.has(row.matrix_key) || keys.has(row.matrix_key) || typeof row.symbol!=='string' || !row.symbol || symbols.has(row.symbol) || typeof row.name!=='string' || !row.name || typeof row.provider!=='string' || !row.provider || row.date!==report.date || !positive(row.close) || row.previous_close!==undefined&&row.previous_close!==null&&!positive(row.previous_close) || !Array.isArray(row.history) || !row.history.length || row.history.length>20000)throw new Error('特色指数标识、日期或行情无效');
    keys.add(row.matrix_key);symbols.add(row.symbol);
    let previous='';
    for(const point of row.history) {
      if(!point || !validDate(point.date) || point.date<=previous || point.date>report.date || !positive(point.close))throw new Error('特色指数历史日期或价格无效');
      previous=point.date;
    }
    const last=row.history[row.history.length-1];
    if(last.date!==report.date || Math.abs(last.close-row.close)>Math.max(1,Math.abs(row.close))*1e-10)throw new Error('特色指数历史末日或末价不匹配');
  }
  for(const error of value.errors||[]) {
    if(!error || !allowed.has(error.matrix_key) || typeof error.message!=='string' || !error.message || error.category!=='source_failed')throw new Error('特色指数失败说明格式无效');
  }
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
  if(value.market_data!==undefined) {
    if(!value.market_data || typeof value.market_data!=='object' || Array.isArray(value.market_data))throw new Error('市场补充数据格式无效');
    const calendar=value.market_data.calendar_basis;
    if(calendar!==undefined) {
      if(!calendar || calendar.date!==value.date || !calendar.anchors || typeof calendar.anchors!=='object' || Array.isArray(calendar.anchors) || !Array.isArray(calendar.bias25_dates) || ![0,25].includes(calendar.bias25_dates.length) || !Array.isArray(calendar.observations) || !Array.isArray(calendar.provenance))throw new Error('交易日历统计基准格式无效');
      const monday=new Date(value.date+'T00:00:00Z');monday.setUTCDate(monday.getUTCDate()-(monday.getUTCDay()+6)%7);
      const starts={year:value.date.slice(0,4)+'-01-01',month:value.date.slice(0,7)+'-01',week:monday.toISOString().slice(0,10)};
      for(const period of ['year','month','week'] as const) {
        const anchor=calendar.anchors[period];
        if(anchor!==null && (!validDate(anchor) || anchor>=starts[period]))throw new Error('交易日历周期锚点无效');
      }
      for(let i=0;i<calendar.bias25_dates.length;i++) {
        const date=calendar.bias25_dates[i];
        if(!validDate(date) || date>value.date || (i>0 && date<=calendar.bias25_dates[i-1]))throw new Error('交易日历25日窗口日期无效');
      }
      if(calendar.bias25_dates.length && calendar.bias25_dates[24]!==value.date)throw new Error('交易日历25日窗口末日无效');
    }
    if(value.market_data.period_prices!==undefined)validatePeriodPrices(value.market_data.period_prices,value);
    if(value.market_data.special_indices!==undefined)validateSpecialIndices(value.market_data.special_indices,value);
    const sectors=value.market_data.sector_indices;
    if(sectors!==undefined) {
      if(!sectors || sectors.date!==value.date || !['available','partial','unavailable'].includes(sectors.status) || !Array.isArray(sectors.rows) || !Number.isInteger(sectors.configured_count) || sectors.configured_count<0 || sectors.configured_count>1000 || sectors.rows.length>sectors.configured_count || sectors.sample_size!==sectors.rows.length || !Array.isArray(sectors.observations) || !Array.isArray(sectors.provenance))throw new Error('行业观察指数格式无效');
      const previous=sectors.previous_trading_date;
      if((sectors.rows.length>0 || previous!==undefined && previous!==null) && (!validDate(previous) || previous>=value.date || Date.parse(value.date)-Date.parse(previous)>20*86400000))throw new Error('行业观察指数前交易日无效');
      const symbols=new Set<string>();
      for(const row of sectors.rows) {
        if(!row || row.date!==value.date || row.previous_date!==previous || typeof row.symbol!=='string' || !row.symbol || symbols.has(row.symbol) || typeof row.name!=='string' || !row.name || typeof row.close!=='number' || row.close<=0 || typeof row.previous_close!=='number' || row.previous_close<=0 || typeof row.change_pct!=='number' || Math.abs(row.change_pct-(row.close-row.previous_close)/row.previous_close*100)>0.000051)throw new Error('行业观察指数日期、价格或涨跌幅无效');
        symbols.add(row.symbol);
      }
    }
    const s=value.market_data.convertibles;
    if(s!==undefined) {
      if(!s || !['available','partial','unavailable'].includes(s.status) || !Array.isArray(s.rows) || s.rows.length>20000 || s.sample_size!==s.rows.length || !Array.isArray(s.observations) || (s.rows.length>0 && s.mapping_filter_date!==value.date) || (s.mapping_filter_date!==undefined && s.mapping_filter_date!==value.date) || (s.date!==undefined && s.date!==value.date))throw new Error('可转债补充数据格式或映射日期无效');
      const symbols=new Set<string>();
      for(const row of s.rows) {
        if(!row || row.date!==value.date || typeof row.symbol!=='string' || !row.symbol || symbols.has(row.symbol) || typeof row.close!=='number' || row.close<=0 || (row.underlying_symbol!==undefined && (typeof row.underlying_symbol!=='string' || !row.underlying_symbol)))throw new Error('可转债行情日期、代码或价格无效');
        if((row.listing_date!==undefined && row.listing_date!==null && (!validDate(row.listing_date) || row.listing_date>value.date)) || (row.delist_date!==undefined && row.delist_date!==null && (!validDate(row.delist_date) || row.delist_date<=value.date)) || ['upcoming','delisted'].includes(row.status))throw new Error('可转债不属于报告日上市范围');
        symbols.add(row.symbol);
      }
    }
  }
  if(!samples)throw new Error('没有有效行情，不写入报告');
}
