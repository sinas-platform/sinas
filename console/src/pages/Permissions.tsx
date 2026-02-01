import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Shield, Plus, Trash2, Save } from 'lucide-react';
import { useState, useMemo } from 'react';
import type { RolePermission } from '../types';

export function Permissions() {
  const queryClient = useQueryClient();
  const [showAddPermissionModal, setShowAddPermissionModal] = useState(false);
  const [newPermissionKey, setNewPermissionKey] = useState('');
  const [pendingChanges, setPendingChanges] = useState<Map<string, { groupName: string; permissionKey: string; value: boolean }>>(new Map());

  const { data: roles, isLoading: rolesLoading } = useQuery({
    queryKey: ['groups'],
    queryFn: () => apiClient.listRoles(),
    retry: false,
  });

  // Fetch permissions for all groups
  const permissionQueries = useQuery({
    queryKey: ['allRolePermissions', roles?.map(g => g.name)],
    queryFn: async () => {
      if (!roles) return {};
      const permissionsMap: Record<string, RolePermission[]> = {};

      await Promise.all(
        roles.map(async (role) => {
          try {
            const permissions = await apiClient.listRolePermissions(role.name);
            permissionsMap[role.name] = permissions;
          } catch (error) {
            console.error(`Failed to fetch permissions for role ${role.name}:`, error);
            permissionsMap[role.name] = [];
          }
        })
      );

      return permissionsMap;
    },
    enabled: !!roles && roles.length > 0,
    retry: false,
  });

  const setPermissionMutation = useMutation({
    mutationFn: ({ groupName, permissionKey, value }: { groupName: string; permissionKey: string; value: boolean }) =>
      apiClient.setRolePermission(groupName, { permission_key: permissionKey, permission_value: value }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['allRolePermissions'] });
    },
  });

  const deletePermissionMutation = useMutation({
    mutationFn: ({ groupName, permissionKey }: { groupName: string; permissionKey: string }) =>
      apiClient.deleteRolePermission(groupName, permissionKey),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['allRolePermissions'] });
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

  // Check if role is admin
  const isAdminRole = (roleName: string): boolean => {
    return roleName.toLowerCase() === 'admin' || roleName.toLowerCase() === 'admins';
  };

  // Check if a permission is enabled for a role
  const isPermissionEnabled = (roleName: string, permissionKey: string): boolean => {
    const changeKey = `${roleName}:${permissionKey}`;
    if (pendingChanges.has(changeKey)) {
      return pendingChanges.get(changeKey)!.value;
    }

    if (!permissionQueries.data) return false;
    const rolePerms = permissionQueries.data[roleName] || [];
    const perm = rolePerms.find((p) => p.permission_key === permissionKey);
    return perm?.permission_value || false;
  };

  // Toggle permission (add to pending changes)
  const togglePermission = (roleName: string, permissionKey: string) => {
    // Don't allow editing admin role permissions
    if (isAdminRole(roleName)) return;

    const currentValue = isPermissionEnabled(roleName, permissionKey);
    const newValue = !currentValue;
    const changeKey = `${roleName}:${permissionKey}`;

    const newChanges = new Map(pendingChanges);
    newChanges.set(changeKey, { groupName: roleName, permissionKey, value: newValue });
    setPendingChanges(newChanges);
  };

  // Apply all pending changes
  const applyChanges = async () => {
    for (const change of pendingChanges.values()) {
      try {
        await setPermissionMutation.mutateAsync(change);
      } catch (error) {
        console.error(`Failed to update permission ${change.permissionKey} for role ${change.groupName}:`, error);
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
    if (roles) {
      for (const role of roles) {
        try {
          await setPermissionMutation.mutateAsync({
            groupName: role.name,
            permissionKey: newPermissionKey.trim(),
            value: false,
          });
        } catch (error) {
          console.error(`Failed to add permission to role ${role.name}:`, error);
        }
      }
    }

    setNewPermissionKey('');
    setShowAddPermissionModal(false);
  };

  // Delete a permission from all groups
  const handleDeletePermission = async (permissionKey: string) => {
    if (!confirm(`Are you sure you want to delete the permission "${permissionKey}" from all roles?`)) {
      return;
    }

    if (roles) {
      for (const role of roles) {
        try {
          await deletePermissionMutation.mutateAsync({ groupName: role.name, permissionKey });
        } catch (error) {
          console.error(`Failed to delete permission from role ${role.name}:`, error);
        }
      }
    }
  };

  const isLoading = rolesLoading || permissionQueries.isLoading;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Permissions Matrix</h1>
          <p className="text-gray-600 mt-1">Manage role permissions across your organization</p>
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
      ) : !roles || roles.length === 0 ? (
        <div className="text-center py-12 card">
          <Shield className="w-16 h-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">No roles yet</h3>
          <p className="text-gray-600">Create roles to manage permissions</p>
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
                {roles.map((role) => (
                  <th key={role.id} className="text-center py-3 px-4 font-semibold text-gray-700 bg-gray-50 min-w-[120px]">
                    <div className="flex flex-col items-center">
                      <div className="flex items-center gap-1">
                        <span className="truncate max-w-[100px]" title={role.name}>{role.name}</span>
                        {isAdminRole(role.name) && (
                          <span className="px-1.5 py-0.5 bg-red-100 text-red-800 text-xs font-medium rounded">Admin</span>
                        )}
                      </div>
                      {role.description && (
                        <span className="text-xs text-gray-500 font-normal truncate max-w-[100px]" title={role.description}>
                          {role.description}
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
                  {roles.map((role) => {
                    const enabled = isPermissionEnabled(role.name, permissionKey);
                    const changeKey = `${role.name}:${permissionKey}`;
                    const hasChange = pendingChanges.has(changeKey);
                    const isAdmin = isAdminRole(role.name);

                    return (
                      <td key={role.id} className="py-3 px-4 text-center">
                        <div className="flex items-center justify-center">
                          <input
                            type="checkbox"
                            checked={enabled}
                            onChange={() => togglePermission(role.name, permissionKey)}
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
