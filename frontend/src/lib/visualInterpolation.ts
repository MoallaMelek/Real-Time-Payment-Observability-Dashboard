export type VisualMode = "normal" | "fast_forward";
export type VisualPeriod = "today" | "yesterday" | "7d" | "30d" | "quarter" | "year";

export interface InterpolationOptions {
  epsilon?: number;
  timeConstantMs?: number;
  maxStepPerSecond?: number;
  reducedMotion?: boolean;
  reset?: boolean;
  visualMode?: VisualMode;
  period?: VisualPeriod;
  expectedSnapshotIntervalMs?: number;
  visualLagMs?: number;
  maxVisualLagMs?: number;
}

interface BaseChannel {
  epsilon: number;
  timeConstantMs: number;
  maxStepPerSecond: number | null;
  visualMode: VisualMode;
  period: VisualPeriod;
  expectedSnapshotIntervalMs: number | null;
  visualLagMs: number;
  maxVisualLagMs: number;
  lastTargetAt: number | null;
}

interface ScalarChannel extends BaseChannel {
  display: number;
  target: number;
  subscribers: Set<(value: number) => void>;
}

interface SeriesChannel extends BaseChannel {
  display: number[];
  target: number[];
  subscribers: Set<(value: number[]) => void>;
}

type FrameScheduler = (callback: FrameRequestCallback) => number;
type FrameCanceller = (handle: number) => void;

function defaultSchedule(callback: FrameRequestCallback): number {
  return window.requestAnimationFrame(callback);
}

function defaultCancel(handle: number): void {
  window.cancelAnimationFrame(handle);
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function periodFactor(period: VisualPeriod): number {
  if (period === "today" || period === "yesterday") return 1.08;
  if (period === "7d" || period === "30d") return 0.9;
  return 0.76;
}

function roundedAxisMax(values: Array<number | null | undefined>): number {
  const max = Math.max(1, ...values.map((value) => Number(value ?? 0)));
  const padded = max * 1.15;
  const magnitude = 10 ** Math.floor(Math.log10(padded));
  const step = magnitude >= 100 ? magnitude / 2 : Math.max(1, magnitude / 5);
  return Math.ceil(padded / step) * step;
}

function copySeries(values: number[]): number[] {
  return values.map((value) => (Number.isFinite(value) ? value : 0));
}

function alignSeriesDisplay(display: number[], target: number[]): number[] {
  if (display.length === target.length) return display;
  return target.map((_value, index) => {
    if (index < display.length) return display[index];
    if (display.length) return display[display.length - 1];
    return 0;
  });
}

export class StableAxisMaxTracker {
  private current = 0;

  reset(): void {
    this.current = 0;
  }

  update(values: Array<number | null | undefined>): number {
    const next = roundedAxisMax(values);
    if (!this.current) {
      this.current = next;
      return this.current;
    }
    if (next > this.current) {
      this.current = next;
      return this.current;
    }
    if (next < this.current * 0.62) {
      this.current = Math.max(next, this.current * 0.72);
    }
    return this.current;
  }
}

export class VisualInterpolationEngine {
  private channels = new Map<string, ScalarChannel>();
  private seriesChannels = new Map<string, SeriesChannel>();
  private frameHandle: number | null = null;
  private previousTime: number | null = null;
  private schedule: FrameScheduler;
  private cancel: FrameCanceller;
  private now: () => number;
  private frameCount = 0;
  private droppedFrames = 0;
  private minFps = Number.POSITIVE_INFINITY;
  private lastFrameDuration = 0;
  private coalescedTargets = 0;
  private targetCount = 0;
  private targetIntervalSamples: number[] = [];
  private jumpSamples: number[] = [];
  private visualLagMaxMs = 0;
  private defaultOptions: InterpolationOptions = {};

  constructor(
    schedule: FrameScheduler = defaultSchedule,
    cancel: FrameCanceller = defaultCancel,
    now: () => number = () => performance.now(),
  ) {
    this.schedule = schedule;
    this.cancel = cancel;
    this.now = now;
  }

  subscribe(key: string, subscriber: (value: number) => void): () => void {
    const channel = this.ensureChannel(key, 0);
    channel.subscribers.add(subscriber);
    subscriber(channel.display);
    return () => {
      channel.subscribers.delete(subscriber);
      if (!channel.subscribers.size && Math.abs(channel.target - channel.display) <= channel.epsilon) {
        this.channels.delete(key);
      }
    };
  }

  subscribeSeries(key: string, subscriber: (value: number[]) => void): () => void {
    const channel = this.ensureSeriesChannel(key, []);
    channel.subscribers.add(subscriber);
    subscriber([...channel.display]);
    return () => {
      channel.subscribers.delete(subscriber);
      if (!channel.subscribers.size && this.isSeriesSettled(channel)) {
        this.seriesChannels.delete(key);
      }
    };
  }

  configureDefaults(options: InterpolationOptions): void {
    this.defaultOptions = {
      ...this.defaultOptions,
      ...options,
      reset: false,
      reducedMotion: false,
    };
  }

  setTarget(key: string, target: number, options: InterpolationOptions = {}): void {
    const configured = this.withDefaults(options);
    const channel = this.ensureChannel(key, target, configured);
    const hadPendingTarget = Math.abs(channel.target - channel.display) > channel.epsilon;
    this.applyOptions(channel, configured);
    this.recordTarget(channel, Math.abs(target - channel.target));
    channel.target = target;

    if (configured.reducedMotion || configured.reset) {
      channel.display = target;
      this.notify(channel);
      return;
    }

    if (hadPendingTarget) this.coalescedTargets += 1;
    this.start();
  }

  setSeriesTarget(key: string, target: number[], options: InterpolationOptions = {}): void {
    const configured = this.withDefaults(options);
    const cleanTarget = copySeries(target);
    const channel = this.ensureSeriesChannel(key, cleanTarget, configured);
    const hadPendingTarget = !this.isSeriesSettled(channel);
    this.applyOptions(channel, configured);
    channel.display = alignSeriesDisplay(channel.display, cleanTarget);
    this.recordTarget(channel, seriesDistance(cleanTarget, channel.target));
    channel.target = cleanTarget;

    if (configured.reducedMotion || configured.reset) {
      channel.display = [...cleanTarget];
      this.notifySeries(channel);
      return;
    }

    if (hadPendingTarget) this.coalescedTargets += 1;
    this.start();
  }

  reset(key: string, value: number): void {
    const channel = this.ensureChannel(key, value);
    channel.display = value;
    channel.target = value;
    this.notify(channel);
  }

  resetSeries(key: string, value: number[]): void {
    const channel = this.ensureSeriesChannel(key, value);
    channel.display = copySeries(value);
    channel.target = copySeries(value);
    this.notifySeries(channel);
  }

  tick(time = this.now()): void {
    const previous = this.previousTime ?? time;
    const deltaMs = Math.min(80, Math.max(0, time - previous));
    this.previousTime = time;
    this.frameCount += 1;
    this.lastFrameDuration = deltaMs;
    if (deltaMs > 20) this.droppedFrames += 1;
    if (deltaMs > 0) this.minFps = Math.min(this.minFps, 1000 / deltaMs);

    let needsNextFrame = false;
    for (const channel of this.channels.values()) {
      if (this.advanceScalar(channel, deltaMs)) needsNextFrame = true;
    }
    for (const channel of this.seriesChannels.values()) {
      if (this.advanceSeries(channel, deltaMs)) needsNextFrame = true;
    }

    if (needsNextFrame) {
      this.frameHandle = this.schedule((nextTime) => this.tick(nextTime));
    } else {
      this.frameHandle = null;
      this.previousTime = null;
    }
  }

  stop(): void {
    if (this.frameHandle !== null) {
      this.cancel(this.frameHandle);
      this.frameHandle = null;
    }
    this.previousTime = null;
  }

  metrics(): Record<string, number> {
    return {
      visual_frame_count: this.frameCount,
      visual_dropped_frames: this.droppedFrames,
      visual_min_fps: Number.isFinite(this.minFps) ? Math.round(this.minFps) : 0,
      visual_last_frame_duration_ms: Math.round(this.lastFrameDuration * 100) / 100,
      visual_coalesced_targets: this.coalescedTargets,
      visual_active_scalar_channels: this.channels.size,
      visual_active_series_channels: this.seriesChannels.size,
      visual_target_count: this.targetCount,
      visual_snapshot_interval_avg_ms: roundedAverage(this.targetIntervalSamples),
      visual_jump_avg: roundedAverage(this.jumpSamples),
      visual_jump_max: roundedMax(this.jumpSamples),
      visual_lag_max_ms: Math.round(this.visualLagMaxMs),
    };
  }

  private ensureChannel(key: string, initial: number, options: InterpolationOptions = {}): ScalarChannel {
    const existing = this.channels.get(key);
    if (existing) return existing;
    const channel: ScalarChannel = {
      display: initial,
      target: initial,
      subscribers: new Set(),
      epsilon: options.epsilon ?? 0.001,
      timeConstantMs: options.timeConstantMs ?? 140,
      maxStepPerSecond: options.maxStepPerSecond ?? null,
      visualMode: options.visualMode ?? "normal",
      period: options.period ?? "today",
      expectedSnapshotIntervalMs: options.expectedSnapshotIntervalMs ?? null,
      visualLagMs: options.visualLagMs ?? 0,
      maxVisualLagMs: options.maxVisualLagMs ?? 1400,
      lastTargetAt: null,
    };
    this.channels.set(key, channel);
    return channel;
  }

  private ensureSeriesChannel(key: string, initial: number[], options: InterpolationOptions = {}): SeriesChannel {
    const existing = this.seriesChannels.get(key);
    if (existing) return existing;
    const cleanInitial = copySeries(initial);
    const channel: SeriesChannel = {
      display: cleanInitial,
      target: cleanInitial,
      subscribers: new Set(),
      epsilon: options.epsilon ?? 0.001,
      timeConstantMs: options.timeConstantMs ?? 140,
      maxStepPerSecond: options.maxStepPerSecond ?? null,
      visualMode: options.visualMode ?? "normal",
      period: options.period ?? "today",
      expectedSnapshotIntervalMs: options.expectedSnapshotIntervalMs ?? null,
      visualLagMs: options.visualLagMs ?? 0,
      maxVisualLagMs: options.maxVisualLagMs ?? 1400,
      lastTargetAt: null,
    };
    this.seriesChannels.set(key, channel);
    return channel;
  }

  private applyOptions(channel: BaseChannel, options: InterpolationOptions): void {
    channel.epsilon = options.epsilon ?? channel.epsilon;
    channel.timeConstantMs = options.timeConstantMs ?? channel.timeConstantMs;
    channel.maxStepPerSecond = options.maxStepPerSecond ?? channel.maxStepPerSecond;
    channel.visualMode = options.visualMode ?? channel.visualMode;
    channel.period = options.period ?? channel.period;
    channel.expectedSnapshotIntervalMs = options.expectedSnapshotIntervalMs ?? channel.expectedSnapshotIntervalMs;
    channel.visualLagMs = options.visualLagMs ?? channel.visualLagMs;
    channel.maxVisualLagMs = options.maxVisualLagMs ?? channel.maxVisualLagMs;
    this.visualLagMaxMs = Math.max(this.visualLagMaxMs, channel.visualLagMs);
  }

  private withDefaults(options: InterpolationOptions): InterpolationOptions {
    return { ...this.defaultOptions, ...options };
  }

  private recordTarget(channel: BaseChannel, jump: number): void {
    const time = this.now();
    if (channel.lastTargetAt !== null) {
      this.targetIntervalSamples.push(time - channel.lastTargetAt);
      if (this.targetIntervalSamples.length > 120) this.targetIntervalSamples.shift();
    }
    channel.lastTargetAt = time;
    this.targetCount += 1;
    this.jumpSamples.push(jump);
    if (this.jumpSamples.length > 120) this.jumpSamples.shift();
  }

  private advanceScalar(channel: ScalarChannel, deltaMs: number): boolean {
    const next = advanceNumber(channel.display, channel.target, deltaMs, channel);
    const changed = next !== channel.display;
    channel.display = next;
    if (changed) this.notify(channel);
    return Math.abs(channel.target - channel.display) > channel.epsilon;
  }

  private advanceSeries(channel: SeriesChannel, deltaMs: number): boolean {
    let needsNextFrame = false;
    let changed = false;
    channel.display = alignSeriesDisplay(channel.display, channel.target);
    const nextDisplay = channel.target.map((target, index) => {
      const next = advanceNumber(channel.display[index] ?? 0, target, deltaMs, channel);
      if (next !== channel.display[index]) changed = true;
      if (Math.abs(target - next) > channel.epsilon) needsNextFrame = true;
      return next;
    });
    channel.display = nextDisplay;
    if (changed) this.notifySeries(channel);
    return needsNextFrame;
  }

  private isSeriesSettled(channel: SeriesChannel): boolean {
    if (channel.display.length !== channel.target.length) return false;
    return channel.target.every((target, index) => Math.abs(target - channel.display[index]) <= channel.epsilon);
  }

  private notify(channel: ScalarChannel): void {
    for (const subscriber of channel.subscribers) subscriber(channel.display);
  }

  private notifySeries(channel: SeriesChannel): void {
    const display = [...channel.display];
    for (const subscriber of channel.subscribers) subscriber(display);
  }

  private start(): void {
    if (this.frameHandle !== null) return;
    this.previousTime = null;
    this.frameHandle = this.schedule((time) => this.tick(time));
  }
}

function advanceNumber(current: number, target: number, deltaMs: number, channel: BaseChannel): number {
  const diff = target - current;
  if (Math.abs(diff) <= channel.epsilon) return target;
  const tau = adaptiveTimeConstant(Math.abs(diff), channel);
  const alpha = 1 - Math.exp(-deltaMs / tau);
  let step = diff * alpha;
  if (channel.maxStepPerSecond) {
    const maxStep = channel.maxStepPerSecond * (deltaMs / 1000);
    step = Math.sign(step) * Math.min(Math.abs(step), maxStep);
  }
  const next = current + step;
  return Math.abs(target - next) <= channel.epsilon ? target : next;
}

function adaptiveTimeConstant(distance: number, channel: BaseChannel): number {
  const fast = channel.visualMode === "fast_forward";
  const profileMin = fast ? 28 : 45;
  const profileMax = fast ? 260 : 1800;
  const cadenceFloor = channel.expectedSnapshotIntervalMs
    ? channel.expectedSnapshotIntervalMs * (fast ? 0.18 : 0.28)
    : 0;
  const base = Math.max(channel.timeConstantMs, cadenceFloor);
  const distanceBoost = 1 + Math.min(fast ? 4.2 : 3.2, Math.log10(distance + 1) / (fast ? 1.45 : 1.85));
  const lagBoost = channel.visualLagMs > channel.maxVisualLagMs
    ? 1 + Math.min(3.5, channel.visualLagMs / channel.maxVisualLagMs)
    : 1;
  return clamp((base * periodFactor(channel.period)) / (distanceBoost * lagBoost), profileMin, profileMax);
}

function seriesDistance(left: number[], right: number[]): number {
  const length = Math.max(left.length, right.length);
  let distance = 0;
  for (let index = 0; index < length; index += 1) {
    distance += Math.abs((left[index] ?? 0) - (right[index] ?? 0));
  }
  return distance;
}

function roundedAverage(values: number[]): number {
  if (!values.length) return 0;
  const total = values.reduce((sum, value) => sum + value, 0);
  return Math.round((total / values.length) * 100) / 100;
}

function roundedMax(values: number[]): number {
  if (!values.length) return 0;
  return Math.round(Math.max(...values) * 100) / 100;
}

export const visualInterpolationEngine = new VisualInterpolationEngine();

declare global {
  interface Window {
    __dashboardVisualMetrics?: () => Record<string, number>;
  }
}

if (typeof window !== "undefined") {
  window.__dashboardVisualMetrics = () => visualInterpolationEngine.metrics();
}
