import { useEffect, useRef, useState } from 'react';
import { Loader2, RotateCcw, Send } from 'lucide-react';
import { api } from '../lib/api';
import { avatarStyle, initials, schemaToInputRows } from '../lib/model';
import type { Agent, Message } from '../lib/types';
import { messageText } from '../lib/types';

/**
 * Live test chat against the real assistant, using the editor's current
 * (autosaved) state. Non-streaming in this iteration: each send blocks on the
 * reply, then the full transcript is refetched so tool activity (role: tool
 * messages) renders as chips.
 */
export function TestChat({ agent }: { agent: Agent }) {
  const [chatId, setChatId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inputValues, setInputValues] = useState<Record<string, string>>({});
  const scrollRef = useRef<HTMLDivElement>(null);

  const inputRows = schemaToInputRows(agent.input_schema);
  const requiredRows = inputRows.filter((r) => r.required);
  const missingRequired = requiredRows.filter((r) => !inputValues[r.name]?.trim());

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, sending]);

  const restart = () => {
    setChatId(null);
    setMessages([]);
    setError(null);
  };

  // A different assistant means a different conversation.
  useEffect(restart, [agent.namespace, agent.name]);

  const send = async () => {
    const text = input.trim();
    if (!text || sending || missingRequired.length > 0) return;
    setSending(true);
    setError(null);
    setInput('');
    setMessages((prev) => [
      ...prev,
      { id: `local-${prev.length}`, role: 'user', content: text, name: null, tool_calls: null, created_at: '' },
    ]);
    try {
      let id = chatId;
      if (!id) {
        const values = Object.fromEntries(Object.entries(inputValues).filter(([, v]) => v.trim() !== ''));
        const chat = await api.createChat(agent.namespace, agent.name, {
          title: 'Studio test chat',
          ...(Object.keys(values).length ? { input: values } : {}),
        });
        id = chat.id;
        setChatId(id);
      }
      await api.sendMessage(id, text);
      setMessages(await api.listMessages(id));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Message failed');
    } finally {
      setSending(false);
    }
  };

  return (
    <aside className="chat-pane">
      <div className="pane-head">
        <div>
          <div className="pane-title">Test chat</div>
          <div className="pane-sub">Talks to the real assistant, with your current edits</div>
        </div>
        <button className="btn btn-secondary btn-sm" style={{ marginLeft: 'auto' }} onClick={restart}>
          <RotateCcw size={13} /> Restart
        </button>
      </div>

      <div ref={scrollRef} className="pane-scroll">
        {inputRows.length > 0 && !chatId && (
          <div className="card" style={{ padding: 14, boxShadow: 'none' }}>
            <p style={{ margin: '0 0 10px', fontSize: 12, color: 'var(--muted)', fontWeight: 600 }}>
              This assistant asks for inputs when it starts:
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {inputRows.map((row) => (
                <div key={row.name} className="field">
                  <label>
                    <span className="token">@{row.name}</span>
                    {row.required && <span style={{ color: 'var(--accent)' }}> *</span>}
                  </label>
                  {row.kind === 'choice' ? (
                    <select
                      className="input"
                      value={inputValues[row.name] ?? ''}
                      onChange={(e) => setInputValues({ ...inputValues, [row.name]: e.target.value })}
                    >
                      <option value="">Choose…</option>
                      {row.choices.map((c) => (
                        <option key={c} value={c}>
                          {c}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      className="input"
                      placeholder={row.description}
                      value={inputValues[row.name] ?? ''}
                      onChange={(e) => setInputValues({ ...inputValues, [row.name]: e.target.value })}
                    />
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {messages.map((m) => {
          if (m.role === 'user') {
            return (
              <div key={m.id} className="msg-user">
                {messageText(m.content)}
              </div>
            );
          }
          if (m.role === 'tool') {
            return (
              <span key={m.id} className="tool-call">
                <span className="tc-dot" /> used <b>{m.name || 'a tool'}</b>
              </span>
            );
          }
          if (m.role === 'assistant') {
            const text = messageText(m.content);
            if (!text && m.tool_calls?.length) return null; // pure tool-call turns render via their tool results
            return (
              <div key={m.id} className="msg-bot">
                <div className="bot-avatar" style={avatarStyle(agent.name)}>{initials(agent.name)}</div>
                <div className="bot-bubble">
                  <div className="bot-text">{text}</div>
                </div>
              </div>
            );
          }
          return null;
        })}

        {sending && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--faint)', fontSize: 12.5 }}>
            <Loader2 size={14} className="spin" /> Thinking…
          </div>
        )}
        {error && <div className="error-box">{error}</div>}
      </div>

      <form
        className="chat-input"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <input
          className="input"
          placeholder={missingRequired.length > 0 ? `Fill in ${missingRequired.map((r) => '@' + r.name).join(', ')} first…` : 'Test your assistant…'}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={sending}
        />
        <button className="btn btn-primary" disabled={sending || !input.trim() || missingRequired.length > 0} aria-label="Send">
          <Send size={14} />
        </button>
      </form>
      <div className="hint-strip">Tool use shows up as chips, so you can see exactly what it did.</div>
    </aside>
  );
}
