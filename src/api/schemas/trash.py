from __future__ import annotations

from pydantic import BaseModel


class TrashEntryResponse(BaseModel):
    """One trashed record, as the API reports it.

    ``holds_secret_material`` is the honest bit the interface must not hide:
    trashing a proxy, an SSH host or a certificate does NOT remove its secret
    material from disk — it rides along in trash.json at the same 0600
    protection the live store gave it. Permanent deletion is what destroys it.
    """

    id: str
    kind: str
    label: str
    name: str
    deleted_at: float
    expires_at: float
    holds_secret_material: bool


class TrashListResponse(BaseModel):
    entries: list[TrashEntryResponse]
    total: int
    retention_days: int


class TrashEmptyResponse(BaseModel):
    success: bool = True
    deleted: int
    message: str
