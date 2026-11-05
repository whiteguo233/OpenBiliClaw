"""Event provenance at the cognition prompt boundary.

``events.created_at`` records when the harness *wrote* a row, not when the user
*did* the thing. Account sync commits a page of rows in one transaction, so a
first import lands hundreds of rows on the same second — a real local ledger
held 89,866 bilibili views inside 950 distinct seconds and 2,429 follows inside
93. Reading that column as event time manufactures behaviour that never
happened: "followed 40 creators in one second", "switched topics five times in
a minute", "same day cross-domain scanning". Those claims then reach the
portrait and are restated to the user as if observed.

Producers that know the real moment write ``metadata.timestamp`` (``view_at``
for history, ``fav_time`` for favourites, ``mtime`` for follows). This module
is the read side, applied once at the prompt boundary:

* :func:`apply_event_provenance` never shows the model an ingest timestamp as
  if it were event time — ``created_at`` is dropped and rows without a source
  timestamp are labelled ``time_quality="ingest"`` (meaning: time unknown).
* :func:`collapse_snapshot_creators` folds a first-import follow batch into one
  inventory row instead of N "followed X" behaviour lines. Creator names stay
  in ``metadata.creators`` so creator affinity is still learnable.

Both functions are pure: caller-owned ledger rows are never mutated.

Evidence policy: this file must stay free of source-specific branches. A
producer marks a batch with ``metadata.snapshot`` or ``metadata.import_source``;
everything else is inferred from the presence of a real timestamp.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from openbiliclaw.sources.event_format import parse_event_timestamp

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# Producer-side markers. ``snapshot`` is an explicit "this batch is an account
# inventory import, not live behaviour" flag; ``import_source`` already exists
# across bootstrap producers (``dy_bootstrap_follow``, ``yt_bootstrap_*``, ...)
# and carries the same meaning, so both are honoured.
SNAPSHOT_METADATA_KEY = "snapshot"
IMPORT_SOURCE_METADATA_KEY = "import_source"
TIMESTAMP_METADATA_KEY = "timestamp"
TIME_QUALITY_METADATA_KEY = "time_quality"
SNAPSHOT_TOTAL_METADATA_KEY = "snapshot_total"
CREATORS_METADATA_KEY = "creators"

# Only creator-shaped subscriptions are collapsed. Favourites stay individual
# rows: their titles are the interest signal the portrait needs.
CREATOR_SNAPSHOT_EVENT_TYPES = frozenset({"follow", "subscribe", "subscription"})

# ``source`` — the row carries the real moment the user acted.
# ``ingest`` — only the import moment is known; the model must not read a time.
# ``snapshot`` — a collapsed inventory row; its window lives in first_at/last_at.
TIME_QUALITY_SOURCE = "source"
TIME_QUALITY_INGEST = "ingest"
TIME_QUALITY_SNAPSHOT = "snapshot"

# Cap on creator names carried into one collapsed row. Prompt-bound economy
# only: 100 names is already far more than any single prompt needs to judge
# creator affinity, and the full list stays in the ledger.
CREATOR_SNAPSHOT_NAMES_CAP = 100


def event_metadata(event: Mapping[str, Any]) -> dict[str, Any]:
    """Return an event's metadata as a dict, parsing the JSON-string form.

    ``Database.query_events`` returns ``metadata`` as a raw JSON string while
    freshly built events carry a dict. Both reach prompt builders.
    """
    raw = event.get("metadata")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def event_type_of(event: Mapping[str, Any]) -> str:
    """Return the canonical lowercase event type (``event_type`` or legacy ``type``)."""
    return str(event.get("event_type") or event.get("type") or "").strip().lower()


def event_source_platform(event: Mapping[str, Any]) -> str:
    """Return the event's source platform, preferring the top-level field."""
    metadata = event_metadata(event)
    return (
        str(event.get("source_platform") or metadata.get("source_platform") or "").strip().lower()
    )


def has_source_time(event: Mapping[str, Any]) -> bool:
    """Whether the row carries the moment the user actually acted."""
    if parse_event_timestamp(event_metadata(event)) is not None:
        return True
    # A few older producers wrote the epoch top-level instead of in metadata.
    for key in ("timestamp", "occurred_at", "view_at", "fav_time", "mtime"):
        raw = event.get(key)
        if isinstance(raw, bool) or not isinstance(raw, int | float | str):
            continue
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            continue
        if seconds > 0:
            return True
    return False


def is_snapshot_event(event: Mapping[str, Any]) -> bool:
    """Whether the row belongs to an account-inventory import.

    ``event_namespace=import`` alone is deliberately *not* sufficient: account
    sync stamps that namespace on incremental rows too, and a follow the user
    made this week is real behaviour even though a sync job wrote it down.

    The legacy branch exists because rows imported before this contract was
    fixed carry the import namespace and no source time at all. They cannot be
    dated, so the honest reading is "inventory of unknown age" — collapsing
    them is what stops a pre-fix ledger from replaying the same 40-per-second
    burst. A genuinely new follow from steady-state sync now carries ``mtime``
    and therefore skips this branch.
    """
    metadata = event_metadata(event)
    if metadata.get(SNAPSHOT_METADATA_KEY) is True:
        return True
    if str(metadata.get(IMPORT_SOURCE_METADATA_KEY) or "").strip():
        return True
    namespace = str(metadata.get("event_namespace") or "").strip().lower()
    return namespace in {"import", "source_import"} and not has_source_time(event)


def _epoch_to_iso(value: float) -> str:
    seconds = value / 1000.0 if abs(value) >= 1e11 else value
    return datetime.fromtimestamp(seconds, UTC).isoformat()


def _event_epoch(event: Mapping[str, Any]) -> float:
    """Return the event's real epoch seconds, or 0.0 when unknown."""
    metadata = event_metadata(event)
    parsed = parse_event_timestamp(metadata)
    if parsed is not None:
        return parsed.timestamp()
    for key in ("timestamp", "occurred_at", "view_at", "fav_time", "mtime"):
        raw = event.get(key)
        if isinstance(raw, bool) or not isinstance(raw, int | float | str):
            continue
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            continue
        if seconds > 0:
            return seconds / 1000.0 if abs(seconds) >= 1e11 else seconds
    return 0.0


def _creator_name(event: Mapping[str, Any]) -> str:
    metadata = event_metadata(event)
    for value in (
        metadata.get("up_name"),
        metadata.get("author"),
        event.get("title"),
    ):
        name = str(value or "").strip()
        if name:
            return name
    return ""


def apply_event_provenance(
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Project ledger rows into prompt-shape rows with honest time provenance.

    ``created_at`` is always removed: it is the write moment, and the only way
    a model can honour that is to never see it.

    A real interaction time that a producer parked under a platform-private key
    (``interaction_time`` / ``watched_at`` / ``view_at`` …) is promoted into
    ``metadata.timestamp``, so the prompt's "timestamp is the moment" contract
    holds for every source instead of silently treating those rows as undated.
    Rows with no interaction time are labelled ``time_quality="ingest"`` so the
    prompt rule "no timestamp means time unknown" has something to bind to.
    """
    projected: list[dict[str, Any]] = []
    for event in events:
        row = dict(event)
        metadata = dict(event_metadata(event))
        row.pop("created_at", None)
        source_epoch = _event_epoch(event)
        if source_epoch > 0:
            if not metadata.get(TIMESTAMP_METADATA_KEY):
                # Promote the private key so the prompt sees one canonical time
                # field regardless of which producer wrote the row.
                metadata[TIMESTAMP_METADATA_KEY] = _epoch_to_iso(source_epoch)
            metadata.setdefault(TIME_QUALITY_METADATA_KEY, TIME_QUALITY_SOURCE)
        else:
            metadata.setdefault(TIME_QUALITY_METADATA_KEY, TIME_QUALITY_INGEST)
        row["metadata"] = metadata or None
        projected.append(row)
    return projected


def collapse_snapshot_creators(
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Fold a first-import follow batch into one inventory row per source.

    A single "followed X" line is behaviour evidence; five hundred of them from
    one import is an account inventory, and presenting it as behaviour is what
    produced "batch-followed forty creators in one day". The collapsed row keeps
    the creator names (``metadata.creators``) and the real follow window
    (``first_at`` / ``last_at``) so affinity and timeframe survive.

    Groups of fewer than two rows are left untouched: one newly followed
    creator during steady-state sync is a genuine action.
    """
    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for event in events:
        if not is_snapshot_event(event):
            continue
        event_type = event_type_of(event)
        if event_type not in CREATOR_SNAPSHOT_EVENT_TYPES:
            continue
        groups.setdefault((event_type, event_source_platform(event)), []).append(event)

    collapsible = {key: rows for key, rows in groups.items() if len(rows) >= 2}
    if not collapsible:
        return [dict(event) for event in events]

    emitted: set[tuple[str, str]] = set()
    collapsed: list[dict[str, Any]] = []
    for event in events:
        key = (event_type_of(event), event_source_platform(event))
        if key not in collapsible:
            collapsed.append(dict(event))
            continue
        if key in emitted:
            continue
        emitted.add(key)
        collapsed.append(_snapshot_creator_row(key, collapsible[key]))
    return collapsed


def _snapshot_creator_row(
    key: tuple[str, str],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    event_type, source_platform = key
    total = len(rows)
    # An unfollowed creator must not re-enter the affinity list through the
    # collapsed inventory row: the retraction discount only existed on the
    # individual row the collapse replaced.
    retracted = [row for row in rows if bool(event_metadata(row).get("retracted"))]
    kept = [row for row in rows if not bool(event_metadata(row).get("retracted"))]
    names = [name for name in (_creator_name(row) for row in kept) if name]
    epochs = sorted(epoch for epoch in (_event_epoch(row) for row in rows) if epoch > 0)

    metadata: dict[str, Any] = {
        SNAPSHOT_METADATA_KEY: True,
        SNAPSHOT_TOTAL_METADATA_KEY: total,
        TIME_QUALITY_METADATA_KEY: TIME_QUALITY_SNAPSHOT,
        # Kept so downstream lanes that key on import provenance still see it.
        IMPORT_SOURCE_METADATA_KEY: "account_snapshot",
    }
    if retracted:
        metadata["retracted_total"] = len(retracted)
    if names:
        metadata[CREATORS_METADATA_KEY] = names[:CREATOR_SNAPSHOT_NAMES_CAP]
    if epochs:
        metadata["first_at"] = _epoch_to_iso(epochs[0])
        metadata["last_at"] = _epoch_to_iso(epochs[-1])

    window = ""
    if epochs:
        window = f"，关注时间 {_epoch_to_iso(epochs[0])[:10]} ~ {_epoch_to_iso(epochs[-1])[:10]}"
    context = (
        f"账号导入存量：关注了 {total} 位创作者{window}。这是账号历史清单，不是最近发生的行为。"
    )
    return {
        "event_type": event_type,
        "title": f"导入存量：关注了 {total} 位创作者",
        "source_platform": source_platform,
        "context": context,
        "metadata": metadata,
    }


def prepare_cognition_events(
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Apply every provenance projection in the order prompt builders need."""
    return apply_event_provenance(collapse_snapshot_creators(events))
