import assert from "node:assert/strict";

import { StableAxisMaxTracker, VisualInterpolationEngine } from "../src/lib/visualInterpolation.ts";

let scheduled: FrameRequestCallback | null = null;
const engine = new VisualInterpolationEngine(
  (callback) => {
    scheduled = callback;
    return 1;
  },
  () => {
    scheduled = null;
  },
  () => 0,
);

function drive(times: number[]) {
  for (const time of times) engine.tick(time);
}

function frames(start: number, count: number, step = 16) {
  return Array.from({ length: count }, (_item, index) => start + index * step);
}

const ascending: number[] = [];
engine.subscribe("ascending", (value) => ascending.push(value));
engine.setTarget("ascending", 100, { epsilon: 0.01, timeConstantMs: 80 });
drive(frames(0, 90));
assert.equal(Math.round(ascending.at(-1)!), 100);
assert.ok(ascending.some((value) => value > 0 && value < 100));

const descending: number[] = [];
engine.reset("descending", 1200);
engine.subscribe("descending", (value) => descending.push(value));
engine.setTarget("descending", 900, { epsilon: 0.5, timeConstantMs: 90 });
drive(frames(0, 120));
assert.equal(Math.round(descending.at(-1)!), 900);
assert.ok(descending.some((value) => value < 1200 && value > 900));

const retargeted: number[] = [];
engine.reset("retarget", 0);
engine.subscribe("retarget", (value) => retargeted.push(value));
engine.setTarget("retarget", 1000, { epsilon: 0.5, timeConstantMs: 120 });
drive(frames(0, 4));
const midFlight = retargeted.at(-1)!;
engine.setTarget("retarget", 200, { epsilon: 0.5, timeConstantMs: 120 });
drive(frames(64, 140));
assert.ok(midFlight > 0 && midFlight < 1000);
assert.equal(Math.round(retargeted.at(-1)!), 200);
assert.ok(engine.metrics().visual_coalesced_targets >= 1);

const fastForward: number[] = [];
engine.configureDefaults({ visualMode: "fast_forward", period: "year", expectedSnapshotIntervalMs: 100, maxVisualLagMs: 450 });
engine.reset("fast-forward", 0);
engine.subscribe("fast-forward", (value) => fastForward.push(value));
engine.setTarget("fast-forward", 100000, { epsilon: 1, timeConstantMs: 90 });
drive(frames(0, 70));
assert.equal(Math.round(fastForward.at(-1)!), 100000);
assert.ok(fastForward.some((value) => value > 0 && value < 100000));
engine.configureDefaults({ visualMode: "normal", period: "today", expectedSnapshotIntervalMs: 500 });

const seriesFrames: number[][] = [];
engine.subscribeSeries("series", (value) => seriesFrames.push(value));
engine.setSeriesTarget("series", [0, 10, 20], { reset: true });
engine.setSeriesTarget("series", [100, 50, 10, 30], { epsilon: 0.1, timeConstantMs: 100 });
drive(frames(0, 120));
assert.deepEqual(seriesFrames.at(-1)?.map(Math.round), [100, 50, 10, 30]);
assert.ok(seriesFrames.some((row) => row.length === 4 && row[0] > 0 && row[0] < 100));

const resetSeriesFrames: number[][] = [];
engine.subscribeSeries("run-a:series-reset", (value) => resetSeriesFrames.push(value));
engine.setSeriesTarget("run-a:series-reset", [5, 10], { reset: true });
engine.setSeriesTarget("run-b:series-reset", [90, 100], { reset: true });
engine.subscribeSeries("run-b:series-reset", (value) => resetSeriesFrames.push(value));
assert.deepEqual(resetSeriesFrames.at(-1), [90, 100]);

const axis = new StableAxisMaxTracker();
const high = axis.update([1000]);
const smallDip = axis.update([930]);
assert.equal(smallDip, high);
const largeDrop = axis.update([100]);
assert.ok(largeDrop < high);
assert.ok(largeDrop > 100);

const reduced: number[] = [];
engine.subscribe("reduced", (value) => reduced.push(value));
engine.setTarget("reduced", 77, { reducedMotion: true });
assert.equal(reduced.at(-1), 77);

assert.ok(scheduled);
engine.stop();

console.log("visual interpolation tests passed");
