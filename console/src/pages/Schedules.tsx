import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Clock, Plus, Edit2, Trash2, PlayCircle, PauseCircle } from 'lucide-react';
import { Link } from 'react-router-dom';

export function Schedules() {
  const queryClient = useQueryClient();

  const { data: schedules, isLoading } = useQuery({
    queryKey: ['schedules'],
    queryFn: () => apiClient.listSchedules(),
    retry: false,
  });

  const deleteMutation = useMutation({
    mutationFn: (scheduleId: string) => apiClient.deleteSchedule(scheduleId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] });
    },
  });

  const toggleActiveMutation = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      apiClient.updateSchedule(id, { is_active }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['schedules'] });
    },
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Scheduled Jobs</h1>
          <p className="text-gray-600 mt-1">Schedule functions and agents to run automatically on a cron schedule</p>
        </div>
        <Link to="/schedules/new" className="btn btn-primary flex items-center">
          <Plus className="w-5 h-5 mr-2" />
          New Schedule
        </Link>
      </div>

      {isLoading ? (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
        </div>
      ) : schedules && schedules.length > 0 ? (
        <div className="grid gap-6">
          {schedules.map((schedule: any) => (
            <div key={schedule.id} className="card">
              <div className="flex items-start justify-between">
                <div className="flex items-center flex-1">
                  <Clock className="w-8 h-8 text-primary-600 mr-3 flex-shrink-0" />
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <h3 className="font-semibold text-gray-900">{schedule.name}</h3>
                      <span
                        className={`px-2 py-0.5 text-xs font-medium rounded ${
                          schedule.schedule_type === 'agent'
                            ? 'bg-purple-100 text-purple-800'
                            : 'bg-blue-100 text-blue-800'
                        }`}
                      >
                        {schedule.schedule_type === 'agent' ? 'agent' : 'fn'}
                      </span>
                      <span
                        className={`px-2 py-0.5 text-xs font-medium rounded ${
                          schedule.is_active
                            ? 'bg-green-100 text-green-800'
                            : 'bg-gray-100 text-gray-800'
                        }`}
                      >
                        {schedule.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </div>
                    <p className="text-sm text-gray-600 mt-1">{schedule.description || 'No description'}</p>
                    <div className="flex items-center gap-4 mt-2">
                      <p className="text-xs text-gray-500">
                        {schedule.schedule_type === 'agent' ? 'Agent' : 'Function'}:{' '}
                        <span className="font-mono">{schedule.target_namespace}/{schedule.target_name}</span>
                      </p>
                      <p className="text-xs text-gray-500">
                        Schedule: <span className="font-mono">{schedule.cron_expression}</span>
                      </p>
                      <p className="text-xs text-gray-500">
                        Timezone: <span className="font-mono">{schedule.timezone}</span>
                      </p>
                    </div>
                    {schedule.last_run && (
                      <p className="text-xs text-gray-500 mt-1">
                        Last run: {new Date(schedule.last_run).toLocaleString()}
                      </p>
                    )}
                    {schedule.next_run && (
                      <p className="text-xs text-green-600 mt-1">
                        Next run: {new Date(schedule.next_run).toLocaleString()}
                      </p>
                    )}
                  </div>
                </div>
                <div className="flex items-center space-x-2">
                  <button
                    onClick={() =>
                      toggleActiveMutation.mutate({
                        id: schedule.name,
                        is_active: !schedule.is_active,
                      })
                    }
                    className={`${
                      schedule.is_active
                        ? 'text-amber-600 hover:text-amber-700'
                        : 'text-green-600 hover:text-green-700'
                    }`}
                    disabled={toggleActiveMutation.isPending}
                    title={schedule.is_active ? 'Pause' : 'Resume'}
                  >
                    {schedule.is_active ? (
                      <PauseCircle className="w-5 h-5" />
                    ) : (
                      <PlayCircle className="w-5 h-5" />
                    )}
                  </button>
                  <Link
                    to={`/schedules/${schedule.name}`}
                    className="text-primary-600 hover:text-primary-700"
                  >
                    <Edit2 className="w-5 h-5" />
                  </Link>
                  <button
                    onClick={() => {
                      if (confirm('Are you sure you want to delete this schedule?')) {
                        deleteMutation.mutate(schedule.name);
                      }
                    }}
                    className="text-red-600 hover:text-red-700"
                    disabled={deleteMutation.isPending}
                  >
                    <Trash2 className="w-5 h-5" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-center py-12 card">
          <Clock className="w-16 h-16 text-gray-400 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-900 mb-2">No schedules configured</h3>
          <p className="text-gray-600 mb-4">Create schedules to run functions and agents automatically</p>
          <Link to="/schedules/new" className="btn btn-primary">
            <Plus className="w-5 h-5 mr-2 inline" />
            Create Schedule
          </Link>
        </div>
      )}
    </div>
  );
}
