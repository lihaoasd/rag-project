# 词条"冠状动脉粥样硬化性心脏病"整词生效方案（终版：人工导出放置 + 挂载 + 重启）

> 日期: 2026-08-20 | 状态: 已实施 | 关联: [08-es-fulltext-search-design.md](../08-es-fulltext-search-design.md) §3.2/§6.3/§6.4

## 1. 问题现象

用户新增词条「冠状动脉粥样硬化性心脏病」（已入库 `lexicon_terms`，enabled=true），
全文检索返回高亮为碎片：

```
1.1 <em>冠状</em><em>动脉粥样硬化</em>性<em>心脏病</em>
```

期望：整词匹配 + 整词高亮（`<em>冠状动脉粥样硬化性心脏病</em>`）。

## 2. 架构决策（用户确认）

原设计（docs/08 §3.2）让 **ES 通过 `remote_ext_dict` 反向轮询业务后端**拉取词库，
属"基础设施反向依赖业务应用"（历史已踩坑：后端不可达 → `_QuantifierDict is null`）。

**最终决策：纯人工流程（用户明确要求）**：

- 词库由前端词库页"**导出 IK 词典**"下载 `ik_ext.dic`（一行一词）；
- **人工放置**到宿主 `/opt/es/dicts/`（已挂载进容器 `config/analysis-ik/dicts/`）；
- **重启 ES**，IK 加载本地词典生效；
- 无任何自动链路（废弃 remote_ext_dict、删除同步脚本），ES 进程零依赖后端。

依赖方向：

```
前端词库页 ──导出──▶ ik_ext.dic ──人工放置──▶ /opt/es/dicts/（挂载进容器 dicts/）
                                                    │ docker restart bid-es
                                                    ▼
                                            IK 加载本地词典，整词分词生效
```

## 3. 服务器现状（已核验）

| 项 | 状态 |
|----|------|
| 容器 | `bid-es`，镜像 `es-ik:8.13.4`，`restart=unless-stopped` |
| 挂载 | `/opt/es/data`（数据）、`/opt/es/config/IKAnalyzer.cfg.xml`（配置）、`/opt/es/dicts`（词库）→ 容器 `config/analysis-ik/dicts/` |
| IKAnalyzer.cfg.xml | `ext_dict=extra_main.dic;dicts/ik_ext.dic`，`remote_ext_dict` 清空 |
| 词条 | PG 已入库；导出端点/前端导出按钮均可输出该词条 |
| 存量索引 | 已按库重建（医学词汇库），整词命中验证通过 |

## 4. 加词操作流程（用户操作手册）

```
1. 前端词库页：新增/编辑词条
2. 前端词库页："导出 IK 词典" → 下载 ik_ext.dic
3. 把 ik_ext.dic 人工上传/覆盖到服务器 192.168.60.120 的 /opt/es/dicts/ik_ext.dic
4. 服务器执行: docker restart bid-es
5. （存量文档整词命中）按库重建索引:
     POST /api/es/rebuild?kb_id=<知识库ID>   或 前端「ES 对账」页"重建该库"
   （新增文档无需重建，写入时即按新词典分词）
```

## 5. 已实施变更

- 服务器 120：
  - `/opt/es/config/IKAnalyzer.cfg.xml`：改本地词典方案（ext_dict → dicts/ik_ext.dic，remote 清空）；
  - `/opt/es/dicts/ik_ext.dic`：词库词典文件（一行一词，UTF-8 无 BOM）；
  - 重建 `bid-es` 容器：新增挂载 `config/IKAnalyzer.cfg.xml` 与 `dicts/`（数据卷保留，对账 0 差异）；
  - 删除 `/opt/es/sync_ik_dict.sh`（不再有自动链路）；
- 前端：`LexiconView.vue` 新增"导出 IK 词典"按钮（下载 ik_ext.dic）；`api/lexicon.js` 新增 `exportIkDict`；
- 文档：`docs/08` §1.2/§2/§3.2/§4.2/§4.3/§5.6/§6.2-§6.5/验证/风险表 → 人工导出放置流程；`elasticsearch/config/IKAnalyzer.cfg.xml` 同步。

## 6. 验证结果

- `_analyze` ik_smart/ik_max_word 输出整词"冠状动脉粥样硬化性心脏病[0-12]"；
- ES 搜索整词高亮 `<em>冠状动脉粥样硬化性心脏病</em>`（原碎片高亮），total 244→1（整词精确匹配）；
- 对账 4 库 pg=es diff=0；后端无代码改动；
- 前端 `npm run build` 通过。

## 7. 不做的事

- 不改后端代码（词库 CRUD/导出端点/重建逻辑已支持）；
- 不启用 remote_ext_dict（彻底移除 ES→后端 反向依赖）；
- 不做秒级热更新（IK 插件硬限制；人工"导出→放置→重启"约 30 秒，可接受）。
