"""Pydantic schemas for database schema browser endpoints."""

from typing import Any, Optional

from pydantic import BaseModel, Field


# ── Introspection Responses ────────────────────────────────────────

class SchemaInfo(BaseModel):
    schema_name: str


class ColumnInfo(BaseModel):
    column_name: str
    data_type: str
    udt_name: str
    is_nullable: str
    column_default: Optional[str] = None
    character_maximum_length: Optional[int] = None
    numeric_precision: Optional[int] = None
    numeric_scale: Optional[int] = None
    ordinal_position: int
    is_primary_key: bool = False
    # Annotations (merged from DB)
    display_name: Optional[str] = None
    description: Optional[str] = None


class ConstraintInfo(BaseModel):
    constraint_name: str
    constraint_type: str
    columns: list[str]
    definition: Optional[str] = None
    ref_schema: Optional[str] = None
    ref_table: Optional[str] = None
    ref_columns: Optional[list[str]] = None


class IndexInfo(BaseModel):
    index_name: str
    definition: str


class TableInfo(BaseModel):
    table_name: str
    table_type: str = "BASE TABLE"
    estimated_rows: int = 0
    size_bytes: int = 0
    # Annotations
    display_name: Optional[str] = None
    description: Optional[str] = None


class TableDetail(BaseModel):
    table_name: str
    schema_name: str
    columns: list[ColumnInfo]
    constraints: list[ConstraintInfo]
    indexes: list[IndexInfo]
    # Annotations
    display_name: Optional[str] = None
    description: Optional[str] = None


class ViewInfo(BaseModel):
    view_name: str
    view_definition: Optional[str] = None


# ── DDL Requests ───────────────────────────────────────────────────

class ColumnDefinition(BaseModel):
    name: str = Field(..., max_length=63)
    type: str
    nullable: bool = True
    default: Optional[str] = None
    primary_key: bool = False


class CreateTableRequest(BaseModel):
    table_name: str = Field(..., max_length=63)
    schema_name: str = Field(default="public", max_length=63)
    columns: list[ColumnDefinition] = Field(..., min_length=1)
    if_not_exists: bool = False


class AlterTableRequest(BaseModel):
    schema_name: str = Field(default="public", max_length=63)
    add_columns: Optional[list[ColumnDefinition]] = None
    drop_columns: Optional[list[str]] = None
    rename_columns: Optional[dict[str, str]] = None


class CreateViewRequest(BaseModel):
    name: str = Field(..., max_length=63)
    schema_name: str = Field(default="public", max_length=63)
    sql: str
    or_replace: bool = False


# ── Data Browser ───────────────────────────────────────────────────

class FilterCondition(BaseModel):
    column: str
    operator: str = Field(
        ...,
        description="One of: =, !=, >, <, >=, <=, LIKE, ILIKE, IS NULL, IS NOT NULL",
    )
    value: Optional[Any] = None


class BrowseRowsResponse(BaseModel):
    rows: list[dict[str, Any]]
    total_count: int


class InsertRowsRequest(BaseModel):
    rows: list[dict[str, Any]] = Field(..., min_length=1)


class UpdateRowsRequest(BaseModel):
    where: dict[str, Any] = Field(..., min_length=1)
    set_values: dict[str, Any] = Field(..., min_length=1)


class DeleteRowsRequest(BaseModel):
    where: dict[str, Any] = Field(..., min_length=1)


# ── Annotations ────────────────────────────────────────────────────

class AnnotationItem(BaseModel):
    schema_name: str = "public"
    table_name: str
    column_name: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None


class AnnotationsUpsertRequest(BaseModel):
    annotations: list[AnnotationItem]
