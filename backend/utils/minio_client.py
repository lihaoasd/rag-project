"""
MinIO 客户端封装 — 文档文件的上传、删除、批量清理。
"""
from pathlib import Path

from minio import Minio
from minio.deleteobjects import DeleteObject

from config import settings
from utils.logging import get_logger

logger = get_logger(__name__)

# 模块级单例 — 遵循 llm_factory.py 的模式
_client: Minio | None = None


def _get_client() -> Minio:
    """获取或创建 MinIO 客户端。"""
    global _client
    if _client is None:
        _client = Minio(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        # 确保 Bucket 存在
        bucket = settings.minio_bucket
        if not _client.bucket_exists(bucket):
            _client.make_bucket(bucket)
            logger.info("创建 MinIO Bucket: %s", bucket)
        else:
            logger.info("MinIO Bucket 已存在: %s", bucket)
    return _client


def upload(object_key: str, file_path: Path, content_type: str = "application/octet-stream") -> None:
    """上传文件到 MinIO。

    Args:
        object_key: 对象键，如 kb_{id}/doc_{id}.pdf
        file_path: 本地文件路径
        content_type: MIME 类型
    """
    client = _get_client()
    client.fput_object(
        bucket_name=settings.minio_bucket,
        object_name=object_key,
        file_path=str(file_path),
        content_type=content_type,
    )
    logger.debug("MinIO 上传成功: %s", object_key)


def delete(object_key: str) -> None:
    """删除单个对象。"""
    client = _get_client()
    client.remove_object(settings.minio_bucket, object_key)
    logger.debug("MinIO 删除成功: %s", object_key)


def delete_prefix(prefix: str) -> None:
    """批量删除指定前缀的所有对象（用于删除知识库时清理所有文档）。

    Args:
        prefix: 对象键前缀，如 kb_{id}/
    """
    client = _get_client()
    objects = client.list_objects(settings.minio_bucket, prefix=prefix, recursive=True)
    delete_list = [DeleteObject(obj.object_name) for obj in objects]
    if delete_list:
        errors = client.remove_objects(settings.minio_bucket, delete_list)
        for err in errors:
            logger.error("MinIO 删除失败: %s — %s", err.object_name, err.message)
        logger.info("MinIO 批量删除: prefix=%s, count=%d", prefix, len(delete_list))
    else:
        logger.debug("MinIO 批量删除: prefix=%s 无匹配对象", prefix)
