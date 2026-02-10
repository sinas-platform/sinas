import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Users as UsersIcon, UserPlus, Edit2, Trash2, Shield, UserMinus, Plus } from 'lucide-react';

type Tab = 'users' | 'roles';

export function Users() {
  const [activeTab, setActiveTab] = useState<Tab>('users');

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Users & Roles</h1>
        <p className="text-gray-600 mt-1">Manage users, roles, and permissions</p>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <nav className="-mb-px flex space-x-8">
          <button
            onClick={() => setActiveTab('users')}
            className={`py-4 px-1 border-b-2 font-medium text-sm transition-colors ${
              activeTab === 'users'
                ? 'border-primary-600 text-primary-600'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
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
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
            }`}
          >
            <Shield className="w-5 h-5 inline mr-2" />
            Roles
          </button>
        </nav>
      </div>

      {/* Tab Content */}
      <div>
        {activeTab === 'users' && <UsersTab />}
        {activeTab === 'roles' && <RolesTab />}
      </div>
    </div>
  );
}

// Users Tab
function UsersTab() {
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);

  const { data: users, isLoading } = useQuery({
    queryKey: ['users'],
    queryFn: () => apiClient.listUsers(),
    retry: false,
  });

  const deleteMutation = useMutation({
    mutationFn: (userId: string) => apiClient.deleteUser(userId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['users'] }),
  });

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button onClick={() => setShowCreateModal(true)} className="btn btn-primary btn-sm">
          <UserPlus className="w-4 h-4 mr-2" />
          Create User
        </button>
      </div>
      {isLoading ? (
        <div className="text-center py-8"><div className="inline-block animate-spin rounded-full h-6 w-6 border-b-2 border-primary-600"></div></div>
      ) : users && users.length > 0 ? (
        <div className="grid gap-4">
          {users.map((user: any) => (
            <div key={user.id} className="card flex items-center justify-between">
              <div className="flex items-center flex-1">
                <UsersIcon className="w-6 h-6 text-primary-600 mr-3" />
                <div className="flex-1">
                  <h3 className="font-semibold text-gray-900">{user.email}</h3>
                  {user.roles && user.roles.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1">
                      {user.roles.map((role: any) => (
                        <span key={role.id} className="px-2 py-0.5 bg-blue-100 text-blue-800 text-xs rounded">
                          {role.name}
                        </span>
                      ))}
                    </div>
                  )}
                  <p className="text-xs text-gray-500 mt-1">
                    Created {new Date(user.created_at).toLocaleDateString()}
                  </p>
                </div>
              </div>
              <div className="flex items-center space-x-2">
                <button
                  onClick={() => confirm('Delete this user?') && deleteMutation.mutate(user.id)}
                  className="text-red-600 hover:text-red-700"
                  disabled={deleteMutation.isPending}
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-center py-12 card">
          <UsersIcon className="w-12 h-12 text-gray-400 mx-auto mb-3" />
          <p className="text-gray-600">No users found</p>
        </div>
      )}

      {showCreateModal && <CreateUserModal onClose={() => setShowCreateModal(false)} />}
    </div>
  );
}

// Groups Tab
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
                      <h3 className="font-semibold text-gray-900">{role.name}</h3>
                      {isAdminRole(role) && (
                        <span className="px-2 py-0.5 bg-red-100 text-red-800 text-xs font-medium rounded">Admin</span>
                      )}
                    </div>
                    {role.description && <p className="text-sm text-gray-600 mt-1">{role.description}</p>}
                    {role.email_domain && (
                      <p className="text-xs text-gray-500 mt-1">Email domain: {role.email_domain}</p>
                    )}
                  </div>
                </div>
                <div className="flex items-center space-x-2">
                  <button
                    onClick={() => setManagingRole(role)}
                    className="text-blue-600 hover:text-blue-700"
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
                        className="text-red-600 hover:text-red-700"
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
          <Shield className="w-12 h-12 text-gray-400 mx-auto mb-3" />
          <p className="text-gray-600">No roles found</p>
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
    <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6">
        <h2 className="text-xl font-semibold text-gray-900 mb-4">{role ? 'Edit' : 'Create'} Role</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Name *</label>
            <input type="text" value={formData.name} onChange={(e) => setFormData({ ...formData, name: e.target.value })} required className="input" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Description</label>
            <textarea value={formData.description} onChange={(e) => setFormData({ ...formData, description: e.target.value })} rows={2} className="input" />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Email Domain</label>
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
  );
}

function RoleManagementModal({ role, onClose }: { role: any; onClose: () => void }) {
  return (
    <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl max-w-3xl w-full p-6 max-h-[90vh] overflow-y-auto">
        <h2 className="text-xl font-semibold text-gray-900 mb-4">Manage Members: {role.name}</h2>

        <MembersManagement role={role} />

        <div className="flex justify-end mt-6 pt-4 border-t border-gray-200">
          <button onClick={onClose} className="btn btn-secondary">Close</button>
        </div>
      </div>
    </div>
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
        <p className="text-sm text-gray-600">Role members</p>
        <button onClick={() => setShowAddModal(true)} className="btn btn-primary btn-sm">
          <UserPlus className="w-4 h-4 mr-2" />
          Add Member
        </button>
      </div>

      {members && members.length > 0 ? (
        <div className="space-y-2">
          {members.map((member: any) => (
            <div key={member.user_id} className="flex items-center justify-between p-3 bg-gray-50 rounded">
              <div>
                <p className="text-sm font-medium text-gray-900">{member.user_email || member.user_id}</p>
                {member.role && <p className="text-xs text-gray-500">Role: {member.role}</p>}
              </div>
              <button
                onClick={() => confirm('Remove this member?') && removeMutation.mutate(member.user_id)}
                className="text-red-600 hover:text-red-700"
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
    <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-[60] p-4">
      <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6">
        <h3 className="text-lg font-semibold text-gray-900 mb-4">Add Member to {role.name}</h3>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">User *</label>
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
    <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6">
        <h2 className="text-xl font-semibold text-gray-900 mb-4">Create User</h2>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Email *</label>
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
            <div className="p-3 bg-red-50 border border-red-200 rounded text-sm text-red-800">
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
  );
}
