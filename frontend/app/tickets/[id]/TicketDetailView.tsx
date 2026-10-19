"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  ApiError,
  getTicket,
  type TicketDetail,
} from "@/lib/api";

function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("en-GB");
}

function statusLabel(status: string): string {
  if (status === "open") return "Open";
  if (status === "closed") return "Closed";
  return status;
}

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
      className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ring-1 ring-inset ${ring}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${dot}`} aria-hidden="true" />
      {statusLabel(status)}
    </span>
  );
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
      className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold ring-1 ring-inset ${ring}`}
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

function InfoRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1 py-3 sm:flex-row sm:gap-6">
      <dt className="w-44 shrink-0 text-sm font-medium text-slate-500">
        {label}
      </dt>
      <dd className="text-sm text-slate-900">{children}</dd>
    </div>
  );
}

export default function TicketDetailView({ id }: { id: string }) {
  const [ticket, setTicket] = useState<TicketDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [backendDown, setBackendDown] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const numericId = Number(id);
    if (!Number.isInteger(numericId) || numericId <= 0) {
      setNotFound(true);
      setLoading(false);
      return;
    }
    getTicket(numericId)
      .then((data) => {
        if (!cancelled) setTicket(data);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) setNotFound(true);
        else setBackendDown(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  return (
    <div className="space-y-6">
      <div>
        <Link
          href="/tickets"
          className="inline-flex items-center gap-1 text-sm font-medium text-slate-500 transition-colors hover:text-blue-700"
        >
          <span aria-hidden="true">←</span> Back to tickets
        </Link>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
            Ticket #{id}
          </h1>
          {!loading && !notFound && !backendDown && ticket && (
            <div className="flex items-center gap-2">
              <StatusPill status={ticket.status} />
              <PriorityPill priority={ticket.priority} />
            </div>
          )}
        </div>
        <p className="mt-1 text-sm text-slate-600">
          Support case with linked conversation and history –{" "}
          <span className="font-medium">Demo data</span>.
        </p>
      </div>

      {loading && (
        <div className="animate-pulse rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" aria-hidden="true">
          <div className="h-5 w-40 rounded bg-slate-100" />
          <div className="mt-3 h-4 w-full rounded bg-slate-100" />
          <div className="mt-2 h-4 w-3/4 rounded bg-slate-100" />
        </div>
      )}

      {!loading && notFound && (
        <div
          role="alert"
          className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-10 text-center"
        >
          <p className="text-sm font-medium text-slate-700">
            Ticket #{id} was not found.
          </p>
        </div>
      )}

      {!loading && backendDown && (
        <div
          role="alert"
          className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 shadow-sm"
        >
          The backend is not reachable – the ticket cannot be displayed right
          now.
        </div>
      )}

      {!loading && !notFound && !backendDown && ticket && (
        <>
          <section
            aria-label="Ticket summary"
            className="rounded-2xl border border-slate-200 bg-white shadow-sm"
          >
            <div className="divide-y divide-slate-100 px-5">
              <InfoRow label="Issue">
                <span className="font-medium">{ticket.intent_label_de}</span>
                {ticket.intent && (
                  <span className="ml-2 font-mono text-xs text-slate-400">
                    ({ticket.intent})
                  </span>
                )}
              </InfoRow>
              <InfoRow label="Summary">
                {ticket.summary ?? "–"}
              </InfoRow>
              <InfoRow label="Order">
                <span className="rounded-md bg-slate-100 px-2 py-0.5 font-mono text-[13px]">
                  {ticket.order_ref ? `#${ticket.order_ref}` : "–"}
                </span>
              </InfoRow>
              <InfoRow label="Escalation reason">
                {ticket.escalation_reason ?? "–"}
              </InfoRow>
              <InfoRow label="Recommended next action">
                {ticket.recommended_action ?? "–"}
              </InfoRow>
              <InfoRow label="Created">
                <span className="tabular-nums">
                  {formatDate(ticket.created_at)}
                </span>
              </InfoRow>
            </div>
          </section>

          <section aria-label="Linked conversation">
            <h2 className="text-lg font-semibold tracking-tight text-slate-900">
              Linked conversation
            </h2>
            {ticket.messages.length === 0 ? (
              <p className="mt-2 text-sm text-slate-500">
                No messages yet.
              </p>
            ) : (
              <ol className="mt-3 space-y-3">
                {ticket.messages.map((message) => (
                  <li
                    key={message.id}
                    className={`max-w-2xl rounded-2xl border px-4 py-3 shadow-sm ${
                      message.role === "user"
                        ? "border-blue-200 bg-blue-50/60"
                        : "border-slate-200 bg-white"
                    }`}
                  >
                    <p
                      className={`text-xs font-semibold uppercase tracking-wide ${
                        message.role === "user"
                          ? "text-blue-700"
                          : "text-slate-500"
                      }`}
                    >
                      {message.role === "user" ? "Customer" : "Assistant"}
                    </p>
                    <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-slate-800">
                      {message.content}
                    </p>
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section aria-label="History">
            <h2 className="text-lg font-semibold tracking-tight text-slate-900">
              Event history
            </h2>
            <p className="mt-1 text-sm text-slate-600">
              All workflow steps actually executed in this conversation.
            </p>
            {ticket.events.length === 0 ? (
              <p className="mt-2 text-sm text-slate-500">
                No events yet.
              </p>
            ) : (
              <ol className="mt-4 space-y-0">
                {ticket.events.map((event, index) => (
                  <li key={event.id} className="relative flex gap-4 pb-6 last:pb-0">
                    <div className="flex flex-col items-center">
                      <span
                        aria-hidden="true"
                        className={`mt-1 h-3 w-3 rounded-full ring-4 ${
                          event.status === "failed"
                            ? "bg-red-500 ring-red-100"
                            : "bg-green-500 ring-green-100"
                        }`}
                      />
                      {index < ticket.events.length - 1 && (
                        <span
                          aria-hidden="true"
                          className="mt-1 w-px flex-1 bg-slate-200"
                        />
                      )}
                    </div>
                    <div className="pb-1">
                      <p className="text-sm font-medium text-slate-800">
                        {event.label_de}
                        {event.status === "failed" && (
                          <span className="ml-2 rounded-full bg-red-100 px-2 py-0.5 text-xs font-semibold text-red-800">
                            Failed
                          </span>
                        )}
                      </p>
                      <p className="text-xs tabular-nums text-slate-500">
                        {formatDate(event.created_at)}
                      </p>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </>
      )}
    </div>
  );
}
