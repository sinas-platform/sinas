import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../lib/auth-context';
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
  Database,
  Brain,
  Settings,
  Activity,
  Webhook,
  FileText,
  Lightbulb,
  Archive,
  AppWindow,
  Cable,
  SearchCode,
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
    name: 'AGENTS',
    items: [
      { name: 'Agents', href: '/agents', icon: Bot },
      { name: 'Skills', href: '/skills', icon: Lightbulb },
      { name: 'LLM Providers', href: '/llm-providers', icon: Brain },
    ],
  },
  {
    name: 'FUNCTIONS',
    items: [
      { name: 'Functions', href: '/functions', icon: Code },
      { name: 'Webhooks', href: '/webhooks', icon: Webhook },
      { name: 'Schedules', href: '/schedules', icon: Clock },
    ],
  },
  {
    name: 'DATA',
    items: [
      { name: 'Database Connections', href: '/database-connections', icon: Cable },
      { name: 'Queries', href: '/queries', icon: SearchCode },
    ],
  },
  {
    name: 'RESOURCES',
    items: [
      { name: 'Templates', href: '/templates', icon: FileText },
      { name: 'Collections', href: '/collections', icon: Archive },
      { name: 'States', href: '/states', icon: Database },
    ],
  },
  {
    name: 'ADMIN',
    items: [
      { name: 'Apps', href: '/apps', icon: AppWindow },
      { name: 'Users & Roles', href: '/users', icon: Users },
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
  const { user, logout } = useAuth();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const handleLogout = async () => {
    await logout();
    navigate('/login');
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Mobile sidebar overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-gray-600 bg-opacity-75 z-40 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`fixed inset-y-0 left-0 z-50 w-64 bg-white border-r border-gray-200 transform transition-transform duration-300 ease-in-out lg:translate-x-0 ${
          sidebarOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex flex-col h-full">
          {/* Logo */}
          <div className="flex items-center justify-between h-16 px-6 border-b border-gray-200">
            <img src="/sinas-logo.svg" alt="sinas" className="h-8" />
            <button
              onClick={() => setSidebarOpen(false)}
              className="lg:hidden text-gray-500 hover:text-gray-700"
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
                            ? 'bg-primary-50 text-primary-700'
                            : 'text-gray-700 hover:bg-gray-100 hover:text-gray-900'
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

          {/* Chat History link */}
          <div className="px-4 pb-2">
            <Link
              to="/chats"
              onClick={() => setSidebarOpen(false)}
              className="flex items-center px-3 py-1.5 text-xs font-medium text-gray-500 hover:text-gray-700 hover:bg-gray-50 rounded-md transition-colors"
            >
              <MessageSquare className="w-4 h-4 mr-2" />
              Chat History
            </Link>
          </div>

          {/* User menu */}
          <div className="p-4 border-t border-gray-200">
            <div className="flex items-center">
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-gray-900 truncate">
                  {user?.email}
                </p>
                <p className="text-xs text-gray-500">Administrator</p>
              </div>
              <button
                onClick={handleLogout}
                className="ml-3 p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-md"
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
        <header className="sticky top-0 z-30 flex items-center justify-between h-16 px-6 bg-white border-b border-gray-200">
          <button
            onClick={() => setSidebarOpen(true)}
            className="lg:hidden text-gray-500 hover:text-gray-700"
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
    </div>
  );
}
