"""Azure OpenAI LLM provider implementation."""
from typing import Any, Optional

from openai import AsyncAzureOpenAI

from .base import BaseLLMProvider
from .openai_provider import OpenAIProvider

# Azure requires an explicit API version; this is a recent stable default used
# when one is not configured. See Azure OpenAI "API version lifecycle" docs.
DEFAULT_API_VERSION = "2024-10-21"

# Modern chat models use ``max_completion_tokens``; ``max_tokens`` is deprecated
# and rejected outright by reasoning models. Default to the modern name and let
# legacy deployments override it via config.
DEFAULT_MAX_TOKENS_PARAM = "max_completion_tokens"


class AzureOpenAIProvider(OpenAIProvider):
    """
    Azure OpenAI provider.

    Azure exposes the same chat completions surface as OpenAI but the client must
    be constructed differently: it needs an ``azure_endpoint`` (e.g.
    ``https://<resource>.openai.azure.com/`` or a gateway URL) and an
    ``api_version``. On Azure the ``model`` argument is the *deployment name*
    rather than the canonical OpenAI model id.

    Per-deployment request quirks are driven entirely by provider ``config`` —
    there is no model-name detection in code. Relevant config keys:

    - ``api_version``: Azure API version (defaults to ``DEFAULT_API_VERSION``).
    - ``azure_deployment``: pin a single deployment (then ``model`` is ignored).
    - ``max_tokens_param``: name used for the token limit. Defaults to
      ``max_completion_tokens``; set ``max_tokens`` for legacy deployments.
    - ``drop_params``: list of parameter names to strip before sending. Set
      ``["temperature"]`` for reasoning deployments (gpt-5/o-series) that only
      accept the default temperature. Streaming requests include
      ``stream_options`` (for token usage); add it here for gateways or API
      versions that reject it.
    - ``extra_params``: dict of defaults merged into every request, e.g.
      ``{"reasoning_effort": "medium"}``. Caller-supplied kwargs take precedence.

    Response handling (tool-call formatting, usage extraction) is inherited
    unchanged from :class:`OpenAIProvider`.
    """

    # Azure batch requires a dedicated Global-Batch deployment, which the
    # provider config doesn't model yet — opt out of the inherited OpenAI
    # batch methods so they aren't offered against a normal deployment.
    supports_batch = False

    def __init__(
        self,
        api_key: Optional[str] = None,
        azure_endpoint: Optional[str] = None,
        api_version: Optional[str] = None,
        azure_deployment: Optional[str] = None,
        max_tokens_param: Optional[str] = None,
        drop_params: Optional[list[str]] = None,
        extra_params: Optional[dict[str, Any]] = None,
    ):
        if not azure_endpoint:
            raise ValueError(
                "Azure OpenAI provider requires an azure_endpoint "
                "(configure it via the provider's api_endpoint)"
            )

        # Skip OpenAIProvider.__init__ on purpose: it builds an AsyncOpenAI
        # client. Initialise the base provider directly, then build the Azure
        # client below.
        BaseLLMProvider.__init__(self, api_key=api_key, base_url=azure_endpoint)

        self.api_version = api_version or DEFAULT_API_VERSION
        self.azure_deployment = azure_deployment
        self.max_tokens_param = max_tokens_param or DEFAULT_MAX_TOKENS_PARAM
        self.drop_params = list(drop_params or [])
        self.extra_params = dict(extra_params or {})
        self.client = AsyncAzureOpenAI(
            api_key=api_key,
            api_version=self.api_version,
            azure_endpoint=azure_endpoint,
            azure_deployment=azure_deployment,
        )

    def _prepare_params(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: Optional[int],
        tools: Optional[list[dict[str, Any]]],
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the request payload applying Azure/deployment-specific config."""
        params: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }

        if max_tokens:
            params[self.max_tokens_param] = max_tokens

        if tools:
            params["tools"] = tools
            params["tool_choice"] = "auto"

        # Config defaults first so explicit caller kwargs win over them.
        for key, value in self.extra_params.items():
            params.setdefault(key, value)
        params.update(kwargs)

        # Strip params unsupported by this deployment (applied last so it also
        # removes anything injected via kwargs/extra_params).
        for name in self.drop_params:
            params.pop(name, None)

        return params
