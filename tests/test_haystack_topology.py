from __future__ import annotations

import json

from witdem.analytics.runtime import derive_replay_graph, normalize_haystack_spans


def _edge(source: str, source_socket: str, target: str, target_socket: str) -> str:
    return json.dumps(
        {
            "source_component": source,
            "source_socket": source_socket,
            "target_component": target,
            "target_socket": target_socket,
            "cycle": False,
            "retry": False,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _span(
    span_id: str,
    name: str,
    start: int,
    end: int,
    *,
    parent: str = "root",
    component: str | None = None,
    incoming: tuple[str, ...] = (),
    emitted: tuple[str, ...] = (),
    inputs: tuple[str, ...] = (),
    attributes: dict | None = None,
) -> dict:
    values = dict(attributes or {})
    values["witdem.execution_id"] = "run-topology"
    if component:
        values.update(
            {
                "haystack.component.name": component,
                "witdem.haystack.topology.version": "1",
                "witdem.haystack.component.id": component,
                "witdem.haystack.component.name": component,
                "witdem.haystack.component.type": f"fixture.{component}",
                "witdem.haystack.runtime.input_sockets": json.dumps(inputs),
                "witdem.haystack.runtime.emitted_sockets": json.dumps(emitted),
            }
        )
        if incoming:
            values["witdem.haystack.topology.incoming"] = json.dumps(incoming)
    return {
        "trace_id": "trace-topology",
        "span_id": span_id,
        "parent_span_id": parent or None,
        "name": name,
        "start_time_unix_nano": start,
        "end_time_unix_nano": end,
        "status": {"status_code": "OK"},
        "attributes": values,
        "resource": {"service.name": "topology-fixture"},
        "instrumentation_scope": {"name": "witdem.haystack", "version": "1"},
    }


def test_explicit_haystack_fanout_and_convergence_survive_normalization() -> None:
    dispatcher_legal = _edge("dispatcher", "legal_contexts", "legal", "contexts")
    dispatcher_finance = _edge("dispatcher", "finance_contexts", "finance", "contexts")
    legal_join = _edge("legal", "contexts", "joiner", "values")
    finance_join = _edge("finance", "contexts", "joiner", "values")
    join_aggregate = _edge("joiner", "values", "aggregator", "review_contexts")
    spans = [
        _span("root", "contract review", 1_000_000_000, 10_000_000_000, parent=""),
        _span(
            "dispatcher",
            "haystack.component.run",
            2_000_000_000,
            2_100_000_000,
            component="dispatcher",
            inputs=("context",),
            emitted=("context", "legal_contexts", "finance_contexts"),
        ),
        _span(
            "legal",
            "haystack.component.run",
            2_200_000_000,
            4_900_000_000,
            component="legal",
            incoming=(dispatcher_legal,),
            inputs=("contexts",),
            emitted=("contexts",),
        ),
        _span(
            "legal-model",
            "haystack.component.model",
            2_300_000_000,
            4_800_000_000,
            parent="legal",
            attributes={"gen_ai.provider.name": "openai", "gen_ai.request.model": "gpt-5.4"},
        ),
        _span(
            "finance",
            "haystack.component.run",
            2_200_000_001,
            4_000_000_000,
            component="finance",
            incoming=(dispatcher_finance,),
            inputs=("contexts",),
            emitted=("contexts",),
        ),
        _span(
            "finance-model",
            "haystack.component.model",
            2_300_000_001,
            3_900_000_000,
            parent="finance",
            attributes={"gen_ai.provider.name": "openai", "gen_ai.request.model": "gpt-5.4"},
        ),
        _span(
            "joiner",
            "haystack.component.run",
            5_000_000_000,
            5_100_000_000,
            component="joiner",
            incoming=(legal_join, finance_join),
            inputs=("values",),
            emitted=("values",),
        ),
        _span(
            "aggregator",
            "haystack.component.run",
            5_200_000_000,
            5_300_000_000,
            component="aggregator",
            incoming=(join_aggregate,),
            inputs=("review_contexts",),
            emitted=("context",),
        ),
    ]

    graph = normalize_haystack_spans(spans, execution_id="run-topology", runtime_id="haystack")
    replay = derive_replay_graph(graph)
    explicit = {
        (edge.source, edge.target)
        for edge in replay.edges
        if edge.relation == "workflow"
    }

    assert explicit == {
        ("dispatcher", "legal"),
        ("dispatcher", "finance"),
        ("legal", "joiner"),
        ("finance", "joiner"),
        ("joiner", "aggregator"),
    }
    assert ("legal", "finance") not in explicit
    assert not any(node.name in {"security", "business"} for node in replay.nodes)
    assert next(node for node in replay.nodes if node.id == "legal-model").parent_operation_id == "legal"
    assert next(node for node in replay.nodes if node.id == "finance-model").parent_operation_id == "finance"


def test_historical_haystack_spans_without_topology_keep_parent_links() -> None:
    spans = [
        _span("root", "old run", 1_000_000_000, 4_000_000_000, parent=""),
        _span(
            "component",
            "haystack.component.run",
            2_000_000_000,
            3_000_000_000,
            component=None,
            parent="root",
            attributes={"haystack.component.name": "legacy component"},
        ),
    ]

    graph = normalize_haystack_spans(spans, execution_id="run-topology", runtime_id="haystack")

    assert any(
        link.source_id == "root" and link.target_id == "component" and link.relation == "parent"
        for link in graph.links
    )
    assert not any(link.relation == "workflow" for link in graph.links)
