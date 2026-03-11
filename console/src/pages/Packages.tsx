import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Package, Plus, Trash2, Download, Upload, X, Eye, Check, AlertTriangle } from 'lucide-react';
import { useState, useRef } from 'react';

type InstallStep = 'input' | 'preview' | 'installing' | 'done';

export function Packages() {
  const queryClient = useQueryClient();
  const [showInstallModal, setShowInstallModal] = useState(false);
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [installStep, setInstallStep] = useState<InstallStep>('input');
  const [yamlInput, setYamlInput] = useState('');
  const [previewResult, setPreviewResult] = useState<any>(null);
  const [installResult, setInstallResult] = useState<any>(null);
  const [createForm, setCreateForm] = useState({ name: '', version: '1.0.0', description: '', author: '', url: '' });
  const [selectedResources, setSelectedResources] = useState<Array<{ type: string; namespace: string; name: string }>>([]);
  const [createdYaml, setCreatedYaml] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { data: packages, isLoading } = useQuery({
    queryKey: ['packages'],
    queryFn: () => apiClient.listPackages(),
  });

  // Load available resources for create modal
  const { data: agents } = useQuery({ queryKey: ['agents'], queryFn: () => apiClient.listAgents(), enabled: showCreateModal });
  const { data: functions } = useQuery({ queryKey: ['functions'], queryFn: () => apiClient.listFunctions(), enabled: showCreateModal });
  const { data: skills } = useQuery({ queryKey: ['skills'], queryFn: () => apiClient.listSkills(), enabled: showCreateModal });
  const { data: manifests } = useQuery({ queryKey: ['manifests'], queryFn: () => apiClient.listManifests(), enabled: showCreateModal });
  const { data: components } = useQuery({ queryKey: ['components'], queryFn: () => apiClient.listComponents(), enabled: showCreateModal });
  const { data: queries } = useQuery({ queryKey: ['queries'], queryFn: () => apiClient.listQueries(), enabled: showCreateModal });
  const { data: collections } = useQuery({ queryKey: ['collections'], queryFn: () => apiClient.listCollections(), enabled: showCreateModal });
  const { data: webhooks } = useQuery({ queryKey: ['webhooks'], queryFn: () => apiClient.listWebhooks(), enabled: showCreateModal });
  const { data: templates } = useQuery({ queryKey: ['templates'], queryFn: () => apiClient.listTemplates(), enabled: showCreateModal });
  const { data: schedules } = useQuery({ queryKey: ['schedules'], queryFn: () => apiClient.listSchedules(), enabled: showCreateModal });
  const { data: databaseTriggers } = useQuery({ queryKey: ['database-triggers'], queryFn: () => apiClient.listDatabaseTriggers(), enabled: showCreateModal });

  const previewMutation = useMutation({
    mutationFn: (source: string) => apiClient.previewPackage(source),
    onSuccess: (data) => {
      setPreviewResult(data);
      setInstallStep('preview');
    },
  });

  const installMutation = useMutation({
    mutationFn: (source: string) => apiClient.installPackage(source),
    onSuccess: (data) => {
      setInstallResult(data);
      setInstallStep('done');
      queryClient.invalidateQueries({ queryKey: ['packages'] });
    },
  });

  const uninstallMutation = useMutation({
    mutationFn: (name: string) => apiClient.uninstallPackage(name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['packages'] });
    },
  });

  const createMutation = useMutation({
    mutationFn: (data: any) => apiClient.createPackageYaml(data),
    onSuccess: (data) => {
      setCreatedYaml(data.yaml);
    },
  });

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      setYamlInput(ev.target?.result as string);
    };
    reader.readAsText(file);
  };

  const handlePreview = () => {
    if (yamlInput.trim()) {
      previewMutation.mutate(yamlInput);
    }
  };

  const handleInstall = () => {
    setInstallStep('installing');
    installMutation.mutate(yamlInput);
  };

  const handleExport = async (name: string) => {
    try {
      const yaml = await apiClient.exportPackage(name);
      const blob = new Blob([yaml], { type: 'text/yaml' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${name}.yaml`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      // Error handled by API client
    }
  };

  const closeInstallModal = () => {
    setShowInstallModal(false);
    setInstallStep('input');
    setYamlInput('');
    setPreviewResult(null);
    setInstallResult(null);
    previewMutation.reset();
    installMutation.reset();
  };

  const toggleResource = (type: string, namespace: string, name: string) => {
    setSelectedResources(prev => {
      const exists = prev.some(r => r.type === type && r.namespace === namespace && r.name === name);
      if (exists) {
        return prev.filter(r => !(r.type === type && r.namespace === namespace && r.name === name));
      }
      return [...prev, { type, namespace, name }];
    });
  };

  const isSelected = (type: string, namespace: string, name: string) =>
    selectedResources.some(r => r.type === type && r.namespace === namespace && r.name === name);

  const handleCreate = () => {
    if (!createForm.name.trim() || selectedResources.length === 0) return;
    createMutation.mutate({
      name: createForm.name,
      version: createForm.version,
      description: createForm.description || undefined,
      author: createForm.author || undefined,
      url: createForm.url || undefined,
      resources: selectedResources,
    });
  };

  const closeCreateModal = () => {
    setShowCreateModal(false);
    setCreateForm({ name: '', version: '1.0.0', description: '', author: '', url: '' });
    setSelectedResources([]);
    setCreatedYaml('');
    createMutation.reset();
  };

  const downloadCreatedYaml = () => {
    const blob = new Blob([createdYaml], { type: 'text/yaml' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${createForm.name}.yaml`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // Build resource sections for create modal
  const resourceSections = [
    { type: 'agent', label: 'Agents', items: agents?.map((a: any) => ({ namespace: a.namespace, name: a.name })) || [] },
    { type: 'function', label: 'Functions', items: functions?.map((f: any) => ({ namespace: f.namespace, name: f.name })) || [] },
    { type: 'skill', label: 'Skills', items: skills?.map((s: any) => ({ namespace: s.namespace, name: s.name })) || [] },
    { type: 'component', label: 'Components', items: components?.map((c: any) => ({ namespace: c.namespace, name: c.name })) || [] },
    { type: 'query', label: 'Queries', items: queries?.map((q: any) => ({ namespace: q.namespace, name: q.name })) || [] },
    { type: 'collection', label: 'Collections', items: collections?.map((c: any) => ({ namespace: c.namespace, name: c.name })) || [] },
    { type: 'manifest', label: 'Manifests', items: manifests?.map((a: any) => ({ namespace: a.namespace, name: a.name })) || [] },
    { type: 'webhook', label: 'Webhooks', items: webhooks?.map((w: any) => ({ namespace: 'default', name: w.path })) || [] },
    { type: 'template', label: 'Templates', items: templates?.map((t: any) => ({ namespace: t.namespace, name: t.name })) || [] },
    { type: 'schedule', label: 'Schedules', items: schedules?.map((s: any) => ({ namespace: 'default', name: s.name })) || [] },
    { type: 'database_trigger', label: 'Database Triggers', items: databaseTriggers?.map((t: any) => ({ namespace: 'default', name: t.name })) || [] },
  ].filter(s => s.items.length > 0);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-100">Packages</h1>
          <p className="text-gray-400 mt-1">Install and manage integration packages</p>
        </div>
        <div className="flex space-x-3">
          <button onClick={() => setShowCreateModal(true)} className="btn btn-secondary flex items-center">
            <Plus className="w-5 h-5 mr-2" />
            Create Package
          </button>
          <button onClick={() => setShowInstallModal(true)} className="btn btn-primary flex items-center">
            <Upload className="w-5 h-5 mr-2" />
            Install Package
          </button>
        </div>
      </div>

      {/* Package List */}
      {isLoading ? (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
        </div>
      ) : packages && packages.length > 0 ? (
        <div className="space-y-3">
          {packages.map((pkg: any) => (
            <div key={pkg.id} className="card">
              <div className="flex items-center gap-4">
                <Package className="w-8 h-8 text-primary-400 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="font-semibold text-gray-100">{pkg.name}</h3>
                    <span className="text-xs px-2 py-0.5 bg-primary-900/30 text-primary-300 rounded font-mono">{pkg.version}</span>
                  </div>
                  {pkg.description && <p className="text-sm text-gray-400 mt-0.5">{pkg.description}</p>}
                  <div className="flex gap-3 mt-1 text-xs text-gray-500">
                    {pkg.author && <span>by {pkg.author}</span>}
                    <span>Installed {new Date(pkg.installed_at).toLocaleDateString()}</span>
                  </div>
                </div>
                <div className="flex items-center gap-1 flex-shrink-0">
                  <button
                    onClick={() => handleExport(pkg.name)}
                    className="p-2 text-gray-500 hover:text-primary-400 hover:bg-white/10 rounded-md transition-colors"
                    title="Export YAML"
                  >
                    <Download className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => {
                      if (confirm(`Are you sure you want to uninstall "${pkg.name}"? All resources created by this package will be deleted.`)) {
                        uninstallMutation.mutate(pkg.name);
                      }
                    }}
                    className="p-2 text-gray-500 hover:text-red-600 hover:bg-white/10 rounded-md transition-colors"
                    disabled={uninstallMutation.isPending}
                    title="Uninstall"
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
          <Package className="w-16 h-16 text-gray-500 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-100 mb-2">No packages installed</h3>
          <p className="text-gray-400 mb-4">Install integration packages to add agents, functions, and more</p>
          <button onClick={() => setShowInstallModal(true)} className="btn btn-primary">
            <Upload className="w-5 h-5 mr-2 inline" />
            Install Package
          </button>
        </div>
      )}

      {/* Install Modal */}
      {showInstallModal && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-[#161616] rounded-lg max-w-2xl w-full p-6 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-semibold text-gray-100">Install Package</h2>
              <button onClick={closeInstallModal} className="p-1 text-gray-500 hover:text-gray-400">
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Step 1: Input */}
            {installStep === 'input' && (
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-300 mb-2">Package YAML</label>
                  <textarea
                    value={yamlInput}
                    onChange={(e) => setYamlInput(e.target.value)}
                    placeholder="Paste SinasPackage YAML here..."
                    className="input w-full h-64 font-mono text-sm"
                  />
                </div>
                <div className="flex items-center gap-3">
                  <input ref={fileInputRef} type="file" accept=".yaml,.yml" onChange={handleFileUpload} className="hidden" />
                  <button onClick={() => fileInputRef.current?.click()} className="btn btn-secondary text-sm">
                    Upload File
                  </button>
                  <div className="flex-1" />
                  <button
                    onClick={handlePreview}
                    disabled={!yamlInput.trim() || previewMutation.isPending}
                    className="btn btn-primary flex items-center"
                  >
                    <Eye className="w-4 h-4 mr-2" />
                    {previewMutation.isPending ? 'Previewing...' : 'Preview'}
                  </button>
                </div>
                {previewMutation.isError && (
                  <div className="p-3 bg-red-900/20 border border-red-900/30 rounded text-sm text-red-400">
                    {(previewMutation.error as any)?.message || 'Preview failed'}
                  </div>
                )}
              </div>
            )}

            {/* Step 2: Preview */}
            {installStep === 'preview' && previewResult && (
              <div className="space-y-4">
                <div className="p-3 bg-blue-900/20 border border-blue-900/30 rounded">
                  <h3 className="text-sm font-medium text-blue-300 mb-2">Preview - Changes to apply:</h3>
                  {previewResult.changes && previewResult.changes.length > 0 ? (
                    <div className="space-y-1">
                      {previewResult.changes.map((change: any, i: number) => (
                        <div key={i} className="flex items-center gap-2 text-sm">
                          <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                            change.action === 'create' ? 'bg-green-900/30 text-green-400' :
                            change.action === 'update' ? 'bg-yellow-900/30 text-yellow-400' :
                            'bg-gray-800 text-gray-400'
                          }`}>
                            {change.action}
                          </span>
                          <span className="text-gray-300">{change.resourceType}</span>
                          <span className="text-gray-500 font-mono">{change.resourceName}</span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-sm text-gray-400">No changes detected</p>
                  )}
                </div>

                {previewResult.warnings && previewResult.warnings.length > 0 && (
                  <div className="p-3 bg-yellow-900/20 border border-yellow-900/30 rounded">
                    <div className="flex items-center gap-2 mb-1">
                      <AlertTriangle className="w-4 h-4 text-yellow-400" />
                      <span className="text-sm font-medium text-yellow-300">Warnings</span>
                    </div>
                    {previewResult.warnings.map((w: string, i: number) => (
                      <p key={i} className="text-sm text-yellow-400/80">{w}</p>
                    ))}
                  </div>
                )}

                <div className="flex justify-between">
                  <button onClick={() => setInstallStep('input')} className="btn btn-secondary">
                    Back
                  </button>
                  <button onClick={handleInstall} className="btn btn-primary flex items-center">
                    <Check className="w-4 h-4 mr-2" />
                    Confirm Install
                  </button>
                </div>
              </div>
            )}

            {/* Step 3: Installing */}
            {installStep === 'installing' && (
              <div className="flex items-center justify-center py-8">
                <div className="inline-block animate-spin rounded-full h-6 w-6 border-b-2 border-primary-600 mr-3"></div>
                <span className="text-gray-400">Installing package...</span>
              </div>
            )}

            {/* Step 4: Done */}
            {installStep === 'done' && installResult && (
              <div className="space-y-4">
                <div className="p-3 bg-green-900/20 border border-green-900/30 rounded">
                  <div className="flex items-center gap-2 mb-2">
                    <Check className="w-5 h-5 text-green-400" />
                    <span className="text-sm font-medium text-green-300">Package installed successfully</span>
                  </div>
                  {installResult.package && (
                    <p className="text-sm text-gray-400">
                      {installResult.package.name} v{installResult.package.version}
                    </p>
                  )}
                </div>

                {installResult.apply?.changes && installResult.apply.changes.length > 0 && (
                  <div className="space-y-1">
                    {installResult.apply.changes.filter((c: any) => c.action !== 'unchanged').map((change: any, i: number) => (
                      <div key={i} className="flex items-center gap-2 text-sm">
                        <span className={`px-1.5 py-0.5 rounded text-xs font-medium ${
                          change.action === 'create' ? 'bg-green-900/30 text-green-400' :
                          'bg-yellow-900/30 text-yellow-400'
                        }`}>
                          {change.action}
                        </span>
                        <span className="text-gray-300">{change.resourceType}</span>
                        <span className="text-gray-500 font-mono">{change.resourceName}</span>
                      </div>
                    ))}
                  </div>
                )}

                {installMutation.isError && (
                  <div className="p-3 bg-red-900/20 border border-red-900/30 rounded text-sm text-red-400">
                    {(installMutation.error as any)?.message || 'Install failed'}
                  </div>
                )}

                <div className="flex justify-end">
                  <button onClick={closeInstallModal} className="btn btn-primary">Done</button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Create Package Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-[#161616] rounded-lg max-w-3xl w-full p-6 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-xl font-semibold text-gray-100">Create Package</h2>
              <button onClick={closeCreateModal} className="p-1 text-gray-500 hover:text-gray-400">
                <X className="w-5 h-5" />
              </button>
            </div>

            {!createdYaml ? (
              <div className="space-y-4">
                {/* Metadata */}
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-1">Name *</label>
                    <input
                      value={createForm.name}
                      onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
                      placeholder="my-package"
                      className="input w-full"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-1">Version</label>
                    <input
                      value={createForm.version}
                      onChange={(e) => setCreateForm({ ...createForm, version: e.target.value })}
                      className="input w-full"
                    />
                  </div>
                  <div className="col-span-2">
                    <label className="block text-sm font-medium text-gray-300 mb-1">Description</label>
                    <input
                      value={createForm.description}
                      onChange={(e) => setCreateForm({ ...createForm, description: e.target.value })}
                      placeholder="What does this package do?"
                      className="input w-full"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-1">Author</label>
                    <input
                      value={createForm.author}
                      onChange={(e) => setCreateForm({ ...createForm, author: e.target.value })}
                      className="input w-full"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-1">URL</label>
                    <input
                      value={createForm.url}
                      onChange={(e) => setCreateForm({ ...createForm, url: e.target.value })}
                      placeholder="https://github.com/..."
                      className="input w-full"
                    />
                  </div>
                </div>

                {/* Resource Picker */}
                <div className="border-t border-white/[0.06] pt-4">
                  <h3 className="text-sm font-medium text-gray-300 mb-3">
                    Select Resources ({selectedResources.length} selected)
                  </h3>
                  <div className="space-y-3 max-h-72 overflow-y-auto">
                    {resourceSections.map(section => (
                      <div key={section.type}>
                        <h4 className="text-xs font-semibold text-gray-500 uppercase mb-1">{section.label}</h4>
                        <div className="flex flex-wrap gap-2">
                          {section.items.map((item: any) => {
                            const selected = isSelected(section.type, item.namespace, item.name);
                            return (
                              <button
                                key={`${section.type}-${item.namespace}-${item.name}`}
                                onClick={() => toggleResource(section.type, item.namespace, item.name)}
                                className={`px-3 py-1.5 rounded text-sm font-mono transition-colors ${
                                  selected
                                    ? 'bg-primary-900/30 text-primary-300 border border-primary-700'
                                    : 'bg-[#0d0d0d] text-gray-400 border border-white/[0.06] hover:border-white/[0.12]'
                                }`}
                              >
                                {item.namespace}/{item.name}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    ))}
                    {resourceSections.length === 0 && (
                      <p className="text-sm text-gray-500">No resources available</p>
                    )}
                  </div>
                </div>

                <div className="flex justify-end gap-3">
                  <button onClick={closeCreateModal} className="btn btn-secondary">Cancel</button>
                  <button
                    onClick={handleCreate}
                    disabled={!createForm.name.trim() || selectedResources.length === 0 || createMutation.isPending}
                    className="btn btn-primary"
                  >
                    {createMutation.isPending ? 'Creating...' : 'Create Package'}
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                <div className="p-3 bg-green-900/20 border border-green-900/30 rounded">
                  <div className="flex items-center gap-2">
                    <Check className="w-5 h-5 text-green-400" />
                    <span className="text-sm font-medium text-green-300">Package YAML created</span>
                  </div>
                </div>

                <div className="bg-gray-900 rounded-lg p-4 overflow-x-auto max-h-80">
                  <pre className="text-sm text-gray-100 font-mono whitespace-pre-wrap">{createdYaml}</pre>
                </div>

                <div className="flex justify-between">
                  <button
                    onClick={() => { navigator.clipboard.writeText(createdYaml); }}
                    className="btn btn-secondary text-sm"
                  >
                    Copy to Clipboard
                  </button>
                  <div className="flex gap-2">
                    <button onClick={downloadCreatedYaml} className="btn btn-secondary flex items-center text-sm">
                      <Download className="w-4 h-4 mr-2" />
                      Download
                    </button>
                    <button onClick={closeCreateModal} className="btn btn-primary text-sm">Done</button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
