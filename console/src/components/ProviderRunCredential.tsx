import { useEffect } from 'react';
import {
  configWithRunCredential,
  runCredentialFromConfig,
  runCredentialProblem,
  type RunCredentialDraft,
} from '../lib/runCredential';

/**
 * An LLM provider's per-run credential setting (config.run_credential), kept
 * in step with the configuration JSON next to it, like ProviderHeaders.
 * `disabled` while that JSON doesn't parse. `onProblemChange` reports the
 * setting's problem, for the form to refuse saving.
 */
export function ProviderRunCredential({
  idPrefix,
  config,
  providerType,
  onChange,
  disabled = false,
  onProblemChange,
}: {
  idPrefix: string;
  config: Record<string, unknown> | undefined;
  providerType: string;
  onChange: (config: Record<string, unknown>) => void;
  disabled?: boolean;
  onProblemChange?: (problem: string | null) => void;
}) {
  const draft = runCredentialFromConfig(config);
  const problem = runCredentialProblem(draft, providerType);
  useEffect(() => {
    onProblemChange?.(problem);
  }, [problem, onProblemChange]);
  const update = (next: Partial<RunCredentialDraft>) => onChange(configWithRunCredential(config, { ...draft, ...next }));

  return (
    <div className="border-t border-line-soft pt-4">
      <h3 className="text-sm font-medium text-gray-300 mb-2">Per-run credential</h3>
      <p className="text-xs text-gray-500 mb-2">
        Lets the service that starts a run give its chat its own key for this provider (
        <code>provider_credentials</code>), presented on every call of the run.{' '}
        <a
          href="https://docs.sinas.co/admin/llm-gateway-attribution#per-run-credentials"
          target="_blank"
          rel="noreferrer"
          className="text-primary-400 hover:underline"
        >
          How it works
        </a>
      </p>
      {disabled && <p className="text-xs text-amber-400 mb-2">Fix the configuration JSON to edit this here.</p>}
      <label className="flex items-center gap-2 text-sm text-gray-300">
        <input
          id={`${idPrefix}-run-credential-enabled`}
          type="checkbox"
          checked={draft.enabled}
          disabled={disabled}
          onChange={(e) => update({ enabled: e.target.checked, required: e.target.checked && draft.required })}
        />
        Accept a per-run credential
      </label>
      {draft.enabled && (
        <div className="mt-2 space-y-2">
          <div className="flex items-center gap-2">
            <input
              id={`${idPrefix}-run-credential-header`}
              value={draft.header}
              onChange={(e) => update({ header: e.target.value, prefix: e.target.value.trim() ? draft.prefix : '' })}
              placeholder="Header (empty: in place of the provider's key)"
              disabled={disabled}
              className="input font-mono text-xs flex-1"
            />
            <input
              id={`${idPrefix}-run-credential-prefix`}
              value={draft.prefix}
              onChange={(e) => update({ prefix: e.target.value })}
              placeholder="Prefix"
              disabled={disabled || !draft.header.trim()}
              className="input font-mono text-xs w-32"
            />
          </div>
          <label className="flex items-center gap-2 text-sm text-gray-300">
            <input
              id={`${idPrefix}-run-credential-required`}
              type="checkbox"
              checked={draft.required}
              disabled={disabled}
              onChange={(e) => update({ required: e.target.checked })}
            />
            Required (never fall back to the configured key)
          </label>
        </div>
      )}
      {problem && <p className="text-xs text-red-400 mt-1">{problem}</p>}
    </div>
  );
}
