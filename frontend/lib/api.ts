/**
 * Typed client for the AI E-Commerce Automation backend (Phase 1).
 *
 * Base URL comes from NEXT_PUBLIC_API_URL (default: http://127.0.0.1:8000).
 * No secrets are used here — this is the public demo frontend.
 */

const API_URL: string =
  process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export function apiBaseUrl(): string {
  return API_URL;
}

/** One step of the workflow timeline returned by POST /api/chat. */
export interface TimelineStep {
  key: string;
  label_de: string;
  status: string;
}

/** Minimal order fields returned by POST /api/chat (Phase 2). */
export interface ChatOrder {
  order_ref: string | null;
  status: string | null;
  status_de: string | null;
  carrier: string | null;
  tracking_ref: string | null;
  eta_date: string | null;
  eta_date_de: string | null;
  total: string;
}

/** Successful POST /api/chat response (Phase 2).
 *
 * ``answer`` / ``events`` / ``ticket`` are the canonical names; ``reply_de``,
 * ``timeline`` and ``ticket_id`` are kept for backward compatibility.
 */
export interface ChatResponse {
  conversation_id: number;
  intent: string;
  confidence: number;
  order_ref: string | null;
  reply_de: string;
  answer: string;
  order: ChatOrder | null;
  timeline: TimelineStep[];
  events: TimelineStep[];
  ticket_id: number | string | null;
  ticket: { id: number | string } | null;
  provider: string;
  escalated: boolean;
  /** Phase 7: true when the daily AI quota was exhausted and the deterministic
   *  mock provider answered instead. */
  quota_fallback?: boolean;
  quota_note?: string;
}

/** A ticket row from GET /api/tickets. */
export interface Ticket {
  id: number;
  conversation_id: number | null;
  intent: string | null;
  intent_label_de: string | null;
  order_ref: string | null;
  summary: string | null;
  status: string;
  priority: string | null;
  escalation_reason: string | null;
  recommended_action: string | null;
  created_at: string;
}

/** A workflow event row from GET /api/events. */
export interface WorkflowEvent {
  id: number;
  store_id: string;
  conversation_id: number | null;
  event_key: string;
  label_de: string;
  status: string;
  created_at: string;
}

/** Thrown when the backend answers with a non-2xx status.
 *  Carries the backend's honest German reply (reply_de) when present. */
export class ApiError extends Error {
  status: number;
  replyDe?: string;

  constructor(status: number, message: string, replyDe?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.replyDe = replyDe;
  }
}

interface ErrorPayload {
  error?: string;
  detail?: string;
  message?: string;
  reply_de?: string;
}

// Requests time out after 25 s so a hung network can never leave the UI
// stuck in a loading state (e.g. the chat send button staying disabled).
const REQUEST_TIMEOUT_MS = 25000;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { ...init, signal: controller.signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError(
        0,
        "The request timed out — please check your connection and try again.",
      );
    }
    throw error;
  } finally {
    clearTimeout(timer);
  }

  if (!res.ok) {
    let payload: ErrorPayload | null = null;
    try {
      payload = (await res.json()) as ErrorPayload;
    } catch {
      // Response has no JSON body — keep the generic message.
    }
    // Prefer the backend's German message; never render raw internals —
    // FastAPI error bodies are controlled and generic by design.
    const message =
      payload?.message ??
      payload?.detail ??
      payload?.error ??
      `Backend returned HTTP ${res.status}`;
    throw new ApiError(res.status, message, payload?.reply_de);
  }

  return (await res.json()) as T;
}

export function postChat(message: string): Promise<ChatResponse> {
  return request<ChatResponse>("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
}

export function getTickets(limit = 50, status?: string): Promise<Ticket[]> {
  const query = status
    ? `/api/tickets?limit=${limit}&status=${encodeURIComponent(status)}`
    : `/api/tickets?limit=${limit}`;
  return request<Ticket[]>(query);
}

/** One row of the German intent distribution from GET /api/dashboard/summary. */
export interface IntentBreakdown {
  intent: string;
  label_de: string;
  count: number;
}

/** A recent workflow event inside the dashboard summary. */
export interface SummaryEvent {
  id: number;
  event_key: string;
  label_de: string;
  status: string;
  conversation_id: number | null;
  created_at: string;
}

/** GET /api/dashboard/summary response. Every number is a real DB count. */
export interface DashboardSummary {
  store_id: string;
  conversations_count: number;
  messages_count: number;
  tickets_open: number;
  tickets_total: number;
  escalations_count: number;
  intents_breakdown: IntentBreakdown[];
  recent_events: SummaryEvent[];
  demo_data: boolean;
}

/** One conversation row from GET /api/conversations. */
export interface Conversation {
  id: number;
  started_at: string;
  message_count: number;
  last_intent: string | null;
  last_intent_label_de: string;
}

/** A single message inside a conversation detail view. */
export interface ConversationMessage {
  id: number;
  role: "user" | "assistant" | "system";
  content: string;
  created_at: string;
}

/** A workflow event in a conversation/event timeline. */
export interface TimelineEvent {
  id: number;
  event_key: string;
  label_de: string;
  status: string;
  created_at: string;
}

/** GET /api/conversations/{id} response: messages + real event timeline. */
export interface ConversationDetail {
  id: number;
  started_at: string;
  message_count: number;
  messages: ConversationMessage[];
  events: TimelineEvent[];
}

/** GET /api/tickets/{id} response: ticket + linked conversation + events. */
export interface TicketDetail extends Ticket {
  intent_label_de: string;
  messages: ConversationMessage[];
  events: TimelineEvent[];
}

export function getDashboardSummary(): Promise<DashboardSummary> {
  return request<DashboardSummary>("/api/dashboard/summary");
}

export function getConversations(limit = 50): Promise<Conversation[]> {
  return request<Conversation[]>(`/api/conversations?limit=${limit}`);
}

export function getConversation(id: number): Promise<ConversationDetail> {
  return request<ConversationDetail>(`/api/conversations/${id}`);
}

export function getTicket(id: number): Promise<TicketDetail> {
  return request<TicketDetail>(`/api/tickets/${id}`);
}

export function getEvents(limit = 20): Promise<WorkflowEvent[]> {
  return request<WorkflowEvent[]>(`/api/events?limit=${limit}`);
}

export function getHealth(): Promise<{ status?: string }> {
  return request<{ status?: string }>("/health");
}

/** Phase 7: demo reset requires the server-side admin key
 * (``X-Admin-Key``). The key is entered by the operator and kept in
 * memory only — never persisted in the browser. */
export function resetDemo(
  adminKey?: string,
): Promise<{ status: string; demo_store: string }> {
  return request<{ status: string; demo_store: string }>("/api/demo/reset", {
    method: "POST",
    headers: adminKey ? { "X-Admin-Key": adminKey } : undefined,
  });
}
