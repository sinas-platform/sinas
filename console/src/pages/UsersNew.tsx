import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Users as UsersIcon, UserPlus, Edit2, Trash2, Shield, UserMinus, Plus } from 'lucide-react';

type Tab = 'users' | 'groups';

export function UsersNew() {
  const [activeTab, setActiveTab] = useState<Tab>('users');

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Users & Groups</h1>
        <p className="text-gray-600 mt-1">Manage users, groups, and permissions</p>
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
            onClick={() => setActiveTab('groups')}
            className={`py-4 px-1 border-b-2 font-medium text-sm transition-colors ${
              activeTab === 'groups'
                ? 'border-primary-600 text-primary-600'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
            }`}
          >
            <Shield className="w-5 h-5 inline mr-2" />
            Groups
          </button>
        </nav>
      </div>

      {/* Tab Content */}
      <div>
        {activeTab === 'users' && <UsersTab />}
        {activeTab === 'groups' && <GroupsTab />}
      </div>
    </div>
  );
}

// Users Tab
function UsersTab() {
  const queryClient = useQueryClient();

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
                  {user.groups && user.groups.length > 0 && (
                    <div className="flex flex-wrap gap-1 mt-1">
                      {user.groups.map((group: any) => (
                        <span key={group.id} className="px-2 py-0.5 bg-blue-100 text-blue-800 text-xs rounded">
                          {group.name}
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
    </div>
  );
}

// Groups Tab
function GroupsTab() {
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingGroup, setEditingGroup] = useState<any>(null);
  const [managingGroup, setManagingGroup] = useState<any>(null);

  const { data: groups, isLoading } = useQuery({
    queryKey: ['groups'],
    queryFn: () => apiClient.listGroups(),
    retry: false,
  });

  const deleteMutation = useMutation({
    mutationFn: (groupName: string) => apiClient.deleteGroup(groupName),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['groups'] }),
  });

  const isAdminGroup = (group: any) => group.name.toLowerCase() === 'admin' || group.name.toLowerCase() === 'admins';

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <button onClick={() => { setEditingGroup(null); setShowCreateModal(true); }} className="btn btn-primary btn-sm">
          <Plus className="w-4 h-4 mr-2" />
          Create Group
        </button>
      </div>

      {isLoading ? (
        <div className="text-center py-8"><div className="inline-block animate-spin rounded-full h-6 w-6 border-b-2 border-primary-600"></div></div>
      ) : groups && groups.length > 0 ? (
        <div className="grid gap-4">
          {groups.map((group: any) => (
            <div key={group.id} className="card">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center flex-1">
                  <Shield className="w-6 h-6 text-primary-600 mr-3" />
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <h3 className="font-semibold text-gray-900">{group.name}</h3>
                      {isAdminGroup(group) && (
                        <span className="px-2 py-0.5 bg-red-100 text-red-800 text-xs font-medium rounded">Admin</span>
                      )}
                    </div>
                    {group.description && <p className="text-sm text-gray-600 mt-1">{group.description}</p>}
                    {group.email_domain && (
                      <p className="text-xs text-gray-500 mt-1">Email domain: {group.email_domain}</p>
                    )}
                  </div>
                </div>
                <div className="flex items-center space-x-2">
                  <button
                    onClick={() => setManagingGroup(group)}
                    className="text-blue-600 hover:text-blue-700"
                    title="Manage members"
                  >
                    <UserPlus className="w-4 h-4" />
                  </button>
                  {!isAdminGroup(group) && (
                    <>
                      <button onClick={() => { setEditingGroup(group); setShowCreateModal(true); }} className="text-primary-600 hover:text-primary-700">
                        <Edit2 className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => confirm('Delete this group?') && deleteMutation.mutate(group.name)}
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
          <p className="text-gray-600">No groups found</p>
        </div>
      )}

      {showCreateModal && <GroupModal group={editingGroup} onClose={() => { setShowCreateModal(false); setEditingGroup(null); }} />}
      {managingGroup && <GroupManagementModal group={managingGroup} onClose={() => setManagingGroup(null)} />}
    </div>
  );
}

function GroupModal({ group, onClose }: { group: any; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [formData, setFormData] = useState({
    name: group?.name || '',
    description: group?.description || '',
    email_domain: group?.email_domain || '',
  });

  const mutation = useMutation({
    mutationFn: (data: any) => group ? apiClient.updateGroup(group.name, data) : apiClient.createGroup(data),
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
        <h2 className="text-xl font-semibold text-gray-900 mb-4">{group ? 'Edit' : 'Create'} Group</h2>
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
            <p className="text-xs text-gray-500 mt-1">Users with this email domain will auto-join this group</p>
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

function GroupManagementModal({ group, onClose }: { group: any; onClose: () => void }) {
  return (
    <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-xl max-w-3xl w-full p-6 max-h-[90vh] overflow-y-auto">
        <h2 className="text-xl font-semibold text-gray-900 mb-4">Manage Members: {group.name}</h2>

        <MembersManagement group={group} />

        <div className="flex justify-end mt-6 pt-4 border-t border-gray-200">
          <button onClick={onClose} className="btn btn-secondary">Close</button>
        </div>
      </div>
    </div>
  );
}

function MembersManagement({ group }: { group: any }) {
  const queryClient = useQueryClient();
  const [showAddModal, setShowAddModal] = useState(false);

  const { data: members } = useQuery({
    queryKey: ['groupMembers', group.name],
    queryFn: () => apiClient.listGroupMembers(group.name),
    retry: false,
  });

  const removeMutation = useMutation({
    mutationFn: (userId: string) => apiClient.removeGroupMember(group.name, userId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['groupMembers', group.name] }),
  });

  return (
    <div className="space-y-3">
      <div className="flex justify-between items-center">
        <p className="text-sm text-gray-600">Group members</p>
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
        <p className="text-sm text-gray-500 text-center py-6">No members in this group</p>
      )}

      {showAddModal && <AddMemberModal group={group} onClose={() => setShowAddModal(false)} />}
    </div>
  );
}

function AddMemberModal({ group, onClose }: { group: any; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [userId, setUserId] = useState('');
  const [role, setRole] = useState('member');

  const { data: users } = useQuery({
    queryKey: ['users'],
    queryFn: () => apiClient.listUsers(),
    retry: false,
  });

  const mutation = useMutation({
    mutationFn: (data: any) => apiClient.addGroupMember(group.name, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['groupMembers', group.name] });
      onClose();
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    mutation.mutate({ user_id: userId, role });
  };

  return (
    <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-[60] p-4">
      <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6">
        <h3 className="text-lg font-semibold text-gray-900 mb-4">Add Member to {group.name}</h3>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">User *</label>
            <select value={userId} onChange={(e) => setUserId(e.target.value)} required className="input">
              <option value="">Select user</option>
              {users?.map((user: any) => <option key={user.id} value={user.id}>{user.email}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">Role</label>
            <input type="text" value={role} onChange={(e) => setRole(e.target.value)} className="input" />
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
