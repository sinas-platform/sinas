/**
 * A failed agent turn as the console shows it: a stream `error` event's
 * message, and for the error codes whose message doesn't already say what
 * to do, a short hint (the error envelope, backend app/providers/errors.py;
 * docs-mint/platform/errors.mdx).
 */

/** A stream `error` event's data. */
export interface StreamErrorPayload {
  error?: string;
  detail?: string;
  code?: string;
  retryable?: boolean;
  upstream_status?: number;
  retry_after?: number;
}

const HINTS: Record<string, string> = {
  rate_limited: 'The model provider is rate limiting requests; try again in a moment.',
  provider_unavailable: 'The model provider is unavailable or overloaded; try again in a moment.',
  provider_timeout: 'The model provider did not answer in time; try again.',
  budget_exceeded: 'A spending budget or quota is exhausted; an admin can raise it.',
  model_not_found: "An admin can check the agent's model and the provider's endpoint.",
  context_length_exceeded: 'The conversation is too long for the model; start a new chat.',
  content_filtered: "The provider's content filter blocked this message.",
  job_timeout: 'The agent ran out of time for this message.',
  provider_credential_required:
    "The model provider needs this chat's own credential; the application that started the chat has to send one.",
};

/** The hint for an error code, if it has one. */
export function errorHint(code: string | undefined | null): string | null {
  return (code && HINTS[code]) || null;
}

/** The text to show for a stream `error` event's data (a JSON envelope, or
 * plain text from an older backend). */
export function streamErrorMessage(data: string | undefined | null): string {
  let payload: StreamErrorPayload | null = null;
  try {
    const parsed: unknown = JSON.parse(data ?? '');
    if (parsed && typeof parsed === 'object') payload = parsed as StreamErrorPayload;
  } catch {
    // not JSON: the text itself
  }
  if (!payload) return data || 'Stream error';
  const message = payload.error || payload.detail || 'Stream error';
  const hint = errorHint(payload.code);
  return hint && !message.includes(hint) ? `${message}\n\n${hint}` : message;
}
