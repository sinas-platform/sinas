import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Archive, Plus, Trash2, Edit2, FolderOpen } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import type { Collection, CollectionCreate, CollectionUpdate } from '../types';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { JSONSchemaEditor } from '../components/JSONSchemaEditor';

export function Collections() {
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [selectedCollection, setSelectedCollection] = useState<Collection | null>(null);

  const [createFormData, setCreateFormData] = useState<CollectionCreate>({
    namespace: 'default',
    name: '',
    content_filter_function: '',
    post_upload_function: '',
    max_file_size_mb: 100,
    max_total_size_gb: 10,
    allow_shared_files: true,
    allow_private_files: true,
    metadata_schema: {},
  });
  const [editFormData, setEditFormData] = useState<CollectionUpdate>({});

  const { data: collections, isLoading, error } = useQuery({
    queryKey: ['collections'],
    queryFn: () => apiClient.listCollections(),
    retry: false,
  });

  const { data: functions } = useQuery({
    queryKey: ['functions'],
    queryFn: () => apiClient.listFunctions(),
    retry: false,
  });

  const createMutation = useMutation({
    mutationFn: (data: CollectionCreate) => apiClient.createCollection(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['collections'] });
      setShowCreateModal(false);
      resetCreateForm();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ namespace, name, data }: { namespace: string; name: string; data: CollectionUpdate }) =>
      apiClient.updateCollection(namespace, name, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['collections'] });
      setShowEditModal(false);
      setSelectedCollection(null);
      setEditFormData({});
    },
  });

  const deleteMutation = useMutation({
    mutationFn: ({ namespace, name }: { namespace: string; name: string }) =>
      apiClient.deleteCollection(namespace, name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['collections'] });
    },
  });

  const resetCreateForm = () => {
    setCreateFormData({
      namespace: 'default',
      name: '',
      content_filter_function: '',
      post_upload_function: '',
      max_file_size_mb: 100,
      max_total_size_gb: 10,
      allow_shared_files: true,
      allow_private_files: true,
      metadata_schema: {},
    });
  };

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (createFormData.name.trim()) {
      const data: CollectionCreate = {
        ...createFormData,
        content_filter_function: createFormData.content_filter_function || undefined,
        post_upload_function: createFormData.post_upload_function || undefined,
      };
      // Only include metadata_schema if it has properties
      if (!createFormData.metadata_schema || Object.keys(createFormData.metadata_schema).length === 0) {
        delete data.metadata_schema;
      }
      createMutation.mutate(data);
    }
  };

  const handleEdit = (e: React.FormEvent) => {
    e.preventDefault();
    if (selectedCollection) {
      const data: CollectionUpdate = { ...editFormData };
      updateMutation.mutate({
        namespace: selectedCollection.namespace,
        name: selectedCollection.name,
        data,
      });
    }
  };

  const openEditModal = (collection: Collection) => {
    setSelectedCollection(collection);
    setEditFormData({
      content_filter_function: collection.content_filter_function,
      post_upload_function: collection.post_upload_function,
      max_file_size_mb: collection.max_file_size_mb,
      max_total_size_gb: collection.max_total_size_gb,
      allow_shared_files: collection.allow_shared_files,
      allow_private_files: collection.allow_private_files,
      metadata_schema: collection.metadata_schema,
    });
    setShowEditModal(true);
  };

  const handleDelete = (collection: Collection) => {
    if (confirm(`Are you sure you want to delete collection "${collection.namespace}/${collection.name}"?`)) {
      deleteMutation.mutate({ namespace: collection.namespace, name: collection.name });
    }
  };

  // Build function options for dropdowns
  const functionOptions = (functions || []).map((func: any) => `${func.namespace}/${func.name}`);

  if (error) {
    return <ErrorDisplay error={error} />;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-100">Collections</h1>
          <p className="text-gray-400 mt-1">Organize files with validation rules and access control</p>
        </div>
        <button
          onClick={() => {
            resetCreateForm();
            setShowCreateModal(true);
          }}
          className="btn btn-primary flex items-center"
        >
          <Plus className="w-5 h-5 mr-2" />
          New Collection
        </button>
      </div>

      {isLoading ? (
        <div className="text-center py-12">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto"></div>
        </div>
      ) : collections && collections.length > 0 ? (
        <div className="grid gap-4">
          {collections.map((collection) => (
            <div key={collection.id} className="card transition-colors">
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <div className="flex items-center space-x-2">
                    <Archive className="w-5 h-5 text-blue-500" />
                    <h3 className="text-lg font-semibold text-gray-100">
                      {collection.namespace}/{collection.name}
                    </h3>
                  </div>
                  <div className="text-sm text-gray-400 mt-2 space-y-1">
                    <p>
                      Max file size: {collection.max_file_size_mb} MB | Max total size: {collection.max_total_size_gb} GB
                    </p>
                    <p>
                      {collection.allow_shared_files ? 'Shared files allowed' : 'Shared files not allowed'}
                      {' | '}
                      {collection.allow_private_files ? 'Private files allowed' : 'Private files not allowed'}
                    </p>
                    {collection.content_filter_function && (
                      <p>Content filter: <span className="font-mono text-xs">{collection.content_filter_function}</span></p>
                    )}
                    {collection.post_upload_function && (
                      <p>Post-upload function: <span className="font-mono text-xs">{collection.post_upload_function}</span></p>
                    )}
                  </div>
                  <div className="text-xs text-gray-500 mt-2">
                    {new Date(collection.created_at).toLocaleString()}
                  </div>
                </div>
                <div className="flex items-center space-x-2 ml-4">
                  <Link
                    to={`/collections/${collection.namespace}/${collection.name}`}
                    className="btn btn-sm btn-secondary"
                    title="Browse Files"
                  >
                    <FolderOpen className="w-4 h-4" />
                  </Link>
                  <button
                    onClick={() => openEditModal(collection)}
                    className="btn btn-sm btn-secondary"
                    title="Edit"
                  >
                    <Edit2 className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => handleDelete(collection)}
                    className="btn btn-sm btn-danger"
                    title="Delete"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-center py-12 card">
          <Archive className="w-16 h-16 text-gray-500 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-100 mb-2">No collections yet</h3>
          <p className="text-gray-400 mb-4">Create your first collection to get started</p>
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <>
          <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50" onClick={() => setShowCreateModal(false)} />
          <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
            <div className="bg-[#161616] rounded-lg max-w-4xl w-full max-h-[90vh] overflow-y-auto p-6 pointer-events-auto" onClick={(e) => e.stopPropagation()}>
              <h2 className="text-2xl font-bold mb-6">Create Collection</h2>
            <form onSubmit={handleCreate} className="space-y-4">
              <div>
                <label className="label">Namespace</label>
                <input
                  type="text"
                  className="input"
                  value={createFormData.namespace}
                  onChange={(e) => setCreateFormData({ ...createFormData, namespace: e.target.value })}
                  required
                  pattern="[a-zA-Z][a-zA-Z0-9_-]*"
                  title="Must start with a letter, then letters, numbers, hyphens, and underscores"
                />
              </div>

              <div>
                <label className="label">Name</label>
                <input
                  type="text"
                  className="input"
                  value={createFormData.name}
                  onChange={(e) => setCreateFormData({ ...createFormData, name: e.target.value })}
                  required
                  pattern="[a-zA-Z][a-zA-Z0-9_-]*"
                  title="Must start with a letter, then letters, numbers, hyphens, and underscores"
                />
              </div>

              <div>
                <label className="label">Content Filter Function</label>
                <select
                  className="input"
                  value={createFormData.content_filter_function || ''}
                  onChange={(e) => setCreateFormData({ ...createFormData, content_filter_function: e.target.value })}
                >
                  <option value="">None</option>
                  {functionOptions.map((fn: string) => (
                    <option key={fn} value={fn}>{fn}</option>
                  ))}
                </select>
                <p className="text-xs text-gray-500 mt-1">
                  Function to validate/filter file content before upload.
                </p>
              </div>

              <div>
                <label className="label">Post-Upload Function</label>
                <select
                  className="input"
                  value={createFormData.post_upload_function || ''}
                  onChange={(e) => setCreateFormData({ ...createFormData, post_upload_function: e.target.value })}
                >
                  <option value="">None</option>
                  {functionOptions.map((fn: string) => (
                    <option key={fn} value={fn}>{fn}</option>
                  ))}
                </select>
                <p className="text-xs text-gray-500 mt-1">
                  Function to run after a file is uploaded (async, non-blocking).
                </p>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="label">Max File Size (MB)</label>
                  <input
                    type="number"
                    className="input"
                    value={createFormData.max_file_size_mb}
                    onChange={(e) => setCreateFormData({ ...createFormData, max_file_size_mb: Number(e.target.value) })}
                    min={1}
                  />
                </div>
                <div>
                  <label className="label">Max Total Size (GB)</label>
                  <input
                    type="number"
                    className="input"
                    value={createFormData.max_total_size_gb}
                    onChange={(e) => setCreateFormData({ ...createFormData, max_total_size_gb: Number(e.target.value) })}
                    min={1}
                  />
                </div>
              </div>

              <div className="flex space-x-6">
                <label className="flex items-center space-x-2">
                  <input
                    type="checkbox"
                    checked={createFormData.allow_shared_files}
                    onChange={(e) => setCreateFormData({ ...createFormData, allow_shared_files: e.target.checked })}
                  />
                  <span>Allow Shared Files</span>
                </label>
                <label className="flex items-center space-x-2">
                  <input
                    type="checkbox"
                    checked={createFormData.allow_private_files}
                    onChange={(e) => setCreateFormData({ ...createFormData, allow_private_files: e.target.checked })}
                  />
                  <span>Allow Private Files</span>
                </label>
              </div>

              <JSONSchemaEditor
                label="Metadata Schema"
                description="JSON Schema to validate file metadata on upload."
                value={createFormData.metadata_schema ?? {}}
                onChange={(schema) => setCreateFormData({ ...createFormData, metadata_schema: schema })}
              />

              <div className="flex justify-end space-x-2 pt-4">
                <button
                  type="button"
                  onClick={() => setShowCreateModal(false)}
                  className="btn btn-secondary"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={createMutation.isPending}
                >
                  {createMutation.isPending ? 'Creating...' : 'Create'}
                </button>
              </div>
            </form>
            </div>
          </div>
        </>
      )}

      {/* Edit Modal */}
      {showEditModal && selectedCollection && (
        <>
          <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50" onClick={() => setShowEditModal(false)} />
          <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
            <div className="bg-[#161616] rounded-lg max-w-4xl w-full max-h-[90vh] overflow-y-auto p-6 pointer-events-auto" onClick={(e) => e.stopPropagation()}>
              <h2 className="text-2xl font-bold mb-6">Edit Collection</h2>
            <form onSubmit={handleEdit} className="space-y-4">
              <div>
                <label className="label">Namespace</label>
                <input
                  type="text"
                  className="input bg-[#161616]"
                  value={selectedCollection.namespace}
                  disabled
                />
              </div>

              <div>
                <label className="label">Name</label>
                <input
                  type="text"
                  className="input bg-[#161616]"
                  value={selectedCollection.name}
                  disabled
                />
              </div>

              <div>
                <label className="label">Content Filter Function</label>
                <select
                  className="input"
                  value={editFormData.content_filter_function ?? selectedCollection.content_filter_function ?? ''}
                  onChange={(e) => setEditFormData({ ...editFormData, content_filter_function: e.target.value || null })}
                >
                  <option value="">None</option>
                  {functionOptions.map((fn: string) => (
                    <option key={fn} value={fn}>{fn}</option>
                  ))}
                </select>
                <p className="text-xs text-gray-500 mt-1">
                  Function to validate/filter file content before upload.
                </p>
              </div>

              <div>
                <label className="label">Post-Upload Function</label>
                <select
                  className="input"
                  value={editFormData.post_upload_function ?? selectedCollection.post_upload_function ?? ''}
                  onChange={(e) => setEditFormData({ ...editFormData, post_upload_function: e.target.value || null })}
                >
                  <option value="">None</option>
                  {functionOptions.map((fn: string) => (
                    <option key={fn} value={fn}>{fn}</option>
                  ))}
                </select>
                <p className="text-xs text-gray-500 mt-1">
                  Function to run after a file is uploaded (async, non-blocking).
                </p>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="label">Max File Size (MB)</label>
                  <input
                    type="number"
                    className="input"
                    value={editFormData.max_file_size_mb ?? selectedCollection.max_file_size_mb}
                    onChange={(e) => setEditFormData({ ...editFormData, max_file_size_mb: Number(e.target.value) })}
                    min={1}
                  />
                </div>
                <div>
                  <label className="label">Max Total Size (GB)</label>
                  <input
                    type="number"
                    className="input"
                    value={editFormData.max_total_size_gb ?? selectedCollection.max_total_size_gb}
                    onChange={(e) => setEditFormData({ ...editFormData, max_total_size_gb: Number(e.target.value) })}
                    min={1}
                  />
                </div>
              </div>

              <div className="flex space-x-6">
                <label className="flex items-center space-x-2">
                  <input
                    type="checkbox"
                    checked={editFormData.allow_shared_files ?? selectedCollection.allow_shared_files}
                    onChange={(e) => setEditFormData({ ...editFormData, allow_shared_files: e.target.checked })}
                  />
                  <span>Allow Shared Files</span>
                </label>
                <label className="flex items-center space-x-2">
                  <input
                    type="checkbox"
                    checked={editFormData.allow_private_files ?? selectedCollection.allow_private_files}
                    onChange={(e) => setEditFormData({ ...editFormData, allow_private_files: e.target.checked })}
                  />
                  <span>Allow Private Files</span>
                </label>
              </div>

              <JSONSchemaEditor
                label="Metadata Schema"
                description="JSON Schema to validate file metadata on upload."
                value={editFormData.metadata_schema ?? selectedCollection.metadata_schema ?? {}}
                onChange={(schema) => setEditFormData({ ...editFormData, metadata_schema: schema })}
              />

              <div className="flex justify-end space-x-2 pt-4">
                <button
                  type="button"
                  onClick={() => setShowEditModal(false)}
                  className="btn btn-secondary"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={updateMutation.isPending}
                >
                  {updateMutation.isPending ? 'Saving...' : 'Save'}
                </button>
              </div>
            </form>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
