import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/react";

// Pages are lazy-loaded; the first import compiles the chart library, which can take a few
// seconds on a slow machine. Give findBy* queries time for that one-off cost.
configure({ asyncUtilTimeout: 15000 });

// Recharts' ResponsiveContainer measures its parent; jsdom has no layout engine.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;
