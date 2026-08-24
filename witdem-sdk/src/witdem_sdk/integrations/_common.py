"""Shared lifecycle for high-level framework integrations."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, cast

from opentelemetry import baggage

from witdem_sdk import Witdem, configure
from witdem_sdk._contract import DescriptiveContractSpec

ResultReporter = Callable[[Any], Mapping[str, Any] | None]


def report_business_result(result: Any, witdem: Witdem, reporter: ResultReporter | None) -> None:
    if reporter is not None:
        values = reporter(result)
        if values is not None:
            cast(Any, witdem.report)(**dict(values))
        return
    config = getattr(witdem, "project_config", None)
    contract_name = getattr(config, "default_contract", None)
    contract = config.contracts.get(contract_name) if config is not None and contract_name else None
    if contract is not None and not isinstance(contract, DescriptiveContractSpec):
        witdem.complete(result, contract=contract_name)


@dataclass(frozen=True)
class IntegrationSettings:
    service_name: str | None = None
    execution_name: str | None = None
    endpoint: str | None = None
    config_path: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)
    report_result: ResultReporter | None = None

    @contextmanager
    def invocation(self) -> Iterator[Witdem]:
        with configure(
            self.service_name,
            endpoint=self.endpoint,
            config_path=self.config_path,
        ) as witdem:
            if baggage.get_baggage("witdem.execution_id"):
                yield witdem
            else:
                with witdem.execution(self.execution_name, attributes=self.attributes):
                    yield witdem

    def report(self, result: Any, witdem: Witdem) -> None:
        report_business_result(result, witdem, self.report_result)


def settings(
    *,
    service_name: str | None,
    execution_name: str | None,
    endpoint: str | None,
    config_path: str | None,
    attributes: Mapping[str, Any] | None,
    report_result: ResultReporter | None,
) -> IntegrationSettings:
    return IntegrationSettings(
        service_name=service_name,
        execution_name=execution_name,
        endpoint=endpoint,
        config_path=config_path,
        attributes=dict(attributes or {}),
        report_result=report_result,
    )
