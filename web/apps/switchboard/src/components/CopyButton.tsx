import { useState } from 'react';
import { Check, Copy } from 'lucide-react';

export function CopyButton({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard blocked (http, permissions): the text stays selectable.
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      aria-label={copied ? 'Copied' : label}
      title={label}
      className="flex h-11 w-11 flex-none items-center justify-center rounded-lg border border-line bg-surface-2 text-gray-200 hover:bg-hover-strong"
    >
      {copied ? <Check className="h-4 w-4 text-green-400" aria-hidden="true" /> : <Copy className="h-4 w-4" aria-hidden="true" />}
    </button>
  );
}
