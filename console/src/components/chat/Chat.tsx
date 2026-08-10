import React from 'react';
import { useChat, useClient, uploadFile, generateFileUrl } from '@sinas/sdk';
import type { ChatSessionMessage, MessageContent } from '@sinas/sdk';

import { v, tokens, injectBaseStyles } from './tokens';
import { injectMarkdownStyles } from './markdownStyles';
import { ChatMessage } from './ChatMessage';
import { ApprovalCard } from './ApprovalCard';
import { ChatInput } from './ChatInput';

export interface ChatProps {
  /** Agent reference: "namespace/name" */
  agent: string;
  /** Resume existing chat by ID */
  chatId?: string;
  /** Agent input variables */
  input?: Record<string, unknown>;
  /** Input placeholder text */
  placeholder?: string;
  /** Header title (optional — omit for no header) */
  title?: string;
  /** Container height */
  height?: string | number;
  /** Additional inline styles */
  style?: React.CSSProperties;
  /** Agent icon URL for assistant message avatars */
  agentIconUrl?: string;
  /** API base URL for component rendering (e.g. "http://localhost:8000") */
  apiBaseUrl?: string;
  /** Called when a new chat is created */
  onChatCreated?: (chatId: string) => void;
  /** Called on errors */
  onError?: (error: string) => void;
  /** Called when messages are refreshed from server (after stream ends) */
  onMessagesRefreshed?: () => void;
}

export function Chat({
  agent,
  chatId: initialChatId,
  input,
  placeholder,
  title,
  height = '100%',
  style,
  agentIconUrl,
  apiBaseUrl,
  onChatCreated,
  onError,
  onMessagesRefreshed,
}: ChatProps) {
  injectBaseStyles();
  injectMarkdownStyles();

  const {
    chatId,
    messages,
    loading,
    streaming,
    error,
    pendingApprovals,
    toolStatus,
    send,
    approve,
    reject,
    stop,
    clearError,
  } = useChat(agent, { chatId: initialChatId, input });

  const messagesEndRef = React.useRef<HTMLDivElement>(null);
  const prevChatIdRef = React.useRef<string | null>(null);
  const prevMessageCountRef = React.useRef<number>(0);

  // Derive apiBaseUrl from SDK config if not provided
  const resolvedApiBaseUrl = React.useMemo(() => {
    if (apiBaseUrl) return apiBaseUrl;
    if (typeof window !== 'undefined' && window.__SINAS_CONFIG__?.apiBase) {
      return window.__SINAS_CONFIG__.apiBase;
    }
    return undefined;
  }, [apiBaseUrl]);

  // postMessage auth bridge for component iframes
  React.useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.data?.type === 'sinas:ready') {
        const token = typeof window !== 'undefined' ? window.__SINAS_AUTH_TOKEN__ : null;
        if (token && event.source) {
          (event.source as Window).postMessage(
            { type: 'sinas:auth', token },
            '*',
          );
        }
      }
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, []);

  // Notify parent of new chat creation
  React.useEffect(() => {
    if (chatId && chatId !== initialChatId && chatId !== prevChatIdRef.current) {
      prevChatIdRef.current = chatId;
      onChatCreated?.(chatId);
    }
  }, [chatId, initialChatId, onChatCreated]);

  // Notify parent of errors
  React.useEffect(() => {
    if (error) onError?.(error);
  }, [error, onError]);

  // Detect message refresh (count changed while not streaming = server refresh)
  React.useEffect(() => {
    if (
      !streaming &&
      messages.length > 0 &&
      messages.length !== prevMessageCountRef.current &&
      prevMessageCountRef.current > 0
    ) {
      onMessagesRefreshed?.();
    }
    prevMessageCountRef.current = messages.length;
  }, [messages.length, streaming, onMessagesRefreshed]);

  // Auto-scroll to bottom on new messages or streaming
  React.useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, pendingApprovals, toolStatus]);

  const client = useClient();

  const handleUploadFile = React.useCallback(
    async (file: File): Promise<string> => {
      const reader = new FileReader();
      const base64 = await new Promise<string>((resolve, reject) => {
        reader.onload = () => {
          const result = reader.result as string;
          resolve(result.split(',')[1]); // strip data URL prefix
        };
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });

      const uniqueName = `${chatId || 'new'}-${Date.now()}-${file.name}`;
      await uploadFile(client, 'default', 'chat-uploads', {
        name: uniqueName,
        content_base64: base64,
        content_type: file.type,
        visibility: 'private',
      });
      const urlResult = await generateFileUrl(
        client, 'default', 'chat-uploads', uniqueName, 2592000,
      );
      return urlResult.url;
    },
    [client, chatId],
  );

  const handleSend = React.useCallback(
    (content: MessageContent) => {
      send(content);
    },
    [send],
  );

  const heightValue = typeof height === 'number' ? `${height}px` : height;

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: heightValue,
        borderRadius: v(tokens.radiusLg),
        border: `1px solid ${v(tokens.colorBorder)}`,
        backgroundColor: v(tokens.colorBg),
        overflow: 'hidden',
        fontFamily: v(tokens.fontSans),
        ...style,
      }}
    >
      {/* Header */}
      {title && (
        <div
          style={{
            padding: '12px 16px',
            borderBottom: `1px solid ${v(tokens.colorBorder)}`,
            backgroundColor: v(tokens.colorBgSubtle),
            fontSize: '14px',
            fontWeight: 600,
            color: v(tokens.colorText),
            flexShrink: 0,
          }}
        >
          {title}
        </div>
      )}

      {/* Messages area */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '16px',
          display: 'flex',
          flexDirection: 'column',
          gap: '8px',
        }}
      >
        {loading && messages.length === 0 && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flex: 1,
              color: v(tokens.colorTextMuted),
              fontSize: '13px',
            }}
          >
            <span style={{ animation: 'sinas-pulse 1.5s ease-in-out infinite' }}>
              Loading...
            </span>
          </div>
        )}

        {messages.map((msg: ChatSessionMessage) => {
          // Skip standalone tool result messages — their content is shown
          // inline in the parent tool call bubble.
          if (msg.role === 'tool' && msg.toolCallId) {
            const mergedDuringStream = messages.some(
              (m) => m.toolResults?.[msg.toolCallId!],
            );
            const matchedToCall = messages.some(
              (m) => m.toolCalls?.some((tc) => tc.id === msg.toolCallId),
            );
            if (mergedDuringStream || matchedToCall) return null;
          }

          // For server-loaded messages, build toolResults from sibling tool messages
          let enrichedMsg = msg;
          if (msg.toolCalls && !msg.toolResults) {
            const results: Record<string, { content: string; name: string }> = {};
            for (const tc of msg.toolCalls) {
              const toolMsg = messages.find(
                (m) => m.role === 'tool' && m.toolCallId === tc.id,
              );
              if (toolMsg?.content) {
                results[tc.id] = { content: toolMsg.content, name: toolMsg.name || tc.function.name };
              }
            }
            if (Object.keys(results).length > 0) {
              enrichedMsg = { ...msg, toolResults: results };
            }
          }

          return (
            <ChatMessage
              key={msg.id}
              message={enrichedMsg}
              agentIconUrl={agentIconUrl}
              apiBaseUrl={resolvedApiBaseUrl}
            />
          );
        })}

        {/* Streaming status indicator */}
        {streaming && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '4px 0' }}>
            <div style={{ width: '24px', flexShrink: 0 }} />
            <div
              style={{
                padding: '8px 14px',
                color: v(tokens.colorTextMuted),
                fontSize: '13px',
              }}
            >
              <span style={{ animation: 'sinas-pulse 1.5s ease-in-out infinite' }}>
                {(() => {
                  const running = toolStatus.filter((t) => t.status === 'running');
                  return running.length > 0 ? running[running.length - 1].description || 'Thinking...' : 'Thinking...';
                })()}
              </span>
            </div>
          </div>
        )}

        {/* Error display */}
        {error && (
          <div
            style={{
              padding: '10px 14px',
              borderRadius: v(tokens.radiusMd),
              backgroundColor: 'rgba(239, 68, 68, 0.08)',
              border: '1px solid rgba(239, 68, 68, 0.2)',
              color: 'var(--red-300)',
              fontSize: '13px',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <span>{error}</span>
            <button
              onClick={clearError}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--red-300)',
                cursor: 'pointer',
                fontSize: '16px',
                padding: '0 4px',
                opacity: 0.7,
              }}
            >
              {'\u00d7'}
            </button>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Pending approvals — pinned above input */}
      {pendingApprovals.length > 0 && (
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '6px',
            padding: '8px 16px',
            borderTop: `1px solid ${v(tokens.colorBorder)}`,
            backgroundColor: v(tokens.colorBg),
            flexShrink: 0,
            maxHeight: '40%',
            overflowY: 'auto',
          }}
        >
          {pendingApprovals.map((approval) => (
            <ApprovalCard
              key={approval.toolCallId}
              functionName={approval.functionName}
              functionNamespace={approval.functionNamespace}
              arguments={approval.arguments}
              onApprove={() => approve(approval.toolCallId)}
              onReject={() => reject(approval.toolCallId)}
            />
          ))}
        </div>
      )}

      {/* Input bar */}
      <ChatInput
        onSend={handleSend}
        onStop={stop}
        streaming={streaming}
        disabled={loading}
        placeholder={placeholder}
        onUploadFile={handleUploadFile}
      />
    </div>
  );
}
