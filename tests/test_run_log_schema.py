from __future__ import annotations

import json

import pytest

from core.common import CliError
from core.run_log_schema import load_run_log_validator, validate_run_log


def test_validation_errors_are_deterministic_and_capped(tmp_path) -> None:
    required = [f"missing-{index:02}" for index in range(25)]
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "required": required,
            }
        )
    )
    bundle_path = tmp_path / "bundle" / "bublik.json"
    bundle_path.parent.mkdir()
    bundle_path.write_text("{}")
    validator = load_run_log_validator(schema_path)

    messages = []
    for _ in range(2):
        with pytest.raises(CliError) as excinfo:
            validate_run_log(bundle_path, schema_path, validator)
        messages.append(str(excinfo.value))

    assert messages[0] == messages[1]
    assert f"bundle: {bundle_path}" in messages[0]
    assert f"schema: {schema_path}" in messages[0]
    assert "errors: 25 (showing first 20)" in messages[0]
    assert messages[0].count("  - /:") == 20
