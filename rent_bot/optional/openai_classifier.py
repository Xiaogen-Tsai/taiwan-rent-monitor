from __future__ import annotations

import json
import logging

from openai import OpenAI

from rent_bot.filters import keyword_classify
from rent_bot.models import Classification, Listing

logger = logging.getLogger(__name__)


SCHEMA = {
    "type": "object",
    "properties": {
        "is_suite": {"type": "boolean"},
        "has_rent_subsidy": {"type": ["boolean", "null"]},
        "has_tax_registration": {"type": ["boolean", "null"]},
        "has_independent_washer": {"type": ["boolean", "null"]},
        "has_garbage_collection": {"type": ["boolean", "null"]},
        "red_flags": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "score_reason": {"type": "string"},
    },
    "required": [
        "is_suite",
        "has_rent_subsidy",
        "has_tax_registration",
        "has_independent_washer",
        "has_garbage_collection",
        "red_flags",
        "summary",
        "score_reason",
    ],
    "additionalProperties": False,
}


def classify_with_openai(listing: Listing, api_key: str, model: str) -> Classification:
    client = OpenAI(api_key=api_key)
    text = listing.text_for_classification()[:6000]
    prompt = f"""
請判斷以下台灣租屋房源是否符合套房需求，並只輸出 JSON。

判斷原則：
- is_suite: 只在明確是套房、獨立套房、分租套房時為 true；雅房、整層住家、店面、車位為 false。
- has_rent_subsidy: 有租金補貼、租補、可申請補助時 true；明確不可租補時 false；沒提到 null。
- has_tax_registration: 有可報稅、可設籍、社宅、社會住宅時 true；明確不可報稅時 false；沒提到 null。
- has_independent_washer: 有獨立洗衣機、室內洗衣機、專用洗衣機、獨洗時 true；共用/投幣洗衣機 false；沒提到 null。
- has_garbage_collection: 有垃圾代收、代收垃圾、垃圾代丟、代丟垃圾時 true；明確需自行倒垃圾時 false；沒提到 null。
- red_flags: 列出雅房、整層住家、短租、頂加、違建、隔間差、需仲介費等風險。
- summary: 用繁中 80 字內摘要。
- score_reason: 用繁中簡短說明判斷依據。

房源內容：
{text}
"""
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是嚴謹的台灣租屋資訊分類器，只能回傳 JSON。"},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        content = response.choices[0].message.content or "{}"
        data = json.loads(content)
        return Classification.model_validate(data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenAI classifier failed; using keyword fallback: %s", exc)
        return keyword_classify(listing)
