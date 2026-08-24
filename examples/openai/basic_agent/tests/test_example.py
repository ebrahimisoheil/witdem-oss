from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

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
