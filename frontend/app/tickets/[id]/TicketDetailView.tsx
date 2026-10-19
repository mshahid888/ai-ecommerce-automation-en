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
      <dt className="w-44 shrink-0 text-sm font-medium text-slate-600">
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
          className="text-sm font-medium text-blue-700 underline underline-offset-2"
        >
          ← Back to tickets
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight text-slate-900">
          Ticket #{id}
        </h1>
        <p className="mt-1 text-sm text-slate-600">
          Support case with linked conversation and history –{" "}
          <span className="font-medium">Demo data</span>.
        </p>
      </div>

      {loading && (
        <p role="status" className="text-sm text-slate-600">
          Loading ticket …
        </p>
      )}

      {!loading && notFound && (
        <div
          role="alert"
          className="rounded-xl border border-slate-200 bg-white px-4 py-8 text-center shadow-sm"
        >
          <p className="text-sm text-slate-600">
            Ticket #{id} was not found.
          </p>
        </div>
      )}

      {!loading && backendDown && (
        <div
          role="alert"
          className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800 shadow-sm"
        >
          The backend is not reachable – the ticket cannot be displayed right
          now.
        </div>
      )}

      {!loading && !notFound && !backendDown && ticket && (
        <>
          <section
            aria-label="Ticket summary"
            className="rounded-xl border border-slate-200 bg-white shadow-sm"
          >
            <div className="divide-y divide-slate-200 px-5">
              <InfoRow label="Issue">
                {ticket.intent_label_de}
                {ticket.intent && (
                  <span className="ml-2 font-mono text-xs text-slate-500">
                    ({ticket.intent})
                  </span>
                )}
              </InfoRow>
              <InfoRow label="Summary">
                {ticket.summary ?? "–"}
              </InfoRow>
              <InfoRow label="Status">
                <span
                  className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                    ticket.status === "open"
                      ? "bg-amber-100 text-amber-800"
                      : "bg-green-100 text-green-800"
                  }`}
                >
                  {statusLabel(ticket.status)}
                </span>
              </InfoRow>
              <InfoRow label="Priority">
                {priorityLabel(ticket.priority)}
              </InfoRow>
              <InfoRow label="Order">
                <span className="font-mono">
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
            <h2 className="text-lg font-semibold text-slate-900">
              Linked conversation
            </h2>
            {ticket.messages.length === 0 ? (
              <p className="mt-2 text-sm text-slate-600">
                No messages yet.
              </p>
            ) : (
              <ol className="mt-3 space-y-3">
                {ticket.messages.map((message) => (
                  <li
                    key={message.id}
                    className={`max-w-2xl rounded-xl border px-4 py-3 shadow-sm ${
                      message.role === "user"
                        ? "border-slate-200 bg-white"
                        : "border-slate-300 bg-slate-50"
                    }`}
                  >
                    <p className="text-xs font-semibold text-slate-500">
                      {message.role === "user" ? "Customer" : "Assistant"}
                    </p>
                    <p className="mt-1 whitespace-pre-wrap text-sm text-slate-800">
                      {message.content}
                    </p>
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section aria-label="History">
            <h2 className="text-lg font-semibold text-slate-900">
              Event history
            </h2>
            <p className="mt-1 text-sm text-slate-600">
              All workflow steps actually executed in this conversation.
            </p>
            {ticket.events.length === 0 ? (
              <p className="mt-2 text-sm text-slate-600">
                No events yet.
              </p>
            ) : (
              <ol className="mt-3 space-y-0">
                {ticket.events.map((event, index) => (
                  <li key={event.id} className="relative flex gap-4 pb-6">
                    <div className="flex flex-col items-center">
                      <span
                        aria-hidden="true"
                        className={`mt-1 h-3 w-3 rounded-full ${
                          event.status === "failed"
                            ? "bg-red-500"
                            : "bg-green-600"
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
