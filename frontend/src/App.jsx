import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./App.css";
import { AppProvider } from "./context/AppContext";
import ProtectedRoute from "./components/ProtectedRoute";
import Layout from "./components/Layout";
import AuthPage from "./pages/AuthPage";
import DashboardPage from "./pages/DashboardPage";
import PreferencesPage from "./pages/PreferencesPage";
import ResumePage from "./pages/ResumePage";
import JobSearchPage from "./pages/JobSearchPage";
import SelectedJobsPage from "./pages/SelectedJobsPage";
import AtsAnalyzerPage from "./pages/AtsAnalyzerPage";
import EmailSummaryPage from "./pages/EmailSummaryPage";
import WorkflowPage from "./pages/WorkflowPage";

export default function App() {
  return (
    <BrowserRouter>
      <AppProvider>
        <Routes>
          <Route path="/login" element={<AuthPage mode="login" />} />
          <Route path="/signup" element={<AuthPage mode="signup" />} />
          <Route path="/forgot-password" element={<AuthPage mode="forgot" />} />
          <Route path="/reset-password" element={<AuthPage mode="reset" />} />
          <Route element={<ProtectedRoute />}>
            <Route element={<Layout />}>
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/preferences" element={<PreferencesPage />} />
              <Route path="/resume" element={<ResumePage />} />
              <Route path="/jobs" element={<JobSearchPage />} />
              <Route path="/selected-jobs" element={<SelectedJobsPage />} />
              <Route path="/ats-analyzer" element={<AtsAnalyzerPage />} />
              <Route path="/optimizer/:jobId" element={<AtsAnalyzerPage />} />
              {/* Backward-compatible redirects from old Optimizer / Match Score routes */}
              <Route path="/optimizer" element={<Navigate to="/ats-analyzer" replace />} />
              <Route path="/matches" element={<Navigate to="/ats-analyzer" replace />} />
              <Route path="/emails" element={<EmailSummaryPage />} />
              <Route path="/workflow" element={<WorkflowPage />} />
            </Route>
          </Route>
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </AppProvider>
    </BrowserRouter>
  );
}
