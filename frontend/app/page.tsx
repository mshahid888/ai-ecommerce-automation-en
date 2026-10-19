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

function SparklesIcon({ className = "" }: { className?: string }) {
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
      <path d="M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09Z" />
    </svg>
  );
}

function UserIcon({ className = "" }: { className?: string }) {
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
      <path d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z" />
    </svg>
  );
}

function PaperAirplaneIcon({ className = "" }: { className?: string }) {
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
      <path d="M6 12 3.269 3.125A59.769 59.769 0 0 1 21.485 12 59.768 59.768 0 0 1 3.27 20.875L5.999 12Zm0 0h7.5" />
    </svg>
  );
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
  const inputRef = useRef<HTMLInputElement>(null);
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
    const live = inputRef.current?.value ?? raw;
    const text = (live || raw).trim();
    if (!text || loading) return;

    setBackendDown(false);
    setMessages((prev) => [...prev, { id: makeId(), role: "user", text }]);
    setInput("");
    if (inputRef.current) inputRef.current.value = "";
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
    if (inputRef.current) inputRef.current.value = question;
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
          <p className="text-xs font-semibold uppercase tracking-widest text-blue-600">
            Support chat
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-900">
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
            className="w-40 rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm placeholder:text-slate-400 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200"
          />
          <button
            type="button"
            onClick={handleResetDemo}
            disabled={resetting}
            className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 shadow-sm transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {resetting ? "Resetting…" : "Reset demo"}
          </button>
        </div>
      </div>

      {ticketNotice && (
        <p
          role="status"
          className="flex items-center gap-2.5 rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm font-medium text-blue-900 shadow-sm"
        >
          <span
            className="h-2 w-2 shrink-0 rounded-full bg-blue-600"
            aria-hidden="true"
          />
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
          className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 shadow-sm"
        >
          {quotaNote}
        </p>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        {/* Chat panel */}
        <section
          aria-label="Support chat"
          className="flex flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm lg:col-span-2"
        >
          <div className="flex items-center gap-3 border-b border-slate-100 px-4 py-3 sm:px-6">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-blue-600 text-white shadow-sm">
              <SparklesIcon className="h-4 w-4" />
            </span>
            <div>
              <p className="text-sm font-semibold text-slate-900">
                Support Assistant
              </p>
              <p className="flex items-center gap-1.5 text-xs text-slate-500">
                <span
                  className="h-1.5 w-1.5 rounded-full bg-green-500"
                  aria-hidden="true"
                />
                Ready to help
              </p>
            </div>
            <span className="ml-auto rounded-full bg-amber-100 px-2.5 py-1 text-xs font-semibold text-amber-800">
              Demo
            </span>
          </div>

          <div
            className="max-h-[480px] min-h-[320px] flex-1 space-y-5 overflow-y-auto p-4 sm:p-6"
            aria-live="polite"
          >
            {messages.map((message) =>
              message.role === "user" ? (
                <div key={message.id} className="flex items-end justify-end gap-2.5">
                  <div className="max-w-[80%] rounded-2xl rounded-br-md bg-blue-600 px-4 py-2.5 text-sm leading-relaxed text-white shadow-sm">
                    {message.text}
                  </div>
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-200 text-slate-500">
                    <UserIcon className="h-4 w-4" />
                  </span>
                </div>
              ) : (
                <div key={message.id} className="flex items-end gap-2.5">
                  <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-100 text-blue-700">
                    <SparklesIcon className="h-4 w-4" />
                  </span>
                  <div
                    className={`max-w-[85%] rounded-2xl rounded-bl-md border px-4 py-2.5 text-sm leading-relaxed shadow-sm ${
                      message.isErrorReply
                        ? "border-red-200 bg-red-50 text-red-800"
                        : "border-slate-200 bg-white text-slate-800"
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
              <div className="flex items-end gap-2.5">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-100 text-blue-700">
                  <SparklesIcon className="h-4 w-4" />
                </span>
                <div
                  role="status"
                  aria-label="Assistant is typing"
                  className="flex items-center gap-1.5 rounded-2xl rounded-bl-md border border-slate-200 bg-white px-4 py-3.5 shadow-sm"
                >
                  <span className="h-2 w-2 animate-bounce rounded-full bg-slate-400" />
                  <span
                    className="h-2 w-2 animate-bounce rounded-full bg-slate-400"
                    style={{ animationDelay: "0.15s" }}
                  />
                  <span
                    className="h-2 w-2 animate-bounce rounded-full bg-slate-400"
                    style={{ animationDelay: "0.3s" }}
                  />
                </div>
              </div>
            )}

            {backendDown && (
              <div
                role="alert"
                className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 shadow-sm"
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

          <div className="border-t border-slate-100 bg-slate-50/60 p-4 sm:p-6">
            <div className="mb-4">
              <p
                id="example-questions-label"
                className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500"
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
                    className="rounded-full border border-slate-300 bg-white px-3.5 py-1.5 text-sm text-slate-700 shadow-sm transition-all hover:-translate-y-px hover:border-blue-400 hover:bg-blue-50 hover:text-blue-800 hover:shadow disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:translate-y-0"
                  >
                    {question}
                  </button>
                ))}
              </div>
            </div>

            <form
              onSubmit={(event) => {
                event.preventDefault();
                void sendMessage(inputRef.current?.value ?? input);
              }}
              className="flex items-center gap-2"
            >
              <div className="flex-1">
                <label htmlFor="chat-input" className="sr-only">
                  Your message
                </label>
                <input
                  id="chat-input"
                  type="text"
                  ref={inputRef}
                  defaultValue=""
                  onChange={(event) => setInput(event.target.value)}
                  placeholder="Type your message…"
                  disabled={loading}
                  autoComplete="off"
                  className="w-full rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm text-slate-900 shadow-sm placeholder:text-slate-400 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200 disabled:opacity-60"
                />
              </div>
              <button
                type="submit"
                disabled={loading}
                aria-label="Send message"
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-blue-600 text-white shadow-sm transition-all hover:bg-blue-700 hover:shadow disabled:cursor-not-allowed disabled:opacity-50"
              >
                <PaperAirplaneIcon className="h-5 w-5" />
              </button>
            </form>
          </div>
        </section>

        {/* Timeline panel */}
        <aside
          aria-label="Activity history"
          className="h-fit rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-6 lg:sticky lg:top-24"
        >
          <h2 className="text-lg font-semibold tracking-tight text-slate-900">
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
