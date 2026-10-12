# Payment observability QA checklist

- [ ] Verify dashboard totals match the underlying transaction aggregation.
- [ ] Test reconnect behavior after a WebSocket disconnect.
- [ ] Check duplicate, late, and out-of-order transaction events.
- [ ] Validate regional breakdowns and top-merchant ranking ties.
- [ ] Ensure transaction logs and screenshots do not expose sensitive payment details.
- [ ] Measure dashboard freshness under burst traffic.
- [ ] Distinguish simulated data from production payment activity.

Record sample data, expected output, and actual output for each test.
