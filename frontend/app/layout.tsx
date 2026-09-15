import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = {
  title: 'Kharcha · Your spending, understood',
  icons: { icon: '/kharcha-icon.png', apple: '/kharcha-icon.png' },
  description:
    'A private, local view of your monthly spending, backed by email evidence.',
};
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <head>
        <link
          rel="preload"
          href="/fonts/manrope-variable.ttf"
          as="font"
          type="font/ttf"
          crossOrigin="anonymous"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
