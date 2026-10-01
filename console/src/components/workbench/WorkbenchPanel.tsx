import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../../lib/api';
import type { WorkbenchFile } from '../../types';
import {
  Download,
  FileText,
  FolderOpen,
  Loader2,
  RefreshCw,
  Trash2,
  Upload,
  X,
} from 'lucide-react';

function formatSize(bytes: number | null): string {
  if (bytes === null || bytes === undefined) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function isTextual(contentType: string): boolean {
  return (
    contentType.startsWith('text/') ||
    [
      'application/json',
      'application/x-yaml',
      'application/sql',
      'application/javascript',
      'application/typescript',
      'application/xml',
    ].includes(contentType)
  );
}

function base64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

/** The chat's workbench: the working tree the agent reads, writes, and
 * executes against. Files uploaded here are visible to the agent's
 * workbench tools and materialize into its code executions. */
export function WorkbenchPanel({ chatId, onClose }: { chatId: string; onClose?: () => void }) {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<{ name: string; text: string } | null>(null);
  const [busyFile, setBusyFile] = useState<string | null>(null);

  const filesKey = ['workbench-files', chatId];
  const { data: files, isLoading, refetch, isFetching } = useQuery({
    queryKey: filesKey,
    queryFn: () => apiClient.listWorkbenchFiles(chatId),
  });

  const uploadMutation = useMutation({
    mutationFn: async (file: globalThis.File) => {
      const buffer = await file.arrayBuffer();
      let binary = '';
      const bytes = new Uint8Array(buffer);
      const chunk = 0x8000;
      for (let i = 0; i < bytes.length; i += chunk) {
        binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
      }
      return apiClient.uploadWorkbenchFile(chatId, {
        name: file.name,
        content_base64: btoa(binary),
        content_type: file.type || undefined,
      });
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: filesKey }),
  });

  const deleteMutation = useMutation({
    mutationFn: (path: string) => apiClient.deleteWorkbenchFile(chatId, path),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: filesKey }),
  });

  const openFile = async (file: WorkbenchFile) => {
    setBusyFile(file.name);
    try {
      const content = await apiClient.readWorkbenchFile(chatId, file.name);
      const bytes = base64ToBytes(content.content_base64);
      if (isTextual(content.content_type)) {
        setPreview({ name: file.name, text: new TextDecoder().decode(bytes) });
      } else {
        downloadBytes(file.name, bytes, content.content_type);
      }
    } finally {
      setBusyFile(null);
    }
  };

  const downloadFile = async (file: WorkbenchFile) => {
    setBusyFile(file.name);
    try {
      const content = await apiClient.readWorkbenchFile(chatId, file.name);
      downloadBytes(file.name, base64ToBytes(content.content_base64), content.content_type);
    } finally {
      setBusyFile(null);
    }
  };

  const downloadBytes = (name: string, bytes: Uint8Array, contentType: string) => {
    const blob = new Blob([bytes.buffer as ArrayBuffer], { type: contentType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name.split('/').pop() || name;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex flex-col h-full w-80 border-l border-line-soft bg-transparent">
      <div className="flex items-center justify-between px-3 py-2 border-b border-line-soft">
        <div className="flex items-center text-gray-100">
          <FolderOpen className="w-4 h-4 mr-2 text-primary-600" />
          <span className="text-sm font-semibold">Workbench</span>
        </div>
        <div className="flex items-center gap-1">
          <button
            title="Upload file"
            className="p-1.5 text-gray-400 hover:text-gray-100"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploadMutation.isPending}
          >
            {uploadMutation.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Upload className="w-4 h-4" />
            )}
          </button>
          <button
            title="Refresh"
            className="p-1.5 text-gray-400 hover:text-gray-100"
            onClick={() => refetch()}
          >
            <RefreshCw className={`w-4 h-4 ${isFetching ? 'animate-spin' : ''}`} />
          </button>
          {onClose && (
            <button title="Close" className="p-1.5 text-gray-400 hover:text-gray-100" onClick={onClose}>
              <X className="w-4 h-4" />
            </button>
          )}
        </div>
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) uploadMutation.mutate(f);
            e.target.value = '';
          }}
        />
      </div>

      {uploadMutation.isError && (
        <div className="px-3 py-2 text-xs text-red-400 border-b border-line-soft">
          Upload failed: {(uploadMutation.error as Error)?.message}
        </div>
      )}

      <div className="flex-1 min-h-0 overflow-y-auto">
        {isLoading ? (
          <div className="flex justify-center py-8">
            <Loader2 className="w-5 h-5 animate-spin text-primary-600" />
          </div>
        ) : !files || files.length === 0 ? (
          <p className="px-3 py-8 text-center text-xs text-gray-500">
            No files yet. The agent's working files and your uploads appear here.
          </p>
        ) : (
          <ul>
            {files.map((file) => (
              <li
                key={file.name}
                className="group flex items-center px-3 py-2 border-b border-line-soft hover:bg-white/5 cursor-pointer"
                onClick={() => openFile(file)}
              >
                <FileText className="w-4 h-4 mr-2 shrink-0 text-gray-500" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-gray-100 truncate" title={file.name}>
                    {file.name}
                  </p>
                  <p className="text-xs text-gray-500">
                    {formatSize(file.size_bytes)} · v{file.current_version} ·{' '}
                    {new Date(file.updated_at).toLocaleString()}
                  </p>
                </div>
                {busyFile === file.name ? (
                  <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
                ) : (
                  <span className="hidden group-hover:flex items-center gap-1">
                    <button
                      title="Download"
                      className="p-1 text-gray-400 hover:text-gray-100"
                      onClick={(e) => {
                        e.stopPropagation();
                        downloadFile(file);
                      }}
                    >
                      <Download className="w-4 h-4" />
                    </button>
                    <button
                      title="Delete"
                      className="p-1 text-gray-400 hover:text-red-400"
                      onClick={(e) => {
                        e.stopPropagation();
                        if (confirm(`Delete '${file.name}' from the workbench?`)) {
                          deleteMutation.mutate(file.name);
                        }
                      }}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      {preview && (
        <div className="h-1/2 flex flex-col border-t border-line-soft">
          <div className="flex items-center justify-between px-3 py-1.5 border-b border-line-soft">
            <span className="text-xs font-mono text-gray-400 truncate" title={preview.name}>
              {preview.name}
            </span>
            <button
              className="p-1 text-gray-400 hover:text-gray-100"
              onClick={() => setPreview(null)}
            >
              <X className="w-4 h-4" />
            </button>
          </div>
          <pre className="flex-1 min-h-0 overflow-auto px-3 py-2 text-xs text-gray-300 whitespace-pre-wrap break-words">
            {preview.text}
          </pre>
        </div>
      )}
    </div>
  );
}
