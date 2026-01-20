import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider, useAuth } from './lib/auth-context';
import { ToastProvider } from './lib/toast-context';
import { APIErrorHandler } from './components/APIErrorHandler';
import { Layout } from './components/Layout';
import { Login } from './pages/Login';
import { Dashboard } from './pages/Dashboard';
import { Chats } from './pages/Chats';
import { ChatDetail } from './pages/ChatDetail';
import { Assistants } from './pages/Assistants';
import { AssistantDetail } from './pages/AssistantDetail';
import { MCPServers } from './pages/MCPServers';
import { UsersNew as Users } from './pages/UsersNew';
import { APIKeys } from './pages/APIKeys';
import { Functions } from './pages/Functions';
import { FunctionEditor } from './pages/FunctionEditor';
import { Webhooks } from './pages/Webhooks';
import { WebhookEditor } from './pages/WebhookEditor';
import { Schedules } from './pages/Schedules';
import { ScheduleEditor } from './pages/ScheduleEditor';
import { RequestLogs } from './pages/RequestLogs';
import { ContextStore } from './pages/ContextStore';
import { LLMProviders } from './pages/LLMProviders';
import { ConfigManager } from './pages/ConfigManager';
import { Permissions } from './pages/Permissions';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

function PrivateRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-primary-600"></div>
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}

function PublicRoute({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-primary-600"></div>
      </div>
    );
  }

  if (user) {
    return <Navigate to="/" replace />;
  }

  return <>{children}</>;
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <APIErrorHandler>
          <AuthProvider>
            <BrowserRouter>
          <Routes>
            <Route
              path="/login"
              element={
                <PublicRoute>
                  <Login />
                </PublicRoute>
              }
            />
            <Route
              path="/"
              element={
                <PrivateRoute>
                  <Layout />
                </PrivateRoute>
              }
            >
              <Route index element={<Dashboard />} />
              <Route path="chats" element={<Chats />} />
              <Route path="chats/:chatId" element={<ChatDetail />} />
              <Route path="agents" element={<Assistants />} />
              <Route path="agents/:namespace/:name" element={<AssistantDetail />} />
              <Route path="functions" element={<Functions />} />
              <Route path="functions/:namespace/:name" element={<FunctionEditor />} />
              <Route path="webhooks" element={<Webhooks />} />
              <Route path="webhooks/*" element={<WebhookEditor />} />
              <Route path="schedules" element={<Schedules />} />
              <Route path="schedules/:scheduleId" element={<ScheduleEditor />} />
              <Route path="mcp" element={<MCPServers />} />
              <Route path="llm-providers" element={<LLMProviders />} />
              <Route path="config" element={<ConfigManager />} />
              <Route path="states" element={<ContextStore />} />
              <Route path="logs" element={<RequestLogs />} />
              <Route path="users" element={<Users />} />
              <Route path="permissions" element={<Permissions />} />
              <Route path="api-keys" element={<APIKeys />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </AuthProvider>
    </APIErrorHandler>
    </ToastProvider>
    </QueryClientProvider>
  );
}

export default App;
