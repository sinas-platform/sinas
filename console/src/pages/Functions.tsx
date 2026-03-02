import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Code, Plus, Trash2, Edit2, PackageOpen, ChevronDown, ChevronRight, Search, Filter, Upload, Play, X, AlertCircle, Check, Globe, FileText } from 'lucide-react';
import { useState, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { SchemaFormField } from '../components/SchemaFormField';
import type { OpenAPIImportRequest, OpenAPIImportResponse } from '../types';

export function Functions() {
  const queryClient = useQueryClient();
  const [showDependencyModal, setShowDependencyModal] = useState(false);
  const [dependencyName, setDependencyName] = useState('');
  const [expandedFunctions, setExpandedFunctions] = useState<Set<string>>(new Set());
  const [searchFilter, setSearchFilter] = useState('');
  const [showExecuteModal, setShowExecuteModal] = useState(false);
  const [executeFunc, setExecuteFunc] = useState<any>(null);
  const [executeInputParams, setExecuteInputParams] = useState<Record<string, any>>({});
  const [executeResult, setExecuteResult] = useState<any>(null);

  // OpenAPI Import state
  const [showImportModal, setShowImportModal] = useState(false);
  const [importStep, setImportStep] = useState<'input' | 'preview' | 'done'>('input');
  const [importSpecMode, setImportSpecMode] = useState<'paste' | 'url'>('paste');
  const [importSpec, setImportSpec] = useState('');
  const [importSpecUrl, setImportSpecUrl] = useState('');
  const [importNamespace, setImportNamespace] = useState('');
  const [importBaseUrl, setImportBaseUrl] = useState('');
  const [importAuthType, setImportAuthType] = useState('none');
  const [importAuthHeader, setImportAuthHeader] = useState('Authorization');
  const [importAuthStateNs, setImportAuthStateNs] = useState('api_keys');
  const [importAuthStateKey, setImportAuthStateKey] = useState('');
  const [importPreview, setImportPreview] = useState<OpenAPIImportResponse | null>(null);
  const [importSelected, setImportSelected] = useState<Set<string>>(new Set());
  const [importExpandedCode, setImportExpandedCode] = useState<Set<string>>(new Set());
  const [importError, setImportError] = useState('');

  const { data: functions, isLoading } = useQuery({
    queryKey: ['functions'],
    queryFn: () => apiClient.listFunctions(),
    retry: false,
  });

  const { data: dependencies } = useQuery({
    queryKey: ['dependencies'],
    queryFn: () => apiClient.listDependencies(),
    retry: false,
  });

  const { data: collections } = useQuery({
    queryKey: ['collections'],
    queryFn: () => apiClient.listCollections(),
    retry: false,
  });

  // Build lookup: function identifier -> collection trigger roles
  const functionTriggerRoles = useMemo(() => {
    const roles: Record<string, { contentFilter: string[]; postUpload: string[] }> = {};
    if (!collections) return roles;
    for (const coll of collections) {
      const collName = `${coll.namespace}/${coll.name}`;
      if (coll.content_filter_function) {
        if (!roles[coll.content_filter_function]) roles[coll.content_filter_function] = { contentFilter: [], postUpload: [] };
        roles[coll.content_filter_function].contentFilter.push(collName);
      }
      if (coll.post_upload_function) {
        if (!roles[coll.post_upload_function]) roles[coll.post_upload_function] = { contentFilter: [], postUpload: [] };
        roles[coll.post_upload_function].postUpload.push(collName);
      }
    }
    return roles;
  }, [collections]);

  const deleteMutation = useMutation({
    mutationFn: ({ namespace, name }: { namespace: string; name: string }) => apiClient.deleteFunction(namespace, name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['functions'] });
    },
  });

  const installDependencyMutation = useMutation({
    mutationFn: (data: any) => apiClient.installDependency(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dependencies'] });
      setShowDependencyModal(false);
      setDependencyName('');
    },
  });

  const deleteDependencyMutation = useMutation({
    mutationFn: (dependencyId: string) => apiClient.deleteDependency(dependencyId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dependencies'] });
    },
  });

  const reloadWorkersMutation = useMutation({
    mutationFn: () => apiClient.reloadWorkers(),
  });

  const importParseMutation = useMutation({
    mutationFn: (data: OpenAPIImportRequest) => apiClient.importOpenAPI(data),
    onSuccess: (data: OpenAPIImportResponse) => {
      setImportPreview(data);
      setImportNamespace(data.namespace);
      // Select all will_create operations by default
      const selectableOps = new Set(
        data.functions
          .filter(f => f.status === 'will_create')
          .map(f => f.operation_id || f.name)
      );
      setImportSelected(selectableOps);
      setImportStep('preview');
      setImportError('');
    },
    onError: (error: any) => {
      setImportError(error?.response?.data?.detail || 'Failed to parse OpenAPI spec');
    },
  });

  const importCreateMutation = useMutation({
    mutationFn: (data: OpenAPIImportRequest) => apiClient.importOpenAPI(data),
    onSuccess: (data: OpenAPIImportResponse) => {
      setImportPreview(data);
      setImportStep('done');
      queryClient.invalidateQueries({ queryKey: ['functions'] });
    },
    onError: (error: any) => {
      setImportError(error?.response?.data?.detail || 'Failed to import functions');
    },
  });

  const executeMutation = useMutation({
    mutationFn: ({ namespace, name, input }: { namespace: string; name: string; input: any }) =>
      apiClient.executeFunction(namespace, name, input),
    onSuccess: (data: any) => {
      setExecuteResult(data);
    },
    onError: (error: any) => {
      setExecuteResult({ status: 'error', error: error?.response?.data?.detail || 'Function execution failed' });
    },
  });

  const handleExecute = (func: any) => {
    const inputSchema = func.input_schema;
    const hasInputParams = inputSchema?.properties && Object.keys(inputSchema.properties).length > 0;
    setExecuteResult(null);

    if (hasInputParams) {
      setExecuteFunc(func);
      setExecuteInputParams({});
      setShowExecuteModal(true);
    } else {
      // Open modal immediately to show output
      setExecuteFunc(func);
      setExecuteInputParams({});
      setShowExecuteModal(true);
      executeMutation.mutate({ namespace: func.namespace, name: func.name, input: {} });
    }
  };

  const handleExecuteModalSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!executeFunc) return;
    setExecuteResult(null);
    executeMutation.mutate({
      namespace: executeFunc.namespace,
      name: executeFunc.name,
      input: executeInputParams,
    });
  };

  const closeExecuteModal = () => {
    setShowExecuteModal(false);
    setExecuteFunc(null);
    setExecuteInputParams({});
    setExecuteResult(null);
  };

  const closeImportModal = () => {
    setShowImportModal(false);
    setImportStep('input');
    setImportSpec('');
    setImportSpecUrl('');
    setImportNamespace('');
    setImportBaseUrl('');
    setImportAuthType('none');
    setImportAuthHeader('Authorization');
    setImportAuthStateNs('api_keys');
    setImportAuthStateKey('');
    setImportPreview(null);
    setImportSelected(new Set());
    setImportExpandedCode(new Set());
    setImportError('');
    importParseMutation.reset();
    importCreateMutation.reset();
  };

  const handleImportParse = () => {
    setImportError('');
    importParseMutation.mutate({
      spec: importSpecMode === 'paste' ? importSpec : undefined,
      spec_url: importSpecMode === 'url' ? importSpecUrl : undefined,
      namespace: importNamespace || undefined,
      base_url_override: importBaseUrl || undefined,
      auth_type: importAuthType,
      auth_header: importAuthHeader,
      auth_state_namespace: importAuthType !== 'none' ? importAuthStateNs : undefined,
      auth_state_key: importAuthType !== 'none' ? importAuthStateKey : undefined,
      dry_run: true,
    });
  };

  const handleImportCreate = () => {
    setImportError('');
    const selectedOps = Array.from(importSelected);
    importCreateMutation.mutate({
      spec: importSpecMode === 'paste' ? importSpec : undefined,
      spec_url: importSpecMode === 'url' ? importSpecUrl : undefined,
      namespace: importNamespace || undefined,
      base_url_override: importBaseUrl || undefined,
      auth_type: importAuthType,
      auth_header: importAuthHeader,
      auth_state_namespace: importAuthType !== 'none' ? importAuthStateNs : undefined,
      auth_state_key: importAuthType !== 'none' ? importAuthStateKey : undefined,
      operations: selectedOps.length > 0 ? selectedOps : undefined,
      dry_run: false,
    });
  };

  const toggleImportSelectAll = () => {
    if (!importPreview) return;
    const creatableOps = importPreview.functions
      .filter(f => f.status === 'will_create')
      .map(f => f.operation_id || f.name);
    if (importSelected.size === creatableOps.length) {
      setImportSelected(new Set());
    } else {
      setImportSelected(new Set(creatableOps));
    }
  };

  const toggleImportOperation = (opId: string) => {
    const newSelected = new Set(importSelected);
    if (newSelected.has(opId)) {
      newSelected.delete(opId);
    } else {
      newSelected.add(opId);
    }
    setImportSelected(newSelected);
  };

  const handleInstallDependency = (e: React.FormEvent) => {
    e.preventDefault();
    if (dependencyName.trim()) {
      installDependencyMutation.mutate({ package_name: dependencyName.trim() });
    }
  };

  const toggleFunctionExpanded = (funcId: string) => {
    const newExpanded = new Set(expandedFunctions);
    if (newExpanded.has(funcId)) {
      newExpanded.delete(funcId);
    } else {
      newExpanded.add(funcId);
    }
    setExpandedFunctions(newExpanded);
  };

  // Filter functions based on search
  const filteredFunctions = useMemo(() => {
    if (!functions) return [];
    if (!searchFilter.trim()) return functions;

    const search = searchFilter.toLowerCase();
    return functions.filter((func: any) =>
      `${func.namespace}/${func.name}`.toLowerCase().includes(search) ||
      func.description?.toLowerCase().includes(search)
    );
  }, [functions, searchFilter]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-100">Functions</h1>
          <p className="text-gray-400 mt-1">Create and manage Python functions</p>
        </div>
        <div className="flex space-x-3">
          <button
            onClick={() => setShowImportModal(true)}
            className="btn btn-secondary flex items-center"
          >
            <Globe className="w-5 h-5 mr-2" />
            Import OpenAPI
          </button>
          <button
            onClick={() => setShowDependencyModal(true)}
            className="btn btn-secondary flex items-center"
          >
            <PackageOpen className="w-5 h-5 mr-2" />
            Dependencies
          </button>
          <Link to="/functions/new/new" className="btn btn-primary flex items-center">
            <Plus className="w-5 h-5 mr-2" />
            New Function
          </Link>
        </div>
      </div>

      {/* Search Bar */}
      {functions && functions.length > 0 && (
        <div className="relative">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-500 pointer-events-none" />
          <input
            type="text"
            value={searchFilter}
            onChange={(e) => setSearchFilter(e.target.value)}
            placeholder="Search functions by name, namespace, or description..."
            className="input w-full !pl-11"
          />
        </div>
      )}

      {/* Dependencies Section */}
      {dependencies && dependencies.length > 0 && (
        <div className="card bg-[#0d0d0d]">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-sm font-medium text-gray-300">Installed Dependencies ({dependencies.length})</h3>
            <button
              onClick={() => reloadWorkersMutation.mutate()}
              disabled={reloadWorkersMutation.isPending}
              className="btn btn-secondary text-xs py-1 px-3"
            >
              {reloadWorkersMutation.isPending ? 'Reloading...' : 'Reload Workers'}
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            {dependencies.map((dep: any) => (
              <div key={dep.id} className="flex items-center bg-[#161616] px-3 py-1 rounded border border-white/[0.06]">
                <span className="text-sm font-mono">{dep.package_name}</span>
                <span className="text-xs text-gray-500 ml-2">{dep.version}</span>
                <button
                  onClick={() => deleteDependencyMutation.mutate(dep.id)}
                  className="ml-2 text-red-600 hover:text-red-400"
                  disabled={deleteDependencyMutation.isPending}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
          {reloadWorkersMutation.isSuccess && (
            <div className="mt-2 text-sm text-green-600">Workers reloaded successfully</div>
          )}
          {reloadWorkersMutation.isError && (
            <div className="mt-2 text-sm text-red-600">Failed to reload workers</div>
          )}
        </div>
      )}

      {/* Functions List */}
      {isLoading ? (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
        </div>
      ) : filteredFunctions && filteredFunctions.length > 0 ? (
        <div className="space-y-3">
          {filteredFunctions.map((func: any) => {
            const isExpanded = expandedFunctions.has(func.id);
            const triggerRoles = functionTriggerRoles[`${func.namespace}/${func.name}`];
            return (
              <div key={func.id} className="card">
                <div className="flex items-center gap-4">
                  {/* Icon */}
                  <div className="flex-shrink-0">
                    {func.icon_url ? (
                      <img src={func.icon_url} alt="" className="w-8 h-8 rounded-lg object-cover" />
                    ) : (
                      <Code className="w-8 h-8 text-gray-500" />
                    )}
                  </div>
                  <button
                    onClick={() => toggleFunctionExpanded(func.id)}
                    className="flex-shrink-0 text-gray-500 hover:text-gray-400 transition-colors"
                    title={isExpanded ? "Hide code" : "Show code"}
                  >
                    {isExpanded ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
                  </button>

                  {/* Info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="font-semibold text-gray-100 truncate">
                        <span className="text-gray-500">{func.namespace}/</span>{func.name}
                      </h3>
                      {func.shared_pool && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-900/30 text-blue-300 flex-shrink-0">
                          Shared Pool
                        </span>
                      )}
                      {func.requires_approval && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-yellow-900/30 text-yellow-300 flex-shrink-0">
                          Requires Approval
                        </span>
                      )}
                      {triggerRoles?.contentFilter.length > 0 && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-orange-900/30 text-orange-300 flex-shrink-0" title={`Content filter for: ${triggerRoles.contentFilter.join(', ')}`}>
                          <Filter className="w-3 h-3 mr-1" />
                          Content Filter
                        </span>
                      )}
                      {triggerRoles?.postUpload.length > 0 && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-900/30 text-green-300 flex-shrink-0" title={`Post-upload for: ${triggerRoles.postUpload.join(', ')}`}>
                          <Upload className="w-3 h-3 mr-1" />
                          Post-Upload
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-gray-400 truncate mt-0.5">{func.description || 'No description'}</p>
                    <div className="flex flex-wrap gap-x-3 gap-y-1 mt-1">
                      <span className="text-xs text-gray-500">
                        Created {new Date(func.created_at).toLocaleDateString()}
                      </span>
                      {func.requirements && func.requirements.length > 0 && (
                        <span className="text-xs text-gray-500">
                          {func.requirements.length} requirement{func.requirements.length > 1 ? 's' : ''}
                        </span>
                      )}
                      {func.enabled_namespaces && func.enabled_namespaces.length > 0 && (
                        <span className="text-xs text-gray-500">
                          Calls {func.enabled_namespaces.length} namespace{func.enabled_namespaces.length > 1 ? 's' : ''}
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <button
                      onClick={() => handleExecute(func)}
                      disabled={executeMutation.isPending}
                      className="inline-flex items-center px-3 py-1.5 text-sm font-medium text-green-400 bg-green-900/20 hover:bg-green-900/30 rounded-md transition-colors"
                      title="Execute function"
                    >
                      <Play className="w-4 h-4 mr-1.5" />
                      Run
                    </button>
                    <Link
                      to={`/functions/${func.namespace}/${func.name}`}
                      className="p-2 text-gray-500 hover:text-primary-600 hover:bg-white/10 rounded-md transition-colors"
                      title="Edit"
                    >
                      <Edit2 className="w-4 h-4" />
                    </Link>
                    <button
                      onClick={() => {
                        if (confirm('Are you sure you want to delete this function?')) {
                          deleteMutation.mutate({ namespace: func.namespace, name: func.name });
                        }
                      }}
                      className="p-2 text-gray-500 hover:text-red-600 hover:bg-white/10 rounded-md transition-colors"
                      disabled={deleteMutation.isPending}
                      title="Delete"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {/* Expandable Code Section */}
                {isExpanded && (
                  <div className="mt-4 bg-gray-900 rounded-lg p-4 overflow-x-auto">
                    <pre className="text-sm text-gray-100 font-mono">{func.code}</pre>
                  </div>
                )}
              </div>
            );
          })}
          {searchFilter && filteredFunctions.length === 0 && (
            <div className="text-center py-8 text-gray-500">No functions match your search</div>
          )}
        </div>
      ) : (
        <div className="text-center py-12 card">
          <Code className="w-16 h-16 text-gray-500 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-100 mb-2">No functions yet</h3>
          <p className="text-gray-400 mb-4">Create Python functions to extend your AI capabilities</p>
          <Link to="/functions/new/new" className="btn btn-primary">
            <Plus className="w-5 h-5 mr-2 inline" />
            Create Function
          </Link>
        </div>
      )}

      {/* Execute Function Modal */}
      {showExecuteModal && executeFunc && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-[#161616] rounded-lg max-w-lg w-full p-6 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-xl font-semibold text-gray-100">Execute Function</h2>
                <p className="text-sm text-gray-500 font-mono">{executeFunc.namespace}/{executeFunc.name}</p>
              </div>
              <button onClick={closeExecuteModal} className="p-1 text-gray-500 hover:text-gray-400">
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Input form (only show if function has input_schema and no result yet) */}
            {!executeResult && !executeMutation.isPending && (() => {
              const inputSchema = executeFunc.input_schema;
              const hasInputParams = inputSchema?.properties && Object.keys(inputSchema.properties).length > 0;
              if (!hasInputParams) return null;

              const properties = inputSchema?.properties || {};
              const requiredFields = inputSchema?.required || [];

              return (
                <form onSubmit={handleExecuteModalSubmit} className="space-y-4">
                  {Object.entries(properties).map(([key, prop]: [string, any]) => (
                    <SchemaFormField
                      key={key}
                      name={key}
                      schema={prop}
                      value={executeInputParams[key]}
                      onChange={(value) => setExecuteInputParams({ ...executeInputParams, [key]: value })}
                      required={requiredFields.includes(key)}
                    />
                  ))}
                  <div className="flex items-center justify-between pt-4">
                    <Link
                      to="/functions/execute"
                      className="text-sm text-primary-600 hover:text-primary-700"
                      onClick={closeExecuteModal}
                    >
                      Open full executor
                    </Link>
                    <button type="submit" className="btn btn-primary">
                      Execute
                    </button>
                  </div>
                </form>
              );
            })()}

            {/* Loading state */}
            {executeMutation.isPending && (
              <div className="flex items-center justify-center py-8">
                <div className="inline-block animate-spin rounded-full h-6 w-6 border-b-2 border-primary-600 mr-3"></div>
                <span className="text-gray-400">Executing...</span>
              </div>
            )}

            {/* Result */}
            {executeResult && (
              <div className="space-y-3">
                <div className={`inline-flex items-center px-2.5 py-1 rounded text-sm font-medium ${
                  executeResult.status === 'success'
                    ? 'bg-green-900/30 text-green-400'
                    : 'bg-red-900/30 text-red-400'
                }`}>
                  {executeResult.status === 'success' ? 'Success' : 'Error'}
                </div>

                {executeResult.execution_id && (
                  <p className="text-xs text-gray-500 font-mono">Execution: {executeResult.execution_id}</p>
                )}

                {/* Output */}
                <div className="bg-gray-900 rounded-lg p-4 overflow-x-auto max-h-80">
                  <pre className="text-sm text-gray-100 font-mono whitespace-pre-wrap break-words">
                    {executeResult.error
                      ? executeResult.error
                      : JSON.stringify(executeResult.result, null, 2)}
                  </pre>
                </div>

                <div className="flex items-center justify-between pt-2">
                  <Link
                    to="/functions/execute"
                    className="text-sm text-primary-600 hover:text-primary-700"
                    onClick={closeExecuteModal}
                  >
                    Open full executor
                  </Link>
                  <div className="flex gap-2">
                    <button
                      onClick={() => {
                        setExecuteResult(null);
                        executeMutation.reset();
                      }}
                      className="btn btn-secondary text-sm"
                    >
                      Run Again
                    </button>
                    <button onClick={closeExecuteModal} className="btn btn-primary text-sm">
                      Done
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Dependency Management Modal */}
      {showDependencyModal && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-[#161616] rounded-lg max-w-2xl w-full p-6">
            <h2 className="text-xl font-semibold text-gray-100 mb-4">Manage Dependencies</h2>

            <form onSubmit={handleInstallDependency} className="mb-6">
              <label htmlFor="dependency" className="block text-sm font-medium text-gray-300 mb-2">
                Install Dependency
              </label>
              <div className="flex space-x-2">
                <input
                  id="dependency"
                  type="text"
                  value={dependencyName}
                  onChange={(e) => setDependencyName(e.target.value)}
                  placeholder="requests, numpy, pandas..."
                  className="input flex-1"
                />
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={installDependencyMutation.isPending || !dependencyName.trim()}
                >
                  {installDependencyMutation.isPending ? 'Installing...' : 'Install'}
                </button>
              </div>
            </form>

            <div className="border-t border-white/[0.06] pt-4">
              <h3 className="text-sm font-medium text-gray-300 mb-3">Installed Dependencies</h3>
              {dependencies && dependencies.length > 0 ? (
                <div className="space-y-2 max-h-64 overflow-y-auto">
                  {dependencies.map((dep: any) => (
                    <div key={dep.id} className="flex items-center justify-between p-2 bg-[#0d0d0d] rounded">
                      <div>
                        <span className="font-mono text-sm">{dep.package_name}</span>
                        <span className="text-xs text-gray-500 ml-2">{dep.version}</span>
                      </div>
                      <button
                        onClick={() => deleteDependencyMutation.mutate(dep.id)}
                        className="text-red-600 hover:text-red-400 text-sm"
                        disabled={deleteDependencyMutation.isPending}
                      >
                        Uninstall
                      </button>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-gray-500">No dependencies installed</p>
              )}
            </div>

            <div className="flex justify-end mt-6">
              <button
                onClick={() => setShowDependencyModal(false)}
                className="btn btn-secondary"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* OpenAPI Import Modal */}
      {showImportModal && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-[#161616] rounded-lg max-w-4xl w-full p-6 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-xl font-semibold text-gray-100">Import OpenAPI</h2>
                <p className="text-sm text-gray-500">
                  {importStep === 'input' && 'Provide an OpenAPI v3 spec to generate functions'}
                  {importStep === 'preview' && 'Review and select operations to import'}
                  {importStep === 'done' && 'Import complete'}
                </p>
              </div>
              <button onClick={closeImportModal} className="p-1 text-gray-500 hover:text-gray-400">
                <X className="w-5 h-5" />
              </button>
            </div>

            {importError && (
              <div className="mb-4 p-3 bg-red-900/20 border border-red-800 rounded-lg flex items-start gap-2">
                <AlertCircle className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" />
                <p className="text-sm text-red-400">{importError}</p>
              </div>
            )}

            {/* Step 1: Input */}
            {importStep === 'input' && (
              <div className="space-y-4">
                {/* Source toggle */}
                <div className="flex gap-2">
                  <button
                    onClick={() => setImportSpecMode('paste')}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-sm ${
                      importSpecMode === 'paste'
                        ? 'bg-primary-600/20 text-primary-400 border border-primary-600/40'
                        : 'bg-[#0d0d0d] text-gray-400 border border-white/[0.06]'
                    }`}
                  >
                    <FileText className="w-4 h-4" />
                    Paste Spec
                  </button>
                  <button
                    onClick={() => setImportSpecMode('url')}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded text-sm ${
                      importSpecMode === 'url'
                        ? 'bg-primary-600/20 text-primary-400 border border-primary-600/40'
                        : 'bg-[#0d0d0d] text-gray-400 border border-white/[0.06]'
                    }`}
                  >
                    <Globe className="w-4 h-4" />
                    From URL
                  </button>
                </div>

                {importSpecMode === 'paste' ? (
                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-1">OpenAPI Spec (JSON or YAML)</label>
                    <textarea
                      value={importSpec}
                      onChange={(e) => setImportSpec(e.target.value)}
                      placeholder='{"openapi": "3.0.0", "info": {...}, "paths": {...}}'
                      className="input font-mono text-sm w-full"
                      rows={10}
                    />
                  </div>
                ) : (
                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-1">Spec URL</label>
                    <input
                      type="text"
                      value={importSpecUrl}
                      onChange={(e) => setImportSpecUrl(e.target.value)}
                      placeholder="https://api.example.com/openapi.json"
                      className="input w-full"
                    />
                  </div>
                )}

                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-1">Namespace</label>
                    <input
                      type="text"
                      value={importNamespace}
                      onChange={(e) => setImportNamespace(e.target.value)}
                      placeholder="Auto-derived from spec title"
                      className="input w-full"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-1">Base URL Override</label>
                    <input
                      type="text"
                      value={importBaseUrl}
                      onChange={(e) => setImportBaseUrl(e.target.value)}
                      placeholder="Auto-detected from spec"
                      className="input w-full"
                    />
                  </div>
                </div>

                {/* Auth config */}
                <div className="border-t border-white/[0.06] pt-4">
                  <h3 className="text-sm font-medium text-gray-300 mb-3">Authentication</h3>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-1">Auth Type</label>
                      <select
                        value={importAuthType}
                        onChange={(e) => setImportAuthType(e.target.value)}
                        className="input w-full"
                      >
                        <option value="none">None</option>
                        <option value="bearer">Bearer Token</option>
                        <option value="api_key">API Key</option>
                      </select>
                    </div>
                    {importAuthType !== 'none' && (
                      <div>
                        <label className="block text-sm font-medium text-gray-300 mb-1">Header Name</label>
                        <input
                          type="text"
                          value={importAuthHeader}
                          onChange={(e) => setImportAuthHeader(e.target.value)}
                          className="input w-full"
                        />
                      </div>
                    )}
                  </div>
                  {importAuthType !== 'none' && (
                    <div className="grid grid-cols-2 gap-4 mt-3">
                      <div>
                        <label className="block text-sm font-medium text-gray-300 mb-1">State Namespace</label>
                        <input
                          type="text"
                          value={importAuthStateNs}
                          onChange={(e) => setImportAuthStateNs(e.target.value)}
                          placeholder="api_keys"
                          className="input w-full"
                        />
                      </div>
                      <div>
                        <label className="block text-sm font-medium text-gray-300 mb-1">State Key</label>
                        <input
                          type="text"
                          value={importAuthStateKey}
                          onChange={(e) => setImportAuthStateKey(e.target.value)}
                          placeholder="my_service"
                          className="input w-full"
                        />
                      </div>
                    </div>
                  )}
                </div>

                <div className="flex justify-end gap-2 pt-2">
                  <button onClick={closeImportModal} className="btn btn-secondary">Cancel</button>
                  <button
                    onClick={handleImportParse}
                    disabled={importParseMutation.isPending || (importSpecMode === 'paste' ? !importSpec.trim() : !importSpecUrl.trim())}
                    className="btn btn-primary"
                  >
                    {importParseMutation.isPending ? 'Parsing...' : 'Parse Spec'}
                  </button>
                </div>
              </div>
            )}

            {/* Step 2: Preview */}
            {importStep === 'preview' && importPreview && (
              <div className="space-y-4">
                {importPreview.warnings.length > 0 && (
                  <div className="p-3 bg-yellow-900/20 border border-yellow-800 rounded-lg">
                    {importPreview.warnings.map((w, i) => (
                      <p key={i} className="text-sm text-yellow-400 flex items-center gap-2">
                        <AlertCircle className="w-4 h-4 flex-shrink-0" />
                        {w}
                      </p>
                    ))}
                  </div>
                )}

                <div className="flex items-center justify-between">
                  <span className="text-sm text-gray-400">
                    {importPreview.functions.length} operation{importPreview.functions.length !== 1 ? 's' : ''} found
                    {' '}&middot;{' '}
                    Namespace: <span className="font-mono text-gray-300">{importPreview.namespace}</span>
                  </span>
                  <button
                    onClick={toggleImportSelectAll}
                    className="text-sm text-primary-400 hover:text-primary-300"
                  >
                    {importSelected.size === importPreview.functions.filter(f => f.status === 'will_create').length
                      ? 'Deselect All'
                      : 'Select All'}
                  </button>
                </div>

                <div className="space-y-2 max-h-[50vh] overflow-y-auto">
                  {importPreview.functions.map((func) => {
                    const opId = func.operation_id || func.name;
                    const isSelected = importSelected.has(opId);
                    const isExisting = func.status === 'exists_skip';
                    const isCodeExpanded = importExpandedCode.has(opId);
                    const methodColors: Record<string, string> = {
                      GET: 'bg-green-900/30 text-green-400',
                      POST: 'bg-blue-900/30 text-blue-400',
                      PUT: 'bg-yellow-900/30 text-yellow-400',
                      PATCH: 'bg-orange-900/30 text-orange-400',
                      DELETE: 'bg-red-900/30 text-red-400',
                    };
                    return (
                      <div key={opId} className={`border rounded-lg p-3 ${isExisting ? 'border-white/[0.03] opacity-50' : 'border-white/[0.06]'}`}>
                        <div className="flex items-center gap-3">
                          <input
                            type="checkbox"
                            checked={isSelected}
                            disabled={isExisting}
                            onChange={() => toggleImportOperation(opId)}
                            className="rounded border-gray-600 bg-gray-800 text-primary-600 focus:ring-primary-600"
                          />
                          <span className={`px-2 py-0.5 rounded text-xs font-bold uppercase ${methodColors[func.method] || 'bg-gray-800 text-gray-400'}`}>
                            {func.method}
                          </span>
                          <div className="flex-1 min-w-0">
                            <span className="text-sm font-mono text-gray-200">{func.name}</span>
                            <span className="text-xs text-gray-500 ml-2">{func.path}</span>
                          </div>
                          {isExisting && (
                            <span className="text-xs text-gray-500 bg-gray-800 px-2 py-0.5 rounded">Exists</span>
                          )}
                          <button
                            onClick={() => {
                              const s = new Set(importExpandedCode);
                              if (s.has(opId)) s.delete(opId); else s.add(opId);
                              setImportExpandedCode(s);
                            }}
                            className="text-gray-500 hover:text-gray-400"
                          >
                            <Code className="w-4 h-4" />
                          </button>
                        </div>
                        {func.description && (
                          <p className="text-xs text-gray-500 mt-1 ml-8">{func.description}</p>
                        )}
                        {isCodeExpanded && (
                          <div className="mt-3 bg-gray-900 rounded-lg p-3 overflow-x-auto">
                            <pre className="text-xs text-gray-300 font-mono">{func.code}</pre>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>

                <div className="flex justify-between items-center pt-2">
                  <button
                    onClick={() => { setImportStep('input'); setImportPreview(null); }}
                    className="btn btn-secondary"
                  >
                    Back
                  </button>
                  <div className="flex gap-2">
                    <button onClick={closeImportModal} className="btn btn-secondary">Cancel</button>
                    <button
                      onClick={handleImportCreate}
                      disabled={importCreateMutation.isPending || importSelected.size === 0}
                      className="btn btn-primary"
                    >
                      {importCreateMutation.isPending
                        ? 'Importing...'
                        : `Import ${importSelected.size} Function${importSelected.size !== 1 ? 's' : ''}`}
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* Step 3: Done */}
            {importStep === 'done' && importPreview && (
              <div className="space-y-4">
                <div className="flex items-center gap-3 p-4 bg-green-900/20 border border-green-800 rounded-lg">
                  <Check className="w-6 h-6 text-green-400" />
                  <div>
                    <p className="text-green-400 font-medium">
                      {importPreview.created} function{importPreview.created !== 1 ? 's' : ''} created
                    </p>
                    {importPreview.skipped > 0 && (
                      <p className="text-sm text-gray-400">{importPreview.skipped} skipped (already exist)</p>
                    )}
                  </div>
                </div>

                {importPreview.functions
                  .filter(f => f.status === 'will_create')
                  .map(f => (
                    <Link
                      key={f.name}
                      to={`/functions/${importPreview!.namespace}/${f.name}`}
                      className="block p-3 bg-[#0d0d0d] rounded-lg border border-white/[0.06] hover:border-white/[0.12] transition-colors"
                    >
                      <span className="text-sm font-mono text-gray-300">
                        <span className="text-gray-500">{importPreview!.namespace}/</span>{f.name}
                      </span>
                      {f.description && <p className="text-xs text-gray-500 mt-0.5">{f.description}</p>}
                    </Link>
                  ))}

                <div className="flex justify-end pt-2">
                  <button onClick={closeImportModal} className="btn btn-primary">Done</button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
