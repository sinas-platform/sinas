import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter, Link, Navigate, Outlet, Route, Routes, useNavigate } from 'react-router-dom';
import { LogOut } from 'lucide-react';
import { clearConnection, getConnection } from './lib/connection';
import { Connect } from './screens/Connect';
import { Projects } from './screens/Projects';
import { ProjectHome } from './screens/ProjectHome';
import { AssistantEditor } from './screens/AssistantEditor';
import { NewWorkflow, ScheduleWorkflow, WebhookWorkflow } from './screens/WorkflowEditor';

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
});

function Shell() {
  const navigate = useNavigate();
  const conn = getConnection();
  if (!conn) return <Navigate to="/connect" replace />;

  const host = new URL(conn.baseUrl).host;

  return (
    <div className="app-shell">
      <header className="topbar">
        <Link to="/projects" className="wordmark">
          <span className="wm-name">sinas</span>
          <span className="wm-app">Studio</span>
        </Link>
        <span style={{ fontSize: 12, color: 'var(--faint)' }}>{host}</span>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 12.5, color: 'var(--muted)' }}>{conn.user.email}</span>
          <button
            className="kebab"
            title="Disconnect"
            onClick={() => {
              clearConnection();
              navigate('/connect');
            }}
          >
            <LogOut size={16} />
          </button>
        </div>
      </header>
      <div className="main-scroll" style={{ display: 'flex', flexDirection: 'column' }}>
        <Outlet />
      </div>
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename="/studio">
        <Routes>
          <Route path="/connect" element={<Connect />} />
          <Route element={<Shell />}>
            <Route index element={<Navigate to="/projects" replace />} />
            <Route path="projects" element={<Projects />} />
            <Route path="projects/:ns/:name" element={<ProjectHome />} />
            <Route path="projects/:pns/:pname/assistants/:ans/:aname" element={<AssistantEditor />} />
            <Route path="projects/:pns/:pname/workflows/new" element={<NewWorkflow />} />
            <Route path="projects/:pns/:pname/workflows/schedule/:name" element={<ScheduleWorkflow />} />
            <Route path="projects/:pns/:pname/workflows/webhook/*" element={<WebhookWorkflow />} />
          </Route>
          <Route path="*" element={<Navigate to="/projects" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
