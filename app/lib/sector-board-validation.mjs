const method = 'ln-close-minus-ema20-ln-close-v1';
function day(v) { return typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v) && Number.isFinite(Date.parse(v)) && new Date(v).toISOString().slice(0, 10) === v; }
function text(v) { return typeof v === 'string' && v.length > 0; }
function positive(v) { return typeof v === 'number' && Number.isFinite(v) && v > 0; }
function near(a, b) { return typeof a === 'number' && Number.isFinite(a) && Math.abs(a - b) < 1e-7; }
function fail() { throw new Error('板块EMA观察格式、日期或计算值无效'); }
export function validateSectorBoard(board, reportDate) {
    if (!board || board.version !== 1 || board.date !== reportDate || board.method !== method || board.period !== 20 || board.minimum_history !== 250 || board.formula_url !== 'https://xueqiu.com/3559889031/354857867' || !['available', 'partial', 'unavailable'].includes(board.status) || !Array.isArray(board.groups) || board.groups.length > 2 || !Array.isArray(board.observations) || !Array.isArray(board.provenance))
        fail();
    const groups = new Set(), securities = new Set();
    let valid = 0;
    for (const g of board.groups) {
        if (!g || !['CN', 'HK'].includes(g.key) || groups.has(g.key) || !text(g.name) || !Array.isArray(g.dates) || ![0, 10].includes(g.dates.length) || !Array.isArray(g.rows) || g.rows.length > 100 || g.configured_count !== g.rows.length || !Number.isInteger(g.sample_size) || !Array.isArray(g.observations))
            fail();
        groups.add(g.key);
        if (g.dates.some((d, i) => !day(d) || d > reportDate || (i > 0 && d >= g.dates[i - 1])) || g.previous_date !== (g.dates[1] || null))
            fail();
        if (g.dates.length && Date.parse(reportDate) - Date.parse(g.dates[0]) > 20 * 86400000)
            fail();
        if (g.key==='HK' && (!Array.isArray(g.history_dates) || g.history_dates.some((d,i)=>!day(d)||d>reportDate||(i>0&&d<=g.history_dates[i-1])))) fail();
        let count = 0;
        for (const r of g.rows) {
            if (!r || !['name', 'official_name', 'code', 'issuer', 'currency', 'identity_url', 'secid', 'price_basis'].every(k => text(r[k])) || !['CNY', 'HKD'].includes(r.currency) || r.currency !== (g.key === 'CN' ? 'CNY' : 'HKD') || !r.identity_url.startsWith('https://') || securities.has(r.secid) || r.date !== (g.dates[0] || null) || !Array.isArray(r.history) || !Array.isArray(r.values) || typeof r.supplemental !== 'boolean')
                fail();
            securities.add(r.secid);
            if (r.status === 'unavailable') {
                if (!text(r.reason) || r.close !== null || r.change_pct !== null || r.history.length || r.values.length || r.provenance !== null)
                    fail();
                continue;
            }
            if (r.status !== 'available' || g.dates.length !== 10 || r.reason !== null || r.history.length < 259 || r.history.length > 640 || !positive(r.close) || !r.provenance || !['eastmoney','csindex','cnindex'].includes(r.provenance.provider) || !text(r.provenance.source_name) || !r.provenance.source_url?.startsWith({eastmoney:'https://push2his.eastmoney.com/',csindex:'https://www.csindex.com.cn/',cnindex:'https://hq.cnindex.com.cn/'}[r.provenance.provider]) || !Number.isFinite(Date.parse(r.provenance.fetched_at)))
                fail();
            let previous = '', ema;
            const values = new Map(), prices = new Map();
            for (let i = 0; i < r.history.length; i++) {
                const h = r.history[i];
                if (!h || !day(h.date) || h.date <= previous || h.date > r.date || !positive(h.close))
                    fail();
                previous = h.date;
                const log = Math.log(h.close);
                ema = ema === undefined ? log : 2 / 21 * log + 19 / 21 * ema;
                prices.set(h.date, h.close);
                if (i >= 249)
                    values.set(h.date, 100 * (log - ema));
            }
            if(g.key==='HK') {
                const expected=g.history_dates.filter(d=>d>=r.history[0].date);
                if(expected.length!==r.history.length||r.history.some((h,i)=>h.date!==expected[i]))fail();
            }
            if (previous !== r.date || !near(r.close, prices.get(r.date)) || !prices.has(g.previous_date) || !near(r.change_pct, (r.close / prices.get(g.previous_date) - 1) * 100) || r.values.length !== 10)
                fail();
            for (let i = 0; i < 10; i++) {
                const p = r.values[i];
                if (!p || p.date !== g.dates[i] || !values.has(p.date) || !near(p.value, values.get(p.date)))
                    fail();
            }
            count++;
        }
        const status = count === 0 ? 'unavailable' : count === g.rows.length ? 'available' : 'partial';
        if (g.sample_size !== count || g.status !== status)
            fail();
        valid += count;
    }
    const expected = valid === 0 ? 'unavailable' : board.groups.every((g) => g.status === 'available') ? 'available' : 'partial';
    if (board.status !== expected)
        fail();
}
