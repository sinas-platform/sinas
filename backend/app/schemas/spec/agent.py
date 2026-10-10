"""Agent spec.

The REST API names the LLM provider by id, config by name; the spec holds
the name (what config and export use) and the applier resolves it.

The normalizations config apply always made are spec validators now, so
REST writes get them too: a bare function, skill or collection name means
the `default` namespace, a bare store name means read-write access (as in
config; a store object without `access` is read-only), and hooks written in
config's camelCase are stored in the snake_case the runtime reads.
"""

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.spec.base import SpecModel


def _in_default(ref: str) -> str:
    return ref if "/" in ref else f"default/{ref}"


class SkillRef(BaseModel):
    skill: str
    preload: bool = False


class StoreRef(BaseModel):
    store: str
    access: str = "readonly"


class CollectionRef(BaseModel):
    collection: str
    access: str = "readonly"


_HOOK_LISTS = {"onUserMessage": "on_user_message", "onAssistantMessage": "on_assistant_message"}
_HOOK_KEYS = {"onTimeout": "on_timeout", "async_": "async"}


class AgentSpec(SpecModel):
    namespace: str = Field(default="default", min_length=1, max_length=255, pattern=r"^[^/]+$")
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    llm_provider_name: Optional[str] = None  # None: the default provider
    model: Optional[str] = None  # None: the provider's default model
    provider_overrides: Optional[dict[str, Any]] = None
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    system_prompt: Optional[str] = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    initial_messages: Optional[list[dict[str, Any]]] = None
    enabled_functions: list[str] = Field(default_factory=list)
    function_parameters: dict[str, Any] = Field(default_factory=dict)
    status_templates: dict[str, str] = Field(default_factory=dict)
    enabled_agents: list[str] = Field(default_factory=list)
    enabled_skills: list[SkillRef] = Field(default_factory=list)
    enabled_stores: list[StoreRef] = Field(default_factory=list)
    enabled_queries: list[str] = Field(default_factory=list)
    query_parameters: dict[str, Any] = Field(default_factory=dict)
    enabled_collections: list[CollectionRef] = Field(default_factory=list)
    enabled_components: list[str] = Field(default_factory=list)
    enabled_connectors: list[dict[str, Any]] = Field(default_factory=list)
    enabled_pipelines: list[str] = Field(default_factory=list)
    hooks: Optional[dict[str, Any]] = None
    icon: Optional[str] = None
    default_job_timeout: Optional[int] = None
    default_keep_alive: bool = False
    system_tools: list[Any] = Field(default_factory=list)
    is_default: bool = False
    is_active: bool = True

    @field_validator("description", "llm_provider_name", "model", "system_prompt", "icon", mode="before")
    @classmethod
    def _empty_is_none(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator("temperature", mode="before")
    @classmethod
    def _default_temperature(cls, value: Any) -> Any:
        return 0.7 if value is None else value

    @field_validator("default_keep_alive", "is_default", mode="before")
    @classmethod
    def _none_is_false(cls, value: Any) -> Any:
        return False if value is None else value

    @field_validator(
        "input_schema", "output_schema", "function_parameters", "status_templates",
        "query_parameters", mode="before",
    )
    @classmethod
    def _none_is_empty_dict(cls, value: Any) -> Any:
        return {} if value is None else value

    @field_validator(
        "enabled_functions", "enabled_agents", "enabled_skills", "enabled_stores",
        "enabled_queries", "enabled_collections", "enabled_components", "enabled_connectors",
        "enabled_pipelines", "system_tools", mode="before",
    )
    @classmethod
    def _none_is_empty_list(cls, value: Any) -> Any:
        return [] if value is None else value

    @field_validator("provider_overrides", mode="before")
    @classmethod
    def _overrides(cls, value: Any) -> Any:
        if not value:
            return None  # {} clears them: back to the provider's settings
        from app.providers.factory import validate_provider_overrides

        problems = validate_provider_overrides(value)
        if problems:
            raise ValueError("; ".join(problems))
        return value

    @field_validator("enabled_functions", mode="after")
    @classmethod
    def _functions(cls, value: list[str]) -> list[str]:
        return [_in_default(ref) for ref in value]

    @field_validator("enabled_skills", mode="before")
    @classmethod
    def _skills(cls, value: Any) -> Any:
        skills = []
        for item in value or []:
            if isinstance(item, str):
                skills.append({"skill": _in_default(item), "preload": False})
            else:
                item = item.model_dump() if hasattr(item, "model_dump") else dict(item)
                skills.append({**item, "skill": _in_default(item.get("skill", ""))})
        return skills

    @field_validator("enabled_stores", mode="before")
    @classmethod
    def _stores(cls, value: Any) -> Any:
        return [
            {"store": item, "access": "readwrite"} if isinstance(item, str)
            else item.model_dump() if hasattr(item, "model_dump") else item
            for item in value or []
        ]

    @field_validator("enabled_collections", mode="before")
    @classmethod
    def _collections(cls, value: Any) -> Any:
        return [
            {"collection": _in_default(item), "access": "readonly"} if isinstance(item, str)
            else item.model_dump() if hasattr(item, "model_dump") else item
            for item in value or []
        ]

    @field_validator("system_tools", mode="before")
    @classmethod
    def _system_tools(cls, value: Any) -> Any:
        return [item.model_dump() if hasattr(item, "model_dump") else item for item in value or []]

    @field_validator("hooks", mode="before")
    @classmethod
    def _hooks(cls, value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "model_dump"):
            value = value.model_dump(by_alias=True)
        hooks: dict[str, Any] = {}
        for key, entries in dict(value).items():
            hooks[_HOOK_LISTS.get(key, key)] = [
                {_HOOK_KEYS.get(k, k): v for k, v in dict(entry).items()}
                for entry in entries or []
            ]
        return hooks

    @property
    def key(self) -> str:
        return f"{self.namespace}/{self.name}"

    def to_config(self) -> dict[str, Any]:
        # As the export always was: empty lists and maps are left out.
        return {k: v for k, v in super().to_config().items() if v not in ({}, [])}
