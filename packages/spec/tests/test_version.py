from stratum_spec import SCHEMA_VERSION


def test_schema_version_is_positive_int() -> None:
    assert isinstance(SCHEMA_VERSION, int)
    assert SCHEMA_VERSION >= 1
