import { lazy, Suspense } from "react";
import { BrowserRouter, Link, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { LensProvider } from "./lens/LensContext";
import { RequireLens } from "./lens/components/RequireLens";
import {
  AcceptInvitePage,
  ForgotPasswordPage,
  LoginPage,
  ResetPasswordPage,
  SignupPage,
  VerifyEmailPage,
} from "./lens/pages/AuthPages";

// Route-based code splitting: each page (and the chart library) loads only when first visited.
const OverviewPage = lazy(() => import("./pages/OverviewPage").then((m) => ({ default: m.OverviewPage })));
const AnalyticsPage = lazy(() => import("./pages/AnalyticsPage").then((m) => ({ default: m.AnalyticsPage })));
const SourcesPage = lazy(() => import("./pages/SourcesPage").then((m) => ({ default: m.SourcesPage })));
const PipelinesPage = lazy(() => import("./pages/PipelinesPage").then((m) => ({ default: m.PipelinesPage })));
const PipelineDetailPage = lazy(() => import("./pages/PipelineDetailPage").then((m) => ({ default: m.PipelineDetailPage })));
const DataQualityPage = lazy(() => import("./pages/DataQualityPage").then((m) => ({ default: m.DataQualityPage })));
const MonitoringPage = lazy(() => import("./pages/MonitoringPage").then((m) => ({ default: m.MonitoringPage })));
const CostsPage = lazy(() => import("./pages/CostsPage").then((m) => ({ default: m.CostsPage })));
const SettingsPage = lazy(() => import("./pages/SettingsPage").then((m) => ({ default: m.SettingsPage })));
const FleetPage = lazy(() => import("./lens/pages/FleetPage").then((m) => ({ default: m.FleetPage })));
const AddConnectionPage = lazy(() =>
  import("./lens/pages/AddConnectionPage").then((m) => ({ default: m.AddConnectionPage })),
);
const ConnectionPage = lazy(() => import("./lens/pages/ConnectionPage").then((m) => ({ default: m.ConnectionPage })));

const loading = (
  <div role="status" aria-live="polite" className="state">
    Loading…
  </div>
);

export function AppRoutes() {
  return (
    <LensProvider>
    <Suspense fallback={loading}>
    <Routes>
      {/* Lens account screens are full-page, outside the dashboard shell. */}
      <Route path="login" element={<LoginPage />} />
      <Route path="signup" element={<SignupPage />} />
      <Route path="forgot-password" element={<ForgotPasswordPage />} />
      <Route path="reset-password" element={<ResetPasswordPage />} />
      <Route path="verify-email" element={<VerifyEmailPage />} />
      <Route path="invite" element={<AcceptInvitePage />} />
      <Route element={<Layout />}>
        <Route path="lens" element={<RequireLens><FleetPage /></RequireLens>} />
        <Route path="lens/connections/new" element={<RequireLens><AddConnectionPage /></RequireLens>} />
        <Route path="lens/connections/:id" element={<RequireLens><ConnectionPage /></RequireLens>} />
        <Route index element={<OverviewPage />} />
        <Route path="analytics" element={<AnalyticsPage />} />
        <Route path="sources" element={<SourcesPage />} />
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
    </LensProvider>
  );
}

export default function App() {
  return (
    // The dashboard lives under /app; the landing page owns the site root.
    <BrowserRouter basename="/app">
      <AppRoutes />
    </BrowserRouter>
  );
}
