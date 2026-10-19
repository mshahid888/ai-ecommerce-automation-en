"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { getTickets, type Ticket } from "@/lib/api";

function StatusPill({ status }: { status: string }) {
  const dot =
    status === "closed"
      ? "bg-green-500"
      : status === "open"
        ? "bg-amber-500"
        : "bg-slate-400";
  const ring =
    status === "closed"
      ? "bg-green-50 text-green-800 ring-green-200"
      : status === "open"
        ? "bg-amber-50 text-amber-800 ring-amber-200"
        : "bg-slate-100 text-slate-600 ring-slate-200";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset ${ring}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} aria-hidden="true" />
      {statusLabel(status)}
    </span>
  );
}

function statusLabel(status: string): string {
  if (status === "open") return "Open";
  if (status === "closed") return "Closed";
  return status;
}

function PriorityPill({ priority }: { priority: string | null }) {
  const ring =
    priority === "high"
      ? "bg-red-50 text-red-800 ring-red-200"
      : priority === "low"
        ? "bg-slate-100 text-slate-600 ring-slate-200"
        : "bg-blue-50 text-blue-800 ring-blue-200";
  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset ${ring}`}
    >
      {priorityLabel(priority)}
    </span>
  );
}

function priorityLabel(priority: string | null): string {
  if (priority === "high") return "High";
  if (priority === "normal") return "Normal";
  if (priority === "low") return "Low";
  return priority ?? "–";
}

function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("en-GB");
}

export default function TicketsPage() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(true);
  const [backendDown, setBackendDown] = useState(false);
  const [onlyOpen, setOnlyOpen] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    let cancelled = false;
    getTickets(50, onlyOpen ? "open" : undefined)
      .then((data) => {
        if (!cancelled) {
          setTickets(data);
          setBackendDown(false);
        }
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
  }, [onlyOpen]);

  useEffect(load, [load]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-widest text-blue-600">
            Support
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">
            Tickets
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            Escalated requests from the chat –{" "}
            <span className="font-medium">demo data</span>.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setOnlyOpen((v) => !v)}
          aria-pressed={onlyOpen}
          className={`rounded-xl px-4 py-2 text-sm font-medium shadow-sm transition-all ${
            onlyOpen
              ? "bg-blue-600 text-white hover:bg-blue-700"
              : "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
          }`}
        >
          {onlyOpen ? "Show all" : "Show open only"}
        </button>
      </div>

      {loading && (
        <div
          className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
          aria-hidden="true"
        >
          <div className="animate-pulse divide-y divide-slate-100">
            {[0, 1, 2].map((i) => (
              <div key={i} className="flex items-center gap-4 px-4 py-4">
                <div className="h-4 w-16 rounded bg-slate-100" />
                <div className="h-4 w-48 rounded bg-slate-100" />
                <div className="h-6 w-20 rounded-full bg-slate-100" />
              </div>
            ))}
          </div>
        </div>
      )}

      {!loading && backendDown && (
        <div
          role="alert"
          className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 shadow-sm"
        >
          The backend is unavailable – tickets cannot be displayed right
          now.
        </div>
      )}

      {!loading && !backendDown && tickets.length === 0 && (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-10 text-center">
          <p className="text-sm font-medium text-slate-700">
            {onlyOpen ? "No open tickets." : "No tickets yet."}
          </p>
          {!onlyOpen && (
            <p className="mt-1 text-sm text-slate-500">
              Start a conversation on the chat page – escalations and returns
              automatically create a ticket.
            </p>
          )}
        </div>
      )}

      {!loading && !backendDown && tickets.length > 0 && (
        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-200 bg-slate-50/60 px-4 py-3">
            <p className="text-sm font-semibold text-slate-800">
              Support tickets
            </p>
            <span className="text-xs font-medium tabular-nums text-slate-500">
              {tickets.length} total
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <caption className="sr-only">
                List of support tickets – click a row for details
              </caption>
              <thead className="border-b border-slate-200">
                <tr>
                  <th
                    scope="col"
                    className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-slate-500"
                  >
                    Ticket
                  </th>
                  <th
                    scope="col"
                    className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-slate-500"
                  >
                    Intent
                  </th>
                  <th
                    scope="col"
                    className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-slate-500"
                  >
                    Status
                  </th>
                  <th
                    scope="col"
                    className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-slate-500"
                  >
                    Priority
                  </th>
                  <th
                    scope="col"
                    className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-slate-500"
                  >
                    Order
                  </th>
                  <th
                    scope="col"
                    className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-slate-500"
                  >
                    Created
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {tickets.map((ticket) => (
                  <tr
                    key={ticket.id}
                    className="transition-colors hover:bg-blue-50/40"
                  >
                    <td className="px-4 py-3.5">
                      <Link
                        href={`/tickets/${ticket.id}`}
                        className="font-mono font-semibold text-blue-700 hover:underline hover:underline-offset-2"
                      >
                        #{ticket.id}
                      </Link>
                    </td>
                    <td className="max-w-[280px] truncate px-4 py-3.5 text-slate-700">
                      <Link
                        href={`/tickets/${ticket.id}`}
                        className="transition-colors hover:text-blue-700"
                        title={ticket.summary ?? ticket.intent ?? ""}
                      >
                        {ticket.intent_label_de ?? ticket.intent ?? "–"}
                      </Link>
                    </td>
                    <td className="px-4 py-3.5">
                      <StatusPill status={ticket.status} />
                    </td>
                    <td className="px-4 py-3.5">
                      <PriorityPill priority={ticket.priority} />
                    </td>
                    <td className="px-4 py-3.5 font-mono text-slate-700">
                      {ticket.order_ref ? `#${ticket.order_ref}` : "–"}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3.5 tabular-nums text-slate-500">
                      {formatDate(ticket.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
