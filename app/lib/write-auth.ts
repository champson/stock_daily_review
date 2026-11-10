import {timingSafeEqual} from 'node:crypto';

const hexadecimalToken = /^[a-f0-9]{64}$/;
const encoder = new TextEncoder();

// Public report reads remain open; writes require an application-level secret.
// Both valid encodings are exactly 64 ASCII bytes before constant-time comparison.
export function authorizeReportWrite(request: Request, expectedToken: unknown): Response | null {
  if (typeof expectedToken !== 'string' || !hexadecimalToken.test(expectedToken)) {
    return Response.json({error:'报告写入授权尚未配置'}, {status:503});
  }
  const suppliedToken = request.headers.get('X-Review-Write-Token');
  if (!suppliedToken || !hexadecimalToken.test(suppliedToken) ||
      !timingSafeEqual(encoder.encode(suppliedToken), encoder.encode(expectedToken))) {
    return Response.json({error:'报告写入未授权'}, {status:401});
  }
  return null;
}
