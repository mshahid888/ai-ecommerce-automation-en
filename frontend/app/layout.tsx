import type { Metadata } from "next";
import Nav from "@/components/Nav";
import Sidebar from "@/components/Sidebar";
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
      <body className="min-h-screen bg-white font-sans text-slate-900 antialiased">
        <Sidebar />

        <div className="lg:pl-64">
          {/* Mobile top bar */}
          <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/95 backdrop-blur lg:hidden">
            <div className="flex items-center justify-between gap-3 px-4 py-3">
              <div className="flex items-center gap-2.5">
                <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-600 text-xs font-bold text-white">
                  AI
                </span>
                <span className="text-sm font-semibold tracking-tight text-slate-900">
                  AI E-Commerce
                </span>
              </div>
              <Nav />
            </div>
          </header>

          {/* Desktop top bar */}
          <div className="hidden border-b border-slate-200 bg-white lg:block">
            <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-3">
              <p className="text-sm text-slate-500">
                Demo shop:{" "}
                <span className="font-medium text-slate-700">
                  Demo Shop GmbH
                </span>
              </p>
              <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-800">
                <span
                  className="h-1.5 w-1.5 rounded-full bg-amber-500"
                  aria-hidden="true"
                />
                Demo environment
              </span>
            </div>
          </div>

          <main className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6">
            {children}
          </main>

          <footer className="border-t border-slate-200">
            <p className="mx-auto max-w-6xl px-4 py-4 text-xs text-slate-500 sm:px-6">
              Phase 6 – demo with real workflows: order status, product catalog,
              and returns triage. All data is simulated; no real orders are
              processed.
            </p>
          </footer>
        </div>
      </body>
    </html>
  );
}
