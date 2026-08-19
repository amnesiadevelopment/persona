"""REST lane for the trash.

Lane parity is the point: the REST delete and the UI delete already file into
the same TrashStore, and these endpoints give the API the same recovery
gestures the window has. A second door that still destroyed records — or that
could trash but never restore — would reintroduce exactly the problem the trash
closes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...core.logging import get_logger
from ...services.trash.store import RETENTION_DAYS, KINDS
from ..dependencies import get_event_bus, get_trash_service
from ..schemas.common import ErrorResponse, SuccessResponse
from ..schemas.trash import (
    TrashEmptyResponse,
    TrashEntryResponse,
    TrashListResponse,
)

logger = get_logger("api.trash")

router = APIRouter(prefix="/trash", tags=["trash"])


def _entry_response(entry) -> TrashEntryResponse:
    return TrashEntryResponse(
        id=entry.id,
        kind=entry.kind,
        label=entry.label,
        name=entry.name,
        deleted_at=entry.deleted_at,
        expires_at=entry.expires_at(),
        holds_secret_material=entry.holds_secret_material,
    )


@router.get("", response_model=TrashListResponse)
def list_trash(
    kind: str | None = None,
    ts=Depends(get_trash_service),
) -> TrashListResponse:
    if kind is not None and kind not in KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown kind '{kind}'. One of: {', '.join(KINDS)}",
        )
    entries = [_entry_response(e) for e in ts.list(kind)]
    return TrashListResponse(
        entries=entries, total=len(entries), retention_days=RETENTION_DAYS
    )


@router.post(
    "/{entry_id}/restore",
    response_model=SuccessResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def restore_entry(
    entry_id: str,
    ts=Depends(get_trash_service),
    bus=Depends(get_event_bus),
) -> SuccessResponse:
    entry = ts.get(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="No such item in the trash")
    label, name = entry.label, entry.name
    ok, msg = ts.restore(entry_id)
    if not ok:
        # A refused restore is a conflict, not a failure: the name is taken, and
        # the caller is told exactly why rather than being handed a rename.
        raise HTTPException(status_code=409, detail=msg)
    logger.info("API restored %s from trash: %s", label, name)
    bus.emit()
    return SuccessResponse(message=f"Restored {label} '{name}'")


@router.delete(
    "/{entry_id}",
    response_model=SuccessResponse,
    responses={404: {"model": ErrorResponse}},
)
def delete_entry_permanently(
    entry_id: str,
    ts=Depends(get_trash_service),
    bus=Depends(get_event_bus),
) -> SuccessResponse:
    """Delete one trashed item and its on-disk material for good, immediately.
    This one really cannot be undone."""
    entry = ts.get(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="No such item in the trash")
    label, name = entry.label, entry.name
    ok, msg = ts.delete_permanently(entry_id)
    if not ok:
        raise HTTPException(status_code=404, detail=msg)
    logger.info("API permanently deleted %s: %s", label, name)
    bus.emit()
    return SuccessResponse(
        message=f"Permanently deleted {label} '{name}'"
    )


@router.delete("", response_model=TrashEmptyResponse)
def empty_trash(
    ts=Depends(get_trash_service),
    bus=Depends(get_event_bus),
) -> TrashEmptyResponse:
    """Empty the whole trash, destroying every entry and its on-disk material.
    Irreversible, and described as such."""
    count = ts.empty()
    logger.info("API emptied the trash (%d entries)", count)
    bus.emit()
    return TrashEmptyResponse(
        deleted=count,
        message=f"Permanently deleted {count} item(s)",
    )
