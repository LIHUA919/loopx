from __future__ import annotations

import copy
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, TypeVar

from ...file_lock import exclusive_cross_runtime_file_lock
from ...paths import GLOBAL_REGISTRY_FILENAME


STRICT_SCHEMA_VERSION = "loopx_project_registry_envelope_v1"
CURRENT_WRITER_PROTOCOL = "goal_instance_v1"
_STRICT_HEADER_KEYS = {
    "schema_version",
    "minimum_writer_protocol",
    "payload_sha256",
}
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

T = TypeVar("T")


class ProjectRegistryError(ValueError):
    """Base error for an invalid or unsafe project registry operation."""


class ProjectRegistryProtocolError(ProjectRegistryError):
    """The registry requires a writer protocol this package does not support."""


class ProjectRegistryMutationError(ProjectRegistryError):
    """A registry replacement could not be verified and was compensated."""


class ProjectRegistryRestoreError(ProjectRegistryMutationError):
    """Exact-byte compensation could not be verified."""


class _ProjectRegistryFormat(str, Enum):
    LEGACY_OBJECT = "legacy_object_v0"
    STRICT_ENVELOPE = "strict_envelope_v1"


@dataclass(frozen=True, slots=True)
class _ProjectRegistryDocument:
    payload: dict[str, Any]
    format: _ProjectRegistryFormat
    minimum_writer_protocol: str | None
    raw_bytes: bytes


def _duplicate_rejecting_object(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProjectRegistryError(
                f"strict project registry contains duplicate key: {key}"
            )
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ProjectRegistryError(
        f"strict project registry contains non-finite number: {value}"
    )


def _payload_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _decode_document(raw_bytes: bytes) -> _ProjectRegistryDocument:
    text = raw_bytes.decode("utf-8")
    stripped = text.lstrip()
    if not stripped:
        raise ProjectRegistryError("project registry is empty")

    if stripped.startswith("{"):
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ProjectRegistryError(
                "legacy project registry root must be a JSON object"
            )
        return _ProjectRegistryDocument(
            payload=payload,
            format=_ProjectRegistryFormat.LEGACY_OBJECT,
            minimum_writer_protocol=None,
            raw_bytes=raw_bytes,
        )

    if not stripped.startswith("["):
        raise ProjectRegistryError(
            "project registry root must be a JSON object or strict envelope"
        )

    root = json.loads(
        text,
        object_pairs_hook=_duplicate_rejecting_object,
        parse_constant=_reject_non_finite,
    )
    if not isinstance(root, list) or len(root) != 2:
        raise ProjectRegistryError(
            "strict project registry envelope must contain exactly two elements"
        )
    header, payload = root
    if not isinstance(header, dict) or set(header) != _STRICT_HEADER_KEYS:
        raise ProjectRegistryError(
            "strict project registry header must contain exactly "
            "schema_version, minimum_writer_protocol, and payload_sha256"
        )
    if header["schema_version"] != STRICT_SCHEMA_VERSION:
        raise ProjectRegistryError(
            "strict project registry schema_version is unsupported"
        )
    protocol = header["minimum_writer_protocol"]
    if not isinstance(protocol, str) or not protocol:
        raise ProjectRegistryError(
            "strict project registry minimum_writer_protocol must be nonempty"
        )
    digest = header["payload_sha256"]
    if not isinstance(digest, str) or not _SHA256_PATTERN.fullmatch(digest):
        raise ProjectRegistryError(
            "strict project registry payload_sha256 is malformed"
        )
    if not isinstance(payload, dict):
        raise ProjectRegistryError(
            "strict project registry payload must be a JSON object"
        )
    try:
        actual_digest = _payload_digest(payload)
    except (TypeError, ValueError) as exc:
        raise ProjectRegistryError(
            "strict project registry payload is not canonical JSON"
        ) from exc
    if digest != actual_digest:
        raise ProjectRegistryError(
            "strict project registry payload digest does not match"
        )
    return _ProjectRegistryDocument(
        payload=payload,
        format=_ProjectRegistryFormat.STRICT_ENVELOPE,
        minimum_writer_protocol=protocol,
        raw_bytes=raw_bytes,
    )


def _read_document(path: Path) -> _ProjectRegistryDocument:
    return _decode_document(path.read_bytes())


def load_project_registry(path: Path) -> dict[str, Any]:
    """Load either supported project-registry wire format."""

    return _read_document(path.expanduser()).payload


def decode_project_registry(raw_bytes: bytes) -> dict[str, Any]:
    """Decode one project-registry snapshot without rereading its path."""

    return _decode_document(raw_bytes).payload


def decode_registry_snapshot(path: Path, raw_bytes: bytes) -> dict[str, Any]:
    """Decode one registry snapshot while keeping global registries object-only."""

    if path.expanduser().name == GLOBAL_REGISTRY_FILENAME:
        payload = json.loads(raw_bytes)
        if not isinstance(payload, dict):
            raise ProjectRegistryError("global registry root must be a JSON object")
        return payload
    return decode_project_registry(raw_bytes)


def load_registry(path: Path) -> dict[str, Any]:
    """Load a project registry or an object-only global registry."""

    expanded = path.expanduser()
    if not expanded.exists():
        return {}
    return decode_registry_snapshot(expanded, expanded.read_bytes())


def _encode_document(
    payload: dict[str, Any],
    *,
    format: _ProjectRegistryFormat,
    minimum_writer_protocol: str | None,
) -> bytes:
    if not isinstance(payload, dict):
        raise TypeError("project registry payload must be a JSON object")
    if format is _ProjectRegistryFormat.LEGACY_OBJECT:
        root: object = payload
        allow_nan = True
    else:
        root = [
            {
                "schema_version": STRICT_SCHEMA_VERSION,
                "minimum_writer_protocol": minimum_writer_protocol,
                "payload_sha256": _payload_digest(payload),
            },
            payload,
        ]
        allow_nan = False
    return (
        json.dumps(
            root,
            ensure_ascii=False,
            allow_nan=allow_nan,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _atomic_write_bytes(path: Path, payload: bytes, *, mode: int | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _require_supported_writer(document: _ProjectRegistryDocument) -> None:
    protocol = document.minimum_writer_protocol
    if (
        document.format is _ProjectRegistryFormat.STRICT_ENVELOPE
        and protocol != CURRENT_WRITER_PROTOCOL
    ):
        raise ProjectRegistryProtocolError(
            "project registry requires unsupported writer protocol: "
            f"{protocol}"
        )


class ProjectRegistryTransaction:
    def __init__(
        self,
        path: Path,
        *,
        document: _ProjectRegistryDocument,
        existed: bool,
        mode: int | None,
    ) -> None:
        self._path = path
        self._document = document
        self._existed = existed
        self._mode = mode
        self._finished = False

    def payload_copy(self) -> dict[str, Any]:
        return copy.deepcopy(self._document.payload)

    def commit(self, payload: dict[str, Any]) -> bool:
        if self._finished:
            raise RuntimeError("project registry transaction is already finished")
        if not isinstance(payload, dict):
            raise TypeError("project registry payload must be a JSON object")
        wrote = not self._existed or payload != self._document.payload
        if not wrote:
            self._finished = True
            return False

        encoded = _encode_document(
            payload,
            format=self._document.format,
            minimum_writer_protocol=self._document.minimum_writer_protocol,
        )
        try:
            _atomic_write_bytes(self._path, encoded, mode=self._mode)
            readback = _read_document(self._path)
            if (
                readback.format is not self._document.format
                or readback.minimum_writer_protocol
                != self._document.minimum_writer_protocol
                or readback.payload != payload
            ):
                raise ProjectRegistryMutationError(
                    "project registry readback did not match committed payload"
                )
        except Exception as exc:
            try:
                self.restore()
            except Exception as restore_exc:
                raise ProjectRegistryRestoreError(
                    "project registry readback failed and exact-byte restore failed"
                ) from restore_exc
            raise ProjectRegistryMutationError(
                "project registry write readback failed; exact preimage restored"
            ) from exc
        self._finished = True
        return True

    def remove(self) -> bool:
        if self._finished:
            raise RuntimeError("project registry transaction is already finished")
        if not self._path.exists():
            self._finished = True
            return False
        self._path.unlink()
        if self._path.exists():
            raise ProjectRegistryMutationError(
                "project registry removal readback failed"
            )
        self._finished = True
        return True

    def restore(self) -> None:
        if self._existed:
            _atomic_write_bytes(
                self._path,
                self._document.raw_bytes,
                mode=self._mode,
            )
            if self._path.read_bytes() != self._document.raw_bytes:
                raise ProjectRegistryRestoreError(
                    "project registry exact-byte restore did not verify"
                )
        else:
            self._path.unlink(missing_ok=True)
            if self._path.exists():
                raise ProjectRegistryRestoreError(
                    "new project registry could not be removed during restore"
                )
        self._finished = True


@contextmanager
def project_registry_transaction(
    path: Path,
    *,
    operation: str,
    create: Callable[[], dict[str, Any]] | None = None,
    agent_id: str | None = None,
) -> Iterator[ProjectRegistryTransaction]:
    """Hold one project-registry lock for a compound owner transaction."""

    expanded = path.expanduser()
    with exclusive_cross_runtime_file_lock(
        expanded,
        agent_id=agent_id,
        operation=operation,
    ):
        existed = expanded.exists()
        if existed:
            document = _read_document(expanded)
            mode = expanded.stat().st_mode & 0o777
        else:
            if create is None:
                raise FileNotFoundError(
                    f"registry file does not exist: {expanded}"
                )
            payload = create()
            if not isinstance(payload, dict):
                raise TypeError(
                    "project registry initializer must return a JSON object"
                )
            document = _ProjectRegistryDocument(
                payload=copy.deepcopy(payload),
                format=_ProjectRegistryFormat.LEGACY_OBJECT,
                minimum_writer_protocol=None,
                raw_bytes=b"",
            )
            mode = None
        _require_supported_writer(document)
        yield ProjectRegistryTransaction(
            expanded,
            document=document,
            existed=existed,
            mode=mode,
        )


def mutate_project_registry(
    path: Path,
    *,
    operation: str,
    reducer: Callable[[dict[str, Any]], T],
    create: Callable[[], dict[str, Any]] | None = None,
    agent_id: str | None = None,
) -> T:
    """Apply one locked, format-preserving project-registry reduction."""

    with project_registry_transaction(
        path,
        operation=operation,
        create=create,
        agent_id=agent_id,
    ) as transaction:
        payload = transaction.payload_copy()
        result = reducer(payload)
        transaction.commit(payload)
        return result


def add_project_registry_backend(path: Path, backend: str) -> bool:
    """Add one backend marker without exposing the registry wire format."""

    normalized = str(backend or "").strip()
    if not normalized:
        raise ValueError("project registry backend is required")

    def reduce(payload: dict[str, Any]) -> bool:
        backends = payload.setdefault("agent_backends", [])
        if not isinstance(backends, list):
            raise ValueError("project registry agent_backends must be a list")
        if normalized in backends:
            return False
        backends.append(normalized)
        return True

    return mutate_project_registry(
        path,
        operation=f"add_project_registry_backend_{normalized}",
        reducer=reduce,
    )
