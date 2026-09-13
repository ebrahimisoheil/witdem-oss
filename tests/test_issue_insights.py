from copy import deepcopy

from witdem.analytics.core import Operation
from witdem.analytics.evidence import required_measurement_alerts
from witdem.analytics.issues import summarize_issue_insights


def summarize(rows, operations=None, evaluations=None):
    return summarize_issue_insights(rows, operations or {}, evaluations or [], cost_unavailable={})


def test_run_availability_is_not_operation_coverage_or_goal_success():
    rows = [
        {'execution_id': 'zero', 'known_cost': 0, 'total_tokens': 0, 'product_goal_achieved': False},
        {'execution_id': 'missing'},
        {'execution_id': 'measured', 'known_cost': 1.5, 'total_tokens': 12, 'product_goal_achieved': True},
    ]
    assert summarize(rows)['measurement'] == {
        'cost': 2, 'tokens': 2, 'business_goal': 2, 'total': 3, 'cost_unavailable': {},
    }
    assert summarize([])['measurement'] == {
        'cost': 0, 'tokens': 0, 'business_goal': 0, 'total': 0, 'cost_unavailable': {},
    }


def test_terminal_failure_predicate_preserves_recovery_and_observed_failure_count():
    rows = [
        {'execution_id': 'failed', 'failure_count': 1, 'runtime_outcome': 'failed'},
        {'execution_id': 'recovered', 'failure_count': 2, 'runtime_outcome': 'recovered'},
        {'execution_id': 'status-only', 'failure_count': 0, 'runtime_outcome': 'failed'},
    ]
    assert summarize(rows)['summary'] == {
        'runs': 3, 'terminal_failures': 1, 'recovered_runs': 1, 'extra_attempts': 0, 'quality_gaps': 0,
    }


def test_quality_direction_target_ties_missing_score_and_population():
    facts = [
        {'execution_id': 'run', 'name': 'higher', 'score': 0.5, 'attributes': {'target': 0.6}},
        {'execution_id': 'run', 'name': 'lower', 'score': 3,
         'attributes': {'target': 2, 'direction': 'lower_is_better'}},
        {'execution_id': 'run', 'name': 'tie', 'score': 2, 'attributes': {'target': 2}},
        {'execution_id': 'run', 'name': 'missing', 'score': None, 'attributes': {'target': 2}},
        {'execution_id': 'other', 'name': 'excluded', 'score': 0, 'attributes': {'target': 1}},
    ]
    result = summarize([{'execution_id': 'run'}], evaluations=facts)
    assert result['summary']['quality_gaps'] == 2
    assert [item['name'] for item in result['quality_gaps']] == ['higher', 'lower']


def test_retry_counts_precede_display_limit_and_do_not_mutate_inputs():
    rows = [{'execution_id': 'run', 'display_name': 'Example'}]
    operations = {'run': [Operation(execution_id='run', kind='custom', name=f'step-{i}', attempt=2)
                          for i in range(12)]}
    original = deepcopy((rows, operations))
    result = summarize(rows, operations)
    assert result['summary']['extra_attempts'] == 12
    assert len(result['retries']) == 10
    assert all(item['affected_runs'] == 1 and item['runs'] == [rows[0]] for item in result['retries'])
    assert (rows, operations) == original
    assert summarize(rows, operations) == result


def test_percentile_nearest_rank_ties_and_selected_population():
    rows = [{'execution_id': str(i), 'duration_seconds': i} for i in range(1, 21)]
    assert [item['execution_id'] for item in summarize(rows)['outliers']] == ['20', '19']
    assert [item['execution_id'] for item in summarize(rows[:1])['outliers']] == ['1']
    assert summarize([{'execution_id': 'absent'}])['outliers'] == []
    tied = [{'execution_id': str(i), 'known_cost': 0} for i in range(12)]
    assert len(summarize(tied)['outliers']) == 10
    assert all(item['reasons'] == ['known_cost'] for item in summarize(tied)['outliers'])


def test_required_measurement_alerts_count_operations_and_distinct_executions():
    operations = [
        {'operation_id': 'a', 'execution_id': 'run-a', 'operation_type': 'model', 'workflow_id': 'z'},
        {'operation_id': 'b', 'execution_id': 'run-a', 'operation_type': 'model', 'workflow_id': 'z'},
        {'operation_id': 'c', 'execution_id': 'run-b', 'operation_type': 'model', 'workflow_id': 'a'},
    ]
    measurements = [
        {'operation_id': key, 'measurement_key': 'cost.usd', 'measurement_status': 'missing'}
        for key in ('a', 'b', 'c')
    ]
    original = deepcopy((operations, measurements))
    assert required_measurement_alerts(operations, measurements) == [{
        'operation_type': 'model', 'measurement_key': 'cost.usd', 'operations': 3,
        'executions': 2, 'workflow_ids': ['a', 'z'],
    }]
    assert (operations, measurements) == original


def test_only_explicit_missing_measurements_of_actual_operations_are_alerts():
    operations = [
        {'operation_id': 'container', 'entity_kind': 'execution', 'execution_id': 'run'},
        {'operation_id': 'work', 'execution_id': 'run', 'operation_type': 'ocr'},
    ]
    measurements = [
        {'operation_id': 'container', 'measurement_key': 'cost.usd', 'measurement_status': 'missing'},
        {'operation_id': 'orphan', 'measurement_key': 'cost.usd', 'measurement_status': 'missing'},
        {'operation_id': 'work', 'measurement_key': 'cost.usd', 'measurement_status': 'measured', 'value': 0},
        {'operation_id': 'work', 'measurement_key': 'tokens.total', 'measurement_status': 'not_applicable'},
        {'operation_id': 'work', 'measurement_key': 'optional'},
    ]
    assert required_measurement_alerts(operations, measurements) == []
    assert required_measurement_alerts([], []) == []
    measurements.append({'operation_id': 'work', 'measurement_key': 'duration', 'measurement_status': 'missing'})
    assert required_measurement_alerts(operations, measurements) == [{
        'operation_type': 'ocr', 'measurement_key': 'duration', 'operations': 1,
        'executions': 1, 'workflow_ids': [],
    }]
