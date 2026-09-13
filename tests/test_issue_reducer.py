from copy import deepcopy

import pytest

from witdem.analytics.core import Operation
from witdem.analytics.evidence import operation_summary, required_measurement_alerts
from witdem.analytics.issue_projection import project_issue_execution, project_issue_operations
from witdem.analytics.issue_reducer import reduce_issue_projections
from witdem.analytics.issues import summarize_issue_insights


@pytest.mark.parametrize('selected', [[], ['a'], ['b'], ['a', 'b']])
def test_compact_cohort_matches_existing_public_issue_response(selected):
    rows = [
        {'execution_id': identity, 'display_name': identity, 'known_cost': index, 'total_tokens': index * 10,
         'duration_seconds': index, 'product_goal_achieved': False, 'failure_count': 1,
         'runtime_outcome': 'recovered' if identity == 'a' else 'failed'}
        for index, identity in enumerate(selected)
    ]
    operations = {identity: [Operation(execution_id=identity, kind='custom', name='shared', attempt=2)]
                  for identity in selected}
    evaluations = [{'execution_id': identity, 'name': 'quality', 'score': 0, 'attributes': {'target': 1}}
                   for identity in selected]
    facts = [{
        'execution_id': identity, 'operation_id': identity, 'operation_type': 'model',
        'status': 'failed' if identity == 'a' else 'completed', 'workflow_id': identity,
        'provider_id': identity, 'duration_seconds': 1,
    } for identity in selected]
    meters = [{'operation_id': identity, 'measurement_key': 'cost.usd', 'measurement_status': 'missing'}
              for identity in selected]
    executions = [project_issue_execution(row, operations[row['execution_id']], evaluations) for row in rows]
    projected_operations = [project_issue_operations(identity,
                            [fact for fact in facts if fact['execution_id'] == identity], meters)
                            for identity in selected]
    original = deepcopy((executions, projected_operations))
    expected = summarize_issue_insights(rows, operations, evaluations, cost_unavailable={})
    expected['operation_failures'] = [item for item in operation_summary(facts, meters)['types'] if item['failed'] > 0]
    expected['missing_required_measurements'] = required_measurement_alerts(facts, meters)
    actual = reduce_issue_projections(executions, projected_operations, cost_unavailable={})
    assert actual == expected
    assert (executions, projected_operations) == original
    if len(selected) == 2:
        assert actual['retries'][0]['affected_runs'] == 2
        assert actual['retries'][0]['extra_attempts'] == 2
        assert actual['operation_failures'][0]['operations'] == 2
        assert actual['operation_failures'][0]['failed'] == 1
        assert actual['missing_required_measurements'][0]['executions'] == 2


def test_missing_or_duplicate_projection_coverage_is_not_a_clean_population():
    execution = project_issue_execution({'execution_id': 'a'}, [], [])
    operations = project_issue_operations('a', [], [])
    with pytest.raises(ValueError, match='coverage'):
        reduce_issue_projections([execution], [], cost_unavailable={})
    with pytest.raises(ValueError, match='unique'):
        reduce_issue_projections([execution, execution], [operations], cost_unavailable={})
    with pytest.raises(ValueError, match='unique'):
        reduce_issue_projections([execution], [operations, operations], cost_unavailable={})


def test_outlier_threshold_is_recomputed_after_population_selection():
    projections = [project_issue_execution({'execution_id': str(i), 'duration_seconds': i}, [], [])
                   for i in range(1, 21)]
    operations = [project_issue_operations(item.execution_id, [], []) for item in projections]
    full = reduce_issue_projections(projections, operations, cost_unavailable={})
    filtered = reduce_issue_projections(projections[:1], operations[:1], cost_unavailable={})
    assert [item['execution_id'] for item in full['outliers']] == ['20', '19']
    assert [item['execution_id'] for item in filtered['outliers']] == ['1']


def test_linked_children_metadata_and_measured_totals_merge_like_public_summary():
    all_facts = []
    all_meters = []
    executions = []
    projections = []
    for identity, value in [('a', 0), ('b', 3)]:
        facts = [
            {'execution_id': identity, 'operation_id': identity, 'operation_type': 'parent', 'status': 'failed'},
            {'execution_id': identity, 'operation_id': identity + '-child', 'parent_operation_id': identity,
             'operation_type': 'child', 'family': 'custom', 'provider_id': identity, 'model_id': identity},
        ]
        meters = [{'operation_id': identity, 'measurement_key': 'cost.usd',
                   'measurement_status': 'measured', 'value': value}]
        all_facts.extend(facts)
        all_meters.extend(meters)
        executions.append(project_issue_execution({'execution_id': identity}, [], []))
        projections.append(project_issue_operations(identity, facts, meters))
    result = reduce_issue_projections(executions, projections, cost_unavailable={})
    expected = [item for item in operation_summary(all_facts, all_meters)['types'] if item['failed'] > 0]
    assert result['operation_failures'] == expected
    assert result['operation_failures'][0]['measurements'] == {'cost.usd': 3}
    assert result['operation_failures'][0]['linked_children'][0]['providers'] == ['a', 'b']
