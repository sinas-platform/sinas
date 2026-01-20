import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Shield, Plus, Trash2, Save } from 'lucide-react';
import { useState, useMemo } from 'react';
import type { GroupPermission } from '../types';

export function Permissions() {
  const queryClient = useQueryClient();
  const [showAddPermissionModal, setShowAddPermissionModal] = useState(false);
  const [newPermissionKey, setNewPermissionKey] = useState('');
  const [pendingChanges, setPendingChanges] = useState<Map<string, { groupName: string; permissionKey: string; value: boolean }>>(new Map());

  const { data: groups, isLoading: groupsLoading } = useQuery({
    queryKey: ['groups'],
    queryFn: () => apiClient.listGroups(),
    retry: false,
  });

  // Fetch permissions for all groups
  const permissionQueries = useQuery({
    queryKey: ['allGroupPermissions', groups?.map(g => g.name)],
    queryFn: async () => {
      if (!groups) return {};
      const permissionsMap: Record<string, GroupPermission[]> = {};

      await Promise.all(
        groups.map(async (group) => {
          try {
            const permissions = await apiClient.listGroupPermissions(group.name);
            permissionsMap[group.name] = permissions;
          } catch (error) {
            console.error(`Failed to fetch permissions for group ${group.name}:`, error);
            permissionsMap[group.name] = [];
          }
        })
      );

      return permissionsMap;
    },
    enabled: !!groups && groups.length > 0,
    retry: false,
  });

  const setPermissionMutation = useMutation({
    mutationFn: ({ groupName, permissionKey, value }: { groupName: string; permissionKey: string; value: boolean }) =>
      apiClient.setGroupPermission(groupName, { permission_key: permissionKey, permission_value: value }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['allGroupPermissions'] });
    },
  });

  const deletePermissionMutation = useMutation({
    mutationFn: ({ groupName, permissionKey }: { groupName: string; permissionKey: string }) =>
      apiClient.deleteGroupPermission(groupName, permissionKey),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['allGroupPermissions'] });
    },
  });

  // Get all unique permission keys across all groups
  const allPermissionKeys = useMemo(() => {
    if (!permissionQueries.data) return [];

    const keysSet = new Set<string>();
    Object.values(permissionQueries.data).forEach((permissions) => {
      permissions.forEach((perm) => keysSet.add(perm.permission_key));
    });

    return Array.from(keysSet).sort();
  }, [permissionQueries.data]);

  // Check if group is admin
  const isAdminGroup = (groupName: string): boolean => {
    return groupName.toLowerCase() === 'admin' || groupName.toLowerCase() === 'admins';
  };

  // Check if a permission is enabled for a group
  const isPermissionEnabled = (groupName: string, permissionKey: string): boolean => {
    const changeKey = `${groupName}:${permissionKey}`;
    if (pendingChanges.has(changeKey)) {
      return pendingChanges.get(changeKey)!.value;
    }

    if (!permissionQueries.data) return false;
    const groupPerms = permissionQueries.data[groupName] || [];
    const perm = groupPerms.find((p) => p.permission_key === permissionKey);
    return perm?.permission_value || false;
  };

  // Toggle permission (add to pending changes)
  const togglePermission = (groupName: string, permissionKey: string) => {
    // Don't allow editing admin group permissions
    if (isAdminGroup(groupName)) return;

    const currentValue = isPermissionEnabled(groupName, permissionKey);
    const newValue = !currentValue;
    const changeKey = `${groupName}:${permissionKey}`;

    const newChanges = new Map(pendingChanges);
    newChanges.set(changeKey, { groupName, permissionKey, value: newValue });
    setPendingChanges(newChanges);
  };

  // Apply all pending changes
  const applyChanges = async () => {
    for (const change of pendingChanges.values()) {
      try {
        await setPermissionMutation.mutateAsync(change);
      } catch (error) {
        console.error(`Failed to update permission ${change.permissionKey} for group ${change.groupName}:`, error);
      }
    }
    setPendingChanges(new Map());
  };

  // Cancel pending changes
  const cancelChanges = () => {
    setPendingChanges(new Map());
  };

  // Add a new custom permission
  const handleAddPermission = async () => {
    if (!newPermissionKey.trim()) return;

    // Add this permission to all groups as false by default
    if (groups) {
      for (const group of groups) {
        try {
          await setPermissionMutation.mutateAsync({
            groupName: group.name,
            permissionKey: newPermissionKey.trim(),
            value: false,
          });
        } catch (error) {
          console.error(`Failed to add permission to group ${group.name}:`, error);
        }
      }
    }

    setNewPermissionKey('');
    setShowAddPermissionModal(false);
  };

  // Delete a permission from all groups
  const handleDeletePermission = async (permissionKey: string) => {
    if (!confirm(`Are you sure you want to delete the permission "${permissionKey}" from all groups?`)) {
      return;
    }

    if (groups) {
      for (const group of groups) {
        try {
          await deletePermissionMutation.mutateAsync({ groupName: group.name, permissionKey });
        } catch (error) {
          console.error(`Failed to delete permission from group ${group.name}:`, error);
        }
      }
    }
  };

  const isLoading = groupsLoading || permissionQueries.isLoading;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Permissions Matrix</h1>
          <p className="text-gray-600 mt-1">Manage group permissions across your organization</p>
        </div>
        <div className="flex items-center gap-3">
          {pendingChanges.size > 0 && (
            <div className="flex items-center gap-2">
              <span className="text-sm text-gray-600">{pendingChanges.size} pending change{pendingChanges.size > 1 ? 's' : ''}</span>
              <button onClick={cancelChanges} className="btn btn-secondary text-sm">
                Cancel
              </button>
              <button onClick={applyChanges} className="btn btn-primary text-sm flex items-center">
                <Save className="w-4 h-4 mr-2" />
                Apply Changes
              </button>
            </div>
          )}
          <button
            onClick={() => setShowAddPermissionModal(true)}
            className="btn btn-primary flex items-center"
          >
            <Plus className="w-5 h-5 mr-2" />
            Add Permission
          </button>
        </div>
      </div>

      {/* Matrix */}
      {isLoading ? (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
          <p className="text-gray-600 mt-2">Loading permissions...</p>
        </div>
      ) : !groups || groups.length === 0 ? (
        <div className="text-center py-12 card">
          <Shield className="w-16 h-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">No groups yet</h3>
          <p className="text-gray-600">Create groups to manage permissions</p>
        </div>
      ) : allPermissionKeys.length === 0 ? (
        <div className="text-center py-12 card">
          <Shield className="w-16 h-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">No permissions yet</h3>
          <p className="text-gray-600 mb-4">Add custom permissions to get started</p>
          <button onClick={() => setShowAddPermissionModal(true)} className="btn btn-primary">
            <Plus className="w-5 h-5 mr-2 inline" />
            Add Permission
          </button>
        </div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="text-left py-3 px-4 font-semibold text-gray-700 bg-gray-50 sticky left-0 z-10 min-w-[250px]">
                  Permission
                </th>
                {groups.map((group) => (
                  <th key={group.id} className="text-center py-3 px-4 font-semibold text-gray-700 bg-gray-50 min-w-[120px]">
                    <div className="flex flex-col items-center">
                      <div className="flex items-center gap-1">
                        <span className="truncate max-w-[100px]" title={group.name}>{group.name}</span>
                        {isAdminGroup(group.name) && (
                          <span className="px-1.5 py-0.5 bg-red-100 text-red-800 text-xs font-medium rounded">Admin</span>
                        )}
                      </div>
                      {group.description && (
                        <span className="text-xs text-gray-500 font-normal truncate max-w-[100px]" title={group.description}>
                          {group.description}
                        </span>
                      )}
                    </div>
                  </th>
                ))}
                <th className="text-center py-3 px-4 font-semibold text-gray-700 bg-gray-50 w-[80px]">Actions</th>
              </tr>
            </thead>
            <tbody>
              {allPermissionKeys.map((permissionKey) => (
                <tr key={permissionKey} className="border-b border-gray-100 hover:bg-gray-50">
                  <td className="py-3 px-4 font-mono text-sm text-gray-900 sticky left-0 bg-white z-10">
                    {permissionKey}
                  </td>
                  {groups.map((group) => {
                    const enabled = isPermissionEnabled(group.name, permissionKey);
                    const changeKey = `${group.name}:${permissionKey}`;
                    const hasChange = pendingChanges.has(changeKey);
                    const isAdmin = isAdminGroup(group.name);

                    return (
                      <td key={group.id} className="py-3 px-4 text-center">
                        <div className="flex items-center justify-center">
                          <input
                            type="checkbox"
                            checked={enabled}
                            onChange={() => togglePermission(group.name, permissionKey)}
                            disabled={isAdmin}
                            className={`w-5 h-5 rounded border-gray-300 text-primary-600 focus:ring-primary-500 ${
                              isAdmin ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'
                            } ${hasChange ? 'ring-2 ring-yellow-400' : ''}`}
                            title={isAdmin ? 'Admin permissions are read-only' : ''}
                          />
                        </div>
                      </td>
                    );
                  })}
                  <td className="py-3 px-4 text-center">
                    <button
                      onClick={() => handleDeletePermission(permissionKey)}
                      className="text-red-600 hover:text-red-700 p-1"
                      title="Delete permission from all groups"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Add Permission Modal */}
      {showAddPermissionModal && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6">
            <h2 className="text-xl font-semibold text-gray-900 mb-4">Add Custom Permission</h2>
            <div className="space-y-4">
              <div>
                <label htmlFor="permission_key" className="block text-sm font-medium text-gray-700 mb-2">
                  Permission Key *
                </label>
                <input
                  id="permission_key"
                  type="text"
                  value={newPermissionKey}
                  onChange={(e) => setNewPermissionKey(e.target.value)}
                  placeholder="e.g., custom.feature.access"
                  className="input"
                  autoFocus
                />
                <p className="text-xs text-gray-500 mt-1">
                  Use dot notation (e.g., namespace.resource.action)
                </p>
              </div>

              <div className="flex justify-end space-x-3 pt-4">
                <button
                  type="button"
                  onClick={() => {
                    setShowAddPermissionModal(false);
                    setNewPermissionKey('');
                  }}
                  className="btn btn-secondary"
                >
                  Cancel
                </button>
                <button
                  onClick={handleAddPermission}
                  className="btn btn-primary"
                  disabled={!newPermissionKey.trim()}
                >
                  Add Permission
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
