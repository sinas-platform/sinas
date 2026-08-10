let injected = false;

export function injectMarkdownStyles(): void {
  if (injected || typeof document === 'undefined') return;
  injected = true;

  const css = `
.sinas-chat-md {
  line-height: 1.6;
  word-wrap: break-word;
  overflow-wrap: break-word;
}
.sinas-chat-md > *:first-child { margin-top: 0; }
.sinas-chat-md > *:last-child { margin-bottom: 0; }

.sinas-chat-md h1,
.sinas-chat-md h2,
.sinas-chat-md h3,
.sinas-chat-md h4 {
  margin: 16px 0 8px;
  font-weight: 600;
  line-height: 1.3;
  color: var(--sinas-color-text, #ededed);
}
.sinas-chat-md h1 { font-size: 1.4em; }
.sinas-chat-md h2 { font-size: 1.2em; }
.sinas-chat-md h3 { font-size: 1.1em; }
.sinas-chat-md h4 { font-size: 1em; }

.sinas-chat-md p { margin: 8px 0; }

.sinas-chat-md a {
  color: var(--sinas-color-primary, #f97316);
  text-decoration: none;
}
.sinas-chat-md a:hover { text-decoration: underline; }

.sinas-chat-md code {
  font-family: var(--sinas-font-mono, ui-monospace, 'SF Mono', monospace);
  font-size: 0.88em;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--hover-strong);
}

.sinas-chat-md pre {
  margin: 10px 0;
  padding: 12px 14px;
  border-radius: 8px;
  background: rgba(0, 0, 0, 0.35);
  overflow-x: auto;
}
.sinas-chat-md pre code {
  padding: 0;
  background: none;
  font-size: 0.85em;
  line-height: 1.5;
}

.sinas-chat-md ul,
.sinas-chat-md ol {
  margin: 8px 0;
  padding-left: 24px;
}
.sinas-chat-md li { margin: 4px 0; }
.sinas-chat-md li > p { margin: 4px 0; }

.sinas-chat-md blockquote {
  margin: 10px 0;
  padding: 8px 14px;
  border-left: 3px solid var(--sinas-color-primary, #f97316);
  background: var(--hover);
  color: var(--sinas-color-text-muted, #888888);
}
.sinas-chat-md blockquote > *:first-child { margin-top: 0; }
.sinas-chat-md blockquote > *:last-child { margin-bottom: 0; }

.sinas-chat-md table {
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0;
  font-size: 0.9em;
}
.sinas-chat-md th,
.sinas-chat-md td {
  padding: 8px 12px;
  border: 1px solid var(--line);
  text-align: left;
}
.sinas-chat-md th {
  background: var(--hover);
  font-weight: 600;
}

.sinas-chat-md hr {
  border: none;
  border-top: 1px solid var(--line);
  margin: 16px 0;
}

.sinas-chat-md img {
  max-width: 100%;
  border-radius: 6px;
}
`;

  const style = document.createElement('style');
  style.id = 'sinas-chat-markdown';
  style.textContent = css;
  document.head.appendChild(style);
}
