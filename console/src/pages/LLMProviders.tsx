import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { apiClient } from '../lib/api';
import { RATECARD_QUERY_KEY, useRatecard } from '../lib/ratecard';
import { AlertTriangle, Brain, Plus, Trash2, Edit2 } from 'lucide-react';
import { useState } from 'react';
import type { LLMProvider, LLMProviderCreate, LLMProviderUpdate, ModelPricing, Ratecard } from '../types';
import { ErrorDisplay } from '../components/ErrorDisplay';
import { RatecardSources } from '../components/Ratecard';
import { ProviderHeaders } from '../components/ProviderHeaders';
import { ProviderRunCredential } from '../components/ProviderRunCredential';
import { headerProblem } from '../lib/attribution';

function plural(n: number, one: string, many: string): string {
  return n ? `${n} ${n === 1 ? one : many}` : '';
}

// The pricing form, as typed (JSON text and a number), until submit.
interface PricingDraft {
  pricing: string;
  aliases: string;
  discount: string;
  // The LiteLLM provider family to price as; '' = the provider type's own
  pricingProvider: string;
  // "Other…" chosen: the family is typed rather than picked
  otherFamily: boolean;
}

type ProviderFamily = Ratecard['provider_families'][number];

// The pricing field a fix from the overview opens the form at
type PricingFocus = 'family' | 'alias';

// A LiteLLM provider name, as the API validates it
const FAMILY_PATTERN = /^[a-z0-9][a-z0-9_-]*$/;
const OTHER_FAMILY = '__other__';

function pricingDraft(provider?: LLMProvider): PricingDraft {
  return {
    pricing: JSON.stringify(provider?.pricing || {}, null, 2),
    aliases: JSON.stringify(provider?.model_aliases || {}, null, 2),
    discount: provider?.discount != null ? String(provider.discount) : '',
    pricingProvider: provider?.pricing_provider || '',
    otherFamily: false,
  };
}

/** Whether the configuration JSON as typed is an object (not a draft mid-edit). */
function isJsonObject(text: string): boolean {
  try {
    const value = JSON.parse(text.trim() || '{}');
    return !!value && typeof value === 'object' && !Array.isArray(value);
  } catch {
    return false;
  }
}

function parseObject(text: string, what: string): Record<string, unknown> {
  let value: unknown;
  try {
    value = JSON.parse(text.trim() || '{}');
  } catch {
    throw new Error(`${what} is not valid JSON`);
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${what} must be a JSON object`);
  }
  return value as Record<string, unknown>;
}

// The API fields of a pricing draft; throws with a message on bad input.
function parsePricingDraft(draft: PricingDraft) {
  const discount = draft.discount.trim() === '' ? null : Number(draft.discount);
  if (discount !== null && (Number.isNaN(discount) || discount < 0 || discount > 1)) {
    throw new Error('Discount must be a number between 0 and 1');
  }
  const family = draft.pricingProvider.trim();
  if (family && (family.length > 64 || !FAMILY_PATTERN.test(family))) {
    throw new Error('"Price models as" must be a lowercase LiteLLM provider name, such as mistral or together_ai');
  }
  return {
    pricing: parseObject(draft.pricing, 'Model prices') as Record<string, ModelPricing>,
    model_aliases: parseObject(draft.aliases, 'Model aliases') as Record<string, string>,
    discount,
    pricing_provider: family || null,
  };
}

function PricingFields({
  idPrefix,
  draft,
  onChange,
  providerType,
  families,
  notice,
  focus,
}: {
  idPrefix: string;
  draft: PricingDraft;
  onChange: (draft: PricingDraft) => void;
  providerType: string;
  // The deployment ratecard's families; undefined when the ratecard can't be read
  families?: ProviderFamily[];
  notice?: string;
  focus?: PricingFocus;
}) {
  const family = draft.pricingProvider.trim();
  const known = !!families?.some((f) => f.family === family);
  const selected = draft.otherFamily || (family && !known) ? OTHER_FAMILY : family;
  return (
    <div className="pt-4 border-t border-line-soft space-y-4">
      <div>
        <p className="text-sm font-medium text-gray-300">Pricing</p>
        <p className="text-xs text-gray-500 mt-1">
          For cost tracking (Admin › Usage). USD per token, in LiteLLM's field names. Models not priced here
          use the deployment ratecard.
        </p>
      </div>
      {notice && (
        <div className="p-3 bg-yellow-900/20 border border-yellow-800 rounded-lg">
          <p className="text-xs text-yellow-300">{notice}</p>
        </div>
      )}
      <div>
        <label htmlFor={`${idPrefix}_pricing_provider`} className="block text-sm font-medium text-gray-300 mb-2">
          Price models as
        </label>
        <select
          id={`${idPrefix}_pricing_provider`}
          value={selected}
          autoFocus={focus === 'family'}
          onChange={(e) => {
            const value = e.target.value;
            if (value === OTHER_FAMILY) onChange({ ...draft, otherFamily: true, pricingProvider: family });
            else onChange({ ...draft, otherFamily: false, pricingProvider: value });
          }}
          className="input"
        >
          <option value="">Same as provider type ({providerType || '—'})</option>
          {families?.map((f) => (
            <option key={f.family} value={f.family}>
              {f.family} ({f.models.toLocaleString()} {f.models === 1 ? 'model' : 'models'})
            </option>
          ))}
          <option value={OTHER_FAMILY}>Other…</option>
        </select>
        {selected === OTHER_FAMILY && (
          <input
            id={`${idPrefix}_pricing_provider_other`}
            type="text"
            value={draft.pricingProvider}
            onChange={(e) => onChange({ ...draft, pricingProvider: e.target.value })}
            placeholder="e.g. together_ai"
            className="input mt-2 font-mono text-xs"
          />
        )}
        <p className="text-xs text-gray-500 mt-1">
          For an OpenAI-compatible API of another provider (Mistral, Groq, a LiteLLM proxy…): the LiteLLM provider
          family its models are priced as.
        </p>
        {family && families && !known && (
          <p className="text-xs text-yellow-400 mt-1">
            The deployment ratecard has no models for <code>{family}</code> now: models are matched by their bare
            names until it does.
          </p>
        )}
      </div>
      <div>
        <label htmlFor={`${idPrefix}_pricing`} className="block text-sm font-medium text-gray-300 mb-2">
          Model prices (JSON)
        </label>
        <textarea
          id={`${idPrefix}_pricing`}
          value={draft.pricing}
          onChange={(e) => onChange({ ...draft, pricing: e.target.value })}
          placeholder='{"gpt-4o": {"input_cost_per_token": 2.5e-6, "output_cost_per_token": 1e-5}}'
          rows={5}
          className="input resize-none font-mono text-xs"
        />
        <p className="text-xs text-gray-500 mt-1">
          Per model: <code>input_cost_per_token</code>, <code>output_cost_per_token</code>,{' '}
          <code>cache_read_input_token_cost</code>, <code>cache_creation_input_token_cost</code>, and the{' '}
          <code>_batches</code> variants.
        </p>
      </div>
      <div>
        <label htmlFor={`${idPrefix}_aliases`} className="block text-sm font-medium text-gray-300 mb-2">
          Model aliases (JSON)
        </label>
        <textarea
          id={`${idPrefix}_aliases`}
          value={draft.aliases}
          autoFocus={focus === 'alias'}
          onChange={(e) => onChange({ ...draft, aliases: e.target.value })}
          placeholder='{"my-azure-deployment": "gpt-4o"}'
          rows={3}
          className="input resize-none font-mono text-xs"
        />
        <p className="text-xs text-gray-500 mt-1">A model name as called → the model it is priced as.</p>
      </div>
      <div>
        <label htmlFor={`${idPrefix}_discount`} className="block text-sm font-medium text-gray-300 mb-2">
          Discount
        </label>
        <input
          id={`${idPrefix}_discount`}
          type="number"
          step="any"
          min="0"
          max="1"
          value={draft.discount}
          onChange={(e) => onChange({ ...draft, discount: e.target.value })}
          placeholder="None"
          className="input"
        />
        <p className="text-xs text-gray-500 mt-1">
          Multiplier on deployment-ratecard prices for negotiated rates: 0.85 is 15% off.
        </p>
      </div>
    </div>
  );
}

// The ratecard as it applies to these providers: deployment sources, each
// provider's own pricing, and the models in use that have no price.
function PricingOverview({
  ratecard,
  editable,
  onFix,
}: {
  ratecard: Ratecard;
  // Providers whose form can be opened from here
  editable: Set<string>;
  onFix: (provider: string, model: string, how: 'family' | 'alias') => void;
}) {
  const families = new Set(ratecard.provider_families.map((f) => f.family));
  const unpriced = new Map<string, Ratecard['unpriced_models']>();
  for (const m of ratecard.unpriced_models) {
    const key = m.provider || '';
    unpriced.set(key, [...(unpriced.get(key) || []), m]);
  }
  return (
    <div className="space-y-4">
      <div className="card">
        <div className="flex items-start justify-between gap-4 mb-4">
          <div>
            <h3 className="text-lg font-semibold text-gray-100">Pricing</h3>
            <p className="text-sm text-gray-400 mt-1">
              Calls are priced from the provider's own prices first, then the deployment ratecard (
              {ratecard.deployment_models.toLocaleString()} models).
            </p>
          </div>
          <Link to="/usage" className="text-sm text-primary-400 hover:text-primary-300 flex-shrink-0">
            Full price table in Admin › Usage →
          </Link>
        </div>
        <RatecardSources sources={ratecard.sources} />
      </div>

      {ratecard.providers.length > 0 && (
        <div className="bg-surface-1 rounded-lg border border-line-soft overflow-hidden">
          <div className="px-6 py-4 border-b border-line-soft">
            <h3 className="text-lg font-semibold text-gray-100">Provider pricing</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-white/[0.06]">
              <thead className="bg-surface-0">
                <tr>
                  {['Provider', 'Prices as', 'Own prices', 'Aliases', 'Discount'].map((h) => (
                    <th key={h} className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="bg-surface-1 divide-y divide-white/[0.06]">
                {ratecard.providers.map((p) => (
                  <tr key={p.name} className={p.is_active ? '' : 'opacity-60'}>
                    <td className="px-6 py-3 text-sm text-gray-100">{p.name}</td>
                    <td className="px-6 py-3 text-sm font-mono text-gray-300">
                      {p.pricing_provider ? (
                        <>
                          {p.pricing_provider}
                          {!families.has(p.pricing_provider) && (
                            <span
                              className="ml-2 inline-flex items-center text-xs font-sans text-yellow-400"
                              title="The deployment ratecard has no models for this family: models are matched by their bare names"
                            >
                              <AlertTriangle className="w-3 h-3 mr-1" />
                              not in the ratecard
                            </span>
                          )}
                        </>
                      ) : (
                        <span className="text-gray-500">
                          {p.priced_as_families.join(', ') || '—'}
                          <span className="font-sans text-xs"> (provider type)</span>
                        </span>
                      )}
                    </td>
                    <td className="px-6 py-3 text-sm text-gray-300">{Object.keys(p.pricing).length || '—'}</td>
                    <td className="px-6 py-3 text-sm text-gray-300">{Object.keys(p.model_aliases).length || '—'}</td>
                    <td className="px-6 py-3 text-sm text-gray-300">{p.discount ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="bg-surface-1 rounded-lg border border-line-soft overflow-hidden">
        <div className="px-6 py-4 border-b border-line-soft">
          <h3 className="text-lg font-semibold text-gray-100">
            Unpriced models{' '}
            <span className="text-sm font-normal text-gray-500">(called in the last {ratecard.days} days)</span>
          </h3>
        </div>
        {unpriced.size === 0 ? (
          <p className="px-6 py-6 text-sm text-gray-500">Every model in use has a price.</p>
        ) : (
          <div className="divide-y divide-white/[0.06]">
            {[...unpriced.entries()].map(([provider, models]) => (
              <div key={provider} className="px-6 py-4">
                <p className="text-sm font-medium text-gray-100 mb-2">{provider || '(no provider)'}</p>
                <ul className="space-y-2">
                  {models.map((m) => (
                    <li key={m.model} className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                      <span className="font-mono text-gray-100">{m.model}</span>
                      <span className="text-gray-500">
                        {m.calls.toLocaleString()} {m.calls === 1 ? 'call' : 'calls'}
                      </span>
                      {editable.has(provider) && (
                        <span className="flex items-center gap-3 text-xs">
                          <button type="button" onClick={() => onFix(provider, m.model, 'family')} className="text-primary-400 hover:text-primary-300">
                            Set “Price models as”
                          </button>
                          <button type="button" onClick={() => onFix(provider, m.model, 'alias')} className="text-primary-400 hover:text-primary-300">
                            Add an alias
                          </button>
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export function LLMProviders() {
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [selectedProvider, setSelectedProvider] = useState<LLMProvider | null>(null);
  const [createFormData, setCreateFormData] = useState<LLMProviderCreate>({
    name: '',
    provider_type: 'openai',
    is_active: true,
    is_default: false,
  });
  const [editFormData, setEditFormData] = useState<LLMProviderUpdate>({});
  const [editConfigText, setEditConfigText] = useState('{}');
  const [createConfigText, setCreateConfigText] = useState('{}');
  // The header rows' first problem, reported by ProviderHeaders.
  const [createHeaderProblem, setCreateHeaderProblem] = useState<string | null>(null);
  const [editHeaderProblem, setEditHeaderProblem] = useState<string | null>(null);
  const [createRunCredentialProblem, setCreateRunCredentialProblem] = useState<string | null>(null);
  const [editRunCredentialProblem, setEditRunCredentialProblem] = useState<string | null>(null);
  const [createPricing, setCreatePricing] = useState<PricingDraft>(pricingDraft());
  const [editPricing, setEditPricing] = useState<PricingDraft>(pricingDraft());
  const [editError, setEditError] = useState('');
  // Why the edit form was opened from the pricing overview, if it was
  const [editNotice, setEditNotice] = useState('');
  const [editFocus, setEditFocus] = useState<PricingFocus | undefined>();

  const { data: providers, isLoading } = useQuery({
    queryKey: ['llmProviders'],
    queryFn: () => apiClient.listLLMProviders(),
    retry: false,
  });

  // Admins only (sinas.usage.read:all): without it the overview is left out
  // and the form offers no families to pick from.
  const ratecard = useRatecard();
  const families = ratecard.data?.provider_families;

  const createMutation = useMutation({
    mutationFn: (data: LLMProviderCreate) => apiClient.createLLMProvider(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llmProviders'] });
      queryClient.invalidateQueries({ queryKey: RATECARD_QUERY_KEY });
      setShowCreateModal(false);
      resetCreateForm();
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: LLMProviderUpdate }) =>
      apiClient.updateLLMProvider(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llmProviders'] });
      queryClient.invalidateQueries({ queryKey: RATECARD_QUERY_KEY });
      setShowEditModal(false);
      setSelectedProvider(null);
      setEditFormData({});
      setEditConfigText('{}');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (providerId: string) => apiClient.deleteLLMProvider(providerId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llmProviders'] });
      queryClient.invalidateQueries({ queryKey: RATECARD_QUERY_KEY });
    },
  });

  const resetCreateForm = () => {
    setCreateFormData({
      name: '',
      provider_type: 'openai',
      is_active: true,
      is_default: false,
    });
    setCreateConfigText('{}');
    setCreatePricing(pricingDraft());
  };

  const [createError, setCreateError] = useState('');

  const handleCreate = (e: React.FormEvent) => {
    e.preventDefault();
    setCreateError('');
    if (!createFormData.name.trim()) {
      setCreateError('Provider name is required');
      return;
    }
    if (!createFormData.provider_type.trim()) {
      setCreateError('Provider type is required');
      return;
    }
    const createHeaders = createHeaderProblem ?? headerProblem(createFormData.config);
    if (createHeaders) {
      setCreateError(`Headers: ${createHeaders}`);
      return;
    }
    if (createRunCredentialProblem) {
      setCreateError(`Per-run credential: ${createRunCredentialProblem}`);
      return;
    }
    let pricing;
    try {
      pricing = parsePricingDraft(createPricing);
    } catch (err) {
      setCreateError((err as Error).message);
      return;
    }
    createMutation.mutate({ ...createFormData, ...pricing });
  };

  const handleEdit = (e: React.FormEvent) => {
    e.preventDefault();
    setEditError('');
    if (selectedProvider && editFormData.name?.trim()) {
      const editHeaders = editHeaderProblem ?? headerProblem(editFormData.config);
      if (editHeaders) {
        setEditError(`Headers: ${editHeaders}`);
        return;
      }
      if (editRunCredentialProblem) {
        setEditError(`Per-run credential: ${editRunCredentialProblem}`);
        return;
      }
      let pricing;
      try {
        pricing = parsePricingDraft(editPricing);
      } catch (err) {
        setEditError((err as Error).message);
        return;
      }
      updateMutation.mutate({
        id: selectedProvider.id,
        data: { ...editFormData, ...pricing },
      });
    }
  };

  const openEditModal = (provider: LLMProvider, notice = '', focus?: PricingFocus) => {
    setSelectedProvider(provider);
    const configJson = JSON.stringify(provider.config || {}, null, 2);
    setEditConfigText(configJson);
    setEditFormData({
      name: provider.name,
      provider_type: provider.provider_type,
      api_endpoint: provider.api_endpoint || '',
      default_model: provider.default_model || '',
      config: provider.config || {},
      is_default: provider.is_default,
      is_active: provider.is_active,
    });
    setEditPricing(pricingDraft(provider));
    setEditError('');
    setEditNotice(notice);
    setEditFocus(focus);
    setShowEditModal(true);
  };

  // A one-click fix from the overview: the provider's form, at the field.
  const fixUnpriced = (providerName: string, model: string, how: 'family' | 'alias') => {
    const provider = providers?.find((p) => p.name === providerName);
    if (!provider) return;
    openEditModal(
      provider,
      how === 'family'
        ? `"${model}" has no price. If this provider calls another provider's OpenAI-compatible API, set "Price models as" to that provider's family.`
        : `"${model}" has no price. Alias it to the ratecard model it is (e.g. {"${model}": "<ratecard model>"}), or give it a price below.`,
      how
    );
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-gray-100">LLM Providers</h1>
          <p className="text-gray-400 mt-1">Manage language model provider configurations</p>
        </div>
        <button
          onClick={() => {
            resetCreateForm();
            setShowCreateModal(true);
          }}
          className="btn btn-primary flex items-center"
        >
          <Plus className="w-5 h-5 mr-2" />
          Add Provider
        </button>
      </div>

      {isLoading ? (
        <div className="text-center py-12">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
        </div>
      ) : providers && providers.length > 0 ? (
        <div className="grid gap-6">
          {providers.map((provider) => (
            <div key={provider.id} className={`card ${!provider.is_active ? 'opacity-60 bg-surface-0' : ''}`}>
              <div className="flex items-start justify-between">
                <div className="flex items-center flex-1">
                  <Brain className={`w-8 h-8 mr-3 flex-shrink-0 ${provider.is_active ? 'text-primary-600' : 'text-gray-500'}`} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <h3 className="font-semibold text-gray-100">{provider.name}</h3>
                      {provider.is_default && (
                        <span className="text-xs font-medium bg-primary-900/30 text-primary-400 px-2 py-0.5 rounded">Default</span>
                      )}
                      {provider.is_active ? (
                        <span className="px-2 py-0.5 bg-green-900/30 text-green-300 text-xs font-medium rounded">Active</span>
                      ) : (
                        <span className="px-2 py-0.5 bg-surface-2 text-gray-400 text-xs font-medium rounded">Inactive</span>
                      )}
                    </div>
                    <p className="text-sm text-gray-400">
                      Provider Type: <span className="font-medium">{provider.provider_type}</span>
                    </p>
                    {provider.api_endpoint && (
                      <p className="text-sm text-gray-400 truncate">
                        Endpoint: {provider.api_endpoint}
                      </p>
                    )}
                    {provider.default_model && (
                      <p className="text-sm text-gray-400">
                        Default Model: <span className="font-medium">{provider.default_model}</span>
                      </p>
                    )}
                    {(Object.keys(provider.pricing || {}).length > 0 ||
                      Object.keys(provider.model_aliases || {}).length > 0 ||
                      provider.discount != null ||
                      !!provider.pricing_provider) && (
                      <p className="text-sm text-gray-400">
                        Pricing:{' '}
                        <span className="font-medium">
                          {[
                            plural(Object.keys(provider.pricing || {}).length, 'model priced', 'models priced'),
                            plural(Object.keys(provider.model_aliases || {}).length, 'alias', 'aliases'),
                            provider.discount != null ? `discount ${provider.discount}` : '',
                            provider.pricing_provider ? `priced as ${provider.pricing_provider}` : '',
                          ]
                            .filter(Boolean)
                            .join(' · ')}
                        </span>
                      </p>
                    )}
                    <p className="text-xs text-gray-500 mt-1">
                      Created: {new Date(provider.created_at).toLocaleString()}
                    </p>
                  </div>
                </div>
                <div className="flex items-center space-x-2 ml-4 flex-shrink-0">
                  <button
                    onClick={() => openEditModal(provider)}
                    className="text-blue-600 hover:text-blue-400"
                    disabled={updateMutation.isPending}
                  >
                    <Edit2 className="w-5 h-5" />
                  </button>
                  <button
                    onClick={() => {
                      if (confirm('Are you sure you want to delete this LLM provider?')) {
                        deleteMutation.mutate(provider.id);
                      }
                    }}
                    className="text-red-600 hover:text-red-400"
                    disabled={deleteMutation.isPending}
                  >
                    <Trash2 className="w-5 h-5" />
                  </button>
                </div>
              </div>
              {provider.config && Object.keys(provider.config).length > 0 && (
                <div className="mt-4 pt-4 border-t border-line-soft">
                  <p className="text-sm font-medium text-gray-300 mb-2">Configuration:</p>
                  <pre className="text-xs bg-surface-0 p-3 rounded overflow-x-auto">
                    {JSON.stringify(provider.config, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <div className="text-center py-12 card">
          <Brain className="w-16 h-16 text-gray-500 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-gray-100 mb-2">No LLM providers configured</h3>
          <p className="text-gray-400 mb-4">Add language model providers to enable AI capabilities</p>
          <button onClick={() => setShowCreateModal(true)} className="btn btn-primary">
            <Plus className="w-5 h-5 mr-2 inline" />
            Add Provider
          </button>
        </div>
      )}

      {ratecard.data && (
        <PricingOverview
          ratecard={ratecard.data}
          editable={new Set((providers || []).map((p) => p.name))}
          onFix={fixUnpriced}
        />
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-surface-1 rounded-lg max-w-md w-full p-6 max-h-[90vh] overflow-y-auto">
            <h2 className="text-xl font-semibold text-gray-100 mb-4">Add LLM Provider</h2>
            <form onSubmit={handleCreate} className="space-y-4">
              <div>
                <label htmlFor="name" className="block text-sm font-medium text-gray-300 mb-2">
                  Provider Name *
                </label>
                <input
                  id="name"
                  type="text"
                  value={createFormData.name}
                  onChange={(e) => setCreateFormData({ ...createFormData, name: e.target.value })}
                  placeholder="OpenAI GPT-4"
                  required
                  className="input"
                  autoFocus
                />
              </div>

              <div>
                <label htmlFor="provider_type" className="block text-sm font-medium text-gray-300 mb-2">
                  Provider Type *
                </label>
                <select
                  id="provider_type"
                  value={createFormData.provider_type}
                  onChange={(e) => setCreateFormData({ ...createFormData, provider_type: e.target.value })}
                  className="input"
                  required
                >
                  <option value="openai">OpenAI</option>
                  <option value="azure">Azure OpenAI</option>
                  <option value="gemini">Google Gemini</option>
                  <option value="anthropic">Anthropic (Claude)</option>
                  <option value="mistral">Mistral AI</option>
                  <option value="ollama">Ollama</option>
                </select>
              </div>

              <div>
                <label htmlFor="api_key" className="block text-sm font-medium text-gray-300 mb-2">
                  API Key
                </label>
                <input
                  id="api_key"
                  type="password"
                  value={createFormData.api_key || ''}
                  onChange={(e) => setCreateFormData({ ...createFormData, api_key: e.target.value })}
                  placeholder="sk-..."
                  className="input"
                />
              </div>

              <div>
                <label htmlFor="api_endpoint" className="block text-sm font-medium text-gray-300 mb-2">
                  API Endpoint
                </label>
                <input
                  id="api_endpoint"
                  type="text"
                  value={createFormData.api_endpoint || ''}
                  onChange={(e) => setCreateFormData({ ...createFormData, api_endpoint: e.target.value })}
                  placeholder="https://api.openai.com/v1"
                  className="input"
                />
              </div>

              <div>
                <label htmlFor="default_model" className="block text-sm font-medium text-gray-300 mb-2">
                  Default Model
                </label>
                <input
                  id="default_model"
                  type="text"
                  value={createFormData.default_model || ''}
                  onChange={(e) => setCreateFormData({ ...createFormData, default_model: e.target.value })}
                  placeholder="gpt-4, mistral-large, llama3, etc."
                  className="input"
                />
                <p className="text-xs text-gray-500 mt-1">
                  Default model to use with this provider
                </p>
              </div>

              <div>
                <label htmlFor="config" className="block text-sm font-medium text-gray-300 mb-2">
                  Configuration (JSON)
                </label>
                <textarea
                  id="config"
                  value={createConfigText}
                  onChange={(e) => {
                    setCreateConfigText(e.target.value);
                    try {
                      const parsed = JSON.parse(e.target.value);
                      setCreateFormData({ ...createFormData, config: parsed });
                    } catch {
                      // Invalid JSON, don't update formData yet
                    }
                  }}
                  placeholder='{"model": "gpt-4", "temperature": 0.7}'
                  rows={6}
                  className="input resize-none font-mono text-xs"
                />
                <p className="text-xs text-gray-500 mt-1">
                  Provider-specific configuration as JSON. For Azure OpenAI set the
                  API Endpoint to your azure_endpoint and add{' '}
                  <code>{'{"api_version": "2024-10-21"}'}</code>. For reasoning
                  deployments (gpt-5/o-series) also add{' '}
                  <code>{'"drop_params": ["temperature"]'}</code>.
                </p>
              </div>

              <ProviderHeaders
                idPrefix="create"
                config={createFormData.config}
                disabled={!isJsonObject(createConfigText)}
                onProblemChange={setCreateHeaderProblem}
                onChange={(config) => {
                  setCreateFormData({ ...createFormData, config });
                  setCreateConfigText(JSON.stringify(config, null, 2));
                }}
              />

              <ProviderRunCredential
                idPrefix="create"
                config={createFormData.config}
                providerType={createFormData.provider_type}
                disabled={!isJsonObject(createConfigText)}
                onProblemChange={setCreateRunCredentialProblem}
                onChange={(config) => {
                  setCreateFormData({ ...createFormData, config });
                  setCreateConfigText(JSON.stringify(config, null, 2));
                }}
              />

              <PricingFields
                idPrefix="create"
                draft={createPricing}
                onChange={setCreatePricing}
                providerType={createFormData.provider_type}
                families={families}
              />

              <div className="flex items-center space-x-4">
                <div className="flex items-center">
                  <input
                    id="is_active"
                    type="checkbox"
                    checked={createFormData.is_active}
                    onChange={(e) => setCreateFormData({ ...createFormData, is_active: e.target.checked })}
                    className="h-4 w-4 text-primary-600 focus:ring-primary-500 border-line rounded"
                  />
                  <label htmlFor="is_active" className="ml-2 block text-sm text-gray-300">
                    Active
                  </label>
                </div>

                <div className="flex items-center">
                  <input
                    id="is_default"
                    type="checkbox"
                    checked={createFormData.is_default}
                    onChange={(e) => setCreateFormData({ ...createFormData, is_default: e.target.checked })}
                    className="h-4 w-4 text-primary-600 focus:ring-primary-500 border-line rounded"
                  />
                  <label htmlFor="is_default" className="ml-2 block text-sm text-gray-300">
                    Set as Default
                  </label>
                </div>
              </div>

              {createError && (
                <div className="p-3 bg-red-900/20 border border-red-800 rounded-lg">
                  <p className="text-sm text-red-400">{createError}</p>
                </div>
              )}
              {createMutation.isError && (
                <ErrorDisplay error={createMutation.error} title="Failed to add provider" />
              )}

              <div className="flex justify-end space-x-3 pt-4">
                <button
                  type="button"
                  onClick={() => {
                    setShowCreateModal(false);
                    resetCreateForm();
                  }}
                  className="btn btn-secondary"
                  disabled={createMutation.isPending}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={createMutation.isPending || !createFormData.name.trim() || !createFormData.provider_type.trim()}
                >
                  {createMutation.isPending ? 'Adding...' : 'Add Provider'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Edit Modal */}
      {showEditModal && selectedProvider && (
        <div className="fixed inset-0 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-surface-1 rounded-lg max-w-md w-full p-6 max-h-[90vh] overflow-y-auto">
            <h2 className="text-xl font-semibold text-gray-100 mb-4">Edit LLM Provider</h2>
            <form onSubmit={handleEdit} className="space-y-4">
              <div>
                <label htmlFor="edit_name" className="block text-sm font-medium text-gray-300 mb-2">
                  Provider Name *
                </label>
                <input
                  id="edit_name"
                  type="text"
                  value={editFormData.name || ''}
                  onChange={(e) => setEditFormData({ ...editFormData, name: e.target.value })}
                  placeholder="OpenAI GPT-4"
                  required
                  className="input"
                  autoFocus={!editFocus}
                />
              </div>

              <div>
                <label htmlFor="edit_provider_type" className="block text-sm font-medium text-gray-300 mb-2">
                  Provider Type *
                </label>
                <select
                  id="edit_provider_type"
                  value={editFormData.provider_type || ''}
                  onChange={(e) => setEditFormData({ ...editFormData, provider_type: e.target.value })}
                  className="input"
                  required
                >
                  <option value="openai">OpenAI</option>
                  <option value="azure">Azure OpenAI</option>
                  <option value="gemini">Google Gemini</option>
                  <option value="anthropic">Anthropic (Claude)</option>
                  <option value="mistral">Mistral AI</option>
                  <option value="ollama">Ollama</option>
                </select>
              </div>

              <div>
                <label htmlFor="edit_api_key" className="block text-sm font-medium text-gray-300 mb-2">
                  API Key
                </label>
                <input
                  id="edit_api_key"
                  type="password"
                  value={editFormData.api_key || ''}
                  onChange={(e) => setEditFormData({ ...editFormData, api_key: e.target.value })}
                  placeholder="Leave empty to keep current key"
                  className="input"
                />
                <p className="text-xs text-gray-500 mt-1">
                  API key is never returned for security. Enter new key to update.
                </p>
              </div>

              <div>
                <label htmlFor="edit_api_endpoint" className="block text-sm font-medium text-gray-300 mb-2">
                  API Endpoint
                </label>
                <input
                  id="edit_api_endpoint"
                  type="text"
                  value={editFormData.api_endpoint || ''}
                  onChange={(e) => setEditFormData({ ...editFormData, api_endpoint: e.target.value })}
                  placeholder="https://api.openai.com/v1"
                  className="input"
                />
              </div>

              <div>
                <label htmlFor="edit_default_model" className="block text-sm font-medium text-gray-300 mb-2">
                  Default Model
                </label>
                <input
                  id="edit_default_model"
                  type="text"
                  value={editFormData.default_model || ''}
                  onChange={(e) => setEditFormData({ ...editFormData, default_model: e.target.value })}
                  placeholder="gpt-4, mistral-large, llama3, etc."
                  className="input"
                />
                <p className="text-xs text-gray-500 mt-1">
                  Default model to use with this provider
                </p>
              </div>

              <div>
                <label htmlFor="edit_config" className="block text-sm font-medium text-gray-300 mb-2">
                  Configuration (JSON)
                </label>
                <textarea
                  id="edit_config"
                  value={editConfigText}
                  onChange={(e) => {
                    setEditConfigText(e.target.value);
                    try {
                      const parsed = JSON.parse(e.target.value);
                      setEditFormData({ ...editFormData, config: parsed });
                    } catch {
                      // Invalid JSON, don't update formData yet
                    }
                  }}
                  placeholder='{"model": "gpt-4", "temperature": 0.7}'
                  rows={6}
                  className="input resize-none font-mono text-xs"
                />
                <p className="text-xs text-gray-500 mt-1">
                  Provider-specific configuration as JSON
                </p>
              </div>

              <ProviderHeaders
                key={selectedProvider?.id}
                idPrefix="edit"
                config={editFormData.config}
                disabled={!isJsonObject(editConfigText)}
                onProblemChange={setEditHeaderProblem}
                onChange={(config) => {
                  setEditFormData({ ...editFormData, config });
                  setEditConfigText(JSON.stringify(config, null, 2));
                }}
              />

              <ProviderRunCredential
                idPrefix="edit"
                config={editFormData.config}
                providerType={editFormData.provider_type || ''}
                disabled={!isJsonObject(editConfigText)}
                onProblemChange={setEditRunCredentialProblem}
                onChange={(config) => {
                  setEditFormData({ ...editFormData, config });
                  setEditConfigText(JSON.stringify(config, null, 2));
                }}
              />

              <PricingFields
                idPrefix="edit"
                draft={editPricing}
                onChange={setEditPricing}
                providerType={editFormData.provider_type || ''}
                families={families}
                notice={editNotice}
                focus={editFocus}
              />

              <div className="flex items-center space-x-4">
                <div className="flex items-center">
                  <input
                    id="edit_is_active"
                    type="checkbox"
                    checked={editFormData.is_active ?? false}
                    onChange={(e) => setEditFormData({ ...editFormData, is_active: e.target.checked })}
                    className="h-4 w-4 text-primary-600 focus:ring-primary-500 border-line rounded"
                  />
                  <label htmlFor="edit_is_active" className="ml-2 block text-sm text-gray-300">
                    Active
                  </label>
                </div>

                <div className="flex items-center">
                  <input
                    id="edit_is_default"
                    type="checkbox"
                    checked={editFormData.is_default ?? false}
                    onChange={(e) => setEditFormData({ ...editFormData, is_default: e.target.checked })}
                    className="h-4 w-4 text-primary-600 focus:ring-primary-500 border-line rounded"
                  />
                  <label htmlFor="edit_is_default" className="ml-2 block text-sm text-gray-300">
                    Set as Default
                  </label>
                </div>
              </div>

              {editError && (
                <div className="p-3 bg-red-900/20 border border-red-800 rounded-lg">
                  <p className="text-sm text-red-400">{editError}</p>
                </div>
              )}
              {updateMutation.isError && (
                <ErrorDisplay error={updateMutation.error} title="Failed to update provider" />
              )}

              <div className="flex justify-end space-x-3 pt-4">
                <button
                  type="button"
                  onClick={() => {
                    setShowEditModal(false);
                    setSelectedProvider(null);
                    setEditFormData({});
                    setEditConfigText('{}');
                  }}
                  className="btn btn-secondary"
                  disabled={updateMutation.isPending}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={updateMutation.isPending || !editFormData.name?.trim()}
                >
                  {updateMutation.isPending ? 'Updating...' : 'Update Provider'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
