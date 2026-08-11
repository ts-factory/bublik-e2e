from __future__ import annotations

import json

import pytest

from core.common import CliError
from core.run_log_schema import (
    load_meta_data_validator,
    load_run_log_validator,
    validate_meta_data,
    validate_run_log,
)


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


def test_meta_data_document_errors_name_document_and_schema(tmp_path) -> None:
    schema_path = tmp_path / "meta-data.schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "required": ["version"],
            }
        )
    )
    meta_data_path = tmp_path / "bundle" / "meta_data.json"
    meta_data_path.parent.mkdir()
    meta_data_path.write_text("{}")

    with pytest.raises(CliError) as excinfo:
        validate_meta_data(
            meta_data_path,
            schema_path,
            load_meta_data_validator(schema_path),
        )

    message = str(excinfo.value)
    assert "generated meta-data failed Draft 7 schema validation" in message
    assert f"document: {meta_data_path}" in message
    assert f"schema: {schema_path}" in message


def test_malformed_generated_meta_data_is_reported_clearly(tmp_path) -> None:
    schema_path = tmp_path / "meta-data.schema.json"
    schema_path.write_text("{}")
    meta_data_path = tmp_path / "meta_data.json"
    meta_data_path.write_text("{")

    with pytest.raises(CliError, match="malformed generated meta-data JSON"):
        validate_meta_data(
            meta_data_path,
            schema_path,
            load_meta_data_validator(schema_path),
        )
