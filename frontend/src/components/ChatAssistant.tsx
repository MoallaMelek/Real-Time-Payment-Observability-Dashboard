import { useEffect, useMemo, useRef, useState } from "react";

import { fetchChatSuggestions, sendChatMessage } from "../services/chatApi";
import { useDashboardTheme } from "../theme/DashboardTheme";
import type { DashboardPeriod } from "../types/dashboard";
import type { ChatMessage, ChatSuggestion } from "../types/chat";

interface Props {
  selectedPeriod: DashboardPeriod;
}

function newId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function ChatAssistant({ selectedPeriod }: Props) {
  const { language, t } = useDashboardTheme();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<ChatSuggestion[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>(() => [
    {
      id: "assistant-welcome",
      role: "assistant",
      content: language === "en"
        ? "Hello. I can explain KPI, merchants, anomalies, affiliations, Redis, EventBus and the replay architecture."
        : "Bonjour. Je peux expliquer les KPI, commercants, anomalies, affiliations, Redis, EventBus et l'architecture de replay.",
    },
  ]);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    fetchChatSuggestions(language).then(setSuggestions).catch(() => setSuggestions([]));
  }, [language]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, open]);

  const canSend = input.trim().length > 0 && !loading;

  const submit = async (messageText = input) => {
    const trimmed = messageText.trim();
    if (!trimmed || loading) return;
    const userMessage: ChatMessage = { id: newId("user"), role: "user", content: trimmed };
    const pendingId = newId("assistant");
    setMessages((current) => [
      ...current,
      userMessage,
      { id: pendingId, role: "assistant", content: t("chatThinking"), pending: true },
    ]);
    setInput("");
    setLoading(true);
    try {
      const response = await sendChatMessage({ message: trimmed, period: selectedPeriod, language, session_id: sessionId ?? undefined });
      setSessionId(response.session_id ?? sessionId);
      setMessages((current) => current.map((item) => item.id === pendingId ? {
        id: pendingId,
        role: "assistant",
        content: response.answer,
        sources: response.sources,
        followUp: response.follow_up,
      } : item));
    } catch {
      setMessages((current) => current.map((item) => item.id === pendingId ? {
        id: pendingId,
        role: "assistant",
        content: t("chatError"),
        error: true,
      } : item));
    } finally {
      setLoading(false);
    }
  };

  const visibleSuggestions = useMemo(() => suggestions.slice(0, 5), [suggestions]);

  return (
    <div className={`chat-assistant ${open ? "is-open" : ""}`}>
      {open ? (
        <aside className="chat-panel" aria-label={t("chatAssistant")}>
          <header className="chat-header">
            <div>
              <strong>{t("chatAssistant")}</strong>
              <span>{t("chatPrompt")}</span>
            </div>
            <button type="button" onClick={() => setOpen(false)} aria-label={t("chatClose")}>×</button>
          </header>

          <div className="chat-suggestions" aria-label="Suggestions">
            {visibleSuggestions.map((item) => (
              <button key={item.label} type="button" onClick={() => submit(item.label)} disabled={loading}>
                {item.label}
              </button>
            ))}
          </div>

          <div className="chat-messages" ref={scrollRef}>
            {messages.map((message) => (
              <article key={message.id} className={`chat-message chat-role-${message.role} ${message.pending ? "is-pending" : ""} ${message.error ? "is-error" : ""}`}>
                <p>{message.content}</p>
                {message.sources?.length ? (
                  <div className="chat-sources">
                    <span>{t("chatSources")}</span>
                    {message.sources?.map((source) => <em key={`${message.id}-${source.type}-${source.label}`}>{source.label}</em>)}
                  </div>
                ) : null}
                {message.followUp?.length ? (
                  <div className="chat-followup">
                    {message.followUp.slice(0, 2).map((item) => (
                      <button key={item} type="button" onClick={() => submit(item)} disabled={loading}>{item}</button>
                    ))}
                  </div>
                ) : null}
              </article>
            ))}
          </div>

          <form className="chat-input-row" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
            <input value={input} onChange={(event) => setInput(event.target.value)} placeholder={t("chatInput")} aria-label={t("chatInput")} />
            <button type="submit" disabled={!canSend}>{t("chatSend")}</button>
          </form>
        </aside>
      ) : null}

      <button type="button" className="chat-fab" onClick={() => setOpen((current) => !current)} aria-label={open ? t("chatClose") : t("chatOpen")} aria-expanded={open}>
        <span aria-hidden="true">AI</span>
      </button>
    </div>
  );
}
