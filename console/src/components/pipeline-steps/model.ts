/**
 * Pipeline step editing model — pure logic, no React, no app imports.
 *
 * PORTABILITY CONTRACT: this module (and PipelineStepsEditor.tsx next to it)
 * are designed to be lifted verbatim into other Sinas apps (Studio, @sinas/ui).
 * They depend only on the step JSON shape defined by the backend
 * (backend/app/services/pipeline_validation.py) — keep it that way.
 *
 * Step shape reminders:
 * - keys ending in `.$` hold JMESPath expressions over
 *   {input, steps.<name>.output, cursor, run};
 * - a step's `input` is either a literal template object (whose own keys may
 *   use `.$`) or a whole-input expression under `input.$`;
 * - unknown keys must be preserved round-trip (the backend validates; the
 *   editor never silently drops what it doesn't model).
 */

export type StepType = 'connector' | 'function' | 'agent' | 'query' | 'load';

export type Step = { name: string; type: StepType } & Record<string, any>;

export interface StepTypeInfo {
  type: StepType;
  label: string;
  hint: string;
}

export const STEP_TYPES: StepTypeInfo[] = [
  { type: 'connector', label: 'Connector', hint: 'Call a connector operation (platform auth, retries)' },
  { type: 'function', label: 'Function', hint: 'Run a function in the function runtime' },
  { type: 'agent', label: 'Agent', hint: 'Invoke an agent; structured output when it has an outputSchema' },
  { type: 'query', label: 'Query', hint: 'Run a saved SQL query' },
  { type: 'load', label: 'Load', hint: 'Upsert items into a database table (idempotent sink)' },
];

/** Step-level keys the visual editor models per type; anything else is shown
 * as preserved "extra" keys and only editable in per-step JSON mode. */
const MODELED_KEYS: Record<StepType, string[]> = {
  connector: ['name', 'type', 'connector', 'operation', 'input', 'input.$', 'cursor', 'retry', 'allowStatuses'],
  function: ['name', 'type', 'function', 'input', 'input.$', 'cursor', 'retry'],
  agent: ['name', 'type', 'agent', 'input', 'input.$', 'message', 'message.$'],
  query: ['name', 'type', 'query', 'input', 'input.$', 'retry'],
  load: ['name', 'type', 'connection', 'table', 'primaryKey.$', 'items.$', 'retry'],
};

export const STEP_NAME_RE = /^[a-zA-Z_][a-zA-Z0-9_-]*$/;
export const REF_RE = /^[a-zA-Z_][a-zA-Z0-9_-]*\/[a-zA-Z_][a-zA-Z0-9_-]*$/;

export function extraKeys(step: Step): string[] {
  const modeled = MODELED_KEYS[step.type] ?? ['name', 'type'];
  return Object.keys(step).filter((k) => !modeled.includes(k));
}

// ---------------------------------------------------------------------------
// Step creation / list operations (all return new arrays/objects — no mutation)
// ---------------------------------------------------------------------------

export function uniqueStepName(base: string, existing: string[]): string {
  if (!existing.includes(base)) return base;
  let i = 2;
  while (existing.includes(`${base}${i}`)) i++;
  return `${base}${i}`;
}

export function newStep(type: StepType, existingNames: string[]): Step {
  const name = uniqueStepName(type === 'connector' ? 'fetch' : type, existingNames);
  switch (type) {
    case 'connector':
      return { name, type, connector: '', operation: '', input: {} };
    case 'function':
      return { name, type, function: '', input: {} };
    case 'agent':
      return { name, type, agent: '', input: {} };
    case 'query':
      return { name, type, query: '', input: {} };
    case 'load':
      return { name, type, connection: '', table: '', 'items.$': '', 'primaryKey.$': 'item.id' };
  }
}

export function moveStep(steps: Step[], index: number, delta: -1 | 1): Step[] {
  const target = index + delta;
  if (target < 0 || target >= steps.length) return steps;
  const next = [...steps];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

export function removeStep(steps: Step[], index: number): Step[] {
  return steps.filter((_, i) => i !== index);
}

export function duplicateStep(steps: Step[], index: number): Step[] {
  const copy: Step = JSON.parse(JSON.stringify(steps[index]));
  copy.name = uniqueStepName(copy.name, steps.map((s) => s.name));
  const next = [...steps];
  next.splice(index + 1, 0, copy);
  return next;
}

export function updateStep(steps: Step[], index: number, patch: Partial<Step>): Step[] {
  const next = [...steps];
  next[index] = { ...next[index], ...patch };
  return next;
}

/** Replace a step wholesale (used by per-step JSON mode). */
export function replaceStep(steps: Step[], index: number, step: Step): Step[] {
  const next = [...steps];
  next[index] = step;
  return next;
}

/** Set or delete a key on a step; deletes when value is undefined. */
export function setStepKey(steps: Step[], index: number, key: string, value: any): Step[] {
  const next = [...steps];
  const step: Step = { ...next[index] };
  if (value === undefined) delete step[key];
  else step[key] = value;
  next[index] = step;
  return next;
}

// ---------------------------------------------------------------------------
// Input mapping: fields ⇄ expression ⇄ raw JSON
// ---------------------------------------------------------------------------

export type InputMode = 'fields' | 'expression' | 'json';

export interface MappingRow {
  key: string;
  /** Displayed value: the expression text, or the literal (strings raw,
   * everything else JSON-encoded). */
  value: string;
  kind: 'expr' | 'literal';
}

function isScalar(v: any): boolean {
  return v === null || ['string', 'number', 'boolean'].includes(typeof v);
}

/** Which editing mode a step's current input supports without loss. */
export function deriveInputMode(step: Step): InputMode {
  if ('input.$' in step) return 'expression';
  const input = step.input;
  if (input === undefined || input === null) return 'fields';
  if (typeof input !== 'object' || Array.isArray(input)) return 'json';
  // Flat object of scalars (expression values are strings by definition)
  return Object.values(input).every(isScalar) ? 'fields' : 'json';
}

export function inputToRows(input: Record<string, any> | undefined): MappingRow[] {
  if (!input) return [];
  return Object.entries(input).map(([key, value]) => {
    if (key.endsWith('.$')) {
      return { key: key.slice(0, -2), value: String(value), kind: 'expr' as const };
    }
    return {
      key,
      value: typeof value === 'string' ? value : JSON.stringify(value),
      kind: 'literal' as const,
    };
  });
}

/** Literal row values round-trip as JSON when they parse as JSON *and* aren't
 * plain words — `42`, `true`, `{"a":1}` become typed values; `hello` stays a
 * string. A leading `=` forces string (rare escape hatch, documented in UI). */
export function parseLiteral(text: string): any {
  if (text.startsWith('=')) return text.slice(1);
  const trimmed = text.trim();
  if (trimmed === '') return '';
  if (/^(-?\d+(\.\d+)?([eE][+-]?\d+)?|true|false|null|[[{"].*)$/s.test(trimmed)) {
    try {
      return JSON.parse(trimmed);
    } catch {
      return text;
    }
  }
  return text;
}

export function literalTypeHint(text: string): string | null {
  const parsed = parseLiteral(text);
  if (typeof parsed === 'string') return null;
  if (parsed === null) return 'null';
  if (Array.isArray(parsed)) return 'array';
  return typeof parsed; // number | boolean | object
}

export function rowsToInput(rows: MappingRow[]): Record<string, any> {
  const input: Record<string, any> = {};
  for (const row of rows) {
    if (!row.key.trim()) continue;
    if (row.kind === 'expr') input[`${row.key}.$`] = row.value;
    else input[row.key] = parseLiteral(row.value);
  }
  return input;
}

// ---------------------------------------------------------------------------
// Expression context help
// ---------------------------------------------------------------------------

/** JMESPath roots available to a step at `index`, for hint UIs. */
export function expressionRoots(steps: Step[], index: number): string[] {
  const roots = ['input', 'cursor', 'run'];
  const prior = steps.slice(0, index).map((s) => `steps.${s.name}.output`);
  return [...prior, ...roots];
}

// ---------------------------------------------------------------------------
// Light client-side validation (server stays authoritative)
// ---------------------------------------------------------------------------

export interface StepIssue {
  index: number;
  message: string;
}

const REF_FIELD: Partial<Record<StepType, string>> = {
  connector: 'connector',
  function: 'function',
  agent: 'agent',
  query: 'query',
};

export function validateSteps(steps: Step[]): StepIssue[] {
  const issues: StepIssue[] = [];
  const names = steps.map((s) => s.name);
  steps.forEach((step, i) => {
    if (!STEP_NAME_RE.test(step.name || '')) {
      issues.push({ index: i, message: 'Step name must match [a-zA-Z_][a-zA-Z0-9_-]*' });
    } else if (names.indexOf(step.name) !== i) {
      issues.push({ index: i, message: `Duplicate step name '${step.name}'` });
    }
    const refField = REF_FIELD[step.type];
    if (refField && !REF_RE.test(step[refField] || '')) {
      issues.push({ index: i, message: `${refField} must be a namespace/name reference` });
    }
    if (step.type === 'connector' && !step.operation) {
      issues.push({ index: i, message: 'operation is required' });
    }
    if (step.type === 'load') {
      if (!step.connection) issues.push({ index: i, message: 'connection is required' });
      if (!step.table) issues.push({ index: i, message: 'table is required' });
      if (!step['items.$']) issues.push({ index: i, message: 'items expression is required' });
      if (!step['primaryKey.$']) issues.push({ index: i, message: 'primaryKey expression is required' });
    }
    const cursorSteps = steps.filter((s) => s.cursor);
    if (cursorSteps.length > 1 && step.cursor && cursorSteps[0] !== step) {
      issues.push({ index: i, message: 'Only one step may declare cursor config' });
    }
  });
  return issues;
}

/** One-line summary for a collapsed step card. */
export function stepSummary(step: Step): string {
  switch (step.type) {
    case 'connector':
      return [step.connector, step.operation].filter(Boolean).join(' · ') || 'not configured';
    case 'function':
      return step.function || 'not configured';
    case 'agent':
      return step.agent || 'not configured';
    case 'query':
      return step.query || 'not configured';
    case 'load':
      return step.connection && step.table ? `${step.connection} → ${step.table}` : 'not configured';
    default:
      return '';
  }
}
