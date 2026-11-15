from __future__ import annotations

from dataclasses import dataclass

import httpx

from dodeed.config import Settings
from dodeed.sources import SourceUnavailable, get_json

BASE_URL = "https://dapi.kakao.com"
KEYWORD_PATH = "/v2/local/search/keyword.json"
REGION_PATH = "/v2/local/geo/coord2regioncode.json"


@dataclass
class Place:
    id: str
    name: str
    category: str
    road_address: str
    lat: float
    lng: float
    phone: str
    place_url: str


def _to_place(d: dict) -> Place:
    return Place(
        id=str(d["id"]),
        name=d["place_name"],
        category=d.get("category_name", ""),
        road_address=d.get("road_address_name") or d.get("address_name", ""),
        lat=float(d["y"]),
        lng=float(d["x"]),
        phone=d.get("phone", ""),
        place_url=d.get("place_url", ""),
    )


class KakaoLocal:
    def __init__(self, http: httpx.AsyncClient, settings: Settings) -> None:
        self._http = http
        self._headers = {"Authorization": f"KakaoAK {settings.kakao_rest_api_key}"}

    async def _get(self, path: str, params: dict) -> dict:
        data = await get_json(self._http, BASE_URL + path, params=params, headers=self._headers)
        if not isinstance(data, dict):
            raise SourceUnavailable("예상하지 못한 응답 형식")
        return data

    async def search_keyword(
        self,
        query: str,
        x: float | None,
        y: float | None,
        radius_m: int = 1000,
        size: int = 15,
    ) -> list[Place]:
        params: dict = {"query": query, "category_group_code": "FD6", "size": size}
        if x is not None and y is not None:
            params.update({"x": x, "y": y, "radius": radius_m, "sort": "distance"})
        else:
            params["sort"] = "accuracy"
        data = await self._get(KEYWORD_PATH, params)
        try:
            return [_to_place(d) for d in data.get("documents", [])]
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceUnavailable("예상하지 못한 응답 형식") from exc

    async def geocode_area(self, query: str) -> tuple[float, float, str] | None:
        data = await self._get(KEYWORD_PATH, {"query": query, "size": 1})
        docs = data.get("documents", [])
        if not docs:
            return None
        try:
            return float(docs[0]["y"]), float(docs[0]["x"]), docs[0]["place_name"]
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceUnavailable("예상하지 못한 응답 형식") from exc

    async def region_name(self, lat: float, lng: float) -> str | None:
        data = await self._get(REGION_PATH, {"x": lng, "y": lat})
        docs = data.get("documents", [])
        for kind in ("H", "B"):
            for d in docs:
                if d.get("region_type") == kind and d.get("region_3depth_name"):
                    return d["region_3depth_name"]
        return None
