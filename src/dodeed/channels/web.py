"""웹 채팅 채널: 로그인 자체가 채널 연결이다 (REQ-CHN-001)."""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from dodeed.accounts.api import current_user_id
from dodeed.channels.deps import get_gateway
from dodeed.channels.gateway import Gateway
from dodeed.channels.messages import Inbound, InboundLocation, InboundText
from dodeed.db import get_db

CHANNEL = "web"
TEXT_MAX_LENGTH = 2000

router = APIRouter(prefix="/api")


class ChatBody(BaseModel):
    text: str = Field(max_length=TEXT_MAX_LENGTH)
    event_id: str = Field(min_length=1, max_length=128)


class LocationBody(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    event_id: str = Field(min_length=1, max_length=128)


async def _handle(
    gateway: Gateway, db: AsyncSession, user_id: uuid.UUID, msg: Inbound
) -> dict[str, list[dict]]:
    out = await gateway.handle(db, msg, resolved_user_id=user_id)
    return {"messages": [m.model_dump() for m in out]}


@router.post("/chat")
async def chat(
    body: ChatBody,
    user_id: uuid.UUID = Depends(current_user_id),
    db: AsyncSession = Depends(get_db),
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, list[dict]]:
    msg = InboundText(
        channel=CHANNEL, external_id=str(user_id), text=body.text, event_id=body.event_id
    )
    return await _handle(gateway, db, user_id, msg)


@router.post("/chat/location")
async def chat_location(
    body: LocationBody,
    user_id: uuid.UUID = Depends(current_user_id),
    db: AsyncSession = Depends(get_db),
    gateway: Gateway = Depends(get_gateway),
) -> dict[str, list[dict]]:
    msg = InboundLocation(
        channel=CHANNEL,
        external_id=str(user_id),
        lat=body.lat,
        lng=body.lng,
        event_id=body.event_id,
    )
    return await _handle(gateway, db, user_id, msg)
