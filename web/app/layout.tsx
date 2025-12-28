import './globals.css';
import '../styles/design-system.css';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Economy Cleaning — Edmonton',
  description: 'Honest, deterministic cleaning quotes in Edmonton.'
};

export default function RootLayout({
  children
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        {children}
      </body>
    </html>
  );
}
