import { lazy, Suspense } from "react";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";

// Route-based code splitting: each page (and the chart library) loads only when first visited.
const OverviewPage = lazy(() => import("./pages/OverviewPage").then((m) => ({ default: m.OverviewPage })));
const AnalyticsPage = lazy(() => import("./pages/AnalyticsPage").then((m) => ({ default: m.AnalyticsPage })));
const PipelinesPage = lazy(() => import("./pages/PipelinesPage").then((m) => ({ default: m.PipelinesPage })));
const PipelineDetailPage = lazy(() => import("./pages/PipelineDetailPage").then((m) => ({ default: m.PipelineDetailPage })));
const DataQualityPage = lazy(() => import("./pages/DataQualityPage").then((m) => ({ default: m.DataQualityPage })));
const MonitoringPage = lazy(() => import("./pages/MonitoringPage").then((m) => ({ default: m.MonitoringPage })));
const CostsPage = lazy(() => import("./pages/CostsPage").then((m) => ({ default: m.CostsPage })));
const SettingsPage = lazy(() => import("./pages/SettingsPage").then((m) => ({ default: m.SettingsPage })));

const loading = (
  <div role="status" aria-live="polite" className="state">
    Loading…
  </div>
);

export function AppRoutes() {
  return (
    <Suspense fallback={loading}>
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<OverviewPage />} />
        <Route path="analytics" element={<AnalyticsPage />} />
        <Route path="pipelines" element={<PipelinesPage />} />
        <Route path="pipelines/:name" element={<PipelineDetailPage />} />
        <Route path="data-quality" element={<DataQualityPage />} />
        <Route path="monitoring" element={<MonitoringPage />} />
        <Route path="costs" element={<CostsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route
          path="*"
          element={
            <div className="state">
              <h1>Page not found</h1>
              <Link to="/">Back to the overview</Link>
            </div>
          }
        />
      </Route>
    </Routes>
    </Suspense>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppRoutes />
    </BrowserRouter>
  );
}
