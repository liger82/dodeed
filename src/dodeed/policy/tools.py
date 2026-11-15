from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from dodeed.llm.types import ToolDef


class Grade(StrEnum):
    READ = "read"
    WRITE = "write"


class PermissionLevel(StrEnum):
    READ_ONLY = "read_only"
    APPROVE = "approve"
    INSTANT = "instant"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    service: str
    grade: Grade
    input_model: type[BaseModel]
    description: str
    instant_allowed: bool = False
    is_payment: bool = False
    allowed_domains: frozenset[str] = field(default_factory=frozenset)


@dataclass
class ToolContext:
    user_id: UUID
    db: AsyncSession
    session_id: UUID


Handler = Callable[[ToolContext, BaseModel], Awaitable[BaseModel]]

# 작업 종류별 허용 도구. 목록에 없는 도구는 정책 엔진이 거부한다.
TASK_TOOLSETS: dict[str, frozenset[str]] = {
    "restaurant_lookup": frozenset({"search_restaurants"}),
}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolSpec, Handler]] = {}

    def register(self, spec: ToolSpec, handler: Handler) -> None:
        if spec.name in self._tools:
            raise ValueError(f"이미 등록된 도구입니다: {spec.name}")
        self._tools[spec.name] = (spec, handler)

    def get(self, name: str) -> tuple[ToolSpec, Handler]:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"등록되지 않은 도구입니다: {name}") from None

    def tool_defs(self, names: Iterable[str]) -> list[ToolDef]:
        defs = []
        for name in names:
            spec, _ = self.get(name)
            defs.append(
                ToolDef(
                    name=spec.name,
                    description=spec.description,
                    parameters=spec.input_model.model_json_schema(),
                )
            )
        return defs

    def has_instant_allowed(self, service: str) -> bool:
        return any(s.service == service and s.instant_allowed for s, _ in self._tools.values())
