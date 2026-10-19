import type { Metadata } from "next";
import Nav from "@/components/Nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI E-Commerce Automation – Demo Shop GmbH",
  description:
    "Demo of the AI customer service for Demo Shop GmbH: order status, product questions, and returns triage.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="h-full">
      <body className="flex min-h-full flex-col bg-white font-sans text-slate-900 antialiased">
        <header className="bg-blue-700">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-4 sm:px-6">
            <div>
              <p className="text-lg font-semibold tracking-tight text-white">
                AI E-Commerce Automation
              </p>
              <p className="text-sm text-blue-100">
                Demo shop: Demo Shop GmbH
              </p>
            </div>
            <Nav />
          </div>
        </header>

        <div
          role="note"
          aria-label="Demo notice"
          className="border-b border-amber-300 bg-amber-100"
        >
          <p className="mx-auto max-w-6xl px-4 py-2.5 text-sm font-medium text-amber-900 sm:px-6">
            Demo environment – simulated data. No real orders.
          </p>
        </div>

        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6">
          {children}
        </main>

        <footer className="border-t border-slate-200 bg-white">
          <p className="mx-auto max-w-6xl px-4 py-4 text-xs text-slate-500 sm:px-6">
            Phase 6 – demo with real workflows: order status, product catalog,
            and returns triage. All data is simulated; no real orders are
            processed.
          </p>
        </footer>
      </body>
    </html>
  );
}
