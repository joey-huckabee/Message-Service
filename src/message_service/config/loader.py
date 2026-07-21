"""Configuration loader.

Reads a TOML file, applies ``${env:VAR}`` substitution to marked string
fields, resolves relative paths against the config-file directory,
validates against :class:`message_service.config.schema.Config`, and
returns the frozen model.

Three error types surface from this module:

* :class:`ConfigurationError` — file-not-found, unreadable, TOML parse
  failure, missing env var referenced by ``${env:VAR}`` (L3-CFG-015,
  L3-CFG-013).
* :class:`pydantic.ValidationError` — schema violations (wrong type,
  out-of-range numeric, forbidden extra keys, etc.). The caller is
  expected to format this via :func:`format_validation_errors` into
  ``[N] <path>: <message>`` lines and exit (L3-CFG-007, L3-CFG-008).
* :class:`FileNotFoundError` — raised directly by ``Path.read_bytes`` if
  the file vanishes between the is_file check and the open; wrapped as
  ``ConfigurationError`` before surfacing.

Requirement references
----------------------
L2-CFG-003, L2-CFG-005, L2-CFG-007, L2-CFG-008
L3-CFG-004, L3-CFG-007, L3-CFG-008, L3-CFG-010, L3-CFG-011,
L3-CFG-012, L3-CFG-013, L3-CFG-014, L3-CFG-015
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from message_service.config.schema import (
    SUBSTITUTABLE_MARKER,
    Config,
)
from message_service.domain.errors import ConfigurationError

# Pattern for ${env:VAR_NAME} substitution. VAR_NAME must match the
# conventional POSIX env-var grammar: letter/underscore then
# alphanumerics/underscores.
_ENV_PATTERN = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}")

# Fields in the schema that resolve relative paths. Declared here rather
# than scanning the model tree so the resolution list is auditable and
# matches L3-CFG-011 exactly.
_PATH_FIELDS: tuple[tuple[str, ...], ...] = (
    ("persistence", "sqlite_path"),
    ("persistence", "filesystem", "report_directory"),
    ("templates", "manifest_path"),
    ("tags", "vocabulary_path"),
    # Optional (audit archival, L3-OBS-041); skipped when the section/key is
    # absent by _resolve_one.
    ("observability", "audit", "archive_directory"),
)


def load_config(config_path: Path | str) -> Config:
    """Load, resolve, and validate a config file.

    Args:
        config_path: Filesystem path to a TOML config file. Relative
            paths within the file are resolved against this file's
            parent directory.

    Returns:
        A frozen :class:`Config` instance.

    Raises:
        ConfigurationError: Missing file, unreadable file, TOML parse
            failure, missing env var, or any other loader-layer
            problem.
        pydantic.ValidationError: Schema validation failure. Callers
            should format this with :func:`format_validation_errors`.
    """
    path = Path(config_path).resolve(strict=False)

    # L3-CFG-015: file exists and is readable before we try to parse.
    if not path.is_file():
        raise ConfigurationError(
            f"config file not found: {path}",
            details={"config_path": str(path)},
        )

    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise ConfigurationError(
            f"config file unreadable: {path}: {exc}",
            details={"config_path": str(path), "reason": str(exc)},
        ) from exc

    try:
        raw = tomllib.loads(raw_bytes.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(
            f"config file is not valid TOML: {path}: {exc}",
            details={"config_path": str(path), "reason": str(exc)},
        ) from exc
    except UnicodeDecodeError as exc:
        raise ConfigurationError(
            f"config file is not valid UTF-8: {path}",
            details={"config_path": str(path), "reason": str(exc)},
        ) from exc

    # Apply transformations (order matters):
    # 1. Env-var substitution on every string leaf of SubstitutableStr fields.
    # 2. Path resolution on the fixed list of _PATH_FIELDS.
    substituted = _substitute_env_vars(raw)
    resolved = _resolve_paths(substituted, base_dir=path.parent)

    # L2-CFG-005: Pydantic validation.
    return Config.model_validate(resolved)


def format_validation_errors(exc: ValidationError) -> str:
    """Format a Pydantic ValidationError for stderr (L3-CFG-007, L3-CFG-008).

    Produces one line per failure in the form::

        [1] /path/to.field: message
        [2] /other/field: message

    The leading whitespace and the bracketed index make the output
    trivially machine-grep-friendly for test assertions.

    Args:
        exc: The ValidationError raised by ``Config.model_validate``.

    Returns:
        The formatted multi-line string (no trailing newline).
    """
    lines: list[str] = []
    for idx, err in enumerate(exc.errors(), start=1):
        # err['loc'] is a tuple of field names / indices from the root.
        json_pointer = "/" + "/".join(str(part) for part in err["loc"])
        lines.append(f"  [{idx}] {json_pointer}: {err['msg']}")
    return "\n".join(lines)


def print_validation_errors(exc: ValidationError) -> None:
    """Print formatted validation errors to stderr (L3-CFG-007).

    Convenience for CLI callers. Prints a header and the formatted
    failure list, then returns. Does not call ``sys.exit``.
    """
    print(
        f"Configuration invalid ({len(exc.errors())} error"
        f"{'s' if len(exc.errors()) != 1 else ''}):",
        file=sys.stderr,
    )
    print(format_validation_errors(exc), file=sys.stderr)


# -----------------------------------------------------------------------------
# Env-var substitution (L3-CFG-012, L3-CFG-013, L3-CFG-014)
# -----------------------------------------------------------------------------


def _substitute_env_vars(data: dict[str, Any]) -> dict[str, Any]:
    """Walk the config dict and substitute ``${env:VAR}`` in marked fields.

    Walks the :class:`message_service.config.schema.Config` model tree
    to find every field typed as :data:`SubstitutableStr`, then
    substitutes only in those fields' values. Other string fields pass
    through unchanged (L3-CFG-014).

    Args:
        data: The raw dict from TOML parsing.

    Returns:
        A new dict with substitutions applied.

    Raises:
        ConfigurationError: If a referenced environment variable is
            unset.
    """
    substitutable_paths = _collect_substitutable_paths()
    result = _substitute_at_paths(data, substitutable_paths, path=())
    # The root is always a dict (enforced by the caller), but
    # _substitute_at_paths returns Any because it recurses into mixed
    # container types. Narrow here for the type checker.
    if not isinstance(result, dict):
        raise ConfigurationError(
            "config root is not a TOML table after env substitution",
            details={"actual_type": type(result).__name__},
        )
    return result


def _collect_substitutable_paths() -> frozenset[tuple[str, ...]]:
    """Compute the set of field paths declared as :data:`SubstitutableStr`.

    Walks the Config model tree via pydantic's ``model_fields`` metadata.
    A field is substitutable when its ``metadata`` list contains the
    :data:`message_service.config.schema.SUBSTITUTABLE_MARKER` string
    (Pydantic unwraps ``Annotated[str, "substitutable"]`` into
    ``annotation=str, metadata=['substitutable']``).

    Returns a frozen set of tuples, each representing the dotted path to
    a ``SubstitutableStr`` field.
    """
    from typing import get_args

    from pydantic import BaseModel as _BaseModel  # local import to keep top clean

    def _model_members(annot: object) -> list[type[_BaseModel]]:
        """Return the BaseModel classes reachable from an annotation.

        Handles a bare model type *and* models wrapped in a union — e.g. an
        optional nested section typed ``AdminAccountConfig | None`` — by
        unwrapping the union and returning its BaseModel members. Without the
        union case, ``SubstitutableStr`` fields under an ``Optional[...]``
        section (like ``auth.admin.password``) would never be discovered.
        """
        if isinstance(annot, type) and issubclass(annot, _BaseModel):
            return [annot]
        return [a for a in get_args(annot) if isinstance(a, type) and issubclass(a, _BaseModel)]

    def _walk(model_cls: type[_BaseModel], prefix: tuple[str, ...]) -> list[tuple[str, ...]]:
        found: list[tuple[str, ...]] = []
        for name, field in model_cls.model_fields.items():
            if SUBSTITUTABLE_MARKER in field.metadata:
                found.append((*prefix, name))
                continue
            for member in _model_members(field.annotation):
                found.extend(_walk(member, (*prefix, name)))
        return found

    return frozenset(_walk(Config, ()))


def _substitute_at_paths(
    value: Any,
    paths: frozenset[tuple[str, ...]],
    path: tuple[str, ...],
) -> Any:
    """Recursively walk ``value``, substituting at any matching path."""
    if isinstance(value, dict):
        return {
            key: _substitute_at_paths(subvalue, paths, (*path, key))
            for key, subvalue in value.items()
        }
    if isinstance(value, list):
        # Lists of substitutable strings are uncommon but we support them
        # for symmetry.
        if path in paths:
            return [_apply_substitution(item) if isinstance(item, str) else item for item in value]
        return value
    if path in paths and isinstance(value, str):
        return _apply_substitution(value)
    return value


def _apply_substitution(value: str) -> str:
    """Apply ``${env:VAR}`` substitution to ``value``.

    Raises:
        ConfigurationError: If a referenced variable is unset.
    """

    def replacer(match: re.Match[str]) -> str:
        var_name = match.group(1)
        env_value = os.environ.get(var_name)
        if env_value is None:
            raise ConfigurationError(
                f"environment variable not set: {var_name}",
                details={"env_var": var_name, "raw_value": value},
            )
        return env_value

    return _ENV_PATTERN.sub(replacer, value)


# -----------------------------------------------------------------------------
# Path resolution (L3-CFG-010, L3-CFG-011)
# -----------------------------------------------------------------------------


def _resolve_paths(
    data: dict[str, Any],
    *,
    base_dir: Path,
) -> dict[str, Any]:
    """Resolve relative path fields against ``base_dir``.

    Only the fields listed in :data:`_PATH_FIELDS` are resolved. Absolute
    paths in the TOML pass through unchanged.

    Args:
        data: The raw dict (post-substitution).
        base_dir: The config file's parent directory.

    Returns:
        A new dict with paths resolved at the declared locations.
    """
    out: dict[str, Any] = dict(data.items())
    for field_path in _PATH_FIELDS:
        _resolve_one(out, field_path, base_dir)
    return out


def _resolve_one(
    data: dict[str, Any],
    field_path: tuple[str, ...],
    base_dir: Path,
) -> None:
    """Resolve a single path at ``field_path`` inside ``data``, in place.

    Does nothing if any intermediate key is missing (the config may
    simply not declare this section; validation will catch the
    omission).
    """
    cursor: Any = data
    for key in field_path[:-1]:
        if not isinstance(cursor, dict) or key not in cursor:
            return
        cursor = cursor[key]

    last_key = field_path[-1]
    if not isinstance(cursor, dict) or last_key not in cursor:
        return

    raw_value = cursor[last_key]
    if not isinstance(raw_value, str):
        return

    candidate = Path(raw_value)
    if candidate.is_absolute():
        cursor[last_key] = str(candidate)
    else:
        cursor[last_key] = str((base_dir / candidate).resolve(strict=False))


__all__ = [
    "format_validation_errors",
    "load_config",
    "print_validation_errors",
]
