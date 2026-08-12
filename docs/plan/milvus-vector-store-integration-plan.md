# 引入 llama_index MilvusVectorStore 检索栈

## Context

当前项目在 LLM、Embedding、Document、SentenceSplitter 四层使用了 llama_index，但向量存储（indexing）和检索（retrieval）两层绕开了 llama_index，直接用 pymilvus 旧式 API（`Collection`、`connections`）。用户希望引入 llama_index 的完整检索栈：`MilvusVectorStore` → `VectorStoreIndex` → `Retriever` → `NodePostprocessor`，保持架构一致性。

## 核心挑战：Schema 不兼容

当前自定义 schema：
```
id (INT64, auto_id, PK) | embedding (FLOAT_VECTOR) | doc_id | file_name | page_number | chunk_index | text
```

`MilvusVectorStore` 内置 schema（不可变核心字段）：
```
id (VARCHAR, PK = node_id) | embedding (FLOAT_VECTOR) | _node_content (JSON) | _node_type | doc_id | text | ref_doc_id
```

- 主键类型不同（INT64 auto_id vs VARCHAR node_id）
- 元数据存储方式不同：自定义字段（file_name, page_number 等）在 llama_index 中会被序列化到 `_node_content` JSON 字段和 `ref_doc_id` 中，通过 `node_to_metadata_dict()` / `metadata_dict_to_node()` 序列化
- `MilvusVectorStore` 通过 `scalar_field_names` + `scalar_field_types` 参数支持**添加额外标量字段**，可以把 `file_name`、`page_number`、`chunk_index` 作为标量字段保留

**关键发现**：`MilvusVectorStore` 使用新版 `MilvusClient` API（`uri` 连接），而当前代码使用旧版 `Collection` + `connections` API。两者是 pymilvus 的两套不同 API 风格，但底层操作同一个 Milvus 实例。

不能直接在旧 Collection 上套 `MilvusVectorStore`——因为 schema 结构不同、主键类型不同。**这意味着迁移需要：要么改造现有 Collection，要么新建 Collection 用新 schema。**

**决定**：采用新建方案。`MilvusVectorStore` 创建新 Collection 时使用 llama_index 的默认 schema + 自定义标量字段（`file_name`、`page_number`、`chunk_index`）。旧的 `kb_*` Collection 保持不变，后续逐步淘汰。

## 接口变更（影响上游）

`MilvusVectorStore.add()` 返回 `List[str]`（llama_index node_id，VARCHAR），而非旧代码的 `List[int]`（Milvus auto_id，INT64）。这意味着：

| 函数 | 旧返回类型 | 新返回类型 | 影响 |
|------|-----------|-----------|------|
| `build_index()` | `list[int]` | `list[str]` | `document_service.py` 用其返回值作为 `milvus_pk` 存入 PG |
| `insert_single_vector()` | `int` | `str` | 同上 |
| `delete_single_vector(kb_id, milvus_pk)` | `int` 参数 | `str` 参数 | 同上 |

PG 中 `chunks.milvus_pk` 字段（`Integer`）需要改为 `String` 类型，需 Alembic 迁移。

## 修改范围

### 1. `backend/config.py` — 新增 Milvus URI 配置

- 新增 `milvus_uri: str` 属性，从 `milvus_host` + `milvus_port` 构建 `http://host:port` 格式

### 2. `backend/core/indexing.py` — 重写，使用 MilvusVectorStore

**旧代码职责**：
- 手动管理连接（`_ensure_connection`, `_connected`）
- 手动定义 Schema（`_create_schema`）
- 手动创建 Collection（`_get_or_create_collection`）
- 手动创建索引（`_ensure_index`）
- 手动分批向量化 + 插入（`build_index`）
- 单条插入/删除（`insert_single_vector`, `delete_single_vector`）
- Collection 删除（`drop_kb_collection`）
- 文档分块（`chunk_documents`）

**新代码**：
- 连接管理：由 `MilvusVectorStore` 内部处理（`MilvusClient`）
- Schema + Collection + Index：由 `MilvusVectorStore` 构造函数一次性完成
- 保留 `chunk_documents()` 函数（有额外逻辑：默认值、metadata 兜底），但修改为返回 llama_index `BaseNode` 列表，以便直接喂给 `vector_store.add()`
- `build_index()` → 使用 `vector_store.add(nodes)` 替代手动插入
- `insert_single_vector()` → 使用 `vector_store.add([node])` 替代
- `delete_single_vector()` → 使用 `vector_store.delete(ref_doc_id=...)`
- `delete_index()` → 使用 `vector_store.delete(ref_doc_id=doc_id)`
- `drop_kb_collection()` → 使用 `vector_store.client.drop_collection(collection_name)`

**关键实现细节**：
- `MilvusVectorStore` 实例化参数：
  ```python
  MilvusVectorStore(
      uri=settings.milvus_uri,
      collection_name=f"kb_{safe_kb_id}",
      dim=settings.embedding_dim,
      similarity_metric="IP",
      index_config={"index_type": "IVF_FLAT", "params": {"nlist": 128}},
      search_config={"nprobe": 16},
      scalar_field_names=["file_name", "page_number", "chunk_index"],
      scalar_field_types=[DataType.VARCHAR, DataType.INT64, DataType.INT64],
      text_key="text",
      overwrite=False,
      collection_properties={"collection.ttl.seconds": 7 * 24 * 3600},
  )
  ```
- 需要将 metadata 中的 `file_name`、`page_number`、`chunk_index` 通过 `TextNode.metadata` 传入，MilvusVectorStore 会通过 `node_to_metadata_dict()` 自动映射到对应标量字段

### 3. `backend/core/retrieval.py` — 重写，使用 llama_index 检索栈

**旧代码职责**：
- 手动创建 Embedding 模型
- 手动 `coll.search()` + 组装结果

**新代码**：
```python
# 创建 vector_store 实例（与索引时使用相同的参数）
vector_store = MilvusVectorStore(...)
index = VectorStoreIndex.from_vector_store(vector_store, embed_model=embed_model)

# 构建 retriever + postprocessor 链
retriever = index.as_retriever(
    similarity_top_k=top_k,
    vector_store_query_mode=VectorStoreQueryMode.DEFAULT,
)

# 检索
nodes = await retriever.aretrieve(query)

# 后处理：阈值过滤
processor = SimilarityPostprocessor(similarity_cutoff=similarity_threshold)
filtered_nodes = processor.postprocess_nodes(nodes)

# 组装为当前的数据格式（保持与 service 层兼容）
hits = []
for node_with_score in filtered_nodes:
    hits.append({
        "score": round(node_with_score.score, 4),
        "text": node_with_score.node.text,
        "doc_id": node_with_score.node.metadata.get("doc_id", ""),
        "file_name": node_with_score.node.metadata.get("file_name", ""),
        "page_number": node_with_score.node.metadata.get("page_number", 1),
        "chunk_index": node_with_score.node.metadata.get("chunk_index", 0),
    })
```

**关键点**：`retrieve()` 函数签名和对外的返回格式**保持不变**，上层 `search_service.py` 和 `chat_service.py` 无需修改。

### 4. `backend/pyproject.toml` — 已安装，无需变更

`llama-index-vector-stores-milvus` 已在依赖中，版本 1.1.0。

## 不变更的文件

- `backend/services/chat_service.py` — 不修改，`retrieve()` 的签名和返回格式不变
- `backend/services/search_service.py` — 不修改
- `backend/routers/search.py` — 不修改
- `backend/core/parser.py` — 不修改
- `backend/core/llm_factory.py` — 不修改，但 `retrieval.py` 会直接使用 `create_embedding()`

## 实现步骤

1. **修改 `config.py`**：添加 `milvus_uri` 属性
2. **修改 `core/indexing.py`**：
   - 移除 `_ensure_connection`、`_create_schema`、`_get_or_create_collection`、`_ensure_index`
   - 新增 `_get_vector_store(kb_id)` 工厂函数
   - 重写 `build_index()`：chunk → nodes → `vector_store.add()`
   - 重写 `insert_single_vector()` / `delete_single_vector()` / `delete_index()` / `drop_kb_collection()`
   - `chunk_documents()` 改为返回 `(list[str], list[dict], list[TextNode])`
3. **修改 `core/retrieval.py`**：
   - 移除直接 pymilvus 调用
   - 使用 `MilvusVectorStore` + `VectorStoreIndex.from_vector_store()` + `as_retriever()` + `SimilarityPostprocessor`
4. **验证**：启动服务、上传文档、执行检索和问答，确认结果一致

## 验证方式

1. 启动后端服务 `cd backend && python main.py`
2. 上传一个 PDF 文档到知识库，确认索引构建成功（日志显示 collection 创建 + 数据插入）
3. 调用 `POST /api/knowledge-bases/{kb_id}/search` 执行检索，确认返回结果包含 `score`、`text`、`doc_id`、`file_name`、`page_number`
4. 调用问答接口，确认 SSE 流式输出正常，来源引用正常
5. 删除切片、新增切片，确认单条操作正常
