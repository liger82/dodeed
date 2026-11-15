"""채널과 무관한 공통 메시지 형식 (REQ-CHN-002)."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class InboundText(BaseModel):
    channel: str
    external_id: str
    text: str
    event_id: str


class InboundLocation(BaseModel):
    channel: str
    external_id: str
    lat: float
    lng: float
    event_id: str


class InboundButton(BaseModel):
    channel: str
    external_id: str
    payload: str
    event_id: str


Inbound = InboundText | InboundLocation | InboundButton


class Button(BaseModel):
    label: str
    payload: str


class Card(BaseModel):
    title: str
    lines: list[str] = []
    buttons: list[Button] = []


class OutText(BaseModel):
    kind: Literal["text"] = "text"
    text: str


class OutButtons(BaseModel):
    kind: Literal["buttons"] = "buttons"
    text: str
    buttons: list[Button]


class OutCardList(BaseModel):
    kind: Literal["cards"] = "cards"
    cards: list[Card]


class OutLocationRequest(BaseModel):
    kind: Literal["location_request"] = "location_request"
    text: str


class OutApprovalRequest(BaseModel):
    """계획 3에서 사용한다."""

    kind: Literal["approval_request"] = "approval_request"
    summary: str
    lines: list[str]
    approve_payload: str
    reject_payload: str


Outbound = Annotated[
    OutText | OutButtons | OutCardList | OutLocationRequest | OutApprovalRequest,
    Field(discriminator="kind"),
]
