import math

EARTH_RADIUS_M = 6_371_000
DETOUR_FACTOR = 1.3
WALK_M_PER_MIN = 67


def walking_minutes(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    """직선거리(하버사인) x 1.3 / 67m 분을 올림한 도보 추정 시간."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    dist = 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))
    return math.ceil(dist * DETOUR_FACTOR / WALK_M_PER_MIN)
