# Gemini chatbot integration

Gemini is the primary reasoning and response layer for the Dashboard TPE
chatbot when `GEMINI_ENABLED=true`.

The backend still executes deterministic, controlled tools for SQL, Redis
snapshots, Business RAG and Code RAG, but Gemini receives the collected evidence
and writes the final user-facing answer.

## Preserved contracts

- Gemini is the only final-answer composer in Gemini mode.
- The existing `DataAnalystAgent` remains the internal orchestrator/tool runner
  and an optional compatibility fallback only when `GEMINI_FALLBACK_ENABLED=true`.
- Gemini never receives or executes free SQL.
- SQL access still goes only through the predefined `ChatbotSqlTools`.
- Tool names are validated against `TOOL_CATALOG` and the `ToolSelector`
  capability matrix.
- Redis, EventBus, Replay, Aggregator and dashboard KPI sources are unchanged.
- `AnswerSanitizer` stays the final response layer.
- API keys are read only from backend environment variables and are never sent to
  the frontend.

## Runtime flow

```text
ChatRequest
-> guardrails and bounded ConversationMemory context
-> HybridChatbotOrchestrator
-> local preliminary understanding and runtime context
-> Gemini structured decision
-> local validation and ToolSelector resolution
-> existing secure tool execution
-> Gemini response composition
-> grounding validation
-> AnswerSanitizer
```

If `GEMINI_FALLBACK_ENABLED=false`, Gemini unavailable/error states do not
produce a local substitute answer. The API returns a short Gemini-unavailable
response instead.

If `GEMINI_FALLBACK_ENABLED=true`, the legacy local fallback remains available
for compatibility.

If Gemini returns JSON that is close but not schema-compliant, the orchestrator
asks for one structured repair attempt before falling back.

The SDK automatic function calling path is disabled. Gemini receives tool
descriptions as data, proposes a plan, and the backend validates and executes
tools locally.

## Configuration

Gemini mode is the default target configuration; never commit a real key.

```env
GEMINI_ENABLED=true
GEMINI_API_KEY=
GEMINI_MODEL=gemini-2.5-flash
GEMINI_TIMEOUT_SECONDS=8
GEMINI_MAX_RETRIES=1
GEMINI_TEMPERATURE=0.2
GEMINI_MAX_OUTPUT_TOKENS=2048
GEMINI_ROLLOUT_PERCENT=100
GEMINI_FALLBACK_ENABLED=false
```

Install backend dependencies after pulling the change:

```powershell
pip install -r backend\requirements.txt
```

Then set `GEMINI_API_KEY` locally. Without a key, Gemini-only mode will not
produce a local analytical answer.

## Observability

The provider records internal metrics:

- request, success and fallback counts;
- timeout, rate-limit and invalid-response counts;
- average Gemini latency;
- input/output token counts when the SDK reports them;
- selected capability;
- provider used;
- circuit breaker state.

These metrics are stored in backend response metadata during Gemini-handled
requests and never include the API key.

## Test coverage

Tests use fake providers only; they do not call Gemini. Covered paths include:

- Gemini disabled with fallback disabled -> Gemini-unavailable response;
- timeout/invalid JSON/empty response -> Gemini-unavailable response when local
  fallback is disabled;
- invented SQL tool -> rejected;
- invalid date argument -> rejected;
- controlled SQL tool routing;
- Business RAG routing;
- Code RAG routing;
- sensitive-card masking before external calls;
- short factual answer without a second Gemini call;
- `AnswerSanitizer` as the final layer;
- rate-limit retry and transient 503 fallback behavior.
- Redis runtime-status routing;
- structured decision repair;
- at most two Gemini calls for an instrumented request.
