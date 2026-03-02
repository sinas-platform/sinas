import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Layers, Plus, Trash2, ExternalLink, RefreshCw } from 'lucide-react';
import { apiClient, getComponentRenderUrl } from '../lib/api';
import type { ComponentCreate } from '../types';

export function Components() {
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);

  const { data: components, isLoading, error } = useQuery({
    queryKey: ['components'],
    queryFn: () => apiClient.listComponents(),
    retry: false,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data?.some((c: { compile_status: string }) => c.compile_status === 'pending' || c.compile_status === 'compiling')) {
        return 2000;
      }
      return false;
    },
  });

  const createMutation = useMutation({
    mutationFn: (data: ComponentCreate) => apiClient.createComponent(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['components'] });
      setShowCreateModal(false);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: ({ namespace, name }: { namespace: string; name: string }) =>
      apiClient.deleteComponent(namespace, name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['components'] }),
  });

  const compileMutation = useMutation({
    mutationFn: ({ namespace, name }: { namespace: string; name: string }) =>
      apiClient.compileComponent(namespace, name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['components'] }),
  });

  const handleCreate = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    createMutation.mutate({
      namespace: (form.get('namespace') as string) || 'default',
      name: form.get('name') as string,
      title: form.get('title') as string || undefined,
      description: form.get('description') as string || undefined,
      source_code: form.get('source_code') as string,
    });
  };

  const getStatusBadge = (status: string) => {
    const colors: Record<string, string> = {
      success: 'bg-green-900/30 text-green-400',
      pending: 'bg-yellow-900/30 text-yellow-400',
      compiling: 'bg-blue-900/30 text-blue-400',
      error: 'bg-red-900/30 text-red-400',
    };
    return colors[status] || 'bg-gray-900/30 text-gray-400';
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6">
        <div className="bg-red-900/20 border border-red-800 rounded-lg p-4 text-red-400">
          Failed to load components: {(error as Error).message}
        </div>
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">Components</h1>
          <p className="text-sm text-gray-400 mt-1">Serverless UI components compiled and served as embeddable iframes</p>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="flex items-center gap-2 px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors"
        >
          <Plus className="w-4 h-4" />
          Create Component
        </button>
      </div>

      {!components?.length ? (
        <div className="text-center py-16">
          <Layers className="w-12 h-12 text-gray-600 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-400 mb-2">No components yet</h3>
          <p className="text-sm text-gray-500 mb-4">Create your first interactive component</p>
          <button
            onClick={() => setShowCreateModal(true)}
            className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700"
          >
            Create Component
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {components.map((comp) => (
            <div key={comp.id} className="bg-[#111111] border border-gray-800 rounded-lg p-4 hover:border-gray-700 transition-colors">
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Layers className="w-5 h-5 text-primary-400" />
                  <Link
                    to={`/components/${comp.namespace}/${comp.name}`}
                    className="text-white font-medium hover:text-primary-400 transition-colors"
                  >
                    {comp.title || comp.name}
                  </Link>
                </div>
                <span className={`px-2 py-0.5 rounded text-xs font-medium ${getStatusBadge(comp.compile_status)}`}>
                  {comp.compile_status}
                </span>
              </div>

              <p className="text-xs text-gray-500 mb-2">{comp.namespace}/{comp.name}</p>

              {comp.description && (
                <p className="text-sm text-gray-400 mb-3 line-clamp-2">{comp.description}</p>
              )}

              <div className="flex items-center justify-between mt-3 pt-3 border-t border-gray-800">
                <span className="text-xs text-gray-500">v{comp.version}</span>
                <div className="flex items-center gap-2">
                  {comp.compile_status !== 'compiling' && (
                    <button
                      onClick={() => compileMutation.mutate({ namespace: comp.namespace, name: comp.name })}
                      className="p-1 text-gray-500 hover:text-primary-400 transition-colors"
                      title="Recompile"
                    >
                      <RefreshCw className="w-4 h-4" />
                    </button>
                  )}
                  {comp.compile_status === 'success' && (
                    <a
                      href={getComponentRenderUrl(comp.render_token!, comp.namespace, comp.name)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="p-1 text-gray-500 hover:text-green-400 transition-colors"
                      title="Preview"
                    >
                      <ExternalLink className="w-4 h-4" />
                    </a>
                  )}
                  <button
                    onClick={() => {
                      if (confirm(`Delete component "${comp.namespace}/${comp.name}"?`)) {
                        deleteMutation.mutate({ namespace: comp.namespace, name: comp.name });
                      }
                    }}
                    className="p-1 text-gray-500 hover:text-red-400 transition-colors"
                    title="Delete"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-[#111111] border border-gray-800 rounded-lg w-full max-w-lg">
            <div className="p-6 border-b border-gray-800">
              <h2 className="text-lg font-semibold text-white">Create Component</h2>
            </div>
            <form onSubmit={handleCreate} className="p-6 space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm text-gray-400 mb-1">Namespace</label>
                  <input
                    name="namespace"
                    defaultValue="default"
                    className="w-full bg-[#0d0d0d] border border-gray-800 rounded px-3 py-2 text-white text-sm focus:outline-none focus:border-primary-600"
                    pattern="^[a-zA-Z][a-zA-Z0-9_-]*$"
                  />
                </div>
                <div>
                  <label className="block text-sm text-gray-400 mb-1">Name</label>
                  <input
                    name="name"
                    required
                    className="w-full bg-[#0d0d0d] border border-gray-800 rounded px-3 py-2 text-white text-sm focus:outline-none focus:border-primary-600"
                    pattern="^[a-zA-Z][a-zA-Z0-9_-]*$"
                    placeholder="my-component"
                  />
                </div>
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">Title</label>
                <input
                  name="title"
                  className="w-full bg-[#0d0d0d] border border-gray-800 rounded px-3 py-2 text-white text-sm focus:outline-none focus:border-primary-600"
                  placeholder="My Component"
                />
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">Description</label>
                <input
                  name="description"
                  className="w-full bg-[#0d0d0d] border border-gray-800 rounded px-3 py-2 text-white text-sm focus:outline-none focus:border-primary-600"
                  placeholder="What does this component do?"
                />
              </div>
              <div>
                <label className="block text-sm text-gray-400 mb-1">Source Code (TSX)</label>
                <textarea
                  name="source_code"
                  required
                  rows={8}
                  className="w-full bg-[#0d0d0d] border border-gray-800 rounded px-3 py-2 text-white text-sm font-mono focus:outline-none focus:border-primary-600"
                  defaultValue={`import React from 'react';\n\nexport default function MyComponent() {\n  return (\n    <div style={{ padding: '1rem' }}>\n      <h1>Hello from SINAS!</h1>\n    </div>\n  );\n}`}
                />
              </div>
              <div className="flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowCreateModal(false)}
                  className="px-4 py-2 text-gray-400 hover:text-white transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={createMutation.isPending}
                  className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50 transition-colors"
                >
                  {createMutation.isPending ? 'Creating...' : 'Create'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
