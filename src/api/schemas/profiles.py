from pydantic import BaseModel


class ProfileCreate(BaseModel):
    name: str
    proxy: str | None = None
    os_type: str = "windows"
    device_type: str = "desktop"
    engine: str = "chromium"
    resolution: str = "auto"
    search_engine: str = "duckduckgo"
    bookmark_pool: str | None = None
    bookmarks: list[str] | None = None
    certificate: str | None = None
    tags: list[str] | None = None
    ai_control: bool = False
    notes: str = ""


class ProfileUpdate(BaseModel):
    """All fields optional — only supplied fields are changed."""

    name: str | None = None
    proxy: str | None = None
    os_type: str | None = None
    device_type: str | None = None
    engine: str | None = None
    resolution: str | None = None
    search_engine: str | None = None
    bookmark_pool: str | None = None
    bookmarks: list[str] | None = None
    certificate: str | None = None
    tags: list[str] | None = None
    ai_control: bool | None = None
    notes: str | None = None


class ProfileResponse(BaseModel):
    name: str
    proxy: str | None
    os_type: str
    device_type: str = "desktop"
    engine: str = "chromium"
    resolution: str = "auto"
    search_engine: str = "duckduckgo"
    bookmark_pool: str | None = None
    bookmarks: list[str] | None = None
    certificate: str | None = None
    tags: list[str] = []
    ai_control: bool = False
    notes: str = ""
    data_dir: str
    is_running: bool


class ProfileListResponse(BaseModel):
    profiles: list[ProfileResponse]
    total: int


class DataDirResponse(BaseModel):
    name: str
    data_dir: str
    exists: bool


class ExportRequest(BaseModel):
    export_dir: str
    include_data: bool = True


class ExportResponse(BaseModel):
    success: bool
    zip_path: str | None = None
    error: str | None = None


class ImportRequest(BaseModel):
    zip_path: str
    overwrite: bool = False


class ImportResponse(BaseModel):
    success: bool
    profile_name: str | None = None
    error: str | None = None
