import React from 'react';
import { v, tokens } from './tokens';

export interface ApprovalCardProps {
  functionName: string;
  functionNamespace: string;
  arguments: Record<string, unknown>;
  onApprove: () => void;
  onReject: () => void;
}

const WarningIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
    <line x1="12" y1="9" x2="12" y2="13" />
    <line x1="12" y1="17" x2="12.01" y2="17" />
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

export function ApprovalCard({
  functionName,
  functionNamespace,
  arguments: args,
  onApprove,
  onReject,
}: ApprovalCardProps) {
  const [expanded, setExpanded] = React.useState(false);

  return (
    <div
      style={{
        borderRadius: v(tokens.radiusMd),
        border: '1px solid rgba(234, 179, 8, 0.25)',
        backgroundColor: 'rgba(234, 179, 8, 0.06)',
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '10px 14px',
          color: 'var(--amber-400)',
          fontSize: '13px',
          fontWeight: 600,
        }}
      >
        <WarningIcon />
        <span>Function Approval Required</span>
      </div>

      {/* Function name */}
      <div style={{ padding: '0 14px 10px' }}>
        <span
          style={{
            display: 'inline-block',
            padding: '3px 8px',
            borderRadius: '4px',
            backgroundColor: 'rgba(234, 179, 8, 0.12)',
            color: 'var(--amber-400)',
            fontFamily: v(tokens.fontMono),
            fontSize: '12px',
          }}
        >
          {functionNamespace}/{functionName}
        </span>
      </div>

      {/* Collapsible args */}
      {Object.keys(args).length > 0 && (
        <>
          <button
            onClick={() => setExpanded(!expanded)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
              width: '100%',
              padding: '6px 14px',
              background: 'none',
              border: 'none',
              borderTop: '1px solid rgba(234, 179, 8, 0.12)',
              color: v(tokens.colorTextMuted),
              cursor: 'pointer',
              fontFamily: 'inherit',
              fontSize: '12px',
              textAlign: 'left',
            }}
          >
            <ChevronIcon expanded={expanded} />
            <span>Arguments</span>
          </button>
          {expanded && (
            <pre
              style={{
                margin: 0,
                padding: '10px 14px',
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
              {JSON.stringify(args, null, 2)}
            </pre>
          )}
        </>
      )}

      {/* Buttons */}
      <div
        style={{
          display: 'flex',
          gap: '8px',
          padding: '10px 14px',
          borderTop: '1px solid rgba(234, 179, 8, 0.12)',
        }}
      >
        <button
          onClick={onApprove}
          style={{
            flex: 1,
            padding: '7px 14px',
            borderRadius: v(tokens.radiusSm),
            border: '1px solid rgba(34, 197, 94, 0.3)',
            backgroundColor: 'rgba(34, 197, 94, 0.12)',
            color: 'var(--green-400)',
            cursor: 'pointer',
            fontFamily: 'inherit',
            fontSize: '13px',
            fontWeight: 500,
          }}
        >
          Approve
        </button>
        <button
          onClick={onReject}
          style={{
            flex: 1,
            padding: '7px 14px',
            borderRadius: v(tokens.radiusSm),
            border: '1px solid rgba(239, 68, 68, 0.3)',
            backgroundColor: 'rgba(239, 68, 68, 0.12)',
            color: 'var(--red-400)',
            cursor: 'pointer',
            fontFamily: 'inherit',
            fontSize: '13px',
            fontWeight: 500,
          }}
        >
          Reject
        </button>
      </div>
    </div>
  );
}
