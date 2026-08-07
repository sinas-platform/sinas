import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../lib/auth-context';
import { apiClient } from '../lib/api';
import {
  LayoutDashboard,
  MessageSquare,
  Bot,
  Server,
  Users,
  Key,
  LogOut,
  Menu,
  X,
  Code,
  Clock,
  Brain,
  Settings,
  Activity,
  Zap,
  Webhook,
  FileText,
  Lightbulb,
  Layers,
  Archive,
  AppWindow,
  Cable,
  SearchCode,
  Package,
  HardDrive,
  KeyRound,
  Plug,
  Sparkles,
  ExternalLink,
} from 'lucide-react';
import { useState } from 'react';

const navigationSections = [
  {
    name: '',
    items: [
      { name: 'Dashboard', href: '/', icon: LayoutDashboard },
    ],
  },
  {
    name: 'BUILD',
    items: [
      { name: 'Agents', href: '/agents', icon: Bot },
      { name: 'Functions', href: '/functions', icon: Code },
      { name: 'Components', href: '/components', icon: Layers },
      { name: 'Queries', href: '/queries', icon: SearchCode },
    ],
  },
  {
    name: 'CONFIGURE',
    items: [
      { name: 'Skills', href: '/skills', icon: Lightbulb },
      { name: 'LLM Providers', href: '/llm-providers', icon: Brain },
      { name: 'Databases', href: '/database-connections', icon: Cable },
      { name: 'Templates', href: '/templates', icon: FileText },
      { name: 'Connectors', href: '/connectors', icon: Plug },
      { name: 'Secrets', href: '/secrets', icon: KeyRound },
    ],
  },
  {
    name: 'TRIGGERS',
    items: [
      { name: 'Webhooks', href: '/webhooks', icon: Webhook },
      { name: 'Schedules', href: '/schedules', icon: Clock },
      { name: 'Database (CDC)', href: '/database-triggers', icon: Zap },
    ],
  },
  {
    name: 'STORAGE',
    items: [
      { name: 'Collections', href: '/collections', icon: Archive },
      { name: 'State Stores', href: '/stores', icon: HardDrive },
    ],
  },
  {
    name: 'ADMIN',
    items: [
      { name: 'Packages', href: '/packages', icon: Package },
      { name: 'Manifests', href: '/manifests', icon: AppWindow },
      { name: 'Access Control', href: '/users', icon: Users },
      { name: 'API Keys', href: '/api-keys', icon: Key },
      { name: 'Logs', href: '/logs', icon: Activity },
      { name: 'System', href: '/system', icon: Server },
      { name: 'Config Manager', href: '/config', icon: Settings },
    ],
  },
];

export function Layout() {
  const location = useLocation();
  const navigate = useNavigate();
  const { user, logout, authMode } = useAuth();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [showChangePassword, setShowChangePassword] = useState(false);
  const passwordEnabled = authMode === 'password' || authMode === 'password+otp';

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  return (
    <div className="min-h-screen bg-[#090909]">
      {/* Mobile sidebar overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/60 z-40 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`fixed inset-y-0 left-0 z-50 w-64 bg-[#111111] border-r border-white/[0.06] transform transition-transform duration-300 ease-in-out lg:translate-x-0 ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex flex-col h-full">
          {/* Logo */}
          <div className="flex items-center justify-between h-16 px-6 border-b border-white/[0.06]">
            <img src="/sinas-logo.svg" alt="sinas" className="h-8" />
            <button
              onClick={() => setSidebarOpen(false)}
              className="lg:hidden text-gray-400 hover:text-gray-200"
            >
              <X className="w-6 h-6" />
            </button>
          </div>

          {/* Navigation */}
          <nav className="flex-1 px-4 py-4 overflow-y-auto">
            {navigationSections.map((section, sectionIdx) => (
              <div key={section.name || 'home'} className={sectionIdx > 0 ? 'mt-6' : ''}>
                {section.name && (
                  <h3 className="px-3 mb-2 text-xs font-semibold text-gray-500 uppercase tracking-wider">
                    {section.name}
                  </h3>
                )}
                <div className="space-y-1">
                  {section.items.map((item) => {
                    const isActive = location.pathname === item.href ||
                      (item.href !== '/' && location.pathname.startsWith(item.href));

                    return (
                      <Link
                        key={item.name}
                        to={item.href}
                        onClick={() => setSidebarOpen(false)}
                        className={`flex items-center px-3 py-2 text-sm font-medium rounded-md transition-colors ${
                          isActive
                            ? 'bg-primary-900/30 text-primary-400'
                            : 'text-gray-400 hover:bg-white/5 hover:text-gray-200'
                        }`}
                      >
                        <item.icon className="w-5 h-5 mr-3" />
                        {item.name}
                      </Link>
                    );
                  })}
                </div>
              </div>
            ))}
          </nav>

          {/* Chat History + Studio links */}
          <div className="px-4 pb-2 space-y-1">
            <Link
              to="/chats"
              onClick={() => setSidebarOpen(false)}
              className="flex items-center px-3 py-1.5 text-xs font-medium text-gray-500 hover:text-gray-300 hover:bg-white/5 rounded-md transition-colors"
            >
              <MessageSquare className="w-4 h-4 mr-2" />
              Chat History
            </Link>
            {/* Studio is bundled with the console and served same-origin at /studio/ */}
            <a
              href="/studio/"
              target="_blank"
              rel="noopener"
              className="flex items-center px-3 py-1.5 text-xs font-medium text-gray-500 hover:text-gray-300 hover:bg-white/5 rounded-md transition-colors"
            >
              <Sparkles className="w-4 h-4 mr-2" />
              Open Studio
              <ExternalLink className="w-3 h-3 ml-auto" />
            </a>
          </div>

          {/* User menu */}
          <div className="p-4 border-t border-white/[0.06]">
            <div className="flex items-center">
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-gray-100 truncate">
                  {user?.email}
                </p>
                <p className="text-xs text-gray-500">Administrator</p>
              </div>
              {passwordEnabled && (
                <button
                  onClick={() => setShowChangePassword(true)}
                  className="ml-1 p-2 text-gray-400 hover:text-gray-200 hover:bg-white/5 rounded-md"
                  title="Change password"
                >
                  <KeyRound className="w-5 h-5" />
                </button>
              )}
              <button
                onClick={handleLogout}
                className="ml-1 p-2 text-gray-400 hover:text-gray-200 hover:bg-white/5 rounded-md"
                title="Logout"
              >
                <LogOut className="w-5 h-5" />
              </button>
            </div>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <div className="lg:pl-64">
        {/* Top bar */}
        <header className="sticky top-0 z-30 flex items-center justify-between h-16 px-6 bg-[#111111] border-b border-white/[0.06]">
          <button
            onClick={() => setSidebarOpen(true)}
            className="lg:hidden text-gray-400 hover:text-gray-200"
          >
            <Menu className="w-6 h-6" />
          </button>
          <div className="flex-1 lg:hidden"></div>
          <div className="text-sm text-gray-500">
            {new Date().toLocaleDateString('en-US', {
              weekday: 'long',
              year: 'numeric',
              month: 'long',
              day: 'numeric',
            })}
          </div>
        </header>

        {/* Page content */}
        <main className="p-6">
          <Outlet />
        </main>
      </div>

      {showChangePassword && (
        <ChangePasswordModal onClose={() => setShowChangePassword(false)} />
      )}
    </div>
  );
}

function ChangePasswordModal({ onClose }: { onClose: () => void }) {
  const { logout } = useAuth();
  const navigate = useNavigate();
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    if (newPassword.length < 8) {
      setError('New password must be at least 8 characters.');
      return;
    }
    if (newPassword !== confirmPassword) {
      setError('Passwords do not match.');
      return;
    }
    setSubmitting(true);
    try {
      await apiClient.changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      });
      // Backend revokes all refresh tokens on password change — log out so the
      // user re-authenticates with the new password.
      await logout();
      navigate('/login');
    } catch (err: any) {
      setError(err.response?.data?.detail || err.message || 'Failed to change password');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="bg-[#161616] rounded-2xl p-6 border border-white/[0.06] w-full max-w-md">
        <div className="flex items-start justify-between mb-4">
          <h3 className="text-lg font-semibold text-gray-100">Change password</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-200">
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">Current password</label>
            <input
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              autoComplete="current-password"
              required
              className="w-full px-3 py-2 bg-[#111111] border border-white/10 rounded-lg text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">New password</label>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
              required
              minLength={8}
              className="w-full px-3 py-2 bg-[#111111] border border-white/10 rounded-lg text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">Confirm new password</label>
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
              required
              minLength={8}
              className="w-full px-3 py-2 bg-[#111111] border border-white/10 rounded-lg text-gray-100 focus:outline-none focus:ring-2 focus:ring-primary-500"
            />
          </div>

          {error && (
            <div className="p-3 bg-red-900/20 border border-red-800/30 rounded-lg text-sm text-red-400">
              {error}
            </div>
          )}

          <p className="text-xs text-gray-500">
            You'll be signed out on success and need to sign in again with the new password.
          </p>

          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose} className="btn btn-secondary" disabled={submitting}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={submitting}>
              {submitting ? 'Updating...' : 'Update password'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
