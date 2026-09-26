from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    example: dict[str, Any]
    output: str
    source: str


TOOL_CATALOG = [
    ToolDefinition("greeting", "Answer a simple greeting without RAG or source code.", {}, {}, "short greeting", "reasoning"),
    ToolDefinition("clarify_question", "Ask a concise clarification when the user question is too ambiguous.", {}, {}, "clarification", "reasoning"),
    ToolDefinition("conversation_operation", "Operate on previous conversation results already stored in memory, without calling SQL or RAG again.", {"operation": "combine|compare|summarize|continue|expand"}, {"operation": "combine"}, "memory-based response", "reasoning"),
    ToolDefinition("unsupported_capability", "Return a controlled answer when the requested business capability is not exposed by the available tools.", {"capability": "unsupported_x", "requested": "text"}, {"capability": "unsupported_slow_transaction_count", "requested": "transactions lentes"}, "controlled unsupported response", "reasoning"),
    ToolDefinition("multi_period_metric", "Read the same metric for several resolved periods and return each value separately.", {"periods": "resolved period list", "metric": "metric name"}, {"periods": [{"period_key": "today"}, {"period_key": "yesterday"}], "metric": "total"}, "multi-period metric values", "reasoning"),
    ToolDefinition("project_code_search", "Search project source code for explicit code questions.", {"query": "text", "limit": "1..5"}, {"query": "chat component", "limit": 3}, "file paths and short excerpts", "code"),
    ToolDefinition("get_current_snapshot", "Read current Redis dashboard snapshot. Alias of get_dashboard_snapshot.", {"period": "period"}, {"period": "today"}, "DashboardSnapshot JSON", "redis"),
    ToolDefinition("get_dashboard_snapshot", "Read the current dashboard snapshot from Redis/API.", {"period": "today|yesterday|7d|30d|quarter|year"}, {"period": "today"}, "DashboardSnapshot JSON", "snapshot"),
    ToolDefinition("get_kpi_by_period", "Read KPI summary for a given period. Alias of get_kpi_summary.", {"period": "period"}, {"period": "today"}, "KPI JSON", "redis"),
    ToolDefinition("get_kpi_summary", "Read KPI summary for a dashboard period. Prefer this for current dashboard KPI.", {"period": "period"}, {"period": "today"}, "KPI JSON", "snapshot"),
    ToolDefinition("get_transaction_volume", "Count non-sensitive transaction volume for a period or last N days.", {"period": "period", "days": "optional integer 1..30"}, {"days": 3}, "transaction count JSON", "sql"),
    ToolDefinition("get_transactions_last_n_days", "Count non-sensitive transactions over the last N days using controlled SQL.", {"days": "integer 1..30"}, {"days": 3}, "count and date window", "sql"),
    ToolDefinition("get_transactions_between_dates", "Count non-sensitive transactions between two ISO dates using controlled SQL.", {"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"}, {"start_date": "2026-05-10", "end_date": "2026-05-12"}, "count and refusal summary", "sql"),
    ToolDefinition("compare_date_ranges", "Compare two explicit ISO date ranges using controlled SQL.", {"left_start": "YYYY-MM-DD", "left_end": "YYYY-MM-DD", "right_start": "YYYY-MM-DD", "right_end": "YYYY-MM-DD"}, {"left_start": "2026-01-01", "left_end": "2026-01-31", "right_start": "2026-02-01", "right_end": "2026-02-28"}, "range comparison JSON", "sql"),
    ToolDefinition("get_tpe_count_by_period", "Count distinct non-sensitive TPE/terminal identifiers between two ISO dates using controlled SQL.", {"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"}, {"start_date": "2026-05-10", "end_date": "2026-05-12"}, "distinct terminal count", "sql"),
    ToolDefinition("compare_periods", "Compare KPI between two known dashboard periods.", {"left": "period", "right": "period"}, {"left": "today", "right": "yesterday"}, "comparison JSON", "snapshot/sql"),
    ToolDefinition("get_top_merchants", "Rank merchants by volume/refusals for a period.", {"period": "period", "limit": "1..20"}, {"period": "7d", "limit": 5}, "merchant rows", "sql"),
    ToolDefinition("get_top_tpe", "Rank terminals/TPE by incidents for a period.", {"period": "period", "limit": "1..20"}, {"period": "today", "limit": 5}, "terminal rows", "sql"),
    ToolDefinition("get_top_anomalies", "Read top anomaly merchants from the dashboard projection.", {"period": "period", "limit": "1..20"}, {"period": "today", "limit": 5}, "anomaly rows", "snapshot"),
    ToolDefinition("get_status_distribution", "Read transaction status distribution for a period.", {"period": "period"}, {"period": "today"}, "status rows", "snapshot"),
    ToolDefinition("get_incidents_by_hour", "Read incident distribution by hour. Legacy alias of get_hourly_incidents.", {"period": "period"}, {"period": "today"}, "hourly rows", "sql"),
    ToolDefinition("get_hourly_incidents", "Read incident distribution by hour.", {"period": "period"}, {"period": "today"}, "hourly rows", "sql"),
    ToolDefinition("get_affiliation_summary", "Read projected affiliation stock. Legacy alias of get_affiliations.", {}, {}, "affiliation stock JSON", "snapshot"),
    ToolDefinition("get_affiliations", "Read projected affiliation stock.", {}, {}, "affiliation stock JSON", "snapshot"),
    ToolDefinition("get_refusal_analysis", "Collect KPI, hourly incidents, top anomalies and top TPE for refusal diagnosis.", {"period": "period"}, {"period": "today"}, "combined evidence", "multi"),
    ToolDefinition("get_success_analysis", "Analyze success rate and non-completed transactions for a period.", {"period": "period"}, {"period": "today"}, "success JSON", "snapshot"),
    ToolDefinition("get_risk_summary", "Summarize risk score, top anomalies and active alerts.", {"period": "period"}, {"period": "today"}, "risk JSON", "snapshot"),
    ToolDefinition("get_risk_alerts", "Read active risk alerts and global risk summary.", {"period": "period"}, {"period": "today"}, "risk alerts JSON", "redis"),
    ToolDefinition("get_affiliation_stock", "Read projected affiliation stock.", {}, {}, "affiliation stock JSON", "redis"),
    ToolDefinition("get_replay_status", "Read replay and Fast Forward status.", {"period": "period"}, {"period": "today"}, "replay status JSON", "redis"),
    ToolDefinition("get_redis_runtime_status", "Read backend Redis runtime status and active cache fallback metadata.", {"period": "period"}, {"period": "today"}, "Redis runtime JSON", "redis"),
    ToolDefinition("get_data_quality_status", "Read reconciliation and data quality status for the selected dashboard period.", {"period": "period"}, {"period": "today"}, "data quality JSON", "snapshot"),
    ToolDefinition("get_period_status", "Read period status and date window.", {"period": "period"}, {"period": "7d"}, "period status JSON", "redis"),
    ToolDefinition("get_dashboard_metadata", "Read non-sensitive dashboard architecture and metadata.", {}, {}, "metadata JSON", "reasoning"),
    ToolDefinition("search_merchant", "Find a merchant by name or identifier through a controlled SQL/search tool.", {"query": "text", "limit": "1..20"}, {"query": "adam", "limit": 5}, "merchant rows", "sql"),
    ToolDefinition("get_merchant_details", "Analyze one merchant profile in the dashboard projection.", {"merchant_name_or_id": "text", "period": "period"}, {"merchant_name_or_id": "Adam Market", "period": "today"}, "merchant profile JSON", "snapshot"),
    ToolDefinition("explain_metric", "Explain a known KPI formula without querying sensitive data.", {"metric_name": "text"}, {"metric_name": "refusal_rate"}, "definition", "docs"),
    ToolDefinition("search_docs", "Legacy alias for local RAG documentation search.", {"query": "text"}, {"query": "Redis EventBus architecture"}, "top chunks", "docs"),
    ToolDefinition("search_project_documentation", "Search local project RAG documentation.", {"query": "text"}, {"query": "dashboard architecture"}, "top chunks", "docs"),
    ToolDefinition("explain_dashboard_component", "Explain a dashboard component from local documentation.", {"query": "text"}, {"query": "KPI cards"}, "explanation chunks", "docs"),
    ToolDefinition("explain_redis_architecture", "Explain Redis cache architecture from local documentation.", {"query": "text"}, {"query": "Redis"}, "explanation chunks", "docs"),
    ToolDefinition("explain_eventbus_architecture", "Explain EventBus architecture from local documentation.", {"query": "text"}, {"query": "EventBus"}, "explanation chunks", "docs"),
    ToolDefinition("explain_replay_architecture", "Explain historical replay architecture from local documentation.", {"query": "text"}, {"query": "LIVE REPLAY"}, "explanation chunks", "docs"),
    ToolDefinition("generate_dashboard_opinion", "Generate a reasoned dashboard opinion from KPI, architecture and known limits.", {"period": "period"}, {"period": "today"}, "opinion JSON", "reasoning"),
    ToolDefinition("search_documentation", "Search local RAG documentation.", {"query": "text"}, {"query": "Redis EventBus architecture"}, "top chunks", "docs"),
    ToolDefinition("search_dashboard_explanations", "Search dashboard feature and KPI explanations.", {"query": "text"}, {"query": "Fast Forward KPI dashboard"}, "top chunks", "docs"),
    ToolDefinition("search_architecture", "Search architecture explanations.", {"query": "text"}, {"query": "LIVE REPLAY vs real time"}, "top chunks", "docs"),
    ToolDefinition("search_redis", "Search Redis/cache explanations.", {"query": "text"}, {"query": "why dashboard is fast"}, "top chunks", "docs"),
    ToolDefinition("search_eventbus", "Search EventBus explanations.", {"query": "text"}, {"query": "why EventBus"}, "top chunks", "docs"),
]


TOOL_CATALOG_BY_NAME = {tool.name: tool for tool in TOOL_CATALOG}
ALLOWED_TOOL_NAMES = set(TOOL_CATALOG_BY_NAME)
