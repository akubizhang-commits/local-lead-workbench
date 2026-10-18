"""Chinese labels shared by exports and API ``*_label`` fields (design §5.1)."""
from .contact_tiers import TIER_LABEL

TIER_LABELS = dict(TIER_LABEL)
CHANNEL_LABELS = {
    "email": "邮箱", "phone": "电话", "contact_form": "联系表单", "supplier_form": "供应商表单",
    "page": "联系页面", "none": "无直接渠道（仅姓名/岗位）",
}
STATUS_LABELS = {"candidate": "候选企业", "verified": "符合目标客户", "not_fit": "不符合目标客户",
                 "do_not_contact": "拒绝联系"}
# "进口贸易商" matches market_store.market_name so the UI and exports agree.
BUSINESS_TYPE_LABELS = {"wholesaler": "批发商", "wholesale": "批发商", "retailer": "零售商", "retail": "零售商",
                        "importer": "进口贸易商", "import": "进口贸易商", "manufacturer": "制造商",
                        "distributor": "分销商"}
REVIEW_LABELS = {"manual": "人工录入", "confirmed": "已人工确认", "automatic": "自动采集，未人工确认"}
SOURCE_LABELS = {"manual": "人工录入"}


def business_type_label(value, hint=False):
    text = BUSINESS_TYPE_LABELS.get(str(value).strip().lower(), str(value))
    return text + "（待核实）" if hint else text
