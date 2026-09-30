import React from 'react';
import { v, tokens } from './tokens';

export interface ToolCallCardProps {
  name: string;
  content: string;
  /** Show a running/in-progress indicator with optional status description */
  running?: boolean;
  /** Status description to show while running (from status_templates) */
  statusText?: string;
  /** Tool result content (merged from tool_end event) */
  result?: string | null;
}

const WrenchIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
  </svg>
);

const SpinnerIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ animation: 'sinas-spin 1s linear infinite' }}>
    <path d="M21 12a9 9 0 1 1-6.219-8.56" />
  </svg>
);

const CheckIcon = () => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="20 6 9 17 4 12" />
  </svg>
);

const ChevronIcon = ({ expanded }: { expanded: boolean }) => (
  <svg
    width="14"
    height="14"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    style={{
      transition: 'transform 150ms ease',
      transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
    }}
  >
    <polyline points="9 18 15 12 9 6" />
  </svg>
);

function formatContent(content: string): string {
  try {
    const parsed = JSON.parse(content);
    return JSON.stringify(parsed, null, 2);
  } catch {
    return content;
  }
}

/** Clean raw tool name: "default__jina_search" → "jina search" */
function formatToolName(name: string): string {
  // Strip namespace prefix (everything before __)
  const parts = name.split('__');
  const base = parts.length > 1 ? parts.slice(1).join('__') : name;
  return base.replace(/_/g, ' ');
}

export function ToolCallCard({ name, content, running, statusText, result }: ToolCallCardProps) {
  const [expanded, setExpanded] = React.useState(false);
  const hasResult = result != null;
  const completed = hasResult && !running;

  // Use status template as title when available, otherwise clean up the function name
  const displayName = statusText || formatToolName(name);

  const tint = { bg: 'rgba(249, 115, 22, 0.06)', border: 'rgba(249, 115, 22, 0.15)', text: '#fb923c' };

  return (
    <div
      style={{
        borderRadius: v(tokens.radiusMd),
        border: `1px solid ${tint.border}`,
        backgroundColor: tint.bg,
        fontSize: '13px',
        overflow: 'hidden',
      }}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          width: '100%',
          padding: '8px 12px',
          background: 'none',
          border: 'none',
          color: tint.text,
          cursor: 'pointer',
          fontFamily: 'inherit',
          fontSize: '13px',
          textAlign: 'left',
        }}
      >
        {running && !hasResult ? (
          <SpinnerIcon />
        ) : completed ? (
          <span style={{ color: 'rgba(74, 222, 128, 0.8)', display: 'inline-flex', alignItems: 'center' }}>
            <CheckIcon />
          </span>
        ) : (
          <WrenchIcon />
        )}
        <span style={{ fontWeight: 500 }}>
          {displayName}
        </span>
        <span style={{ marginLeft: 'auto' }}>
          <ChevronIcon expanded={expanded} />
        </span>
      </button>
      {expanded && (
        <div>
          {content && (
            <pre
              style={{
                margin: 0,
                padding: '10px 12px',
                borderTop: `1px solid ${tint.border}`,
                background: 'rgba(0, 0, 0, 0.2)',
                color: v(tokens.colorTextMuted),
                fontFamily: v(tokens.fontMono),
                fontSize: '12px',
                lineHeight: '1.5',
                overflowX: 'auto',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {formatContent(content)}
            </pre>
          )}
          {hasResult && (
            <pre
              style={{
                margin: 0,
                padding: '10px 12px',
                borderTop: `1px solid ${tint.border}`,
                background: 'rgba(0, 0, 0, 0.15)',
                color: v(tokens.colorText),
                fontFamily: v(tokens.fontMono),
                fontSize: '12px',
                lineHeight: '1.5',
                overflowX: 'auto',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                maxHeight: '200px',
                overflowY: 'auto',
              }}
            >
              {formatContent(result!)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
