import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { FileText, Plus, Trash2, Edit2, Eye, Code2 } from 'lucide-react';
import { useState } from 'react';
import type { Template, TemplateCreate, TemplateUpdate } from '../types';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { JSONSchemaEditor } from '../components/JSONSchemaEditor';

export function Templates() {
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [showPreviewModal, setShowPreviewModal] = useState(false);
  const [selectedTemplate, setSelectedTemplate] = useState<Template | null>(null);
  const [previewVariables, setPreviewVariables] = useState<string>('{}');
  const [previewResult, setPreviewResult] = useState<any>(null);

  const [createFormData, setCreateFormData] = useState<TemplateCreate>({
    namespace: 'default',
    name: '',
    html_content: '',
    variable_schema: {},
  });
  const [editFormData, setEditFormData] = useState<TemplateUpdate>({});

  const { data: templates, isLoading, error } = useQuery({
    queryKey: ['templates'],
    queryFn: () => apiClient.listTemplates(),
    retry: false,
  });

  const createMutation = useMutation({
    mutationFn: (data: TemplateCreate) => apiClient.createTemplate(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['templates'] });
      setShowCreateModal(false);
      resetCreateForm();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: TemplateUpdate }) =>
      apiClient.updateTemplate(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['templates'] });
      setShowEditModal(false);
      setSelectedTemplate(null);
      setEditFormData({});
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (templateId: string) => apiClient.deleteTemplate(templateId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['templates'] });
    },
  });

  const previewMutation = useMutation({
    mutationFn: ({ id, variables }: { id: string; variables: Record<string, any> }) =>
      apiClient.renderTemplate(id, variables),
    onSuccess: (data: any) => {
      setPreviewResult(data);
    },
  });

  const resetCreateForm = () => {
    setCreateFormData({
      namespace: 'default',
      name: '',
      html_content: '',
      variable_schema: {},
    });
  };

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (createFormData.name.trim() && createFormData.html_content.trim()) {
      createMutation.mutate(createFormData);
    }
  };

  const handleEdit = (e: React.FormEvent) => {
    e.preventDefault();
    if (selectedTemplate && editFormData.name?.trim()) {
      updateMutation.mutate({
        id: selectedTemplate.id,
        data: editFormData,
      });
    }
  };

  const openEditModal = (template: Template) => {
    setSelectedTemplate(template);
    setEditFormData({
      namespace: template.namespace,
      name: template.name,
      description: template.description || '',
      title: template.title || '',
      html_content: template.html_content,
      text_content: template.text_content || '',
      variable_schema: template.variable_schema || {},
      is_active: template.is_active,
    });
    setShowEditModal(true);
  };

  const openPreviewModal = (template: Template) => {
    setSelectedTemplate(template);
    setPreviewVariables('{}');
    setPreviewResult(null);
    setShowPreviewModal(true);
  };

  const handlePreview = (e: React.FormEvent) => {
    e.preventDefault();
    if (selectedTemplate) {
      try {
        const variables = JSON.parse(previewVariables);
        previewMutation.mutate({ id: selectedTemplate.id, variables });
      } catch (err) {
        alert('Invalid JSON in variables');
      }
    }
  };

  if (error) {
    return <ErrorDisplay error={error} />;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-100">Templates</h1>
          <p className="text-gray-400 mt-1">Manage content templates</p>
        </div>
        <button
          onClick={() => {
            resetCreateForm();
            setShowCreateModal(true);
          }}
          className="btn btn-primary flex items-center"
        >
          <Plus className="w-5 h-5 mr-2" />
          Create Template
        </button>
      </div>

      {isLoading ? (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
        </div>
      ) : templates && templates.length > 0 ? (
        <div className="grid gap-4">
          {templates.map((template) => (
            <div key={template.id} className="card">
              <div className="flex items-start justify-between">
                <div className="flex items-center flex-1">
                  <FileText className="w-8 h-8 text-primary-600 mr-3 flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="font-semibold text-gray-100">
                        <span className="text-gray-500">{template.namespace}/</span>{template.name}
                      </h3>
                      {!template.is_active && (
                        <span className="px-2 py-0.5 text-xs bg-[#1e1e1e] text-gray-400 rounded">Inactive</span>
                      )}
                      {template.managed_by && (
                        <Code2 className="w-4 h-4 text-blue-500" />
                      )}
                    </div>
                    {template.description && (
                      <p className="text-sm text-gray-400 mt-1">{template.description}</p>
                    )}
                    {template.title && (
                      <p className="text-sm text-gray-500 mt-1">Title: {template.title}</p>
                    )}
                    <div className="flex gap-4 mt-2 text-xs text-gray-500">
                      <span>{Object.keys(template.variable_schema?.properties || {}).length} variables</span>
                      <span>Updated {new Date(template.updated_at).toLocaleDateString()}</span>
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2 ml-4">
                  <button
                    onClick={() => openPreviewModal(template)}
                    className="btn btn-secondary btn-sm flex items-center"
                  >
                    <Eye className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => openEditModal(template)}
                    className="btn btn-secondary btn-sm flex items-center"
                  >
                    <Edit2 className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => {
                      if (confirm(`Delete template "${template.name}"?`)) {
                        deleteMutation.mutate(template.id);
                      }
                    }}
                    className="btn btn-danger btn-sm"
                    disabled={deleteMutation.isPending}
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-center py-12 bg-[#0d0d0d] rounded-lg border-2 border-dashed border-white/10">
          <FileText className="w-16 h-16 text-gray-500 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-100 mb-2">No templates</h3>
          <p className="text-gray-400 mb-4">Get started by creating your first template</p>
          <button
            onClick={() => {
              resetCreateForm();
              setShowCreateModal(true);
            }}
            className="btn btn-primary inline-flex items-center"
          >
            <Plus className="w-5 h-5 mr-2" />
            Create Template
          </button>
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <>
          <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50" onClick={() => setShowCreateModal(false)} />
          <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
            <div className="bg-[#161616] rounded-lg max-w-4xl w-full max-h-[90vh] overflow-y-auto p-6 pointer-events-auto" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-2xl font-bold mb-6">Create Template</h2>
            <form onSubmit={handleCreate} className="space-y-4">
              <div>
                <label className="label">Namespace</label>
                <input
                  type="text"
                  className="input"
                  value={createFormData.namespace || 'default'}
                  onChange={(e) =>
                    setCreateFormData({ ...createFormData, namespace: e.target.value })
                  }
                  placeholder="default"
                  pattern="^[a-z][a-z0-9_-]*$"
                  required
                />
                <p className="text-xs text-gray-500 mt-1">Use lowercase letters, numbers, underscores, and hyphens</p>
              </div>

              <div>
                <label className="label">Template Name</label>
                <input
                  type="text"
                  className="input"
                  value={createFormData.name}
                  onChange={(e) =>
                    setCreateFormData({ ...createFormData, name: e.target.value })
                  }
                  placeholder="otp_email"
                  pattern="^[a-z][a-z0-9_-]*$"
                  required
                />
                <p className="text-xs text-gray-500 mt-1">Use lowercase letters, numbers, underscores, and hyphens</p>
              </div>

              <div>
                <label className="label">Description</label>
                <input
                  type="text"
                  className="input"
                  value={createFormData.description || ''}
                  onChange={(e) =>
                    setCreateFormData({ ...createFormData, description: e.target.value })
                  }
                  placeholder="What is this template for?"
                />
              </div>

              <div>
                <label className="label">Title</label>
                <input
                  type="text"
                  className="input"
                  value={createFormData.title || ''}
                  onChange={(e) =>
                    setCreateFormData({ ...createFormData, title: e.target.value })
                  }
                  placeholder="Your Login Code"
                />
              </div>

              <div>
                <label className="label">HTML Content (Jinja2 template)</label>
                <textarea
                  className="input font-mono text-sm"
                  rows={12}
                  value={createFormData.html_content}
                  onChange={(e) =>
                    setCreateFormData({ ...createFormData, html_content: e.target.value })
                  }
                  placeholder="<p>Hello {{ user_name }},</p>"
                  required
                />
                <p className="text-xs text-gray-500 mt-1">Use {`{{ variable_name }}`} for variables</p>
              </div>

              <div>
                <label className="label">Text Content (plain text fallback)</label>
                <textarea
                  className="input font-mono text-sm"
                  rows={6}
                  value={createFormData.text_content || ''}
                  onChange={(e) =>
                    setCreateFormData({ ...createFormData, text_content: e.target.value })
                  }
                  placeholder="Hello {{ user_name }},&#10;..."
                />
              </div>

              <div>
                <JSONSchemaEditor
                  label="Variable Schema (JSON Schema)"
                  description="Define template variables that can be used in Jinja2 templates with {{ variable_name }}"
                  value={createFormData.variable_schema || {}}
                  onChange={(schema) => setCreateFormData({ ...createFormData, variable_schema: schema })}
                />
              </div>

              <div className="flex justify-end gap-2 mt-6">
                <button
                  type="button"
                  onClick={() => setShowCreateModal(false)}
                  className="btn btn-secondary"
                >
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary" disabled={createMutation.isPending}>
                  {createMutation.isPending ? 'Creating...' : 'Create Template'}
                </button>
              </div>
              {createMutation.error && (
                <ErrorDisplay error={createMutation.error} />
              )}
            </form>
            </div>
          </div>
        </>
      )}

      {/* Edit Modal - Two-column with live preview */}
      {showEditModal && selectedTemplate && (
        <>
          <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50" onClick={() => setShowEditModal(false)} />
          <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
            <div className="bg-[#161616] rounded-lg max-w-7xl w-full h-[90vh] overflow-hidden flex flex-col p-6 pointer-events-auto" onClick={(e: React.MouseEvent) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-2xl font-bold">Edit Template: <span className="text-gray-500">{selectedTemplate.namespace}/</span>{selectedTemplate.name}</h2>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setShowEditModal(false)}
                  className="btn btn-secondary"
                >
                  Cancel
                </button>
                <button
                  onClick={handleEdit}
                  className="btn btn-primary"
                  disabled={updateMutation.isPending}
                >
                  {updateMutation.isPending ? 'Saving...' : 'Save Changes'}
                </button>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-6 flex-1 overflow-hidden">
              {/* Left: Form */}
              <div className="overflow-y-auto pr-4 space-y-4">
                <div>
                  <label className="label">Namespace</label>
                  <input
                    type="text"
                    className="input"
                    value={editFormData.namespace || 'default'}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                      setEditFormData({ ...editFormData, namespace: e.target.value })
                    }
                    pattern="^[a-z][a-z0-9_-]*$"
                    required
                  />
                </div>

                <div>
                  <label className="label">Template Name</label>
                  <input
                    type="text"
                    className="input"
                    value={editFormData.name || ''}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                      setEditFormData({ ...editFormData, name: e.target.value })
                    }
                    pattern="^[a-z][a-z0-9_-]*$"
                    required
                  />
                </div>

                <div>
                  <label className="label">Description</label>
                  <input
                    type="text"
                    className="input"
                    value={editFormData.description || ''}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                      setEditFormData({ ...editFormData, description: e.target.value })
                    }
                  />
                </div>

                <div>
                  <label className="label">Title / Subject</label>
                  <input
                    type="text"
                    className="input"
                    value={editFormData.title || ''}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                      setEditFormData({ ...editFormData, title: e.target.value })
                    }
                  />
                </div>

                <div>
                  <label className="label">HTML Content (Jinja2)</label>
                  <textarea
                    className="input font-mono text-sm"
                    rows={16}
                    value={editFormData.html_content || ''}
                    onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
                      setEditFormData({ ...editFormData, html_content: e.target.value })
                    }
                    required
                  />
                  <p className="text-xs text-gray-500 mt-1">Use {`{{ variable_name }}`} for variables</p>
                </div>

                <div>
                  <label className="label">Text Content (fallback)</label>
                  <textarea
                    className="input font-mono text-sm"
                    rows={6}
                    value={editFormData.text_content || ''}
                    onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
                      setEditFormData({ ...editFormData, text_content: e.target.value })
                    }
                  />
                </div>

                <div>
                  <JSONSchemaEditor
                    label="Variable Schema (JSON Schema)"
                    description="Define template variables that can be used in Jinja2 templates with {{ variable_name }}"
                    value={editFormData.variable_schema || {}}
                    onChange={(schema) => setEditFormData({ ...editFormData, variable_schema: schema })}
                  />
                </div>

                <div className="flex items-center">
                  <input
                    type="checkbox"
                    id="is_active"
                    checked={editFormData.is_active ?? true}
                    onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                      setEditFormData({ ...editFormData, is_active: e.target.checked })
                    }
                    className="mr-2"
                  />
                  <label htmlFor="is_active" className="text-sm text-gray-300">Active</label>
                </div>

                {updateMutation.error && (
                  <ErrorDisplay error={updateMutation.error} />
                )}
              </div>

              {/* Right: Live Preview */}
              <div className="border-l pl-6 flex flex-col overflow-hidden">
                <h3 className="font-semibold text-gray-100 mb-3">Live Preview</h3>
                <div className="flex-1 border rounded-lg overflow-hidden bg-[#161616]">
                  <iframe
                    srcDoc={editFormData.html_content || '<p class="p-4 text-gray-500">Start typing to see preview...</p>'}
                    className="w-full h-full"
                    title="Template Preview"
                    sandbox="allow-same-origin"
                  />
                </div>
                {editFormData.title && (
                  <div className="mt-3 p-3 bg-[#0d0d0d] rounded border">
                    <p className="text-xs text-gray-400 mb-1">Title/Subject:</p>
                    <p className="font-medium text-gray-100">{editFormData.title}</p>
                  </div>
                )}
              </div>
              </div>
            </div>
          </div>
        </>
      )}

      {/* Preview Modal */}
      {showPreviewModal && selectedTemplate && (
        <>
          <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50" onClick={() => setShowPreviewModal(false)} />
          <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
            <div className="bg-[#161616] rounded-lg max-w-4xl w-full max-h-[90vh] overflow-y-auto p-6 pointer-events-auto" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-2xl font-bold mb-6">Preview Template: <span className="text-gray-500">{selectedTemplate.namespace}/</span>{selectedTemplate.name}</h2>
            <form onSubmit={handlePreview} className="space-y-4">
              <div>
                <label className="label">Variables (JSON)</label>
                <textarea
                  className="input font-mono text-sm"
                  rows={6}
                  value={previewVariables}
                  onChange={(e) => setPreviewVariables(e.target.value)}
                  placeholder={'{\n  "user_name": "John Doe",\n  "otp_code": "123456"\n}'}
                />
                <p className="text-xs text-gray-500 mt-1">
                  Required: {JSON.stringify(selectedTemplate.variable_schema?.required || [])}
                </p>
              </div>

              <div className="flex gap-2">
                <button type="submit" className="btn btn-primary" disabled={previewMutation.isPending}>
                  {previewMutation.isPending ? 'Rendering...' : 'Render Preview'}
                </button>
                <button
                  type="button"
                  onClick={() => setShowPreviewModal(false)}
                  className="btn btn-secondary"
                >
                  Close
                </button>
              </div>

              {previewMutation.error && (
                <ErrorDisplay error={previewMutation.error} />
              )}

              {previewResult && (
                <div className="space-y-4 mt-6">
                  {previewResult.title && (
                    <div>
                      <h3 className="font-semibold text-gray-100 mb-2">Title:</h3>
                      <div className="p-3 bg-[#0d0d0d] rounded border">{previewResult.title}</div>
                    </div>
                  )}

                  <div>
                    <h3 className="font-semibold text-gray-100 mb-2">HTML Preview:</h3>
                    <div
                      className="p-4 bg-[#161616] rounded border"
                      dangerouslySetInnerHTML={{ __html: previewResult.html_content }}
                    />
                  </div>

                  {previewResult.text_content && (
                    <div>
                      <h3 className="font-semibold text-gray-100 mb-2">Text Content:</h3>
                      <pre className="p-3 bg-[#0d0d0d] rounded border text-sm whitespace-pre-wrap">
                        {previewResult.text_content}
                      </pre>
                    </div>
                  )}
                </div>
              )}
            </form>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
