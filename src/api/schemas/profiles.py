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
    # Rule 3's verdict on the STORED pair: the reason this profile's
    # os_type/device_type cannot both be true, or None when they can (PS-188).
    #
    # Create and update REFUSE this pair, so a profile authored through this API
    # can never carry it. It arrives through the three RECOVERY doors, which
    # accept it deliberately — import, restore and the legacy disk load recover
    # records rather than authoring them, and a door that refuses turns a
    # recoverable backup into an unimportable one.
    #
    # Present on the row precisely BECAUSE those doors do not refuse: the
    # decision taken was "accept and record", and a record nobody can read is
    # not a record. This is the read half — it is what lets an operator find the
    # incoherent profiles they already have, which was previously impossible
    # from any surface. Derived on read from `Profile.device_type_incoherence`,
    # never stored, so it cannot go stale against the two fields above it.
    #
    # Unlike `data_dir` below, this discloses nothing about the host: it is a
    # statement about two values the row already carries.
    device_type_incoherence: str | None = None
    engine: str = "chromium"
    resolution: str = "auto"
    search_engine: str = "duckduckgo"
    bookmark_pool: str | None = None
    bookmarks: list[str] | None = None
    certificate: str | None = None
    tags: list[str] = []
    ai_control: bool = False
    notes: str = ""
    is_running: bool
    # `data_dir` is deliberately NOT on this row. It is an absolute host
    # filesystem path carrying the operator's OS account name, and the profile
    # row is the broadcast surface -- every list and every read hands it out
    # unasked. `GET /profiles/{name}/data-dir` (DataDirResponse below) already
    # answers it on explicit request, which is the least-exposure shape: asked
    # for, not volunteered. This also brings the REST lane's answer into line
    # with the MCP lane, which withholds endpoint/location data for stated
    # reasons (mcp_server.py:82, :332; refusal_report.py:56) -- previously the
    # two lanes disagreed and only one had recorded why.


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
