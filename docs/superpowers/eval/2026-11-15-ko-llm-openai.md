# 한국어 LLM 품질 비교

- 사례 25개, 시스템 프롬프트는 운영과 같은 오케스트레이터 `SYSTEM`, 도구는 `search_restaurants` 하나
- 도구 선택: 기대한 도구를 불렀는지(도구가 없어야 하는 사례는 부르지 않았는지)
- 인자 일치: 사례에 적은 인자가 모두 같은 값인지(부분 일치, 앞뒤 공백 무시)
- 단가(1M 토큰당 입력/출력 USD): gpt-6-luna $0.10/$0.50, gemini-3.5-flash-lite $0.30/$2.50

## 요약

| 모델 | 도구 선택 | 인자 일치 | 오류 | 평균 지연(ms) | 입력 토큰 | 출력 토큰 | 추정 비용(USD) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| OpenAI (gpt-6-luna) | 25/25 | 25/25 | 0 | 1320 | 16501 | 872 | $0.002086 |

## 사례별 결과

| # | 발화 | 기대 | OpenAI |
| --- | --- | --- | --- |
| 1 | 강남역에서 혼밥하기 좋은 국밥집 찾아줘 | {"situation": "solo", "area_query": "강남역", "cuisine": "국밥"} | OO search_restaurants {"area_query": "강남역", "cuisine": "국밥", "situation": "solo"} |
| 2 | 혼자 밥 먹을 만한 데 있어? 신촌역 근처로 | {"situation": "solo", "area_query": "신촌역"} | OO search_restaurants {"area_query": "신촌역", "situation": "solo", "use_current_location": false} |
| 3 | 여의도에서 혼밥할 라멘집 알려줘 | {"situation": "solo", "area_query": "여의도", "cuisine": "라멘"} | OO search_restaurants {"area_query": "여의도", "cuisine": "라멘", "situation": "solo"} |
| 4 | 판교역 혼밥 맛집 추천해줘. 화장실은 가게 안에 있었으면 좋겠어 | {"situation": "solo", "area_query": "판교역", "toilet_inside": true} | OO search_restaurants {"area_query": "판교역", "situation": "solo", "toilet_inside": true, "use_current_location": false} |
| 5 | 오늘 점심 혼자 먹을 건데 종로3가 쪽 백반집 있을까? | {"situation": "solo", "area_query": "종로3가", "cuisine": "백반"} | OO search_restaurants {"area_query": "종로3가", "cuisine": "백반", "situation": "solo", "time_text": "오늘 점심"} |
| 6 | 성수역 근처 데이트하기 좋은 파스타집 찾아줘 | {"situation": "date", "area_query": "성수역", "cuisine": "파스타"} | OO search_restaurants {"area_query": "성수역", "cuisine": "파스타", "situation": "date"} |
| 7 | 여자친구랑 이태원에서 저녁 먹을 분위기 좋은 곳 알려줘 | {"situation": "date", "area_query": "이태원"} | OO search_restaurants {"area_query": "이태원", "situation": "date"} |
| 8 | 기념일 데이트로 한남동 스테이크 집 추천해줘 | {"situation": "date", "area_query": "한남동", "cuisine": "스테이크"} | OO search_restaurants {"area_query": "한남동", "cuisine": "스테이크", "situation": "date", "use_current_location": false} |
| 9 | 연남동에서 소개팅할 만한 식당 있어? | {"situation": "date", "area_query": "연남동"} | OO search_restaurants {"area_query": "연남동", "situation": "date"} |
| 10 | 남자친구랑 잠실역에서 초밥 먹고 싶어 | {"situation": "date", "area_query": "잠실역", "cuisine": "초밥"} | OO search_restaurants {"area_query": "잠실역", "cuisine": "초밥", "situation": "date"} |
| 11 | 신림역 가성비 좋은 김치찌개집 찾아줘 | {"situation": "value", "area_query": "신림역", "cuisine": "김치찌개"} | OO search_restaurants {"area_query": "신림역", "cuisine": "김치찌개", "situation": "value"} |
| 12 | 노량진에서 만 원 이하로 배부르게 먹을 데 알려줘 | {"situation": "value", "area_query": "노량진", "price_max": 10000} | OO search_restaurants {"area_query": "노량진", "price_max": 10000, "situation": "value"} |
| 13 | 회기역 근처 싸고 양 많은 집 있어? | {"situation": "value", "area_query": "회기역"} | OO search_restaurants {"area_query": "회기역", "situation": "value", "use_current_location": false} |
| 14 | 구로디지털단지역에서 8천 원 안쪽 점심 가성비 맛집 | {"situation": "value", "area_query": "구로디지털단지역", "price_max": 8000} | OO search_restaurants {"area_query": "구로디지털단지역", "price_max": 8000, "situation": "value", "time_text": "점심"} |
| 15 | 건대입구 가성비 돈까스 추천해줘 | {"situation": "value", "area_query": "건대입구", "cuisine": "돈까스"} | OO search_restaurants {"area_query": "건대입구", "cuisine": "돈까스", "situation": "value"} |
| 16 | 홍대입구역에서 오늘 7시에 4명이 삼겹살 먹을 곳 찾아줘 | {"area_query": "홍대입구역", "cuisine": "삼겹살", "time_text": "오늘 7시", "party_size": 4} | OO search_restaurants {"area_query": "홍대입구역", "cuisine": "삼겹살", "party_size": 4, "time_text": "오늘 7시"} |
| 17 | 내일 점심에 3명이서 광화문 쪽 칼국수집 갈 건데 찾아줄래? | {"area_query": "광화문", "cuisine": "칼국수", "time_text": "내일 점심", "party_size": 3} | OO search_restaurants {"area_query": "광화문", "cuisine": "칼국수", "party_size": 3, "time_text": "내일 점심"} |
| 18 | 토요일 저녁 6시 반에 6명 모임인데 서울대입구역 근처 중국집 있을까 | {"area_query": "서울대입구역", "cuisine": "중국집", "time_text": "토요일 저녁 6시 반", "party_size": 6} | OO search_restaurants {"area_query": "서울대입구역", "cuisine": "중국집", "party_size": 6, "time_text": "토요일 저녁 6시 반"} |
| 19 | 금요일 8시에 둘이서 압구정로데오 이자카야 가려고 | {"area_query": "압구정로데오", "cuisine": "이자카야", "time_text": "금요일 8시", "party_size": 2} | OO search_restaurants {"area_query": "압구정로데오", "cuisine": "이자카야", "party_size": 2, "time_text": "금요일 8시"} |
| 20 | 오늘 밤 9시 이후에 5명이 수원역에서 치킨 먹을 데 알려줘 | {"area_query": "수원역", "cuisine": "치킨", "time_text": "오늘 밤 9시 이후", "party_size": 5} | OO search_restaurants {"area_query": "수원역", "cuisine": "치킨", "party_size": 5, "time_text": "오늘 밤 9시 이후"} |
| 21 | 지금 여기서 걸어갈 만한 밥집 있어? | {"use_current_location": true} | OO search_restaurants {"use_current_location": true} |
| 22 | 내 근처에 지금 문 연 국수집 찾아줘 | {"use_current_location": true, "cuisine": "국수"} | OO search_restaurants {"cuisine": "국수", "use_current_location": true} |
| 23 | 여기 주변에서 혼자 먹기 괜찮은 데 알려줘 | {"use_current_location": true, "situation": "solo"} | OO search_restaurants {"situation": "solo", "use_current_location": true} |
| 24 | 안녕! 너는 뭘 할 수 있어? | 도구 없음 | OO (도구 없이 답변) |
| 25 | 고마워, 덕분에 맛있게 먹었어 | 도구 없음 | OO (도구 없이 답변) |

표의 `OO`/`OX`/`XX`는 (도구 선택, 인자 일치) 결과입니다.
