import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Lightbulb, Plus, Trash2, Edit2, Eye } from 'lucide-react';
import { useState } from 'react';
import type { Skill, SkillCreate, SkillUpdate } from '../types';
import { ErrorDisplay } from '../components/ErrorDisplay';

export function Skills() {
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [showViewModal, setShowViewModal] = useState(false);
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);

  const [createFormData, setCreateFormData] = useState<SkillCreate>({
    namespace: 'default',
    name: '',
    description: '',
    content: '',
  });
  const [editFormData, setEditFormData] = useState<SkillUpdate>({});

  const { data: skills, isLoading, error } = useQuery({
    queryKey: ['skills'],
    queryFn: () => apiClient.listSkills(),
    retry: false,
  });

  const createMutation = useMutation({
    mutationFn: (data: SkillCreate) => apiClient.createSkill(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['skills'] });
      setShowCreateModal(false);
      resetCreateForm();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ namespace, name, data }: { namespace: string; name: string; data: SkillUpdate }) =>
      apiClient.updateSkill(namespace, name, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['skills'] });
      setShowEditModal(false);
      setSelectedSkill(null);
      setEditFormData({});
    },
  });

  const deleteMutation = useMutation({
    mutationFn: ({ namespace, name }: { namespace: string; name: string }) =>
      apiClient.deleteSkill(namespace, name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['skills'] });
    },
  });

  const resetCreateForm = () => {
    setCreateFormData({
      namespace: 'default',
      name: '',
      description: '',
      content: '',
    });
  };

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (createFormData.name.trim() && createFormData.description.trim() && createFormData.content.trim()) {
      createMutation.mutate(createFormData);
    }
  };

  const handleEdit = (e: React.FormEvent) => {
    e.preventDefault();
    if (selectedSkill) {
      updateMutation.mutate({
        namespace: selectedSkill.namespace,
        name: selectedSkill.name,
        data: editFormData,
      });
    }
  };

  const openEditModal = (skill: Skill) => {
    setSelectedSkill(skill);
    setEditFormData({
      namespace: skill.namespace,
      name: skill.name,
      description: skill.description,
      content: skill.content,
      is_active: skill.is_active,
    });
    setShowEditModal(true);
  };

  const openViewModal = (skill: Skill) => {
    setSelectedSkill(skill);
    setShowViewModal(true);
  };

  const handleDelete = (skill: Skill) => {
    if (confirm(`Are you sure you want to delete skill "${skill.namespace}/${skill.name}"?`)) {
      deleteMutation.mutate({ namespace: skill.namespace, name: skill.name });
    }
  };

  if (error) {
    return <ErrorDisplay error={error} />;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Skills</h1>
          <p className="text-gray-600 mt-1">Reusable instruction modules that agents can retrieve as needed</p>
        </div>
        <button
          onClick={() => {
            resetCreateForm();
            setShowCreateModal(true);
          }}
          className="btn btn-primary flex items-center"
        >
          <Plus className="w-5 h-5 mr-2" />
          Create Skill
        </button>
      </div>

      {isLoading ? (
        <div className="text-center py-12">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600 mx-auto"></div>
        </div>
      ) : skills && skills.length > 0 ? (
        <div className="grid gap-4">
          {skills.map((skill) => (
            <div key={skill.id} className="card hover:shadow-md transition-shadow">
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <div className="flex items-center space-x-2">
                    <Lightbulb className="w-5 h-5 text-yellow-500" />
                    <h3 className="text-lg font-semibold text-gray-900">
                      {skill.namespace}/{skill.name}
                    </h3>
                    {!skill.is_active && (
                      <span className="px-2 py-1 text-xs bg-gray-200 text-gray-600 rounded">
                        Inactive
                      </span>
                    )}
                  </div>
                  <p className="text-gray-600 mt-2">{skill.description}</p>
                  <div className="text-xs text-gray-500 mt-2">
                    {new Date(skill.created_at).toLocaleString()}
                  </div>
                </div>
                <div className="flex items-center space-x-2 ml-4">
                  <button
                    onClick={() => openViewModal(skill)}
                    className="btn btn-sm btn-secondary"
                    title="View content"
                  >
                    <Eye className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => openEditModal(skill)}
                    className="btn btn-sm btn-secondary"
                    title="Edit"
                  >
                    <Edit2 className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => handleDelete(skill)}
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
          <Lightbulb className="w-16 h-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">No skills yet</h3>
          <p className="text-gray-600 mb-4">Create your first skill to get started</p>
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <>
          <div className="fixed inset-0 bg-black/20 backdrop-blur-sm z-50" onClick={() => setShowCreateModal(false)} />
          <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
            <div className="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] overflow-y-auto p-6 pointer-events-auto" onClick={(e) => e.stopPropagation()}>
              <h2 className="text-2xl font-bold mb-6">Create Skill</h2>
            <form onSubmit={handleCreate} className="space-y-4">
              <div>
                <label className="label">Namespace</label>
                <input
                  type="text"
                  className="input"
                  value={createFormData.namespace}
                  onChange={(e) => setCreateFormData({ ...createFormData, namespace: e.target.value })}
                  required
                  pattern="[a-z0-9_-]+"
                  title="Lowercase letters, numbers, hyphens, and underscores only"
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
                  pattern="[a-z0-9_-]+"
                  title="Lowercase letters, numbers, hyphens, and underscores only"
                />
              </div>

              <div>
                <label className="label">Description</label>
                <input
                  type="text"
                  className="input"
                  placeholder="What this skill helps with (shown to LLM as tool description)"
                  value={createFormData.description}
                  onChange={(e) => setCreateFormData({ ...createFormData, description: e.target.value })}
                  required
                />
                <p className="text-xs text-gray-500 mt-1">
                  This appears as the tool description. Make it clear and actionable.
                </p>
              </div>

              <div>
                <label className="label">Content (Markdown)</label>
                <textarea
                  className="input font-mono text-sm"
                  rows={12}
                  placeholder="# Skill Instructions&#10;&#10;Detailed instructions in markdown format..."
                  value={createFormData.content}
                  onChange={(e) => setCreateFormData({ ...createFormData, content: e.target.value })}
                  required
                />
                <p className="text-xs text-gray-500 mt-1">
                  Markdown instructions retrieved when LLM calls this skill.
                </p>
              </div>

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
      {showEditModal && selectedSkill && (
        <>
          <div className="fixed inset-0 bg-black/20 backdrop-blur-sm z-50" onClick={() => setShowEditModal(false)} />
          <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
            <div className="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] overflow-y-auto p-6 pointer-events-auto" onClick={(e) => e.stopPropagation()}>
              <h2 className="text-2xl font-bold mb-6">Edit Skill</h2>
            <form onSubmit={handleEdit} className="space-y-4">
              <div>
                <label className="label">Namespace</label>
                <input
                  type="text"
                  className="input"
                  value={editFormData.namespace || selectedSkill.namespace}
                  onChange={(e) => setEditFormData({ ...editFormData, namespace: e.target.value })}
                  pattern="[a-z0-9_-]+"
                />
              </div>

              <div>
                <label className="label">Name</label>
                <input
                  type="text"
                  className="input"
                  value={editFormData.name || selectedSkill.name}
                  onChange={(e) => setEditFormData({ ...editFormData, name: e.target.value })}
                  pattern="[a-z0-9_-]+"
                />
              </div>

              <div>
                <label className="label">Description</label>
                <input
                  type="text"
                  className="input"
                  value={editFormData.description ?? selectedSkill.description}
                  onChange={(e) => setEditFormData({ ...editFormData, description: e.target.value })}
                />
              </div>

              <div>
                <label className="label">Content (Markdown)</label>
                <textarea
                  className="input font-mono text-sm"
                  rows={12}
                  value={editFormData.content ?? selectedSkill.content}
                  onChange={(e) => setEditFormData({ ...editFormData, content: e.target.value })}
                />
              </div>

              <div>
                <label className="flex items-center space-x-2">
                  <input
                    type="checkbox"
                    checked={editFormData.is_active ?? selectedSkill.is_active}
                    onChange={(e) => setEditFormData({ ...editFormData, is_active: e.target.checked })}
                  />
                  <span>Active</span>
                </label>
              </div>

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

      {/* View Modal */}
      {showViewModal && selectedSkill && (
        <>
          <div className="fixed inset-0 bg-black/20 backdrop-blur-sm z-50" onClick={() => setShowViewModal(false)} />
          <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
            <div className="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[90vh] overflow-y-auto p-6 pointer-events-auto" onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center justify-between mb-6">
                <h2 className="text-2xl font-bold">
                  {selectedSkill.namespace}/{selectedSkill.name}
                </h2>
                <button
                  onClick={() => setShowViewModal(false)}
                  className="text-gray-500 hover:text-gray-700 text-3xl leading-none"
                >
                  ×
                </button>
              </div>
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">Description</label>
                  <p className="text-gray-700">{selectedSkill.description}</p>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">Content</label>
                  <pre className="bg-gray-50 p-4 rounded border border-gray-200 overflow-auto max-h-96 text-sm font-mono whitespace-pre-wrap">
                    {selectedSkill.content}
                  </pre>
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
