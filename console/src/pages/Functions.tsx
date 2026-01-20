import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Code, Plus, Trash2, Edit2, Package } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';

export function Functions() {
  const queryClient = useQueryClient();
  const [showPackageModal, setShowPackageModal] = useState(false);
  const [packageName, setPackageName] = useState('');

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

  const handleInstallPackage = (e: React.FormEvent) => {
    e.preventDefault();
    if (packageName.trim()) {
      installPackageMutation.mutate({ name: packageName.trim() });
    }
  };

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

      {/* Packages Section */}
      {packages && packages.length > 0 && (
        <div className="card bg-gray-50">
          <h3 className="text-sm font-medium text-gray-700 mb-2">Installed Packages ({packages.length})</h3>
          <div className="flex flex-wrap gap-2">
            {packages.map((pkg: any) => (
              <div key={pkg.id} className="flex items-center bg-white px-3 py-1 rounded border border-gray-200">
                <span className="text-sm font-mono">{pkg.name}</span>
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
        </div>
      )}

      {/* Functions List */}
      {isLoading ? (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
        </div>
      ) : functions && functions.length > 0 ? (
        <div className="grid gap-6">
          {functions.map((func: any) => (
            <div key={func.id} className="card">
              <div className="flex items-start justify-between mb-4">
                <div className="flex items-center flex-1">
                  <Code className="w-8 h-8 text-primary-600 mr-3 flex-shrink-0" />
                  <div className="flex-1">
                    <h3 className="font-semibold text-gray-900">{func.name}</h3>
                    <p className="text-sm text-gray-600">{func.description || 'No description'}</p>
                    <p className="text-xs text-gray-500 mt-1">
                      Created {new Date(func.created_at).toLocaleDateString()}
                    </p>
                  </div>
                </div>
                <div className="flex items-center space-x-2">
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
              <div className="bg-gray-900 rounded-lg p-4 overflow-x-auto">
                <pre className="text-sm text-gray-100 font-mono">{func.code}</pre>
              </div>
            </div>
          ))}
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
                        <span className="font-mono text-sm">{pkg.name}</span>
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
