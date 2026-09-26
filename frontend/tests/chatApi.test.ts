import assert from "node:assert/strict";

import { fetchChatSuggestions, sendChatMessage } from "../src/services/chatApi.ts";

const calls: Array<{ url: string; init?: RequestInit }> = [];

globalThis.fetch = async (url: string | URL | Request, init?: RequestInit): Promise<Response> => {
  calls.push({ url: String(url), init });
  if (String(url).includes("/api/chat/suggestions")) {
    return new Response(JSON.stringify([{ label: "Why is Redis used?" }]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }
  return new Response(
    JSON.stringify({
      answer: "Redis is used as a KPI cache.",
      intent: "explain_redis",
      period: "today",
      session_id: "chat-test",
      sources: [{ type: "docs", label: "Local project knowledge base" }],
      data: { cache_backend: "redis" },
      follow_up: ["Explain EventBus?"],
      plan: ["Search docs", "Synthesize"],
      tool_calls: [{ name: "search_docs", arguments: {}, purpose: "Find Redis explanation" }],
      reasoning_summary: "Used docs.",
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
};

const response = await sendChatMessage({ message: "Why Redis?", period: "today", language: "en" });
assert.equal(response.intent, "explain_redis");
assert.equal(response.sources[0].label, "Local project knowledge base");
assert.equal(JSON.parse(String(calls[0].init?.body)).message, "Why Redis?");

const suggestions = await fetchChatSuggestions("en");
assert.equal(suggestions[0].label, "Why is Redis used?");

console.log("chat api tests passed");
