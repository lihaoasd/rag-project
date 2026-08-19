"""基础模型与 JSONB 编码单元测试。"""
from models.base import UTF8JSONB


def test_utf8jsonb_serializer_preserves_chinese():
    processor = UTF8JSONB().bind_processor(None)
    serialized = processor({"name": "力建设有限公司2021"})

    assert "力建设有限公司2021" in serialized
    assert "\\u" not in serialized
