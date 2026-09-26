# Data reconciliation and quality report

This project keeps the existing replay flow:

SQL Server -> ReplayProvider -> ReplayProducer -> EventBus -> Aggregator -> Redis -> WebSocket -> React.

The dashboard never reads SQL directly.  SQL Server is used only by the backend
provider through predefined aggregate queries.

## KPI provenance

| Metric | Provenance | Category |
| --- | --- | --- |
| `total_transactions` | Aggregator accepted replay events, reconciled with SQL count | `source_fact` |
| `total_amount` | Aggregator accepted replay events, reconciled with SQL amount converted from millimes to TND | `source_fact` |
| `refused` / `refusal_rate` | Refused count is reconciled with SQL status classification; rate is computed | `source_fact` / `derived_metric` |
| `non_completed_transactions` | Aggregator accepted replay events, reconciled with SQL status classification | `source_fact` |
| `active_terminals` | Aggregator distinct terminals, reconciled with SQL distinct terminals | `source_fact` |
| `total_transfers_amount`, `transfers_count` | SQL aggregate from transfer table for the active period | `source_fact` |
| `success_rate`, `average_processing_time_ms`, slow/fraud indicators | Aggregator calculations from replayed facts | `derived_metric` |
| `global_risk_score`, `top_anomalies` | Risk engine calculations | `derived_metric` |
| affiliation stock evolution | Deterministic projection over observed merchants | `simulated_projection` |

## Invariants

The quality report checks:

- non-negative totals and amounts;
- refused, non-completed, success and active-terminal counts do not exceed total transactions;
- refusal and success rates remain between 0 and 100;
- status distribution sums to total transactions;
- response-time distribution sums to transactions with processing time;
- affiliation available/reserved/affected values balance with total when available.

Any violation is logged and makes the reconciliation status `invalid`.

## Reconciliation states

- `pending`: replay is not complete yet;
- `validated`: replay is complete and differences are zero;
- `warning`: only explicitly tolerated differences exist, such as millime rounding within `0.001` TND, or unknown SQL statuses are visible;
- `invalid`: untolerated differences, invariant violations, or EventBus delivery errors exist;
- `unavailable`: SQL control source is unavailable, so the system never claims validation.

The same `data_quality` object is stored in the KPI cache and emitted through
REST/WebSocket.  React displays it without recalculating business metrics.

## Explicit date report

For field validation against portfolio_demo, the backend exposes a controlled report
that replays explicit dates through an isolated provider/producer/EventBus/
Aggregator pipeline:

```text
GET /api/supervision/reconciliation-report?dates=2025-01-15&dates=2025-01-16
```

The response rows contain the expected SQL count, Aggregator count, difference,
status, refused/non-completed/TPE counts, amount comparison and unknown raw SQL
statuses.  This endpoint uses the same predefined provider queries and is not
called by the React dashboard.
