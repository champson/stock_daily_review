/** Screenshot-style cross-period statistics, derived without changing stored reports.
 * All returns are percentage points. Missing history is distinct from a zero return.
 */
type Period = 'year' | 'month' | 'week' | 'day';
type Values<T> = Record<Period, T>;
type Quote = Record<string, any>;
type Cell = {value: number | null; reason: string | null};
type MatrixRow = {
  key: string; label: string; unit: 'count' | 'percentage'; signed: boolean;
  values: Values<number | null>; bias25: number | null; coverage: Values<number>;
  reasons: Values<string | null> & {bias25: string | null}; notes: string[];
};
const columns: {key: Period; label: string}[] = [
  {key:'year',label:'今年'}, {key:'month',label:'本月'},
  {key:'week',label:'本周'}, {key:'day',label:'当天'},
];
const periods = columns.map(c => c.key);
const indices = [
  ['sh000001','上证指数'], ['sh000016','上证50'], ['sh000300','沪深300'],
  ['sh000905','中证500'], ['sh000852','中证1000'], ['sh932000','中证2000'],
  ['sz399006','创业板指'], ['sh000985','中证全指'], ['sh000688','科创50'],
  ['bj899050','北证50'],
];
function validDate(value: any): value is string {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) &&
    Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0,10) === value;
}
function positive(value: any): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0;
}
function fill<T>(value: T): Values<T> {return {year:value,month:value,week:value,day:value};}
function percent(current: number, base: number): number | null {
  const result = (current-base)/base*100;
  return Number.isFinite(result) ? result : null;
}
function returnCell(current: number, base: number): Cell {
  const value = percent(current,base);
  return {value,reason:value === null ? '价格比值超出有效数值范围' : null};
}
function cutoffs(day: string): Values<string> {
  const monday = new Date(day+'T00:00:00Z');
  monday.setUTCDate(monday.getUTCDate()-(monday.getUTCDay()+6)%7);
  return {year:day.slice(0,4)+'-01-01',month:day.slice(0,7)+'-01',week:monday.toISOString().slice(0,10),day};
}
function currentQuote(row: any, day: string): row is Quote {
  return !!row && row.date === day && typeof row.symbol === 'string' && !!row.symbol && positive(row.close);
}
// Discard every duplicate, rather than giving whichever copy arrived first a vote.
function uniqueQuotes(value: any, day: string): Quote[] {
  if (!Array.isArray(value)) return [];
  const counts = new Map<string,number>();
  for (const row of value) if (currentQuote(row,day)) counts.set(row.symbol,(counts.get(row.symbol)||0)+1);
  return value.filter(row => currentQuote(row,day) && counts.get(row.symbol) === 1);
}
function validHistory(row: Quote, day: string): {rows: Quote[] | null; reason: string | null} {
  const history = row.history;
  if (!Array.isArray(history) || !history.length) return {rows:null,reason:'缺少历史收盘序列'};
  let previous = '';
  for (const item of history) {
    if (!item || !validDate(item.date) || item.date <= previous || item.date > day || !positive(item.close))
      return {rows:null,reason:'历史日期乱序、重复、越界或价格无效'};
    previous = item.date;
  }
  const last = history[history.length-1];
  if (last.date !== day || Math.abs(last.close-row.close) > Math.max(1,Math.abs(row.close))*1e-10)
    return {rows:null,reason:'历史末日或末价与当日收盘不一致'};
  return {rows:history,reason:null};
}
function returns(row: Quote, day: string, kind: 'stock' | 'index' | 'bond', calendar: any): Values<Cell> {
  const result: Values<Cell> = {
    year:{value:null,reason:null},month:{value:null,reason:null},
    week:{value:null,reason:null},day:{value:null,reason:null},
  };
  result.day = positive(row.previous_close)
    ? returnCell(row.close,row.previous_close)
    : {value:null,reason:'缺少有效前收盘价'};
  const history = validHistory(row,day), boundaries = cutoffs(day);
  for (const period of ['year','month','week'] as Period[]) {
    if (kind === 'stock' && !['qfq','hfq'].includes(row.history_adjustment)) {
      result[period] = {value:null,reason:'缺少同一复权基准的个股历史，暂不计算跨期收益'};
      continue;
    }
    const expected = calendar?.date === day ? calendar?.anchors?.[period] : null;
    if (!validDate(expected) || expected >= boundaries[period]) {
      result[period] = {value:null,reason:'缺少经交易日历核验的周期锚点'};
      continue;
    }
    if (!history.rows) {result[period] = {value:null,reason:history.reason}; continue;}
    const anchor = history.rows.find(h => h.date === expected);
    if (!anchor) {result[period] = {value:null,reason:'历史缺少交易日历指定的周期前收盘锚点 '+expected+'（新上市证券也可能不适用）'}; continue;}
    result[period] = returnCell(row.close,anchor.close);
  }
  return result;
}
function bias(row: Quote, day: string, calendar: any): Cell {
  const expected = calendar?.date === day ? calendar?.bias25_dates : null;
  if (!Array.isArray(expected) || expected.length !== 25 || expected[24] !== day ||
    expected.some((date: any,index: number)=>!validDate(date) || date > day || (index>0 && date<=expected[index-1])))
    return {value:null,reason:'缺少经交易日历核验的最近25个交易日'};
  const history = validHistory(row,day);
  if (!history.rows) return {value:null,reason:history.reason};
  const prices = new Map(history.rows.map(item=>[item.date,item.close as number]));
  const missing = expected.filter((date: string)=>!prices.has(date));
  if (missing.length) return {value:null,reason:'历史缺少交易日历指定的25日窗口收盘价：'+missing.join('、')};
  const mean = expected.reduce((total: number,date: string)=>total+prices.get(date)!/25,0);
  return returnCell(row.close,mean);
}
function baseRow(key: string, label: string, unit: MatrixRow['unit']='percentage', signed=false): MatrixRow {
  return {key,label,unit,signed,values:fill(null),bias25:null,coverage:fill(0),
    reasons:{...fill<string|null>(null),bias25:null},notes:[]};
}
function median(values: number[]): number {
  const ordered = [...values].sort((a,b)=>a-b), middle = Math.floor(ordered.length/2);
  return ordered.length%2 ? ordered[middle] : ordered[middle-1]/2+ordered[middle]/2;
}
function mean(values: number[]): number {return values.reduce((total,value)=>total+value/values.length,0);}
function sample(rows: Quote[], day: string, kind: 'stock' | 'bond', calendar: any) {
  const computed = rows.map(row => returns(row,day,kind,calendar));
  const values = fill<number[]>([]), missing = fill<string|null>(null);
  for (const period of periods) {
    values[period] = computed.map(r=>r[period].value).filter((value): value is number => value !== null);
    const reasons = [...new Set(computed.map(r=>r[period].reason).filter(Boolean))];
    missing[period] = !rows.length ? '尚无通过校验的当日证券样本' :
      values[period].length === rows.length ? null :
      '有效样本 '+values[period].length+' / '+rows.length+'；'+reasons.join('；');
  }
  return {values,missing,total:rows.length};
}
function aggregateRow(key: string, label: string, samples: ReturnType<typeof sample>,
  calculate: (values: number[])=>number, unit: MatrixRow['unit']='percentage', signed=false): MatrixRow {
  const row = baseRow(key,label,unit,signed);
  for (const period of periods) {
    const values = samples.values[period];
    row.coverage[period] = values.length;
    row.values[period] = values.length ? calculate(values) : null;
    row.reasons[period] = samples.missing[period];
  }
  row.reasons.bias25 = 'BIAS25仅在有足够历史的指数行计算';
  row.notes.push('各周期有效收益样本：'+columns.map(c=>c.label+' '+row.coverage[c.key]).join('，')+'；各期分母可不同。');
  return row;
}
// Price division can introduce a last-bit error at an exact threshold.
function above(value: number, boundary: number): boolean {return value > boundary+1e-9;}

export function buildMarketMatrix(report: any) {
  if (!report || !validDate(report.date)) return null;
  const day = report.date, rows: MatrixRow[] = [], calendar = report.market_data?.calendar_basis;
  const stocks = uniqueQuotes(report.sections?.stocks?.rows,day);
  const stockSample = sample(stocks,day,'stock',calendar);
  const count = (predicate: (value:number)=>boolean) => (values:number[]) => values.filter(predicate).length;
  const ratio = (predicate: (value:number)=>boolean) => (values:number[]) => count(predicate)(values)/values.length*100;
  const definitions: [string,string,(values:number[])=>number,MatrixRow['unit'],boolean?][] = [
    ['stock_total',report.sections?.stocks?.universe_complete === true ? 'A股总数量（有效收益样本）' : 'A股有效样本数',v=>v.length,'count'],
    ['stock_up','A股上涨数量',count(v=>v>0),'count'],
    ['stock_down','A股下跌数量',count(v=>v<0),'count'],
    ['stock_flat','A股零涨幅数量',count(v=>v===0),'count'],
    ['bin_gt10','涨幅 > 10%',ratio(v=>above(v,10)),'percentage'],
    ['bin_5_10','涨幅 > 5% 至 10%',ratio(v=>above(v,5)&&!above(v,10)),'percentage'],
    ['bin_0_5','涨幅 > 0% 至 5%',ratio(v=>v>0&&!above(v,5)),'percentage'],
    ['bin_m5_0','涨幅 > -5% 至 0%',ratio(v=>above(v,-5)&&v<=0),'percentage'],
    ['bin_m10_m5','涨幅 > -10% 至 -5%',ratio(v=>above(v,-10)&&!above(v,-5)),'percentage'],
    ['bin_le_m10','涨幅 ≤ -10%',ratio(v=>!above(v,-10)),'percentage'],
    ['stock_up_ratio','A股上涨比例',ratio(v=>v>0),'percentage'],
    ['stock_down_ratio','A股下跌比例',ratio(v=>v<0),'percentage'],
    ['stock_mean','A股算术平均涨幅',mean,'percentage',true],
    ['stock_median','A股涨幅中位数',median,'percentage',true],
  ];
  for (const [key,label,calculate,unit,signed] of definitions) rows.push(aggregateRow(key,label,stockSample,calculate,unit,signed));
  const convertibleSection = report.market_data?.convertibles;
  const bonds = (convertibleSection?.date && convertibleSection.date !== day) ||
    convertibleSection?.mapping_filter_date !== day ? [] :
    uniqueQuotes(convertibleSection?.rows,day).filter(row => row.status !== 'upcoming' && row.status !== 'delisted' &&
      (!row.listing_date || (validDate(row.listing_date) && row.listing_date <= day)) &&
      (!row.delist_date || (validDate(row.delist_date) && row.delist_date > day)));
  const bondSample = sample(bonds,day,'bond',calendar);
  const linked = new Set(bonds.map(r=>r.underlying_symbol).filter((symbol):symbol is string=>typeof symbol === 'string' && !!symbol));
  const underlyingSample = sample(stocks.filter(row=>linked.has(row.symbol)),day,'stock',calendar);
  for (const [key,label,values] of [
    ['bond','可转债',bondSample],['underlying','正股',underlyingSample],
  ] as const) {
    const average = aggregateRow(key+'_mean',label+'算术平均涨幅',values,mean,'percentage',true);
    const mid = aggregateRow(key+'_median',label+'涨幅中位数',values,median,'percentage',true);
    if (key === 'bond') {
      average.notes.push('转债按证券等权，计算价格收益；不含票息，不作为可转债等权指数。');
      mid.notes.push('仅包含报告日已上市且有有效行情的转债。');
    } else {
      average.notes.push('按关联正股证券代码去重，多只转债对应同一正股只计算一次。');
      mid.notes.push('正股仅从当日报告A股样本匹配；未匹配或缺少收益的证券不参与统计。');
    }
    const sourceNotes = Array.isArray(convertibleSection?.observations) ? convertibleSection.observations.filter((item: any)=>typeof item === 'string') : [];
    average.notes.push(...sourceNotes); mid.notes.push(...sourceNotes);
    rows.push(average,mid);
  }
  const indexRows = new Map(uniqueQuotes(report.sections?.indices?.rows,day).map(row=>[row.symbol,row]));
  for (const [symbol,label] of indices) {
    const row = baseRow('index_'+symbol,label,'percentage',true), quote = indexRows.get(symbol);
    if (quote) {
      const computed = returns(quote,day,'index',calendar), computedBias = bias(quote,day,calendar);
      for (const period of periods) {
        row.values[period] = computed[period].value;
        row.coverage[period] = computed[period].value === null ? 0 : 1;
        row.reasons[period] = computed[period].reason;
      }
      row.bias25 = computedBias.value; row.reasons.bias25 = computedBias.reason;
      if (typeof quote.price_basis === 'string') row.notes.push(quote.price_basis);
    } else {
      for (const period of periods) row.reasons[period] = '未取得该指数的当日有效行情';
      row.reasons.bias25 = '未取得该指数历史收盘序列';
    }
    row.notes.push('证券代码 '+symbol+'；跨期收益基于指数价格，不含分红再投资。');
    rows.push(row);
  }
  for (const [key,label] of [
    ['special_microcap','微盘股指数'], ['special_ths_underlying','同花顺正股等权'],
    ['special_convertible_equal_weight','可转债等权'],
  ]) {
    const row = baseRow(key,label,'percentage',true), reason = '待核验原图使用的指数代码、编制口径及授权数据源';
    for (const period of periods) row.reasons[period] = reason;
    row.reasons.bias25 = reason; row.notes.push(reason); rows.push(row);
  }
  return {version:1,date:day,columns,rows,notes:[
    '今年／本月／本周分别相对官方交易日历指定的年初、月初、周一之前最近交易日收盘；缺少日历或精确锚点时留空。当天相对前收盘。',
    'A股与正股跨期收益仅接受同一前复权或后复权基准的历史；新上市或缺少锚点的证券不进入对应周期样本。',
    '六档分布使用该周期同一有效样本作分母；(-5%,0%]含零涨幅，最低档为≤-10%，避免遗漏恰好-10%的证券。显示四舍五入可能使六档合计略偏离100%。',
    'BIAS25 = (当日收盘 / 最近25个交易日收盘算术平均值 − 1) × 100%，均线包含当天；25个日期须全部经交易日历核验并有收盘价。缺失值显示为—，不视作0。',
    report.sections?.stocks?.universe_complete === true ? 'A股统计按报告声明的完整证券范围计算。' : '当前A股统计为已获取的有效样本，未经上市名册核对，不代表全市场总数。',
    ...(Array.isArray(calendar?.observations) ? calendar.observations.filter((item: any)=>typeof item === 'string') : []),
  ]};
}
