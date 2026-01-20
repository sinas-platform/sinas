import { useQuery } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { useAuth } from '../lib/auth-context';
import { MessageSquare, Bot, Server, Code, Clock, Database } from 'lucide-react';
import { Link } from 'react-router-dom';

export function Dashboard() {
  const { user } = useAuth();

  const { data: chats } = useQuery({
    queryKey: ['chats'],
    queryFn: () => apiClient.listChats(),
    enabled: !!user,
    retry: false,
  });

  const { data: agents } = useQuery({
    queryKey: ['assistants'],
    queryFn: () => apiClient.listAssistants(),
    enabled: !!user,
    retry: false,
  });

  const { data: functions } = useQuery({
    queryKey: ['functions'],
    queryFn: () => apiClient.listFunctions(),
    enabled: !!user,
    retry: false,
  });

  const { data: schedules } = useQuery({
    queryKey: ['schedules'],
    queryFn: () => apiClient.listSchedules(),
    enabled: !!user,
    retry: false,
  });

  const { data: mcpServers } = useQuery({
    queryKey: ['mcpServers'],
    queryFn: () => apiClient.listMCPServers(),
    enabled: !!user,
    retry: false,
  });

  const { data: states } = useQuery({
    queryKey: ['states'],
    queryFn: () => apiClient.listStates(),
    enabled: !!user,
    retry: false,
  });

  const statsSections = [
    {
      title: 'CONFIGURE',
      stats: [
        { name: 'Agents', value: agents?.length || 0, icon: Bot, href: '/agents', color: 'purple' },
        { name: 'Functions', value: functions?.length || 0, icon: Code, href: '/functions', color: 'green' },
        { name: 'MCP Servers', value: mcpServers?.filter((s) => s.is_active).length || 0, icon: Server, href: '/mcp', color: 'cyan' },
        { name: 'Active Schedules', value: schedules?.filter((s) => s.is_active).length || 0, icon: Clock, href: '/schedules', color: 'yellow' },
      ],
    },
    {
      title: 'TEST & MONITOR',
      stats: [
        { name: 'Chats', value: chats?.length || 0, icon: MessageSquare, href: '/chats', color: 'blue' },
        { name: 'States', value: states?.length || 0, icon: Database, href: '/states', color: 'indigo' },
      ],
    },
  ];

  const recentChats = chats?.slice(0, 5) || [];
  const activeAgents = agents?.filter((a) => a.is_active).slice(0, 5) || [];

  const getColorClasses = (color: string) => {
    const colors: Record<string, { bg: string; text: string; icon: string }> = {
      blue: { bg: 'bg-blue-50', text: 'text-blue-700', icon: 'text-blue-600' },
      purple: { bg: 'bg-purple-50', text: 'text-purple-700', icon: 'text-purple-600' },
      green: { bg: 'bg-green-50', text: 'text-green-700', icon: 'text-green-600' },
      orange: { bg: 'bg-orange-50', text: 'text-orange-700', icon: 'text-orange-600' },
      indigo: { bg: 'bg-indigo-50', text: 'text-indigo-700', icon: 'text-indigo-600' },
      pink: { bg: 'bg-pink-50', text: 'text-pink-700', icon: 'text-pink-600' },
      yellow: { bg: 'bg-yellow-50', text: 'text-yellow-700', icon: 'text-yellow-600' },
      cyan: { bg: 'bg-cyan-50', text: 'text-cyan-700', icon: 'text-cyan-600' },
    };
    return colors[color] || colors.blue;
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Dashboard</h1>
        <p className="text-gray-600 mt-1">Management console for SINAS AI platform</p>
      </div>

      {/* Stats Sections */}
      {statsSections.map((section) => (
        <div key={section.title}>
          <h2 className="text-lg font-semibold text-gray-900 mb-3">{section.title}</h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {section.stats.map((stat) => {
              const Icon = stat.icon;
              const colors = getColorClasses(stat.color);
              return (
                <Link
                  key={stat.name}
                  to={stat.href}
                  className="card hover:shadow-md transition-shadow cursor-pointer"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-600 truncate">{stat.name}</p>
                      <p className="text-3xl font-bold text-gray-900 mt-2">{stat.value}</p>
                    </div>
                    <div className={`p-3 ${colors.bg} rounded-lg flex-shrink-0`}>
                      <Icon className={`w-6 h-6 ${colors.icon}`} />
                    </div>
                  </div>
                </Link>
              );
            })}
          </div>
        </div>
      ))}

      {/* Recent Activity */}
      <div>
        <h2 className="text-lg font-semibold text-gray-900 mb-3">Recent Activity</h2>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Recent Chats */}
          <div className="card">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-medium text-gray-900">Recent Chats</h3>
              <Link to="/chats" className="text-sm text-primary-600 hover:text-primary-700">
                View all
              </Link>
            </div>
            <div className="space-y-2">
              {recentChats.length === 0 ? (
                <p className="text-gray-500 text-sm">No chats yet</p>
              ) : (
                recentChats.map((chat) => (
                  <Link
                    key={chat.id}
                    to={`/chats/${chat.id}`}
                    className="flex items-center gap-3 p-2 hover:bg-gray-50 rounded-lg transition-colors"
                  >
                    <MessageSquare className="w-4 h-4 text-gray-400 flex-shrink-0" />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-gray-900 truncate">{chat.title}</p>
                      <p className="text-xs text-gray-500">
                        {new Date(chat.updated_at).toLocaleDateString()}
                      </p>
                    </div>
                  </Link>
                ))
              )}
            </div>
          </div>

          {/* Active Agents */}
          <div className="card">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-medium text-gray-900">Active Agents</h3>
              <Link to="/agents" className="text-sm text-primary-600 hover:text-primary-700">
                View all
              </Link>
            </div>
            <div className="space-y-2">
              {activeAgents.length === 0 ? (
                <p className="text-gray-500 text-sm">No active agents</p>
              ) : (
                activeAgents.map((agent) => (
                  <Link
                    key={agent.id}
                    to={`/agents/${agent.namespace}/${agent.name}`}
                    className="flex items-center gap-3 p-2 hover:bg-gray-50 rounded-lg transition-colors"
                  >
                    <Bot className="w-4 h-4 text-gray-400 flex-shrink-0" />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-gray-900 truncate">{agent.name}</p>
                      {agent.model && (
                        <p className="text-xs text-gray-500 truncate">{agent.model}</p>
                      )}
                    </div>
                  </Link>
                ))
              )}
            </div>
          </div>

          {/* Active Schedules */}
          <div className="card">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-medium text-gray-900">Active Schedules</h3>
              <Link to="/schedules" className="text-sm text-primary-600 hover:text-primary-700">
                View all
              </Link>
            </div>
            <div className="space-y-2">
              {!schedules || schedules.filter((s) => s.is_active).length === 0 ? (
                <p className="text-gray-500 text-sm">No active schedules</p>
              ) : (
                schedules
                  .filter((s) => s.is_active)
                  .slice(0, 5)
                  .map((schedule) => (
                    <Link
                      key={schedule.id}
                      to={`/schedules/${schedule.id}`}
                      className="flex items-center gap-3 p-2 hover:bg-gray-50 rounded-lg transition-colors"
                    >
                      <Clock className="w-4 h-4 text-gray-400 flex-shrink-0" />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium text-gray-900 truncate">{schedule.name}</p>
                        <p className="text-xs text-gray-500 font-mono truncate">{schedule.cron_expression}</p>
                      </div>
                    </Link>
                  ))
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Quick Actions */}
      <div>
        <h2 className="text-lg font-semibold text-gray-900 mb-3">Quick Actions</h2>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          <Link to="/chats" className="card hover:shadow-md transition-shadow cursor-pointer">
            <div className="flex items-center gap-3">
              <MessageSquare className="w-5 h-5 text-primary-600" />
              <span className="font-medium text-gray-900">New Chat</span>
            </div>
          </Link>
          <Link to="/agents" className="card hover:shadow-md transition-shadow cursor-pointer">
            <div className="flex items-center gap-3">
              <Bot className="w-5 h-5 text-primary-600" />
              <span className="font-medium text-gray-900">Create Agent</span>
            </div>
          </Link>
          <Link to="/functions" className="card hover:shadow-md transition-shadow cursor-pointer">
            <div className="flex items-center gap-3">
              <Code className="w-5 h-5 text-primary-600" />
              <span className="font-medium text-gray-900">New Function</span>
            </div>
          </Link>
        </div>
      </div>
    </div>
  );
}
