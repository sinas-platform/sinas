import { v, tokens } from './tokens';
import { renderMarkdown } from './markdownRenderer';
import { ToolCallCard } from './ToolCallCard';
import type { ChatSessionMessage, ContentPart, ComponentContentPart } from '@sinas/sdk';

export interface ChatMessageProps {
  message: ChatSessionMessage;
  agentIconUrl?: string;
  apiBaseUrl?: string;
}

const BotIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M12 8V4H8" />
    <rect x="2" y="8" width="20" height="14" rx="2" />
    <path d="M6 15h.01" />
    <path d="M18 15h.01" />
    <path d="M10 18h4" />
  </svg>
);

const UserIcon = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
    <circle cx="12" cy="7" r="4" />
  </svg>
);

const MusicIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M9 18V5l12-2v13" />
    <circle cx="6" cy="18" r="3" />
    <circle cx="18" cy="16" r="3" />
  </svg>
);

const FileIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <polyline points="14 2 14 8 20 8" />
  </svg>
);

function parseContent(content: string | null): ContentPart[] | string | null {
  if (!content) return null;
  if (typeof content === 'string' && content.trim().startsWith('[')) {
    try {
      return JSON.parse(content) as ContentPart[];
    } catch {
      return content;
    }
  }
  return content;
}

function buildComponentRenderUrl(part: ComponentContentPart, apiBaseUrl: string): string {
  const params = new URLSearchParams();
  params.set('token', part.render_token);
  if (part.input) params.set('input', JSON.stringify(part.input));
  return `${apiBaseUrl}/components/${part.namespace}/${part.name}/render?${params.toString()}`;
}

function ComponentFrame({
  part,
  apiBaseUrl,
}: {
  part: ComponentContentPart;
  apiBaseUrl: string;
}) {
  const renderUrl = buildComponentRenderUrl(part, apiBaseUrl);
  const token = typeof window !== 'undefined' ? window.__SINAS_AUTH_TOKEN__ : null;
  const openUrl = token
    ? `${renderUrl}#auth=${encodeURIComponent(token)}`
    : renderUrl;

  return (
    <div
      style={{
        border: `1px solid ${v(tokens.colorBorder)}`,
        borderRadius: v(tokens.radiusMd),
        overflow: 'hidden',
        marginTop: '6px',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '4px 10px',
          backgroundColor: v(tokens.colorBgSubtle),
          borderBottom: `1px solid ${v(tokens.colorBorder)}`,
          fontSize: '12px',
        }}
      >
        <span style={{ color: v(tokens.colorTextMuted) }}>
          {part.title || `${part.namespace}/${part.name}`}
        </span>
        <a
          href={openUrl}
          target="_blank"
          rel="noopener noreferrer"
          style={{ color: v(tokens.colorPrimary), textDecoration: 'none' }}
        >
          Open
        </a>
      </div>
      <iframe
        src={renderUrl}
        style={{
          width: '100%',
          height: '400px',
          border: 'none',
          backgroundColor: '#fff',
        }}
        title={part.title || part.name}
      />
    </div>
  );
}

function MultimodalContent({
  parts,
  isUser,
  apiBaseUrl,
}: {
  parts: ContentPart[];
  isUser: boolean;
  apiBaseUrl?: string;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
      {parts.map((part, idx) => {
        if (part.type === 'text') {
          if (isUser) {
            return (
              <span key={idx} style={{ whiteSpace: 'pre-wrap' }}>
                {part.text}
              </span>
            );
          }
          const html = renderMarkdown(part.text);
          return (
            <div
              key={idx}
              className="sinas-chat-md"
              dangerouslySetInnerHTML={{ __html: html }}
            />
          );
        }
        if (part.type === 'image') {
          return (
            <img
              key={idx}
              src={part.image}
              alt="Attached image"
              style={{
                maxWidth: '300px',
                borderRadius: '6px',
              }}
            />
          );
        }
        if (part.type === 'audio') {
          return (
            <div
              key={idx}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '6px 10px',
                borderRadius: '6px',
                backgroundColor: 'rgba(0, 0, 0, 0.2)',
                fontSize: '12px',
                color: v(tokens.colorTextMuted),
              }}
            >
              <MusicIcon />
              <span>Audio file ({part.format})</span>
            </div>
          );
        }
        if (part.type === 'file') {
          return (
            <div
              key={idx}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '6px 10px',
                borderRadius: '6px',
                backgroundColor: 'rgba(0, 0, 0, 0.2)',
                fontSize: '12px',
                color: v(tokens.colorTextMuted),
              }}
            >
              <FileIcon />
              <span>{part.filename || 'File attachment'}</span>
            </div>
          );
        }
        if (part.type === 'component' && apiBaseUrl) {
          return (
            <ComponentFrame
              key={idx}
              part={part}
              apiBaseUrl={apiBaseUrl}
            />
          );
        }
        return null;
      })}
    </div>
  );
}

export function ChatMessage({ message, agentIconUrl, apiBaseUrl }: ChatMessageProps) {
  const isUser = message.role === 'user';
  const isTool = message.role === 'tool';

  if (isTool) {
    // Check if this is a component tool result
    if (apiBaseUrl && message.content) {
      try {
        const parsed = JSON.parse(message.content);
        if (parsed?.type === 'component' && parsed.render_token) {
          return (
            <div style={{ padding: '4px 0 4px 32px', maxWidth: '85%' }}>
              <ComponentFrame part={parsed} apiBaseUrl={apiBaseUrl} />
            </div>
          );
        }
      } catch {
        // Not JSON or not a component — fall through to regular tool card
      }
    }

    return (
      <div style={{ padding: '4px 0 4px 32px', maxWidth: '85%' }}>
        <ToolCallCard
          name={message.name || 'Tool Result'}
          content={message.content || ''}
        />
      </div>
    );
  }

  if (isUser) {
    const parsed = parseContent(message.content);
    return (
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'flex-end', gap: '8px', padding: '4px 0' }}>
        <div
          style={{
            maxWidth: '80%',
            padding: '10px 14px',
            borderRadius: '16px 16px 4px 16px',
            backgroundColor: v(tokens.colorPrimary),
            color: '#fff',
            fontSize: '14px',
            lineHeight: '1.5',
            wordBreak: 'break-word',
          }}
        >
          {Array.isArray(parsed) ? (
            <MultimodalContent parts={parsed} isUser={true} apiBaseUrl={apiBaseUrl} />
          ) : (
            <span style={{ whiteSpace: 'pre-wrap' }}>{parsed}</span>
          )}
        </div>
        <div
          style={{
            width: '24px',
            height: '24px',
            marginTop: '8px',
            borderRadius: '50%',
            backgroundColor: 'var(--hover-strong)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
            color: v(tokens.colorTextMuted),
          }}
        >
          <UserIcon />
        </div>
      </div>
    );
  }

  // Assistant message
  const hasToolCalls = message.toolCalls && message.toolCalls.length > 0;
  const parsed = parseContent(message.content);
  const hasContent = parsed && (Array.isArray(parsed) || (typeof parsed === 'string' && parsed.length > 0));

  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '8px', padding: '4px 0' }}>
      <div
        style={{
          width: '24px',
          height: '24px',
          marginTop: '8px',
          borderRadius: '50%',
          backgroundColor: agentIconUrl ? 'transparent' : 'rgba(249, 115, 22, 0.15)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
          color: v(tokens.colorPrimary),
          overflow: 'hidden',
        }}
      >
        {agentIconUrl ? (
          <img
            src={agentIconUrl}
            alt=""
            style={{ width: '24px', height: '24px', objectFit: 'cover', borderRadius: '50%' }}
          />
        ) : (
          <BotIcon />
        )}
      </div>
      <div style={{ maxWidth: '85%', minWidth: 0 }}>
        {hasContent && (
          <div
            style={{
              padding: '10px 14px',
              borderRadius: '16px 16px 16px 4px',
              backgroundColor: v(tokens.colorBgElevated),
              border: `1px solid ${v(tokens.colorBorder)}`,
              color: v(tokens.colorText),
              fontSize: '14px',
            }}
          >
            {Array.isArray(parsed) ? (
              <MultimodalContent parts={parsed} isUser={false} apiBaseUrl={apiBaseUrl} />
            ) : (
              <div
                className="sinas-chat-md"
                dangerouslySetInnerHTML={{ __html: renderMarkdown(parsed as string) }}
              />
            )}
          </div>
        )}
        {hasToolCalls &&
          message.toolCalls!.map((tc, idx) => {
            const result = message.toolResults?.[tc.id];
            // Derive running state: no result yet while message is still streaming
            const isRunning = !result && !!message.streaming;
            return (
              <div key={tc.id} style={{ marginTop: hasContent || idx > 0 ? '6px' : 0 }}>
                <ToolCallCard
                  name={tc.function.name}
                  content={tc.function.arguments}
                  running={isRunning}
                  statusText={tc.description}
                  result={result?.content ?? null}
                />
              </div>
            );
          })}
      </div>
    </div>
  );
}
