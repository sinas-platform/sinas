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

  const addPermission = (perm: string) => {
    if (mode === 'dict') {
      props.onChange({ ...props.value, [perm]: true });
    } else {
      if (!props.value.includes(perm)) {
        props.onChange([...props.value, perm]);
      }
    }
  };

  const removePermission = (perm: string) => {
    if (mode === 'dict') {
      const next = { ...props.value };
      delete next[perm];
      props.onChange(next);
    } else {
      props.onChange(props.value.filter((p) => p !== perm));
    }
  };

  const togglePermission = (perm: string) => {
    if (isSelected(perm)) {
      removePermission(perm);
    } else {
      addPermission(perm);
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
        <div className="mb-3 border border-white/[0.06] rounded-lg p-3 bg-[#0d0d0d]">
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
        <details className="border border-white/[0.06] rounded-lg">
          <summary className="cursor-pointer p-3 text-sm font-medium text-gray-300 hover:bg-white/5">
            Permission Reference
          </summary>
          <div className="p-3 pt-0">
            {/* Scope toggle */}
            <div className="flex items-center gap-2 mb-3 pb-2 border-b border-white/[0.04]">
              <span className="text-xs text-gray-500">Scope:</span>
              <button
                type="button"
                onClick={() => setPermScope('own')}
                className={`px-2 py-0.5 text-xs rounded ${
                  permScope === 'own'
                    ? 'bg-blue-900/30 text-blue-300 font-medium'
                    : 'bg-[#161616] text-gray-400 hover:bg-[#1e1e1e]'
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
                    : 'bg-[#161616] text-gray-400 hover:bg-[#1e1e1e]'
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
                      const permKey = `sinas.${entry.resource}.${action}:${scope}`;
                      const selected = isSelected(permKey);
                      return (
                        <button
                          key={action}
                          type="button"
                          onClick={() => togglePermission(permKey)}
                          className={`px-1.5 py-0.5 text-[11px] rounded font-mono transition-colors ${
                            selected
                              ? 'bg-blue-900/30 text-blue-300'
                              : 'bg-[#161616] text-gray-400 hover:bg-[#1e1e1e]'
                          }`}
                          title={permKey}
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
