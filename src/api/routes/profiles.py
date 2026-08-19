from __future__ import annotations

import os
import pathlib
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException

from ...core.config import DATA_DIR
from ...core.logging import get_logger
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

    if not pm.add_profile(
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
    ):
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
    new_proxy = supplied.get("proxy", profile.proxy)
    new_os = supplied.get("os_type", profile.os_type)
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

    if "proxy" in supplied and new_proxy:
        _validate_proxy_ref(new_proxy, ps)

    # bookmark_pool is assigned unconditionally by update_profile, so pass the
    # profile's current value when the PATCH omits it (else it would be wiped).
    # Every other optional field is only applied when non-None, so an omitted
    # field passes None and stays untouched.
    if not pm.update_profile(
        name,
        new_name,
        new_proxy or "",
        new_os,
        new_search_engine=supplied.get("search_engine"),
        new_bookmark_pool=supplied.get("bookmark_pool", profile.bookmark_pool),
        new_bookmarks=supplied.get("bookmarks"),
        new_tags=supplied.get("tags"),
        new_ai_control=supplied.get("ai_control"),
        new_device_type=supplied.get("device_type"),
        new_notes=new_notes,
        new_engine=supplied.get("engine"),
        new_resolution=supplied.get("resolution"),
        new_certificate=supplied.get("certificate"),
    ):
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
    data_dir = os.path.join(os.getcwd(), DATA_DIR, name)
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
