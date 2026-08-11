"""Load user-supplied Draft 7 schemas and validate generated documents."""

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


def load_validator(schema_path: Path, document_name: str) -> Draft7Validator:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CliError(
            f"cannot read {document_name} schema {schema_path}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise CliError(
            f"malformed {document_name} schema JSON {schema_path}: "
            f"line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

    try:
        Draft7Validator.check_schema(schema)
    except SchemaError as exc:
        raise CliError(
            f"invalid Draft 7 {document_name} schema {schema_path} at "
            f"{json_pointer(exc.absolute_path)}: {exc.message}"
        ) from exc
    return Draft7Validator(schema)


def load_run_log_validator(schema_path: Path) -> Draft7Validator:
    return load_validator(schema_path, "run-log")


def load_meta_data_validator(schema_path: Path) -> Draft7Validator:
    return load_validator(schema_path, "meta-data")


def _error_sort_key(error: ValidationError) -> tuple[str, str, str]:
    return (json_pointer(error.absolute_path), error.validator or "", error.message)


def validate_document(
    document_path: Path,
    schema_path: Path,
    validator: Draft7Validator,
    document_name: str,
    path_label: str = "document",
) -> None:
    try:
        payload: Any = json.loads(document_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CliError(
            f"cannot read generated {document_name} {document_path}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise CliError(
            f"malformed generated {document_name} JSON {document_path}: "
            f"line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc

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
        f"generated {document_name} failed Draft 7 schema validation:\n"
        f"  {path_label}: {document_path}\n"
        f"  schema: {schema_path}\n"
        f"  {count_line}\n"
        f"{details}"
    )


def validate_run_log(
    bundle_path: Path,
    schema_path: Path,
    validator: Draft7Validator,
) -> None:
    validate_document(bundle_path, schema_path, validator, "run log", "bundle")


def validate_meta_data(
    meta_data_path: Path,
    schema_path: Path,
    validator: Draft7Validator,
) -> None:
    validate_document(meta_data_path, schema_path, validator, "meta-data")
