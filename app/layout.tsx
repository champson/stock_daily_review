import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "收盘之后 · 股票每日复盘",
  description: "通达信官方数据驱动的综合市场收盘复盘与历史存档。",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="antialiased">{children}</body>
    </html>
  );
}
