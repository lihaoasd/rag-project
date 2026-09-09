# 投标领域实体词库构建方案

> 版本: v1.0 | 日期: 2026-08-07 | 状态: 初稿
>
> **v2 修订（2026-09-08）**：词库与领域模板建立**多对多映射**（词条 × 领域模板 → 该模板内实体 code），取代本文档的“全局固定分类 → 固定实体类型”机制；词库回归全局服务 IK 分词与查询同义词扩展。权威决策见 `docs/10-generic-kb-v2-design.md §7`，落地方案见 `docs/plan/48-lexicon-domain-mapping-plan.md`。

---

## 1. 概述

### 1.1 目标

构建一份结构化的投标领域实体词库，用于替代（或辅助）LLM 完成实体抽取，实现零成本、毫秒级的实体识别。

### 1.2 定位

```
                           ┌─────────────┐
                           │   实体抽取    │
                           └──────┬──────┘
                                  │
              ┌───────────────────┼───────────────────┐
              ▼                   ▼                   ▼
     ┌────────────┐      ┌────────────┐      ┌────────────┐
     │  正则模式   │      │   词库     │      │  jieba NER │
     │            │      │  (本文档)  │      │            │
     │ 金额/日期/ │      │ 公司/项目/ │      │  人名补漏  │
     │ 编号/资质  │      │ 术语/地点  │      │            │
     └────────────┘      └────────────┘      └────────────┘
                                  │
                                  ▼
                           ┌─────────────┐
                           │  实体去重合并 │
                           └──────┬──────┘
                                  │
                                  ▼
                           ┌─────────────┐
                           │   Neo4j 写入 │
                           └─────────────┘
```

### 1.3 词库 vs LLM

| 维度 | 词库 | LLM |
|------|------|-----|
| 实体类型覆盖 | 需要人工维护，覆盖度逐渐提升 | 自动发现新实体，但有遗漏和幻觉 |
| 成本 | 零 | 每个 chunk 都调 API |
| 速度 | 毫秒级 | 秒级 |
| 稳定性 | 100% 稳定（确定性匹配） | AI 非确定性输出 |
| 关系抽取 | 做不到 | 能 |
| 适用 | **高频实体**（公司名、项目名） | **低频/长尾实体** |

**策略**：词库覆盖 80% 的高频实体，LLM 作为补充覆盖长尾和关系抽取。

---

## 2. 词库结构设计

### 2.1 目录结构

```
backend/
├── lexicon/
│   ├── __init__.py
│   ├── loader.py                # 词库加载器
│   ├── validator.py             # 词库校验工具
│   │
│   ├── core/                    # 核心词库（手动维护）
│   │   ├── organizations.txt    # 公司/机构名
│   │   ├── projects.txt         # 项目名称
│   │   ├── persons.txt          # 人名
│   │   ├── locations.txt        # 地点
│   │   ├── qualifications.txt   # 资质/证书
│   │   ├── money_patterns.json  # 金额正则模式
│   │   ├── date_patterns.json   # 日期正则模式
│   │   └── bid_terms.txt        # 投标领域术语
│   │
│   ├── auto/                    # 自动生成词库（LLM 辅助）
│   │   ├── discovered_orgs.txt  # LLM 发现的新公司名
│   │   └── ...
│   │
│   └── custom/                  # 自定义词库（用户上传）
│       └── {kb_id}/
│           ├── organizations.txt
│           └── projects.txt
```

### 2.2 词库文件格式

每个 `.txt` 文件格式为 **一行一实体**，字段用 TAB 分隔：

```
# organizations.txt
# 格式: 实体名称	别名（逗号分隔）	描述
甲公司	甲建设集团,甲建设	建筑施工企业，年产值50亿
乙公司	乙工程有限公司	桥梁工程专业分包商
市交通局	XX市交通运输局	招标方
```

```json
// money_patterns.json
{
  "patterns": [
    {"regex": "(?:人民币\\s*)?([壹贰叁肆伍陆柒捌玖拾佰仟万亿零整]+元)", "group": 1, "currency": "CNY"},
    {"regex": "(\\d[\\d,]*\\.?\\d*\\s*[万亿千百]?\\s*元)", "group": 1, "currency": "CNY"},
    {"regex": "[¥￥]\\s*(\\d[\\d,]*\\.?\\d*)", "group": 1, "currency": "CNY"}
  ]
}
```

### 2.3 实体类型定义

```python
# backend/lexicon/entity_types.py

from enum import Enum

class EntityType(str, Enum):
    ORGANIZATION = "Organization"       # 公司、机构、政府部门
    PROJECT = "Project"                 # 投标项目、工程项目
    PERSON = "Person"                   # 人名
    MONEY = "Money"                     # 金额
    DATE = "Date"                       # 日期
    LOCATION = "Location"              # 地点
    QUALIFICATION = "Qualification"    # 资质、证书、许可
    DOCUMENT_TYPE = "DocumentType"     # 文件类型（招标文件、合同等）
    PROJECT_NO = "ProjectNo"           # 项目编号、招标编号
    BID_TERM = "BidTerm"              # 投标专业术语

# 优先级：实体类型在词库匹配时的优先级（数字越小越优先）
ENTITY_PRIORITY = {
    EntityType.PROJECT_NO: 1,      # 编号最精确，优先匹配
    EntityType.MONEY: 2,           # 金额正则明确，次优先
    EntityType.DATE: 2,
    EntityType.QUALIFICATION: 3,   # 资质有固定模式
    EntityType.ORGANIZATION: 4,    # 词库匹配
    EntityType.PROJECT: 4,
    EntityType.PERSON: 5,
    EntityType.LOCATION: 5,
    EntityType.BID_TERM: 6,
}
```

---

## 3. 词库来源

### 3.1 五类来源

| 序号 | 来源 | 说明 | 适用实体类型 |
|------|------|------|-------------|
| 1 | **手动整理** | 从历史投标文档中人工提取高频实体 | 公司名、项目名、资质 |
| 2 | **公开数据** | 政府采购网、天眼查、企查查等公开数据 | 公司名、工商信息 |
| 3 | **LLM 自动发现** | 部署后让 LLM 扫描文档，发现未命中实体 | 所有类型（作为补充） |
| 4 | **用户反馈** | 前端提供"添加实体"功能，用户手动补充 | 所有类型 |
| 5 | **行业标准** | 建筑行业分类标准、资质目录、行政区划表 | 资质、地点、术语 |

### 3.2 各实体类型获取方法

#### 3.2.1 公司/机构名（Organization）

```
来源:
  1. 历史投标文档中所有出现的公司名称（中标方、招标方、分包方）
  2. 中标公示网站爬取（如全国公共资源交易平台）
  3. 工商信息库（天眼查 OpenAPI）
  4. LLM 扫描发现

去重策略:
  - "甲公司" vs "甲建设集团有限公司" → 同一实体，统一用全称
  - "XX市交通局" vs "XX市交通运输局" → 同一实体
  - 维护别名表，匹配时任一别名命中即算匹配
```

#### 3.2.2 项目名（Project）

```
来源:
  1. 历史投标文档的标题/扉页项目名称
  2. 政府采购网项目公告
  3. 文档文件名（通常包含项目名关键词）

命名规范:
  - 统一格式: "XX市XX公路改建工程" 而非 "XX公路项目"
  - 包含编号: "XX公路改建工程（XXGL-2024-001）"

构建方式:
  上传文档后，自动从文档标题/第一页提取项目名，写入 auto/discovered_projects.txt
```

#### 3.2.3 资质（Qualification）

```
来源:
  - 《建筑业企业资质标准》（住建部发布）
  - 招标文件中"投标人资格要求"章节

施工总承包序列:
  建筑工程施工总承包（特级、一级、二级、三级）
  公路工程施工总承包（特级、一级、二级、三级）
  市政公用工程施工总承包（特级、一级、二级、三级）
  水利水电工程施工总承包（特级、一级、二级、三级）
  机电工程施工总承包（一级、二级、三级）
  ...

专业承包序列:
  地基基础工程专业承包（一级、二级、三级）
  起重设备安装工程专业承包（一级、二级、三级）
  电子与智能化工程专业承包（一级、二级）
  消防设施工程专业承包（一级、二级）
  ...

其他:
  安全生产许可证
  ISO9001质量管理体系认证
  ISO14001环境管理体系认证
  OHSAS18001职业健康安全管理体系认证
```

#### 3.2.4 金额（Money）

```
来源: 正则匹配（无需词库）

但需要维护常见金额表达式:
  - 中文大写: 壹贰叁肆伍陆柒捌玖拾佰仟万亿零整
  - 单位: 万元 亿元 元
  - 币种前缀: ¥ $ 人民币

金额类型细分（后续可用）:
  - 投标保证金
  - 履约保证金
  - 合同金额
  - 招标控制价
  - 中标金额
```

#### 3.2.5 日期（Date）

```
来源: 正则匹配（无需词库）

中文日期格式:
  - 2024年3月15日
  - 二〇二四年三月十五日
  - 2024-03-15
  - 2024/03/15

投标场景关键日期类型:
  - 公告发布日期
  - 投标截止时间
  - 开标日期
  - 评标日期
  - 中标公示期
  - 合同签订日期
  - 履约期限
```

#### 3.2.6 投标专业术语（BidTerm）

```
来源: 行业知识整理

基础概念:
  招标、投标、中标、开标、评标、定标、流标、废标
  招标人、投标人、中标人、招标代理机构
  招标公告、招标文件、投标文件、中标通知书

文件类型:
  招标公告、资格预审公告、澄清公告、中标候选人公示、中标结果公告
  招标文件、技术规范书、工程量清单、施工图纸、合同草案

保证金类型:
  投标保证金、履约保证金、质量保证金、农民工工资保证金

评标方法:
  综合评分法、经评审的最低投标价法、性价比法

其他:
  联合体投标、分包、转包、挂靠
  招标控制价、最高投标限价、成本警戒线
  不平衡报价、低于成本价竞标
```

---

## 4. 词库初始化

### 4.1 快速启动词库

以下是一个可直接使用的最小启动词库，覆盖投标文档最常见的实体：

```text
# core/organizations.txt（示例）
XX建设集团有限公司	XX建设	公路、市政总承包一级
XX路桥工程有限公司	XX路桥	桥梁工程专业承包一级
XX建筑工程有限公司	XX建筑	建筑工程总承包二级
XX市交通运输局	市交通局	招标方
XX市住房和城乡建设局	市住建局	行业主管部门

# core/qualifications.txt（示例，完整见附录A）
建筑工程施工总承包一级
建筑工程施工总承包二级
建筑工程施工总承包三级
公路工程施工总承包一级
公路工程施工总承包二级
市政公用工程施工总承包一级
...
安全生产许可证
ISO9001质量管理体系认证

# core/locations.txt（示例）
北京市	北京	首都
上海市	上海
广州市	广州
成都市	成都
...（按需添加省级、市级地名）

# core/bid_terms.txt（示例）
投标保证金
履约保证金
质量保证金
最高投标限价
招标控制价
工程量清单
施工组织设计
投标有效期
...
```

### 4.2 初始化步骤

| 步骤 | 操作 | 产出 | 耗时 |
|------|------|------|------|
| 1 | 收集 5~10 份典型历史投标文档 | PDF/Word 文件 | 即时 |
| 2 | 从文档标题/扉页提取项目名 | `projects.txt` | 30 分钟 |
| 3 | 扫描文档，列出所有出现的公司名 | `organizations.txt` | 1 小时 |
| 4 | 从住建部《建筑业企业资质标准》复制资质列表 | `qualifications.txt` | 30 分钟 |
| 5 | 整理投标领域术语表 | `bid_terms.txt` | 1 小时 |
| 6 | 复制全国地级以上城市列表 | `locations.txt` | 10 分钟 |
| 7 | 配置金钱/日期/编号正则 | `*_patterns.json` | 30 分钟 |
| | **合计** | | **~4 小时** |

---

## 5. 词库加载与匹配

### 5.1 加载器

```python
# backend/lexicon/loader.py

import os
import re
import jieba
from pathlib import Path
from typing import Dict, List, Set
from dataclasses import dataclass, field
from collections import defaultdict

@dataclass
class LexiconEntry:
    """词库条目"""
    name: str                # 实体标准名称
    aliases: List[str] = field(default_factory=list)  # 别名
    description: str = ""    # 描述
    source: str = ""         # 来源文件

class LexiconLoader:
    """词库加载器"""

    BASE_DIR = Path(__file__).parent

    def __init__(self):
        self.entries: Dict[str, List[LexiconEntry]] = defaultdict(list)
        self.alias_index: Dict[str, str] = {}  # 别名 → 标准名
        self.text_index: Set[str] = set()       # 所有可匹配文本集合（用于快速判断）
        self._loaded = False

    def load_all(self):
        """加载所有词库文件"""
        if self._loaded:
            return

        # 1. 加载词库文件
        for category_dir in ["core", "auto", "custom"]:
            dir_path = self.BASE_DIR / category_dir
            if not dir_path.exists():
                continue
            for filepath in dir_path.glob("*.txt"):
                entity_type = filepath.stem.upper()
                self._load_txt_file(filepath, entity_type)

        # 2. 加载正则模式文件
        for filepath in (self.BASE_DIR / "core").glob("*_patterns.json"):
            entity_type = filepath.stem.replace("_patterns", "").upper()
            self._load_pattern_file(filepath, entity_type)

        # 3. 构建索引
        self._build_index()

        self._loaded = True

    def _load_txt_file(self, filepath: Path, entity_type: str):
        """加载 TSV 格式词库文件"""
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                parts = line.split("\t")
                name = parts[0].strip()
                aliases = [a.strip() for a in parts[1].split(",")] if len(parts) > 1 and parts[1] else []
                description = parts[2].strip() if len(parts) > 2 else ""
                source = str(filepath.relative_to(self.BASE_DIR))

                entry = LexiconEntry(
                    name=name,
                    aliases=aliases,
                    description=description,
                    source=source,
                )
                self.entries[entity_type].append(entry)

                # 加 jieba 自定义词
                jieba.add_word(name)
                for alias in aliases:
                    jieba.add_word(alias)

    def _load_pattern_file(self, filepath: Path, entity_type: str):
        """加载正则模式文件"""
        import json
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 将 patterns 存为特殊 entry
        for pattern in data.get("patterns", []):
            entry = LexiconEntry(
                name=pattern["regex"],
                aliases=[],
                description=f"group={pattern['group']}",
                source=str(filepath.relative_to(self.BASE_DIR)),
            )
            self.entries[entity_type].append(entry)

    def _build_index(self):
        """构建别名 → 标准名 的快速查找索引"""
        for entity_type, entries in self.entries.items():
            for entry in entries:
                self.text_index.add(entry.name)
                for alias in entry.aliases:
                    self.text_index.add(alias)
                    self.alias_index[alias] = entry.name

    def resolve(self, text: str) -> str:
        """别名解析为标准名"""
        return self.alias_index.get(text, text)

    def contains(self, text: str) -> bool:
        """检查文本是否为已知实体"""
        return text in self.text_index
```

### 5.2 匹配器

```python
# backend/lexicon/matcher.py

import re
from typing import List, Dict
from dataclasses import dataclass
from .loader import LexiconLoader
from .entity_types import EntityType, ENTITY_PRIORITY

@dataclass
class EntityMatch:
    name: str
    type: EntityType
    start: int              # 在原文中的起始位置
    end: int                # 在原文中的结束位置
    standard_name: str      # 解析后的标准名称
    source: str             # "regex" | "lexicon" | "jieba_ner"

class LexiconMatcher:
    """词库匹配器 — 将词库与正则组合，从文本中抽实体"""

    def __init__(self, loader: LexiconLoader):
        self.loader = loader
        loader.load_all()
        self._compile_patterns()

    def _compile_patterns(self):
        """编译所有正则模式"""
        self.regex_patterns: List[tuple] = []
        for entry in self.loader.entries.get("MONEY", []):
            self.regex_patterns.append((re.compile(entry.name), EntityType.MONEY))
        for entry in self.loader.entries.get("DATE", []):
            self.regex_patterns.append((re.compile(entry.name), EntityType.DATE))
        for entry in self.loader.entries.get("PROJECT_NO", []):
            self.regex_patterns.append((re.compile(entry.name), EntityType.PROJECT_NO))

    def _match_regex(self, text: str) -> List[EntityMatch]:
        """正则匹配"""
        matches = []
        for pattern, entity_type in self.regex_patterns:
            for m in pattern.finditer(text):
                value = m.group(1) if m.lastindex else m.group()
                matches.append(EntityMatch(
                    name=value,
                    type=entity_type,
                    start=m.start(),
                    end=m.end(),
                    standard_name=value,
                    source="regex"
                ))
        return matches

    def _match_lexicon(self, text: str) -> List[EntityMatch]:
        """词库字符串匹配"""
        matches = []
        for entity_type_str, entries in self.loader.entries.items():
            # 跳过正则类型（已在 _match_regex 中处理）
            if entity_type_str in ("MONEY", "DATE", "PROJECT_NO"):
                continue
            entity_type = EntityType(entity_type_str)

            for entry in entries:
                # 匹配标准名
                for name in [entry.name] + entry.aliases:
                    start = 0
                    while True:
                        pos = text.find(name, start)
                        if pos == -1:
                            break
                        matches.append(EntityMatch(
                            name=name,
                            type=entity_type,
                            start=pos,
                            end=pos + len(name),
                            standard_name=entry.name,
                            source="lexicon"
                        ))
                        start = pos + 1
        return matches

    def match_all(self, text: str) -> List[EntityMatch]:
        """统一入口：正则 + 词库匹配"""
        regex_matches = self._match_regex(text)
        lexicon_matches = self._match_lexicon(text)
        all_matches = regex_matches + lexicon_matches

        # 去重（按位置去重，保留优先级高的）
        return self._deduplicate(all_matches)

    def _deduplicate(self, matches: List[EntityMatch]) -> List[EntityMatch]:
        """按位置去重：重叠的匹配保留优先级高的"""
        if not matches:
            return []

        # 按位置排序
        matches.sort(key=lambda m: (m.start, -self._priority(m), m.end - m.start))

        result = []
        last_end = -1
        for m in matches:
            if m.start >= last_end:
                result.append(m)
                last_end = m.end
        return result

    def _priority(self, match: EntityMatch) -> int:
        """实体类型优先级"""
        return ENTITY_PRIORITY.get(match.type, 99)
```

---

## 6. 词库维护

### 6.1 增量更新机制

```
文档上传
    │
    ▼
词库匹配提取实体
    │
    ├── 匹配成功 → 直接使用（零成本）
    │
    └── 匹配不足（覆盖率 < 阈值）
        │
        ▼
    触发 LLM 补充抽取
        │
        ├── LLM 发现的实体 → 写入 auto/discovered_*.txt
        │                     （标记为待审核）
        │
        └── 人工审核后 → 移入 core/ 正式词库
```

### 6.2 自动发现

```python
# backend/lexicon/discoverer.py

class LexiconDiscoverer:
    """从 LLM 抽取结果中发现新实体，自动扩充词库"""

    def __init__(self, loader: LexiconLoader, auto_dir: Path):
        self.loader = loader
        self.auto_dir = auto_dir

    def discover_from_llm_results(self, entities: list):
        """对比 LLM 抽取结果与词库，发现未命中的实体"""
        for entity in entities:
            if not self.loader.contains(entity.name):
                # 未命中词库 → 写入 auto 目录的对应类型文件
                self._append_to_auto(entity.type, entity)

    def _append_to_auto(self, entity_type: str, entity):
        """追加到自动发现词库"""
        filepath = self.auto_dir / f"{entity_type.lower()}.txt"

        # 检查是否已存在该条目
        existing = set()
        if filepath.exists():
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    if "\t" in line:
                        existing.add(line.split("\t")[0].strip())

        if entity.name not in existing:
            with open(filepath, "a", encoding="utf-8") as f:
                desc = getattr(entity, "description", "")
                f.write(f"{entity.name}\t\t{desc}  # auto-discovered\n")

    def get_coverage_report(self, entities: list) -> dict:
        """计算词库覆盖率"""
        total = len(entities)
        hits = sum(1 for e in entities if self.loader.contains(e.name))
        return {
            "total": total,
            "hits": hits,
            "misses": total - hits,
            "coverage": f"{hits/total*100:.1f}%" if total else "N/A",
            "missed_entities": [e.name for e in entities if not self.loader.contains(e.name)],
        }
```

### 6.3 维护流程

```
┌─────────────────────────────────────────────────────┐
│                    词库维护生命周期                    │
│                                                      │
│  1. 系统自动发现 ──→ auto/discovered_*.txt           │
│        │                                              │
│        ▼ (管理员审核)                                  │
│  2. 人工校验 ──→ 去重、合并别名、补充描述             │
│        │                                              │
│        ▼                                              │
│  3. 移入 core/ ──→ 正式词库                           │
│        │                                              │
│        ▼ (每季度/每批文档)                             │
│  4. 质量检查 ──→ 覆盖率报告、错误实体清理             │
│                                                      │
└─────────────────────────────────────────────────────┘
```

### 6.4 前端管理界面（建议）

| 序号 | 功能 | 说明 |
|------|------|------|
| 1 | 词库浏览 | 按实体类型分页查看所有词库条目 |
| 2 | 词库搜索 | 搜索实体名，查看别名和描述 |
| 3 | 待审核队列 | 展示 `auto/` 目录中待审核的新实体 |
| 4 | 一键批准/拒绝 | 审核后移入 `core/` 或删除 |
| 5 | 手动新增 | 管理员手动添加实体 |
| 6 | 别名管理 | 为实体添加/编辑别名 |
| 7 | 覆盖率面板 | 展示最近一批文档的词库命中率 |
| 8 | 导入/导出 | 支持 CSV 导入批量词库 |

---

## 7. 词库初始化清单

### 7.1 附录A：资质词库（完整）

```text
# qualifications.txt
# ===== 施工总承包 =====
建筑工程施工总承包特级
建筑工程施工总承包一级
建筑工程施工总承包二级
建筑工程施工总承包三级
公路工程施工总承包特级
公路工程施工总承包一级
公路工程施工总承包二级
公路工程施工总承包三级
铁路工程施工总承包特级
铁路工程施工总承包一级
铁路工程施工总承包二级
铁路工程施工总承包三级
港口与航道工程施工总承包特级
港口与航道工程施工总承包一级
港口与航道工程施工总承包二级
港口与航道工程施工总承包三级
水利水电工程施工总承包特级
水利水电工程施工总承包一级
水利水电工程施工总承包二级
水利水电工程施工总承包三级
电力工程施工总承包特级
电力工程施工总承包一级
电力工程施工总承包二级
电力工程施工总承包三级
矿山工程施工总承包特级
矿山工程施工总承包一级
矿山工程施工总承包二级
矿山工程施工总承包三级
冶金工程施工总承包特级
冶金工程施工总承包一级
冶金工程施工总承包二级
冶金工程施工总承包三级
石油化工工程施工总承包特级
石油化工工程施工总承包一级
石油化工工程施工总承包二级
石油化工工程施工总承包三级
市政公用工程施工总承包特级
市政公用工程施工总承包一级
市政公用工程施工总承包二级
市政公用工程施工总承包三级
通信工程施工总承包一级
通信工程施工总承包二级
通信工程施工总承包三级
机电工程施工总承包一级
机电工程施工总承包二级
机电工程施工总承包三级

# ===== 常用专业承包 =====
地基基础工程专业承包一级
地基基础工程专业承包二级
地基基础工程专业承包三级
起重设备安装工程专业承包一级
起重设备安装工程专业承包二级
起重设备安装工程专业承包三级
电子与智能化工程专业承包一级
电子与智能化工程专业承包二级
消防设施工程专业承包一级
消防设施工程专业承包二级
防水防腐保温工程专业承包一级
防水防腐保温工程专业承包二级
桥梁工程专业承包一级
桥梁工程专业承包二级
桥梁工程专业承包三级
隧道工程专业承包一级
隧道工程专业承包二级
隧道工程专业承包三级
钢结构工程专业承包一级
钢结构工程专业承包二级
钢结构工程专业承包三级
建筑装修装饰工程专业承包一级
建筑装修装饰工程专业承包二级
建筑机电安装工程专业承包一级
建筑机电安装工程专业承包二级
建筑机电安装工程专业承包三级
建筑幕墙工程专业承包一级
建筑幕墙工程专业承包二级
古建筑工程专业承包一级
古建筑工程专业承包二级
古建筑工程专业承包三级
城市及道路照明工程专业承包一级
城市及道路照明工程专业承包二级
城市及道路照明工程专业承包三级
公路路面工程专业承包一级
公路路面工程专业承包二级
公路路面工程专业承包三级
公路路基工程专业承包一级
公路路基工程专业承包二级
公路路基工程专业承包三级
公路交通工程专业承包一级
公路交通工程专业承包二级
环保工程专业承包一级
环保工程专业承包二级
环保工程专业承包三级

# ===== 其他 =====
安全生产许可证
ISO9001质量管理体系认证
ISO14001环境管理体系认证
OHSAS18001职业健康安全管理体系认证
ISO45001职业健康安全管理体系认证
```

### 7.2 词库文件清单

| 序号 | 文件 | 类型 | 预估条目数 | 构建方式 |
|------|------|------|-----------|----------|
| 1 | `organizations.txt` | 词库 | 50~200 | 手动整理 + LLM 发现 |
| 2 | `projects.txt` | 词库 | 50~500 | 自动提取 + LLM 发现 |
| 3 | `persons.txt` | 词库 | 20~100 | jieba NER + LLM 补充 |
| 4 | `locations.txt` | 词库 | 300+ | 行政区划公开数据 |
| 5 | `qualifications.txt` | 词库 | 100+ | 建筑业资质标准 |
| 6 | `bid_terms.txt` | 词库 | 50~100 | 领域知识整理 |
| 7 | `money_patterns.json` | 正则 | 5~10 | 手动配置 |
| 8 | `date_patterns.json` | 正则 | 5~10 | 手动配置 |
| 9 | `project_no_patterns.json` | 正则 | 3~5 | 手动配置 |

---

## 8. 集成到现有系统

### 8.1 与 parser.py 集成

```python
# backend/core/parser.py 中增加词库提取步骤

from backend.lexicon.loader import LexiconLoader
from backend.lexicon.matcher import LexiconMatcher

class DocumentParser:
    def __init__(self):
        self.lexicon_loader = LexiconLoader()
        self.lexicon_matcher = LexiconMatcher(self.lexicon_loader)

    def parse(self, file_path: str):
        # ... 现有的 PDF/Word 解析逻辑 ...

        for doc in llama_documents:
            # 新增：词库实体提取
            entities = self.lexicon_matcher.match_all(doc.text)
            doc.metadata["entities"] = [
                {"name": e.standard_name, "type": e.type.value, "source": e.source}
                for e in entities
            ]
        return llama_documents
```

### 8.2 与 graph_extractor.py 集成

```python
# 实体提取流程：词库先行，LLM 补漏

class HybridEntityExtractor:
    def extract(self, text: str):
        # Step 1: 词库匹配（毫秒级，零成本）
        lexicon_entities = self.lexicon_matcher.match_all(text)

        # Step 2: 判断覆盖率
        # 如果词库覆盖率达标（>80%），跳过 LLM 抽取
        coverage_ok = len(lexicon_entities) > 0  # 简化条件

        # Step 3: LLM 补充（仅关系抽取 + 长尾实体）
        if not coverage_ok:
            llm_entities = self._llm_extract(text)
        else:
            llm_entities = []

        # Step 4: 合并去重
        return self._merge_and_deduplicate(lexicon_entities, llm_entities)

    def extract_relations(self, text: str, entities: list):
        """只调 LLM 做关系抽取（实体已由词库提供）"""
        if not entities:
            return []
        return self._llm_extract_relations(text, entities)
```

---

## 9. 总结

| 维度 | 说明 |
|------|------|
| **核心思路** | 正则 + 词库覆盖 80% 实体，LLM 只做关系抽取和长尾补漏 |
| **成本** | 实体抽取零 LLM 调用，关系抽取 LLM 调用量减少 80% |
| **速度** | 实体提取毫秒级完成 |
| **精度** | 词库匹配 100% 确定，正则提取稳定可靠 |
| **维护** | 初始花 4 小时构建，后续通过 LLM 自动发现 + 人工审核持续补充 |
| **冷启动** | 先跑 2 周纯 LLM 抽取，积累足够实体数据后切换词库模式 |
