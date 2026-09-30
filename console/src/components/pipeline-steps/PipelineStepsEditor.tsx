/**
 * PipelineStepsEditor — ergonomic editor for pipeline step sequences.
 *
 * PORTABILITY CONTRACT (shared with model.ts): prop-driven and app-agnostic.
 * No API clients, no router, no app types — resource suggestions come in via
 * props; edited steps go out via onChange. Styling is semantic `pse-*` class
 * names only; each app ships its own stylesheet (console: steps-editor.css).
 * Dependencies: react, lucide-react.
 *
 * Ergonomics:
 * - one card per step with type-specific fields and resource suggestions;
 * - mapping fields with a per-row literal ⇄ JMESPath toggle (`.$` handled
 *   automatically), whole-input expression mode, and a lossless raw-JSON
 *   fallback per step (unknown keys are preserved, never dropped);
 * - cursor / retry sections where the backend allows them;
 * - expression hints listing the roots available at that position.
 */
import { useMemo, useState } from 'react';
import {
  ArrowDown, ArrowUp, Braces, ChevronDown, ChevronRight, Copy, Plus,
  SigmaSquare, Trash2, Type as TypeIcon,
} from 'lucide-react';
import type { MappingRow, Step, StepType } from './model';
import {
  STEP_TYPES, deriveInputMode, duplicateStep, expressionRoots, extraKeys,
  inputToRows, literalTypeHint, moveStep, newStep, removeStep, replaceStep,
  rowsToInput, setStepKey, stepSummary, updateStep, validateSteps,
} from './model';
import './steps-editor.css';

export interface ConnectorResource {
  ref: string; // namespace/name
  operations: string[];
}

export interface StepResources {
  connectors?: ConnectorResource[];
  functions?: string[];
  agents?: string[];
  queries?: string[];
  connections?: string[]; // database connection names
}

export interface PipelineStepsEditorProps {
  steps: Step[];
  onChange: (steps: Step[]) => void;
  resources?: StepResources;
}

const CUSTOM_SENTINEL = '\u0000custom';

/** Resource picker: a real dropdown when we know the options (datalist was
 * invisible-until-you-type and unreliable in Safari — users thought they had
 * to hand-type refs), with a Custom… escape and free text as the fallback,
 * so missing resources never block editing (install order, cross-package
 * refs). A current value that is not in the catalog stays selectable. */
function SuggestInput({
  value, onChange, options, placeholder, mono = true, invalid = false,
}: {
  value: string;
  onChange: (v: string) => void;
  options?: string[];
  placeholder?: string;
  mono?: boolean;
  invalid?: boolean;
}) {
  const [customMode, setCustomMode] = useState(false);
  const cls = `pse-input${mono ? ' pse-mono' : ''}${invalid ? ' pse-invalid' : ''}`;

  if (!options?.length || customMode) {
    return (
      <input
        className={cls}
        value={value}
        placeholder={placeholder}
        autoFocus={customMode}
        onChange={(e) => onChange(e.target.value)}
        onBlur={() => {
          // Re-offer the dropdown once the custom value matches a known ref
          if (options?.includes(value)) setCustomMode(false);
        }}
      />
    );
  }

  const known = options.includes(value);
  return (
    <select
      className={cls}
      value={known || value === '' ? value : value}
      onChange={(e) => {
        if (e.target.value === CUSTOM_SENTINEL) {
          setCustomMode(true);
          return;
        }
        onChange(e.target.value);
      }}
    >
      <option value="">{placeholder || 'Select…'}</option>
      {!known && value !== '' && <option value={value}>{value} (not found)</option>}
      {options.map((o) => (
        <option key={o} value={o}>{o}</option>
      ))}
      <option value={CUSTOM_SENTINEL}>Custom…</option>
    </select>
  );
}

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label className="pse-field">
      <span className="pse-label">{label}</span>
      {children}
      {hint && <span className="pse-hint">{hint}</span>}
    </label>
  );
}

/** Per-row mapping editor: key, literal/expression toggle, value. */
function MappingRowsEditor({
  rows, onRows, roots,
}: {
  rows: MappingRow[];
  onRows: (rows: MappingRow[]) => void;
  roots: string[];
}) {
  const setRow = (i: number, patch: Partial<MappingRow>) => {
    const next = rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r));
    onRows(next);
  };
  return (
    <div className="pse-mapping">
      {rows.map((row, i) => {
        const hint = row.kind === 'literal' ? literalTypeHint(row.value) : null;
        return (
          <div key={i} className="pse-mapping-row">
            <input
              className="pse-input pse-mono pse-mapping-key"
              value={row.key}
              placeholder="param"
              onChange={(e) => setRow(i, { key: e.target.value })}
            />
            <button
              type="button"
              className={`pse-kind-toggle${row.kind === 'expr' ? ' pse-kind-expr' : ''}`}
              title={row.kind === 'expr'
                ? 'JMESPath expression (click for literal value)'
                : 'Literal value (click for JMESPath expression)'}
              onClick={() => setRow(i, { kind: row.kind === 'expr' ? 'literal' : 'expr' })}
            >
              {row.kind === 'expr' ? <SigmaSquare size={13} /> : <TypeIcon size={13} />}
            </button>
            <div className="pse-mapping-value">
              <SuggestInput
                value={row.value}
                onChange={(v) => setRow(i, { value: v })}
                options={row.kind === 'expr' ? roots : undefined}
                placeholder={row.kind === 'expr' ? 'steps.fetch.output.body' : 'value'}
              />
              {hint && <span className="pse-type-badge">{hint}</span>}
            </div>
            <button
              type="button"
              className="pse-icon-btn"
              title="Remove"
              onClick={() => onRows(rows.filter((_, idx) => idx !== i))}
            >
              <Trash2 size={13} />
            </button>
          </div>
        );
      })}
      <button
        type="button"
        className="pse-add-row"
        onClick={() => onRows([...rows, { key: '', value: '', kind: 'literal' }])}
      >
        <Plus size={13} /> Add field
      </button>
    </div>
  );
}

/** The step `input` editor with its three modes. */
function InputEditor({
  step, index, steps, onSteps,
}: {
  step: Step;
  index: number;
  steps: Step[];
  onSteps: (steps: Step[]) => void;
}) {
  // The mode is DERIVED from the step's actual shape (reorder/raw-edit safe);
  // the only sticky UI state is "show me the JSON anyway".
  const naturalMode = deriveInputMode(step);
  const [jsonOverride, setJsonOverride] = useState(false);
  // Mode switches STASH the value being left behind instead of destroying
  // it (the spec forbids a step carrying both `input` and `input.$`, so the
  // memory lives here in UI state): Expression -> Fields -> Expression
  // round-trips the exact expression, and vice versa.
  const [stashedExpr, setStashedExpr] = useState<string | null>(null);
  const [stashedFields, setStashedFields] = useState<Record<string, any> | null>(null);
  const [jsonText, setJsonText] = useState('');
  const [jsonError, setJsonError] = useState<string | null>(null);
  const effectiveMode: 'fields' | 'expression' | 'json' =
    jsonOverride || naturalMode === 'json' ? 'json' : naturalMode;
  const roots = expressionRoots(steps, index);

  const switchMode = (next: 'fields' | 'expression' | 'json') => {
    setJsonError(null);
    if (next === 'json') {
      setJsonText(JSON.stringify(step['input.$'] !== undefined ? { 'input.$': step['input.$'] } : step.input ?? {}, null, 2));
      setJsonOverride(true);
      return;
    }
    setJsonOverride(false);
    if (next === 'expression' && step['input.$'] === undefined) {
      if (step.input && Object.keys(step.input).length) setStashedFields(step.input);
      let s = setStepKey(steps, index, 'input', undefined);
      s = setStepKey(s, index, 'input.$', stashedExpr ?? '');
      onSteps(s);
    } else if (next === 'fields' && step['input.$'] !== undefined) {
      if (step['input.$']) setStashedExpr(step['input.$']);
      let s = setStepKey(steps, index, 'input.$', undefined);
      s = setStepKey(s, index, 'input', stashedFields ?? {});
      onSteps(s);
    }
  };

  const applyJson = (text: string) => {
    setJsonText(text);
    try {
      const parsed = JSON.parse(text);
      setJsonError(null);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed) && 'input.$' in parsed) {
        let s = setStepKey(steps, index, 'input', undefined);
        s = setStepKey(s, index, 'input.$', parsed['input.$']);
        onSteps(s);
      } else {
        let s = setStepKey(steps, index, 'input.$', undefined);
        s = setStepKey(s, index, 'input', parsed);
        onSteps(s);
      }
    } catch (e: any) {
      setJsonError(e.message);
    }
  };

  return (
    <div className="pse-section">
      <div className="pse-section-head">
        <span className="pse-section-title">Input</span>
        <div className="pse-mode-tabs">
          {(['fields', 'expression', 'json'] as const).map((m) => (
            <button
              key={m}
              type="button"
              className={`pse-mode-tab${effectiveMode === m ? ' pse-active' : ''}`}
              onClick={() => switchMode(m)}
            >
              {m === 'fields' ? 'Fields' : m === 'expression' ? 'Expression' : 'JSON'}
            </button>
          ))}
        </div>
      </div>

      {effectiveMode === 'fields' && (
        <MappingRowsEditor
          rows={inputToRows(step.input)}
          onRows={(rows) => onSteps(setStepKey(steps, index, 'input', rowsToInput(rows)))}
          roots={roots}
        />
      )}

      {effectiveMode === 'expression' && (
        <Field label="Whole input as JMESPath" hint={`Roots: ${roots.join(', ')}`}>
          <SuggestInput
            value={step['input.$'] ?? ''}
            onChange={(v) => onSteps(setStepKey(steps, index, 'input.$', v))}
            options={roots}
            placeholder="steps.fetch.output.body"
          />
        </Field>
      )}

      {effectiveMode === 'json' && (
        <div className="pse-field">
          <textarea
            className={`pse-input pse-mono pse-json${jsonError ? ' pse-invalid' : ''}`}
            rows={6}
            value={jsonText || JSON.stringify(step['input.$'] !== undefined ? { 'input.$': step['input.$'] } : step.input ?? {}, null, 2)}
            onChange={(e) => applyJson(e.target.value)}
            spellCheck={false}
          />
          {jsonError && <span className="pse-error">{jsonError}</span>}
          <span className="pse-hint">Nested templates: keys ending in `.$` are JMESPath. Fields view is available for flat objects.</span>
        </div>
      )}
    </div>
  );
}

function CursorEditor({
  step, index, steps, onSteps,
}: {
  step: Step;
  index: number;
  steps: Step[];
  onSteps: (steps: Step[]) => void;
}) {
  const cursor = step.cursor;
  const otherHasCursor = steps.some((s, i) => i !== index && s.cursor);
  return (
    <div className="pse-section">
      <div className="pse-section-head">
        <label className="pse-check">
          <input
            type="checkbox"
            checked={!!cursor}
            disabled={!cursor && otherHasCursor}
            onChange={(e) =>
              onSteps(setStepKey(steps, index, 'cursor', e.target.checked ? { param: '', path: '' } : undefined))
            }
          />
          <span className="pse-section-title">Cursor</span>
        </label>
        {!cursor && otherHasCursor && <span className="pse-hint">another step already owns the cursor</span>}
      </div>
      {cursor && (
        <div className="pse-grid-3">
          <Field label="Inject as param">
            <input
              className="pse-input pse-mono"
              value={cursor.param ?? ''}
              placeholder="startHistoryId"
              onChange={(e) => onSteps(setStepKey(steps, index, 'cursor', { ...cursor, param: e.target.value }))}
            />
          </Field>
          <Field label="Read new mark from (JMESPath)">
            <input
              className="pse-input pse-mono"
              value={cursor.path ?? ''}
              placeholder="body.historyId"
              onChange={(e) => onSteps(setStepKey(steps, index, 'cursor', { ...cursor, path: e.target.value }))}
            />
          </Field>
          <Field label="Initial value (optional)">
            <input
              className="pse-input pse-mono"
              value={cursor.initial ?? ''}
              placeholder="empty = omit param on first run"
              onChange={(e) => {
                const next = { ...cursor };
                if (e.target.value === '') delete next.initial;
                else next.initial = e.target.value;
                onSteps(setStepKey(steps, index, 'cursor', next));
              }}
            />
          </Field>
        </div>
      )}
    </div>
  );
}

function RetryEditor({
  step, index, steps, onSteps,
}: {
  step: Step;
  index: number;
  steps: Step[];
  onSteps: (steps: Step[]) => void;
}) {
  const retry = step.retry ?? {};
  const attempts = retry.maxAttempts ?? 1;
  return (
    <div className="pse-inline-fields">
      <Field label="Retry attempts">
        <input
          className="pse-input pse-narrow"
          type="number"
          min={1}
          max={10}
          value={attempts}
          onChange={(e) => {
            const v = parseInt(e.target.value || '1', 10);
            onSteps(setStepKey(steps, index, 'retry', v > 1 ? { ...retry, maxAttempts: v } : undefined));
          }}
        />
      </Field>
      {attempts > 1 && (
        <Field label="Backoff">
          <select
            className="pse-input"
            value={retry.backoff ?? 'none'}
            onChange={(e) => onSteps(setStepKey(steps, index, 'retry', { ...retry, backoff: e.target.value }))}
          >
            <option value="none">none</option>
            <option value="linear">linear</option>
            <option value="exponential">exponential</option>
          </select>
        </Field>
      )}
    </div>
  );
}

function StepBody({
  step, index, steps, onSteps, resources,
}: {
  step: Step;
  index: number;
  steps: Step[];
  onSteps: (steps: Step[]) => void;
  resources?: StepResources;
}) {
  const roots = expressionRoots(steps, index);
  const connectorRefs = resources?.connectors?.map((c) => c.ref);
  const operations = useMemo(
    () => resources?.connectors?.find((c) => c.ref === step.connector)?.operations,
    [resources, step.connector]
  );

  return (
    <div className="pse-body">
      {step.type === 'connector' && (
        <>
          <div className="pse-grid-2">
            <Field label="Connector">
              <SuggestInput
                value={step.connector ?? ''}
                onChange={(v) => onSteps(updateStep(steps, index, { connector: v }))}
                options={connectorRefs}
                placeholder="google/gmail"
              />
            </Field>
            <Field label="Operation">
              <SuggestInput
                value={step.operation ?? ''}
                onChange={(v) => onSteps(updateStep(steps, index, { operation: v }))}
                options={operations}
                placeholder="list-history"
              />
            </Field>
          </div>
          <InputEditor step={step} index={index} steps={steps} onSteps={onSteps} />
          <CursorEditor step={step} index={index} steps={steps} onSteps={onSteps} />
          <div className="pse-inline-fields">
            <RetryEditor step={step} index={index} steps={steps} onSteps={onSteps} />
            <Field label="Also accept statuses" hint="comma-separated, e.g. 404">
              <input
                className="pse-input pse-narrow pse-mono"
                value={(step.allowStatuses ?? []).join(', ')}
                onChange={(e) => {
                  const statuses = e.target.value
                    .split(',')
                    .map((s) => parseInt(s.trim(), 10))
                    .filter((n) => !Number.isNaN(n));
                  onSteps(setStepKey(steps, index, 'allowStatuses', statuses.length ? statuses : undefined));
                }}
              />
            </Field>
          </div>
        </>
      )}

      {step.type === 'function' && (
        <>
          <Field label="Function">
            <SuggestInput
              value={step.function ?? ''}
              onChange={(v) => onSteps(updateStep(steps, index, { function: v }))}
              options={resources?.functions}
              placeholder="gmail/extract-messages"
            />
          </Field>
          <InputEditor step={step} index={index} steps={steps} onSteps={onSteps} />
          <CursorEditor step={step} index={index} steps={steps} onSteps={onSteps} />
          <RetryEditor step={step} index={index} steps={steps} onSteps={onSteps} />
        </>
      )}

      {step.type === 'agent' && (
        <>
          <Field label="Agent" hint="Structured output requires the agent to declare an outputSchema; the reply is validated against it. Agent steps are never auto-retried.">
            <SuggestInput
              value={step.agent ?? ''}
              onChange={(v) => onSteps(updateStep(steps, index, { agent: v }))}
              options={resources?.agents}
              placeholder="gmail/triage"
            />
          </Field>
          <InputEditor step={step} index={index} steps={steps} onSteps={onSteps} />
          <div className="pse-section">
            <div className="pse-section-head">
              <span className="pse-section-title">Message</span>
              <button
                type="button"
                className={`pse-kind-toggle${'message.$' in step ? ' pse-kind-expr' : ''}`}
                title={'message.$' in step ? 'JMESPath expression (click for literal)' : 'Literal text (click for JMESPath)'}
                onClick={() => {
                  if ('message.$' in step) {
                    let s = setStepKey(steps, index, 'message.$', undefined);
                    onSteps(setStepKey(s, index, 'message', step['message.$'] ?? ''));
                  } else {
                    let s = setStepKey(steps, index, 'message', undefined);
                    onSteps(setStepKey(s, index, 'message.$', step.message ?? ''));
                  }
                }}
              >
                {'message.$' in step ? <SigmaSquare size={13} /> : <TypeIcon size={13} />}
              </button>
            </div>
            {'message.$' in step ? (
              <SuggestInput
                value={step['message.$'] ?? ''}
                onChange={(v) => onSteps(setStepKey(steps, index, 'message.$', v))}
                options={roots}
                placeholder="steps.extract.output.summary"
              />
            ) : (
              <textarea
                className="pse-input"
                rows={2}
                value={step.message ?? ''}
                placeholder="Empty = the JSON-serialized input is sent as the message"
                onChange={(e) =>
                  onSteps(setStepKey(steps, index, 'message', e.target.value === '' ? undefined : e.target.value))
                }
              />
            )}
          </div>
        </>
      )}

      {step.type === 'query' && (
        <>
          <Field label="Query">
            <SuggestInput
              value={step.query ?? ''}
              onChange={(v) => onSteps(updateStep(steps, index, { query: v }))}
              options={resources?.queries}
              placeholder="search/similar-docs"
            />
          </Field>
          <InputEditor step={step} index={index} steps={steps} onSteps={onSteps} />
          <RetryEditor step={step} index={index} steps={steps} onSteps={onSteps} />
        </>
      )}

      {step.type === 'load' && (
        <>
          <div className="pse-grid-2">
            <Field label="Database connection">
              <SuggestInput
                value={step.connection ?? ''}
                onChange={(v) => onSteps(updateStep(steps, index, { connection: v }))}
                options={resources?.connections}
                placeholder="reporting"
              />
            </Field>
            <Field label="Table" hint="auto-created on first run (pk + jsonb payload)">
              <input
                className="pse-input pse-mono"
                value={step.table ?? ''}
                placeholder="jira_worklogs"
                onChange={(e) => onSteps(updateStep(steps, index, { table: e.target.value }))}
              />
            </Field>
          </div>
          <div className="pse-grid-2">
            <Field label="Items (JMESPath)" hint="array to upsert, one row per item">
              <SuggestInput
                value={step['items.$'] ?? ''}
                onChange={(v) => onSteps(setStepKey(steps, index, 'items.$', v))}
                options={roots}
                placeholder="steps.extract.output.worklogs"
              />
            </Field>
            <Field label="Primary key (JMESPath over each item)" hint="`item` is bound per item">
              <input
                className="pse-input pse-mono"
                value={step['primaryKey.$'] ?? ''}
                placeholder="item.id"
                onChange={(e) => onSteps(setStepKey(steps, index, 'primaryKey.$', e.target.value))}
              />
            </Field>
          </div>
          <RetryEditor step={step} index={index} steps={steps} onSteps={onSteps} />
        </>
      )}
    </div>
  );
}

function StepCard({
  step, index, steps, onSteps, resources, issues,
}: {
  step: Step;
  index: number;
  steps: Step[];
  onSteps: (steps: Step[]) => void;
  resources?: StepResources;
  issues: string[];
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [rawMode, setRawMode] = useState(false);
  const [rawText, setRawText] = useState('');
  const [rawError, setRawError] = useState<string | null>(null);
  const extras = extraKeys(step);

  const enterRaw = () => {
    setRawText(JSON.stringify(step, null, 2));
    setRawError(null);
    setRawMode(true);
  };

  const applyRaw = (text: string) => {
    setRawText(text);
    try {
      const parsed = JSON.parse(text);
      setRawError(null);
      onSteps(replaceStep(steps, index, parsed));
    } catch (e: any) {
      setRawError(e.message);
    }
  };

  return (
    <div className={`pse-step${issues.length ? ' pse-step-invalid' : ''}`}>
      <div className="pse-step-head">
        <button type="button" className="pse-icon-btn" onClick={() => setCollapsed(!collapsed)}>
          {collapsed ? <ChevronRight size={15} /> : <ChevronDown size={15} />}
        </button>
        <span className="pse-step-index">{index + 1}</span>
        <select
          className="pse-input pse-type-select"
          value={step.type}
          onChange={(e) => {
            // Type change rebuilds the step but keeps its name.
            const fresh = newStep(e.target.value as StepType, steps.filter((_, i) => i !== index).map((s) => s.name));
            fresh.name = step.name;
            onSteps(replaceStep(steps, index, fresh));
          }}
        >
          {STEP_TYPES.map((t) => (
            <option key={t.type} value={t.type}>{t.label}</option>
          ))}
        </select>
        <input
          className="pse-input pse-mono pse-step-name"
          value={step.name}
          onChange={(e) => onSteps(updateStep(steps, index, { name: e.target.value }))}
        />
        {collapsed && <span className="pse-step-summary">{stepSummary(step)}</span>}
        {extras.length > 0 && !rawMode && (
          <span className="pse-extra-badge" title={`Keys only editable in JSON view: ${extras.join(', ')}`}>
            +{extras.length} raw
          </span>
        )}
        <div className="pse-step-actions">
          <button
            type="button"
            className={`pse-icon-btn${rawMode ? ' pse-active' : ''}`}
            title={rawMode ? 'Back to form view' : 'Edit this step as JSON'}
            onClick={() => (rawMode ? setRawMode(false) : enterRaw())}
          >
            <Braces size={14} />
          </button>
          <button type="button" className="pse-icon-btn" title="Move up" disabled={index === 0}
            onClick={() => onSteps(moveStep(steps, index, -1))}>
            <ArrowUp size={14} />
          </button>
          <button type="button" className="pse-icon-btn" title="Move down" disabled={index === steps.length - 1}
            onClick={() => onSteps(moveStep(steps, index, 1))}>
            <ArrowDown size={14} />
          </button>
          <button type="button" className="pse-icon-btn" title="Duplicate"
            onClick={() => onSteps(duplicateStep(steps, index))}>
            <Copy size={14} />
          </button>
          <button type="button" className="pse-icon-btn pse-danger" title="Delete"
            onClick={() => onSteps(removeStep(steps, index))}>
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      {issues.length > 0 && !collapsed && (
        <div className="pse-issues">
          {issues.map((m, i) => (
            <span key={i} className="pse-error">{m}</span>
          ))}
        </div>
      )}

      {!collapsed && (rawMode ? (
        <div className="pse-body">
          <textarea
            className={`pse-input pse-mono pse-json${rawError ? ' pse-invalid' : ''}`}
            rows={10}
            value={rawText}
            onChange={(e) => applyRaw(e.target.value)}
            spellCheck={false}
          />
          {rawError && <span className="pse-error">{rawError}</span>}
        </div>
      ) : (
        <StepBody step={step} index={index} steps={steps} onSteps={onSteps} resources={resources} />
      ))}
    </div>
  );
}

export function PipelineStepsEditor({ steps, onChange, resources }: PipelineStepsEditorProps) {
  const issues = validateSteps(steps);
  const [addOpen, setAddOpen] = useState(false);

  return (
    <div className="pse">
      {steps.map((step, index) => (
        <div key={index} className="pse-step-slot">
          {index > 0 && <div className="pse-connector-line" />}
          <StepCard
            step={step}
            index={index}
            steps={steps}
            onSteps={onChange}
            resources={resources}
            issues={issues.filter((i) => i.index === index).map((i) => i.message)}
          />
        </div>
      ))}

      <div className="pse-add">
        {steps.length > 0 && <div className="pse-connector-line" />}
        {addOpen ? (
          <div className="pse-add-menu">
            {STEP_TYPES.map((t) => (
              <button
                key={t.type}
                type="button"
                className="pse-add-option"
                onClick={() => {
                  onChange([...steps, newStep(t.type, steps.map((s) => s.name))]);
                  setAddOpen(false);
                }}
              >
                <span className="pse-add-label">{t.label}</span>
                <span className="pse-hint">{t.hint}</span>
              </button>
            ))}
            <button type="button" className="pse-add-option pse-hint" onClick={() => setAddOpen(false)}>
              Cancel
            </button>
          </div>
        ) : (
          <button type="button" className="pse-add-btn" onClick={() => setAddOpen(true)}>
            <Plus size={15} /> Add step
          </button>
        )}
      </div>
    </div>
  );
}
