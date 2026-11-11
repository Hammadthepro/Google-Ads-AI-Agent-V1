import { NewClient, EditClient } from "./pages/ClientForm";
import { Agent } from "./pages/Agent";
import { AuditReportPage } from "./pages/AuditReportPage";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import Layout from "./components/Layout";
import { Spinner } from "./components/ui";
import { AuthProvider, useAuth } from "./lib/auth";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import { ClientDetail, Clients } from "./pages/Clients";
import { BusinessDetail, Businesses } from "./pages/Businesses";
import Accounts from "./pages/Accounts";
import { TaskDetail, Tasks } from "./pages/Tasks";
import { AuditDetail, Audits } from "./pages/Audits";
import { PlanDetail, Planner } from "./pages/Planner";
import { ApprovalDetail, Approvals } from "./pages/Approvals";
import Monitor from "./pages/Monitor";
import Weekly from "./pages/Weekly";
import Experiments from "./pages/Experiments";
import Learning from "./pages/Learning";
import Users from "./pages/Users";
import Integrations from "./pages/Integrations";
import Settings from "./pages/Settings";
import Activity from "./pages/Activity";

function AgentRedirect() {
  const loc = useLocation();
  return <Navigate to={{ pathname: "/", search: loc.search }} replace />;
}

function Protected({ children }: { children: JSX.Element }) {
  const { user, loading } = useAuth();
  const loc = useLocation();
  if (loading) return <div className="p-10"><Spinner /></div>;
  if (!user) return <Navigate to="/login" state={{ from: loc.pathname }} replace />;
  return children;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<Protected><Layout /></Protected>}>
          <Route index element={<Agent />} />
          <Route path="agent" element={<AgentRedirect />} />
          <Route path="overview" element={<Dashboard />} />
          <Route path="clients" element={<Clients />} />
          <Route path="clients/new" element={<NewClient />} />
          <Route path="clients/:id" element={<ClientDetail />} />
          <Route path="clients/:id/edit" element={<EditClient />} />
          <Route path="audit-reports/:id" element={<AuditReportPage />} />
          <Route path="businesses" element={<Businesses />} />
          <Route path="businesses/:id" element={<BusinessDetail />} />
          <Route path="accounts" element={<Accounts />} />
          <Route path="tasks" element={<Tasks />} />
          <Route path="tasks/:id" element={<TaskDetail />} />
          <Route path="audits" element={<Audits />} />
          <Route path="audits/:id" element={<AuditDetail />} />
          <Route path="planner" element={<Planner />} />
          <Route path="planner/:id" element={<PlanDetail />} />
          <Route path="approvals" element={<Approvals />} />
          <Route path="approvals/:id" element={<ApprovalDetail />} />
          <Route path="monitor" element={<Monitor />} />
          <Route path="weekly" element={<Weekly />} />
          <Route path="experiments" element={<Experiments />} />
          <Route path="learning" element={<Learning />} />
          <Route path="users" element={<Users />} />
          <Route path="integrations" element={<Integrations />} />
          <Route path="settings" element={<Settings />} />
          <Route path="activity" element={<Activity />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </AuthProvider>
  );
}
