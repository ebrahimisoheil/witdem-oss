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

CREATE TABLE IF NOT EXISTS workflow_templates (
    workflow_id VARCHAR,
    template_hash VARCHAR,
    name VARCHAR,
    definition VARCHAR,
    source VARCHAR,
    registered_at TIMESTAMP,
    PRIMARY KEY (workflow_id, template_hash)
);

CREATE TABLE IF NOT EXISTS execution_workflows (
    execution_id VARCHAR PRIMARY KEY,
    workflow_id VARCHAR,
    template_hash VARCHAR,
    match_source VARCHAR,
    matched_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workflow_execution_projections (
    execution_id VARCHAR PRIMARY KEY,
    workflow_id VARCHAR,
    template_hash VARCHAR,
    projector_version VARCHAR,
    projection VARCHAR,
    projected_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workflow_execution_nodes (
    execution_id VARCHAR,
    workflow_id VARCHAR,
    template_hash VARCHAR,
    node_id VARCHAR,
    state VARCHAR,
    attempts BIGINT,
    duration_seconds DOUBLE,
    known_cost DOUBLE,
    total_tokens DOUBLE,
    providers VARCHAR,
    models VARCHAR,
    evidence VARCHAR,
    PRIMARY KEY (execution_id, node_id)
);
