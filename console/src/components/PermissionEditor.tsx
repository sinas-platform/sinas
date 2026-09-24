import { useQuery } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { X } from 'lucide-react';
import { useState } from 'react';

interface PermissionRegistryEntry {
  resource: string;
  description: string;
  actions: string[];
  namespaced?: boolean;
  adminOnly?: boolean;
}

/**
 * The permission keys one granted action must carry.
 *
 * Namespaced resources are checked against a concrete path where they are
 * used — `sinas.functions/acme/send_email.execute:own` — and a flat
 * `sinas.functions.execute:all` does not match that (a pattern without a path
 * never matches a concrete one with a path). A key granted only the flat form
 * was therefore refused by every resource-level check, while the same user's
 * console session worked, because the session carries the role's own
 * path-form keys (#77).
 *
 * Both forms are granted together rather than swapping one for the other: a
 * few actions on namespaced resources are still checked flat (`create`,
 * `functions.shared_pool`), so neither form alone covers a whole resource.
 */
const permissionKeysFor = (
  entry: PermissionRegistryEntry,
  action: string,
  scope: string
): string[] => {
  const flat = `sinas.${entry.resource}.${action}:${scope}`;
  return entry.namespaced
    ? [flat, `sinas.${entry.resource}/*/*.${action}:${scope}`]
    : [flat];
};

/**
 * Reusable permission editor with registry reference and custom input.
 *
 * Supports two value formats:
 * - Record<string, boolean> (for API keys where permissions are a dict)
 * - string[] (for Manifests where permissions are a list)
 */

interface PermissionEditorBaseProps {
  /** Label above the component (optional, rendered by parent if needed) */
  label?: string;
  /** Hint text below the label */
  hint?: string;
  /** Placeholder for custom permission input */
  placeholder?: string;
}

interface PermissionEditorDictProps extends PermissionEditorBaseProps {
  mode: 'dict';
  value: Record<string, boolean>;
  onChange: (value: Record<string, boolean>) => void;
}

interface PermissionEditorListProps extends PermissionEditorBaseProps {
  mode: 'list';
  value: string[];
  onChange: (value: string[]) => void;
}

type PermissionEditorProps = PermissionEditorDictProps | PermissionEditorListProps;

export function PermissionEditor(props: PermissionEditorProps) {
  const { label, hint, placeholder, mode } = props;
  const [customPermission, setCustomPermission] = useState('');
  const [permScope, setPermScope] = useState<'own' | 'all'>('own');

  const { data: permissionRegistry } = useQuery({
    queryKey: ['permissionRegistry'],
    queryFn: () => apiClient.getPermissionReference(),
    retry: false,
    staleTime: 5 * 60 * 1000,
  });

  // Normalize to string[] for display
  const selectedPermissions: string[] =
    mode === 'dict'
      ? Object.keys(props.value).filter((k) => props.value[k])
      : props.value;

  const isSelected = (perm: string): boolean => {
    if (mode === 'dict') {
      return !!props.value[perm];
    }
    return props.value.includes(perm);
  };

  const addPermissions = (perms: string[]) => {
    if (mode === 'dict') {
      const next = { ...props.value };
      perms.forEach((perm) => {
        next[perm] = true;
      });
      props.onChange(next);
    } else {
      const missing = perms.filter((perm) => !props.value.includes(perm));
      if (missing.length > 0) {
        props.onChange([...props.value, ...missing]);
      }
    }
  };

  const removePermissions = (perms: string[]) => {
    if (mode === 'dict') {
      const next = { ...props.value };
      perms.forEach((perm) => {
        delete next[perm];
      });
      props.onChange(next);
    } else {
      props.onChange(props.value.filter((p) => !perms.includes(p)));
    }
  };

  const addPermission = (perm: string) => addPermissions([perm]);
  const removePermission = (perm: string) => removePermissions([perm]);

  const togglePermissions = (perms: string[]) => {
    if (perms.every(isSelected)) {
      removePermissions(perms);
    } else {
      addPermissions(perms);
    }
  };

  const addCustomPermission = () => {
    const trimmed = customPermission.trim();
    if (trimmed) {
      addPermission(trimmed);
      setCustomPermission('');
    }
  };

  return (
    <div>
      {label && (
        <label className="block text-sm font-medium text-gray-300 mb-2">{label}</label>
      )}
      {hint && (
        <p className="text-xs text-gray-500 mb-3">{hint}</p>
      )}

      {/* Selected Permissions */}
      {selectedPermissions.length > 0 && (
        <div className="mb-3 border border-line-soft rounded-lg p-3 bg-surface-0">
          <div className="text-xs font-medium text-gray-300 mb-2">
            Selected ({selectedPermissions.length}):
          </div>
          <div className="flex flex-wrap gap-2">
            {selectedPermissions.map((permission) => (
              <span
                key={permission}
                className="inline-flex items-center gap-1 px-2 py-1 bg-blue-900/30 text-blue-300 text-xs rounded font-mono"
              >
                {permission}
                <button
                  type="button"
                  onClick={() => removePermission(permission)}
                  className="hover:text-blue-100"
                >
                  <X className="w-3 h-3" />
                </button>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Add Custom Permission */}
      <div className="mb-3">
        <div className="flex gap-2">
          <input
            type="text"
            value={customPermission}
            onChange={(e) => setCustomPermission(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                addCustomPermission();
              }
            }}
            placeholder={placeholder || 'e.g., sinas.agents/*/*.read:own'}
            className="input flex-1 font-mono text-sm"
          />
          <button
            type="button"
            onClick={addCustomPermission}
            disabled={!customPermission.trim()}
            className="btn btn-secondary"
          >
            Add
          </button>
        </div>
      </div>

      {/* Permission Reference */}
      {permissionRegistry && permissionRegistry.length > 0 && (
        <details className="border border-line-soft rounded-lg">
          <summary className="cursor-pointer p-3 text-sm font-medium text-gray-300 hover:bg-hover">
            Permission Reference
          </summary>
          <div className="p-3 pt-0">
            {/* Scope toggle */}
            <div className="flex items-center gap-2 mb-3 pb-2 border-b border-line-soft">
              <span className="text-xs text-gray-500">Scope:</span>
              <button
                type="button"
                onClick={() => setPermScope('own')}
                className={`px-2 py-0.5 text-xs rounded ${
                  permScope === 'own'
                    ? 'bg-blue-900/30 text-blue-300 font-medium'
                    : 'bg-surface-1 text-gray-400 hover:bg-surface-2'
                }`}
              >
                :own
              </button>
              <button
                type="button"
                onClick={() => setPermScope('all')}
                className={`px-2 py-0.5 text-xs rounded ${
                  permScope === 'all'
                    ? 'bg-blue-900/30 text-blue-300 font-medium'
                    : 'bg-surface-1 text-gray-400 hover:bg-surface-2'
                }`}
              >
                :all
              </button>
              <span className="text-xs text-gray-500 ml-1">(:all grants :own)</span>
            </div>
            <div className="space-y-2 max-h-72 overflow-y-auto">
              {(permissionRegistry as PermissionRegistryEntry[]).map((entry) => (
                <div key={entry.resource} className="flex items-start gap-2">
                  <div className="w-32 flex-shrink-0 pt-0.5">
                    <span className="text-xs font-medium text-gray-100">
                      {entry.description}
                    </span>
                    {entry.adminOnly && (
                      <span className="ml-1 text-[10px] text-amber-600 font-medium">
                        admin
                      </span>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-1 flex-1">
                    {entry.actions.map((action) => {
                      const scope = entry.adminOnly ? 'all' : permScope;
                      const permKeys = permissionKeysFor(entry, action, scope);
                      const selected = permKeys.every(isSelected);
                      return (
                        <button
                          key={action}
                          type="button"
                          onClick={() => togglePermissions(permKeys)}
                          className={`px-1.5 py-0.5 text-[11px] rounded font-mono transition-colors ${
                            selected
                              ? 'bg-blue-900/30 text-blue-300'
                              : 'bg-surface-1 text-gray-400 hover:bg-surface-2'
                          }`}
                          title={permKeys.join('\n')}
                        >
                          {action}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </details>
      )}
    </div>
  );
}
