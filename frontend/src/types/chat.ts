import type { DashboardPeriod } from "./dashboard";
import type { DashboardLanguage } from "../i18n/dashboard";

export interface ChatSource {
  type: "snapshot" | "sql" | "docs" | "security";
  label: string;
}

export interface ChatRequest {
  message: string;
  period?: DashboardPeriod;
  language?: DashboardLanguage;
  session_id?: string;
  conversation_id?: string;
}

export interface ChatEvidence {
  source: "redis" | "sql_tool" | "rag" | "reasoning";
  tool: string;
  metric: string;
  value: unknown;
  period: DashboardPeriod;
  confidence: "high" | "medium" | "low";
}

export interface ChatResponse {
  answer: string;
  intent: string;
  period: DashboardPeriod;
  session_id?: string | null;
  conversation_id?: string | null;
  sources: ChatSource[];
  data: Record<string, unknown>;
  follow_up: string[];
  plan: string[];
  tool_calls: Array<{ name: string; arguments: Record<string, unknown>; purpose: string }>;
  reasoning_summary?: string | null;
  provider?: "local" | "fallback" | "openai" | "gemini" | "local_fallback";
  tools_used?: string[];
  evidence?: ChatEvidence[];
  detected_period?: DashboardPeriod | null;
  confidence?: "high" | "medium" | "low";
}

export interface ChatSuggestion {
  label: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: ChatSource[];
  data?: Record<string, unknown>;
  followUp?: string[];
  plan?: string[];
  toolCalls?: Array<{ name: string; purpose: string }>;
  reasoningSummary?: string | null;
  provider?: ChatResponse["provider"];
  pending?: boolean;
  error?: boolean;
}
