export const categories = ['A', 'B', 'C', 'D', 'insufficient', 'unavailable'];
const barFields = ['date', 'open', 'high', 'low', 'close', 'volume', 'ma50', 'ma150', 'ma200'];
const day = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) &&
  Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
const finite = value => typeof value === 'number' && Number.isFinite(value);
const positive = value => finite(value) && value > 0;

export function validateSepa(value, reportDate) {
  if (!value || value.schema_version !== 1 || value.date !== reportDate ||
      !['available', 'partial', 'unavailable'].includes(value.status) ||
      typeof value.generated_at !== 'string' || !Number.isFinite(Date.parse(value.generated_at)) ||
      !Array.isArray(value.rows) || value.rows.length > 500 || !value.groups ||
      !Array.isArray(value.observations) || value.observations.some(x => typeof x !== 'string') ||
      JSON.stringify(value.bar_fields) !== JSON.stringify(barFields)) throw new Error('SEPA 格式或日期无效');
  if (value.status === 'unavailable') {
    if (value.rows.length || value.candidate_count !== null || !value.observations.length) throw new Error('SEPA 失败不能伪装为空名单');
  } else {
    if (value.candidate_count !== value.rows.length || value.candidate_source?.provider !== 'daxiapi' ||
        value.candidate_source?.data_date !== reportDate || value.candidate_source?.pool_complete !== false ||
        !Number.isFinite(Date.parse(value.candidate_source?.fetched_at))) throw new Error('SEPA 候选日期或范围无效');
    if (value.rows.length) {
      const market = value.market_rs, c = market?.coverage;
      if (market?.date !== reportDate || market?.status !== 'available' || !c ||
          !Number.isInteger(c.valid_count) || !Number.isInteger(c.eligible_count) || c.valid_count < 1000 ||
          c.eligible_count < c.valid_count || !finite(c.ratio) || c.ratio < 0.9 ||
          Math.abs(c.ratio - c.valid_count / c.eligible_count) > 1e-10) throw new Error('SEPA RS 覆盖不足');
    }
  }
  const counts = Object.fromEntries(categories.map(key => [key, 0])), seen = new Set();
  for (const row of value.rows) {
    if (!row || !/^(sh|sz|bj)\d{6}$/.test(row.symbol) || seen.has(row.symbol) ||
        typeof row.name !== 'string' || !categories.includes(row.category) ||
        !Array.isArray(row.bars) || row.bars.length > 120 ||
        row.rs_percentile !== null && (!finite(row.rs_percentile) || row.rs_percentile < 0 || row.rs_percentile > 100)) throw new Error('SEPA 证券记录无效');
    seen.add(row.symbol); counts[row.category]++;
    if (row.category === 'unavailable') {
      if (row.bars.length || typeof row.reason !== 'string' || !row.reason) throw new Error('SEPA 缺失行情说明无效');
      continue;
    }
    if (!positive(row.close) || !row.bars.length || row.quote?.adjustment !== 'qfq' ||
        row.quote?.volume_unit !== 'shares' || row.quote?.source !== 'tencent' ||
        row.trend?.date !== reportDate || !['pass', 'fail', 'insufficient'].includes(row.trend?.status) ||
        !Array.isArray(row.trend?.checks) || row.trend.checks.length !== 8 ||
        row.trend.checks.some(c => !c || typeof c.label !== 'string' || !['pass','fail','insufficient'].includes(c.status)) ||
        row.vcp?.as_of !== reportDate || typeof row.vcp?.state !== 'string' ||
        !Array.isArray(row.vcp?.swings)) throw new Error('SEPA 分析证据无效');
    if (['A', 'B', 'C'].includes(row.category) && row.trend.status !== 'pass') throw new Error('SEPA 趋势分类矛盾');
    let previous = '';
    for (const bar of row.bars) {
      if (!Array.isArray(bar) || bar.length !== 9 || !day(bar[0]) || bar[0] <= previous || bar[0] > reportDate ||
          !bar.slice(1, 5).every(positive) || bar[3] > Math.min(bar[1], bar[4]) || bar[2] < Math.max(bar[1], bar[4]) ||
          bar[5] !== null && (!finite(bar[5]) || bar[5] < 0) ||
          !bar.slice(6).every(v => v === null || positive(v))) throw new Error('SEPA 图表日期、价格或单位无效');
      previous = bar[0];
    }
    const last = row.bars.at(-1);
    if (last[0] !== reportDate || last[4] !== row.close) throw new Error('SEPA 图表末价与报告不符');
  }
  if (Object.keys(value.groups).length !== categories.length || categories.some(key => value.groups[key] !== counts[key])) throw new Error('SEPA 分类数量不一致');
  if (value.status === 'available' && (counts.insufficient || counts.unavailable)) throw new Error('SEPA 可用状态与行数据矛盾');
}
