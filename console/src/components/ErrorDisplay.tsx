export function ErrorDisplay({ error, title }: { error: any; title?: string }) {
  if (!error) return null;

  const errorMessage = error?.message || error?.toString() || 'An unknown error occurred';

  return (
    <div className="p-3 bg-red-900/20 border border-red-800/30 rounded-lg text-sm text-red-400">
      {title && <p className="font-medium mb-1">{title}</p>}
      <pre className="text-xs whitespace-pre-wrap font-mono overflow-x-auto">
        {errorMessage}
      </pre>
    </div>
  );
}
