# 42 PDF 解析分块方案（结构化对齐 Word 链路）

> 版本: v1.0 | 日期: 2026-09-04 | 状态: 待评审

## 1. 需求（已澄清）

当前 PDF 上传被后端禁用（`ALLOWED_EXTENSIONS = {".docx",".doc"}`，上传返回 400"PDF 解析暂不支持"），
而 Word（docx/doc）已具备"解析→分块"完整结构化链路。本任务**恢复 PDF 支持**并实现与 Word
同一质量档次的**解析 + 分块**，使 PDF 文档进入 RAG 检索/问答/图模块时具备一致的 chunk 元数据。

| 澄清项 | 结论 |
|--------|------|
| 分块粒度 | **对齐 Word 结构化链路**：逐块解析（标题层级 + 段落 + 表格 + 图片），逻辑块聚合后按标题分块，chunk 带 `heading/heading_path/page_number/chunk_type/image_keys` |
| PDF 图片 | **与 Word 一致**：图片上传 MinIO、OCR（ocr 模型，vision 兜底）文字并入所在块正文参与分块，chunk 关联 `image_keys` 供前端原图预览；删除/重切片/重解析沿用 `images/` 前缀清理 |
| 扫描件 | **先支持文本型 PDF**：无文本层页面识别后跳过（不计内容，不 OCR 整页）；整篇无文本则解析失败并给出友好提示 |
| 方案文档 | 本文件（docs/plan/42…）评审通过后再编码 |
| 验收样本 | 用户提供 `D:\Pictures\投标\`（`02 - 图片嵌入表格.pdf` 文本型带表格图片；`身份证.pdf/身份证2.pdf` 纯扫描页） |

### 样本实测结论（决定启发式取值）
用探针脚本（PyMuPDF + pdfplumber）对 `D:\Pictures\投标` 三个 PDF 实测：
- 文本型样本：正文全 12pt、无字号层级 → 标题识别**必须正则为主、字号/加粗仅作补充判据**；
- 表格内容会同时出现在 PDF 文本层 → 需按 pdfplumber 表格 bbox **剔除表格区域内的文本行**，避免与表格项重复；
- 表格行文本如"（10）职业健康安全管理体系认证证书；"以括号数字开头但以"；"结尾 → 现有 `is_heading` 标点规则已能排除（列表项）；
- 扫描型样本（身份证）：`textlen=0`、含整页大图 → 可作为"扫描页跳过"的真实样例；
- 图片细节：`get_image_info` 的部分 xref **没有绘制 rect**（不可见/资源残留）→ 必须按"页面有绘制 rect 且面积达标"过滤。

## 2. 现状

- **Word 链路（docx/doc）**：`core/parser.py` `DocxParser/DocParser` → `ParagraphItem`（heading/表格/图片粒度）→
  `services/document_service._parse_and_store_in_batches` 按批次 `aggregate_paragraphs` → `process_word_images(blocks,…)`
  （MinIO 上传 + OCR/vision，写回 `WordImage.object_key/ocr_text/vision_text`）→ `word_chunk_blocks`（标题聚合块、
  超长块按字符二次切分 `word_block_max_chars/overlap`）→ Milvus `build_index` + ES 双写 + PG `batch_create_chunks`。
- **PDF 旧链路（遗留未启用）**：`PDFParser` 每页拼 text+tables+内联 OCR → 一个 `LlamaDocument`/页 →
  `indexing.chunk_documents`（SentenceSplitter 按 token 切），无标题/无图片上传/无 `image_keys`；
  `chunk_documents` 仅此一个生产调用点。
- **数据层已就绪**：`document_chunks` 已含 `page_number/chunk_index` 列与 `chunk_metadata`（JSON：
  `heading/heading_path/image_keys/chunk_type/source_chunk_index` 等）；Milvus/ES 索引字段已含
  heading/heading_path/page_number → **无需迁移**。
- **前端已就绪**：`DocumentListView` accept 与类型校验本就含 `.pdf`（仅后端白名单挡）。
- 解析入口仅 `document_service.parse_document/rechunk_document/reparse_document`（经 `parse_queue` 触发）调用 `core.parser.parse_document`。

## 3. 改动范围

| 文件 | 改动 |
|------|------|
| `backend/utils/file_utils.py` | 白名单加入 `.pdf`；docstring 更新 |
| `backend/routers/document.py` | 400 提示文案改为支持 pdf |
| `backend/core/parser.py` | `ParagraphItem` 增可选 `page_number`；`PDFParser` 重写为结构化委托（不再内联 OCR、不再产 LlamaDocument）；`parse_document` pdf 分支返回 `(items, page_count)`；清理 `_extract_all_tables/_ocr_images_sync` 等旧实现与不再使用的 import |
| `backend/core/pdf_extractor.py`（新增） | 版式提取：纯函数 + 页面流水线，产出 `ParagraphItem` 列表与物理页数/跳过页信息 |
| `backend/core/word_chunker.py` | `LogicalBlock` 增 `start_page`；`chunk_blocks/word_chunk_blocks` 把块起始页写入 TextNode `page_number`；Word 项无 page_number → 行为不变（仍默认 1） |
| `backend/services/document_service.py` | `_parse_and_store_in_batches` 对 pdf 走与 Word 相同的 blocks 链路；三处解析入口适配 pdf 返回 tuple；移除对 parse_file 传 ocr_config（图片识别统一在块级处理） |
| `backend/services/image_recognition_service.py` | 仅注释/docstring 说明 Word/PDF 共用（函数名保持不变，避免无关改动） |
| `backend/tests/…` | 更新 PDF 拒绝用例为放行；新增 pdf_extractor / pdf heading / page 透传测试 |
| `docs/changes.md` | 追加本次修改记录 |

不涉及：数据库迁移、前端（已支持 pdf）、ES/Milvus 映射、图模块、docker-compose。
`indexing.chunk_documents`（SentenceSplitter 路径）不再被生产调用，但**保留**函数与既有测试（避免删改动前已有代码）。

## 4. 关键设计

### 4.1 数据模型（最小扩展，Word 行为不变）
- `ParagraphItem` 增 `page_number: int | None = None`（Word 不填；PDF 每项填来源页）。
- `LogicalBlock` 增 `start_page: int | None = None`——聚合时取块内首项 page_number（块可跨页，取起始页）。
- `chunk_blocks/word_chunk_blocks`：节点 `metadata["page_number"] = block.start_page`（None 时
  `word_chunk_blocks` 仍 `setdefault(1)` → Word 路径输出与现在完全一致）。
- chunk/page 引用语义：**chunk.page_number = 逻辑块起始页**（引用展示"第 N 页"用；同 Word 的"块=chunk 容器"口径）。

### 4.2 解析流水线 `core/pdf_extractor.py`

纯函数（便于单测，输入自建小数据结构）：
```
PdfLine      (text, size, bold, bbox, page)
PdfTableZone (bbox, rows: list[list[str]])   # pdfplumber page.find_tables() 产物
PdfImageZone (bbox, WordImage)               # 已提取字节与 MIME
PdfParseResult(items: list[ParagraphItem], page_count: int, skipped_pages: list[int])
```

流程（伪代码）：
```
extract_pdf_items(path, extract_images) -> PdfParseResult
  doc = fitz.open(path)                      # 同步，调用方放线程池（沿用既有约定）
  # 1) 一遍扫描收集全部文本行（page, y0, size, bold, text）
  #    size/bold 取该行主 span；正文 size = 加权中位数（剔除超大/超小离群）
  # 2) 文档级页眉页脚过滤：
  #    - 底部条带内纯数字行（页码）→ 丢弃
  #    - 顶部/底部条带内文本，同文在 ≥2 页同归一化条带重复出现 → 丢弃（页眉）
  # 3) pdfplumber 每页 find_tables → PdfTableZone（异常单页降级：跳过该页表格并记日志）
  # 4) 逐页：
  #    a. 过滤文本行：中心点在任一表格 bbox 内 → 丢弃（表格文字改由表格项产出，防重复）
  #    b. 剩余行按纵向间距聚段（间距 > 阈值或行本身命中标题 → 新段）
  #        行内 span 拼接：上行结尾为 CJK 字符 → 直接续接；否则补一个空格
  #    c. 段落 → ParagraphItem：
  #        单行段做标题判定（见 4.3）；正文段 is_heading=False
  #    d. 图片：page.get_image_info + get_image_rects，逐 xref：
  #        无绘制 rect → 跳过；xref 在多数页面重复出现且面积占比小 → 装饰性元素跳过；
  #        整页大面积且贯穿 >80% 页面 → 水印/底图跳过；其余 → extract_image 取字节为 WordImage
  #    e. 归位：图片中心落在表格 bbox → 挂该表格项；否则挂垂直范围覆盖其中心的段/表格，
  #        若位于段落之间空白 → 挂上方最近段落（图随上文）
  #    f. 表格项 = ParagraphItem(is_table=True, text=逐行" | ".join(cells))
  #    g. 页面内元素按 (y0 升序, x0 升序) 落序（单栏为主；双栏仅自上而下，记为已知局限）
  # 5) 扫描页记录：无文本行、无表格、无可挂图片的页面 → skipped_pages.append(page)
  # 6) 汇总返回 PdfParseResult
```
常量（模块级、可调）：顶部/底部条带比例、重复页判定阈值、聚段间距阈值、
装饰图片"重复 ≥3 页且 rect 面积 ≤ 页面 2%"、水印"≥60% 页面面积且 ≥80% 页面出现"等。

### 4.3 PDF 标题判定（复用 + 排版补充）
```
detect_pdf_heading(text, size, bold, body_size) -> (is_heading, level)
  1) 主判据：core.parser.is_heading(text, style_name=None)
     —— 同一套中文标题正则（第X章/一、/1./1.1/（一）/(1)/①/a.）+ 黑名单/长度/标点规则，
        保证与 Word 对同类标题的判定一致（列表项"（10）…；"因结尾分号已排除）
  2) 补充（仅未命中主判据时）：
     条件 = 长度 ≤ 40 且无结尾标点（。；，）且不命中补充黑名单
           且（字号 ≥ body_size × 1.25 或 加粗）
     补充黑名单新增 PDF 题注前缀：^图\s*\d、^表\s*\d（图/表题注非标题）
     level：字号 ≥ 1.6×body → 1；≥ 1.3×body → 2；仅加粗 → 3
  3) 多行段一律不判标题（标题为单行短文本）
  4) INFO 日志抽样输出前 30 个识别标题（文本/级别），供真档人工抽查
```
级别仅用于 `aggregate_paragraphs` 的 heading_stack 相对层级（同 Word），不做字号绝对映射。

### 4.4 图片链路（与 Word 完全同构）
- PDF 解析只做"提取字节 + 归位"，**不做识别**；
- 块级 `process_word_images(blocks, kb_id, doc_id, ocr_config, vision_config)`：
  MinIO 上传 `{kb_id}/{doc_id}/images/{uuid}.{ext}` → OCR（ocr 模型）→ 无文字时 vision 兜底；
- 之后 `word_chunk_blocks` 将图片识别文字并入正文、`image_keys` 写入 chunk metadata → 前端缩略图/预览、
  删除、重切片清理全部复用既有实现。

### 4.5 document_service 统一链路
```
_parse_and_store_in_batches(parsed, file_type, ...):
  if file_type in ("docx","doc"):                     # 行为不变
      blocks = aggregate_paragraphs(parsed) …
  else:  # "pdf"
      items, page_count = parsed                      # tuple 拆包
      blocks = aggregate_paragraphs(items) …          # 与 Word 相同的批次循环
      return chunk_count, page_count                  # 物理页数
```
三处入口（首次解析/重切片/重新解析）：
- 解析结果为空且文件为纯扫描 PDF → `ValueError("PDF 无可解析文本（疑似扫描件，暂不支持整页 OCR）")`；
- 有文本页但全部为空内容等其它情况仍维持现有"解析结果为空"语义。

### 4.6 文件放行与文案
- `ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc"}`；router 400 文案"支持: pdf, docx, doc"。

## 5. 实现步骤（每步验证）

1. 放行 PDF：`file_utils.py` + `routers/document.py` 文案
   → 验证：`test_file_utils` 中 pdf 断言翻转为 True；router 上传 pdf（mock service）不再 400。
2. 模型/分块最小扩展：`ParagraphItem.page_number`、`LogicalBlock.start_page`、page 透传
   → 验证：`test_word_chunker` 回归（Word 无 page 仍 page_number=1）+ 新例（带 page 的项→chunk 携带起始页）。
3. 新增 `core/pdf_extractor.py`（先纯函数后流水线）
   → 验证：单测覆盖聚段/去重过滤/表格区剔除/图片过滤/标题判定/排序/扫描页；
   PyMuPDF 合成 PDF（不同字号加粗标题 + 正文 + 图片）走通 `extract_pdf_items`。
4. `PDFParser` 重写 + `parse_document` 返回契约 + `document_service` 接线
   → 验证：mock 级 service 测试 + docx 链路回归；更新引用旧 PDF 返回契约的测试。
5. 全量验证：后端 pytest 定向 + 全量；用 `D:\Pictures\投标` 三个样本跑本地脚本
   （不依赖外部基础设施）核对 items/chunks/标题/表格/图片/扫描页识别结果；输出抽样供人工复核。
6. `docs/changes.md` 追加修改记录，diff 审计（每行改动可追溯本方案）。

## 6. 验证方式（验收清单）

- [ ] 单测：标题判定（第X章/多级/（10）列表项/图1-1 题注/字号降级档位/加粗兜底）；聚段与 span 拼接；
      表格 bbox 去重；页眉/页码过滤；图片（无 rect/装饰重复/水印/正常）过滤与归位；
      扫描页识别；parse 契约（tuple）；word_chunker page 透传 + Word 回归。
- [ ] 测试翻新：`test_file_utils`（pdf True）、`test_document_router`（pdf 上传放行）等旧"拒绝"用例。
- [ ] 后端全量 pytest（基线 677 passed，目标全绿）。
- [ ] 手工（本地脚本 + 真实样本）：`02 - 图片嵌入表格.pdf` 正常产出段落/表格/图片项 → chunk 含 heading_path/page/image_keys；
      `身份证.pdf` 单页扫描 → 明确跳过；解析统计日志正确。
- [ ] 手工（可选，需运行环境）：上传真实招标 PDF → 状态 completed、切片页标题/页码/图片预览正确、检索命中引用页码正确。
- [ ] `docs/changes.md` 修改记录与本次 diff 一一对应。

## 7. 风险与开放问题

| 风险 | 缓解 |
|------|------|
| PDF 版式多样（双栏/扫描/矢量图），标题识别误判 | 正则为主 + 保守补充判据；INFO 抽样日志便于真档调参；扫描页跳过有界 |
| pdfplumber 表格识别对无边框表格失效 → 文本行未去重 | 单页异常降级记日志；样本验证；后续可加"文本行纵向连续性"二次去重 |
| 图片量大/装饰图片误提取 → MinIO 存储与 OCR 成本 | 无 rect/装饰/水印过滤 + extract_images 开关（未配置 OCR/vision 时完全不提取，沿用 Word 语义） |
| 块跨页 → page_number 取起始页（非精确末页） | 与"引用第 N 页"展示口径一致，接受 |
| 双栏文档读取顺序 | v1 单栏优先（自上而下），记为已知局限，真档样本验证时确认 |

## 8. 部署提醒
- 无迁移；后端重启即生效（含 docker 镜像重建部署）。
- 现有 PDF 未上传（此前被禁），无需数据迁移。
