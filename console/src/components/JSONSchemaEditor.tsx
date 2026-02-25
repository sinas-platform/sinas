import { useState, useEffect } from 'react';
import { Plus, Trash2, ChevronDown, ChevronRight, Code, Layout } from 'lucide-react';

interface JSONSchemaEditorProps {
  value: any;
  onChange: (schema: any) => void;
  className?: string;
  label?: string;
  description?: string;
}

type JSONSchemaType = 'string' | 'number' | 'integer' | 'boolean' | 'object' | 'array' | 'null';

interface SchemaProperty {
  type: JSONSchemaType | JSONSchemaType[];
  description?: string;
  default?: any;
  enum?: any[];
  items?: any;
  properties?: Record<string, SchemaProperty>;
  required?: string[];
  [key: string]: any;
}

export function JSONSchemaEditor({ value, onChange, className = '', label, description }: JSONSchemaEditorProps) {
  const [mode, setMode] = useState<'guided' | 'raw'>('guided');
  const [rawValue, setRawValue] = useState(JSON.stringify(value || {}, null, 2));
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(new Set(['root']));

  // Sync raw value when switching modes or value changes externally
  const handleModeSwitch = (newMode: 'guided' | 'raw') => {
    if (newMode === 'raw') {
      setRawValue(JSON.stringify(value || {}, null, 2));
      setJsonError(null);
    }
    setMode(newMode);
  };

  const handleRawChange = (newRaw: string) => {
    setRawValue(newRaw);
    try {
      const parsed = JSON.parse(newRaw);
      setJsonError(null);
      onChange(parsed);
    } catch (e) {
      setJsonError((e as Error).message);
    }
  };

  const toggleExpanded = (path: string) => {
    const newExpanded = new Set(expandedPaths);
    if (newExpanded.has(path)) {
      newExpanded.delete(path);
    } else {
      newExpanded.add(path);
    }
    setExpandedPaths(newExpanded);
  };

  const updateSchema = (updates: Partial<any>) => {
    onChange({ ...(value || {}), ...updates });
  };

  const addProperty = () => {
    const properties = value?.properties || {};
    const newKey = `property_${Object.keys(properties).length + 1}`;
    updateSchema({
      type: 'object',
      properties: {
        ...properties,
        [newKey]: { type: 'string' }
      }
    });
    setExpandedPaths(new Set([...expandedPaths, 'root']));
  };

  const removeProperty = (key: string) => {
    const properties = { ...(value?.properties || {}) };
    delete properties[key];
    const required = (value?.required || []).filter((r: string) => r !== key);
    updateSchema({
      properties,
      required: required.length > 0 ? required : undefined
    });
  };

  const updateProperty = (key: string, updates: Partial<SchemaProperty>) => {
    const properties = value?.properties || {};
    updateSchema({
      properties: {
        ...properties,
        [key]: { ...properties[key], ...updates }
      }
    });
  };

  const renameProperty = (oldKey: string, newKey: string) => {
    if (oldKey === newKey || !newKey) return;
    const properties = { ...(value?.properties || {}) };
    const propValue = properties[oldKey];
    delete properties[oldKey];
    properties[newKey] = propValue;

    const required = (value?.required || []).map((r: string) => r === oldKey ? newKey : r);
    updateSchema({
      properties,
      required: required.length > 0 ? required : undefined
    });
  };

  const toggleRequired = (key: string) => {
    const required = value?.required || [];
    const newRequired = required.includes(key)
      ? required.filter((r: string) => r !== key)
      : [...required, key];
    updateSchema({
      required: newRequired.length > 0 ? newRequired : undefined
    });
  };

  const PropertyEditor = ({ propKey, prop, path }: { propKey: string; prop: SchemaProperty; path: string }) => {
    const [localKey, setLocalKey] = useState(propKey);
    const [localDescription, setLocalDescription] = useState(prop.description || '');
    const [localDefault, setLocalDefault] = useState(prop.default ?? '');

    // Sync local state when prop key changes externally
    useEffect(() => {
      setLocalKey(propKey);
    }, [propKey]);

    useEffect(() => {
      setLocalDescription(prop.description || '');
    }, [prop.description]);

    useEffect(() => {
      setLocalDefault(prop.default ?? '');
    }, [prop.default]);

    const isExpanded = expandedPaths.has(path);
    const isRequired = (value?.required || []).includes(propKey);
    const propType = Array.isArray(prop.type) ? prop.type[0] : prop.type || 'string';
    const isComplex = propType === 'object' || propType === 'array';

    return (
      <div className="border border-white/[0.06] rounded-lg p-3 bg-[#161616]">
        <div className="flex items-start gap-2">
          {isComplex && (
            <button
              type="button"
              onClick={() => toggleExpanded(path)}
              className="mt-1 text-gray-500 hover:text-gray-400"
            >
              {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
            </button>
          )}

          <div className="flex-1 space-y-2">
            {/* Property Name and Type */}
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={localKey}
                onChange={(e) => setLocalKey(e.target.value)}
                onBlur={() => {
                  if (localKey && localKey !== propKey) {
                    renameProperty(propKey, localKey);
                  }
                }}
                className="input text-sm font-medium w-40"
                placeholder="property_name"
              />

              <select
                value={propType}
                onChange={(e) => {
                  const newType = e.target.value as JSONSchemaType;
                  const updates: Partial<SchemaProperty> = { type: newType };

                  if (newType === 'object') {
                    updates.properties = prop.properties || {};
                  } else if (newType === 'array') {
                    updates.items = prop.items || { type: 'string' };
                  }

                  updateProperty(propKey, updates);
                }}
                className="input text-sm w-32"
              >
                <option value="string">String</option>
                <option value="number">Number</option>
                <option value="integer">Integer</option>
                <option value="boolean">Boolean</option>
                <option value="object">Object</option>
                <option value="array">Array</option>
              </select>

              <label className="flex items-center text-sm text-gray-400">
                <input
                  type="checkbox"
                  checked={isRequired}
                  onChange={() => toggleRequired(propKey)}
                  className="mr-1"
                />
                Required
              </label>

              <button
                type="button"
                onClick={() => removeProperty(propKey)}
                className="ml-auto text-red-600 hover:text-red-400"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>

            {/* Description */}
            <input
              type="text"
              value={localDescription}
              onChange={(e) => setLocalDescription(e.target.value)}
              onBlur={() => {
                updateProperty(propKey, { description: localDescription || undefined });
              }}
              placeholder="Description (optional)"
              className="input text-sm w-full"
            />

            {/* Type-specific fields */}
            {propType === 'array' && isExpanded && (
              <div className="ml-4 pl-4 border-l-2 border-white/[0.06] space-y-2">
                <label className="block text-xs font-medium text-gray-300">Array Item Type</label>
                <select
                  value={(prop.items as any)?.type || 'string'}
                  onChange={(e) => updateProperty(propKey, {
                    items: {
                      ...(prop.items as any || {}),
                      type: e.target.value
                    }
                  })}
                  className="input text-sm w-32"
                >
                  <option value="string">String</option>
                  <option value="number">Number</option>
                  <option value="integer">Integer</option>
                  <option value="boolean">Boolean</option>
                  <option value="object">Object</option>
                </select>
              </div>
            )}

            {propType === 'object' && isExpanded && (
              <div className="ml-4 pl-4 border-l-2 border-white/[0.06]">
                <div className="text-xs font-medium text-gray-300 mb-2">Nested Properties</div>
                <div className="space-y-2">
                  {Object.entries(prop.properties || {}).map(([nestedKey, nestedProp], nestedIndex) => (
                    <PropertyEditor
                      key={`${path}.${nestedKey}-${nestedIndex}`}
                      propKey={nestedKey}
                      prop={nestedProp as SchemaProperty}
                      path={`${path}.${nestedKey}`}
                    />
                  ))}
                  <button
                    type="button"
                    onClick={() => {
                      const nested = { ...(prop.properties || {}) };
                      const newKey = `nested_${Object.keys(nested).length + 1}`;
                      nested[newKey] = { type: 'string' };
                      updateProperty(propKey, { properties: nested });
                    }}
                    className="text-sm text-primary-600 hover:text-primary-700 flex items-center"
                  >
                    <Plus className="w-4 h-4 mr-1" />
                    Add Nested Property
                  </button>
                </div>
              </div>
            )}

            {(propType === 'string' || propType === 'number' || propType === 'integer') && (
              <input
                type={propType === 'string' ? 'text' : 'number'}
                value={localDefault}
                onChange={(e) => setLocalDefault(e.target.value)}
                onBlur={() => {
                  const val = propType === 'string' ? localDefault : Number(localDefault);
                  updateProperty(propKey, { default: val || undefined });
                }}
                placeholder="Default value (optional)"
                className="input text-sm w-full"
              />
            )}
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className={className}>
      {label && (
        <label className="block text-sm font-medium text-gray-300 mb-2">
          {label}
        </label>
      )}
      {description && (
        <p className="text-sm text-gray-400 mb-2">{description}</p>
      )}

      {/* Mode Toggle */}
      <div className="flex items-center gap-2 mb-3">
        <button
          type="button"
          onClick={() => handleModeSwitch('guided')}
          className={`flex items-center px-3 py-1.5 text-sm rounded-md ${
            mode === 'guided'
              ? 'bg-primary-100 text-primary-700 font-medium'
              : 'bg-[#161616] text-gray-400 hover:bg-[#1e1e1e]'
          }`}
        >
          <Layout className="w-4 h-4 mr-1.5" />
          Guided
        </button>
        <button
          type="button"
          onClick={() => handleModeSwitch('raw')}
          className={`flex items-center px-3 py-1.5 text-sm rounded-md ${
            mode === 'raw'
              ? 'bg-primary-100 text-primary-700 font-medium'
              : 'bg-[#161616] text-gray-400 hover:bg-[#1e1e1e]'
          }`}
        >
          <Code className="w-4 h-4 mr-1.5" />
          Raw JSON
        </button>
      </div>

      {/* Editor Content */}
      {mode === 'guided' ? (
        <div className="space-y-3">
          <div className="space-y-2">
            {Object.entries(value?.properties || {}).map(([key, prop], index) => (
              <PropertyEditor
                key={`root.${key}-${index}`}
                propKey={key}
                prop={prop as SchemaProperty}
                path={`root.${key}`}
              />
            ))}
          </div>

          <button
            type="button"
            onClick={addProperty}
            className="btn btn-secondary text-sm flex items-center"
          >
            <Plus className="w-4 h-4 mr-1" />
            Add Property
          </button>

          {Object.keys(value?.properties || {}).length === 0 && (
            <div className="text-center py-8 text-gray-500 text-sm border-2 border-dashed border-white/10 rounded-lg">
              No properties defined. Click "Add Property" to get started.
            </div>
          )}
        </div>
      ) : (
        <div>
          <textarea
            value={rawValue}
            onChange={(e) => handleRawChange(e.target.value)}
            className={`input font-mono text-sm resize-none ${jsonError ? 'border-red-500' : ''}`}
            rows={12}
            placeholder='{"type": "object", "properties": {...}}'
          />
          {jsonError && (
            <div className="mt-2 text-sm text-red-600 bg-red-900/20 border border-red-800/30 rounded p-2">
              <strong>Invalid JSON:</strong> {jsonError}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
