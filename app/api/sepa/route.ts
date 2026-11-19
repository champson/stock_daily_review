import {readReport, validDate} from '../../lib/reports';
import {buildSepaFocus} from '../../lib/sepa-focus.mjs';
export const dynamic = 'force-dynamic';
export async function GET(request: Request) {
  const date = new URL(request.url).searchParams.get('date') || undefined;
  if (date && !validDate(date)) return Response.json({error: '报告日期无效'}, {status: 400});
  try {
    const {report, dates} = await readReport(date);
    const daily = report as {date?: string; sepa?: unknown} | null;
    return Response.json({report: daily?.sepa || null, date: daily?.date || date || null, dates,
      focus: buildSepaFocus(daily)},
      {headers: {'cache-control': 'no-store'}});
  } catch {
    return Response.json({error: '报告存储暂不可用'}, {status: 503});
  }
}
