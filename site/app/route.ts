import { html } from './ui';
export const dynamic = 'force-dynamic';
export async function GET() {
  return new Response(html, {headers:{'content-type':'text/html; charset=utf-8','cache-control':'no-store','content-security-policy':"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self' https://chatgpt.com https://*.chatgpt.com; base-uri 'none'; object-src 'none'",'x-content-type-options':'nosniff'}});
}
