# Performance optimization notes

The functional architecture remains unchanged:

SQL Server -> ReplayProvider -> ReplayProducer -> EventBus -> Aggregator -> Redis -> WebSocket -> React.

The changes focus on latency and throughput without weakening reconciliation,
deduplication, Redis snapshot semantics or WebSocket/API contracts.

## Baseline profile

Local synthetic benchmark, 500 replay events, no real SQL I/O:

| Segment | Median | p95 |
| --- | ---: | ---: |
| read/mock batch creation | 3.404 ms | 3.972 ms |
| unit publish + aggregation | 6.576 ms | 8.021 ms |
| alert evaluation | 0.265 ms | 0.388 ms |
| snapshot build | 0.277 ms | 0.461 ms |
| total | 10.634 ms | 12.000 ms |

Dominant bottlenecks:

- one async publish per transaction;
- one Aggregator lock acquisition per transaction;
- cache/WebSocket work held in the engine's critical path.

## Optimizations

- Added native transaction batch publication:
  `ReplayProducer.publish_events()` -> `EventBus.publish_transaction_batch()` ->
  `AnalyticsAggregator.consume_transaction_batch()`.
- Kept unit publish/subscribe methods for future producers and compatibility.
- Removed Redis read-after-write from snapshot updates.
- The engine now builds a coherent snapshot while holding the pipeline lock, then
  writes Redis and queues WebSocket messages after releasing the lock.
- WebSocket broadcast is latest-only and non-blocking per connection. Slow
  clients have a bounded queue of one supervision update; newer snapshots replace
  older unsent snapshots.
- Frontend snapshot application no longer waits 250 ms. It coalesces bursts with
  `requestAnimationFrame` and applies only the largest `state_version`.
- ECharts live updates no longer run 650-700 ms animations.
- Backend exposes bounded latency summaries under `engine.metrics()["latencies"]`
  for SQL fetch, EventBus, aggregation, snapshot build, Redis write,
  backend end-to-end and WebSocket queue/send latency.

## After profile

Local synthetic benchmark, 5,000 replay events, no real SQL I/O:

| Path | publish + aggregate |
| --- | ---: |
| unit events | 86.467 ms |
| native batch | 65.204 ms |

Observed improvement: 24.6% for the publish/aggregate segment on this machine.

For 500-event synthetic runs, object creation and aggregation dominate enough
that the total median stays in the same range. The larger batch benchmark better
isolates the removed per-event await/lock overhead.

## Compromises

- Ranking sections remain exact and coherent per snapshot. They were not made
  approximate or stale in this pass.
- No `orjson` dependency was added; there was no measured need to change JSON
  semantics.
- The Vite chunk-size warning remains unrelated to replay latency.

## Reconciliation check

After optimization, portfolio_demo reconciliation for 2025-01-15 remains validated:

- SQL transactions: 6,400
- Aggregator transactions: 6,400
- SQL refused: 610
- Aggregator refused: 610
- SQL non-completed: 52
- Aggregator non-completed: 52
- SQL amount TND: 720,000.000
- Aggregator amount TND: 720,000.000
- unknown status values: none
- differences: zero
