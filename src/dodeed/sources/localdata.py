"""행정안전부 식품_일반음식점 조회서비스 (공공데이터포털, 15154916) 클라이언트.

확인된 것: 엔드포인트, 요청 변수(serviceKey, pageNo, numOfRows, returnType,
cond[BPLC_NM::LIKE], cond[ROAD_NM_ADDR::LIKE]).
미확인: 응답 필드명(문서에 없음). 아래 파싱은 알려진 표준 필드명을 쓰는 최선 추정이며,
모양이 다르면 "unknown"으로 안전하게 실패한다.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx

from dodeed.config import Settings
from dodeed.sources import SourceUnavailable, get_json

BASE_URL = "https://apis.data.go.kr/1741000/general_restaurants"
Status = Literal["open", "closed", "unknown"]


def _norm(s: str) -> str:
    return "".join(s.split())


def _items(data: Any) -> list[dict]:
    body = data["response"]["body"]
    items = body["items"]
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]
    return [i for i in items if isinstance(i, dict)]


def _status(row: dict) -> Status:
    name = str(row.get("SALS_STTS_NM") or row.get("DTL_SALS_STTS_NM") or "")
    code = str(row.get("SALS_STTS_CD") or "")
    if "폐업" in name or code == "03":
        return "closed"
    if "영업" in name or "정상" in name or code == "01":
        return "open"
    return "unknown"


class LocalData:
    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        self._http = http
        self._key = settings.localdata_api_key

    async def business_status(self, name: str, road_address: str) -> Status:
        params = {
            "serviceKey": self._key,
            "pageNo": 1,
            "numOfRows": 100,
            "returnType": "json",
            "cond[BPLC_NM::LIKE]": name,
            "cond[ROAD_NM_ADDR::LIKE]": road_address,
        }
        try:
            data = await get_json(self._http, BASE_URL, params=params)
            rows = _items(data)
        except (SourceUnavailable, KeyError, TypeError, AttributeError):
            return "unknown"
        n, a = _norm(name), _norm(road_address)
        for row in rows:
            if (
                _norm(str(row.get("BPLC_NM", ""))) == n
                and _norm(str(row.get("ROAD_NM_ADDR", ""))) == a
            ):
                return _status(row)
        return "unknown"
