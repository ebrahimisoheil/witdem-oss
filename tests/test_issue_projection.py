import json

import pytest
from pydantic import ValidationError

from witdem.analytics.core import Operation
from witdem.analytics.issue_projection import IssueExecutionProjection, project_issue_execution
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
