# 44. PDF 扫描页整页 OCR 方案

> 状态：已实现（2026-09-05）　|　类型：需求变更（42 号方案 v1 的扫描页处理升级）　|　关联：docs/plan/42-pdf-parse-chunk-plan.md §4.4 图像流程

## 1. 需求与变更点

**需求（用户确认）**："PDF 中遇到图片采用 OCR" → 具体为：**无文本层的扫描页（整页是图片）也做整页 OCR**，
纯扫描 PDF 不再抛"暂不支持整页 OCR"，可正常解析入库。

**对 42 号方案 v1 的变更**：
| v1（现状） | v1.1（本次） |
|---|---|
| 无文本层页：跳过（计入物理页数，不出条目） | 无文本层页：若含整页图片 → 整页图走 OCR，文字作为正文项入库（可查看/检索） |
| 纯扫描 PDF：解析抛 ValueError | 已配置 OCR/识别模型时正常解析；未配置时仍抛错（提示配置模型） |
| 文本型 PDF 内嵌图 OCR | 保持不变（43 链路已覆盖） |

## 2. 现状事实（已核对代码）

- `core/pdf_extractor.py::_parse_page`：无正文段且无表格时直接 `return []`（行 642-644），由
  `extract_pdf_items` 记为 `skipped_pages`；页内图片（`page.get_images` → `extract_image`）仅在**有文本层的页**
  提取并归位（行 647-673）。
- `PDFParser.parse`：`page_count>0 且 items 为空` → 抛 "PDF 无可解析文本（疑似扫描件，暂不支持整页 OCR）"。
- 图片消费链路（已具备，无需新引擎）：
  - `ParagraphItem.images: list[WordImage]`（image_bytes/content_type）；
  - `aggregate_paragraphs`：仅图片（空文本）的段落会并入逻辑块（`images` 非空即保留），且 `start_page` 取自段落 `page_number`；
  - `word_chunk_blocks`（chunk_blocks 行 185-203）：`block.images` 的 `ocr_text`（vision 兜底）并入正文，
    **"仅图片的块也能产出文本 chunk"**（注释即此语义），并携带 `image_keys`（MinIO 原图，供前端预览）；
  - `process_word_images`：OCR client（create_ocr(ocr_config)）逐图 `recognize(bytes)`，失败/空结果由 vision 兜底，
    识别文字写回 `image.ocr_text`；依赖知识库 OCR（或 vision）模型配置（`extract_images` 由配置决定，见 document_service）。
- 结论：扫描页可建模为「空文本 ParagraphItem + 整页图 WordImage」，OCR/上传/分块/入库全部复用现有通道。

## 3. 关键设计（伪代码）

### 3.1 扫描页判定与整页图候选（pdf_extractor）
```python
# core/pdf_extractor.py 常量
SCAN_IMAGE_MIN_COVER = 0.2   # 图面积 ≥ 页面积 20% 才视为“整页扫描候选”，防纯 logo/装饰页被 OCR

def _scan_page_images(page, page_no, page_area, decor/watermark/band 过滤集合) -> list[WordImage]:
    """无文本层页：取覆盖页面积 ≥20% 且未被过滤的图片字节（通常每页 1 张整页扫描）。"""
    out = []
    for xref in {img[0] for img in page.get_images(full=True)}:
        if xref in (decor ∪ watermark ∪ band): continue
        bbox = _image_zone_bbox(page, xref);      if bbox is None: continue
        if _image_area(bbox) < page_area * SCAN_IMAGE_MIN_COVER: continue
        base = page.parent.extract_image(xref)
        if base and base.get("image"):
            out.append(WordImage(image_bytes=base["image"],
                                 content_type=f"image/{base.get('ext', 'png')}"))
    return out
```

### 3.2 _parse_page 变更（替换“无文本层直接跳过”）
```python
# 原：if not paras and not table_items: return []          （行 642-644）
if not paras and not table_items:
    if not extract_images:                      # 未配置识别模型 → 维持现状：跳过
        return []
    page_area = page.rect.width * page.rect.height
    scan_images = _scan_page_images(page, page_no, page_area,
                                    decor_xrefs, watermark_xrefs, band_image_xrefs)
    if not scan_images:                         # 无整页图 → 空白页，仍跳过
        return []
    # 整页扫描 → 每张整页图为“空文本 + 图”项，OCR 由块级 process_word_images 完成
    return [ParagraphItem(text="", page_number=page_no, images=[img])
            for img in scan_images]
```

### 3.3 纯扫描文档不再误报
- `PDFParser.parse` 的 ValueError 触发条件不变（items 为空才抛）；整页 OCR 后 items 非空 → 正常解析。
- 未配置 OCR/vision（extract_images=False）纯扫描文档仍抛错，但**文案更新**提示配置模型：
  "PDF 无可解析文本（疑似扫描件：未配置 OCR/图片识别模型，或页面确无内容）"。

### 3.4 与既有链路衔接（无需改动）
- 扫描页项无标题 → 归属当前逻辑块正文；每页一项保证 page_number/start_page 正确、chunk 页码=扫描页页码。
- OCR 文字非空 → 产出文本 chunk 并可全文检索；OCR 空（纯图片无文字）→ 该块无文本被分块器自然跳过，
  但图片仍上传（image_keys）——避免无意义空块。
- `skipped_pages` 语义收窄为“确无内容页（无文本且无整页图）”。

## 4. 改动范围

| 文件 | 改动 |
|---|---|
| `backend/core/pdf_extractor.py` | 常量 `SCAN_IMAGE_MIN_COVER`；新增 `_scan_page_images`；`_parse_page` 无文本层分支改为整页 OCR 项（受 `extract_images` 门控） |
| `backend/core/parser.py` | `PDFParser` ValueError 文案更新（提示配置 OCR/识别模型） |
| `backend/tests/test_pdf_extractor.py` | 新增：扫描页（仅整页图）extract_images=True → 产出 1 个空文本+图项、page_number 正确、skipped=[]；False → 仍跳过；空白页仍跳过（回归）；整页图项 → chunk（手工置 ocr_text）后文本/页码/image_keys 正确 |
| `docs/changes.md` | 修改记录 |
| 无需改动 | word_chunker、image_recognition_service、document_service、parser 返回结构、DB/索引 schema |

## 5. 实现步骤与验证

1. `pdf_extractor`：常量 + `_scan_page_images` + `_parse_page` 分支改造。
2. `parser.py` ValueError 文案。
3. 测试：新增扫描页用例（合成“仅图片页”PDF：先建文本页渲染成 PNG，再插入到空白页保存）+ 空白页回归 + OCR 文本并入 chunk 断言。
4. 全量 pytest 回归（基线 722 passed）。
5. 真实样本验收（部署环境）：`D:\Pictures\投标\身份证.pdf / 身份证2.pdf` 配置 OCR 模型后应能解析出身份证文字；未配置时报错文案更新。
6. `docs/changes.md` 记录 + diff 审计。

## 6. 风险与说明

- **成本/耗时**：扫描页逐页 OCR（PaddleOCR），大文档显著变慢；沿用单 worker FIFO，无并发放大。首版不做“页级并行/降采样”，如需再单独优化。
- **OCR 质量**：倾斜/低清晰扫描取决于模型；vision 仅在 OCR 无文字时兜底（与内嵌图一致）。
- **误 OCR**：面积 ≥20% 页面的非扫描大图（如宣传海报内嵌整页图）会被当扫描页 OCR —— 若文字层页面不受影响（该分支仅无文本层页触发），可接受；过滤阈值按需调参。
- **空白页**（无文本无图）维持跳过/不计条目，不 OCR。
- 该能力依赖知识库 OCR/vision 模型配置；未配置时行为与 42 号 v1 一致（跳过/报错），不新增静默失败。
