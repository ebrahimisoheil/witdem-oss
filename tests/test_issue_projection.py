import json

import pytest
from pydantic import ValidationError

from witdem.analytics.core import Operation
from witdem.analytics.evidence import operation_summary, required_measurement_alerts
from witdem.analytics.issue_projection import (
    IssueExecutionProjection,
    IssueOperationProjection,
    project_issue_execution,
    project_issue_operations,
)
from witdem.analytics.issues import summarize_issue_insights


def test_projection_is_untruncated_and_matches_public_counts():
    row = {'execution_id': 'run', 'known_cost': 0, 'total_tokens': 0, 'product_goal_achieved': False}
    operations = [Operation(execution_id='run', kind='custom', name=f'work-{i}', attempt=2) for i in range(12)]
    facts = [{'execution_id': 'run', 'name': f'quality-{i}', 'score': 0, 'attributes': {'target': 1}}
             for i in range(12)]
    projection = project_issue_execution(row, operations, facts)
    public = summarize_issue_insights([row], {'run': operations}, facts, cost_unavailable={})
    assert len(projection.retries) == public['summary']['extra_attempts'] == 12
    assert len(projection.quality_gaps) == public['summary']['quality_gaps'] == 12
    assert len(public['retries']) == len(public['quality_gaps']) == 10
    assert projection.known_cost == projection.total_tokens == 0
    assert projection.product_goal_achieved is False
    assert len({retry.key for retry in projection.retries}) == 12
    assert IssueExecutionProjection.model_validate_json(projection.model_dump_json()) == projection


def test_projection_allowlists_fields_instead_of_storing_raw_content():
    private = 'DO-NOT-RETAIN-CONTENT'
    row = {'execution_id': 'run', 'prompt': private, 'attributes': {'secret': private}}
    operations = [Operation(execution_id='run', kind='custom', name='work', attempt=2,
                            attributes={'prompt': private, 'response': private})]
    facts = [{'execution_id': 'run', 'name': 'quality', 'score': 0,
              'attributes': {'target': 1, 'response': private}}]
    projection = project_issue_execution(row, operations, facts)
    assert private not in projection.model_dump_json()
    assert projection.known_cost is None and projection.product_goal_achieved is None
    assert 'outlier' not in projection.model_dump_json()


@pytest.mark.parametrize('mutation', ['version', 'extra', 'missing', 'nested_extra'])
def test_projection_rejects_unsupported_or_incomplete_serialized_inputs(mutation):
    projection = project_issue_execution({'execution_id': 'run'}, [], [])
    value = json.loads(projection.model_dump_json())
    if mutation == 'version':
        value['schema_version'] = '2'
    elif mutation == 'extra':
        value['prompt'] = 'not allowed'
    elif mutation == 'missing':
        del value['known_cost']
    else:
        value['retries'] = [{'key': 'a', 'label': 'a', 'extra_attempts': 1, 'prompt': 'not allowed'}]
    with pytest.raises(ValidationError):
        IssueExecutionProjection.model_validate(value)


def test_projection_uses_public_recovery_predicate_and_rejects_foreign_operations():
    row = {'execution_id': 'run', 'runtime_outcome': 'recovered', 'failure_count': 1}
    projection = project_issue_execution(row, [], [])
    assert projection.recovered and not projection.terminal_failure
    with pytest.raises(ValueError, match='must belong'):
        project_issue_execution(row, [Operation(execution_id='other', kind='custom', name='work')], [])
    facts = [{'execution_id': 'other', 'score': 0, 'attributes': {'target': 1}}]
    assert project_issue_execution(row, [], facts).quality_gaps == []


def test_operation_projection_retains_successful_types_and_matches_public_diagnostics():
    operations = [
        {'execution_id': 'run', 'operation_id': 'root', 'entity_kind': 'execution', 'status': 'failed'},
        {'execution_id': 'run', 'operation_id': 'failed', 'operation_type': 'ocr', 'status': 'failed',
         'workflow_id': 'contract-review', 'duration_seconds': 2},
        {'execution_id': 'run', 'operation_id': 'success', 'operation_type': 'ocr', 'status': 'completed'},
        {'execution_id': 'run', 'operation_id': 'child', 'parent_operation_id': 'success',
         'operation_type': 'model', 'status': 'completed', 'provider_id': 'provider', 'model_id': 'model'},
    ]
    measurements = [
        {'operation_id': 'failed', 'measurement_key': 'cost.usd', 'measurement_status': 'missing'},
        {'operation_id': 'success', 'measurement_key': 'cost.usd', 'measurement_status': 'measured', 'value': 0},
        {'operation_id': 'child', 'measurement_key': 'tokens.total', 'measurement_status': 'measured', 'value': 12},
        {'operation_id': 'root', 'measurement_key': 'cost.usd', 'measurement_status': 'missing'},
    ]
    projection = project_issue_operations('run', operations, measurements)
    expected = operation_summary(operations, measurements)
    assert projection.total_operations == 3
    assert projection.execution_containers == 1
    assert projection.failed_operations == 1
    assert [item.model_dump() for item in projection.operation_types] == expected['types']
    assert [item.model_dump() for item in projection.required_measurements] == required_measurement_alerts(
        operations, measurements,
    )
    assert any(item.failed == 0 for item in projection.operation_types)
    assert projection.operation_types[0].operations == 2
    assert projection.operation_types[0].linked_children[0].providers == ['provider']
    assert IssueOperationProjection.model_validate_json(projection.model_dump_json()) == projection


def test_operation_projection_excludes_content_and_fails_closed_on_invalid_contracts():
    operation = {'execution_id': 'run', 'operation_id': 'work', 'operation_type': 'model',
                 'prompt': 'PRIVATE', 'attributes': {'response': 'PRIVATE'}}
    projection = project_issue_operations('run', [operation], [])
    assert 'PRIVATE' not in projection.model_dump_json()
    with pytest.raises(ValueError, match='must belong'):
        project_issue_operations('other', [operation], [])
    value = projection.model_dump()
    value['schema_version'] = 'unsupported'
    with pytest.raises(ValidationError):
        IssueOperationProjection.model_validate(value)
    value = projection.model_dump()
    del value['required_measurements']
    with pytest.raises(ValidationError):
        IssueOperationProjection.model_validate(value)
    value = projection.model_dump()
    value['operation_types'][0]['prompt'] = 'PRIVATE'
    with pytest.raises(ValidationError):
        IssueOperationProjection.model_validate(value)


def test_empty_operation_projection_is_explicitly_complete():
    projection = project_issue_operations('empty', [], [])
    assert projection.model_dump() == {
        'schema_version': '1', 'execution_id': 'empty', 'total_operations': 0,
        'execution_containers': 0, 'failed_operations': 0, 'operation_types': [], 'required_measurements': [],
    }


@pytest.mark.parametrize('field', ['total_operations', 'failed_operations'])
def test_operation_projection_rejects_inconsistent_summary_counts(field):
    value = project_issue_operations('empty', [], []).model_dump()
    value[field] = 1
    with pytest.raises(ValidationError, match='do not match'):
        IssueOperationProjection.model_validate(value)
