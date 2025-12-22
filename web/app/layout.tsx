import './globals.css';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Cleaning Economy Chat Tester',
  description: 'Minimal chat UI for the Cleaning Economy Bot.'
};

export default function RootLayout({
  children
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <div className="page">
          <header className="header">
            <div>
              <p className="eyebrow">Economy MVP</p>
              <h1>Cleaning Economy Chat Tester</h1>
              <p className="subtitle">
                Use this widget to test /v1/chat/turn responses and estimate rendering.
              </p>
            </div>
          </header>
          <main className="main">{children}</main>
        </div>
      </body>
    </html>
  );
}
