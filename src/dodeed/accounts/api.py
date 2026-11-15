import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from dodeed.accounts import service
from dodeed.db import get_db

SESSION_COOKIE = "dodeed_session"
SESSION_MAX_AGE = 14 * 24 * 3600

router = APIRouter(prefix="/api")


class SignupBody(BaseModel):
    email: str
    password: str = Field(min_length=8)
    display_name: str


class LoginBody(BaseModel):
    email: str
    password: str


def _serializer(request: Request) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(request.app.state.settings.secret_hmac_key, salt="session")


def current_user_id(request: Request) -> uuid.UUID:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "로그인이 필요합니다")
    try:
        return uuid.UUID(_serializer(request).loads(token, max_age=SESSION_MAX_AGE))
    except (BadSignature, ValueError):
        raise HTTPException(401, "로그인이 필요합니다") from None


@router.post("/signup")
async def signup(body: SignupBody, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    try:
        user_id = await service.signup(db, body.email, body.password, body.display_name)
    except service.DuplicateEmailError:
        raise HTTPException(409, "이미 가입된 이메일입니다") from None
    return {"user_id": str(user_id)}


@router.post("/login")
async def login(
    body: LoginBody, request: Request, response: Response, db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    user_id = await service.authenticate(db, body.email, body.password)
    if user_id is None:
        raise HTTPException(401, "이메일 또는 비밀번호가 올바르지 않습니다")
    response.set_cookie(
        SESSION_COOKIE,
        _serializer(request).dumps(str(user_id)),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.app.state.settings.web_base_url.startswith("https://"),
    )
    return {"user_id": str(user_id)}


@router.post("/link-codes")
async def create_link_code(
    user_id: uuid.UUID = Depends(current_user_id), db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    now = datetime.now(UTC)
    code = await service.issue_link_code(db, user_id, now)
    return {"code": code, "expires_at": (now + service.LINK_CODE_TTL).isoformat()}


@router.delete("/channels/{channel}", status_code=204)
async def unlink_channel(
    channel: str, user_id: uuid.UUID = Depends(current_user_id), db: AsyncSession = Depends(get_db)
) -> Response:
    await service.unlink(db, user_id, channel)
    return Response(status_code=204)
