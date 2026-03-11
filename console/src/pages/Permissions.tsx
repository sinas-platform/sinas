import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Shield, Plus, Trash2, Save, ChevronDown, ChevronRight } from 'lucide-react';
import { useState, useMemo, useCallback } from 'react';
import type { RolePermission } from '../types';



type PermState = 'none' | 'own' | 'all';

interface RegistryEntry {
  resource: string;
  description: string;
  actions: string[];
  namespaced?: boolean;
  adminOnly?: boolean;
}

// Parse "sinas.agents/*/*.create:own" → { resource: "agents", path: "*/*", action: "create", scope: "own" }
// Parse "sinas.users.read:all" → { resource: "users", path: null, action: "read", scope: "all" }
function parsePermKey(key: string): { resource: string; path: string | null; action: string; scope: 'own' | 'all' } | null {
  // Namespaced: sinas.{resource}/{path}.{action}:{scope}
  const nsMatch = key.match(/^sinas\.([^/.]+)\/([^.]+)\.([^:]+):(own|all)$/);
  if (nsMatch) return { resource: nsMatch[1], path: nsMatch[2], action: nsMatch[3], scope: nsMatch[4] as 'own' | 'all' };
  // Non-namespaced: sinas.{resource}.{action}:{scope}
  const match = key.match(/^sinas\.([^.]+)\.([^:]+):(own|all)$/);
  if (match) return { resource: match[1], action: match[2], scope: match[3] as 'own' | 'all', path: null };
  return null;
}

function buildKey(resource: string, path: string | null, action: string, scope: string): string {
  if (path) return `sinas.${resource}/${path}.${action}:${scope}`;
  return `sinas.${resource}.${action}:${scope}`;
}

export function Permissions() {
  const queryClient = useQueryClient();
  const [showAddPermissionModal, setShowAddPermissionModal] = useState(false);
  const [newPermissions, setNewPermissions] = useState<string[]>([]);
  const [pendingChanges, setPendingChanges] = useState<Map<string, { groupName: string; permissionKey: string; value: boolean }>>(new Map());
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [expandedOverrides, setExpandedOverrides] = useState<Set<string>>(new Set());
  const [overrideInputs, setOverrideInputs] = useState<Record<string, string>>({});

  const { data: permissionRegistry } = useQuery({
    queryKey: ['permissionRegistry'],
    queryFn: () => apiClient.getPermissionReference(),
    retry: false,
    staleTime: 5 * 60 * 1000,
  });

  const { data: roles, isLoading: rolesLoading } = useQuery({
    queryKey: ['groups'],
    queryFn: () => apiClient.listRoles(),
    retry: false,
  });

  const permissionQueries = useQuery({
    queryKey: ['allRolePermissions', roles?.map(g => g.name)],
    queryFn: async () => {
      if (!roles) return {};
      const map: Record<string, RolePermission[]> = {};
      await Promise.all(roles.map(async (role) => {
        try { map[role.name] = await apiClient.listRolePermissions(role.name); }
        catch { map[role.name] = []; }
      }));
      return map;
    },
    enabled: !!roles && roles.length > 0,
    retry: false,
  });

  const setPermissionMutation = useMutation({
    mutationFn: ({ groupName, permissionKey, value }: { groupName: string; permissionKey: string; value: boolean }) =>
      apiClient.setRolePermission(groupName, { permission_key: permissionKey, permission_value: value }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['allRolePermissions'] }),
  });

  const deletePermissionMutation = useMutation({
    mutationFn: ({ groupName, permissionKey }: { groupName: string; permissionKey: string }) =>
      apiClient.deleteRolePermission(groupName, permissionKey),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['allRolePermissions'] }),
  });

  const editableRoles = useMemo(() => {
    if (!roles) return [];
    return roles.filter(r => !['admin', 'admins'].includes(r.name.toLowerCase()));
  }, [roles]);

  const isEnabled = useCallback((roleName: string, key: string): boolean => {
    const ck = `${roleName}:${key}`;
    if (pendingChanges.has(ck)) return pendingChanges.get(ck)!.value;
    if (!permissionQueries.data) return false;
    const p = permissionQueries.data[roleName]?.find(x => x.permission_key === key);
    return p?.permission_value || false;
  }, [pendingChanges, permissionQueries.data]);

  const getState = useCallback((resource: string, path: string | null, action: string, roleName: string): PermState => {
    if (isEnabled(roleName, buildKey(resource, path, action, 'all'))) return 'all';
    if (isEnabled(roleName, buildKey(resource, path, action, 'own'))) return 'own';
    return 'none';
  }, [isEnabled]);

  const cycleState = (resource: string, path: string | null, action: string, roleName: string) => {
    const current = getState(resource, path, action, roleName);
    const ownKey = buildKey(resource, path, action, 'own');
    const allKey = buildKey(resource, path, action, 'all');
    const next = new Map(pendingChanges);

    if (current === 'none') {
      next.set(`${roleName}:${ownKey}`, { groupName: roleName, permissionKey: ownKey, value: true });
      next.set(`${roleName}:${allKey}`, { groupName: roleName, permissionKey: allKey, value: false });
    } else if (current === 'own') {
      next.set(`${roleName}:${ownKey}`, { groupName: roleName, permissionKey: ownKey, value: false });
      next.set(`${roleName}:${allKey}`, { groupName: roleName, permissionKey: allKey, value: true });
    } else {
      next.set(`${roleName}:${ownKey}`, { groupName: roleName, permissionKey: ownKey, value: false });
      next.set(`${roleName}:${allKey}`, { groupName: roleName, permissionKey: allKey, value: false });
    }
    setPendingChanges(next);
  };

  const applyChanges = async () => {
    for (const c of pendingChanges.values()) {
      try { await setPermissionMutation.mutateAsync(c); } catch { /* ignore */ }
    }
    setPendingChanges(new Map());
  };

  const handleAddPermissions = async () => {
    if (!newPermissions.length || !editableRoles.length) return;
    for (const perm of newPermissions) {
      for (const role of editableRoles) {
        try { await setPermissionMutation.mutateAsync({ groupName: role.name, permissionKey: perm, value: false }); } catch { /* ignore */ }
      }
    }
    setNewPermissions([]);
    setShowAddPermissionModal(false);
  };

  const deleteKeys = async (keys: string[]) => {
    if (!editableRoles.length) return;
    for (const key of keys) {
      for (const role of editableRoles) {
        try { await deletePermissionMutation.mutateAsync({ groupName: role.name, permissionKey: key }); } catch { /* ignore */ }
      }
    }
  };

  // Add an override for a namespaced resource
  const addOverride = async (resource: string, nsPath: string) => {
    if (!editableRoles.length || !permissionRegistry) return;
    const entry = permissionRegistry.find((e: RegistryEntry) => e.resource === resource);
    if (!entry) return;
    for (const action of entry.actions) {
      for (const scope of ['own', 'all'] as const) {
        const key = buildKey(resource, nsPath, action, scope);
        for (const role of editableRoles) {
          try { await setPermissionMutation.mutateAsync({ groupName: role.name, permissionKey: key, value: false }); } catch { /* ignore */ }
        }
      }
    }
  };

  // Collect existing overrides (specific namespace/name paths per resource)
  const overridesByResource = useMemo(() => {
    if (!permissionQueries.data) return new Map<string, Set<string>>();
    const map = new Map<string, Set<string>>();
    for (const perms of Object.values(permissionQueries.data)) {
      for (const p of perms) {
        const parsed = parsePermKey(p.permission_key);
        if (!parsed || !parsed.path || parsed.path === '*/*') continue;
        if (!map.has(parsed.resource)) map.set(parsed.resource, new Set());
        map.get(parsed.resource)!.add(parsed.path);
      }
    }
    return map;
  }, [permissionQueries.data]);

  // Custom permissions (not matching any registry entry)
  const customPermissions = useMemo(() => {
    if (!permissionQueries.data || !permissionRegistry) return [];
    const allKeys = new Set<string>();
    for (const perms of Object.values(permissionQueries.data)) {
      for (const p of perms) allKeys.add(p.permission_key);
    }
    const custom: string[] = [];
    for (const key of allKeys) {
      if (key === 'sinas.*:all' || key === 'sinas.*:own') continue;
      const parsed = parsePermKey(key);
      if (!parsed) { custom.push(key); continue; }
      const entry = permissionRegistry.find((e: RegistryEntry) => e.resource === parsed.resource);
      if (!entry || !entry.actions.includes(parsed.action)) custom.push(key);
    }
    return [...new Set(custom)].sort();
  }, [permissionQueries.data, permissionRegistry]);

  const toggleCollapse = (key: string) => {
    setCollapsedGroups(prev => { const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n; });
  };

  const toggleOverrides = (resource: string) => {
    setExpandedOverrides(prev => { const n = new Set(prev); n.has(resource) ? n.delete(resource) : n.add(resource); return n; });
  };

  const isLoading = rolesLoading || permissionQueries.isLoading;

  const stateStyle: Record<PermState, string> = {
    none: 'bg-white/[0.04] text-gray-500',
    own: 'bg-emerald-950 text-emerald-400',
    all: 'bg-blue-950 text-blue-400',
  };

  const crudOrder = ['create', 'read', 'update', 'delete'];
  const sortActions = (actions: string[]) => {
    return [...actions].sort((a, b) => {
      const ai = crudOrder.indexOf(a), bi = crudOrder.indexOf(b);
      if (ai !== -1 && bi !== -1) return ai - bi;
      if (ai !== -1) return -1;
      if (bi !== -1) return 1;
      return a.localeCompare(b);
    });
  };

  const registryCategories = useMemo(() => {
    if (!permissionRegistry) return { core: [], admin: [] };
    const sorted = [...permissionRegistry].sort((a: RegistryEntry, b: RegistryEntry) => a.resource.localeCompare(b.resource));
    return {
      core: sorted.filter((e: RegistryEntry) => !e.adminOnly),
      admin: sorted.filter((e: RegistryEntry) => e.adminOnly),
    };
  }, [permissionRegistry]);

  // Render action pills for a given resource/path/role
  const ActionPills = ({ resource, path, actions, roleName }: {
    resource: string; path: string | null; actions: string[]; roleName: string;
  }) => (
    <div className="flex flex-wrap gap-1">
      {sortActions(actions).map((action) => {
        const state = getState(resource, path, action, roleName);
        const ownKey = buildKey(resource, path, action, 'own');
        const allKey = buildKey(resource, path, action, 'all');
        const changed = pendingChanges.has(`${roleName}:${ownKey}`) || pendingChanges.has(`${roleName}:${allKey}`);
        return (
          <button
            key={action}
            onClick={() => cycleState(resource, path, action, roleName)}
            className={`px-1.5 py-0.5 text-[11px] rounded font-mono transition-all ${stateStyle[state]} ${
              changed ? 'ring-1 ring-yellow-400/70' : ''
            } hover:brightness-150`}
            title={`${action}: ${state === 'none' ? 'disabled' : ':' + state} — click to cycle`}
          >
            {action}
          </button>
        );
      })}
    </div>
  );

  // Render a resource row (wildcard or specific override)
  const PermissionRow = ({ resource, path, label, actions, indent }: {
    resource: string; path: string | null; label: string; actions: string[]; indent?: boolean;
  }) => (
    <tr className="border-b border-white/[0.04] hover:bg-white/[0.02]">
      <td className={`py-2 px-4 sticky left-0 bg-[#161616] z-10 ${indent ? 'pl-10' : ''}`}>
        <span className={`text-sm font-mono ${indent ? 'text-gray-400' : 'text-gray-200'}`}>{label}</span>
      </td>
      {editableRoles.map((role) => (
        <td key={role.id} className="py-2 px-3">
          <ActionPills resource={resource} path={path} actions={actions} roleName={role.name} />
        </td>
      ))}
      <td className="py-2 px-2">
        {indent && path && (
          <button
            onClick={async () => {
              if (!confirm(`Delete overrides for ${resource}/${path}?`)) return;
              const keys: string[] = [];
              for (const action of actions) {
                keys.push(buildKey(resource, path, action, 'own'));
                keys.push(buildKey(resource, path, action, 'all'));
              }
              await deleteKeys(keys);
            }}
            className="text-red-600/40 hover:text-red-400 p-1"
            title="Delete override"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}
      </td>
    </tr>
  );

  // Render rows for a resource entry (inline to avoid remount/focus issues)
  const renderResourceRows = (entry: RegistryEntry) => {
    const overrides = overridesByResource.get(entry.resource);
    const hasOverrides = overrides && overrides.size > 0;
    const showOverrides = expandedOverrides.has(entry.resource);
    const inputVal = overrideInputs[entry.resource] || '';

    return (
      <tbody key={entry.resource}>
        {/* Main wildcard row */}
        <tr className="border-b border-white/[0.04] hover:bg-white/[0.02]">
          <td className="py-2 px-4 sticky left-0 bg-[#161616] z-10">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-gray-100">{entry.description}</span>
              <span className="text-[11px] text-gray-500 font-mono">{entry.resource}</span>
            </div>
          </td>
          {editableRoles.map((role) => (
            <td key={role.id} className="py-2 px-3">
              <ActionPills
                resource={entry.resource}
                path={entry.namespaced ? '*/*' : null}
                actions={entry.actions}
                roleName={role.name}
              />
            </td>
          ))}
          <td className="py-2 px-2">
            {entry.namespaced && (
              <button
                onClick={() => toggleOverrides(entry.resource)}
                className="text-gray-600 hover:text-gray-300 p-1"
                title={showOverrides ? 'Hide overrides' : 'Show overrides'}
              >
                {showOverrides ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
              </button>
            )}
          </td>
        </tr>

        {/* Overrides section for namespaced resources */}
        {entry.namespaced && showOverrides && (
          <>
            {hasOverrides && Array.from(overrides!).sort().map(path => (
              <PermissionRow
                key={path}
                resource={entry.resource}
                path={path}
                label={path}
                actions={entry.actions}
                indent
              />
            ))}

            {/* Add override input */}
            <tr className="border-b border-white/[0.04]">
              <td className="py-1.5 px-4 pl-10 sticky left-0 bg-[#161616] z-10" colSpan={editableRoles.length + 2}>
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    value={inputVal}
                    onChange={(e) => setOverrideInputs(prev => ({ ...prev, [entry.resource]: e.target.value }))}
                    onKeyDown={async (e) => {
                      if (e.key === 'Enter' && inputVal.trim()) {
                        await addOverride(entry.resource, inputVal.trim());
                        setOverrideInputs(prev => ({ ...prev, [entry.resource]: '' }));
                      }
                    }}
                    placeholder="namespace/name"
                    className="input text-sm font-mono py-1 px-2 w-48"
                  />
                  <button
                    onClick={async () => {
                      if (inputVal.trim()) {
                        await addOverride(entry.resource, inputVal.trim());
                        setOverrideInputs(prev => ({ ...prev, [entry.resource]: '' }));
                      }
                    }}
                    disabled={!inputVal.trim()}
                    className="btn btn-secondary text-xs py-1 px-2 whitespace-nowrap"
                  >
                    <Plus className="w-3.5 h-3.5" />
                    Add
                  </button>
                </div>
              </td>
            </tr>
          </>
        )}
      </tbody>
    );
  };

  const renderCategoryGroup = (title: string, entries: RegistryEntry[], categoryKey: string) => {
    if (!entries.length) return null;
    const collapsed = collapsedGroups.has(categoryKey);
    return (
      <tbody key={categoryKey}>
        <tr className="cursor-pointer hover:bg-white/[0.02]" onClick={() => toggleCollapse(categoryKey)}>
          <td colSpan={editableRoles.length + 2} className="py-2 px-4">
            <div className="flex items-center gap-2">
              {collapsed ? <ChevronRight className="w-4 h-4 text-gray-500" /> : <ChevronDown className="w-4 h-4 text-gray-500" />}
              <span className="text-xs font-semibold text-gray-400 uppercase tracking-wider">{title}</span>
              <span className="text-[11px] text-gray-600">({entries.length})</span>
            </div>
          </td>
        </tr>
      </tbody>
    );
  };

  return (
    <div className="space-y-6">
      {/* Actions bar */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4 text-[11px] text-gray-500">
          <span className="flex items-center gap-1.5">
            <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono ${stateStyle.none}`}>off</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono ${stateStyle.own}`}>:own</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono ${stateStyle.all}`}>:all</span>
          </span>
          <span className="text-gray-600">click to cycle</span>
        </div>
        {pendingChanges.size > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-sm text-gray-400">{pendingChanges.size} change{pendingChanges.size > 1 ? 's' : ''}</span>
            <button onClick={() => setPendingChanges(new Map())} className="btn btn-secondary text-sm">Cancel</button>
            <button onClick={applyChanges} className="btn btn-primary text-sm flex items-center">
              <Save className="w-4 h-4 mr-2" />Apply
            </button>
          </div>
        )}
      </div>

      {/* Matrix */}
      {isLoading ? (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
          <p className="text-gray-400 mt-2">Loading permissions...</p>
        </div>
      ) : !editableRoles.length ? (
        <div className="text-center py-12 card">
          <Shield className="w-16 h-16 text-gray-500 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-100 mb-2">No editable roles</h3>
          <p className="text-gray-400">Create non-admin roles to manage permissions</p>
        </div>
      ) : (
        <div className="card overflow-x-auto p-0">
          <table className="w-full">
            <thead>
              <tr className="border-b border-white/[0.06]">
                <th className="text-left py-3 px-4 font-semibold text-gray-300 bg-[#0d0d0d] sticky left-0 z-10 min-w-[220px]">
                  Resource
                </th>
                {editableRoles.map((role) => (
                  <th key={role.id} className="text-left py-3 px-3 font-semibold text-gray-300 bg-[#0d0d0d] min-w-[200px]">
                    {role.name}
                  </th>
                ))}
                <th className="bg-[#0d0d0d] w-[40px]"></th>
              </tr>
            </thead>
            {renderCategoryGroup('Resources', registryCategories.core, 'core')}
            {!collapsedGroups.has('core') && registryCategories.core.map(entry => renderResourceRows(entry))}
            {renderCategoryGroup('Administration', registryCategories.admin, 'admin')}
            {!collapsedGroups.has('admin') && registryCategories.admin.map(entry => renderResourceRows(entry))}
            <tbody>

              {/* Custom permissions */}
              <tr className="hover:bg-white/[0.02]">
                <td colSpan={editableRoles.length + 2} className="py-2 px-4">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 cursor-pointer" onClick={() => toggleCollapse('custom')}>
                      {collapsedGroups.has('custom') ? <ChevronRight className="w-4 h-4 text-gray-500" /> : <ChevronDown className="w-4 h-4 text-gray-500" />}
                      <span className="text-xs font-semibold text-amber-400/80 uppercase tracking-wider">Custom</span>
                      {customPermissions.length > 0 && (
                        <span className="text-[11px] text-gray-600">({customPermissions.length})</span>
                      )}
                    </div>
                    <button onClick={() => setShowAddPermissionModal(true)} className="btn btn-secondary text-xs py-1 px-2">
                      <Plus className="w-3.5 h-3.5" />
                      Add Custom Permission
                    </button>
                  </div>
                </td>
              </tr>
              {!collapsedGroups.has('custom') && customPermissions.map((key) => (
                <tr key={key} className="border-b border-white/[0.04] hover:bg-white/[0.02]">
                  <td className="py-2 px-4 sticky left-0 bg-[#161616] z-10">
                    <span className="text-sm font-mono text-gray-400">{key}</span>
                  </td>
                  {editableRoles.map((role) => {
                    const enabled = isEnabled(role.name, key);
                    const changed = pendingChanges.has(`${role.name}:${key}`);
                    return (
                      <td key={role.id} className="py-2 px-3">
                        <input
                          type="checkbox"
                          checked={enabled}
                          onChange={() => {
                            const n = new Map(pendingChanges);
                            n.set(`${role.name}:${key}`, { groupName: role.name, permissionKey: key, value: !enabled });
                            setPendingChanges(n);
                          }}
                          className={`w-4 h-4 rounded border-white/10 text-emerald-600 focus:ring-emerald-500 cursor-pointer ${changed ? 'ring-1 ring-yellow-400' : ''}`}
                        />
                      </td>
                    );
                  })}
                  <td className="py-2 px-2">
                    <button
                      onClick={async () => {
                        if (confirm(`Delete "${key}" from all roles?`)) await deleteKeys([key]);
                      }}
                      className="text-red-600/40 hover:text-red-400 p-1"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Add Custom Permission Modal */}
      {showAddPermissionModal && (
        <>
          <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50" onClick={() => { setShowAddPermissionModal(false); setNewPermissions([]); }} />
          <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
            <div className="bg-[#161616] rounded-lg max-w-lg w-full p-6 pointer-events-auto">
              <h2 className="text-xl font-semibold text-gray-100 mb-2">Add Custom Permission</h2>
              <p className="text-sm text-gray-500 mb-4">
                Add permissions not covered by the built-in resource types. Use the format <code className="text-gray-400">sinas.resource.action:scope</code> or any custom key for application-specific permissions.
              </p>

              {/* Added permissions */}
              {newPermissions.length > 0 && (
                <div className="mb-3 flex flex-wrap gap-1.5">
                  {newPermissions.map((p) => (
                    <span key={p} className="inline-flex items-center gap-1 px-2 py-1 bg-white/[0.06] text-gray-300 text-xs rounded font-mono">
                      {p}
                      <button onClick={() => setNewPermissions(prev => prev.filter(x => x !== p))} className="text-gray-500 hover:text-gray-300">
                        &times;
                      </button>
                    </span>
                  ))}
                </div>
              )}

              <input
                type="text"
                autoFocus
                placeholder="e.g. myapp.reports.read:own"
                className="input w-full font-mono text-sm mb-4"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    const val = (e.target as HTMLInputElement).value.trim();
                    if (val && !newPermissions.includes(val)) {
                      setNewPermissions(prev => [...prev, val]);
                      (e.target as HTMLInputElement).value = '';
                    }
                  }
                }}
              />

              <div className="flex justify-end gap-3">
                <button onClick={() => { setShowAddPermissionModal(false); setNewPermissions([]); }} className="btn btn-secondary">Cancel</button>
                <button onClick={handleAddPermissions} className="btn btn-primary" disabled={!newPermissions.length}>
                  Add {newPermissions.length > 1 ? `${newPermissions.length} Permissions` : 'Permission'}
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
