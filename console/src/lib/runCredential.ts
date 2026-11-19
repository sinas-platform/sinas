// An LLM provider's per-run credential setting (config.run_credential) in the
// console. Pure functions (tested in tests/run-credential.test.mjs). The
// backend validates the same rules (app/providers/run_credentials.py); these
// only point out problems before saving.

export interface RunCredentialDraft {
  enabled: boolean;
  /** '' = in place of the provider's own key (header: null) */
  header: string;
  prefix: string;
  required: boolean;
}

// Provider types with a key of their own that `header: null` replaces.
const NATIVE_TYPES = new Set(['openai', 'gemini', 'mistral', 'anthropic', 'azure', 'azure_openai']);
const HEADER_NAME = /^[A-Za-z0-9][A-Za-z0-9-]{0,99}$/;
const PRINTABLE = /^[\x20-\x7e]*$/;
const FORBIDDEN = new Set([
  'host', 'content-length', 'content-type', 'content-encoding', 'transfer-encoding', 'connection',
  'keep-alive', 'te', 'trailer', 'upgrade', 'expect', 'accept', 'accept-encoding', 'user-agent', 'cookie',
  'set-cookie', 'forwarded', 'x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto', 'x-real-ip',
  'idempotency-key', 'x-should-retry', 'traceparent', 'tracestate', 'baggage', 'b3', 'uber-trace-id',
  'sentry-trace', 'x-cloud-trace-context', 'grpc-trace-bin', 'x-amzn-trace-id',
]);
const FORBIDDEN_PREFIXES = ['x-sinas-', 'anthropic-', 'x-stainless-', 'sec-', 'x-b3-', 'uberctx-', 'ot-tracer-', 'x-datadog-'];

/** The form's draft for a provider's config. */
export function runCredentialFromConfig(config: Record<string, unknown> | null | undefined): RunCredentialDraft {
  const raw = config?.run_credential;
  const value = raw && typeof raw === 'object' && !Array.isArray(raw) ? (raw as Record<string, unknown>) : {};
  return {
    enabled: value.enabled === true,
    header: typeof value.header === 'string' ? value.header : '',
    prefix: typeof value.prefix === 'string' ? value.prefix : '',
    required: value.required === true,
  };
}

/** `config` with run_credential set from the draft (off and empty: no key). */
export function configWithRunCredential(
  config: Record<string, unknown> | null | undefined,
  draft: RunCredentialDraft,
): Record<string, unknown> {
  const { run_credential: _old, ...rest } = config ?? {};
  void _old;
  if (!draft.enabled && !draft.header && !draft.prefix && !draft.required) return rest;
  return {
    ...rest,
    run_credential: {
      enabled: draft.enabled,
      header: draft.header.trim() || null,
      prefix: draft.prefix,
      required: draft.required,
    },
  };
}

/** The draft's first problem for this provider type, or null. */
export function runCredentialProblem(draft: RunCredentialDraft, providerType: string): string | null {
  const header = draft.header.trim();
  if (draft.required && !draft.enabled) return 'Required needs the run credential enabled';
  if (header) {
    if (!HEADER_NAME.test(header)) return 'The header name may hold letters, digits and -';
    const lowered = header.toLowerCase();
    if (FORBIDDEN.has(lowered) || FORBIDDEN_PREFIXES.some((p) => lowered.startsWith(p))) {
      return `${header} can't carry a credential (framing, trace context or an SDK/Sinas header)`;
    }
  } else if (draft.prefix) {
    return 'A prefix applies to a named header only';
  } else if (draft.enabled && !NATIVE_TYPES.has(providerType.trim().toLowerCase())) {
    return `Provider type ${providerType || '—'} has no key of its own to replace: name a header`;
  }
  if (draft.prefix.length > 64 || !PRINTABLE.test(draft.prefix)) return 'The prefix must be printable ASCII, at most 64 characters';
  return null;
}
