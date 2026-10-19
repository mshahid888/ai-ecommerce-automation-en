"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  getDashboardSummary,
  type DashboardSummary,
} from "@/lib/api";

function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("en-GB");
}

const CARD_DEFS = [
  {
    key: "conversations_count" as const,
    label: "Conversations",
    description: "Chat conversations in the demo database",
  },
  {
    key: "messages_count" as const,
    label: "Messages",
    description: "Customer and assistant messages",
  },
  {
    key: "tickets_open" as const,
    label: "Open tickets",
    description: "Support cases requiring an agent",
  },
  {
    key: "escalations_count" as const,
    label: "Escalations",
    description: "Explicit handoffs to agents",
  },
] as const;

export default function DashboardPage() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [backendDown, setBackendDown] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getDashboardSummary()
      .then((data) => {
        if (!cancelled) setSummary(data);
      })
      .catch(() => {
        if (!cancelled) setBackendDown(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const maxIntentCount = Math.max(
    0,
    ...(summary?.intents_breakdown.map((b) => b.count) ?? []),
  );

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
          Dashboard
        </h1>
        <p className="mt-1 text-sm text-slate-600">
          Operational overview for the demo shop – all figures come from the
          database and are marked as{" "}
          <span className="font-medium">Demo data</span>.
        </p>
      </div>

      {loading && (
        <p role="status" className="text-sm text-slate-600">
          Loading dashboard…
        </p>
      )}

      {!loading && backendDown && (
        <div
          role="alert"
          className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800 shadow-sm"
        >
          The backend is unavailable – metrics cannot be displayed right
          now.
        </div>
      )}

      {!loading && !backendDown && summary && (
        <>
          <section aria-label="Metrics">
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {CARD_DEFS.map((card) => (
                <div
                  key={card.key}
                  className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm"
                >
                  <p className="text-sm font-medium text-slate-600">
                    {card.label}
                  </p>
                  <p className="mt-2 text-3xl font-semibold tabular-nums text-slate-900">
                    {summary[card.key]}
                  </p>
                  <span className="mt-3 inline-block rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-semibold text-slate-600">
                    Demo data
                  </span>
                  <p className="mt-2 text-xs text-slate-500">
                    {card.description}
                  </p>
                </div>
              ))}
            </div>
            <p className="mt-3 text-xs text-slate-500">
              Total tickets:{" "}
              <span className="font-medium tabular-nums text-slate-700">
                {summary.tickets_total}
              </span>{" "}
              (demo data) ·{" "}
              <Link
                href="/tickets"
                className="font-medium text-blue-700 underline underline-offset-2"
              >
                View all tickets
              </Link>
            </p>
          </section>

          <section aria-label="Intent distribution">
            <h2 className="text-lg font-semibold text-slate-900">
              Intent distribution
            </h2>
            <p className="mt-1 text-sm text-slate-600">
              Detected intents from real chat conversations.{" "}
              <span className="font-medium">Demo data</span> – not a business
              evaluation.
            </p>
            {summary.intents_breakdown.length === 0 ? (
              <div className="mt-4 rounded-xl border border-slate-200 bg-white px-4 py-8 text-center shadow-sm">
                <p className="text-sm text-slate-600">
                  No conversations yet. Start a conversation on the{" "}
                  <Link href="/" className="font-medium text-blue-700 underline">
                    chat page
                  </Link>
                  , to see the distribution.
                </p>
              </div>
            ) : (
              <div className="mt-4 space-y-3">
                {summary.intents_breakdown.map((row) => (
                  <div key={row.intent}>
                    <div className="flex items-baseline justify-between text-sm">
                      <span className="font-medium text-slate-800">
                        {row.label_de}
                      </span>
                      <span className="tabular-nums text-slate-600">
                        {row.count}
                      </span>
                    </div>
                    <div
                      role="img"
                      aria-label={`${row.label_de}: ${row.count}`}
                      className="mt-1 h-2.5 overflow-hidden rounded-full bg-slate-100"
                    >
                      <div
                        className="h-full rounded-full bg-blue-600"
                        style={{
                          width: `${
                            maxIntentCount > 0
                              ? Math.max(
                                  4,
                                  Math.round(
                                    (row.count / maxIntentCount) * 100,
                                  ),
                                )
                              : 0
                          }%`,
                        }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section aria-label="Recent activity">
            <h2 className="text-lg font-semibold text-slate-900">
              Recent activity
            </h2>
            <p className="mt-1 text-sm text-slate-600">
              Workflow steps actually executed – each entry corresponds to an
              operation in the backend.
            </p>
            {summary.recent_events.length === 0 ? (
              <div className="mt-4 rounded-xl border border-slate-200 bg-white px-4 py-8 text-center shadow-sm">
                <p className="text-sm text-slate-600">
                  No activity logged yet.
                </p>
              </div>
            ) : (
              <ol className="mt-4 space-y-2">
                {summary.recent_events.map((event) => (
                  <li
                    key={event.id}
                    className="flex flex-wrap items-baseline justify-between gap-2 rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm shadow-sm"
                  >
                    <span className="font-medium text-slate-800">
                      {event.label_de}
                      {event.status === "failed" && (
                        <span className="ml-2 rounded-full bg-red-100 px-2 py-0.5 text-xs font-semibold text-red-800">
                          Failed
                        </span>
                      )}
                    </span>
                    <span className="text-xs tabular-nums text-slate-500">
                      {formatDate(event.created_at)}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </section>
          <section aria-label="Security and privacy (demo)">
            <h2 className="text-lg font-semibold text-slate-900">
              Security &amp; privacy (Demo)
            </h2>
            <div className="mt-3 rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-700 shadow-sm">
              <ul className="list-none space-y-2 pl-0">
                <li className="flex items-start gap-2"><span aria-hidden="true" className="font-bold text-green-600">✓</span><span>
                  <span className="font-medium">Simulated data:</span>{" "}
                  All orders, products, and customers in this demo are
                  fictional. No real orders are processed, and no real
                  returns or refunds are executed.
                </span></li>
                <li className="flex items-start gap-2"><span aria-hidden="true" className="font-bold text-green-600">✓</span><span>
                  <span className="font-medium">No real payments:</span>{" "}
                  The demo does not request payment data and does not store
                  personal data of real customers.
                </span></li>
                <li className="flex items-start gap-2"><span aria-hidden="true" className="font-bold text-green-600">✓</span><span>
                  <span className="font-medium">Usage limits:</span>{" "}
                  Requests are limited per visitor (chat, read requests, and
                  demo reset). If the limit is exceeded, the backend responds
                  with HTTP&nbsp;429 and a wait time.
                </span></li>
                <li className="flex items-start gap-2"><span aria-hidden="true" className="font-bold text-green-600">✓</span><span>
                  <span className="font-medium">AI quota:</span> The number
                  of real AI requests per day is capped. When the quota is
                  exhausted, the demo rule engine answers — this is clearly
                  marked in the conversation.
                </span></li>
                <li className="flex items-start gap-2"><span aria-hidden="true" className="font-bold text-green-600">✓</span><span>
                  <span className="font-medium">Admin protection:</span>{" "}
                  Resetting the demo requires a server-side admin key.
                  Security-relevant events (e.g. failed admin attempts) are
                  recorded in an immutable audit log.
                </span></li>
              </ul>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
