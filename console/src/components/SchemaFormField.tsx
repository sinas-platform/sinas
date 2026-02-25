import { useState } from 'react';

interface SchemaFormFieldProps {
  name: string;
  schema: {
    type?: string;
    description?: string;
    enum?: any[];
    default?: any;
    items?: any;
  };
  value: any;
  onChange: (value: any) => void;
  required?: boolean;
  placeholder?: string;
}

export function SchemaFormField({
  name,
  schema,
  value,
  onChange,
  required = false,
  placeholder
}: SchemaFormFieldProps) {
  const [jsonError, setJsonError] = useState<string | null>(null);

  const type = schema.type || 'string';
  const description = schema.description;
  const enumValues = schema.enum;

  // Handle enum (select dropdown)
  if (enumValues && enumValues.length > 0) {
    return (
      <div className="mb-3">
        <label className="block text-sm font-medium text-gray-300 mb-1">
          {name} {required && <span className="text-red-500">*</span>}
        </label>
        {description && (
          <p className="text-xs text-gray-500 mb-1">{description}</p>
        )}
        <select
          value={value ?? ''}
          onChange={(e) => {
            const val = e.target.value;
            // Try to parse to proper type if it's a number or boolean
            if (val === 'true') onChange(true);
            else if (val === 'false') onChange(false);
            else if (!isNaN(Number(val)) && val !== '') onChange(Number(val));
            else onChange(val);
          }}
          required={required}
          className="input"
        >
          <option value="">Select...</option>
          {enumValues.map((enumVal) => (
            <option key={String(enumVal)} value={String(enumVal)}>
              {String(enumVal)}
            </option>
          ))}
        </select>
      </div>
    );
  }

  // Handle different types
  switch (type) {
    case 'boolean':
      return (
        <div className="mb-3">
          <label className="flex items-center space-x-2">
            <input
              type="checkbox"
              checked={value === true}
              onChange={(e) => onChange(e.target.checked)}
              className="rounded border-white/10 text-primary-600 focus:ring-primary-500"
            />
            <span className="text-sm font-medium text-gray-300">
              {name} {required && <span className="text-red-500">*</span>}
            </span>
          </label>
          {description && (
            <p className="text-xs text-gray-500 mt-1 ml-6">{description}</p>
          )}
        </div>
      );

    case 'integer':
    case 'number':
      return (
        <div className="mb-3">
          <label className="block text-sm font-medium text-gray-300 mb-1">
            {name} {required && <span className="text-red-500">*</span>}
          </label>
          {description && (
            <p className="text-xs text-gray-500 mb-1">{description}</p>
          )}
          <input
            type="number"
            step={type === 'integer' ? '1' : 'any'}
            value={value ?? ''}
            onChange={(e) => {
              const val = e.target.value;
              if (val === '') {
                onChange('');
              } else {
                const num = Number(val);
                onChange(type === 'integer' ? Math.floor(num) : num);
              }
            }}
            placeholder={placeholder || (schema.default != null ? String(schema.default) : '')}
            required={required}
            className="input"
          />
        </div>
      );

    case 'array':
      return (
        <div className="mb-3">
          <label className="block text-sm font-medium text-gray-300 mb-1">
            {name} {required && <span className="text-red-500">*</span>}
            <span className="text-xs text-gray-500 ml-2">(JSON array)</span>
          </label>
          {description && (
            <p className="text-xs text-gray-500 mb-1">{description}</p>
          )}
          <textarea
            value={typeof value === 'string' ? value : JSON.stringify(value ?? [], null, 2)}
            onChange={(e) => {
              const val = e.target.value;
              try {
                const parsed = JSON.parse(val);
                if (Array.isArray(parsed)) {
                  onChange(parsed);
                  setJsonError(null);
                } else {
                  setJsonError('Value must be an array');
                }
              } catch (err) {
                setJsonError('Invalid JSON');
                // Store as string temporarily
                onChange(val);
              }
            }}
            placeholder={placeholder || '["item1", "item2"]'}
            required={required}
            className="input font-mono text-sm"
            rows={3}
          />
          {jsonError && (
            <p className="text-xs text-red-600 mt-1">{jsonError}</p>
          )}
        </div>
      );

    case 'object':
      return (
        <div className="mb-3">
          <label className="block text-sm font-medium text-gray-300 mb-1">
            {name} {required && <span className="text-red-500">*</span>}
            <span className="text-xs text-gray-500 ml-2">(JSON object)</span>
          </label>
          {description && (
            <p className="text-xs text-gray-500 mb-1">{description}</p>
          )}
          <textarea
            value={typeof value === 'string' ? value : JSON.stringify(value ?? {}, null, 2)}
            onChange={(e) => {
              const val = e.target.value;
              try {
                const parsed = JSON.parse(val);
                if (typeof parsed === 'object' && !Array.isArray(parsed)) {
                  onChange(parsed);
                  setJsonError(null);
                } else {
                  setJsonError('Value must be an object');
                }
              } catch (err) {
                setJsonError('Invalid JSON');
                // Store as string temporarily
                onChange(val);
              }
            }}
            placeholder={placeholder || '{"key": "value"}'}
            required={required}
            className="input font-mono text-sm"
            rows={4}
          />
          {jsonError && (
            <p className="text-xs text-red-600 mt-1">{jsonError}</p>
          )}
        </div>
      );

    case 'string':
    default:
      return (
        <div className="mb-3">
          <label className="block text-sm font-medium text-gray-300 mb-1">
            {name} {required && <span className="text-red-500">*</span>}
          </label>
          {description && (
            <p className="text-xs text-gray-500 mb-1">{description}</p>
          )}
          <input
            type="text"
            value={value ?? ''}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder || (schema.default != null ? String(schema.default) : '')}
            required={required}
            className="input"
          />
        </div>
      );
  }
}
