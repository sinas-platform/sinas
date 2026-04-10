import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Users as UsersIcon, UserPlus, Edit2, Trash2, Shield, UserMinus, Plus, Lock, Search, AlertTriangle, ChevronLeft, ChevronRight, X } from 'lucide-react';
import { Permissions as PermissionsTab } from './Permissions';

type Tab = 'users' | 'roles' | 'permissions';

export function Users() {
  const [activeTab, setActiveTab] = useState<Tab>('users');

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-100">Access Control</h1>
        <p className="text-gray-400 mt-1">Manage users, roles, and permissions</p>
      </div>

      {/* Tabs */}
      <div className="border-b border-white/[0.06]">
        <nav className="-mb-px flex space-x-8">
          <button
            onClick={() => setActiveTab('users')}
            className={`py-4 px-1 border-b-2 font-medium text-sm transition-colors ${
              activeTab === 'users'
                ? 'border-primary-600 text-primary-600'
                : 'border-transparent text-gray-500 hover:text-gray-300 hover:border-white/10'
            }`}
          >
            <UsersIcon className="w-5 h-5 inline mr-2" />
            Users
          </button>
          <button
            onClick={() => setActiveTab('roles')}
            className={`py-4 px-1 border-b-2 font-medium text-sm transition-colors ${
              activeTab === 'roles'
                ? 'border-primary-600 text-primary-600'
                : 'border-transparent text-gray-500 hover:text-gray-300 hover:border-white/10'
            }`}
          >
            <Shield className="w-5 h-5 inline mr-2" />
            Roles
          </button>
          <button
            onClick={() => setActiveTab('permissions')}
            className={`py-4 px-1 border-b-2 font-medium text-sm transition-colors ${
              activeTab === 'permissions'
                ? 'border-primary-600 text-primary-600'
                : 'border-transparent text-gray-500 hover:text-gray-300 hover:border-white/10'
            }`}
          >
            <Lock className="w-5 h-5 inline mr-2" />
            Permissions
          </button>
        </nav>
      </div>

      {/* Tab Content */}
      <div>
        {activeTab === 'users' && <UsersTab />}
        {activeTab === 'roles' && <RolesTab />}
        {activeTab === 'permissions' && <PermissionsTab />}
      </div>
    </div>
  );
}

// Users Tab
const PAGE_SIZE = 50;

function UsersTab() {
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingUser, setEditingUser] = useState<any>(null);
  const [deletingUser, setDeletingUser] = useState<any>(null);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [page, setPage] = useState(0);

  // Debounce search
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(search);
      setPage(0);
    }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const { data: users, isLoading } = useQuery({
    queryKey: ['users', debouncedSearch, page],
    queryFn: () => apiClient.listUsers({
      skip: page * PAGE_SIZE,
      limit: PAGE_SIZE,
      search: debouncedSearch || undefined,
    }),
    retry: false,
  });

  const { data: roles } = useQuery({
    queryKey: ['groups'],
    queryFn: () => apiClient.listRoles(),
    retry: false,
  });

  const deleteMutation = useMutation({
    mutationFn: (userId: string) => apiClient.deleteUser(userId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      setDeletingUser(null);
    },
  });

  const hasNextPage = users && users.length === PAGE_SIZE;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        {/* Search */}
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 w-5 h-5 text-gray-500 pointer-events-none" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by email..."
            className="input w-full !pl-11"
          />
          {search && (
            <button
              onClick={() => setSearch('')}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>
        <button onClick={() => setShowCreateModal(true)} className="btn btn-primary btn-sm flex-shrink-0">
          <UserPlus className="w-4 h-4 mr-2" />
          Create User
        </button>
      </div>

      {isLoading ? (
        <div className="text-center py-8">
          <div className="inline-block animate-spin rounded-full h-6 w-6 border-b-2 border-primary-600"></div>
        </div>
      ) : users && users.length > 0 ? (
        <>
          <div className="card overflow-x-auto p-0">
            <table className="w-full">
              <thead>
                <tr className="border-b border-white/[0.06]">
                  <th className="text-left py-3 px-4 text-xs font-medium text-gray-500 uppercase tracking-wider">Email</th>
                  <th className="text-left py-3 px-4 text-xs font-medium text-gray-500 uppercase tracking-wider">Roles</th>
                  <th className="text-left py-3 px-4 text-xs font-medium text-gray-500 uppercase tracking-wider">Created</th>
                  <th className="text-right py-3 px-4 text-xs font-medium text-gray-500 uppercase tracking-wider w-24">Actions</th>
                </tr>
              </thead>
              <tbody>
                {users.map((user: any) => (
                  <tr key={user.id} className="border-b border-white/[0.04] hover:bg-white/[0.02]">
                    <td className="py-3 px-4">
                      <div className="text-sm text-gray-100">{user.email}</div>
                      <div className="text-xs text-gray-600 font-mono select-all">{user.id}</div>
                    </td>
                    <td className="py-3 px-4">
                      {user.roles && user.roles.length > 0 ? (
                        <div className="flex flex-wrap gap-1">
                          {user.roles.map((roleName: string) => (
                            <span
                              key={roleName}
                              className={`px-2 py-0.5 text-xs rounded ${
                                roleName.toLowerCase() === 'admins'
                                  ? 'bg-red-900/30 text-red-300'
                                  : 'bg-blue-900/30 text-blue-300'
                              }`}
                            >
                              {roleName}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span className="text-xs text-gray-600">No roles</span>
                      )}
                    </td>
                    <td className="py-3 px-4 text-sm text-gray-400">
                      {new Date(user.created_at).toLocaleDateString('en-GB')}
                    </td>
                    <td className="py-3 px-4">
                      <div className="flex items-center justify-end gap-2">
                        <button
                          onClick={() => setEditingUser(user)}
                          className="text-gray-400 hover:text-gray-200 p-1"
                          title="Edit roles"
                        >
                          <Edit2 className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => setDeletingUser(user)}
                          className="text-gray-400 hover:text-red-400 p-1"
                          title="Delete user"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between text-sm text-gray-400">
            <span>
              Showing {page * PAGE_SIZE + 1}–{page * PAGE_SIZE + users.length}
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="btn btn-secondary btn-sm disabled:opacity-30"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span className="text-gray-500">Page {page + 1}</span>
              <button
                onClick={() => setPage((p) => p + 1)}
                disabled={!hasNextPage}
                className="btn btn-secondary btn-sm disabled:opacity-30"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        </>
      ) : (
        <div className="text-center py-12 card">
          <UsersIcon className="w-12 h-12 text-gray-500 mx-auto mb-3" />
          <p className="text-gray-400">{debouncedSearch ? 'No users matching search' : 'No users found'}</p>
        </div>
      )}

      {showCreateModal && <CreateUserModal onClose={() => setShowCreateModal(false)} />}
      {editingUser && <EditUserRolesModal user={editingUser} allRoles={roles || []} onClose={() => setEditingUser(null)} />}
      {deletingUser && (
        <ConfirmDeleteModal
          title="Delete User"
          targetName={deletingUser.email}
          description="This will permanently delete this user and all their data. This action cannot be undone."
          onConfirm={() => deleteMutation.mutate(deletingUser.id)}
          onCancel={() => setDeletingUser(null)}
          isPending={deleteMutation.isPending}
          error={deleteMutation.error}
        />
      )}
    </div>
  );
}

/** Type-to-confirm destructive delete modal */
function ConfirmDeleteModal({
  title,
  targetName,
  description,
  onConfirm,
  onCancel,
  isPending,
  error,
}: {
  title: string;
  targetName: string;
  description: string;
  onConfirm: () => void;
  onCancel: () => void;
  isPending: boolean;
  error?: any;
}) {
  const [typed, setTyped] = useState('');
  const matches = typed === 'DELETE';

  return (
    <>
      <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50" onClick={onCancel} />
      <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
        <div className="bg-[#161616] rounded-lg max-w-md w-full p-6 pointer-events-auto">
          <div className="flex items-center gap-2 mb-4">
            <AlertTriangle className="w-5 h-5 text-red-400" />
            <h2 className="text-xl font-semibold text-gray-100">{title}</h2>
          </div>
          <p className="text-gray-300 text-sm mb-2">{description}</p>
          <p className="text-gray-400 text-sm mb-4">
            Target: <span className="font-mono font-bold text-gray-100">{targetName}</span>
          </p>
          <p className="text-gray-400 text-sm mb-2">
            Type <span className="font-mono font-bold text-red-400">DELETE</span> to confirm:
          </p>
          <input
            type="text"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            className="input font-mono"
            placeholder="DELETE"
            autoFocus
          />
          {error && (
            <div className="mt-3 p-3 bg-red-900/20 border border-red-800/30 rounded text-sm text-red-300">
              {(error as any)?.response?.data?.detail || 'Operation failed'}
            </div>
          )}
          <div className="flex justify-end space-x-3 pt-4">
            <button type="button" onClick={onCancel} className="btn btn-secondary" disabled={isPending}>
              Cancel
            </button>
            <button
              onClick={onConfirm}
              className="btn bg-red-600 hover:bg-red-700 text-white"
              disabled={!matches || isPending}
            >
              {isPending ? 'Deleting...' : 'Delete'}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

/** Modal for editing a user's role assignments */
function EditUserRolesModal({ user, allRoles, onClose }: { user: any; allRoles: any[]; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [roleNames, setRoleNames] = useState<Set<string>>(
    new Set((user.roles || []) as string[])
  );

  const addMutation = useMutation({
    mutationFn: ({ roleName, userId }: { roleName: string; userId: string }) =>
      apiClient.addRoleMember(roleName, { user_id: userId }),
    onSuccess: (_data, { roleName }) => {
      setRoleNames(prev => new Set([...prev, roleName]));
      queryClient.invalidateQueries({ queryKey: ['users'] });
      queryClient.invalidateQueries({ queryKey: ['groupMembers'] });
    },
  });

  const removeMutation = useMutation({
    mutationFn: ({ roleName, userId }: { roleName: string; userId: string }) =>
      apiClient.removeRoleMember(roleName, userId),
    onSuccess: (_data, { roleName }) => {
      setRoleNames(prev => { const next = new Set(prev); next.delete(roleName); return next; });
      queryClient.invalidateQueries({ queryKey: ['users'] });
      queryClient.invalidateQueries({ queryKey: ['groupMembers'] });
    },
  });

  const toggleRole = (roleName: string) => {
    if (roleNames.has(roleName)) {
      removeMutation.mutate({ roleName, userId: user.id });
    } else {
      addMutation.mutate({ roleName, userId: user.id });
    }
  };

  const isPending = addMutation.isPending || removeMutation.isPending;

  return (
    <>
      <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50" onClick={onClose} />
      <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
        <div className="bg-[#161616] rounded-lg max-w-md w-full p-6 pointer-events-auto">
          <h2 className="text-xl font-semibold text-gray-100 mb-1">Edit Roles</h2>
          <p className="text-sm text-gray-400 mb-4">{user.email}</p>

          <div className="space-y-2">
            {allRoles.map((role: any) => {
              const isAssigned = roleNames.has(role.name);
              return (
                <label
                  key={role.id}
                  className="flex items-center gap-3 p-3 rounded-lg bg-[#0d0d0d] hover:bg-white/[0.03] cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={isAssigned}
                    onChange={() => toggleRole(role.name)}
                    disabled={isPending}
                    className="rounded border-white/10 text-primary-600 focus:ring-primary-500"
                  />
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-gray-100">{role.name}</span>
                      {role.name.toLowerCase() === 'admins' && (
                        <span className="px-1.5 py-0.5 bg-red-900/30 text-red-300 text-[10px] font-medium rounded">Admin</span>
                      )}
                    </div>
                    {role.description && (
                      <span className="text-xs text-gray-500">{role.description}</span>
                    )}
                  </div>
                </label>
              );
            })}
          </div>

          <div className="flex justify-end mt-6 pt-4 border-t border-white/[0.06]">
            <button onClick={onClose} className="btn btn-secondary">Done</button>
          </div>
        </div>
      </div>
    </>
  );
}

// Roles Tab
function RolesTab() {
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingRole, setEditingRole] = useState<any>(null);
  const [managingRole, setManagingRole] = useState<any>(null);

  const { data: roles, isLoading } = useQuery({
    queryKey: ['groups'],
    queryFn: () => apiClient.listRoles(),
    retry: false,
  });

  const deleteMutation = useMutation({
    mutationFn: (roleName: string) => apiClient.deleteRole(roleName),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['groups'] }),
  });

  const isAdminRole = (role: any) => role.name.toLowerCase() === 'admin' || role.name.toLowerCase() === 'admins';

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button onClick={() => { setEditingRole(null); setShowCreateModal(true); }} className="btn btn-primary btn-sm">
          <Plus className="w-4 h-4 mr-2" />
          Create Role
        </button>
      </div>

      {isLoading ? (
        <div className="text-center py-8"><div className="inline-block animate-spin rounded-full h-6 w-6 border-b-2 border-primary-600"></div></div>
      ) : roles && roles.length > 0 ? (
        <div className="grid gap-4">
          {roles.map((role: any) => (
            <div key={role.id} className="card">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center flex-1">
                  <Shield className="w-6 h-6 text-primary-600 mr-3" />
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <h3 className="font-semibold text-gray-100">{role.name}</h3>
                      {isAdminRole(role) && (
                        <span className="px-2 py-0.5 bg-red-900/30 text-red-300 text-xs font-medium rounded">Admin</span>
                      )}
                    </div>
                    {role.description && <p className="text-sm text-gray-400 mt-1">{role.description}</p>}
                    {role.email_domain && (
                      <p className="text-xs text-gray-500 mt-1">Email domain: {role.email_domain}</p>
                    )}
                  </div>
                </div>
                <div className="flex items-center space-x-2">
                  <button
                    onClick={() => setManagingRole(role)}
                    className="text-blue-600 hover:text-blue-400"
                    title="Manage members"
                  >
                    <UserPlus className="w-4 h-4" />
                  </button>
                  {!isAdminRole(role) && (
                    <>
                      <button onClick={() => { setEditingRole(role); setShowCreateModal(true); }} className="text-primary-600 hover:text-primary-700">
                        <Edit2 className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => confirm('Delete this role?') && deleteMutation.mutate(role.name)}
                        className="text-red-600 hover:text-red-400"
                        disabled={deleteMutation.isPending}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-center py-12 card">
          <Shield className="w-12 h-12 text-gray-500 mx-auto mb-3" />
          <p className="text-gray-400">No roles found</p>
        </div>
      )}

      {showCreateModal && <RoleModal role={editingRole} onClose={() => { setShowCreateModal(false); setEditingRole(null); }} />}
      {managingRole && <RoleManagementModal role={managingRole} onClose={() => setManagingRole(null)} />}
    </div>
  );
}

function RoleModal({ role, onClose }: { role: any; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [formData, setFormData] = useState({
    name: role?.name || '',
    description: role?.description || '',
    email_domain: role?.email_domain || '',
  });

  const mutation = useMutation({
    mutationFn: (data: any) => role ? apiClient.updateRole(role.name, data) : apiClient.createRole(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['groups'] });
      onClose();
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    mutation.mutate(formData);
  };

  return (
    <>
      <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50" onClick={onClose} />
      <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
        <div className="bg-[#161616] rounded-lg max-w-md w-full p-6 pointer-events-auto">
          <h2 className="text-xl font-semibold text-gray-100 mb-4">{role ? 'Edit' : 'Create'} Role</h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">Name *</label>
              <input type="text" value={formData.name} onChange={(e) => setFormData({ ...formData, name: e.target.value })} required className="input" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">Description</label>
              <textarea value={formData.description} onChange={(e) => setFormData({ ...formData, description: e.target.value })} rows={2} className="input" />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">Email Domain</label>
              <input type="text" value={formData.email_domain} onChange={(e) => setFormData({ ...formData, email_domain: e.target.value })} placeholder="example.com" className="input" />
              <p className="text-xs text-gray-500 mt-1">Users with this email domain will auto-join this role</p>
            </div>
            <div className="flex justify-end space-x-3 pt-4">
              <button type="button" onClick={onClose} className="btn btn-secondary">Cancel</button>
              <button type="submit" disabled={mutation.isPending} className="btn btn-primary">
                {mutation.isPending ? 'Saving...' : 'Save'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </>
  );
}

function RoleManagementModal({ role, onClose }: { role: any; onClose: () => void }) {
  return (
    <>
      <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50" onClick={onClose} />
      <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
        <div className="bg-[#161616] rounded-lg max-w-3xl w-full p-6 max-h-[90vh] overflow-y-auto pointer-events-auto">
          <h2 className="text-xl font-semibold text-gray-100 mb-4">Manage Members: {role.name}</h2>

          <MembersManagement role={role} />

          <div className="flex justify-end mt-6 pt-4 border-t border-white/[0.06]">
            <button onClick={onClose} className="btn btn-secondary">Close</button>
          </div>
        </div>
      </div>
    </>
  );
}

function MembersManagement({ role }: { role: any }) {
  const queryClient = useQueryClient();
  const [showAddModal, setShowAddModal] = useState(false);

  const { data: members } = useQuery({
    queryKey: ['groupMembers', role.name],
    queryFn: () => apiClient.listRoleMembers(role.name),
    retry: false,
  });

  const removeMutation = useMutation({
    mutationFn: (userId: string) => apiClient.removeRoleMember(role.name, userId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['groupMembers', role.name] }),
  });

  return (
    <div className="space-y-3">
      <div className="flex justify-between items-center">
        <p className="text-sm text-gray-400">Role members</p>
        <button onClick={() => setShowAddModal(true)} className="btn btn-primary btn-sm">
          <UserPlus className="w-4 h-4 mr-2" />
          Add Member
        </button>
      </div>

      {members && members.length > 0 ? (
        <div className="space-y-2">
          {members.map((member: any) => (
            <div key={member.user_id} className="flex items-center justify-between p-3 bg-[#0d0d0d] rounded">
              <div>
                <p className="text-sm font-medium text-gray-100">{member.user_email || member.user_id}</p>
                {member.role && <p className="text-xs text-gray-500">Role: {member.role}</p>}
              </div>
              <button
                onClick={() => confirm('Remove this member?') && removeMutation.mutate(member.user_id)}
                className="text-red-600 hover:text-red-400"
                disabled={removeMutation.isPending}
              >
                <UserMinus className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-sm text-gray-500 text-center py-6">No members in this role</p>
      )}

      {showAddModal && <AddMemberModal role={role} onClose={() => setShowAddModal(false)} />}
    </div>
  );
}

function AddMemberModal({ role, onClose }: { role: any; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [userId, setUserId] = useState('');

  const { data: users } = useQuery({
    queryKey: ['users'],
    queryFn: () => apiClient.listUsers(),
    retry: false,
  });

  const mutation = useMutation({
    mutationFn: (data: any) => apiClient.addRoleMember(role.name, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['groupMembers', role.name] });
      onClose();
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    mutation.mutate({ user_id: userId });
  };

  return (
    <>
      <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-[60]" onClick={onClose} />
      <div className="fixed inset-0 flex items-center justify-center z-[60] p-4 pointer-events-none">
        <div className="bg-[#161616] rounded-lg max-w-md w-full p-6 pointer-events-auto">
          <h3 className="text-lg font-semibold text-gray-100 mb-4">Add Member to {role.name}</h3>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">User *</label>
              <select value={userId} onChange={(e) => setUserId(e.target.value)} required className="input">
                <option value="">Select user</option>
                {users?.map((user: any) => <option key={user.id} value={user.id}>{user.email}</option>)}
              </select>
            </div>
            <div className="flex justify-end space-x-3">
              <button type="button" onClick={onClose} className="btn btn-secondary">Cancel</button>
              <button type="submit" disabled={mutation.isPending} className="btn btn-primary">
                {mutation.isPending ? 'Adding...' : 'Add'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </>
  );
}

function CreateUserModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [email, setEmail] = useState('');

  const mutation = useMutation({
    mutationFn: (data: { email: string }) => apiClient.createUser(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      onClose();
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    mutation.mutate({ email });
  };

  return (
    <>
      <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50" onClick={onClose} />
      <div className="fixed inset-0 flex items-center justify-center z-50 p-4 pointer-events-none">
        <div className="bg-[#161616] rounded-lg max-w-md w-full p-6 pointer-events-auto">
          <h2 className="text-xl font-semibold text-gray-100 mb-4">Create User</h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">Email *</label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                placeholder="user@example.com"
                className="input"
                autoFocus
              />
              <p className="text-xs text-gray-500 mt-1">
                User will be created and assigned to the default role
              </p>
            </div>
            {mutation.isError && (
              <div className="p-3 bg-red-900/20 border border-red-800/30 rounded text-sm text-red-300">
                {(mutation.error as any)?.response?.data?.detail || 'Failed to create user'}
              </div>
            )}
            <div className="flex justify-end space-x-3 pt-4">
              <button type="button" onClick={onClose} className="btn btn-secondary">Cancel</button>
              <button type="submit" disabled={mutation.isPending} className="btn btn-primary">
                {mutation.isPending ? 'Creating...' : 'Create User'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </>
  );
}
