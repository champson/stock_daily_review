// Use the report's verified exchange calendar, never weekdays or a stock's
// candle count (which can omit suspension days), to identify recent breakouts.
export const RECENT_SESSIONS = 5;
const isDate = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) &&
  Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;

export function buildSepaFocus(daily) {
  const report = daily?.sepa;
  const basis = daily?.market_data?.calendar_basis;
  const dates = basis?.bias25_dates;
  const verified = report?.date === daily?.date && basis?.date === daily?.date &&
    Array.isArray(dates) && dates.length === 25 && dates.at(-1) === daily.date &&
    dates.every((date, i) => isDate(date) && date <= daily.date && (!i || date > dates[i - 1]));
  const sessions = verified ? dates : [];
  const window = sessions.slice(-RECENT_SESSIONS);
  const counts = {focus: 0, ready: 0, recent: 0, older: 0, unknown: 0};
  const rows = {};
  for (const row of report?.rows || []) {
    const vcp = row.vcp || {}, breakout = vcp.breakout;
    const date = isDate(breakout?.date) && breakout.date <= report.date ? breakout.date : null;
    const index = sessions.indexOf(date);
    const confirmed = breakout?.volume_confirmed === true;
    let state = 'other';
    if (row.trend?.status === 'pass' && vcp.metrics?.setup_active === true) {
      if (row.category === 'A' && vcp.state === 'ready') state = 'ready';
      if (row.category === 'B' && ['breakout', 'retest'].includes(vcp.state) && confirmed) {
        state = !verified || !date ? 'unknown' : window.includes(date) ? 'recent' :
          date < window[0] ? 'older' : 'unknown';
      }
    }
    rows[row.symbol] = {state, breakout_date: date, breakout_confirmed: confirmed,
      age_sessions: index < 0 ? null : sessions.length - 1 - index};
    if (state in counts) counts[state]++;
    if (state === 'ready' || state === 'recent') counts.focus++;
  }
  return {window_sessions: RECENT_SESSIONS, window_dates: window,
    calendar_status: verified ? 'available' : 'unavailable', counts, rows};
}
