-- Initialize ClickHouse database for SINAS request logging

CREATE DATABASE IF NOT EXISTS sinas;

-- Request logs table with comprehensive tracking
CREATE TABLE IF NOT EXISTS sinas.request_logs
(
    -- Primary identifiers
    request_id UUID DEFAULT generateUUIDv4(),
    timestamp DateTime64(3) DEFAULT now64(3),

    -- User and permission tracking
    user_id String,
    user_email String,
    permission_used String,
    has_permission Boolean,

    -- Request details
    method String,
    path String,
    query_params String,
    request_body String,

    -- HTTP headers
    user_agent String,
    referer String,
    ip_address String,

    -- Response details
    status_code UInt16,
    response_time_ms UInt32,
    response_size_bytes UInt32,

    -- Resource tracking
    resource_type String,
    resource_id String,
    group_id String,

    -- Error tracking
    error_message String,
    error_type String,

    -- Additional metadata
    metadata String  -- JSON string for flexible additional data
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (timestamp, user_id, path)
SETTINGS index_granularity = 8192;
-- Note: No TTL set - logs are kept indefinitely for compliance purposes
-- Partitioning by month allows easy manual archival if needed

-- Index for fast user lookups
CREATE INDEX IF NOT EXISTS idx_user ON sinas.request_logs (user_id) TYPE bloom_filter GRANULARITY 1;

-- Index for permission queries
CREATE INDEX IF NOT EXISTS idx_permission ON sinas.request_logs (permission_used) TYPE bloom_filter GRANULARITY 1;

-- Index for path queries
CREATE INDEX IF NOT EXISTS idx_path ON sinas.request_logs (path) TYPE bloom_filter GRANULARITY 1;
