# Visual smoothness

This pass does not change the backend data pipeline or the API/WebSocket
contracts. Snapshots remain exact and discrete. The frontend treats them as
targets for visual rendering.

## Render audit

Before this change:

- KPI cards rendered formatted strings directly from the latest snapshot.
- A new snapshot therefore changed counters, amounts and percentages in one
  visual step.
- KPI sparklines and ECharts series still received complete target arrays in
  one update.
- The dashboard already coalesced WebSocket messages with
  `requestAnimationFrame`, so the remaining issue was visual interpolation, not
  snapshot latency.
- The default normal replay cadence emitted 500-row targets every 5 seconds,
  which made jumps large even though SQL/EventBus/Aggregator were not sleeping
  to fake animation.

Primary causes of robotic motion:

- no separate visual state for numeric KPI values;
- no continuous movement toward the latest target;
- chart arrays were replaced as complete target series;
- chart axes recalculated tightly around every new range;
- no lightweight visual performance metrics.

## Target vs display state

`useDashboardData` still owns the exact latest backend snapshot. Animated KPI,
sparkline and chart components subscribe to a central `VisualInterpolationEngine`:

```text
backend snapshot -> target value -> VisualInterpolationEngine -> displayed value
```

Only the animated numeric text and chart/sparkline components rerender on
animation frames. The page, tables, filters, chatbot and non-animated sections do
not rerender at 60 FPS because interpolated values live inside small memoized
components.

If `replay_run_id` changes, the channel key changes too, so the old animation is
discarded and the new context starts from the new target.

## Interpolation

The interpolation loop:

- uses one shared `requestAnimationFrame` scheduler;
- uses real elapsed time, not frame counts;
- moves continuously toward the latest target;
- coalesces new targets during an active animation;
- supports scalar values and numeric series with the same loop;
- adapts speed by distance, period, replay mode and expected snapshot cadence;
- snaps exactly to the target inside a configured epsilon;
- respects `prefers-reduced-motion` by applying values immediately.

Counters are interpolated as floats and rounded only at render time. Amounts and
percentages are interpolated as numbers and formatted only in the component.
Series are interpolated by index; added points start from the nearest visible
point and then move toward the new target instead of appearing as a full dataset
replacement.

Normal mode uses a more readable profile. Fast Forward uses a shorter lag bound
and more aggressive catch-up, but still moves from the current visual position to
the latest target.

## Charts

ECharts instances are still reused. Updates use `setOption` on the existing
instance and short update transitions. The data passed to ECharts is now already
interpolated by the visual engine:

- update animation duration: 90 ms;
- natural easing;
- hysteresis on axis maxima to expand quickly but avoid shrinking on tiny dips;
- no dispose/clear/remount on snapshot updates.

Affected chart surfaces:

- incidents by hour bars and trend line;
- fraud/slow/anti-fraud trend lines;
- response-time histogram;
- status donut values;
- KPI sparklines.

## Backend pacing

No sleep was added to SQL, ReplayProducer, EventBus, Aggregator, Redis or
WebSocket. The normal default replay step was reduced from 500 transactions every
5 seconds to 100 transactions every 0.5 seconds. Fast Forward keeps a larger SQL
prefetch but emits visible 250-row sub-batches by default. This reduces target
distance without blocking ingestion primitives.

## Debug metrics

In the browser console:

```js
window.__dashboardVisualMetrics?.()
```

returns local-only visual metrics such as frame count, dropped frames, minimum
observed FPS, coalesced targets, active scalar/series channel counts, average
snapshot interval and average/max jump size. No telemetry is sent.

## Synthetic benchmark

Node synthetic run of scalar and series channels over simulated 16 ms frames:

| Metric | Value |
| --- | ---: |
| tested scalar paths | A to B, B to A, retarget before arrival, fast-forward |
| tested series paths | same-length update, appended point, run reset |
| dropped frames | 0 |
| minimum observed FPS from simulated frame deltas | 63 |

This is not a browser FPS claim. It only shows that the interpolation math is
small compared with a 16.7 ms frame budget.

## Guarantees

- Backend snapshots remain the source of truth.
- Final displayed values converge exactly to the latest snapshot targets.
- Obsolete snapshots are still rejected by `state_version` in the sync layer.
- `replay_run_id` prevents visual mixing across periods/runs.
- Reduced-motion users do not get long animated transitions.
- Redis, reconciliation, exports and chatbot continue to consume exact backend
  snapshots, not visual intermediate values.
