# Word 文档分片方案 — 段落聚合 + 中文标题识别

> 版本: v1.0 | 日期: 2026-08-12 | 状态: 待评审

---

## Context

### 现状

| 环节 | 当前实现 | 问题 |
|------|----------|------|
| DOCX 解析 | `DocxParser` 用 python-docx 提取所有段落+表格，拼接为一个字符串 | 整个文档是一个 `LlamaDocument`，无结构信息 |
| DOC 解析 | 复用 `DocxParser`（python-docx 不支持 .doc 格式） | 实际无法解析 .doc 文件 |
| 切片 | `SentenceSplitter` 按 token 数切 | 无视段落边界，在任意位置截断；不感知文档逻辑结构 |

### 为什么不用 LlamaIndex 通用分割器

| 分割器 | 不适合的原因 |
|--------|------------|
| `SentenceSplitter` | 仅按句子+token切，不感知文档结构，段落中间截断 |
| `TokenTextSplitter` | 更粗糙，连句子边界都不管 |
| `SemanticSplitterNodeParser` | 需要逐句 embedding，冗余——Word 已有段落边界；不识别标题 |
| `MarkdownNodeParser` | 依赖 `#` 标记，投标文档不用 Word 标题样式，无法转为 Markdown |
| `HierarchicalNodeParser` | 仍是按 token 多粒度切，不按逻辑边界 |

**核心结论：Word 有天然段落结构，通用分割器用不上这个信息。自己写段落聚合逻辑，兜底才用 SentenceSplitter。**

### 投标文档的标题特征

中文投标文档很少使用 Word 原生 Heading 样式（Heading 1~6），而是通过字号加粗在视觉上区分标题。但标题**文本内容**有规律可循：

```
第X章 投标须知
第一章 总则
1. 项目概况
1.1 项目名称
1.1.1 建设地点
（一）商务条款
一、报价说明
第一节 通用条款
```

---

## 1. 方案概览

```
DOCX/DOC 文件
      │
      ▼
┌────────────────────────────────────────────────┐
│ ① DocxParser 改造                               │
│    - python-docx 提取段落列表（保留顺序和文本）   │
│    - 提取表格，关联到相邻段落                     │
│    - 输出: 结构化段落列表 [{text, is_table}]      │
│    - 【不在此层做分片，只做提取和标记】            │
└────────────────────────────────────────────────┘
      │
      ▼
┌────────────────────────────────────────────────┐
│ ② WordChunker（新增）                            │
│    2a. 标题识别 — 正则匹配中文标书标题模式        │
│    2b. 段落聚合 — 以标题为边界，相邻正文段归入     │
│    2c. 超长兜底 — 逻辑块 > 512 tokens 则         │
│        SentenceSplitter 细切，继承标题路径        │
│    输出: LlamaIndex TextNode 列表                │
└────────────────────────────────────────────────┘
      │
      ▼
┌────────────────────────────────────────────────┐
│ ③ DocParser（新增）                              │
│    LibreOffice 无头转换 .doc → .docx            │
│    转换后走 DocxParser + WordChunker 同一链路    │
└────────────────────────────────────────────────┘
```

---

## 2. 详细设计

### 2.1 标题识别

#### 2.1.1 投标文档标题体系

中文投标文档的典型层级结构：

```
第一章 招标公告              ← 第X章（L1，顶层）
  第一节 项目概况             ← 第X节（L1，等同章）
  一、商务条款                ← 中文数字 + 、（L2）
    1. 项目名称               ← 阿拉伯数字 + .（L2，等同中文数字）
    1.1 建设地点              ← 多级数字（L3）
      （一）分项报价           ← 括号中文数字（L4）
        (1) 设备清单           ← 括号阿拉伯数字（L4/L5）
          ① 网络设备           ← 圆圈数字（L5）
            a. 路由器          ← 字母序号（L5）
```

但实际文档不一定从 L1 开始，可能顶层就是 `一、` 或 `1.`。因此层级编号只在**相对意义**上使用（维护标题栈），不要求文档具备完整层级。

#### 2.1.2 标题模式定义

```python
import re
from typing import Optional

# 层级含义：
#   1 = 章（顶层结构，第X章 / 第X节）
#   2 = 节（中文数字 / 阿拉伯单级数字）
#   3 = 子节（多级数字 1.1 / 括号中文数字）
#   4 = 款（括号数字 / 圆圈数字）
#   5 = 项（字母序号）

HEADING_PATTERNS: list[tuple[str, int, str]] = [
    # === Level 1: 章级标题 ===
    # 第X章 / 第X节 / 第X条（合同用）
    (
        r"^第[一二三四五六七八九十百千零\d]+[章节条]",
        1,
        "章标题",
    ),
    # 附件 / 附录（与章同级，独立模块）
    (
        r"^(?:附件|附表|附录)[一二三四五六七八九十\d]*[：:]*",
        1,
        "附件标题",
    ),

    # === Level 2: 节级标题 ===
    # 中文数字 + 顿号/句号：一、二、三、
    (
        r"^[一二三四五六七八九十]+[、．]",
        2,
        "中文数字标题",
    ),
    # 阿拉伯数字 + 顿号：1、2、3、（常见于技术规格书）
    (
        r"^\d{1,2}[、]",
        2,
        "数字顿号标题",
    ),
    # 阿拉伯数字 + 句号：1. 2.（需防列表项误判，见 2.1.4）
    (
        r"^\d{1,2}\.(?!\d)[\s　]",
        2,
        "数字句号标题",
    ),

    # === Level 3: 子节级标题 ===
    # 多级数字：1.1 / 1.1.1 / 2.3.5
    (
        r"^\d{1,2}\.\d{1,2}(?:\.\d{1,2})*[\s、.]*",
        3,
        "多级数字标题",
    ),
    # 括号中文数字：（一）（二）（三）
    (
        r"^[（(][一二三四五六七八九十]+[)）]",
        3,
        "括号中文数字标题",
    ),

    # === Level 4: 款级标题 ===
    # 括号阿拉伯数字：(1) (2)
    (
        r"^[（(]\d+[)）]",
        4,
        "括号数字标题",
    ),
    # 圆圈数字：①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳
    (
        r"^[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]",
        4,
        "圆圈数字标题",
    ),

    # === Level 5: 项级标题 ===
    # 字母 + 句号/顿号/括号：a. b. c. / (a) (b)
    (
        r"^[a-z][\.、)]",
        5,
        "字母序号标题",
    ),
    (
        r"^[（(][a-z][)）]",
        5,
        "括号字母标题",
    ),
]

# 标题最大长度（字符）— 超过此值视为正文
MAX_HEADING_LENGTH = 50

# 明确非标题模式（段落以这些词开头不算标题）
NON_HEADING_PREFIXES: list[str] = [
    r"^注[：:意]",           # 注：注意：
    r"^备注[：:]",           # 备注：
    r"^说明[：:]",           # 说明：
    r"^例如[：:]",           # 例如：
    r"^示例[：:]",           # 示例：
    r"^其中[：:]",           # 其中：
    r"^特别[说提][：:]",     # 特别说明：特别提示：
    r"^包括[：:]",           # 包括：
    r"^如[：:下]",           # 如：如下
    r"^以上[：:]",           # 以上：
    r"^以下[：:]",           # 以下：
    r"^\d+[\.\d]*\s*[）)]",  # 1) 2) — 更可能是列表项，非标题
]
```

#### 2.1.3 标题判定逻辑

```python
def is_heading(
    para_text: str,
    style_name: Optional[str] = None,
) -> tuple[bool, Optional[int]]:
    """
    判定一个段落是否为标题。

    Args:
        para_text: 段落文本
        style_name: python-docx 段落样式名（Style Name），用于辅助判定：
            - 含 "Heading" → 直接判定为标题，返回最高优先级
            - 含 "List" / "Bullet" / "Enum" → 直接判定为非标题
            - None / "Normal" / 其他 → 走正则匹配

    Returns:
        (is_heading, level)
        level: 1-5 对应层级，None 表示非标题

    判定流程（短路）:
        1. 空文本 → False
        2. 样式白名单（Heading） → True, level=1
        3. 样式黑名单（List） → False
        4. 非标题前缀 → False
        5. 长度超限 → False
        6. 正则匹配 → True + level / False
    """
    text = para_text.strip()
    if not text:
        return False, None

    # --- 样式白名单 ---
    if style_name and "heading" in style_name.lower():
        # 从样式名推断层级（如 "Heading 2" → level=2）
        level = _extract_heading_level(style_name)
        return True, level

    # --- 样式黑名单 ---
    if style_name and any(
        keyword in style_name.lower()
        for keyword in ("list", "bullet", "enum", "toc")
    ):
        return False, None

    # --- 非标题前缀 ---
    for prefix_pattern in NON_HEADING_PREFIXES:
        if re.match(prefix_pattern, text):
            return False, None

    # --- 长度超限 ---
    if len(text) > MAX_HEADING_LENGTH:
        return False, None

    # --- 正文标点结尾（列表项特征） ---
    # 标题不以 ；。， 结尾
    if text[-1] in ("；", "。", "，", "；"):
        return False, None

    # --- 正则匹配 ---
    for pattern, level, _name in HEADING_PATTERNS:
        if re.match(pattern, text):
            return True, level

    return False, None


def _extract_heading_level(style_name: str) -> int:
    """从 Word 样式名提取层级，如 'Heading 2' → 2"""
    m = re.search(r"(\d+)", style_name)
    if m:
        level = int(m.group(1))
        return min(level, 5)  # 最多到 level 5
    return 1  # "Heading" 无数字 → level 1
```

#### 2.1.4 防误判设计

| 场景 | 风险 | 防御手段 |
|------|------|----------|
| 列表项 `1. 具有独立法人资格；` | 被误判为标题 | ① `；。，` 结尾排除；② List 样式黑名单；③ 长度通常更长 |
| 年份开头 `2024. 项目启动` | `\d+\.` 误匹配 | 限定 `\d{1,2}\.`，年份 4 位不匹配 |
| 注释 `注：本条款…` | 被当作标题切分 | NON_HEADING_PREFIXES 显式排除 |
| 目录条目 `第一章 总则 …… 1` | TOC 被当作正文标题 | `toc` 样式黑名单 |
| 连续编号列表项 | 正文被切碎 | 列表项样式多为 List Paragraph，黑名单过滤 |

#### 2.1.5 已知局限

以下场景**无法通过纯文本正则可靠识别**，依赖文档作者使用 Word 样式或后续迭代增强：

| 局限 | 示例 | 可能的增强方向 |
|------|------|--------------|
| 无编号标题 | `投标人资格要求`（纯文字、无序号） | 检测段落加粗 + 短文本 + 前后均为长正文 |
| 标题序号不连续 | `一、… 三、…`（缺 `二、`） | 不影响功能，仅 heading_path 中间断链 |
| 编号层级跳变 | `第一章` 下直接出现 `(1)` | 标题栈正确弹出，不丢失路径 |
| .doc 旧格式 | python-docx 无法读取 | DocParser（2.5 节）LibreOffice 转 .docx 后走同一链路 |

#### 2.1.6 兜底：无标题文档

如果整篇文档一个标题都没匹配到，则整个文档作为一个逻辑块，走 SentenceSplitter 细切。不丢数据。

---

### 2.2 段落聚合

#### 2.2.1 核心算法

```python
@dataclass
class ParagraphItem:
    text: str
    is_heading: bool
    heading_level: int | None
    is_table: bool

@dataclass
class LogicalBlock:
    heading: str | None          # 所属标题文本（最近的标题）
    heading_level: int | None     # 标题层级
    heading_path: list[str]       # 标题路径（面包屑）
    paragraphs: list[str]         # 该块下的正文段落
    tables: list[str]             # 该块下的表格

def aggregate_paragraphs(paragraphs: list[ParagraphItem]) -> list[LogicalBlock]:
    """
    以标题为边界，邻近正文段落聚合到同一逻辑块。
    
    规则:
    1. 遇到标题 → 开启新块
    2. 正文/表格 → 归入当前块
    3. 标题路径跟踪: 遇到 H1 时清空路径，遇到 H2 时清空 H2 以下
    """
    blocks: list[LogicalBlock] = []
    current_block = LogicalBlock(
        heading=None, heading_level=None,
        heading_path=[], paragraphs=[], tables=[]
    )
    heading_stack: list[str] = []  # 维护当前标题路径

    for item in paragraphs:
        if item.is_heading:
            # 保存上一个块
            if current_block.paragraphs or current_block.tables:
                blocks.append(current_block)
            
            # 维护标题栈
            while heading_stack and len(heading_stack) >= (item.heading_level or 0):
                heading_stack.pop()
            heading_stack.append(item.text)
            
            current_block = LogicalBlock(
                heading=item.text,
                heading_level=item.heading_level,
                heading_path=list(heading_stack),
                paragraphs=[],
                tables=[],
            )
        else:
            if item.is_table:
                current_block.tables.append(item.text)
            else:
                current_block.paragraphs.append(item.text)

    # 最后一个块
    if current_block.paragraphs or current_block.tables:
        blocks.append(current_block)

    return blocks
```

#### 2.2.2 表格归属

表格属于**其上方最近的标题**。表格文本在块内拼接时放在段落之后，格式化为：

```
[表] 序号 | 名称 | 金额
[表] 1 | 设备A | 10000
```

---

### 2.3 超长逻辑块兜底

#### 2.3.1 判定与细切

```python
MAX_BLOCK_TOKENS = 512   # 从 settings.chunk_size 读取
OVERFLOW_RATIO = 1.5      # 超过 1.5 倍才切（容忍轻微超限）

def chunk_blocks(blocks: list[LogicalBlock]) -> list[TextNode]:
    """逻辑块 → TextNode，超长用 SentenceSplitter 细切"""
    
    sentence_splitter = SentenceSplitter(
        chunk_size=MAX_BLOCK_TOKENS,
        chunk_overlap=64,
    )
    
    for block in blocks:
        # 构建块文本: 标题 + 正文 + 表格
        body_parts = []
        if block.heading:
            body_parts.append(block.heading)
        body_parts.extend(block.paragraphs)
        body_parts.extend(block.tables)
        body = "\n\n".join(body_parts)
        
        token_estimate = len(body) // 2  # 中文约 2 字符/token
        
        if token_estimate <= MAX_BLOCK_TOKENS * OVERFLOW_RATIO:
            # 小逻辑块：直接作为一个 TextNode
            yield TextNode(
                text=body,
                metadata={
                    "heading": block.heading or "",
                    "heading_path": " > ".join(block.heading_path),
                    "chunk_method": "paragraph_aggregate",
                },
            )
        else:
            # 大逻辑块：SentenceSplitter 细切
            doc = LlamaDocument(text=body)
            sub_nodes = sentence_splitter.get_nodes_from_documents([doc])
            for node in sub_nodes:
                node.metadata["heading"] = block.heading or ""
                node.metadata["heading_path"] = " > ".join(block.heading_path)
                node.metadata["chunk_method"] = "sentence_splitter"
            yield from sub_nodes
```

#### 2.3.2 为什么兜底仍用 SentenceSplitter

- 超长逻辑块通常是某章正文极长（如详细的技术方案描述），内部无子标题
- 这种情况只能靠语义边界，SentenceSplitter 至少保证在句子边界切
- 所有子块继承父标题路径，检索时仍然能定位到具体章节

---

### 2.4 DocxParser 改造

#### 2.4.1 改动范围

`DocxParser._parse_sync()` 的输出从 `list[LlamaDocument]` 改为 `list[ParagraphItem]`：

```python
def _parse_sync(self, file_path: Path) -> list[ParagraphItem]:
    from docx import Document as DocxDocument
    doc = DocxDocument(file_path)
    items: list[ParagraphItem] = []

    # 段落
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        heading, level = is_heading(text)
        items.append(ParagraphItem(
            text=text,
            is_heading=heading,
            heading_level=level,
            is_table=False,
        ))

    # 表格
    for table in doc.tables:
        rows = []
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            rows.append(" | ".join(cells))
        table_text = "\n".join(rows)
        if table_text.strip():
            items.append(ParagraphItem(
                text=table_text,
                is_heading=False,
                heading_level=None,
                is_table=True,
            ))

    return items
```

#### 2.4.2 接口兼容

原 `parse()` 返回 `list[LlamaDocument]`，上游 `document_service.py:parse_document()` 调用 `parse_file()` 后直接传 `chunk_documents()`。

改造后：
- `DocxParser.parse()` 返回 `list[ParagraphItem]`
- `parse_file()` 返回类型改为 `list[ParagraphItem] | list[LlamaDocument]`（union）
- `parse_document()` 根据文件类型分叉：

```python
# document_service.py 中
if file_type in ("docx", "doc"):
    paragraphs = await parse_file(temp_path, file_type)
    chunks, metadatas = word_chunk(paragraphs, doc_id, file_name, chunk_size)
else:
    llama_docs = await parse_file(temp_path, file_type)
    chunks, metadatas, _ = chunk_documents(llama_docs, chunk_size, chunk_overlap)
```

---

### 2.5 .doc 兼容（DocParser）

#### 2.5.1 方案

使用 LibreOffice 无头模式将 .doc 转为 .docx，然后走 DocxParser 同一链路。

```python
class DocParser(BaseParser):
    """Legacy .doc 解析器：LibreOffice 转换 → DocxParser"""

    async def parse(self, file_path: Path) -> list[ParagraphItem]:
        output_dir = file_path.parent / "convert"
        output_dir.mkdir(exist_ok=True)

        # LibreOffice 无头转换
        proc = await asyncio.create_subprocess_exec(
            "soffice",
            "--headless",
            "--convert-to", "docx",
            "--outdir", str(output_dir),
            str(file_path),
        )
        await proc.wait()

        docx_path = output_dir / (file_path.stem + ".docx")
        if not docx_path.exists():
            raise ValueError("LibreOffice 转换失败")

        parser = DocxParser()
        result = await parser.parse(docx_path)

        # 清理
        docx_path.unlink(missing_ok=True)
        return result
```

#### 2.5.2 降级方案

若环境没有 LibreOffice，回退到纯 Python 方案：

| 库 | 原理 | 质量 |
|----|------|------|
| `textract` | 调用 antiword/catdoc | 中等，只提取文本无表格 |
| `olefile` + 手动解析 | 读取 OLE2 二进制流 | 低，只提取文本 |

**建议**：Docker 镜像预装 LibreOffice，生产环境用主方案。开发环境提示安装。

---

### 2.6 Metadata 变更

#### 2.6.1 新增字段

每个切片（TextNode）的 metadata 增加：

| 字段 | 类型 | 来源 | 示例 |
|------|------|------|------|
| `heading` | str | 所属最近标题 | `"1.1 项目概况"` |
| `heading_path` | str | 面包屑路径 | `"第三章 > 1.1 项目概况 > (一) 建设内容"` |
| `chunk_method` | str | 切片方式 | `"paragraph_aggregate"` 或 `"sentence_splitter"` |

#### 2.6.2 索引层变更

- `scalar_field_names` 增加 `heading`、`heading_path`、`chunk_method`（VARCHAR）
- PG `document_chunks` 表增加同名字段（或存入 `text_content` 不在 DB 重复）
- 检索时可按 `heading_path` 过滤同章节上下文

> **决定**：暂不修改 PG schema，`heading` 和 `heading_path` 信息已包含在 `text_content` 中，Milvus 标量字段增加即可用于过滤。后续若有按章节筛选的需求再迁移 PG。

#### 2.6.3 Milvus scalar_fields 变更

```python
# indexing.py _get_vector_store()
scalar_field_names=["file_name", "page_number", "chunk_index", "heading", "heading_path"],
scalar_field_types=[DataType.VARCHAR, DataType.INT64, DataType.INT64, DataType.VARCHAR, DataType.VARCHAR],
```

---

## 3. 文件改动范围

| 文件 | 改动类型 | 内容 |
|------|----------|------|
| `backend/core/parser.py` | **修改** | DocxParser 输出改为 ParagraphItem 列表；新增 is_heading()；新增 DocParser |
| `backend/core/word_chunker.py` | **新增** | WordChunker: aggregate_paragraphs() + chunk_blocks() |
| `backend/core/indexing.py` | **修改** | _get_vector_store() 增加 heading/heading_path 标量字段；chunk_documents() 保持不变（PDF 继续用） |
| `backend/services/document_service.py` | **修改** | parse_document() 按 file_type 分叉：docx/doc 走 word_chunker，pdf 走 chunk_documents |
| `backend/config.py` | **不修改** | chunk_size/chunk_overlap 复用现有配置 |
| `backend/routers/chunk.py` | **不修改** | 切片管理 API 不感知分片策略变化 |

### 不修改

- `backend/models/` — 模型不变
- `backend/repositories/` — Repository 不变
- `build_index()` — 接口不变（仍然接收 chunks + metadatas）
- 前端 — 无感知
- PDF 处理链路 — 完全不变

---

## 4. 实现步骤

```
1. parser.py: 新增 is_heading() + HeadingPattern 常量
   → 验证: 准备 5 个典型投标文档标题样本，单测通过

2. parser.py: 改造 DocxParser._parse_sync() → 输出 ParagraphItem 列表
   → 验证: 上传一个 docx，parse() 返回正确的段落列表（含 is_heading 标记）

3. word_chunker.py: 新增 aggregate_paragraphs() + chunk_blocks()
   → 验证: 单元测试，给定已知段落列表，验证逻辑块边界和标题路径

4. document_service.py: 分叉逻辑
   → 验证: 上传 docx 走 word_chunk；上传 pdf 走 chunk_documents；均正常入库

5. parser.py: 新增 DocParser（LibreOffice）
   → 验证: 上传 .doc 文件，转换后正常解析分片

6. indexing.py: 增加 heading/heading_path 标量字段
   → 验证: 新建知识库 → 上传 Word → 切片检索能过滤 heading_path

7. 端到端回归
   → 验证: PDF 上传/解析/问答不受影响；现有单元测试通过
```

---

## 5. 风险与边界

| 风险 | 应对 |
|------|------|
| 标题正则漏匹配 | 兜底：整篇无标题时退化为纯 SentenceSplitter |
| 标题正则误匹配（正文被识别为标题） | `MAX_HEADING_LENGTH=50` 截断；后续可根据实际文档样本迭代正则 |
| LibreOffice 依赖太重 | Dockerfile 预装；开发环境 `pip install textract` 降级方案 |
| 段落聚合后某块过大 | `SentenceSplitter` 兜底细切，子块继承标题元数据 |
| 表格被放在文档末尾后没有标题归属 | 表格跟随其上方最近的标题，无标题时 heading 为空字符串 |

---

## 6. 验证方式

### 6.1 单元测试

- `test_word_chunker.py`
  - 给定 10 个段落（3 标题 + 7 正文）→ 验证产出 3 个逻辑块
  - 给定 1 个超长段落（2000 字符）→ 验证 SentenceSplitter 细切，子块 heading_path 一致
  - 给定无标题段落列表 → 验证产出 1 个逻辑块，heading 为空
- `test_heading_patterns.py`
  - 正例：`第一章`、`1.1`、`（一）`、`一、` 等正确识别
  - 负例：`第一章 这是很长的一段正文超过五十字符` 不识别为标题
  - 负例：`本次采购主要包括以下几个方面` 不识别为标题

### 6.2 集成测试

- 上传一个典型 30 页投标 DOCX → 检查：
  - 切片数在合理范围（20~60，非几百个碎块）
  - 每个切片 metadata 有 `heading` 和 `heading_path`
  - 同一章的切片 `heading_path` 前缀一致
- 上传 PDF 回归 → 行为不变

### 6.3 检索质量验证

- 向包含 Word 文档的知识库提问 "第三章报价要求是什么"
- 检查检索返回的切片是否确实来自第三章，而非其他章节
