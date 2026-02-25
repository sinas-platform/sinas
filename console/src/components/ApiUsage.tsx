import { useState } from 'react';
import { Code, Terminal, ChevronDown, ChevronRight, Copy, Check } from 'lucide-react';

interface CodeSnippet {
  label: string;
  language: 'bash' | 'python';
  code: string;
}

interface ApiUsageProps {
  /** Collapsible section title */
  title?: string;
  /** HTTP/curl examples */
  curl: CodeSnippet[];
  /** Python SDK examples */
  sdk: CodeSnippet[];
  /** Start expanded */
  defaultOpen?: boolean;
}

function CodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="relative group">
      <pre className="text-xs text-gray-200 whitespace-pre-wrap font-mono overflow-x-auto bg-[#0d0d0d] p-3 rounded border border-white/[0.06] pr-10">
        <code>{code}</code>
      </pre>
      <button
        type="button"
        onClick={handleCopy}
        className="absolute top-2 right-2 p-1 rounded text-gray-500 hover:text-gray-400 hover:bg-[#1e1e1e] opacity-0 group-hover:opacity-100 transition-opacity"
        title="Copy to clipboard"
      >
        {copied ? <Check className="w-3.5 h-3.5 text-green-500" /> : <Copy className="w-3.5 h-3.5" />}
      </button>
    </div>
  );
}

export function ApiUsage({ title = 'API Usage', curl, sdk, defaultOpen = false }: ApiUsageProps) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const [activeTab, setActiveTab] = useState<'curl' | 'sdk'>('curl');

  const snippets = activeTab === 'curl' ? curl : sdk;

  return (
    <div className="border border-white/[0.06] rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center justify-between w-full px-4 py-3 text-left bg-[#0d0d0d] hover:bg-white/10 transition-colors"
      >
        <div className="flex items-center gap-2 text-sm font-medium text-gray-300">
          <Code className="w-4 h-4 text-gray-500" />
          {title}
        </div>
        {isOpen ? (
          <ChevronDown className="w-4 h-4 text-gray-500" />
        ) : (
          <ChevronRight className="w-4 h-4 text-gray-500" />
        )}
      </button>

      {isOpen && (
        <div className="p-4 border-t border-white/[0.06] space-y-3">
          {/* Tabs */}
          <div className="flex gap-1 bg-[#161616] rounded-md p-0.5 w-fit">
            <button
              type="button"
              onClick={() => setActiveTab('curl')}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded transition-colors ${
                activeTab === 'curl'
                  ? 'bg-[#161616] text-gray-100'
                  : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              <Terminal className="w-3 h-3" />
              HTTP
            </button>
            <button
              type="button"
              onClick={() => setActiveTab('sdk')}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded transition-colors ${
                activeTab === 'sdk'
                  ? 'bg-[#161616] text-gray-100'
                  : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              <Code className="w-3 h-3" />
              Python SDK
            </button>
          </div>

          {/* Snippets */}
          <div className="space-y-3">
            {snippets.map((snippet, i) => (
              <div key={`${activeTab}-${i}`}>
                {snippet.label && (
                  <p className="text-xs text-gray-500 mb-1">{snippet.label}</p>
                )}
                <CodeBlock code={snippet.code} />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
