import { useParams, Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient, API_BASE_URL } from '../lib/api';
import { ArrowLeft, Loader2, Bot } from 'lucide-react';
import { Chat } from '@sinas/ui';

export function ChatDetail() {
  const { chatId } = useParams<{ chatId: string }>();
  const queryClient = useQueryClient();

  const { data: chat, isLoading } = useQuery({
    queryKey: ['chat-meta', chatId],
    queryFn: () => apiClient.getChat(chatId!),
    enabled: !!chatId,
  });

  const { data: agent } = useQuery({
    queryKey: ['agent', chat?.agent_namespace, chat?.agent_name],
    queryFn: () => apiClient.getAgent(chat!.agent_namespace!, chat!.agent_name!),
    enabled: !!chat?.agent_namespace && !!chat?.agent_name,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-96">
        <Loader2 className="w-8 h-8 animate-spin text-primary-600" />
      </div>
    );
  }

  if (!chat) {
    return (
      <div className="text-center py-12">
        <h2 className="text-xl font-semibold text-gray-100">Chat not found</h2>
        <Link to="/chats" className="text-primary-600 hover:text-primary-400 mt-2 inline-block">
          Back to chats
        </Link>
      </div>
    );
  }

  const agentRef = `${chat.agent_namespace}/${chat.agent_name}`;

  return (
    <div className="flex flex-col h-[calc(100vh-8rem)]">
      {/* Header */}
      <div className="flex items-center justify-between pb-4 border-b border-white/[0.06]">
        <div className="flex items-center">
          <Link to="/chats" className="mr-4 text-gray-400 hover:text-gray-100">
            <ArrowLeft className="w-5 h-5" />
          </Link>
          {agent?.icon_url ? (
            <img src={agent.icon_url} alt="" className="w-8 h-8 rounded-lg object-cover mr-3" />
          ) : (
            <Bot className="w-8 h-8 text-primary-600 mr-3" />
          )}
          <div>
            <h1 className="text-2xl font-bold text-gray-100">{chat.title}</h1>
            <p className="text-sm text-gray-500">
              {agentRef} • Created {new Date(chat.created_at).toLocaleDateString()}
            </p>
          </div>
        </div>
      </div>

      {/* Chat component from @sinas/ui */}
      <div className="flex-1 min-h-0 pt-4">
        <Chat
          agent={agentRef}
          chatId={chatId}
          height="100%"
          placeholder="Type your message... (Shift+Enter for new line)"
          agentIconUrl={agent?.icon_url || undefined}
          apiBaseUrl={API_BASE_URL}
          onMessagesRefreshed={() => {
            queryClient.invalidateQueries({ queryKey: ['chat-meta', chatId] });
          }}
          style={{ border: 'none', borderRadius: 0, backgroundColor: 'transparent' }}
        />
      </div>
    </div>
  );
}
