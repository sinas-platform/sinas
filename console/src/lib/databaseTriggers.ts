// Database Triggers poll through the CDC worker, which a deployment may turn
// off (CDC_ENABLED=false, or `features.cdc: false` in the Helm chart). `/info`
// reports it as `features.cdc`. CDC-off is an overlay on what's stored, never
// a write: a trigger keeps its own is_active, so turning CDC back on resumes
// exactly the ones that were on. Pure, so `npm test` covers it.

export const CDC_OFF_REASON = 'CDC is disabled on this deployment (CDC_ENABLED=false)';

type InfoLike = { features?: Record<string, boolean> | null } | null | undefined;

/** Only an explicit `false` disables: a missing flag (older backend, /info
 * still loading or unreachable) counts as enabled, as for every feature. */
export function cdcEnabled(info: InfoLike): boolean {
  return info?.features?.cdc !== false;
}

export type TriggerStatus =
  /** Stored as on, but CDC is off: nobody switched it off, and Resume
   * wouldn't help. Must not look like 'inactive'. */
  | 'cdc_off'
  | 'error'
  | 'active'
  /** Someone paused it; Resume switches it back on. */
  | 'inactive';

type TriggerLike = { is_active: boolean; error_message?: string | null };

export function triggerStatus(trigger: TriggerLike, cdc: boolean): TriggerStatus {
  if (!cdc && trigger.is_active) return 'cdc_off';
  if (trigger.error_message) return 'error';
  return trigger.is_active ? 'active' : 'inactive';
}

/** Pausing always works; switching on is what CDC-off refuses (the API
 * answers 403 too). */
export function canToggle(trigger: TriggerLike, cdc: boolean): boolean {
  return trigger.is_active || cdc;
}
