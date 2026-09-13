from pathlib import Path

import pytest

from witdem.analytics.core import Evaluation, Event, Operation, Outcome
from witdem.analytics.evidence import EvidenceBundle
from witdem.analytics.issue_projection import (
    project_bundle_issue_execution,
    project_bundle_issue_operations,
    project_issue_operations,
)
from witdem.analytics.issue_reducer import reduce_issue_projections
from witdem.analytics.operation_facts import workflow_operation_facts
from witdem.analytics.repository import AnalyticsRepository
from witdem.analytics.repository.state import FilterState
from witdem.analytics.runtime import NormalizedExecutionGraph, derive_replay_graph
from witdem.dashboard import service
from witdem.ingest import live_db
from witdem.workflows import WorkflowDefinition, project_execution


@pytest.mark.parametrize('status', ['completed', 'failed', 'running'])
@pytest.mark.parametrize('measured', [False, True])
def test_bundle_run_issues_match_actual_serving_repository(tmp_path, monkeypatch, status, measured):
    database = tmp_path / 'issues.duckdb'
    monkeypatch.setenv('WITDEM_DB_PATH', str(database))
    monkeypatch.setenv('WITDEM_DATA_DIR', str(tmp_path))
    original = EvidenceBundle.model_validate_json(
        (Path(__file__).parent / 'fixtures/evidence-bundle-v1-oldest.json').read_text(),
    )
    identity = original.execution.execution_id
    operations = [Operation(
        execution_id=identity, operation_id='model-call', kind='model', name='review', status='error', attempt=2,
        attributes={'provider': 'test', 'model': 'test-model', 'prompt': 'DO_NOT_RETAIN',
                    **({'cost_usd': 0, 'total_tokens': 0} if measured else {})},
    )]
    evaluations = [Evaluation(execution_id=identity, name='quality', source='test', score=0,
                              attributes={'target': 0.5, 'response': 'DO_NOT_RETAIN'})]
    outcomes = [Outcome(execution_id=identity, name='product_goal', attributes={'product_goal_achieved': False})]
    bundle = original.model_copy(update={
        'execution': original.execution.model_copy(update={'status': status}),
        'operations': operations, 'links': [], 'events': [], 'evaluations': evaluations, 'outcomes': outcomes,
    })
    live_db.publish_transformed_bundle(bundle.execution, operations, [], [*evaluations, *outcomes])
    reader = AnalyticsRepository(database)
    try:
        expected = reader.get_issue_insights()
        projection = project_bundle_issue_execution(bundle)
        # This test covers run issues only. No operation classification facts were
        # published; operation-level parity has its own projection tests.
        result = reduce_issue_projections(
            [projection], [project_issue_operations(identity, [], [])], cost_unavailable={},
        )
        assert {key: result[key] for key in expected} == expected
        assert result['measurement']['cost'] == int(measured)
        assert result['measurement']['tokens'] == int(measured)
        assert result['measurement']['business_goal'] == 1
        assert result['summary']['extra_attempts'] == 1
        assert result['summary']['quality_gaps'] == 1
        assert 'DO_NOT_RETAIN' not in projection.model_dump_json()
    finally:
        reader.close()


def test_bundle_projection_rejects_foreign_records():
    bundle = EvidenceBundle.model_validate_json(
        (Path(__file__).parent / 'fixtures/evidence-bundle-v1-oldest.json').read_text(),
    )
    bundle = bundle.model_copy(update={'operations': [Operation(execution_id='foreign', kind='custom', name='work')]})
    with pytest.raises(ValueError, match='must belong'):
        project_bundle_issue_execution(bundle)


def test_bundle_operation_projection_uses_declared_required_and_optional_meters(tmp_path, monkeypatch):
    bundle = EvidenceBundle.model_validate_json(
        (Path(__file__).parent / 'fixtures/evidence-bundle-v1-oldest.json').read_text(),
    )
    identity = bundle.execution.execution_id
    definition = WorkflowDefinition.model_validate({
        'version': 2, 'id': 'retrieve-workflow', 'name': 'Retrieve',
        'stages': [{'id': 'retrieval', 'name': 'Retrieval', 'nodes': [{
            'id': 'retrieve', 'name': 'Retrieve', 'match': {'names': ['retrieve']},
            'operation': {'type': 'retrieval', 'expects': ['queries'], 'optional': ['documents.output']},
        }]}],
    })
    operation = Operation(execution_id=identity, kind='component', name='retrieve',
                          attributes={'haystack.component.name': 'retrieve'})
    bundle = bundle.model_copy(update={'operations': [operation], 'links': [], 'events': []})
    projection = project_bundle_issue_operations(bundle, definition=definition)
    assert projection.total_operations == 1
    assert projection.operation_types[0].type == 'retrieval'
    gaps = {item.measurement_key for item in projection.required_measurements}
    assert 'queries' in gaps
    assert 'documents.output' not in gaps
    assert all(item.workflow_ids == ['retrieve-workflow'] for item in projection.required_measurements)
    database = tmp_path / 'declared-issues.duckdb'
    monkeypatch.setenv('WITDEM_DB_PATH', str(database))
    monkeypatch.setenv('WITDEM_DATA_DIR', str(tmp_path))
    live_db.publish_transformed_bundle(
        bundle.execution, bundle.operations, [], [*bundle.evaluations, *bundle.outcomes],
    )
    graph = derive_replay_graph(NormalizedExecutionGraph(execution=bundle.execution, operations=bundle.operations))
    replay = project_execution(definition, execution={'execution_id': identity}, graph=graph.model_dump(mode='json'))
    facts, meters = workflow_operation_facts(replay, bundle.operations)
    live_db.store_operation_facts(database, [identity], facts, meters)
    reader = AnalyticsRepository(database)
    try:
        expected = service.issues(reader, FilterState())
        actual = reduce_issue_projections([project_bundle_issue_execution(bundle)], [projection], cost_unavailable={})
        assert actual == expected
    finally:
        reader.close()


def test_bundle_operation_projection_does_not_treat_unresolved_declarations_as_absent():
    bundle = EvidenceBundle.model_validate_json(
        (Path(__file__).parent / 'fixtures/evidence-bundle-v1-oldest.json').read_text(),
    )
    identity = bundle.execution.execution_id
    declaration = Event(execution_id=identity, type='event', name='workflow.definition',
                        payload={'definition': {'version': 'unsupported'}})
    bundle = bundle.model_copy(update={'events': [declaration]})
    with pytest.raises(ValueError, match='explicit resolution'):
        project_bundle_issue_operations(bundle, definition=None)
    empty = bundle.model_copy(update={'events': [], 'operations': [], 'links': []})
    assert project_bundle_issue_operations(empty, definition=None).total_operations == 0
