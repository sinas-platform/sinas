import { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { apiClient } from '../lib/api';
import { Play, Code, AlertCircle, CheckCircle, Loader } from 'lucide-react';
import { SchemaFormField } from '../components/SchemaFormField';

export function FunctionExecute() {
  const [selectedFunction, setSelectedFunction] = useState<string>('');
  const [inputJson, setInputJson] = useState<string>('{}');
  const [inputError, setInputError] = useState<string>('');
  const [inputParams, setInputParams] = useState<Record<string, any>>({});
  const [useAdvancedMode, setUseAdvancedMode] = useState(false);

  const { data: functions, isLoading: loadingFunctions } = useQuery({
    queryKey: ['functions'],
    queryFn: () => apiClient.listFunctions(),
  });

  const executeMutation = useMutation({
    mutationFn: async () => {
      if (!selectedFunction) {
        throw new Error('Please select a function');
      }

      // Use schema-based input params if not in advanced mode and schema exists
      let inputData;
      if (!useAdvancedMode && selectedFunctionObj?.input_schema?.properties) {
        inputData = inputParams;
      } else {
        // Parse input JSON in advanced mode
        try {
          inputData = JSON.parse(inputJson);
        } catch (e) {
          throw new Error(`Invalid JSON: ${(e as Error).message}`);
        }
      }

      const [namespace, name] = selectedFunction.split('/');
      return apiClient.executeFunction(namespace, name, inputData);
    },
  });

  const handleExecute = () => {
    setInputError('');
    executeMutation.mutate();
  };

  const selectedFunctionObj = functions?.find(
    (f) => `${f.namespace}/${f.name}` === selectedFunction
  );

  // Reset input when function changes
  const handleFunctionChange = (functionRef: string) => {
    setSelectedFunction(functionRef);
    setInputParams({});
    setInputJson('{}');
    setInputError('');
    setUseAdvancedMode(false);
  };

  // Check if selected function has input schema
  const hasInputSchema = selectedFunctionObj?.input_schema?.properties &&
    Object.keys(selectedFunctionObj.input_schema.properties).length > 0;

  const prettyPrintJson = (json: string) => {
    try {
      return JSON.stringify(JSON.parse(json), null, 2);
    } catch {
      return json;
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-100">Function Executions</h1>
        <p className="mt-1 text-sm text-gray-500">
          Test and run functions directly from the UI
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Input Panel */}
        <div className="space-y-6">
          <div className="bg-[#161616] rounded-lg border border-white/[0.06] p-6">
            <h3 className="text-lg font-semibold text-gray-100 mb-4">Input</h3>

            {/* Function Selector */}
            <div className="mb-4">
              <label className="label">Function</label>
              <select
                value={selectedFunction}
                onChange={(e) => handleFunctionChange(e.target.value)}
                className="input"
                disabled={loadingFunctions}
              >
                <option value="">Select a function...</option>
                {functions?.map((func) => (
                  <option key={func.id} value={`${func.namespace}/${func.name}`}>
                    {func.namespace}/{func.name}
                  </option>
                ))}
              </select>
            </div>

            {/* Function Details */}
            {selectedFunctionObj && (
              <div className="mb-4 p-3 bg-[#0d0d0d] rounded border border-white/[0.06]">
                <p className="text-sm font-medium text-gray-100 mb-1">
                  {selectedFunctionObj.namespace}/{selectedFunctionObj.name}
                </p>
                {selectedFunctionObj.description && (
                  <p className="text-sm text-gray-400">{selectedFunctionObj.description}</p>
                )}
              </div>
            )}

            {/* Input Mode Toggle (only show if function has schema) */}
            {hasInputSchema && (
              <div className="mb-4 flex items-center justify-between p-3 bg-blue-900/20 border border-blue-800/30 rounded">
                <span className="text-sm text-blue-300">
                  {useAdvancedMode ? 'Advanced Mode (JSON)' : 'Form Mode (Schema-based)'}
                </span>
                <button
                  onClick={() => setUseAdvancedMode(!useAdvancedMode)}
                  className="text-xs text-blue-400 hover:text-blue-300 underline"
                >
                  {useAdvancedMode ? 'Switch to Form' : 'Switch to JSON'}
                </button>
              </div>
            )}

            {/* Schema-based Form (if function has schema and not in advanced mode) */}
            {hasInputSchema && !useAdvancedMode ? (
              <div className="border-t pt-4">
                <h4 className="text-sm font-medium text-gray-100 mb-3">Input Parameters</h4>
                {Object.entries(selectedFunctionObj.input_schema.properties || {}).map(([key, prop]: [string, any]) => (
                  <SchemaFormField
                    key={key}
                    name={key}
                    schema={prop}
                    value={inputParams[key]}
                    onChange={(value) => setInputParams({ ...inputParams, [key]: value })}
                    required={selectedFunctionObj.input_schema.required?.includes(key)}
                  />
                ))}
              </div>
            ) : (
              /* JSON Editor (advanced mode or no schema) */
              <div>
                <div className="flex items-center justify-between mb-2">
                  <label className="label">Input Data (JSON)</label>
                  <button
                    onClick={() => {
                      try {
                        setInputJson(prettyPrintJson(inputJson));
                        setInputError('');
                      } catch (e) {
                        setInputError(`Invalid JSON: ${(e as Error).message}`);
                      }
                    }}
                    className="text-xs text-primary-600 hover:text-primary-700"
                  >
                    Format JSON
                  </button>
                </div>
                <textarea
                  value={inputJson}
                  onChange={(e) => {
                    setInputJson(e.target.value);
                    setInputError('');
                  }}
                  className="input font-mono text-sm h-64 resize-none"
                  placeholder={'{\n  "key": "value"\n}'}
                />
                {inputError && (
                  <p className="mt-2 text-sm text-red-600 flex items-center">
                    <AlertCircle className="w-4 h-4 mr-1" />
                    {inputError}
                  </p>
                )}
              </div>
            )}

            {/* Execute Button */}
            <button
              onClick={handleExecute}
              disabled={!selectedFunction || executeMutation.isPending}
              className="btn btn-primary w-full mt-4"
            >
              {executeMutation.isPending ? (
                <>
                  <Loader className="w-4 h-4 animate-spin" />
                  Executing...
                </>
              ) : (
                <>
                  <Play className="w-4 h-4" />
                  Execute Function
                </>
              )}
            </button>
          </div>
        </div>

        {/* Output Panel */}
        <div className="space-y-6">
          <div className="bg-[#161616] rounded-lg border border-white/[0.06] p-6">
            <h3 className="text-lg font-semibold text-gray-100 mb-4">Output</h3>

            {!executeMutation.data && !executeMutation.error && !executeMutation.isPending && (
              <div className="flex flex-col items-center justify-center py-12 text-gray-500">
                <Code className="w-12 h-12 mb-2" />
                <p className="text-sm">Execute a function to see results</p>
              </div>
            )}

            {executeMutation.isPending && (
              <div className="flex flex-col items-center justify-center py-12 text-gray-500">
                <Loader className="w-12 h-12 mb-2 animate-spin" />
                <p className="text-sm">Function executing...</p>
              </div>
            )}

            {executeMutation.isError && (
              <div className="p-4 bg-red-900/20 border border-red-800/30 rounded">
                <div className="flex items-start">
                  <AlertCircle className="w-5 h-5 text-red-600 mt-0.5 mr-2" />
                  <div>
                    <p className="font-medium text-red-900">Execution Error</p>
                    <p className="text-sm text-red-400 mt-1">
                      {(executeMutation.error as Error).message}
                    </p>
                  </div>
                </div>
              </div>
            )}

            {executeMutation.data && (
              <div>
                {/* Status Badge */}
                {executeMutation.data.status === 'success' ? (
                  <div className="flex items-center mb-4 text-green-400">
                    <CheckCircle className="w-5 h-5 mr-2" />
                    <span className="font-medium">Execution Successful</span>
                  </div>
                ) : (
                  <div className="flex items-center mb-4 text-red-400">
                    <AlertCircle className="w-5 h-5 mr-2" />
                    <span className="font-medium">Execution Failed</span>
                  </div>
                )}

                {/* Execution ID */}
                <div className="mb-4 p-3 bg-[#0d0d0d] rounded border border-white/[0.06]">
                  <p className="text-xs text-gray-500">Execution ID</p>
                  <p className="text-sm font-mono text-gray-100 mt-1">
                    {executeMutation.data.execution_id}
                  </p>
                </div>

                {/* Result/Error Output */}
                <div>
                  <p className="label mb-2">
                    {executeMutation.data.status === 'success' ? 'Result' : 'Error'}
                  </p>
                  <pre className="bg-gray-900 text-gray-100 p-4 rounded text-sm overflow-auto max-h-96">
                    {executeMutation.data.status === 'success'
                      ? JSON.stringify(executeMutation.data.result, null, 2)
                      : executeMutation.data.error}
                  </pre>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
