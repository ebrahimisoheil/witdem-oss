-- Canonical read-model schema reference.
-- The live writer remains the owner of schema creation in ingest/live_db.py;
-- this file makes the table contract inspectable without opening Python code.

CREATE TABLE IF NOT EXISTS executions (
    execution_id VARCHAR,
    runtime_id VARCHAR,
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    status VARCHAR,
    schema_version VARCHAR,
    attributes VARCHAR
);

CREATE TABLE IF NOT EXISTS operations (
    operation_id VARCHAR,
    execution_id VARCHAR,
    trace_id VARCHAR,
    span_id VARCHAR,
    parent_span_id VARCHAR,
    kind VARCHAR,
    name VARCHAR,
    status VARCHAR,
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    attempt BIGINT,
    attributes VARCHAR
);

CREATE TABLE IF NOT EXISTS links (
    link_id VARCHAR,
    execution_id VARCHAR,
    source_id VARCHAR,
    target_id VARCHAR,
    relation VARCHAR,
    attributes VARCHAR
);

CREATE TABLE IF NOT EXISTS events (
    event_id VARCHAR PRIMARY KEY,
    execution_id VARCHAR,
    trace_id VARCHAR,
    span_id VARCHAR,
    timestamp TIMESTAMP,
    type VARCHAR,
    name VARCHAR,
    payload VARCHAR,
    schema_version VARCHAR
);

CREATE TABLE IF NOT EXISTS evaluations (
    evaluation_id VARCHAR PRIMARY KEY,
    execution_id VARCHAR,
    subject_id VARCHAR,
    name VARCHAR,
    value VARCHAR,
    label VARCHAR,
    score DOUBLE,
    source VARCHAR,
    confidence DOUBLE,
    definition_version VARCHAR,
    attributes VARCHAR
);

CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id VARCHAR PRIMARY KEY,
    execution_id VARCHAR,
    name VARCHAR,
    status VARCHAR,
    value VARCHAR,
    timestamp TIMESTAMP,
    attributes VARCHAR
);
