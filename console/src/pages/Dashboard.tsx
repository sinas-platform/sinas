import { useQuery } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { useAuth } from '../lib/auth-context';
import { MessageSquare, Bot, Code, TrendingUp, Zap, CheckCircle, X, Brain, ArrowRight } from 'lucide-react';
import { Link } from 'react-router-dom';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts';
import { useState } from 'react';

const ONBOARDING_DISMISSED_KEY = 'sinas_onboarding_dismissed';

export function Dashboard() {
  const { user } = useAuth();
  const [onboardingDismissed, setOnboardingDismissed] = useState(
    () => localStorage.getItem(ONBOARDING_DISMISSED_KEY) === 'true'
  );

  const { data: agents } = useQuery({
    queryKey: ['assistants'],
    queryFn: () => apiClient.listAgents(),
    enabled: !!user,
    retry: false,
  });

  const { data: functions } = useQuery({
    queryKey: ['functions'],
    queryFn: () => apiClient.listFunctions(),
    enabled: !!user,
    retry: false,
  });

  const { data: messagesData } = useQuery({
    queryKey: ['messages-dashboard'],
    queryFn: () => apiClient.searchMessages({ limit: 1000 }),
    enabled: !!user,
    retry: false,
  });

  const { data: llmProviders } = useQuery({
    queryKey: ['llmProviders'],
    queryFn: () => apiClient.listLLMProviders(),
    enabled: !!user,
    retry: false,
  });

  const { data: chats } = useQuery({
    queryKey: ['chats'],
    queryFn: () => apiClient.listChats(),
    enabled: !!user,
    retry: false,
  });

  const messages = messagesData?.messages || [];

  // Onboarding state
  const hasProviders = (llmProviders?.filter((p) => p.is_active)?.length || 0) > 0;
  const hasAgents = (agents?.length || 0) > 0;
  const hasChats = (chats?.length || 0) > 0;
  const allComplete = hasProviders && hasAgents && hasChats;
  const showOnboarding = !onboardingDismissed && !allComplete;

  const dismissOnboarding = () => {
    localStorage.setItem(ONBOARDING_DISMISSED_KEY, 'true');
    setOnboardingDismissed(true);
  };

  // Calculate activity over last 7 days
  const last7Days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() - (6 - i));
    return d.toISOString().split('T')[0];
  });

  const activityByDay = last7Days.map((date) => ({
    date: new Date(date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    messages: messages.filter((m: any) => m.created_at.startsWith(date)).length,
  }));

  // Top agents by message count
  const agentUsage = messages.reduce((acc: any, msg: any) => {
    if (msg.chat?.agent_namespace && msg.chat?.agent_name) {
      const key = `${msg.chat.agent_namespace}/${msg.chat.agent_name}`;
      acc[key] = (acc[key] || 0) + 1;
    }
    return acc;
  }, {});

  const topAgents = Object.entries(agentUsage)
    .sort(([, a]: any, [, b]: any) => b - a)
    .slice(0, 5)
    .map(([agent, count]) => ({ agent, count }));

  // Role distribution
  const roleDistribution = messages.reduce((acc: any, msg: any) => {
    acc[msg.role] = (acc[msg.role] || 0) + 1;
    return acc;
  }, {});

  const roleData = Object.entries(roleDistribution).map(([name, value]) => ({ name, value }));

  const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#6366f1'];

  const stats = [
    { name: 'Total Messages', value: messages.length, icon: MessageSquare, color: 'blue' },
    { name: 'Active Agents', value: agents?.filter((a) => a.is_active).length || 0, icon: Bot, color: 'purple' },
    { name: 'Functions', value: functions?.length || 0, icon: Code, color: 'green' },
    { name: 'Tool Calls', value: messages.filter((m: any) => m.tool_calls && m.tool_calls.length > 0).length, icon: Zap, color: 'orange' },
  ];

  const getColorClasses = (color: string) => {
    const colors: Record<string, { bg: string; text: string; icon: string }> = {
      blue: { bg: 'bg-blue-50', text: 'text-blue-700', icon: 'text-blue-600' },
      purple: { bg: 'bg-purple-50', text: 'text-purple-700', icon: 'text-purple-600' },
      green: { bg: 'bg-green-50', text: 'text-green-700', icon: 'text-green-600' },
      orange: { bg: 'bg-orange-50', text: 'text-orange-700', icon: 'text-orange-600' },
    };
    return colors[color] || colors.blue;
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Dashboard</h1>
        <p className="text-gray-600 mt-1">Overview of your Sinas AI platform</p>
      </div>

      {/* Onboarding Checklist */}
      {showOnboarding && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-5">
          <div className="flex items-start justify-between mb-3">
            <div>
              <h3 className="text-sm font-semibold text-blue-900">Get Started with SINAS</h3>
              <p className="text-xs text-blue-700 mt-0.5">Complete these steps to start using your AI platform</p>
            </div>
            <button
              onClick={dismissOnboarding}
              className="text-blue-400 hover:text-blue-600 p-1 -mt-1 -mr-1"
              title="Dismiss"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
          <div className="space-y-2">
            <Link
              to="/llm-providers"
              className="flex items-center gap-3 p-2.5 bg-white rounded-md border border-blue-100 hover:border-blue-300 transition-colors group"
            >
              {hasProviders ? (
                <CheckCircle className="w-5 h-5 text-green-500 flex-shrink-0" />
              ) : (
                <div className="w-5 h-5 rounded-full border-2 border-blue-300 flex items-center justify-center flex-shrink-0">
                  <span className="text-xs font-semibold text-blue-400">1</span>
                </div>
              )}
              <Brain className="w-4 h-4 text-blue-500 flex-shrink-0" />
              <span className={`text-sm flex-1 ${hasProviders ? 'text-gray-500 line-through' : 'text-gray-900 font-medium'}`}>
                Configure an LLM Provider
              </span>
              {!hasProviders && <ArrowRight className="w-4 h-4 text-gray-400 group-hover:text-blue-500" />}
            </Link>
            <Link
              to="/agents"
              className="flex items-center gap-3 p-2.5 bg-white rounded-md border border-blue-100 hover:border-blue-300 transition-colors group"
            >
              {hasAgents ? (
                <CheckCircle className="w-5 h-5 text-green-500 flex-shrink-0" />
              ) : (
                <div className="w-5 h-5 rounded-full border-2 border-blue-300 flex items-center justify-center flex-shrink-0">
                  <span className="text-xs font-semibold text-blue-400">2</span>
                </div>
              )}
              <Bot className="w-4 h-4 text-blue-500 flex-shrink-0" />
              <span className={`text-sm flex-1 ${hasAgents ? 'text-gray-500 line-through' : 'text-gray-900 font-medium'}`}>
                Create an Agent
              </span>
              {!hasAgents && <ArrowRight className="w-4 h-4 text-gray-400 group-hover:text-blue-500" />}
            </Link>
            <Link
              to="/agents"
              className="flex items-center gap-3 p-2.5 bg-white rounded-md border border-blue-100 hover:border-blue-300 transition-colors group"
            >
              {hasChats ? (
                <CheckCircle className="w-5 h-5 text-green-500 flex-shrink-0" />
              ) : (
                <div className="w-5 h-5 rounded-full border-2 border-blue-300 flex items-center justify-center flex-shrink-0">
                  <span className="text-xs font-semibold text-blue-400">3</span>
                </div>
              )}
              <MessageSquare className="w-4 h-4 text-blue-500 flex-shrink-0" />
              <span className={`text-sm flex-1 ${hasChats ? 'text-gray-500 line-through' : 'text-gray-900 font-medium'}`}>
                Start a Conversation
              </span>
              {!hasChats && <ArrowRight className="w-4 h-4 text-gray-400 group-hover:text-blue-500" />}
            </Link>
          </div>
        </div>
      )}

      {/* Key Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {stats.map((stat) => {
          const Icon = stat.icon;
          const colors = getColorClasses(stat.color);
          return (
            <div key={stat.name} className="card">
              <div className="flex items-center justify-between">
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-600 truncate">{stat.name}</p>
                  <p className="text-2xl font-bold text-gray-900 mt-2">{stat.value}</p>
                </div>
                <div className={`p-3 ${colors.bg} rounded-lg flex-shrink-0`}>
                  <Icon className={`w-6 h-6 ${colors.icon}`} />
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Activity Chart */}
        <div className="card">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-lg font-semibold text-gray-900">Activity (Last 7 Days)</h3>
            <TrendingUp className="w-5 h-5 text-gray-400" />
          </div>
          {activityByDay.some((d) => d.messages > 0) ? (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={activityByDay}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                <XAxis dataKey="date" tick={{ fill: '#6b7280', fontSize: 12 }} />
                <YAxis tick={{ fill: '#6b7280', fontSize: 12 }} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#ffffff', border: '1px solid #e5e7eb', borderRadius: '6px' }}
                />
                <Bar dataKey="messages" fill="#3b82f6" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex items-center justify-center h-48 text-gray-400">
              <p className="text-sm">No activity in the last 7 days</p>
            </div>
          )}
        </div>

        {/* Role Distribution */}
        <div className="card">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Message Types</h3>
          {roleData.length > 0 ? (
            <div className="flex items-center justify-center">
              <ResponsiveContainer width="100%" height={200}>
                <PieChart>
                  <Pie
                    data={roleData}
                    cx="50%"
                    cy="50%"
                    labelLine={false}
                    label={({ name, percent }) => `${name} (${(percent * 100).toFixed(0)}%)`}
                    outerRadius={80}
                    fill="#8884d8"
                    dataKey="value"
                  >
                    {roleData.map((_, index) => (
                      <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip />
                </PieChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="flex items-center justify-center h-48 text-gray-400">
              <p className="text-sm">No message data available</p>
            </div>
          )}
        </div>
      </div>

      {/* Top Agents */}
      {topAgents.length > 0 && (
        <div className="card">
          <h3 className="text-lg font-semibold text-gray-900 mb-4">Most Used Agents</h3>
          <div className="space-y-3">
            {topAgents.map((item: any, index) => (
              <div key={item.agent} className="flex items-center justify-between">
                <div className="flex items-center gap-3 flex-1">
                  <div
                    className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-semibold ${
                      index === 0
                        ? 'bg-yellow-100 text-yellow-700'
                        : index === 1
                        ? 'bg-gray-100 text-gray-700'
                        : index === 2
                        ? 'bg-orange-100 text-orange-700'
                        : 'bg-gray-50 text-gray-600'
                    }`}
                  >
                    {index + 1}
                  </div>
                  <Link
                    to={`/agents/${item.agent.split('/')[0]}/${item.agent.split('/')[1]}`}
                    className="text-sm font-mono text-gray-900 hover:text-primary-600"
                  >
                    {item.agent}
                  </Link>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-gray-900">{item.count}</span>
                  <span className="text-xs text-gray-500">messages</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
