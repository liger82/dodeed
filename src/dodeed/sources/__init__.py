"""외부 출처 클라이언트 공통부: 오류 형식과 재시도 규칙."""

from __future__ import annotations

from typing import Any

import httpx

HTTP_TIMEOUT_S = 10.0


class SourceUnavailable(Exception):
    """외부 출처가 응답하지 않거나 오류를 돌려줬다."""


async def get_json(
    http: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """GET 후 JSON을 돌려준다. 429/5xx는 1회 재시도, 그 밖의 4xx는 즉시 실패."""
    for attempt in range(2):
        try:
            resp = await http.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT_S)
        except httpx.HTTPError as exc:
            raise SourceUnavailable(f"요청 실패: {type(exc).__name__}") from exc
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == 0:
                continue
            raise SourceUnavailable(f"상태 코드 {resp.status_code}")
        if resp.status_code >= 400:
            raise SourceUnavailable(f"상태 코드 {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise SourceUnavailable("JSON 해석 실패") from exc
    raise SourceUnavailable("재시도 초과")  # pragma: no cover
