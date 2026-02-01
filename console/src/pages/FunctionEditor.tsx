import { useState, useEffect } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { ArrowLeft, Save, Trash2, Package } from 'lucide-react';
import CodeEditor from '@uiw/react-textarea-code-editor';
import { JSONSchemaEditor } from '../components/JSONSchemaEditor';

export function FunctionEditor() {
  const { namespace, name } = useParams<{ namespace: string; name: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isNew = namespace === 'new';

  const [formData, setFormData] = useState({
    namespace: 'default',
    name: '',
    description: '',
    code: `def my_function(input, context):
    """
    Function entry point.

    Args:
        input: Input parameters validated against input_schema
        context: Execution context containing:
            - user_id: Authenticated user's ID
            - user_email: User's email address
            - access_token: JWT token for making authenticated API calls
            - execution_id: Current execution ID
            - trigger_type: How function was triggered (WEBHOOK, AGENT, SCHEDULE)
            - chat_id: Optional chat ID if triggered from a chat

    Returns:
        Any type matching output_schema (string, number, boolean, array, object, etc.)
    """
    # Example: Use access token to call other SINAS APIs
    # import requests
    # headers = {"Authorization": f"Bearer {context['access_token']}"}
    # response = requests.get("http://host.docker.internal:8000/api/v1/...", headers=headers)

    # Your code here
    return {"result": "success"}`,
    input_schema: {
      type: "object",
      properties: {
        message: {
          type: "string",
          description: "Input message"
        }
      },
      required: ["message"]
    } as any,
    output_schema: {
      type: "object",
      properties: {
        result: {
          type: "string",
          description: "Output result"
        }
      },
      required: ["result"]
    } as any,
    requirements: [] as string[],
    enabled_namespaces: [] as string[],
    shared_pool: false,
    requires_approval: false,
  });

  const [requirementInput, setRequirementInput] = useState('');
  const [namespaceInput, setNamespaceInput] = useState('');

  const { data: func, isLoading } = useQuery({
    queryKey: ['function', namespace, name],
    queryFn: () => apiClient.getFunction(namespace!, name!),
    enabled: !isNew && !!namespace && !!name,
  });

  // Load function data when available
  useEffect(() => {
    if (func && !isNew) {
      setFormData({
        namespace: func.namespace || 'default',
        name: func.name || '',
        description: func.description || '',
        code: func.code || '',
        input_schema: func.input_schema || {},
        output_schema: func.output_schema || {},
        requirements: func.requirements || [],
        enabled_namespaces: func.enabled_namespaces || [],
        shared_pool: func.shared_pool || false,
        requires_approval: func.requires_approval || false,
      });
    }
  }, [func, isNew]);

  const saveMutation = useMutation({
    mutationFn: (data: any) => {
      return isNew
        ? apiClient.createFunction(data)
        : apiClient.updateFunction(namespace!, name!, data);
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['functions'] });
      queryClient.invalidateQueries({ queryKey: ['function', namespace, name] });
      if (isNew) {
        navigate(`/functions/${data.namespace}/${data.name}`);
      } else if (data.namespace !== namespace || data.name !== name) {
        // Name or namespace changed, navigate to new URL
        navigate(`/functions/${data.namespace}/${data.name}`);
      }
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => apiClient.deleteFunction(namespace!, name!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['functions'] });
      navigate('/functions');
    },
  });

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault();

    // Validate that function definition exists (with optional context parameter)
    const functionDefRegex = new RegExp(`def\\s+${formData.name}\\s*\\(`);
    if (!functionDefRegex.test(formData.code)) {
      alert(`Code must contain a function definition matching the name: def ${formData.name}(input, context):`);
      return;
    }

    saveMutation.mutate(formData);
  };

  const addRequirement = () => {
    if (requirementInput.trim() && !formData.requirements.includes(requirementInput.trim())) {
      setFormData({
        ...formData,
        requirements: [...formData.requirements, requirementInput.trim()],
      });
      setRequirementInput('');
    }
  };

  const removeRequirement = (req: string) => {
    setFormData({
      ...formData,
      requirements: formData.requirements.filter((r) => r !== req),
    });
  };

  const addNamespace = () => {
    if (namespaceInput.trim() && !formData.enabled_namespaces.includes(namespaceInput.trim())) {
      setFormData({
        ...formData,
        enabled_namespaces: [...formData.enabled_namespaces, namespaceInput.trim()],
      });
      setNamespaceInput('');
    }
  };

  const removeNamespace = (namespace: string) => {
    setFormData({
      ...formData,
      enabled_namespaces: formData.enabled_namespaces.filter((n) => n !== namespace),
    });
  };

  // Check if the entry point function exists in the code
  const hasEntryPoint = () => {
    if (!formData.name) return false;
    const escapedName = formData.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const functionRegex = new RegExp(`def\\s+${escapedName}\\s*\\(`);
    return functionRegex.test(formData.code);
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center">
          <Link to="/functions" className="mr-4 text-gray-600 hover:text-gray-900">
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <div>
            <h1 className="text-3xl font-bold text-gray-900">
              {isNew ? 'New Function' : (
                <>
                  <span className="text-gray-500">{formData.namespace}/</span>{formData.name || 'Edit Function'}
                </>
              )}
            </h1>
            <p className="text-gray-600 mt-1">
              {isNew ? 'Create a new Python function' : 'Edit function configuration and code'}
            </p>
          </div>
        </div>
        <div className="flex space-x-3">
          {!isNew && (
            <button
              onClick={() => {
                if (confirm('Are you sure you want to delete this function?')) {
                  deleteMutation.mutate();
                }
              }}
              className="btn btn-danger flex items-center"
              disabled={deleteMutation.isPending}
            >
              <Trash2 className="w-4 h-4 mr-2" />
              Delete
            </button>
          )}
          <button
            onClick={handleSave}
            disabled={saveMutation.isPending}
            className="btn btn-primary flex items-center"
          >
            <Save className="w-4 h-4 mr-2" />
            {saveMutation.isPending ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>

      {/* Success/Error Messages */}
      {saveMutation.isError && (
        <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          Failed to save function. Please check your code and JSON schemas.
        </div>
      )}

      {saveMutation.isSuccess && (
        <div className="p-4 bg-green-50 border border-green-200 rounded-lg text-sm text-green-700">
          Function saved successfully!
        </div>
      )}

      <form onSubmit={handleSave} className="space-y-6">
        {/* Basic Info */}
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Basic Information</h2>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Namespace *
              </label>
              <input
                type="text"
                value={formData.namespace}
                onChange={(e) => setFormData({ ...formData, namespace: e.target.value })}
                placeholder="default"
                pattern="^[a-z][a-z0-9_-]*$"
                title="Must start with lowercase letter, contain only lowercase letters, numbers, underscores, and hyphens"
                required
                className="input"
              />
              <p className="text-xs text-gray-500 mt-1">
                Use lowercase letters, numbers, underscores, and hyphens
              </p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Function Name *
              </label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                placeholder="my_function"
                pattern="^[a-zA-Z_][a-zA-Z0-9_]*$"
                title="Must start with letter or underscore, contain only letters, numbers, and underscores"
                required
                className="input"
              />
              <p className="text-xs text-gray-500 mt-1">
                Must start with letter or underscore, contain only alphanumerics and underscores
              </p>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Description
              </label>
              <input
                type="text"
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                placeholder="What does this function do?"
                className="input"
              />
            </div>

            <div className="space-y-3 pt-2 border-t border-gray-200">
              <h3 className="text-sm font-medium text-gray-900">Execution Settings</h3>

              <div className="flex items-start">
                <input
                  type="checkbox"
                  id="shared_pool"
                  checked={formData.shared_pool}
                  onChange={(e) => setFormData({ ...formData, shared_pool: e.target.checked })}
                  className="mt-1 h-4 w-4 text-primary-600 focus:ring-primary-500 border-gray-300 rounded"
                />
                <label htmlFor="shared_pool" className="ml-3">
                  <span className="block text-sm font-medium text-gray-700">Use Shared Worker Pool</span>
                  <span className="block text-xs text-gray-500 mt-0.5">
                    Run in shared worker pool instead of isolated container. More efficient for trusted functions with high call frequency.
                  </span>
                </label>
              </div>

              <div className="flex items-start">
                <input
                  type="checkbox"
                  id="requires_approval"
                  checked={formData.requires_approval}
                  onChange={(e) => setFormData({ ...formData, requires_approval: e.target.checked })}
                  className="mt-1 h-4 w-4 text-primary-600 focus:ring-primary-500 border-gray-300 rounded"
                />
                <label htmlFor="requires_approval" className="ml-3">
                  <span className="block text-sm font-medium text-gray-700">Require Approval Before Execution</span>
                  <span className="block text-xs text-gray-500 mt-0.5">
                    LLM must ask user for approval before calling this function. Use for dangerous operations (delete, send email, etc.).
                  </span>
                </label>
              </div>
            </div>
          </div>
        </div>

        {/* Code Editor */}
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">Python Code *</h2>
          <div className="border border-gray-300 rounded-lg overflow-hidden" style={{ minHeight: '400px' }}>
            <CodeEditor
              value={formData.code}
              language="python"
              placeholder="Enter your Python code here..."
              onChange={(e) => setFormData({ ...formData, code: e.target.value })}
              padding={15}
              style={{
                fontSize: 14,
                backgroundColor: '#fafafa',
                color: '#1f2937',
                fontFamily: 'ui-monospace, SFMono-Regular, SF Mono, Consolas, Liberation Mono, Menlo, monospace',
                minHeight: '400px',
              }}
            />
          </div>
          <div className="flex items-center justify-between mt-2">
            <p className="text-xs text-gray-500">
              Entry point must be <code className="font-mono bg-gray-100 px-1 rounded">def {formData.name || 'function_name'}(input, context):</code> matching the Function Name above. The <code className="font-mono bg-gray-100 px-1 rounded">context</code> parameter provides user info and access token. Return value can be any type matching output_schema.
            </p>
            {formData.name && (
              <div className="flex items-center ml-4">
                {hasEntryPoint() ? (
                  <span className="flex items-center text-xs text-green-600 font-medium">
                    <svg className="w-4 h-4 mr-1" fill="currentColor" viewBox="0 0 20 20">
                      <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                    </svg>
                    Entry point found
                  </span>
                ) : (
                  <span className="flex items-center text-xs text-red-600 font-medium">
                    <svg className="w-4 h-4 mr-1" fill="currentColor" viewBox="0 0 20 20">
                      <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
                    </svg>
                    Entry point missing
                  </span>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Input Schema */}
        <div className="card">
          <JSONSchemaEditor
            label="Input Schema (JSON Schema) *"
            description="Define expected input parameters for this function"
            value={formData.input_schema}
            onChange={(schema) => setFormData({ ...formData, input_schema: schema })}
          />
        </div>

        {/* Output Schema */}
        <div className="card">
          <JSONSchemaEditor
            label="Output Schema (JSON Schema) *"
            description="Define expected output structure for this function"
            value={formData.output_schema}
            onChange={(schema) => setFormData({ ...formData, output_schema: schema })}
          />
        </div>

        {/* Requirements */}
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-4">
            <Package className="w-5 h-5 inline mr-2" />
            Python Requirements
          </h2>
          <div className="flex space-x-2 mb-2">
            <input
              type="text"
              value={requirementInput}
              onChange={(e) => setRequirementInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addRequirement())}
              placeholder="requests, numpy==1.24.0, etc. (press Enter)"
              className="input flex-1"
            />
            <button
              type="button"
              onClick={addRequirement}
              className="btn btn-secondary"
            >
              Add
            </button>
          </div>
          {formData.requirements.length > 0 && (
            <div className="space-y-2">
              {formData.requirements.map((req) => (
                <div
                  key={req}
                  className="flex items-center justify-between p-2 bg-gray-50 rounded"
                >
                  <span className="font-mono text-sm">{req}</span>
                  <button
                    type="button"
                    onClick={() => removeRequirement(req)}
                    className="text-red-600 hover:text-red-700 text-sm"
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Enabled Namespaces */}
        <div className="card">
          <h2 className="text-lg font-semibold text-gray-900 mb-2">Enabled Namespaces</h2>
          <p className="text-sm text-gray-600 mb-4">
            Namespaces this function can call (empty = own namespace only)
          </p>
          <div className="flex space-x-2 mb-2">
            <input
              type="text"
              value={namespaceInput}
              onChange={(e) => setNamespaceInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addNamespace())}
              placeholder="payments, email, etc. (press Enter)"
              className="input flex-1"
            />
            <button
              type="button"
              onClick={addNamespace}
              className="btn btn-secondary"
            >
              Add
            </button>
          </div>
          {formData.enabled_namespaces.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {formData.enabled_namespaces.map((namespace) => (
                <div
                  key={namespace}
                  className="inline-flex items-center gap-2 px-3 py-1 bg-primary-100 text-primary-800 rounded-full text-sm"
                >
                  <span className="font-mono">{namespace}</span>
                  <button
                    type="button"
                    onClick={() => removeNamespace(namespace)}
                    className="text-primary-600 hover:text-primary-900"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </form>
    </div>
  );
}
