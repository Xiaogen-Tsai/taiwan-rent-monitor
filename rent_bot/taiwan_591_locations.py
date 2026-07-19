from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence


# Snapshot of Taiwan's 22 cities/counties and 368 administrative districts,
# mapped to the section IDs exposed by the public 591 rental filters and
# verified 2026-07-19. Two non-administrative legacy section labels exposed by
# 591 are kept separately below for parser compatibility, not user selection.
TAIWAN_591_LOCATIONS: dict[str, tuple[int, dict[str, int]]] = {
    "台北市": (
        1,
        {
            "大安區": 5,
            "內湖區": 10,
            "士林區": 8,
            "文山區": 12,
            "北投區": 9,
            "中山區": 3,
            "信義區": 7,
            "松山區": 4,
            "萬華區": 6,
            "中正區": 1,
            "大同區": 2,
            "南港區": 11,
        },
    ),
    "基隆市": (
        2,
        {
            "安樂區": 17,
            "信義區": 14,
            "七堵區": 19,
            "中正區": 15,
            "中山區": 16,
            "仁愛區": 13,
            "暖暖區": 18,
        },
    ),
    "新北市": (
        3,
        {
            "板橋區": 26,
            "新莊區": 44,
            "中和區": 38,
            "三重區": 43,
            "新店區": 34,
            "土城區": 39,
            "永和區": 37,
            "汐止區": 27,
            "蘆洲區": 47,
            "淡水區": 50,
            "樹林區": 41,
            "林口區": 46,
            "三峽區": 40,
            "五股區": 48,
            "鶯歌區": 42,
            "泰山區": 45,
            "八里區": 49,
            "瑞芳區": 30,
            "深坑區": 28,
            "三芝區": 51,
            "萬里區": 20,
            "金山區": 21,
            "貢寮區": 33,
            "石門區": 52,
            "雙溪區": 32,
            "石碇區": 29,
            "坪林區": 35,
            "烏來區": 36,
            "平溪區": 31,
        },
    ),
    "新竹市": (4, {"東區": 371, "北區": 372, "香山區": 370}),
    "新竹縣": (
        5,
        {
            "竹北市": 54,
            "竹東鎮": 61,
            "湖口鄉": 55,
            "新豐鄉": 56,
            "新埔鎮": 57,
            "關西鎮": 58,
            "芎林鄉": 59,
            "寶山鄉": 60,
            "橫山鄉": 63,
            "尖石鄉": 64,
            "北埔鄉": 65,
            "峨眉鄉": 66,
            "五峰鄉": 62,
        },
    ),
    "桃園市": (
        6,
        {
            "桃園區": 73,
            "中壢區": 67,
            "平鎮區": 68,
            "八德區": 75,
            "楊梅區": 70,
            "龜山區": 74,
            "蘆竹區": 79,
            "龍潭區": 69,
            "大溪區": 76,
            "大園區": 78,
            "觀音區": 72,
            "新屋區": 71,
            "復興區": 77,
        },
    ),
    "苗栗縣": (
        7,
        {
            "頭份市": 81,
            "竹南鎮": 80,
            "苗栗市": 88,
            "苑裡鎮": 87,
            "後龍鎮": 85,
            "通霄鎮": 86,
            "公館鄉": 91,
            "銅鑼鄉": 94,
            "卓蘭鎮": 97,
            "三義鄉": 95,
            "大湖鄉": 92,
            "造橋鄉": 89,
            "頭屋鄉": 90,
            "南庄鄉": 83,
            "西湖鄉": 96,
            "三灣鄉": 82,
            "泰安鄉": 93,
            "獅潭鄉": 84,
        },
    ),
    "台中市": (
        8,
        {
            "北屯區": 103,
            "西屯區": 104,
            "大里區": 107,
            "太平區": 106,
            "南屯區": 105,
            "豐原區": 110,
            "北區": 102,
            "南區": 100,
            "西區": 101,
            "潭子區": 116,
            "沙鹿區": 120,
            "大雅區": 117,
            "清水區": 123,
            "烏日區": 109,
            "龍井區": 121,
            "東區": 99,
            "大甲區": 124,
            "神岡區": 118,
            "霧峰區": 108,
            "梧棲區": 122,
            "大肚區": 119,
            "后里區": 111,
            "東勢區": 113,
            "外埔區": 125,
            "新社區": 115,
            "大安區": 126,
            "中區": 98,
            "石岡區": 112,
            "和平區": 114,
        },
    ),
    "彰化縣": (
        10,
        {
            "彰化市": 127,
            "員林市": 136,
            "和美鎮": 134,
            "鹿港鎮": 131,
            "溪湖鎮": 140,
            "二林鎮": 149,
            "福興鄉": 132,
            "花壇鄉": 129,
            "社頭鄉": 137,
            "大村鄉": 141,
            "田中鎮": 143,
            "伸港鄉": 135,
            "秀水鄉": 130,
            "永靖鄉": 138,
            "埔心鄉": 139,
            "北斗鎮": 144,
            "芳苑鄉": 151,
            "埔鹽鄉": 142,
            "埤頭鄉": 146,
            "溪州鄉": 147,
            "田尾鄉": 145,
            "芬園鄉": 128,
            "線西鄉": 133,
            "大城鄉": 150,
            "竹塘鄉": 148,
            "二水鄉": 152,
        },
    ),
    "南投縣": (
        11,
        {
            "南投市": 153,
            "草屯鎮": 155,
            "埔里鎮": 157,
            "竹山鎮": 164,
            "名間鄉": 159,
            "國姓鄉": 156,
            "鹿谷鄉": 165,
            "水里鄉": 161,
            "仁愛鄉": 158,
            "信義鄉": 163,
            "魚池鄉": 162,
            "中寮鄉": 154,
            "集集鎮": 160,
        },
    ),
    "嘉義市": (12, {"西區": 373, "東區": 374}),
    "嘉義縣": (
        13,
        {
            "民雄鄉": 180,
            "水上鄉": 173,
            "中埔鄉": 171,
            "朴子市": 176,
            "太保市": 175,
            "竹崎鄉": 169,
            "新港鄉": 179,
            "大林鎮": 181,
            "布袋鎮": 184,
            "東石鄉": 177,
            "六腳鄉": 178,
            "梅山鄉": 168,
            "義竹鄉": 183,
            "鹿草鄉": 174,
            "溪口鄉": 182,
            "番路鄉": 167,
            "阿里山鄉": 170,
            "大埔鄉": 172,
        },
    ),
    "雲林縣": (
        14,
        {
            "斗六市": 194,
            "虎尾鎮": 187,
            "麥寮鄉": 193,
            "西螺鎮": 198,
            "斗南鎮": 185,
            "北港鎮": 200,
            "古坑鄉": 196,
            "土庫鎮": 188,
            "莿桐鄉": 197,
            "口湖鄉": 202,
            "二崙鄉": 199,
            "元長鄉": 204,
            "崙背鄉": 192,
            "水林鄉": 201,
            "台西鄉": 191,
            "四湖鄉": 203,
            "大埤鄉": 186,
            "林內鄉": 195,
            "東勢鄉": 190,
            "褒忠鄉": 189,
        },
    ),
    "台南市": (
        15,
        {
            "永康區": 212,
            "安南區": 211,
            "東區": 206,
            "北區": 209,
            "南區": 207,
            "中西區": 208,
            "仁德區": 219,
            "新營區": 230,
            "安平區": 210,
            "歸仁區": 213,
            "佳里區": 224,
            "善化區": 238,
            "麻豆區": 223,
            "新化區": 214,
            "新市區": 241,
            "關廟區": 220,
            "安定區": 242,
            "白河區": 232,
            "西港區": 225,
            "學甲區": 228,
            "鹽水區": 237,
            "下營區": 235,
            "後壁區": 231,
            "六甲區": 234,
            "七股區": 226,
            "官田區": 222,
            "柳營區": 236,
            "東山區": 233,
            "將軍區": 227,
            "玉井區": 216,
            "北門區": 229,
            "大內區": 239,
            "楠西區": 217,
            "南化區": 218,
            "山上區": 240,
            "左鎮區": 215,
            "龍崎區": 221,
        },
    ),
    "高雄市": (
        17,
        {
            "鳳山區": 268,
            "三民區": 250,
            "左營區": 253,
            "楠梓區": 251,
            "前鎮區": 249,
            "苓雅區": 245,
            "小港區": 252,
            "鼓山區": 247,
            "大寮區": 269,
            "仁武區": 254,
            "岡山區": 258,
            "林園區": 270,
            "路竹區": 259,
            "新興區": 243,
            "鳥松區": 271,
            "橋頭區": 263,
            "大樹區": 272,
            "美濃區": 274,
            "梓官區": 264,
            "旗山區": 273,
            "大社區": 255,
            "湖內區": 267,
            "茄萣區": 282,
            "燕巢區": 262,
            "阿蓮區": 260,
            "前金區": 244,
            "旗津區": 248,
            "鹽埕區": 246,
            "彌陀區": 265,
            "永安區": 266,
            "內門區": 276,
            "六龜區": 275,
            "杉林區": 277,
            "田寮區": 261,
            "甲仙區": 278,
            "桃源區": 279,
            "那瑪夏區": 280,
            "茂林區": 281,
        },
    ),
    "屏東縣": (
        19,
        {
            "屏東市": 295,
            "潮州鎮": 308,
            "內埔鄉": 306,
            "萬丹鄉": 307,
            "東港鎮": 316,
            "新園鄉": 319,
            "恆春鎮": 326,
            "長治鄉": 303,
            "里港鄉": 300,
            "鹽埔鄉": 302,
            "枋寮鄉": 320,
            "高樹鄉": 301,
            "九如鄉": 299,
            "萬巒鄉": 311,
            "佳冬鄉": 318,
            "林邊鄉": 315,
            "竹田鄉": 305,
            "崁頂鄉": 312,
            "琉球鄉": 317,
            "麟洛鄉": 304,
            "南州鄉": 314,
            "新埤鄉": 313,
            "車城鄉": 324,
            "三地門鄉": 296,
            "來義鄉": 310,
            "滿州鄉": 327,
            "瑪家鄉": 298,
            "泰武鄉": 309,
            "枋山鄉": 321,
            "春日鄉": 322,
            "獅子鄉": 323,
            "牡丹鄉": 325,
            "霧台鄉": 297,
        },
    ),
    "宜蘭縣": (
        21,
        {
            "宜蘭市": 328,
            "羅東鎮": 333,
            "冬山鄉": 337,
            "五結鄉": 336,
            "蘇澳鎮": 338,
            "礁溪鄉": 330,
            "員山鄉": 332,
            "頭城鎮": 329,
            "壯圍鄉": 331,
            "三星鄉": 334,
            "南澳鄉": 339,
            "大同鄉": 335,
        },
    ),
    "台東縣": (
        22,
        {
            "台東市": 341,
            "卑南鄉": 345,
            "成功鎮": 351,
            "太麻里鄉": 353,
            "關山鎮": 347,
            "東河鄉": 350,
            "池上鄉": 349,
            "鹿野鄉": 346,
            "長濱鄉": 352,
            "大武鄉": 355,
            "蘭嶼鄉": 343,
            "綠島鄉": 342,
            "海端鄉": 348,
            "金峰鄉": 354,
            "延平鄉": 344,
            "達仁鄉": 356,
        },
    ),
    "花蓮縣": (
        23,
        {
            "花蓮市": 357,
            "吉安鄉": 360,
            "玉里鎮": 367,
            "新城鄉": 358,
            "秀林鄉": 359,
            "壽豐鄉": 361,
            "光復鄉": 363,
            "瑞穗鄉": 365,
            "鳳林鎮": 362,
            "富里鄉": 369,
            "萬榮鄉": 366,
            "卓溪鄉": 368,
            "豐濱鄉": 364,
        },
    ),
    "澎湖縣": (
        24,
        {"馬公市": 283, "湖西鄉": 288, "白沙鄉": 287, "西嶼鄉": 284, "望安鄉": 285, "七美鄉": 286},
    ),
    "金門縣": (
        25,
        {"金城鎮": 292, "金寧鄉": 291, "金湖鎮": 290, "金沙鎮": 289, "烈嶼鄉": 293, "烏坵鄉": 294},
    ),
    "連江縣": (
        26,
        {"南竿鄉": 22, "北竿鄉": 23, "東引鄉": 25, "莒光鄉": 24},
    ),
}

# 591 currently exposes these labels under region 26 even though they are not
# Lienchiang County administrative districts. They may still appear in source
# URLs or data, so the parser recognizes them without offering them in
# [locations].
LEGACY_591_SECTIONS_BY_REGION_ID: dict[str, dict[str, str]] = {
    "26": {"256": "東沙", "257": "南沙"},
}


def normalize_location_name(value: str) -> str:
    """Normalize common spelling variants used by Taiwan rental sources."""

    return value.strip().replace("臺", "台").replace("峨嵋鄉", "峨眉鄉")


CITY_BY_REGION_ID: dict[str, str] = {
    str(region_id): city for city, (region_id, _districts) in TAIWAN_591_LOCATIONS.items()
}
CITY_NAMES_BY_LENGTH: tuple[str, ...] = tuple(
    sorted(TAIWAN_591_LOCATIONS, key=lambda item: (-len(item), item))
)
SECTION_BY_REGION_ID: dict[str, dict[str, str]] = {
    str(region_id): {str(section_id): district for district, section_id in districts.items()}
    for _city, (region_id, districts) in TAIWAN_591_LOCATIONS.items()
}
for _region_id, _legacy_sections in LEGACY_591_SECTIONS_BY_REGION_ID.items():
    SECTION_BY_REGION_ID.setdefault(_region_id, {}).update(_legacy_sections)

DISTRICT_CITIES: dict[str, tuple[str, ...]] = {}
for _city, (_region_id, _districts) in TAIWAN_591_LOCATIONS.items():
    for _district in _districts:
        DISTRICT_CITIES[_district] = (*DISTRICT_CITIES.get(_district, ()), _city)

# Longest-first prevents a short name such as 中區 from winning before 中正區.
DISTRICT_NAMES_BY_LENGTH: tuple[str, ...] = tuple(
    sorted(DISTRICT_CITIES, key=lambda item: (-len(item), item))
)


def normalize_and_validate_locations(
    locations: Mapping[str, Iterable[str]],
    *,
    label: str = "locations",
) -> dict[str, set[str]]:
    normalized: dict[str, set[str]] = {}
    for raw_city, raw_districts in locations.items():
        city = normalize_location_name(raw_city)
        city_entry = TAIWAN_591_LOCATIONS.get(city)
        if city_entry is None:
            valid = "、".join(TAIWAN_591_LOCATIONS)
            raise ValueError(f"Unknown Taiwan city/county {raw_city!r} in {label}. Valid values: {valid}")

        if isinstance(raw_districts, str):
            raise ValueError(f"Districts for {city} in {label} must be an array, not a string")
        districts = {normalize_location_name(item) for item in raw_districts}
        if not districts:
            raise ValueError(f"At least one district or '*' is required for {city} in {label}")
        if "*" in districts:
            if len(districts) != 1:
                raise ValueError(f"Use '*' by itself for {city} in {label}")
            normalized[city] = {"*"}
            continue
        unknown = sorted(districts - set(city_entry[1]))
        if unknown:
            valid = "、".join(city_entry[1])
            names = "、".join(unknown)
            raise ValueError(f"Unknown district(s) for {city} in {label}: {names}. Valid values: {valid}")
        normalized.setdefault(city, set()).update(districts)
    return normalized


def build_591_search_urls(
    locations: Mapping[str, Iterable[str]],
    *,
    kinds: Sequence[int] = (2, 3),
) -> list[str]:
    """Build one URL per room kind and district for reliable first-page coverage."""

    normalized = normalize_and_validate_locations(locations, label="591 locations")
    urls: list[str] = []
    for city, (region_id, district_ids) in TAIWAN_591_LOCATIONS.items():
        selected = normalized.get(city)
        if not selected:
            continue
        if selected == {"*"}:
            urls.extend(
                f"https://rent.591.com.tw/list?kind={kind}&region={region_id}" for kind in kinds
            )
            continue
        for district, section_id in district_ids.items():
            if district not in selected:
                continue
            for kind in kinds:
                urls.append(
                    f"https://rent.591.com.tw/list?kind={kind}&region={region_id}&section={section_id}"
                )
    if len(urls) > 120:
        raise ValueError(
            f"Automatic 591 location selection would generate {len(urls)} URLs (limit: 120). "
            "Use ['*'] for whole-city searches, select fewer districts, or provide advanced search_urls."
        )
    return urls


def city_for_district(district: str, *, preferred_city: str | None = None) -> str | None:
    district = normalize_location_name(district)
    candidates = DISTRICT_CITIES.get(district, ())
    if preferred_city:
        preferred_city = normalize_location_name(preferred_city)
        if preferred_city in candidates:
            return preferred_city
    return candidates[0] if len(candidates) == 1 else None
