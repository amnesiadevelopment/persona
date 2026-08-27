"""Shared helpers for API routes (DRY)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException

from .schemas.profiles import ProfileResponse

if TYPE_CHECKING:
    from ..interfaces import IBrowserLauncher, IProfileManager


def require_profile(name: str, pm: IProfileManager) -> None:
    """Raise 404 if the profile does not exist."""
    if name not in pm.profiles:
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")


def build_profile_response(
    name: str,
    pm: IProfileManager,
    bl: IBrowserLauncher,
) -> ProfileResponse:
    """Build a ProfileResponse DTO for the given profile name."""
    profile = pm.profiles[name]
    return ProfileResponse(
        name=profile.name,
        proxy=profile.proxy,
        os_type=profile.os_type,
        device_type=getattr(profile, "device_type", "desktop"),
        # Rule 3's verdict on the stored pair, derived on read (PS-188). See
        # ProfileResponse: the recovery doors accept an incoherent pair by
        # design, so this is the surface that lets an operator find one.
        device_type_incoherence=getattr(
            profile, "device_type_incoherence", None
        ),
        engine=getattr(profile, "engine", "chromium"),
        resolution=getattr(profile, "resolution", "auto"),
        search_engine=getattr(profile, "search_engine", "duckduckgo"),
        bookmark_pool=getattr(profile, "bookmark_pool", None),
        bookmarks=getattr(profile, "bookmarks", None),
        certificate=getattr(profile, "certificate", None),
        tags=getattr(profile, "tags", []),
        ai_control=getattr(profile, "ai_control", False),
        notes=getattr(profile, "notes", ""),
        is_running=bl.is_running(name),
    )
