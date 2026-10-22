"""One policy result used by both graphical planning and deployment."""
from dataclasses import asdict, dataclass, field
from enum import Enum


class RouteState(str, Enum):
    COMPATIBLE = 'compatible'
    EXPERIMENTAL = 'experimental'
    MISSING = 'missing_prerequisite'
    INCOMPATIBLE = 'known_incompatible'
    UNSAFE = 'unsafe_or_not_permitted'
    VERIFIED = 'execution_verified'


@dataclass(frozen=True)
class Requirement:
    code: str
    satisfied: bool
    detail: str


@dataclass
class RouteResult:
    route_id: str
    name: str
    state: RouteState
    requirements: list[Requirement] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    consumer: str | None = None
    proxy: str | None = None
    recommended: bool = False

    @property
    def eligible(self):
        return self.state in (RouteState.COMPATIBLE, RouteState.EXPERIMENTAL, RouteState.VERIFIED)

    def to_dict(self):
        data = asdict(self)
        data['state'] = self.state.value
        data['eligible'] = self.eligible
        return data
