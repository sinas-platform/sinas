import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams, Link } from 'react-router-dom';
import { apiClient } from '../lib/api';
import { Upload, Download, Trash2, ChevronDown, ChevronRight, ArrowLeft, File as FileIcon, Pencil, Search, X, Plus, Minus, Link2 } from 'lucide-react';
import { SchemaFormField } from '../components/SchemaFormField';
import { useState, useRef } from 'react';
import type { FileWithVersions, FileSearchResult } from '../types';
import { ErrorDisplay } from '../components/ErrorDisplay';

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

const fileToBase64 = (file: File): Promise<string> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.readAsDataURL(file);
    reader.onload = () => {
      const result = reader.result as string;
      const base64 = result.split(',')[1];
      resolve(base64);
    };
    reader.onerror = (error) => reject(error);
  });
};

export function CollectionDetail() {
  const { namespace, name } = useParams<{ namespace: string; name: string }>();
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(new Set());
  const [showUploadModal, setShowUploadModal] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const [uploadVisibility, setUploadVisibility] = useState<'private' | 'shared'>('private');
  const [uploadProgress, setUploadProgress] = useState<{ current: number; total: number } | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  // Metadata edit state
  const [metadataEditFile, setMetadataEditFile] = useState<FileWithVersions | null>(null);
  const [metadataEditValues, setMetadataEditValues] = useState<Record<string, any>>({});
  const [metadataJson, setMetadataJson] = useState('');
  const [metadataJsonError, setMetadataJsonError] = useState<string | null>(null);

  // Search state
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [metadataFilterValues, setMetadataFilterValues] = useState<Record<string, any>>({});
  const [freeformFilters, setFreeformFilters] = useState<{ key: string; value: string }[]>([]);
  const [searchResults, setSearchResults] = useState<FileSearchResult[] | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  const [copiedFileId, setCopiedFileId] = useState<string | null>(null);
  const [urlLoading, setUrlLoading] = useState<string | null>(null);

  const handleCopyUrl = async (file: FileWithVersions) => {
    setUrlLoading(file.id);
    try {
      const result = await apiClient.generateFileUrl(namespace!, name!, file.name);
      await navigator.clipboard.writeText(result.url);
      setCopiedFileId(file.id);
      setTimeout(() => setCopiedFileId(null), 2000);
    } catch (err) {
      console.error('Failed to generate URL:', err);
    } finally {
      setUrlLoading(null);
    }
  };

  const { data: collection, isLoading: collectionLoading, error: collectionError } = useQuery({
    queryKey: ['collection', namespace, name],
    queryFn: () => apiClient.getCollection(namespace!, name!),
    enabled: !!namespace && !!name,
    retry: false,
  });

  const { data: files, isLoading: filesLoading, error: filesError } = useQuery({
    queryKey: ['files', namespace, name],
    queryFn: () => apiClient.listFiles(namespace!, name!),
    enabled: !!namespace && !!name,
    retry: false,
  });

  const deleteMutation = useMutation({
    mutationFn: (filename: string) => apiClient.deleteFile(namespace!, name!, filename),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['files', namespace, name] });
    },
  });

  const metadataMutation = useMutation({
    mutationFn: ({ filename, metadata }: { filename: string; metadata: Record<string, any> }) =>
      apiClient.updateFileMetadata(namespace!, name!, filename, metadata),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['files', namespace, name] });
      setMetadataEditFile(null);
    },
  });

  const handleFileSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files;
    if (selected && selected.length > 0) {
      setPendingFiles(Array.from(selected));
      setUploadVisibility('private');
      setUploadError(null);
      setUploadProgress(null);
      setShowUploadModal(true);
    }
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const removePendingFile = (index: number) => {
    setPendingFiles((prev) => {
      const next = [...prev];
      next.splice(index, 1);
      return next;
    });
  };

  const handleUploadConfirm = async () => {
    if (pendingFiles.length === 0) return;
    setUploadError(null);
    setUploadProgress({ current: 0, total: pendingFiles.length });

    for (let i = 0; i < pendingFiles.length; i++) {
      setUploadProgress({ current: i + 1, total: pendingFiles.length });
      const file = pendingFiles[i];
      try {
        const base64 = await fileToBase64(file);
        await apiClient.uploadFile(namespace!, name!, {
          name: file.name,
          content_base64: base64,
          content_type: file.type || 'application/octet-stream',
          visibility: uploadVisibility,
        });
      } catch (err: any) {
        setUploadError(`Failed to upload "${file.name}": ${err.message || 'Unknown error'}`);
        setUploadProgress(null);
        return;
      }
    }

    queryClient.invalidateQueries({ queryKey: ['files', namespace, name] });
    setShowUploadModal(false);
    setPendingFiles([]);
    setUploadProgress(null);
  };

  const handleDownload = async (file: FileWithVersions, version?: number) => {
    try {
      const response = await apiClient.downloadFile(namespace!, name!, file.name, version);
      const byteCharacters = atob(response.content_base64);
      const byteNumbers = new Array(byteCharacters.length);
      for (let i = 0; i < byteCharacters.length; i++) {
        byteNumbers[i] = byteCharacters.charCodeAt(i);
      }
      const byteArray = new Uint8Array(byteNumbers);
      const blob = new Blob([byteArray], { type: response.content_type });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = file.name;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Download failed:', err);
    }
  };

  const handleDelete = (file: FileWithVersions) => {
    if (window.confirm(`Delete file "${file.name}"? This action cannot be undone.`)) {
      deleteMutation.mutate(file.name);
    }
  };

  const toggleExpanded = (fileId: string) => {
    setExpandedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(fileId)) {
        next.delete(fileId);
      } else {
        next.add(fileId);
      }
      return next;
    });
  };

  const openMetadataEdit = (file: FileWithVersions) => {
    setMetadataEditFile(file);
    setMetadataEditValues({ ...file.file_metadata });
    setMetadataJson(JSON.stringify(file.file_metadata, null, 2));
    setMetadataJsonError(null);
  };

  const handleMetadataSave = () => {
    if (!metadataEditFile) return;
    if (hasSchema) {
      metadataMutation.mutate({ filename: metadataEditFile.name, metadata: metadataEditValues });
    } else {
      try {
        const parsed = JSON.parse(metadataJson);
        setMetadataJsonError(null);
        metadataMutation.mutate({ filename: metadataEditFile.name, metadata: parsed });
      } catch {
        setMetadataJsonError('Invalid JSON');
      }
    }
  };

  const schemaProperties = collection?.metadata_schema?.properties as Record<string, any> | undefined;
  const hasSchema = schemaProperties && Object.keys(schemaProperties).length > 0;

  const handleSearch = async () => {
    const metaFilter: Record<string, any> = {};

    if (hasSchema) {
      for (const [key, val] of Object.entries(metadataFilterValues)) {
        if (val !== '' && val !== undefined && val !== null) metaFilter[key] = val;
      }
    } else {
      for (const f of freeformFilters) {
        if (f.key) metaFilter[f.key] = f.value;
      }
    }

    if (!searchQuery && Object.keys(metaFilter).length === 0) return;

    setSearchLoading(true);
    setSearchError(null);
    setSearchResults(null);

    try {
      const results = await apiClient.searchFiles(namespace!, name!, {
        query: searchQuery || undefined,
        metadata_filter: Object.keys(metaFilter).length > 0 ? metaFilter : undefined,
      });
      setSearchResults(results);
    } catch (err: any) {
      setSearchError(err.message || 'Search failed');
    } finally {
      setSearchLoading(false);
    }
  };

  // Build a map of search results by file ID for inline display
  const searchResultMap = new Map<string, FileSearchResult>();
  if (searchResults) {
    for (const r of searchResults) searchResultMap.set(r.file_id, r);
  }

  // When search is active, filter file list to only matching files
  const displayFiles = searchResults !== null && files
    ? files.filter((f) => searchResultMap.has(f.id))
    : files;

  if (collectionLoading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto"></div>
      </div>
    );
  }

  if (collectionError) {
    return (
      <div className="space-y-6">
        <Link to="/collections" className="inline-flex items-center text-gray-600 hover:text-gray-900">
          <ArrowLeft className="w-4 h-4 mr-1" />
          Back to Collections
        </Link>
        <ErrorDisplay error={collectionError} title="Failed to load collection" />
      </div>
    );
  }

  if (!collection) {
    return (
      <div className="text-center py-12">
        <h2 className="text-xl font-semibold text-gray-900">Collection not found</h2>
        <Link to="/collections" className="text-primary-600 hover:text-primary-700 mt-2 inline-block">
          Back to collections
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center">
          <Link to="/collections" className="mr-4 text-gray-600 hover:text-gray-900">
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <div>
            <h1 className="text-3xl font-bold text-gray-900">
              {collection.namespace}/{collection.name}
            </h1>
            <p className="text-gray-600 mt-1">
              Max file size: {collection.max_file_size_mb} MB
              {' | '}Max total: {collection.max_total_size_gb} GB
              {collection.allow_shared_files && ' | Shared files allowed'}
              {collection.allow_private_files && ' | Private files allowed'}
            </p>
          </div>
        </div>
        <div>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={handleFileSelected}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            className="btn btn-primary flex items-center"
          >
            <Upload className="w-4 h-4 mr-2" />
            Upload Files
          </button>
        </div>
      </div>

      {/* Search Panel */}
      <div className="card">
        <button
          onClick={() => setSearchOpen(!searchOpen)}
          className="flex items-center gap-2 text-sm font-medium text-gray-700 hover:text-gray-900 w-full"
        >
          {searchOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          <Search className="w-4 h-4" />
          Search Files
        </button>

        {searchOpen && (
          <div className="mt-4 space-y-4">
            {/* Content search */}
            <div>
              <label className="label">Content Search (regex)</label>
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="e.g. function\s+\w+"
                className="input w-full"
                onKeyDown={(e) => { if (e.key === 'Enter') handleSearch(); }}
              />
            </div>

            {/* Metadata filters */}
            <div>
              <label className="label">Metadata Filters</label>
              {hasSchema ? (
                <div className="bg-gray-50 border border-gray-200 rounded-lg p-3">
                  {Object.entries(schemaProperties!).map(([propName, propSchema]) => (
                    <SchemaFormField
                      key={propName}
                      name={propName}
                      schema={propSchema as any}
                      value={metadataFilterValues[propName] ?? ''}
                      onChange={(val) =>
                        setMetadataFilterValues((prev) => ({ ...prev, [propName]: val }))
                      }
                    />
                  ))}
                </div>
              ) : (
                <>
                  <div className="flex justify-end mb-2">
                    <button
                      onClick={() => setFreeformFilters([...freeformFilters, { key: '', value: '' }])}
                      className="btn btn-sm btn-secondary flex items-center gap-1"
                    >
                      <Plus className="w-3 h-3" /> Add
                    </button>
                  </div>
                  {freeformFilters.map((filter, i) => (
                    <div key={i} className="flex items-center gap-2 mb-2">
                      <input
                        type="text"
                        value={filter.key}
                        onChange={(e) => {
                          const next = [...freeformFilters];
                          next[i] = { ...next[i], key: e.target.value };
                          setFreeformFilters(next);
                        }}
                        placeholder="Key"
                        className="input flex-1"
                      />
                      <input
                        type="text"
                        value={filter.value}
                        onChange={(e) => {
                          const next = [...freeformFilters];
                          next[i] = { ...next[i], value: e.target.value };
                          setFreeformFilters(next);
                        }}
                        placeholder="Value"
                        className="input flex-1"
                      />
                      <button
                        onClick={() => setFreeformFilters(freeformFilters.filter((_, j) => j !== i))}
                        className="btn btn-sm btn-danger"
                      >
                        <Minus className="w-3 h-3" />
                      </button>
                    </div>
                  ))}
                </>
              )}
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={handleSearch}
                disabled={searchLoading}
                className="btn btn-primary flex items-center gap-2"
              >
                <Search className="w-4 h-4" />
                {searchLoading ? 'Searching...' : 'Search'}
              </button>
              {searchResults !== null && (
                <button
                  onClick={() => { setSearchResults(null); setSearchQuery(''); setMetadataFilterValues({}); setFreeformFilters([]); setSearchError(null); }}
                  className="btn btn-secondary"
                >
                  Clear
                </button>
              )}
            </div>

            {searchError && (
              <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded p-3">{searchError}</div>
            )}

            {searchResults !== null && (
              <div className="text-sm text-gray-600 bg-gray-50 border border-gray-200 rounded px-3 py-2">
                {searchResults.length} file{searchResults.length !== 1 ? 's' : ''} matched
              </div>
            )}
          </div>
        )}
      </div>

      {/* File list */}
      <div className="card">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-gray-900">Files</h2>
          <span className="text-sm text-gray-500">{files?.length || 0} file(s)</span>
        </div>

        {filesError && (
          <ErrorDisplay error={filesError} title="Failed to load files" />
        )}

        {filesLoading ? (
          <div className="text-center py-8 text-gray-500">Loading files...</div>
        ) : !files || files.length === 0 ? (
          <div className="text-center py-12">
            <FileIcon className="w-16 h-16 text-gray-300 mx-auto mb-4" />
            <h3 className="text-lg font-medium text-gray-900 mb-2">No files yet</h3>
            <p className="text-gray-600">Upload your first file.</p>
          </div>
        ) : displayFiles && displayFiles.length === 0 ? (
          <div className="text-center py-8 text-gray-500">No files match your search.</div>
        ) : (
          <div className="space-y-2">
            {(displayFiles || files).map((file: FileWithVersions) => {
              const isExpanded = expandedFiles.has(file.id);
              const latestVersion = file.versions?.length > 0
                ? file.versions.reduce((a, b) => a.version_number > b.version_number ? a : b)
                : null;
              const hasMetadata = file.file_metadata && Object.keys(file.file_metadata).length > 0;
              const contentMatches = searchResultMap.get(file.id)?.matches;

              return (
                <div
                  key={file.id}
                  className="border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors"
                >
                  <div className="p-4 flex items-center justify-between">
                    <div className="flex items-center flex-1 min-w-0">
                      {/* Expand toggle */}
                      {file.versions && file.versions.length > 0 ? (
                        <button
                          onClick={() => toggleExpanded(file.id)}
                          className="p-1 text-gray-400 hover:text-gray-600 mr-2"
                        >
                          {isExpanded ? (
                            <ChevronDown className="w-4 h-4" />
                          ) : (
                            <ChevronRight className="w-4 h-4" />
                          )}
                        </button>
                      ) : (
                        <div className="w-6 mr-2" />
                      )}

                      <FileIcon className="w-5 h-5 text-gray-400 mr-3 flex-shrink-0" />

                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-3 flex-wrap">
                          <span className="text-sm font-medium text-gray-900 truncate">
                            {file.name}
                          </span>
                          <span className="text-xs text-gray-500">
                            {file.content_type}
                          </span>
                          {latestVersion && (
                            <span className="text-xs text-gray-500">
                              {formatFileSize(latestVersion.size_bytes)}
                            </span>
                          )}
                          <span className="text-xs text-gray-500">
                            v{file.current_version}
                          </span>
                          <span
                            className={`px-2 py-0.5 text-xs font-medium rounded ${
                              file.visibility === 'shared'
                                ? 'bg-blue-50 text-blue-600'
                                : 'bg-gray-100 text-gray-600'
                            }`}
                          >
                            {file.visibility}
                          </span>
                          {contentMatches && contentMatches.length > 0 && (
                            <span className="px-2 py-0.5 text-xs font-medium rounded bg-yellow-50 text-yellow-700">
                              {contentMatches.length} match{contentMatches.length !== 1 ? 'es' : ''}
                            </span>
                          )}
                        </div>
                        <div className="text-xs text-gray-500 mt-1">
                          Updated {new Date(file.updated_at).toLocaleString()}
                        </div>
                        {/* Metadata pills */}
                        {hasMetadata && (
                          <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
                            {Object.entries(file.file_metadata).map(([k, v]) => (
                              <span
                                key={k}
                                className="inline-flex items-center px-2 py-0.5 text-xs bg-purple-50 text-purple-700 rounded"
                              >
                                <span className="font-medium">{k}:</span>
                                <span className="ml-1">{typeof v === 'string' ? v : JSON.stringify(v)}</span>
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center gap-2 ml-4">
                      <button
                        onClick={() => openMetadataEdit(file)}
                        className="btn btn-sm btn-secondary"
                        title="Edit metadata"
                      >
                        <Pencil className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => handleDownload(file)}
                        className="btn btn-sm btn-secondary"
                        title="Download latest version"
                      >
                        <Download className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => handleCopyUrl(file)}
                        className={`btn btn-sm ${copiedFileId === file.id ? 'btn-primary' : 'btn-secondary'}`}
                        title="Generate & copy temporary public URL"
                        disabled={urlLoading === file.id}
                      >
                        {urlLoading === file.id ? (
                          <div className="w-4 h-4 border-2 border-gray-400 border-t-transparent rounded-full animate-spin" />
                        ) : copiedFileId === file.id ? (
                          <span className="text-xs">Copied!</span>
                        ) : (
                          <Link2 className="w-4 h-4" />
                        )}
                      </button>
                      <button
                        onClick={() => handleDelete(file)}
                        className="btn btn-sm btn-danger"
                        title="Delete file"
                        disabled={deleteMutation.isPending}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>

                  {/* Content matches from search */}
                  {contentMatches && contentMatches.length > 0 && (
                    <div className="border-t border-gray-200 bg-yellow-50/50 px-4 py-3 space-y-2">
                      <h4 className="text-xs font-semibold text-gray-700 uppercase tracking-wide">
                        Content Matches
                      </h4>
                      {contentMatches.map((match, mi) => (
                        <div key={mi} className="text-xs">
                          <span className="text-gray-500 font-medium">Line {match.line}:</span>
                          <pre className="mt-1 bg-white border border-gray-200 rounded p-2 overflow-x-auto font-mono text-gray-700">
                            {match.context.map((line, li) => (
                              <div
                                key={li}
                                className={line === match.text ? 'bg-yellow-100 -mx-2 px-2' : ''}
                              >
                                {line}
                              </div>
                            ))}
                          </pre>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Version history */}
                  {isExpanded && file.versions && file.versions.length > 0 && (
                    <div className="border-t border-gray-200 bg-gray-50 px-4 py-3">
                      <h4 className="text-xs font-semibold text-gray-700 mb-2 uppercase tracking-wide">
                        Version History
                      </h4>
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="text-gray-500">
                            <th className="text-left py-1 pr-4 font-medium">Version</th>
                            <th className="text-left py-1 pr-4 font-medium">Size</th>
                            <th className="text-left py-1 pr-4 font-medium">SHA-256</th>
                            <th className="text-left py-1 pr-4 font-medium">Date</th>
                            <th className="text-right py-1 font-medium">Actions</th>
                          </tr>
                        </thead>
                        <tbody>
                          {file.versions
                            .sort((a, b) => b.version_number - a.version_number)
                            .map((version) => (
                              <tr key={version.id} className="border-t border-gray-200">
                                <td className="py-1.5 pr-4 text-gray-900">v{version.version_number}</td>
                                <td className="py-1.5 pr-4 text-gray-600">{formatFileSize(version.size_bytes)}</td>
                                <td className="py-1.5 pr-4 text-gray-600 font-mono">
                                  {version.hash_sha256.substring(0, 16)}...
                                </td>
                                <td className="py-1.5 pr-4 text-gray-600">
                                  {new Date(version.created_at).toLocaleString()}
                                </td>
                                <td className="py-1.5 text-right">
                                  <button
                                    onClick={() => handleDownload(file, version.version_number)}
                                    className="text-blue-600 hover:text-blue-800"
                                    title={`Download v${version.version_number}`}
                                  >
                                    <Download className="w-3.5 h-3.5 inline" />
                                  </button>
                                </td>
                              </tr>
                            ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Upload Modal */}
      {showUploadModal && pendingFiles.length > 0 && (
        <>
          <div
            className="fixed inset-0 bg-black/20 backdrop-blur-sm z-50"
            onClick={() => {
              if (!uploadProgress) {
                setShowUploadModal(false);
                setPendingFiles([]);
              }
            }}
          />
          <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
            <div
              className="bg-white rounded-lg shadow-xl max-w-lg w-full max-h-[90vh] overflow-y-auto p-6 pointer-events-auto"
              onClick={(e) => e.stopPropagation()}
            >
              <h2 className="text-xl font-semibold text-gray-900 mb-4">
                Upload {pendingFiles.length} File{pendingFiles.length !== 1 ? 's' : ''}
              </h2>

              <div className="space-y-4">
                <div>
                  <label className="label">Files</label>
                  <div className="space-y-1 max-h-48 overflow-y-auto">
                    {pendingFiles.map((file, i) => (
                      <div key={i} className="flex items-center justify-between text-sm bg-gray-50 border border-gray-200 rounded px-3 py-2">
                        <div className="font-mono truncate flex-1 mr-2">
                          {file.name}
                          <span className="text-gray-500 ml-2">
                            ({formatFileSize(file.size)})
                          </span>
                        </div>
                        {!uploadProgress && (
                          <button
                            onClick={() => removePendingFile(i)}
                            className="text-gray-400 hover:text-red-500 flex-shrink-0"
                          >
                            <X className="w-4 h-4" />
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                </div>

                <div>
                  <label className="label">Visibility</label>
                  <div className="flex items-center gap-4">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="radio"
                        name="visibility"
                        value="private"
                        checked={uploadVisibility === 'private'}
                        onChange={() => setUploadVisibility('private')}
                        className="text-primary-600 focus:ring-primary-500"
                        disabled={!!uploadProgress}
                      />
                      <span className="text-sm text-gray-700">Private</span>
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input
                        type="radio"
                        name="visibility"
                        value="shared"
                        checked={uploadVisibility === 'shared'}
                        onChange={() => setUploadVisibility('shared')}
                        className="text-primary-600 focus:ring-primary-500"
                        disabled={!collection.allow_shared_files || !!uploadProgress}
                      />
                      <span className={`text-sm ${collection.allow_shared_files ? 'text-gray-700' : 'text-gray-400'}`}>
                        Shared
                      </span>
                    </label>
                  </div>
                  <p className="text-xs text-gray-500 mt-1">
                    Private files are only accessible by you. Shared files can be accessed by others with collection permissions.
                  </p>
                </div>

                {uploadProgress && (
                  <div className="text-sm text-blue-700 bg-blue-50 border border-blue-200 rounded p-3">
                    Uploading {uploadProgress.current}/{uploadProgress.total}...
                  </div>
                )}

                {uploadError && (
                  <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded p-3">{uploadError}</div>
                )}

                <div className="flex justify-end gap-2 pt-4 border-t border-gray-200">
                  <button
                    type="button"
                    onClick={() => {
                      if (!uploadProgress) {
                        setShowUploadModal(false);
                        setPendingFiles([]);
                      }
                    }}
                    className="btn btn-secondary"
                    disabled={!!uploadProgress}
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleUploadConfirm}
                    disabled={!!uploadProgress || pendingFiles.length === 0}
                    className="btn btn-primary flex items-center"
                  >
                    {uploadProgress ? (
                      `Uploading ${uploadProgress.current}/${uploadProgress.total}...`
                    ) : (
                      <>
                        <Upload className="w-4 h-4 mr-2" />
                        Upload
                      </>
                    )}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </>
      )}

      {/* Metadata Edit Modal */}
      {metadataEditFile && (
        <>
          <div
            className="fixed inset-0 bg-black/20 backdrop-blur-sm z-50"
            onClick={() => setMetadataEditFile(null)}
          />
          <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
            <div
              className="bg-white rounded-lg shadow-xl max-w-lg w-full max-h-[90vh] overflow-y-auto p-6 pointer-events-auto"
              onClick={(e) => e.stopPropagation()}
            >
              <h2 className="text-xl font-semibold text-gray-900 mb-4">
                Edit Metadata: {metadataEditFile.name}
              </h2>

              <div className="space-y-4">
                {hasSchema ? (
                  <div>
                    {Object.entries(schemaProperties!).map(([propName, propSchema]) => (
                      <SchemaFormField
                        key={propName}
                        name={propName}
                        schema={propSchema as any}
                        value={metadataEditValues[propName] ?? ''}
                        onChange={(val) =>
                          setMetadataEditValues((prev) => ({ ...prev, [propName]: val }))
                        }
                        required={(collection.metadata_schema?.required as string[] || []).includes(propName)}
                      />
                    ))}
                  </div>
                ) : (
                  <div>
                    <label className="label">Metadata (JSON)</label>
                    <textarea
                      value={metadataJson}
                      onChange={(e) => {
                        setMetadataJson(e.target.value);
                        setMetadataJsonError(null);
                      }}
                      className="input w-full font-mono text-sm"
                      rows={10}
                      spellCheck={false}
                    />
                    {metadataJsonError && (
                      <p className="text-xs text-red-600 mt-1">{metadataJsonError}</p>
                    )}
                  </div>
                )}

                {metadataMutation.isError && (
                  <ErrorDisplay error={metadataMutation.error} title="Failed to update metadata" />
                )}

                <div className="flex justify-end gap-2 pt-4 border-t border-gray-200">
                  <button
                    type="button"
                    onClick={() => setMetadataEditFile(null)}
                    className="btn btn-secondary"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleMetadataSave}
                    disabled={metadataMutation.isPending}
                    className="btn btn-primary"
                  >
                    {metadataMutation.isPending ? 'Saving...' : 'Save'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
