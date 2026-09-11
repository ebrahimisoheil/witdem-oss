"""Versioned investigation paging contracts and an in-memory reference adapter.

Not an HTTP endpoint or scalable storage implementation. A serving adapter must
derive scope_id from the authorized tenant, canonical filters and immutable
snapshot/revision. Cursors are selectors, never authorization credentials.
Legacy Issues responses are intentionally unchanged until consumers adopt pages.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from witdem.analytics.issue_projection import IssueExecutionProjection


class IssuePageRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False)


class IssueInvestigationCursor(IssuePageRecord):
    schema_version: Literal['1']
    kind: Literal['failures', 'retry_runs']
    scope_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    retry_key: str | None
    after_execution_id: str = Field(min_length=1)

    @model_validator(mode='after')
    def matching_kind(self) -> 'IssueInvestigationCursor':
        if (self.kind == 'retry_runs') != bool(self.retry_key):
            raise ValueError('retry key is required only for retry-run pages')
        if self.kind == 'failures' and self.retry_key is not None:
            raise ValueError('failure pages cannot select a retry key')
        return self


class IssueInvestigationRequest(IssuePageRecord):
    schema_version: Literal['1']
    kind: Literal['failures', 'retry_runs']
    scope_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    retry_key: str | None = None
    page_size: int = Field(default=25, ge=1, le=100)
    cursor: IssueInvestigationCursor | None = None

    @model_validator(mode='after')
    def matching_scope(self) -> 'IssueInvestigationRequest':
        # Share selector validation even for the first page.
        IssueInvestigationCursor(schema_version='1', kind=self.kind, scope_id=self.scope_id,
                                 retry_key=self.retry_key, after_execution_id='_')
        if self.cursor is not None and (
            self.cursor.kind != self.kind or self.cursor.scope_id != self.scope_id
            or self.cursor.retry_key != self.retry_key
        ):
            raise ValueError('issue cursor does not match the selected population and investigation')
        return self


class IssueRunReference(IssuePageRecord):
    execution_id: str = Field(min_length=1)
    display_name: str | None


class IssueFailureReference(IssueRunReference):
    failure_location: str = Field(min_length=1)
    runtime_outcome: str | None
    duration_seconds: float | None
    known_cost: float | None


class IssueInvestigationPage(IssuePageRecord):
    schema_version: Literal['1']
    kind: Literal['failures', 'retry_runs']
    scope_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    retry_key: str | None
    total: int = Field(ge=0)
    page_size: int = Field(ge=1, le=100)
    items: list[IssueFailureReference | IssueRunReference]
    next_cursor: IssueInvestigationCursor | None

    @model_validator(mode='after')
    def consistent(self) -> 'IssueInvestigationPage':
        IssueInvestigationRequest(schema_version='1', kind=self.kind, scope_id=self.scope_id,
                                  retry_key=self.retry_key, page_size=self.page_size, cursor=self.next_cursor)
        identities = [item.execution_id for item in self.items]
        if identities != sorted(set(identities)):
            raise ValueError('issue page identities must be unique and ordered')
        if len(self.items) > min(self.total, self.page_size):
            raise ValueError('issue page exceeds its total or page size')
        if any(isinstance(item, IssueFailureReference) != (self.kind == 'failures') for item in self.items):
            raise ValueError('issue page item does not match investigation kind')
        if self.next_cursor is not None and (
            not identities or len(identities) != self.page_size or self.total <= len(identities)
            or self.next_cursor.after_execution_id != identities[-1]
        ):
            raise ValueError('issue continuation must follow a full nonterminal page')
        return self


def page_issue_investigation(
    executions: list[IssueExecutionProjection], request: IssueInvestigationRequest,
) -> IssueInvestigationPage:
    """Reference keyset semantics over a complete authorized snapshot.

    Retry identity is the canonical OSS key, not its potentially shared label.
    A recovered run with a breakpoint remains a failure investigation member,
    independently of the terminal-failure summary count. Ordering is execution
    identity ascending (storage adapters should use an equivalent binary collation).
    """
    request = IssueInvestigationRequest.model_validate_json(request.model_dump_json())
    identities = [item.execution_id for item in executions]
    if len(identities) != len(set(identities)):
        raise ValueError('issue paging requires unique execution identities')
    selected: list[IssueFailureReference | IssueRunReference] = []
    for item in sorted(executions, key=lambda value: value.execution_id):
        if request.kind == 'failures' and item.failure_location:
            selected.append(IssueFailureReference(
                execution_id=item.execution_id, display_name=item.display_name,
                failure_location=item.failure_location, runtime_outcome=item.runtime_outcome,
                duration_seconds=item.duration_seconds, known_cost=item.known_cost,
            ))
        elif request.kind == 'retry_runs' and any(retry.key == request.retry_key for retry in item.retries):
            selected.append(IssueRunReference(execution_id=item.execution_id, display_name=item.display_name))
    total = len(selected)
    if request.cursor is not None:
        selected = [item for item in selected if item.execution_id > request.cursor.after_execution_id]
    items = selected[:request.page_size]
    cursor = None
    if len(selected) > request.page_size:
        cursor = IssueInvestigationCursor(schema_version='1', kind=request.kind, scope_id=request.scope_id,
                                         retry_key=request.retry_key, after_execution_id=items[-1].execution_id)
    return IssueInvestigationPage(schema_version='1', kind=request.kind, scope_id=request.scope_id,
                                 retry_key=request.retry_key, total=total, page_size=request.page_size,
                                 items=items, next_cursor=cursor)
