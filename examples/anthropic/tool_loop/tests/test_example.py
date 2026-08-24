from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace

_MODULE = module_from_spec(spec_from_file_location("example_run", Path(__file__).parents[1] / "run.py"))
assert _MODULE.__spec__ is not None
_MODULE.__spec__.loader.exec_module(_MODULE)


def test_import() -> None:
    assert _MODULE.EXAMPLE_NAME


def test_uses_unified_sdk_setup() -> None:
    assert callable(_MODULE.configure)


def test_telemetry_smoke() -> None:
    result = _MODULE.telemetry_smoke()
    assert result == {"executions": 1, "operations": 1}


def test_tool_loop_uses_anthropic_tool_use_ids() -> None:
    response = SimpleNamespace(
        content=[
            SimpleNamespace(type="tool_use", id="toolu_real_123", name="search_orders", input={}),
            SimpleNamespace(type="text", text="ignored"),
        ]
    )
    tool_uses = _MODULE._tool_uses(response)
    assert len(tool_uses) == 1
    assert tool_uses[0].id == "toolu_real_123"
