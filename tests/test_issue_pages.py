import pytest
from pydantic import ValidationError

from witdem.analytics.issue_pages import IssueInvestigationPage, IssueInvestigationRequest, page_issue_investigation
from witdem.analytics.issue_projection import IssueRetryContribution, project_issue_execution


def population():
    return [project_issue_execution({'execution_id': f'run-{index:03}', 'runtime_outcome': 'recovered'}, [], [])
            .model_copy(update={
                'failure_location': 'model' if index % 2 == 0 else None,
                'retries': [IssueRetryContribution(
                    key='a' if index % 2 == 0 else 'b', label='shared', extra_attempts=2,
                )],
            }) for index in reversed(range(27))]


@pytest.mark.parametrize('kind,retry_key', [('failures', None), ('retry_runs', 'a')])
def test_pages_cover_every_member_once_with_stable_totals(kind, retry_key):
    runs = population()
    cursor = None
    identities = []
    sizes = []
    while True:
        request = IssueInvestigationRequest(schema_version='1', kind=kind, scope_id='a' * 64,
                                            retry_key=retry_key, page_size=5, cursor=cursor)
        page = page_issue_investigation(runs, request)
        assert page.total == 14
        assert IssueInvestigationPage.model_validate_json(page.model_dump_json()) == page
        identities.extend(item.execution_id for item in page.items)
        sizes.append(len(page.items))
        cursor = page.next_cursor
        if cursor is None:
            break
    assert sizes == [5, 5, 4]
    assert identities == [f'run-{index:03}' for index in range(0, 27, 2)]


@pytest.mark.parametrize('update', [
    {'scope_id': 'b' * 64}, {'kind': 'retry_runs', 'retry_key': 'a'}, {'schema_version': '2'},
])
def test_cursor_rejects_different_scope_investigation_or_version(update):
    request = IssueInvestigationRequest(schema_version='1', kind='failures', scope_id='a' * 64, page_size=1)
    cursor = page_issue_investigation(population(), request).next_cursor
    values = {**request.model_dump(), 'cursor': cursor, **update}
    with pytest.raises(ValidationError):
        IssueInvestigationRequest.model_validate(values)


@pytest.mark.parametrize('update', [
    {'page_size': 0}, {'page_size': 101}, {'page_size': True}, {'retry_key': 'x'},
    {'kind': 'retry_runs'}, {'scope_id': 'unscoped'},
])
def test_invalid_requests_fail_closed(update):
    with pytest.raises(ValidationError):
        IssueInvestigationRequest.model_validate({'schema_version': '1', 'kind': 'failures',
                                                  'scope_id': 'a' * 64, **update})


def test_empty_snapshot_duplicate_identity_and_canonical_retry_key():
    request = IssueInvestigationRequest(schema_version='1', kind='retry_runs', scope_id='a' * 64, retry_key='shared')
    # A display label is not a canonical retry group identity.
    assert page_issue_investigation(population(), request).total == 0
    assert page_issue_investigation([], request).items == []
    with pytest.raises(ValueError, match='unique'):
        page_issue_investigation(population() * 2, request)


def test_response_cannot_claim_invalid_continuation_or_exceed_page_size():
    request = IssueInvestigationRequest(schema_version='1', kind='failures', scope_id='a' * 64, page_size=2)
    page = page_issue_investigation(population(), request)
    for update in ({'page_size': 1}, {'total': 1}, {'items': list(reversed(page.items))}, {'items': []}):
        with pytest.raises(ValidationError):
            IssueInvestigationPage.model_validate({**page.model_dump(), **update})
