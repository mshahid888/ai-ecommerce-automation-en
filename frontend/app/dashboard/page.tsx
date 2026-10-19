"use client";

import Link from "next/link";
import { useEffect, useState, type ComponentType } from "react";
import { ChatIcon, TicketIcon } from "@/components/Sidebar";
import {
  getDashboardSummary,
  type DashboardSummary,
} from "@/lib/api";

function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("en-GB");
}

function MessagesIcon({ className = "" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="M8 10h.01M12 10h.01M16 10h.01M9 16H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-5l-5 5v-5Z" />
    </svg>
  );
}

function EscalationIcon({ className = "" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5m-13.5-9L12 3m0 0 4.5 4.5M12 3v13.5" />
    </svg>
  );
}

const CARD_DEFS: {
  key: "conversations_count" | "messages_count" | "tickets_open" | "escalations_count";
  label: string;
  description: string;
  Icon: ComponentType<{ className?: string }>;
  tint: string;
}[] = [
  {
    key: "conversations_count",
    label: "Conversations",
    description: "Chat conversations in the demo database",
    Icon: ChatIcon,
    tint: "bg-blue-100 text-blue-700",
  },
  {
    key: "messages_count",
    label: "Messages",
    description: "Customer and assistant messages",
    Icon: MessagesIcon,
    tint: "bg-blue-100 text-blue-700",
  },
  {
    key: "tickets_open",
    label: "Open tickets",
    description: "Support cases requiring an agent",
    Icon: TicketIcon,
    tint: "bg-amber-100 text-amber-700",
  },
  {
    key: "escalations_count",
    label: "Escalations",
    description: "Explicit handoffs to agents",
    Icon: EscalationIcon,
    tint: "bg-slate-100 text-slate-600",
  },
];

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
  const totalIntents =
    summary?.intents_breakdown.reduce((sum, row) => sum + row.count, 0) ?? 0;

  return (
    <div className="space-y-8">
      <div>
        <p className="text-xs font-semibold uppercase tracking-widest text-blue-600">
          Overview
        </p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">
          Dashboard
        </h1>
        <p className="mt-1 text-sm text-slate-600">
          Operational overview for the demo shop – all figures come from the
          database and are marked as{" "}
          <span className="font-medium">Demo data</span>.
        </p>
      </div>

      {loading && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4" aria-hidden="true">
          {CARD_DEFS.map((card) => (
            <div
              key={card.key}
              className="animate-pulse rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
            >
              <div className="h-10 w-10 rounded-xl bg-slate-100" />
              <div className="mt-4 h-8 w-20 rounded bg-slate-100" />
              <div className="mt-2 h-4 w-28 rounded bg-slate-100" />
            </div>
          ))}
        </div>
      )}

      {!loading && backendDown && (
        <div
          role="alert"
          className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 shadow-sm"
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
                  className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-md"
                >
                  <div className="flex items-center justify-between">
                    <span
                      className={`flex h-10 w-10 items-center justify-center rounded-xl ${card.tint}`}
                    >
                      <card.Icon className="h-5 w-5" />
                    </span>
                    <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                      Demo data
                    </span>
                  </div>
                  <p className="mt-4 text-3xl font-semibold tabular-nums tracking-tight text-slate-900">
                    {summary[card.key]}
                  </p>
                  <p className="mt-1 text-sm font-medium text-slate-700">
                    {card.label}
                  </p>
                  <p className="mt-0.5 text-xs text-slate-500">
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

          <section
            aria-label="Intent distribution"
            className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-lg font-semibold tracking-tight text-slate-900">
                Intent distribution
              </h2>
              {totalIntents > 0 && (
                <span className="text-xs font-medium tabular-nums text-slate-500">
                  {totalIntents} detected
                </span>
              )}
            </div>
            <p className="mt-1 text-sm text-slate-600">
              Detected intents from real chat conversations.{" "}
              <span className="font-medium">Demo data</span> – not a business
              evaluation.
            </p>
            {summary.intents_breakdown.length === 0 ? (
              <div className="mt-4 rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center">
                <p className="text-sm text-slate-600">
                  No conversations yet. Start a conversation on the{" "}
                  <Link href="/" className="font-medium text-blue-700 underline">
                    chat page
                  </Link>
                  , to see the distribution.
                </p>
              </div>
            ) : (
              <div className="mt-5 space-y-4">
                {summary.intents_breakdown.map((row) => {
                  const pct =
                    totalIntents > 0
                      ? Math.round((row.count / totalIntents) * 100)
                      : 0;
                  return (
                    <div key={row.intent}>
                      <div className="flex items-baseline justify-between text-sm">
                        <span className="font-medium text-slate-800">
                          {row.label_de}
                        </span>
                        <span className="tabular-nums text-slate-600">
                          {row.count}
                          <span className="ml-1.5 text-xs text-slate-400">
                            {pct}%
                          </span>
                        </span>
                      </div>
                      <div
                        role="img"
                        aria-label={`${row.label_de}: ${row.count}`}
                        className="mt-1.5 h-2.5 overflow-hidden rounded-full bg-slate-100"
                      >
                        <div
                          className="h-full rounded-full bg-gradient-to-r from-blue-500 to-blue-600 transition-all"
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
                  );
                })}
              </div>
            )}
          </section>

          <section aria-label="Recent activity">
            <h2 className="text-lg font-semibold tracking-tight text-slate-900">
              Recent activity
            </h2>
            <p className="mt-1 text-sm text-slate-600">
              Workflow steps actually executed – each entry corresponds to an
              operation in the backend.
            </p>
            {summary.recent_events.length === 0 ? (
              <div className="mt-4 rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center">
                <p className="text-sm text-slate-600">
                  No activity logged yet.
                </p>
              </div>
            ) : (
              <ol className="mt-4 space-y-2">
                {summary.recent_events.map((event) => (
                  <li
                    key={event.id}
                    className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm shadow-sm transition-colors hover:border-blue-200"
                  >
                    <span
                      className={`h-2 w-2 shrink-0 rounded-full ${
                        event.status === "failed" ? "bg-red-500" : "bg-green-500"
                      }`}
                      aria-hidden="true"
                    />
                    <span className="min-w-0 flex-1 font-medium text-slate-800">
                      {event.label_de}
                      {event.status === "failed" && (
                        <span className="ml-2 rounded-full bg-red-100 px-2 py-0.5 text-xs font-semibold text-red-800">
                          Failed
                        </span>
                      )}
                    </span>
                    <span className="shrink-0 text-xs tabular-nums text-slate-500">
                      {formatDate(event.created_at)}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section
            aria-label="Security and privacy (demo)"
            className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6"
          >
            <h2 className="text-lg font-semibold tracking-tight text-slate-900">
              Security &amp; privacy (Demo)
            </h2>
            <ul className="mt-3 list-none space-y-2.5 pl-0 text-sm text-slate-700">
              <li className="flex items-start gap-2.5"><span aria-hidden="true" className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-green-100 text-xs font-bold text-green-700">✓</span><span>
                <span className="font-medium">Simulated data:</span>{" "}
                All orders, products, and customers in this demo are
                fictional. No real orders are processed, and no real
                returns or refunds are executed.
              </span></li>
              <li className="flex items-start gap-2.5"><span aria-hidden="true" className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-green-100 text-xs font-bold text-green-700">✓</span><span>
                <span className="font-medium">No real payments:</span>{" "}
                The demo does not request payment data and does not store
                personal data of real customers.
              </span></li>
              <li className="flex items-start gap-2.5"><span aria-hidden="true" className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-green-100 text-xs font-bold text-green-700">✓</span><span>
                <span className="font-medium">Usage limits:</span>{" "}
                Requests are limited per visitor (chat, read requests, and
                demo reset). If the limit is exceeded, the backend responds
                with HTTP&nbsp;429 and a wait time.
              </span></li>
              <li className="flex items-start gap-2.5"><span aria-hidden="true" className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-green-100 text-xs font-bold text-green-700">✓</span><span>
                <span className="font-medium">AI quota:</span> The number
                of real AI requests per day is capped. When the quota is
                exhausted, the demo rule engine answers — this is clearly
                marked in the conversation.
              </span></li>
              <li className="flex items-start gap-2.5"><span aria-hidden="true" className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-green-100 text-xs font-bold text-green-700">✓</span><span>
                <span className="font-medium">Admin protection:</span>{" "}
                Resetting the demo requires a server-side admin key.
                Security-relevant events (e.g. failed admin attempts) are
                recorded in an immutable audit log.
              </span></li>
            </ul>
          </section>
        </>
      )}
    </div>
  );
}
