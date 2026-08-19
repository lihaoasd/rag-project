"""
文件工具 — 格式校验、临时目录管理。
"""
import os
from pathlib import Path


ALLOWED_EXTENSIONS = {".docx", ".doc"}


def validate_file_type(filename: str) -> bool:
    """校验文件是否为支持的格式（docx / doc；PDF 暂不支持解析）。"""
    ext = Path(filename).suffix.lower()
    return ext in ALLOWED_EXTENSIONS


def get_file_extension(filename: str) -> str:
    """获取文件扩展名（不含点，小写）。"""
    return Path(filename).suffix.lower().lstrip(".")


def ensure_temp_dir(temp_dir: str) -> str:
    """确保临时目录存在，返回绝对路径。"""
    path = Path(temp_dir).resolve()
    os.makedirs(path, exist_ok=True)
    return str(path)
