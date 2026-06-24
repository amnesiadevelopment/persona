from dataclasses import asdict, dataclass, field


@dataclass
class Bookmark:
    name: str
    url: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Pool:
    name: str
    bookmark_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
