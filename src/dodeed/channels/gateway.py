"""채널 게이트웨이: 중복 제거, 계정 연결, 오케스트레이터 호출 (REQ-CHN-002)."""

import re
import uuid
from datetime import UTC, datetime
from typing import Protocol

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from dodeed.accounts.service import get_user_pii, redeem_link_code, resolve_user
from dodeed.channels.messages import (
    Inbound,
    InboundLocation,
    InboundText,
    Outbound,
    OutText,
)
from dodeed.config import Settings
from dodeed.security.redaction import UserPII
from dodeed.sessions.service import Conversation, get_or_create, get_state, set_state

_LINK_RE = re.compile(r"^/link\s+(\d{6})$")
EVENT_TTL = 86400
LINK_FAIL_TTL = 600
LINK_FAIL_LIMIT = 5

MSG_LINKED = "연결되었어요. 이제 dodeed에게 말을 걸어 보세요."
MSG_LINK_FAILED = "연결 코드가 올바르지 않거나 만료되었어요. 앱이나 웹에서 새 코드를 받아 주세요."
MSG_LINK_LOCKED = "연결 시도가 너무 많아요. 10분 뒤에 다시 시도해 주세요."
MSG_LOCATION = "위치를 받았어요."


class OrchestratorLike(Protocol):
    async def handle(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        conversation: Conversation,
        text: str,
        pii: UserPII,
    ) -> list[Outbound]: ...


class Gateway:
    def __init__(
        self, orchestrator: OrchestratorLike, settings: Settings, redis: aioredis.Redis
    ) -> None:
        self._orchestrator = orchestrator
        self._settings = settings
        self._redis = redis

    def _signup_text(self) -> OutText:
        return OutText(
            text=f"dodeed를 쓰려면 먼저 가입해 주세요: {self._settings.web_base_url}/signup\n"
            "가입 후 받은 연결 코드를 /link 123456 형식으로 보내 주세요."
        )

    async def handle(
        self,
        db: AsyncSession,
        msg: Inbound,
        *,
        resolved_user_id: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> list[Outbound]:
        now = now or datetime.now(UTC)
        event_key = f"dodeed:event:{msg.channel}:{msg.external_id}:{msg.event_id}"
        first = await self._redis.set(event_key, 1, nx=True, ex=EVENT_TTL)
        if not first:
            return []
        try:
            return await self._process(db, msg, resolved_user_id, now)
        except BaseException:
            # 처리에 실패하면 채널의 재전송이 중복으로 버려지지 않도록 표시를 지운다
            await self._redis.delete(event_key)
            raise

    async def _process(
        self,
        db: AsyncSession,
        msg: Inbound,
        resolved_user_id: uuid.UUID | None,
        now: datetime,
    ) -> list[Outbound]:
        user_id = resolved_user_id
        if user_id is None:
            user_id = await resolve_user(db, msg.channel, msg.external_id)
        if user_id is None:
            if isinstance(msg, InboundText):
                m = _LINK_RE.match(msg.text.strip())
                if m:
                    return [await self._link(db, msg, m.group(1), now)]
            return [self._signup_text()]

        if isinstance(msg, InboundLocation):
            conv = await get_or_create(db, user_id, msg.channel, now)
            state = await get_state(db, conv.id)
            state["location"] = {"lat": msg.lat, "lng": msg.lng, "at": now.isoformat()}
            await set_state(db, conv.id, state)
            await db.commit()
            return [OutText(text=MSG_LOCATION)]

        text = msg.text if isinstance(msg, InboundText) else msg.payload
        conv = await get_or_create(db, user_id, msg.channel, now)
        pii = await get_user_pii(db, user_id)
        return await self._orchestrator.handle(db, user_id, conv, text, pii)

    async def _link(self, db: AsyncSession, msg: InboundText, code: str, now: datetime) -> OutText:
        key = f"dodeed:linkfail:{msg.channel}:{msg.external_id}"
        count = await self._redis.get(key)
        if count is not None and int(count) >= LINK_FAIL_LIMIT:
            return OutText(text=MSG_LINK_LOCKED)
        user_id = await redeem_link_code(db, code, msg.channel, msg.external_id, now)
        if user_id is None:
            # INCR과 EXPIRE를 한 트랜잭션으로 보내 TTL 없는 카운터가 남지 않게 한다.
            # NX: 이미 TTL이 있으면 늘리지 않는다 (잠금 시간이 계속 밀리지 않도록).
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, LINK_FAIL_TTL, nx=True)
                await pipe.execute()
            return OutText(text=MSG_LINK_FAILED)
        await self._redis.delete(key)
        return OutText(text=MSG_LINKED)
