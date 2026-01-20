import { useState, useRef } from 'react';
import type { ChangeEvent } from 'react';
import { apiClient } from '../lib/api';
import { useToast } from '../lib/toast-context';

interface ValidationError {
  location?: string[];
  message: string;
  severity?: string;
}

interface ConfigValidateResponse {
  valid: boolean;
  errors?: ValidationError[];
  warnings?: ValidationError[];
}

interface ConfigApplySummary {
  created: { [key: string]: number };
  updated: { [key: string]: number };
  unchanged: { [key: string]: number };
  deleted: { [key: string]: number };
}

interface ResourceChange {
  resourceType: string;
  resourceName: string;
  action: string;
  details?: string | null;
  changes?: any;
}

interface ConfigApplyResponse {
  success: boolean;
  summary: ConfigApplySummary;
  changes: ResourceChange[];
  errors?: string[];
  warnings?: string[];
}

type Step = 'edit' | 'validating' | 'dryrun' | 'confirm' | 'applying' | 'complete';

export function ConfigManager() {
  const [yamlContent, setYamlContent] = useState('');
  const [currentStep, setCurrentStep] = useState<Step>('edit');
  const [validationResult, setValidationResult] = useState<ConfigValidateResponse | null>(null);
  const [dryRunResult, setDryRunResult] = useState<ConfigApplyResponse | null>(null);
  const [applyResult, setApplyResult] = useState<ConfigApplyResponse | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { showToast } = useToast();

  const handleFileUpload = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (event) => {
      const content = event.target?.result as string;
      setYamlContent(content);
      resetWorkflow();
    };
    reader.readAsText(file);
  };

  const resetWorkflow = () => {
    setCurrentStep('edit');
    setValidationResult(null);
    setDryRunResult(null);
    setApplyResult(null);
  };

  const handleNext = async () => {
    if (!yamlContent.trim()) {
      showToast('Please enter YAML content', 'error');
      return;
    }

    // Step 1: Validate
    setCurrentStep('validating');

    try {
      const validation = await apiClient.validateConfig(yamlContent);
      setValidationResult(validation);

      if (!validation.valid) {
        showToast('YAML validation failed', 'error');
        setCurrentStep('edit');
        return;
      }

      showToast('YAML validated successfully', 'success');

      // Step 2: Dry run
      setCurrentStep('dryrun');

      const dryRun = await apiClient.applyConfig(yamlContent, true);
      setDryRunResult(dryRun);

      if (!dryRun.success) {
        showToast('Dry run completed with errors', 'error');
        setCurrentStep('edit');
        return;
      }

      showToast('Dry run completed successfully', 'success');
      setCurrentStep('confirm');

    } catch (error: any) {
      showToast(`Error: ${error.message}`, 'error');
      setCurrentStep('edit');
    }
  };

  const handleConfirm = async () => {
    setCurrentStep('applying');

    try {
      const result = await apiClient.applyConfig(yamlContent, false);
      setApplyResult(result);

      if (result.success) {
        showToast('Configuration applied successfully', 'success');
        setCurrentStep('complete');
      } else {
        showToast('Configuration applied with errors', 'error');
        setCurrentStep('edit');
      }
    } catch (error: any) {
      showToast(`Apply error: ${error.message}`, 'error');
      setCurrentStep('edit');
    }
  };

  const handleReject = () => {
    resetWorkflow();
    showToast('Configuration application cancelled', 'info');
  };

  const handleExport = async () => {
    try {
      const config = await apiClient.exportConfig();
      setYamlContent(config);
      showToast('Configuration exported successfully', 'success');
    } catch (error: any) {
      showToast(`Export error: ${error.message}`, 'error');
    }
  };

  const renderSummary = (summary: ConfigApplySummary) => {
    const sections = [
      { title: 'Created', data: summary.created, color: 'text-green-600' },
      { title: 'Updated', data: summary.updated, color: 'text-blue-600' },
      { title: 'Unchanged', data: summary.unchanged, color: 'text-gray-600' },
      { title: 'Deleted', data: summary.deleted, color: 'text-red-600' },
    ];

    return (
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
        {sections.map(({ title, data, color }) => {
          const total = Object.values(data).reduce((sum, count) => sum + count, 0);
          if (total === 0) return null;

          return (
            <div key={title} className="bg-white p-4 rounded-lg border">
              <h4 className={`font-semibold ${color} mb-2`}>{title}</h4>
              <div className="text-2xl font-bold">{total}</div>
              {Object.entries(data).map(([type, count]) => (
                count > 0 && (
                  <div key={type} className="text-sm text-gray-600 mt-1">
                    {type}: {count}
                  </div>
                )
              ))}
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Configuration Manager</h1>
        <button
          onClick={handleExport}
          className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 bg-white hover:bg-gray-50"
        >
          Export Current Config
        </button>
      </div>

      <div className="bg-white rounded-lg border p-6">
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <label className="block text-sm font-medium text-gray-700">
              YAML Configuration
            </label>
            <div className="flex gap-2">
              <input
                ref={fileInputRef}
                type="file"
                accept=".yaml,.yml"
                onChange={handleFileUpload}
                className="hidden"
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 bg-white hover:bg-gray-50"
              >
                Upload File
              </button>
              <button
                onClick={() => {
                  setYamlContent('');
                  setValidationResult(null);
                  setDryRunResult(null);
                }}
                className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 bg-white hover:bg-gray-50"
              >
                Clear
              </button>
            </div>
          </div>

          <textarea
            value={yamlContent}
            onChange={(e) => {
              setYamlContent(e.target.value);
              resetWorkflow();
            }}
            disabled={currentStep !== 'edit'}
            placeholder="Paste your YAML configuration here or upload a file..."
            className="w-full h-96 px-3 py-2 border border-gray-300 rounded-lg font-mono text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 disabled:bg-gray-50 disabled:text-gray-600"
          />

          {/* Step indicator */}
          <div className="flex items-center gap-2 text-sm">
            <div className={`flex items-center gap-2 ${currentStep === 'edit' ? 'text-blue-600 font-medium' : 'text-gray-400'}`}>
              <div className={`w-6 h-6 rounded-full flex items-center justify-center ${currentStep === 'edit' ? 'bg-blue-600 text-white' : 'bg-gray-200'}`}>1</div>
              <span>Edit</span>
            </div>
            <div className="flex-1 h-0.5 bg-gray-200"></div>
            <div className={`flex items-center gap-2 ${currentStep === 'validating' ? 'text-blue-600 font-medium' : currentStep === 'dryrun' || currentStep === 'confirm' || currentStep === 'applying' || currentStep === 'complete' ? 'text-green-600' : 'text-gray-400'}`}>
              <div className={`w-6 h-6 rounded-full flex items-center justify-center ${currentStep === 'validating' ? 'bg-blue-600 text-white' : currentStep === 'dryrun' || currentStep === 'confirm' || currentStep === 'applying' || currentStep === 'complete' ? 'bg-green-600 text-white' : 'bg-gray-200'}`}>2</div>
              <span>Validate</span>
            </div>
            <div className="flex-1 h-0.5 bg-gray-200"></div>
            <div className={`flex items-center gap-2 ${currentStep === 'dryrun' ? 'text-blue-600 font-medium' : currentStep === 'confirm' || currentStep === 'applying' || currentStep === 'complete' ? 'text-green-600' : 'text-gray-400'}`}>
              <div className={`w-6 h-6 rounded-full flex items-center justify-center ${currentStep === 'dryrun' ? 'bg-blue-600 text-white' : currentStep === 'confirm' || currentStep === 'applying' || currentStep === 'complete' ? 'bg-green-600 text-white' : 'bg-gray-200'}`}>3</div>
              <span>Dry Run</span>
            </div>
            <div className="flex-1 h-0.5 bg-gray-200"></div>
            <div className={`flex items-center gap-2 ${currentStep === 'confirm' || currentStep === 'applying' ? 'text-blue-600 font-medium' : currentStep === 'complete' ? 'text-green-600' : 'text-gray-400'}`}>
              <div className={`w-6 h-6 rounded-full flex items-center justify-center ${currentStep === 'confirm' || currentStep === 'applying' ? 'bg-blue-600 text-white' : currentStep === 'complete' ? 'bg-green-600 text-white' : 'bg-gray-200'}`}>4</div>
              <span>Confirm</span>
            </div>
          </div>

          <div className="flex gap-3">
            {currentStep === 'edit' && (
              <button
                onClick={handleNext}
                disabled={!yamlContent.trim()}
                className="px-6 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Next
              </button>
            )}

            {(currentStep === 'validating' || currentStep === 'dryrun') && (
              <div className="flex items-center gap-2 text-blue-600">
                <div className="inline-block animate-spin rounded-full h-5 w-5 border-b-2 border-blue-600"></div>
                <span className="font-medium">
                  {currentStep === 'validating' ? 'Validating...' : 'Running dry run...'}
                </span>
              </div>
            )}

            {currentStep === 'confirm' && (
              <>
                <button
                  onClick={handleConfirm}
                  className="px-6 py-2 bg-green-600 text-white rounded-lg text-sm font-medium hover:bg-green-700"
                >
                  Confirm & Apply
                </button>
                <button
                  onClick={handleReject}
                  className="px-6 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 bg-white hover:bg-gray-50"
                >
                  Reject
                </button>
              </>
            )}

            {currentStep === 'applying' && (
              <div className="flex items-center gap-2 text-green-600">
                <div className="inline-block animate-spin rounded-full h-5 w-5 border-b-2 border-green-600"></div>
                <span className="font-medium">Applying configuration...</span>
              </div>
            )}

            {currentStep === 'complete' && (
              <button
                onClick={resetWorkflow}
                className="px-6 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
              >
                Start New Configuration
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Validation Results */}
      {validationResult && (
        <div className={`bg-white rounded-lg border p-6 ${validationResult.valid ? 'border-green-300' : 'border-red-300'}`}>
          <h2 className="text-lg font-semibold mb-4">
            {validationResult.valid ? '✓ Validation Passed' : '✗ Validation Failed'}
          </h2>

          {validationResult.errors && validationResult.errors.length > 0 && (
            <div className="mb-4">
              <h3 className="font-medium text-red-600 mb-2">Errors:</h3>
              <div className="space-y-2">
                {validationResult.errors.map((error, idx) => (
                  <div key={idx} className="bg-red-50 border border-red-200 rounded p-3">
                    <div className="text-sm text-red-800">{error.message}</div>
                    {error.location && error.location.length > 0 && (
                      <div className="text-xs text-red-600 mt-1">Location: {error.location.join(' > ')}</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {validationResult.warnings && validationResult.warnings.length > 0 && (
            <div>
              <h3 className="font-medium text-yellow-600 mb-2">Warnings:</h3>
              <div className="space-y-2">
                {validationResult.warnings.map((warning, idx) => (
                  <div key={idx} className="bg-yellow-50 border border-yellow-200 rounded p-3">
                    <div className="text-sm text-yellow-800">{warning.message}</div>
                    {warning.location && warning.location.length > 0 && (
                      <div className="text-xs text-yellow-600 mt-1">Location: {warning.location.join(' > ')}</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Dry Run Results */}
      {dryRunResult && currentStep === 'confirm' && (
        <div className={`bg-white rounded-lg border p-6 ${dryRunResult.success ? 'border-blue-300' : 'border-red-300'}`}>
          <h2 className="text-lg font-semibold mb-4">Dry Run Results - Review Changes</h2>

          {renderSummary(dryRunResult.summary)}

          {dryRunResult.errors && dryRunResult.errors.length > 0 && (
            <div className="mb-4">
              <h3 className="font-medium text-red-600 mb-2">Errors:</h3>
              <div className="space-y-2">
                {dryRunResult.errors.map((error, idx) => (
                  <div key={idx} className="bg-red-50 border border-red-200 rounded p-3">
                    <div className="text-sm text-red-800">{error}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {dryRunResult.warnings && dryRunResult.warnings.length > 0 && (
            <div className="mb-4">
              <h3 className="font-medium text-yellow-600 mb-2">Warnings:</h3>
              <div className="space-y-2">
                {dryRunResult.warnings.map((warning, idx) => (
                  <div key={idx} className="bg-yellow-50 border border-yellow-200 rounded p-3">
                    <div className="text-sm text-yellow-800">{warning}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {dryRunResult.changes && dryRunResult.changes.length > 0 && (
            <div>
              <h3 className="font-medium text-gray-900 mb-2">
                Detailed Changes ({dryRunResult.changes.length} resources):
              </h3>
              <div className="space-y-2 max-h-96 overflow-y-auto">
                {dryRunResult.changes.map((change, idx) => (
                  <div key={idx} className="bg-gray-50 border border-gray-200 rounded p-3">
                    <div className="flex items-start gap-3">
                      <span className={`px-2 py-1 rounded text-xs font-semibold uppercase whitespace-nowrap ${
                        change.action === 'create' ? 'bg-green-100 text-green-800' :
                        change.action === 'update' ? 'bg-blue-100 text-blue-800' :
                        change.action === 'delete' ? 'bg-red-100 text-red-800' :
                        'bg-gray-100 text-gray-800'
                      }`}>
                        {change.action}
                      </span>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-baseline gap-2 mb-1">
                          <span className="text-sm font-semibold text-gray-900">{change.resourceType}</span>
                          <span className="text-sm text-gray-600 font-mono truncate">
                            {change.resourceName}
                          </span>
                        </div>
                        {change.details && (
                          <p className="text-xs text-gray-600 mt-1">{change.details}</p>
                        )}
                        {change.changes && Object.keys(change.changes).length > 0 && (
                          <div className="mt-2">
                            <details className="text-xs">
                              <summary className="cursor-pointer text-blue-600 hover:text-blue-700 font-medium">
                                View changes
                              </summary>
                              <pre className="text-xs text-gray-700 mt-2 bg-white p-2 rounded overflow-x-auto border">
                                {JSON.stringify(change.changes, null, 2)}
                              </pre>
                            </details>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Apply Results */}
      {applyResult && currentStep === 'complete' && (
        <div className={`bg-white rounded-lg border p-6 ${applyResult.success ? 'border-green-300' : 'border-red-300'}`}>
          <h2 className="text-lg font-semibold mb-4">
            {applyResult.success ? '✓ Configuration Applied Successfully' : '✗ Configuration Applied with Errors'}
          </h2>

          {renderSummary(applyResult.summary)}

          {applyResult.errors && applyResult.errors.length > 0 && (
            <div className="mb-4">
              <h3 className="font-medium text-red-600 mb-2">Errors:</h3>
              <div className="space-y-2">
                {applyResult.errors.map((error, idx) => (
                  <div key={idx} className="bg-red-50 border border-red-200 rounded p-3">
                    <div className="text-sm text-red-800">{error}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {applyResult.warnings && applyResult.warnings.length > 0 && (
            <div className="mb-4">
              <h3 className="font-medium text-yellow-600 mb-2">Warnings:</h3>
              <div className="space-y-2">
                {applyResult.warnings.map((warning, idx) => (
                  <div key={idx} className="bg-yellow-50 border border-yellow-200 rounded p-3">
                    <div className="text-sm text-yellow-800">{warning}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {applyResult.changes && applyResult.changes.length > 0 && (
            <div>
              <h3 className="font-medium text-gray-900 mb-2">
                Changes Applied ({applyResult.changes.length} resources):
              </h3>
              <div className="space-y-2 max-h-96 overflow-y-auto">
                {applyResult.changes.map((change, idx) => (
                  <div key={idx} className="bg-gray-50 border border-gray-200 rounded p-3">
                    <div className="flex items-start gap-3">
                      <span className={`px-2 py-1 rounded text-xs font-semibold uppercase whitespace-nowrap ${
                        change.action === 'create' ? 'bg-green-100 text-green-800' :
                        change.action === 'update' ? 'bg-blue-100 text-blue-800' :
                        change.action === 'delete' ? 'bg-red-100 text-red-800' :
                        'bg-gray-100 text-gray-800'
                      }`}>
                        {change.action}
                      </span>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-baseline gap-2 mb-1">
                          <span className="text-sm font-semibold text-gray-900">{change.resourceType}</span>
                          <span className="text-sm text-gray-600 font-mono truncate">
                            {change.resourceName}
                          </span>
                        </div>
                        {change.details && (
                          <p className="text-xs text-gray-600 mt-1">{change.details}</p>
                        )}
                        {change.changes && Object.keys(change.changes).length > 0 && (
                          <div className="mt-2">
                            <details className="text-xs">
                              <summary className="cursor-pointer text-blue-600 hover:text-blue-700 font-medium">
                                View changes
                              </summary>
                              <pre className="text-xs text-gray-700 mt-2 bg-white p-2 rounded overflow-x-auto border">
                                {JSON.stringify(change.changes, null, 2)}
                              </pre>
                            </details>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
