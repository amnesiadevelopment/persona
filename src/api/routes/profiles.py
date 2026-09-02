from __future__ import annotations

import os
import pathlib
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

from ...core.config import DATA_DIR
from ...core.logging import get_logger
from ...services.profile.coherence import IncoherentProfile
from ...services.profile.pool_assignment import POOL_NONE, POOL_UNCHANGED
from ...services.profile.proxy_assignment import PROXY_NONE, PROXY_UNCHANGED
from ...utils.validation import validate_profile_name, validate_proxy_format
from ..dependencies import (
    get_browser_launcher,
    get_event_bus,
    get_profile_manager,
    get_proxy_store,
)
from ..helpers import build_profile_response, require_profile
from ..schemas.common import ErrorResponse, SuccessResponse
from ..schemas.profiles import (
    DataDirResponse,
    ExportRequest,
    ExportResponse,
    ImportRequest,
    ImportResponse,
    ProfileCreate,
    ProfileListResponse,
    ProfileResponse,
    ProfileUpdate,
)

if TYPE_CHECKING:
    from ...core.events import EventBus
    from ...interfaces import IBrowserLauncher, IProfileManager

logger = get_logger("api.profiles")

router = APIRouter(prefix="/profiles", tags=["profiles"])


def _validate_proxy_ref(proxy: str, ps) -> None:
    """A profile's proxy field is a NAME reference into the proxy store; the API
    also tolerates a raw URL for convenience. Accept an existing proxy name OR a
    well-formed proxy URL. (Before, the route ran validate_proxy_format — a URL
    regex — on the name and 400'd every proxy-by-name create/update.)"""
    if proxy in ps.names():
        return
    valid, msg = validate_proxy_format(proxy)
    if not valid:
        raise HTTPException(
            status_code=400,
            detail=f"Proxy '{proxy}' is not a known proxy name or a valid URL",
        )


@router.get("", response_model=ProfileListResponse)
def list_profiles(
    pm: IProfileManager = Depends(get_profile_manager),
    bl: IBrowserLauncher = Depends(get_browser_launcher),
) -> ProfileListResponse:
    profiles = [build_profile_response(p.name, pm, bl) for p in pm.list_profiles()]
    return ProfileListResponse(profiles=profiles, total=len(profiles))


@router.post(
    "",
    response_model=ProfileResponse,
    status_code=201,
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def create_profile(
    body: ProfileCreate,
    pm: IProfileManager = Depends(get_profile_manager),
    bl: IBrowserLauncher = Depends(get_browser_launcher),
    ps=Depends(get_proxy_store),
    bus: EventBus = Depends(get_event_bus),
) -> ProfileResponse:
    valid, msg = validate_profile_name(body.name)
    if not valid:
        raise HTTPException(status_code=400, detail=msg)

    if body.proxy:
        _validate_proxy_ref(body.proxy, ps)

    # The os_type/engine coherence rules are enforced by the model (every door
    # crosses them), so this route only has to TRANSLATE the refusal into HTTP.
    # 400, not 409: the request describes a machine that cannot exist, which is
    # a bad request, not a conflict with an existing record. The detail carries
    # the model's reason verbatim so the caller learns which pair conflicts and
    # how to resolve it.
    try:
        created = pm.add_profile(
            body.name,
            body.proxy or "",
            body.os_type,
            search_engine=body.search_engine,
            bookmark_pool=body.bookmark_pool,
            bookmarks=body.bookmarks,
            tags=body.tags,
            device_type=body.device_type,
            notes=body.notes,
            engine=body.engine,
            resolution=body.resolution,
            certificate=body.certificate,
            ai_control=body.ai_control,
        )
    except IncoherentProfile as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if not created:
        raise HTTPException(status_code=409, detail="Profile already exists")

    logger.info("API created profile: %s", body.name)
    bus.emit()
    return build_profile_response(body.name, pm, bl)


@router.get(
    "/{name}",
    response_model=ProfileResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_profile(
    name: str,
    pm: IProfileManager = Depends(get_profile_manager),
    bl: IBrowserLauncher = Depends(get_browser_launcher),
) -> ProfileResponse:
    require_profile(name, pm)
    return build_profile_response(name, pm, bl)


@router.patch(
    "/{name}",
    response_model=ProfileResponse,
    responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def update_profile(
    name: str,
    body: ProfileUpdate,
    pm: IProfileManager = Depends(get_profile_manager),
    bl: IBrowserLauncher = Depends(get_browser_launcher),
    ps=Depends(get_proxy_store),
    bus: EventBus = Depends(get_event_bus),
) -> ProfileResponse:
    require_profile(name, pm)
    supplied = body.model_dump(exclude_unset=True)
    profile = pm.profiles[name]

    new_name = supplied.get("name", name)
    # The one distinction only a route can draw: an OMITTED `proxy` key vs a
    # SUPPLIED empty one. The model now refuses to read absence as a clear (see
    # services/profile/proxy_assignment.py), so this lane says which it meant
    # instead of reading the stored proxy and passing it back in. That old
    # read-back was correct but was the door protecting the model; the model now
    # protects itself and a caller that omits the key is safe by default.
    if "proxy" not in supplied:
        new_proxy = PROXY_UNCHANGED
    elif supplied["proxy"]:
        new_proxy = supplied["proxy"]
    else:
        # Explicitly supplied as null/"" — the caller is deliberately choosing
        # DIRECT, which stays expressible.
        new_proxy = PROXY_NONE
    new_os = supplied.get("os_type")
    new_notes = supplied.get("notes")

    if "name" in supplied:
        valid, msg = validate_profile_name(new_name)
        if not valid:
            raise HTTPException(status_code=400, detail=msg)
        if new_name != name and bl.is_running(name):
            raise HTTPException(
                status_code=409,
                detail="Stop the browser before renaming",
            )

    if isinstance(new_proxy, str):
        _validate_proxy_ref(new_proxy, ps)

    # The same distinction only a route can draw, for the pool: an OMITTED
    # `bookmark_pool` key vs a SUPPLIED empty one. The model now refuses to read
    # absence as a clear (see services/profile/pool_assignment.py), so this lane
    # says which it meant instead of reading the stored pool and passing it back
    # in. That old read-back was correct but was the door protecting the model;
    # the model now protects itself and a caller that omits the key is safe by
    # default.
    if "bookmark_pool" not in supplied:
        new_bookmark_pool = POOL_UNCHANGED
    elif supplied["bookmark_pool"]:
        new_bookmark_pool = supplied["bookmark_pool"]
    else:
        # Explicitly supplied as null/"" — the caller is deliberately choosing
        # no pool, which stays expressible.
        new_bookmark_pool = POOL_NONE

    # The same distinction only a route can draw, for the certificate — with
    # the opposite mapping, deliberately. On this field the model already reads
    # None as "leave alone" and "" as the explicit clear, so an omitted key was
    # ALREADY correct here. It was correct BY ACCIDENT: nothing in this lane
    # said which it meant, and `supplied.get("certificate")` returning None for
    # an absent key is a property of `dict.get`, not a statement of intent. The
    # two branches below say it, so a later edit cannot quietly break it and a
    # reader does not have to reconstruct it from a coincidence.
    #
    # No CERT_UNCHANGED is sent: it means "I cannot account for the stored
    # assignment", which is the DIALOG's state, never a route's — an API caller
    # that omits the key is not confused about the certificate, it is silent
    # about it, and None already says exactly that. See
    # services/profile/cert_assignment.py.
    if "certificate" not in supplied:
        new_certificate: str | None = None
    elif supplied["certificate"]:
        new_certificate = supplied["certificate"]
    else:
        # Explicitly supplied as null/"" — the caller is deliberately choosing
        # no certificate. "" is the model's clear on this field.
        new_certificate = ""

    # Coherence is enforced by the model, which sees the PATCH's fields AND the
    # stored ones — so `PATCH {"os_type": "macos"}` on a profile already stored
    # as firefox is judged on the pair it would RESULT IN, not on the one field
    # supplied. This route only translates that refusal into a 400.
    try:
        updated = pm.update_profile(
            name,
            new_name,
            new_proxy,
            new_os,
            new_search_engine=supplied.get("search_engine"),
            new_bookmark_pool=new_bookmark_pool,
            new_bookmarks=supplied.get("bookmarks"),
            new_tags=supplied.get("tags"),
            new_ai_control=supplied.get("ai_control"),
            new_device_type=supplied.get("device_type"),
            new_notes=new_notes,
            new_engine=supplied.get("engine"),
            new_resolution=supplied.get("resolution"),
            new_certificate=new_certificate,
        )
    except IncoherentProfile as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if not updated:
        raise HTTPException(status_code=409, detail="Update failed (name conflict?)")

    logger.info("API updated profile: %s -> %s", name, new_name)
    bus.emit()
    return build_profile_response(new_name, pm, bl)


@router.delete(
    "/{name}",
    response_model=SuccessResponse,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
def delete_profile(
    name: str,
    pm: IProfileManager = Depends(get_profile_manager),
    bl: IBrowserLauncher = Depends(get_browser_launcher),
    bus: EventBus = Depends(get_event_bus),
) -> SuccessResponse:
    require_profile(name, pm)
    if bl.is_running(name):
        raise HTTPException(
            status_code=409,
            detail="Stop the browser before deleting",
        )
    # delete_profile returns False when the data dir could not be parked (disk
    # full, permissions, cross-device): the profile is then left completely
    # intact. Replying 200 "deleted" regardless told the caller an identity was
    # gone while it was still on disk — a claim the code does not deliver, which
    # is precisely what this ticket closes. Lane parity: the UI says the same.
    if not pm.delete_profile(name):
        raise HTTPException(
            status_code=500,
            detail=(
                f"Could not move profile '{name}' to the trash; "
                "it is unchanged."
            ),
        )
    logger.info("API deleted profile: %s", name)
    bus.emit()
    return SuccessResponse(message=f"Profile '{name}' moved to the trash")


@router.get(
    "/{name}/data-dir",
    response_model=DataDirResponse,
    responses={404: {"model": ErrorResponse}},
)
def get_data_dir(
    name: str,
    pm: IProfileManager = Depends(get_profile_manager),
) -> DataDirResponse:
    require_profile(name, pm)
    # abspath, not a bare join: this endpoint has always answered with an
    # absolute path, and abspath keeps that true under every override shape.
    # NOTE the rationale changed in PS-127: config._under_home no longer returns
    # a relative env override verbatim — it anchors one, so DATA_DIR is already
    # absolute here (measured at ba39a03 vs this branch under the shipped
    # `.env.example` shape `PERSONA_DATA_DIR=persona_data`: 'persona_data' ->
    # '/<cwd>/persona_data'). This abspath is therefore REDUNDANT-BUT-CORRECT
    # rather than load-bearing — abspath on an already-absolute path is
    # identity — and it is kept deliberately: it still normalises the join and
    # costs nothing. Do not re-add an os.getcwd() join in its place; that was
    # the silent compensation PS-127 removed.
    data_dir = os.path.abspath(os.path.join(DATA_DIR, name))
    return DataDirResponse(
        name=name,
        data_dir=data_dir,
        exists=pathlib.Path(data_dir).exists(),
    )


@router.post(
    "/{name}/export",
    response_model=ExportResponse,
    responses={404: {"model": ErrorResponse}},
)
def export_profile(
    name: str,
    body: ExportRequest,
    pm: IProfileManager = Depends(get_profile_manager),
) -> ExportResponse:
    require_profile(name, pm)
    # `export_dir` is used as given, and that is deliberate — see the DESTINATION
    # POLICY note above export_to_zip in services/profile/transfer.py (PS-180).
    # Short version: this route is reachable only by someone already holding the
    # operator's token on the operator's own machine, and export exists to put a
    # profile somewhere else. The check below is an existence check, not a
    # confinement, and it is not standing in for one.
    if not pathlib.Path(body.export_dir).is_dir():
        raise HTTPException(status_code=400, detail="export_dir is not a directory")

    success, result = pm.export_profile(name, body.export_dir, body.include_data)
    if success:
        logger.info("API exported profile: %s -> %s", name, result)
        return ExportResponse(success=True, zip_path=result)
    return ExportResponse(success=False, error=result)


@router.post(
    "/import",
    response_model=ImportResponse,
    responses={400: {"model": ErrorResponse}},
)
def import_profile(
    body: ImportRequest,
    pm: IProfileManager = Depends(get_profile_manager),
    bus: EventBus = Depends(get_event_bus),
) -> ImportResponse:
    if not pathlib.Path(body.zip_path).is_file():
        raise HTTPException(status_code=400, detail="zip_path is not a file")

    success, result = pm.import_profile(body.zip_path, body.overwrite)
    if success:
        logger.info("API imported profile: %s", result)
        bus.emit()
        return ImportResponse(success=True, profile_name=result)
    return ImportResponse(success=False, error=result)
