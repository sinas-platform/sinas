import { Users as UsersIcon } from 'lucide-react';

export function Users() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900">Users & Groups</h1>
        <p className="text-gray-600 mt-1">Manage users, groups, and permissions</p>
      </div>

      <div className="text-center py-12 card">
        <UsersIcon className="w-16 h-16 text-gray-400 mx-auto mb-4" />
        <h3 className="text-lg font-medium text-gray-900 mb-2">User Management</h3>
        <p className="text-gray-600">Detailed user and group management coming soon</p>
      </div>
    </div>
  );
}
