"""
领域模板预设（种子常量）— 迁移与代码共用的单一事实源。

结构约定（docs/10-generic-kb-v2-design.md §2.2）：
- entity  : {code, name, color, description, examples?}
    code 为 Neo4j 节点标签与 type 属性值，须匹配 ^[A-Za-z][A-Za-z0-9_]*$；
- relation: {name, description, direction_hint?}（关系名中文，Neo4j 反引号写库）。

约束常量供 domain_service 校验复用：
- 实体数量范围 3..20；关系数量 0..30；
- 系统保留标签 Entity / Document 不得作为实体 code。
"""
import re

ENTITY_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
ENTITY_COUNT_MIN = 3
ENTITY_COUNT_MAX = 20
RELATION_COUNT_MAX = 30
RESERVED_LABELS = {"Entity", "Document"}

# 种子固定 ID（UUID 风格；迁移 0024 与代码缺省回退共用）
GENERIC_PRESET_ID = "00000000-0000-4000-8000-000000000001"
BID_PRESET_ID = "00000000-0000-4000-8000-000000000002"

# 内置"通用"预设种子（对应设计 D4：必选）
GENERIC_PRESET = {
    "name": "通用",
    "description": "不绑定具体领域的通用本体，适合任意语料起步（金额/时间等字面值会作为独立实体）。",
    "entity_types": [
        {"code": "Organization", "name": "组织/机构", "color": "#409EFF",
         "description": "组织、机构、公司、部门、团体", "examples": ["甲公司", "市交通局"]},
        {"code": "Person", "name": "人员", "color": "#67C23A",
         "description": "人物（法人、负责人、联系人等）", "examples": ["张三"]},
        {"code": "Place", "name": "地点", "color": "#E6A23C",
         "description": "地点、地理位置、区域", "examples": ["北京市"]},
        {"code": "Event", "name": "事件", "color": "#F56C6C",
         "description": "事件、活动、会议", "examples": ["开标会", "验收会"]},
        {"code": "Time", "name": "时间", "color": "#909399",
         "description": "时间点、日期", "examples": ["2026年1月1日"]},
        {"code": "Project", "name": "项目", "color": "#9254DE",
         "description": "项目、工程、任务", "examples": ["XX 项目"]},
        {"code": "Product", "name": "产品", "color": "#13C2C2",
         "description": "产品、物品、资产、系统、货物", "examples": ["服务器", "软件系统"]},
        {"code": "Quantity", "name": "数量", "color": "#EB2F96",
         "description": "数量、金额、度量值（必须含单位）", "examples": ["3000万元", "50吨"]},
        {"code": "Concept", "name": "概念", "color": "#9C27B0",
         "description": "概念、资质、标准、制度、条款、技能", "examples": ["质量管理体系", "一级资质"]},
    ],
    "relation_types": [
        {"name": "属于", "description": "人员/组织隶属于某组织", "direction_hint": "Person|Organization→Organization"},
        {"name": "位于", "description": "组织/项目/事件所在的地点", "direction_hint": "Organization|Project|Event→Place"},
        {"name": "发生在", "description": "事件/项目发生的时间或地点", "direction_hint": "Event|Project→Time|Place"},
        {"name": "参与", "description": "人员/组织参与事件或项目", "direction_hint": "Person|Organization→Event|Project"},
        {"name": "负责", "description": "人员/组织负责项目、产品、事件", "direction_hint": "Person|Organization→Project|Product|Event"},
        {"name": "合作", "description": "组织间/人员间合作关系", "direction_hint": "Organization|Person→Organization|Person"},
        {"name": "关联", "description": "语义不明确的通用关系（兜底）", "direction_hint": "任意→任意"},
        {"name": "包含", "description": "项目/组织/概念包含的内容", "direction_hint": "Project|Event|Organization→任意"},
        {"name": "要求", "description": "项目/产品对资质、标准、人员的要求", "direction_hint": "Project|Product→Concept|Product|Person"},
        {"name": "拥有", "description": "组织/人员拥有的资质、产品", "direction_hint": "Organization|Person→Product|Concept"},
        {"name": "数量", "description": "实体对应的数量/金额度量", "direction_hint": "任意→Quantity"},
        {"name": "日期", "description": "实体对应的时间/日期", "direction_hint": "任意→Time"},
    ],
}

# 内置"投标"预设种子（对应设计 D4：待确认默认采纳；供现有投标语料直接使用）
BID_PRESET = {
    "name": "投标",
    "description": "招投标/工程建设领域本体（继承 v1 投标语义：中标/招标/资质要求/保证金等）。",
    "entity_types": [
        {"code": "Organization", "name": "组织/公司", "color": "#409EFF",
         "description": "公司、招标人、投标人、代理机构、监管单位", "examples": ["甲公司", "市交通局"]},
        {"code": "Person", "name": "人员", "color": "#67C23A",
         "description": "法人、项目负责人、联系人、评标专家", "examples": ["张三"]},
        {"code": "Project", "name": "项目/标段", "color": "#9254DE",
         "description": "招标项目、投标项目、标段、包件", "examples": ["XX 市政工程"]},
        {"code": "Qualification", "name": "资质/证书", "color": "#9C27B0",
         "description": "资质、证书、许可、等级", "examples": ["建筑工程施工总承包一级"]},
        {"code": "Product", "name": "货物/服务", "color": "#13C2C2",
         "description": "货物、设备、材料、服务标的物", "examples": ["变压器", "运维服务"]},
        {"code": "Money", "name": "金额", "color": "#EB2F96",
         "description": "报价、合同金额、保证金金额", "examples": ["3000万元"]},
        {"code": "Time", "name": "时间", "color": "#909399",
         "description": "开标时间、截止时间、工期节点", "examples": ["2026年8月1日"]},
        {"code": "Place", "name": "地点", "color": "#E6A23C",
         "description": "项目地点、开标地点、公司地址", "examples": ["北京市朝阳区"]},
        {"code": "Event", "name": "事件", "color": "#F56C6C",
         "description": "开标、评标、定标、签约、验收", "examples": ["开标", "合同签约"]},
    ],
    "relation_types": [
        {"name": "招标", "description": "招标人发布某招标项目", "direction_hint": "Organization→Project"},
        {"name": "投标", "description": "公司参与投标某项目（未中标）", "direction_hint": "Organization→Project"},
        {"name": "中标", "description": "公司中标某项目", "direction_hint": "Organization→Project"},
        {"name": "分包", "description": "公司间分包关系", "direction_hint": "Organization→Organization"},
        {"name": "合作", "description": "公司间合作/联合体关系", "direction_hint": "Organization→Organization"},
        {"name": "隶属于", "description": "人员隶属于某公司（法人/任职）", "direction_hint": "Person→Organization"},
        {"name": "要求资质", "description": "项目对资质/证书的要求", "direction_hint": "Project→Qualification"},
        {"name": "拥有资质", "description": "公司拥有的资质/证书", "direction_hint": "Organization→Qualification"},
        {"name": "合同金额", "description": "项目的中标/合同金额", "direction_hint": "Project→Money"},
        {"name": "缴纳保证金", "description": "公司向项目缴纳的保证金", "direction_hint": "Organization→Money"},
        {"name": "负责", "description": "人员负责某项目（负责人）", "direction_hint": "Person→Project"},
        {"name": "位于", "description": "项目/公司所在的地点", "direction_hint": "Project|Organization→Place"},
        {"name": "发生在", "description": "事件发生的时间/地点", "direction_hint": "Event→Time|Place"},
        {"name": "包含", "description": "项目包含的事件、标的物", "direction_hint": "Project→Event|Product"},
        {"name": "关联", "description": "语义不明确的通用关系（兜底）", "direction_hint": "任意→任意"},
    ],
}

PRESETS: list[dict] = [GENERIC_PRESET, BID_PRESET]
