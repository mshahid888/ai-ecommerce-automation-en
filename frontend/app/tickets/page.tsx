"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { getTickets, type Ticket } from "@/lib/api";

function statusBadgeClass(status: string): string {
  if (status === "open") return "bg-amber-100 text-amber-800";
  if (status === "closed") return "bg-green-100 text-green-800";
  return "bg-slate-100 text-slate-700";
}

function statusLabel(status: string): string {
  if (status === "open") return "Open";
  if (status === "closed") return "Closed";
  return status;
}

function priorityLabel(priority: string | null): string {
  if (priority === "high") return "High";
  if (priority === "normal") return "Normal";
  if (priority === "low") return "Low";
  return priority ?? "–";
}

function priorityBadgeClass(priority: string | null): string {
  if (priority === "high") return "bg-red-100 text-red-800";
  if (priority === "low") return "bg-slate-100 text-slate-600";
  return "bg-blue-100 text-blue-800";
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
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
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
          className={`rounded-md px-3 py-2 text-sm font-medium transition-colors ${
            onlyOpen
              ? "bg-blue-600 text-white"
              : "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
          }`}
        >
          {onlyOpen ? "Show all" : "Show open only"}
        </button>
      </div>

      {loading && (
        <p role="status" className="text-sm text-slate-600">
          Loading tickets…
        </p>
      )}

      {!loading && backendDown && (
        <div
          role="alert"
          className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800 shadow-sm"
        >
          The backend is unavailable – tickets cannot be displayed right
          now.
        </div>
      )}

      {!loading && !backendDown && tickets.length === 0 && (
        <div className="rounded-xl border border-slate-200 bg-white px-4 py-8 text-center shadow-sm">
          <p className="text-sm text-slate-600">
            {onlyOpen
              ? "No open tickets."
              : "No tickets yet. Start a conversation on the chat page – escalations and returns automatically create a ticket."}
          </p>
        </div>
      )}

      {!loading && !backendDown && tickets.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
          <table className="w-full min-w-[720px] text-left text-sm">
            <caption className="sr-only">
              List of support tickets – click a row for details
            </caption>
            <thead className="border-b border-slate-200 bg-slate-50">
              <tr>
                <th scope="col" className="px-4 py-3 font-semibold text-slate-700">
                  Ticket ID
                </th>
                <th scope="col" className="px-4 py-3 font-semibold text-slate-700">
                  Intent
                </th>
                <th scope="col" className="px-4 py-3 font-semibold text-slate-700">
                  Status
                </th>
                <th scope="col" className="px-4 py-3 font-semibold text-slate-700">
                  Priority
                </th>
                <th scope="col" className="px-4 py-3 font-semibold text-slate-700">
                  Order
                </th>
                <th scope="col" className="px-4 py-3 font-semibold text-slate-700">
                  Created
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-200">
              {tickets.map((ticket) => (
                <tr key={ticket.id} className="hover:bg-slate-50">
                  <td className="px-4 py-3">
                    <Link
                      href={`/tickets/${ticket.id}`}
                      className="font-mono font-medium text-blue-700 underline underline-offset-2"
                    >
                      #{ticket.id}
                    </Link>
                  </td>
                  <td className="max-w-[280px] truncate px-4 py-3 text-slate-700">
                    <Link
                      href={`/tickets/${ticket.id}`}
                      className="hover:text-blue-700 hover:underline"
                      title={ticket.summary ?? ticket.intent ?? ""}
                    >
                      {ticket.intent_label_de ?? ticket.intent ?? "–"}
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold ${statusBadgeClass(ticket.status)}`}
                    >
                      {statusLabel(ticket.status)}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold ${priorityBadgeClass(ticket.priority)}`}
                    >
                      {priorityLabel(ticket.priority)}
                    </span>
                  </td>
                  <td className="px-4 py-3 font-mono text-slate-700">
                    {ticket.order_ref ? `#${ticket.order_ref}` : "–"}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 tabular-nums text-slate-600">
                    {formatDate(ticket.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
