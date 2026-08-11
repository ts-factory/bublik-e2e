"""Load a user-supplied Draft 7 schema and validate generated run logs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError, ValidationError

from core.common import CliError

MAX_VALIDATION_ERRORS = 20


def json_pointer(path: Iterable[object]) -> str:
    parts = [str(part).replace("~", "~0").replace("/", "~1") for part in path]
    return "/" + "/".join(parts) if parts else "/"


def load_run_log_validator(schema_path: Path) -> Draft7Validator:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CliError(f"cannot read run-log schema {schema_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CliError(
            f"malformed run-log schema JSON {schema_path}: "
            f"line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

    try:
        Draft7Validator.check_schema(schema)
    except SchemaError as exc:
        raise CliError(
            f"invalid Draft 7 run-log schema {schema_path} at "
            f"{json_pointer(exc.absolute_path)}: {exc.message}"
        ) from exc
    return Draft7Validator(schema)


def _error_sort_key(error: ValidationError) -> tuple[str, str, str]:
    return (json_pointer(error.absolute_path), error.validator or "", error.message)


def validate_run_log(
    bundle_path: Path,
    schema_path: Path,
    validator: Draft7Validator,
) -> None:
    try:
        payload: Any = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CliError(f"cannot read generated run log {bundle_path}: {exc}") from exc

    errors = sorted(validator.iter_errors(payload), key=_error_sort_key)
    if not errors:
        return

    shown = errors[:MAX_VALIDATION_ERRORS]
    count_line = f"errors: {len(errors)}"
    if len(errors) > len(shown):
        count_line += f" (showing first {len(shown)})"
    details = "\n".join(
        f"  - {json_pointer(error.absolute_path)}: {error.message}" for error in shown
    )
    raise CliError(
        "generated run log failed Draft 7 schema validation:\n"
        f"  bundle: {bundle_path}\n"
        f"  schema: {schema_path}\n"
        f"  {count_line}\n"
        f"{details}"
    )
