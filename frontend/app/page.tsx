"use client";

import { useEffect, useRef, useState } from "react";
import Timeline from "@/components/Timeline";
import {
  ApiError,
  apiBaseUrl,
  postChat,
  resetDemo,
  type TimelineStep,
} from "@/lib/api";

interface Message {
  id: number;
  role: "user" | "assistant";
  text: string;
  /** True when the text is the backend's honest error reply (not a normal answer). */
  isErrorReply?: boolean;
}

const EXAMPLE_QUESTIONS = [
  "Hello, where is my order #10482?",
  "Is this water bottle dishwasher-safe?",
  "I would like to return my order.",
  "My package never arrived, and I want my money back immediately.",
] as const;

let nextId = 1;
function makeId(): number {
  nextId += 1;
  return nextId;
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: 0,
      role: "assistant",
      text: "How can I help you?",
    },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [backendDown, setBackendDown] = useState(false);
  const [timeline, setTimeline] = useState<TimelineStep[]>([]);
  const [ticketNotice, setTicketNotice] = useState<string | null>(null);
  const [resetNotice, setResetNotice] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);
  // Phase 7: admin key for the demo reset — kept in memory only, never
  // persisted in the browser.
  const [adminKey, setAdminKey] = useState("");
  const [quotaNote, setQuotaNote] = useState<string | null>(null);
  const scrollAnchorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollAnchorRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function sendMessage(raw: string) {
    const text = raw.trim();
    if (!text || loading) return;

    setBackendDown(false);
    setMessages((prev) => [...prev, { id: makeId(), role: "user", text }]);
    setInput("");
    setLoading(true);

    try {
      const result = await postChat(text);
      setTimeline(result.events ?? result.timeline ?? []);
      const ticketId = result.ticket?.id ?? result.ticket_id;
      setTicketNotice(
        ticketId != null
          ? `Support ticket #${ticketId} created – an agent will review the case.`
          : null,
      );
      // Phase 7: honest note when the AI quota was exhausted.
      setQuotaNote(
        result.quota_fallback && result.quota_note ? result.quota_note : null,
      );
      setMessages((prev) => [
        ...prev,
        {
          id: makeId(),
          role: "assistant",
          text: result.answer || result.reply_de || "(No reply received from the backend.)",
        },
      ]);
    } catch (error) {
      if (error instanceof ApiError && error.replyDe) {
        // The backend answered with an error but provided its honest
        // German fallback reply — render that instead of inventing one.
        setMessages((prev) => [
          ...prev,
          {
            id: makeId(),
            role: "assistant",
            text: error.replyDe as string,
            isErrorReply: true,
          },
        ]);
      } else {
        // The backend could not be reached (or returned no usable reply):
        // never fake a reply — show the honest error state.
        setBackendDown(true);
      }
    } finally {
      setLoading(false);
    }
  }

  function handleExampleClick(question: string) {
    setInput(question);
    void sendMessage(question);
  }

  async function handleResetDemo() {
    if (resetting) return;
    setResetting(true);
    setResetNotice(null);
    try {
      // Phase 7: the reset requires the server-side admin key.
      const result = await resetDemo(adminKey.trim() || undefined);
      setTimeline([]);
      setTicketNotice(null);
      setQuotaNote(null);
      setBackendDown(false);
      setMessages([
        {
          id: makeId(),
          role: "assistant",
          text: "How can I help you?",
        },
      ]);
      setResetNotice(`Demo was reset (${result.demo_store}).`);
    } catch (error) {
      if (error instanceof ApiError) {
        setResetNotice(`Reset failed: ${error.message}.`);
      } else {
        setResetNotice(`Backend unavailable (${apiBaseUrl()}).`);
      }
    } finally {
      setResetting(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
            Customer chat
          </h1>
          <p className="mt-1 text-sm text-slate-600">
            Ask a question about your order, a product, or a return. The
            backend checks real demo data: order status, product catalog,
            and return policy.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="admin-key" className="sr-only">
            Admin key
          </label>
          <input
            id="admin-key"
            type="password"
            value={adminKey}
            onChange={(event) => setAdminKey(event.target.value)}
            placeholder="Admin key"
            autoComplete="off"
            className="w-40 rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200"
          />
          <button
            type="button"
            onClick={handleResetDemo}
            disabled={resetting}
            className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 shadow-sm transition-colors hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {resetting ? "Resetting…" : "Reset demo"}
          </button>
        </div>
      </div>

      {ticketNotice && (
        <p
          role="status"
          className="rounded-lg border border-blue-300 bg-blue-50 px-4 py-2.5 text-sm font-medium text-blue-900"
        >
          {ticketNotice}
        </p>
      )}

      {resetNotice && (
        <p role="status" className="text-sm text-slate-600">
          {resetNotice}
        </p>
      )}

      {quotaNote && (
        <p
          role="status"
          className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-2.5 text-sm text-amber-900"
        >
          {quotaNote}
        </p>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Chat panel */}
        <section
          aria-label="Support chat"
          className="flex flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm lg:col-span-2"
        >
          <div
            className="max-h-[480px] min-h-[320px] flex-1 space-y-4 overflow-y-auto p-4 sm:p-6"
            aria-live="polite"
          >
            {messages.map((message) =>
              message.role === "user" ? (
                <div key={message.id} className="flex justify-end">
                  <div className="max-w-[80%] rounded-2xl rounded-br-md bg-blue-600 px-4 py-2.5 text-sm text-white">
                    {message.text}
                  </div>
                </div>
              ) : (
                <div key={message.id} className="flex justify-start">
                  <div
                    className={`max-w-[85%] rounded-2xl rounded-bl-md border px-4 py-2.5 text-sm ${
                      message.isErrorReply
                        ? "border-red-300 bg-red-50 text-red-800"
                        : "border-slate-200 bg-slate-50 text-slate-900"
                    }`}
                  >
                    {message.isErrorReply && (
                      <p className="mb-1 text-xs font-semibold">
                        Backend notice
                      </p>
                    )}
                    {message.text}
                  </div>
                </div>
              ),
            )}

            {loading && (
              <div className="flex justify-start">
                <div
                  role="status"
                  className="rounded-2xl rounded-bl-md border border-slate-200 bg-slate-50 px-4 py-2.5 text-sm text-slate-600"
                >
                  Processing your request…
                </div>
              </div>
            )}

            {backendDown && (
              <div
                role="alert"
                className="rounded-lg border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800"
              >
                <p className="font-semibold">Backend unavailable.</p>
                <p className="mt-1">
                  The backend is not responding at{" "}
                  <span className="font-mono">{apiBaseUrl()}</span>. Please
                  make sure the backend is running and try again.
                </p>
              </div>
            )}
            <div ref={scrollAnchorRef} />
          </div>

          <div className="border-t border-slate-200 bg-slate-50 p-4 sm:p-6">
            <div className="mb-4">
              <p
                id="example-questions-label"
                className="mb-2 text-xs font-semibold text-slate-600"
              >
                Sample questions
              </p>
              <div
                aria-labelledby="example-questions-label"
                className="flex flex-wrap gap-2"
              >
                {EXAMPLE_QUESTIONS.map((question) => (
                  <button
                    key={question}
                    type="button"
                    onClick={() => handleExampleClick(question)}
                    disabled={loading}
                    className="rounded-full border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 transition-colors hover:border-blue-400 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {question}
                  </button>
                ))}
              </div>
            </div>

            <form
              onSubmit={(event) => {
                event.preventDefault();
                void sendMessage(input);
              }}
              className="flex gap-2"
            >
              <div className="flex-1">
                <label htmlFor="chat-input" className="sr-only">
                  Your message
                </label>
                <input
                  id="chat-input"
                  type="text"
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  placeholder="Type your message…"
                  disabled={loading}
                  autoComplete="off"
                  className="w-full rounded-md border border-slate-300 bg-white px-3 py-2.5 text-sm text-slate-900 placeholder:text-slate-400 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200 disabled:opacity-60"
                />
              </div>
              <button
                type="submit"
                disabled={loading || input.trim().length === 0}
                className="shrink-0 rounded-md bg-blue-600 px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                Send
              </button>
            </form>
          </div>
        </section>

        {/* Timeline panel */}
        <aside
          aria-label="Activity history"
          className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-6"
        >
          <h2 className="mb-1 text-lg font-semibold tracking-tight text-slate-900">
            Activity history
          </h2>
          <p className="mb-4 text-sm text-slate-600">
            The individual steps of the last request, as reported by the
            backend.
          </p>
          <Timeline steps={timeline} />
        </aside>
      </div>
    </div>
  );
}
