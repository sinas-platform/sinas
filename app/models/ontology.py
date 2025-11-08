"""Ontology models for semantic layer and data management."""
from datetime import datetime
from enum import Enum as PyEnum
from uuid import uuid4

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.models.base import Base


class DataType(PyEnum):
    """Data types for concept properties."""
    STRING = "STRING"
    INT = "INT"
    DECIMAL = "DECIMAL"
    BOOL = "BOOL"
    DATETIME = "DATETIME"
    JSON = "JSON"


class Cardinality(PyEnum):
    """Relationship cardinality types."""
    ONE_TO_ONE = "1-1"
    ONE_TO_MANY = "1-many"
    MANY_TO_ONE = "many-1"
    MANY_TO_MANY = "many-many"


class DataSource(Base):
    """External or internal data source configuration."""
    __tablename__ = "data_sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(Text, nullable=False)
    type = Column(Text, nullable=False)  # postgres, snowflake, bigquery
    conn_string = Column(Text, nullable=False)  # Store encrypted
    default_database = Column(Text)
    default_schema = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    group = relationship("Group", back_populates="data_sources")
    concept_queries = relationship("ConceptQuery", back_populates="data_source", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint('group_id', 'name', name='uq_datasource_group_name'),
    )


class Concept(Base):
    """A concept in the ontology (e.g., Customer, Order, Product)."""
    __tablename__ = "concepts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    group_id = Column(UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    namespace = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    display_name = Column(Text)
    description = Column(Text)
    is_self_managed = Column(Boolean, default=False, nullable=False)  # self-managed in SINAS DB
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    group = relationship("Group", back_populates="concepts")
    properties = relationship("Property", back_populates="concept", cascade="all, delete-orphan")
    concept_query = relationship("ConceptQuery", back_populates="concept", uselist=False, cascade="all, delete-orphan")
    relationships_from = relationship("Relationship", foreign_keys="Relationship.from_concept_id", back_populates="from_concept", cascade="all, delete-orphan")
    relationships_to = relationship("Relationship", foreign_keys="Relationship.to_concept_id", back_populates="to_concept")
    endpoints = relationship("Endpoint", back_populates="subject_concept", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint('group_id', 'namespace', 'name', name='uq_concept_group_namespace_name'),
    )


class Property(Base):
    """A property/attribute of a concept."""
    __tablename__ = "properties"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    concept_id = Column(UUID(as_uuid=True), ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(Text, nullable=False)
    display_name = Column(Text)
    description = Column(Text)
    data_type = Column(Enum(DataType), nullable=False)
    is_identifier = Column(Boolean, default=False, nullable=False)  # Primary key equivalent
    is_required = Column(Boolean, default=False, nullable=False)
    default_value = Column(Text)  # Stored as string, cast to appropriate type
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    concept = relationship("Concept", back_populates="properties")
    relationships_from = relationship("Relationship", foreign_keys="Relationship.from_property_id", back_populates="from_property")
    relationships_to = relationship("Relationship", foreign_keys="Relationship.to_property_id", back_populates="to_property")
    endpoint_properties = relationship("EndpointProperty", back_populates="property", cascade="all, delete-orphan")
    endpoint_filters = relationship("EndpointFilter", back_populates="property", cascade="all, delete-orphan")
    endpoint_orders = relationship("EndpointOrder", back_populates="property", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint('concept_id', 'name', name='uq_property_concept_name'),
    )


class Relationship(Base):
    """Relationship between two concepts."""
    __tablename__ = "relationships"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    from_concept_id = Column(UUID(as_uuid=True), ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False, index=True)
    to_concept_id = Column(UUID(as_uuid=True), ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(Text, nullable=False)
    cardinality = Column(Enum(Cardinality), nullable=False)
    from_property_id = Column(UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    to_property_id = Column(UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    from_concept = relationship("Concept", foreign_keys=[from_concept_id], back_populates="relationships_from")
    to_concept = relationship("Concept", foreign_keys=[to_concept_id], back_populates="relationships_to")
    from_property = relationship("Property", foreign_keys=[from_property_id], back_populates="relationships_from")
    to_property = relationship("Property", foreign_keys=[to_property_id], back_populates="relationships_to")
    endpoint_joins = relationship("EndpointJoin", back_populates="relationship", cascade="all, delete-orphan")


class ConceptQuery(Base):
    """SQL query or sync configuration for materializing a concept."""
    __tablename__ = "concept_queries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    concept_id = Column(UUID(as_uuid=True), ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False, unique=True)
    data_source_id = Column(UUID(as_uuid=True), ForeignKey("data_sources.id", ondelete="CASCADE"))  # Nullable for self-managed
    sql_text = Column(Text)  # Nullable for self-managed concepts
    sync_enabled = Column(Boolean, default=False, nullable=False)  # Option 2: sync to local DB
    sync_schedule = Column(Text)  # Cron expression for sync schedule
    last_synced_at = Column(DateTime)
    validated_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    concept = relationship("Concept", back_populates="concept_query")
    data_source = relationship("DataSource", back_populates="concept_queries")


class ResponseFormat(PyEnum):
    """Response format for endpoint execution."""
    JSON = "JSON"
    CSV = "CSV"
    PARQUET = "PARQUET"


class JoinType(PyEnum):
    """SQL join types."""
    INNER = "inner"
    LEFT = "left"
    RIGHT = "right"


class SortDirection(PyEnum):
    """Sort direction for ordering."""
    ASC = "ASC"
    DESC = "DESC"


class FilterOperator(PyEnum):
    """Filter operators for endpoint filters."""
    EQ = "="
    NEQ = "!="
    GT = ">"
    GTE = ">="
    LT = "<"
    LTE = "<="
    IN = "IN"
    NOT_IN = "NOT IN"
    LIKE = "LIKE"
    ILIKE = "ILIKE"
    IS_NULL = "IS NULL"
    IS_NOT_NULL = "IS NOT NULL"
    BETWEEN = "BETWEEN"


class Endpoint(Base):
    """API endpoint configuration for querying concepts."""
    __tablename__ = "endpoints"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(Text, nullable=False)
    route = Column(Text, nullable=False, unique=True)
    subject_concept_id = Column(UUID(as_uuid=True), ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False, index=True)
    response_format = Column(Enum(ResponseFormat), default=ResponseFormat.JSON, nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    description = Column(Text)
    limit_default = Column(Integer, default=100, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    subject_concept = relationship("Concept", back_populates="endpoints")
    properties = relationship("EndpointProperty", back_populates="endpoint", cascade="all, delete-orphan")
    filters = relationship("EndpointFilter", back_populates="endpoint", cascade="all, delete-orphan")
    orders = relationship("EndpointOrder", back_populates="endpoint", cascade="all, delete-orphan")
    joins = relationship("EndpointJoin", back_populates="endpoint", cascade="all, delete-orphan")


class EndpointProperty(Base):
    """Property configuration for an endpoint."""
    __tablename__ = "endpoint_properties"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    endpoint_id = Column(UUID(as_uuid=True), ForeignKey("endpoints.id", ondelete="CASCADE"), nullable=False, index=True)
    concept_id = Column(UUID(as_uuid=True), ForeignKey("concepts.id", ondelete="CASCADE"), nullable=False)
    property_id = Column(UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    alias = Column(Text)
    aggregation = Column(Text)  # max, avg, count, sum, etc
    include = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    endpoint = relationship("Endpoint", back_populates="properties")
    concept = relationship("Concept")
    property = relationship("Property", back_populates="endpoint_properties")


class EndpointFilter(Base):
    """Filter configuration for an endpoint."""
    __tablename__ = "endpoint_filters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    endpoint_id = Column(UUID(as_uuid=True), ForeignKey("endpoints.id", ondelete="CASCADE"), nullable=False, index=True)
    property_id = Column(UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    op = Column(Enum(FilterOperator), nullable=False)
    param_name = Column(Text, nullable=False)
    required = Column(Boolean, default=False, nullable=False)
    default_value = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    endpoint = relationship("Endpoint", back_populates="filters")
    property = relationship("Property", back_populates="endpoint_filters")


class EndpointOrder(Base):
    """Order/sort configuration for an endpoint."""
    __tablename__ = "endpoint_orders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    endpoint_id = Column(UUID(as_uuid=True), ForeignKey("endpoints.id", ondelete="CASCADE"), nullable=False, index=True)
    property_id = Column(UUID(as_uuid=True), ForeignKey("properties.id", ondelete="CASCADE"), nullable=False)
    direction = Column(Enum(SortDirection), default=SortDirection.ASC, nullable=False)
    priority = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    endpoint = relationship("Endpoint", back_populates="orders")
    property = relationship("Property", back_populates="endpoint_orders")


class EndpointJoin(Base):
    """Join configuration for an endpoint."""
    __tablename__ = "endpoint_joins"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    endpoint_id = Column(UUID(as_uuid=True), ForeignKey("endpoints.id", ondelete="CASCADE"), nullable=False, index=True)
    relationship_id = Column(UUID(as_uuid=True), ForeignKey("relationships.id", ondelete="CASCADE"), nullable=False)
    join_type = Column(Enum(JoinType), default=JoinType.INNER, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    endpoint = relationship("Endpoint", back_populates="joins")
    relationship = relationship("Relationship", back_populates="endpoint_joins")
