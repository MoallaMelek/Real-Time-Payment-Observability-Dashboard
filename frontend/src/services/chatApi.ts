import type { ChatRequest, ChatResponse, ChatSuggestion } from "../types/chat";

const API_BASE_URL = import.meta.env?.VITE_API_BASE_URL ?? "http://localhost:8000";

export async function sendChatMessage(payload: ChatRequest): Promise<ChatResponse> {
  const response = await fetch(`${API_BASE_URL}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`chat_request_failed:${response.status}`);
  }
  return response.json() as Promise<ChatResponse>;
}

export async function fetchChatSuggestions(language: "fr" | "en"): Promise<ChatSuggestion[]> {
  const response = await fetch(`${API_BASE_URL}/api/chat/suggestions?language=${language}`);
  if (!response.ok) {
    throw new Error(`chat_suggestions_failed:${response.status}`);
  }
  return response.json() as Promise<ChatSuggestion[]>;
}
