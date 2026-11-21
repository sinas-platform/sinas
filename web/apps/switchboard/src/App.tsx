import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Activity, KeyRound, LayoutGrid } from 'lucide-react';
import { ApiErrorToasts, AuthProvider, Login, RequireAuth, Shell, ToastProvider, type NavItem } from '@sinas/ui';
import { Switchboards } from './pages/Switchboards';
import { SwitchboardDetail } from './pages/SwitchboardDetail';
import { Audit } from './pages/Audit';
import { Clients } from './pages/Clients';
import { consoleUrl } from './lib/links';

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 15_000 } },
});

const nav: NavItem[] = [
  { name: 'Switchboards', href: '/switchboards', icon: LayoutGrid },
  { name: 'Audit log', href: '/audit', icon: Activity },
  { name: 'Clients', href: '/clients', icon: KeyRound },
];

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <ApiErrorToasts />
        <AuthProvider>
          <BrowserRouter basename={import.meta.env.BASE_URL.replace(/\/$/, '')}>
            <Routes>
              <Route
                path="/login"
                element={<Login product="Switchboard" tagline="Publish tools to outside agents" ssoHref={consoleUrl('/login')} />}
              />
              <Route
                element={
                  <RequireAuth>
                    <Shell product="Switchboard" nav={nav} footerLinks={[{ name: 'Open Sinas console', href: consoleUrl('/') }]} />
                  </RequireAuth>
                }
              >
                <Route index element={<Navigate to="/switchboards" replace />} />
                <Route path="switchboards" element={<Switchboards />} />
                <Route path="switchboards/:namespace/:name" element={<SwitchboardDetail />} />
                <Route path="audit" element={<Audit />} />
                <Route path="clients" element={<Clients />} />
                <Route path="*" element={<Navigate to="/switchboards" replace />} />
              </Route>
            </Routes>
          </BrowserRouter>
        </AuthProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}
