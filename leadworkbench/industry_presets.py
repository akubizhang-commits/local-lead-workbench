"""Preset industries for market keywords (design §8.1, decision D1).

Each preset has exactly one English Maps search term per company type. A preset
expands to the union of the selected types' terms; with no type selected it
uses the wholesale term.
"""
from .market_store import normalized

PRESETS = (
    ("apparel", "服装", "clothing wholesaler", "clothing store", "garment importer"),
    ("footwear", "鞋类", "shoe wholesaler", "shoe store", "footwear importer"),
    ("building", "建材与屋面", "roofing supply", "building materials store", "building materials importer"),
    ("metals", "金属材料", "metal supplier", "metal supply store", "steel importer"),
    ("hardware", "五金工具", "hardware wholesaler", "hardware store", "tool importer"),
    ("electronics", "电子产品", "electronics wholesaler", "electronics store", "electronics importer"),
    ("furniture", "家具家居", "furniture wholesaler", "furniture store", "furniture importer"),
    ("food", "食品饮料", "food wholesaler", "grocery store", "food importer"),
    ("beauty", "美妆个护", "beauty supply wholesaler", "beauty supply store", "cosmetics importer"),
    ("auto_parts", "汽车配件", "auto parts wholesaler", "auto parts store", "auto parts importer"),
)
TYPES = ("wholesale", "retail", "import")
BY_ID = {row[0]: {"id": row[0], "label": row[1], "terms": dict(zip(TYPES, row[2:]))} for row in PRESETS}
MAX_KEYWORDS = 30
TOO_MANY = "关键词和预设展开后最多 30 个"
UNKNOWN = "未知的预设行业"


def presets():
    """GET /api/industry-presets payload; fresh dicts on every call."""
    return [{"id": p["id"], "label": p["label"], "terms": dict(p["terms"])} for p in BY_ID.values()]


def expand(industry, company_types):
    """Search terms of a preset for the selected company types (wholesale when none)."""
    if not industry:
        return []
    if industry not in BY_ID:
        raise ValueError(UNKNOWN)
    types = [t for t in TYPES if t in set(company_types or ())] or ["wholesale"]
    return [BY_ID[industry]["terms"][t] for t in types]


def merge_keywords(keywords, industry, company_types):
    """Keywords plus the preset's expansion: normalized, deduplicated and sorted.

    The single expansion rule for creating a market (TaskInput) and editing one
    (Store.update_market_keywords, with the market's stored company_types)."""
    values = list(keywords or []) + expand(industry, company_types)
    return sorted({normalized(v) for v in values if normalized(v)})
