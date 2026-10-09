"""Collection spec."""

from typing import Any, Optional

from pydantic import Field, field_validator

from app.schemas.spec.base import SpecModel


class CollectionSpec(SpecModel):
    namespace: str = Field(default="default", min_length=1, max_length=100, pattern=r"^[^/]+$")
    name: str = Field(min_length=1, max_length=100)
    metadata_schema: dict[str, Any] = Field(default_factory=dict)
    # Functions run on upload, as "namespace/name".
    content_filter_function: Optional[str] = Field(default=None, max_length=255)
    post_upload_function: Optional[str] = Field(default=None, max_length=255)
    # Config never bounded these (0 blocks uploads, but applied); the REST
    # schema keeps its 1..1000 range.
    max_file_size_mb: int = Field(default=100, ge=0)
    max_total_size_gb: int = Field(default=10, ge=0)
    is_public: bool = False
    allow_shared_files: bool = True
    allow_private_files: bool = True

    @field_validator("content_filter_function", "post_upload_function", mode="before")
    @classmethod
    def _empty_is_none(cls, value: Any) -> Any:
        return None if value == "" else value

    @field_validator("content_filter_function", "post_upload_function")
    @classmethod
    def _function_ref(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and (value.count("/") != 1 or value.startswith("/") or value.endswith("/")):
            raise ValueError("must be a function as 'namespace/name'")
        return value

    @field_validator("metadata_schema", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any) -> Any:
        return {} if value is None else value

    @property
    def key(self) -> str:
        return f"{self.namespace}/{self.name}"

    def to_config(self) -> dict[str, Any]:
        return {k: v for k, v in super().to_config().items() if v != {}}
