import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Code, Plus, Trash2, Edit2, Package, ChevronDown, ChevronRight, Search, Filter, Upload } from 'lucide-react';
import { useState, useMemo } from 'react';
import { Link } from 'react-router-dom';

export function Functions() {
  const queryClient = useQueryClient();
  const [showPackageModal, setShowPackageModal] = useState(false);
  const [packageName, setPackageName] = useState('');
  const [expandedFunctions, setExpandedFunctions] = useState<Set<string>>(new Set());
  const [searchFilter, setSearchFilter] = useState('');

  const { data: functions, isLoading } = useQuery({
    queryKey: ['functions'],
    queryFn: () => apiClient.listFunctions(),
    retry: false,
  });

  const { data: packages } = useQuery({
    queryKey: ['packages'],
    queryFn: () => apiClient.listPackages(),
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

  const installPackageMutation = useMutation({
    mutationFn: (data: any) => apiClient.installPackage(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['packages'] });
      setShowPackageModal(false);
      setPackageName('');
    },
  });

  const deletePackageMutation = useMutation({
    mutationFn: (packageId: string) => apiClient.deletePackage(packageId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['packages'] });
    },
  });

  const reloadWorkersMutation = useMutation({
    mutationFn: () => apiClient.reloadWorkers(),
  });

  const handleInstallPackage = (e: React.FormEvent) => {
    e.preventDefault();
    if (packageName.trim()) {
      installPackageMutation.mutate({ package_name: packageName.trim() });
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
          <h1 className="text-3xl font-bold text-gray-900">Functions</h1>
          <p className="text-gray-600 mt-1">Create and manage Python functions</p>
        </div>
        <div className="flex space-x-3">
          <button
            onClick={() => setShowPackageModal(true)}
            className="btn btn-secondary flex items-center"
          >
            <Package className="w-5 h-5 mr-2" />
            Packages
          </button>
          <Link to="/functions/new/new" className="btn btn-primary flex items-center">
            <Plus className="w-5 h-5 mr-2" />
            New Function
          </Link>
        </div>
      </div>

      {/* Search Bar */}
      {functions && functions.length > 0 && (
        <div className="card">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-400 pointer-events-none" />
            <input
              type="text"
              value={searchFilter}
              onChange={(e) => setSearchFilter(e.target.value)}
              placeholder="Search functions by name, namespace, or description..."
              className="input w-full !pl-11"
            />
          </div>
          {searchFilter && (
            <p className="text-sm text-gray-500 mt-2">
              Found {filteredFunctions.length} function{filteredFunctions.length !== 1 ? 's' : ''}
            </p>
          )}
        </div>
      )}

      {/* Packages Section */}
      {packages && packages.length > 0 && (
        <div className="card bg-gray-50">
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-sm font-medium text-gray-700">Installed Packages ({packages.length})</h3>
            <button
              onClick={() => reloadWorkersMutation.mutate()}
              disabled={reloadWorkersMutation.isPending}
              className="btn btn-secondary text-xs py-1 px-3"
            >
              {reloadWorkersMutation.isPending ? 'Reloading...' : 'Reload Workers'}
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            {packages.map((pkg: any) => (
              <div key={pkg.id} className="flex items-center bg-white px-3 py-1 rounded border border-gray-200">
                <span className="text-sm font-mono">{pkg.package_name}</span>
                <span className="text-xs text-gray-500 ml-2">{pkg.version}</span>
                <button
                  onClick={() => deletePackageMutation.mutate(pkg.id)}
                  className="ml-2 text-red-600 hover:text-red-700"
                  disabled={deletePackageMutation.isPending}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
          {reloadWorkersMutation.isSuccess && (
            <div className="mt-2 text-sm text-green-600">
              ✓ Workers reloaded successfully
            </div>
          )}
          {reloadWorkersMutation.isError && (
            <div className="mt-2 text-sm text-red-600">
              Failed to reload workers
            </div>
          )}
        </div>
      )}

      {/* Functions List */}
      {isLoading ? (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
        </div>
      ) : filteredFunctions && filteredFunctions.length > 0 ? (
        <div className="grid gap-6">
          {filteredFunctions.map((func: any) => {
            const isExpanded = expandedFunctions.has(func.id);
            return (
            <div key={func.id} className="card">
              <div className="flex items-start justify-between mb-4">
                <div className="flex items-center flex-1">
                  <Code className="w-8 h-8 text-primary-600 mr-3 flex-shrink-0" />
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <h3 className="font-semibold text-gray-900">
                        <span className="text-gray-500">{func.namespace}/</span>{func.name}
                      </h3>
                      {func.shared_pool && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-100 text-blue-800">
                          Shared Pool
                        </span>
                      )}
                      {func.requires_approval && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-yellow-100 text-yellow-800">
                          Requires Approval
                        </span>
                      )}
                      {functionTriggerRoles[`${func.namespace}/${func.name}`]?.contentFilter.length > 0 && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-orange-100 text-orange-800" title={`Content filter for: ${functionTriggerRoles[`${func.namespace}/${func.name}`].contentFilter.join(', ')}`}>
                          <Filter className="w-3 h-3 mr-1" />
                          Content Filter
                        </span>
                      )}
                      {functionTriggerRoles[`${func.namespace}/${func.name}`]?.postUpload.length > 0 && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-800" title={`Post-upload for: ${functionTriggerRoles[`${func.namespace}/${func.name}`].postUpload.join(', ')}`}>
                          <Upload className="w-3 h-3 mr-1" />
                          Post-Upload
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-gray-600">{func.description || 'No description'}</p>
                    <div className="flex flex-wrap gap-2 mt-2">
                      <p className="text-xs text-gray-500">
                        Created {new Date(func.created_at).toLocaleDateString()}
                      </p>
                      {func.requirements && func.requirements.length > 0 && (
                        <span className="text-xs bg-gray-100 text-gray-700 px-2 py-0.5 rounded">
                          {func.requirements.length} requirement{func.requirements.length > 1 ? 's' : ''}
                        </span>
                      )}
                      {func.enabled_namespaces && func.enabled_namespaces.length > 0 && (
                        <span className="text-xs bg-purple-100 text-purple-700 px-2 py-0.5 rounded">
                          Calls {func.enabled_namespaces.length} namespace{func.enabled_namespaces.length > 1 ? 's' : ''}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                <div className="flex items-center space-x-2">
                  <button
                    onClick={() => toggleFunctionExpanded(func.id)}
                    className="text-gray-600 hover:text-gray-900"
                    title={isExpanded ? "Hide code" : "Show code"}
                  >
                    {isExpanded ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className="w-5 h-5" />}
                  </button>
                  <Link
                    to={`/functions/${func.namespace}/${func.name}`}
                    className="text-primary-600 hover:text-primary-700"
                  >
                    <Edit2 className="w-5 h-5" />
                  </Link>
                  <button
                    onClick={() => {
                      if (confirm('Are you sure you want to delete this function?')) {
                        deleteMutation.mutate({ namespace: func.namespace, name: func.name });
                      }
                    }}
                    className="text-red-600 hover:text-red-700"
                    disabled={deleteMutation.isPending}
                  >
                    <Trash2 className="w-5 h-5" />
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
        </div>
      ) : (
        <div className="text-center py-12 card">
          <Code className="w-16 h-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">No functions yet</h3>
          <p className="text-gray-600 mb-4">Create Python functions to extend your AI capabilities</p>
          <Link to="/functions/new/new" className="btn btn-primary">
            <Plus className="w-5 h-5 mr-2 inline" />
            Create Function
          </Link>
        </div>
      )}

      {/* Package Management Modal */}
      {showPackageModal && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full p-6">
            <h2 className="text-xl font-semibold text-gray-900 mb-4">Manage Packages</h2>

            <form onSubmit={handleInstallPackage} className="mb-6">
              <label htmlFor="package" className="block text-sm font-medium text-gray-700 mb-2">
                Install Package
              </label>
              <div className="flex space-x-2">
                <input
                  id="package"
                  type="text"
                  value={packageName}
                  onChange={(e) => setPackageName(e.target.value)}
                  placeholder="requests, numpy, pandas..."
                  className="input flex-1"
                />
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={installPackageMutation.isPending || !packageName.trim()}
                >
                  {installPackageMutation.isPending ? 'Installing...' : 'Install'}
                </button>
              </div>
            </form>

            <div className="border-t border-gray-200 pt-4">
              <h3 className="text-sm font-medium text-gray-700 mb-3">Installed Packages</h3>
              {packages && packages.length > 0 ? (
                <div className="space-y-2 max-h-64 overflow-y-auto">
                  {packages.map((pkg: any) => (
                    <div key={pkg.id} className="flex items-center justify-between p-2 bg-gray-50 rounded">
                      <div>
                        <span className="font-mono text-sm">{pkg.package_name}</span>
                        <span className="text-xs text-gray-500 ml-2">{pkg.version}</span>
                      </div>
                      <button
                        onClick={() => deletePackageMutation.mutate(pkg.id)}
                        className="text-red-600 hover:text-red-700 text-sm"
                        disabled={deletePackageMutation.isPending}
                      >
                        Uninstall
                      </button>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-gray-500">No packages installed</p>
              )}
            </div>

            <div className="flex justify-end mt-6">
              <button
                onClick={() => setShowPackageModal(false)}
                className="btn btn-secondary"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
