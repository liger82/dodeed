import secrets
import uuid
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dodeed.accounts.models import ChannelLink, LinkCode, User
from dodeed.security.redaction import UserPII

LINK_CODE_TTL = timedelta(minutes=10)

_hasher = PasswordHasher()
# 없는 이메일로 로그인해도 같은 비용으로 검증하기 위한 고정 해시
# (응답 시간으로 가입 여부가 드러나지 않게)
_DUMMY_HASH = _hasher.hash("dodeed-dummy-password-for-timing")


class DuplicateEmailError(Exception):
    pass


async def signup(db: AsyncSession, email: str, password: str, display_name: str) -> uuid.UUID:
    email = email.strip().lower()
    existing = await db.scalar(select(User.id).where(User.email == email))
    if existing is not None:
        raise DuplicateEmailError(email)
    user = User(
        id=uuid.uuid4(),
        email=email,
        password_hash=_hasher.hash(password),
        display_name=display_name,
    )
    db.add(user)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as e:
        raise DuplicateEmailError(email) from e
    await db.commit()
    return user.id


async def authenticate(db: AsyncSession, email: str, password: str) -> uuid.UUID | None:
    user = await db.scalar(select(User).where(User.email == email.strip().lower()))
    try:
        _hasher.verify(user.password_hash if user is not None else _DUMMY_HASH, password)
    except VerifyMismatchError:
        return None
    return user.id if user is not None else None


async def issue_link_code(db: AsyncSession, user_id: uuid.UUID, now: datetime) -> str:
    while True:
        code = f"{secrets.randbelow(10**6):06d}"
        clash = await db.scalar(
            select(LinkCode.id).where(
                LinkCode.code == code, LinkCode.used_at.is_(None), LinkCode.expires_at >= now
            )
        )
        if clash is None:
            break
    db.add(LinkCode(code=code, user_id=user_id, expires_at=now + LINK_CODE_TTL))
    await db.commit()
    return code


async def redeem_link_code(
    db: AsyncSession, code: str, channel: str, external_id: str, now: datetime
) -> uuid.UUID | None:
    # 동시 사용을 막기 위해 행을 잠근다
    link_code = await db.scalar(
        select(LinkCode)
        .where(LinkCode.code == code, LinkCode.used_at.is_(None), LinkCode.expires_at >= now)
        .with_for_update()
    )
    if link_code is None:
        return None
    owner = await resolve_user(db, channel, external_id)
    if owner is not None and owner != link_code.user_id:
        # 이미 다른 계정에 연결된 채널은 가로채지 못하게 거절한다
        return None
    link_code.used_at = now
    if owner is None:
        db.add(ChannelLink(user_id=link_code.user_id, channel=channel, external_id=external_id))
    await db.commit()
    return link_code.user_id


async def resolve_user(db: AsyncSession, channel: str, external_id: str) -> uuid.UUID | None:
    return await db.scalar(
        select(ChannelLink.user_id).where(
            ChannelLink.channel == channel, ChannelLink.external_id == external_id
        )
    )


async def unlink(db: AsyncSession, user_id: uuid.UUID, channel: str) -> None:
    await db.execute(
        delete(ChannelLink).where(ChannelLink.user_id == user_id, ChannelLink.channel == channel)
    )
    await db.commit()


async def get_user_pii(db: AsyncSession, user_id: uuid.UUID) -> UserPII:
    user = await db.get(User, user_id)
    if user is None:
        return UserPII(name=None, phone=None, email=None, address=None)
    return UserPII(name=user.display_name, phone=user.phone, email=user.email, address=None)
