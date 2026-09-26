import { memo, useEffect, useMemo, useState } from "react";

import { visualInterpolationEngine, type InterpolationOptions } from "../lib/visualInterpolation";

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function optionSignature(options: InterpolationOptions | undefined): string {
  return JSON.stringify({
    epsilon: options?.epsilon,
    timeConstantMs: options?.timeConstantMs,
    maxStepPerSecond: options?.maxStepPerSecond,
    visualMode: options?.visualMode,
    period: options?.period,
    expectedSnapshotIntervalMs: options?.expectedSnapshotIntervalMs,
    visualLagMs: options?.visualLagMs,
    maxVisualLagMs: options?.maxVisualLagMs,
  });
}

interface AnimatedNumberTextProps {
  id: string;
  value: number;
  format: (value: number) => string;
  resetKey?: string | null;
  options?: InterpolationOptions;
}

function AnimatedNumberTextBase({ id, value, format, resetKey, options }: AnimatedNumberTextProps) {
  const channelKey = `${resetKey ?? "default"}:${id}`;
  const reducedMotion = prefersReducedMotion();
  const [displayValue, setDisplayValue] = useState(value);
  const signature = optionSignature(options);
  const stableOptions = useMemo(
    () => ({ ...options, reducedMotion }),
    [reducedMotion, signature],
  );

  useEffect(() => {
    const unsubscribe = visualInterpolationEngine.subscribe(channelKey, setDisplayValue);
    visualInterpolationEngine.setTarget(channelKey, value, { ...stableOptions, reset: true });
    return unsubscribe;
  }, [channelKey]);

  useEffect(() => {
    visualInterpolationEngine.setTarget(channelKey, value, stableOptions);
  }, [channelKey, stableOptions, value]);

  return <>{format(displayValue)}</>;
}

export const AnimatedNumberText = memo(AnimatedNumberTextBase);
