import { Navigate, Route, Routes } from "react-router-dom";
import type { Permission } from "@/api/types";
import { RequireAuth, RequirePermission } from "@/auth/guards";
import { AppShell } from "@/components/AppShell";
import AdminPage from "@/pages/AdminPage";
import AiPage from "@/pages/AiPage";
import AnalyticsPage from "@/pages/AnalyticsPage";
import CasePage from "@/pages/CasePage";
import CasesPage from "@/pages/CasesPage";
import EmailDetailPage from "@/pages/EmailDetailPage";
import EmailLogPage from "@/pages/EmailLogPage";
import LoginPage from "@/pages/LoginPage";
import OverviewPage from "@/pages/OverviewPage";
import AttentionPage from "@/pages/AttentionPage";
import DataSourcesPage from "@/pages/DataSourcesPage";
import KnowledgeDocumentPage from "@/pages/KnowledgeDocumentPage";
import KnowledgePage from "@/pages/KnowledgePage";
import SettingsPage from "@/pages/SettingsPage";
import TestAreaPage from "@/pages/TestAreaPage";
import TopicPage from "@/pages/TopicPage";
import TopicsPage from "@/pages/TopicsPage";

function guard(permission: Permission, page: JSX.Element) {
  return <RequirePermission permission={permission}>{page}</RequirePermission>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route index element={guard("overview:view", <OverviewPage />)} />
        <Route path="attention" element={guard("queue:view", <AttentionPage />)} />
        <Route path="queue" element={<Navigate to="/attention" replace />} />
        <Route path="data-sources" element={guard("datasources:manage", <DataSourcesPage />)} />
        <Route path="emails" element={guard("emails:view", <EmailLogPage />)} />
        <Route path="emails/:id" element={guard("emails:view", <EmailDetailPage />)} />
        <Route path="knowledge" element={guard("knowledge:view", <KnowledgePage />)} />
        <Route path="knowledge/:id" element={guard("knowledge:view", <KnowledgeDocumentPage />)} />
        <Route path="topics" element={guard("topics:view", <TopicsPage />)} />
        <Route path="topics/new" element={guard("topics:edit", <TopicPage />)} />
        <Route path="topics/:id" element={guard("topics:view", <TopicPage />)} />
        <Route path="test" element={guard("test:use", <TestAreaPage />)} />
        <Route path="cases" element={guard("cases:view", <CasesPage />)} />
        <Route path="cases/:id" element={guard("cases:view", <CasePage />)} />
        <Route path="ai" element={guard("ai:view", <AiPage />)} />
        <Route path="analytics" element={guard("analytics:view", <AnalyticsPage />)} />
        <Route path="settings" element={guard("settings:view", <SettingsPage />)} />
        <Route path="admin" element={guard("users:view", <AdminPage />)} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
