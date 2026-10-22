"""OTLP/HTTP JSON logs payloads, and their reduction to stored events.

Models only the part of `ExportLogsServiceRequest` this app reads; unknown
fields are ignored, as OTLP requires of receivers. Field names follow the
OTLP/JSON mapping (lowerCamelCase), which is why these models alias rather
than use the snake_case of the rest of `models.py`.

Shapes were checked against a real payload from codex-cli 0.147.0 and
against claude's documented events. Two quirks shape the normalization:

* codex puts the event name in the `event.name` attribute and leaves
  `body` empty, and sends `timeUnixNano` as "0" -- only the observed time
  and the `event.timestamp` attribute carry a real time.
* OTLP/JSON encodes int64 as a string, and both CLIs also send many
  numbers as string attributes; values are stored as sent and coerced
  where they are read (see agent_otel).
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from backend.orchestrator.state import AgentEventRow

# Longest string attribute kept. Event attributes are short labels and
# counts; anything longer is a payload (tool output, a prompt someone opted
# into logging) that would bloat the database without helping a comparison.
MAX_ATTRIBUTE_CHARS = 4096

# Event-name prefixes of the CLIs wired up in agent_otel. Used to pick the
# namespaced name when a record carries more than one candidate.
_KNOWN_PREFIXES = ("claude_code.", "codex.")

# Resource attributes copied onto each event, so a stored event says which
# CLI and version produced it without keeping the whole resource.
_RESOURCE_KEYS = ("service.name", "service.version")


class _OtlpModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class AnyValue(_OtlpModel):
    """An OTLP attribute value; exactly one field is set."""

    string_value: str | None = Field(default=None, alias="stringValue")
    bool_value: bool | None = Field(default=None, alias="boolValue")
    int_value: int | str | None = Field(default=None, alias="intValue")
    double_value: float | None = Field(default=None, alias="doubleValue")
    array_value: ArrayValue | None = Field(default=None, alias="arrayValue")
    kvlist_value: KeyValueList | None = Field(default=None, alias="kvlistValue")
    bytes_value: str | None = Field(default=None, alias="bytesValue")


class ArrayValue(_OtlpModel):
    values: list[AnyValue] = Field(default_factory=list)


class KeyValue(_OtlpModel):
    key: str
    value: AnyValue | None = None


class KeyValueList(_OtlpModel):
    values: list[KeyValue] = Field(default_factory=list)


class Resource(_OtlpModel):
    attributes: list[KeyValue] = Field(default_factory=list)


class LogRecord(_OtlpModel):
    time_unix_nano: int | str | None = Field(default=None, alias="timeUnixNano")
    observed_time_unix_nano: int | str | None = Field(default=None, alias="observedTimeUnixNano")
    event_name: str | None = Field(default=None, alias="eventName")
    body: AnyValue | None = None
    attributes: list[KeyValue] = Field(default_factory=list)


class ScopeLogs(_OtlpModel):
    log_records: list[LogRecord] = Field(default_factory=list, alias="logRecords")


class ResourceLogs(_OtlpModel):
    resource: Resource | None = None
    scope_logs: list[ScopeLogs] = Field(default_factory=list, alias="scopeLogs")


class ExportLogsServiceRequest(_OtlpModel):
    """The body of a POST to `/v1/logs`."""

    resource_logs: list[ResourceLogs] = Field(default_factory=list, alias="resourceLogs")


# AnyValue nests through ArrayValue and KeyValueList, which are defined after it.
AnyValue.model_rebuild()


def _plain(value: AnyValue | None) -> object:
    """Converts an AnyValue to a plain JSON value."""
    if value is None:
        return None
    if value.array_value is not None:
        return [_plain(v) for v in value.array_value.values]
    if value.kvlist_value is not None:
        return _attributes(value.kvlist_value.values)
    if value.int_value is not None:
        text = str(value.int_value)
        return int(text) if text.lstrip("-").isdigit() else None
    scalar = next(
        (
            v
            for v in (value.string_value, value.bool_value, value.double_value, value.bytes_value)
            if v is not None
        ),
        None,
    )
    return scalar[:MAX_ATTRIBUTE_CHARS] if isinstance(scalar, str) else scalar


def _attributes(pairs: list[KeyValue]) -> dict[str, object]:
    """Flattens OTLP key/value pairs into a dict."""
    return {pair.key: _plain(pair.value) for pair in pairs}


def _iso_from_nanos(nanos: int | str | None) -> str | None:
    """Converts a unix-nanosecond timestamp to ISO-8601, treating 0 as unset."""
    if nanos is None or not str(nanos).isdigit() or int(nanos) == 0:
        return None
    return dt.datetime.fromtimestamp(int(nanos) / 1e9, tz=dt.UTC).isoformat()


def _event_name(record: LogRecord, attributes: dict[str, object]) -> str:
    """Picks a record's event name from the places the CLIs put it.

    codex sets the `event.name` attribute to the full name; claude's docs
    name events `claude_code.<name>`, carried in the body and the
    attribute. A namespaced candidate wins; otherwise the first present.
    """
    body = record.body.string_value if record.body is not None else None
    candidates = [str(c) for c in (attributes.get("event.name"), body, record.event_name) if c]
    for candidate in candidates:
        if candidate.startswith(_KNOWN_PREFIXES):
            return candidate
    return candidates[0] if candidates else "unknown"


def to_event_rows(request: ExportLogsServiceRequest) -> list[AgentEventRow]:
    """Reduces an export request to the rows the state store keeps.

    Args:
        request: A validated export request.

    Returns:
        One row per log record, in the order received.
    """
    rows: list[AgentEventRow] = []
    for resource_logs in request.resource_logs:
        resource = _attributes(resource_logs.resource.attributes) if resource_logs.resource else {}
        carried = {k: resource[k] for k in _RESOURCE_KEYS if k in resource}
        for scope_logs in resource_logs.scope_logs:
            for record in scope_logs.log_records:
                attributes = carried | _attributes(record.attributes)
                timestamp = attributes.get("event.timestamp")
                rows.append(
                    AgentEventRow(
                        name=_event_name(record, attributes),
                        ts=(
                            timestamp
                            if isinstance(timestamp, str)
                            else _iso_from_nanos(record.time_unix_nano)
                            or _iso_from_nanos(record.observed_time_unix_nano)
                        ),
                        attributes=attributes,
                    )
                )
    return rows
