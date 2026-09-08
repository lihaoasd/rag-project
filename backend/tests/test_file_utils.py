"""
文件工具模块单元测试。
"""
import os
import tempfile
import shutil

from utils.file_utils import validate_file_type, get_file_extension, ensure_temp_dir


class TestValidateFileType:
    """测试文件格式校验。"""

    def test_pdf_valid(self):
        assert validate_file_type("document.pdf") is True

    def test_pdf_uppercase(self):
        assert validate_file_type("document.PDF") is True

    def test_pdf_mixed_case(self):
        assert validate_file_type("document.Pdf") is True

    def test_docx_valid(self):
        assert validate_file_type("document.docx") is True

    def test_docx_uppercase(self):
        assert validate_file_type("document.DOCX") is True

    def test_doc_valid(self):
        assert validate_file_type("document.doc") is True

    def test_unsupported_txt(self):
        assert validate_file_type("document.txt") is False

    def test_unsupported_png(self):
        assert validate_file_type("document.png") is False

    def test_no_extension(self):
        assert validate_file_type("document") is False

    def test_unsupported_xls(self):
        assert validate_file_type("document.xls") is False

    def test_unsupported_zip(self):
        assert validate_file_type("document.zip") is False

    def test_empty_filename(self):
        assert validate_file_type("") is False

    def test_multiple_dots_filename(self):
        assert validate_file_type("test.doc.pdf") is True  # 扩展名取最后一段 .pdf


class TestGetFileExtension:
    """测试文件扩展名提取。"""

    def test_pdf(self):
        assert get_file_extension("test.pdf") == "pdf"

    def test_docx(self):
        assert get_file_extension("test.docx") == "docx"

    def test_doc(self):
        assert get_file_extension("test.doc") == "doc"

    def test_uppercase(self):
        assert get_file_extension("test.PDF") == "pdf"

    def test_no_extension(self):
        assert get_file_extension("test") == ""

    def test_multiple_dots(self):
        assert get_file_extension("test.backup.pdf") == "pdf"


class TestEnsureTempDir:
    """测试临时目录管理。"""

    def test_creates_directory_if_not_exists(self):
        tmp_root = tempfile.gettempdir()
        test_dir = os.path.join(tmp_root, "test_ai_bid_rag_temp")

        # 确保测试前不存在
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir)

        try:
            result = ensure_temp_dir(test_dir)
            assert os.path.isdir(result)
            assert os.path.isabs(result)
        finally:
            if os.path.exists(test_dir):
                shutil.rmtree(test_dir)

    def test_returns_absolute_path_for_relative(self):
        result = ensure_temp_dir("./temp")
        assert os.path.isabs(result)

    def test_existing_directory_no_error(self):
        tmp_root = tempfile.gettempdir()
        test_dir = os.path.join(tmp_root, "test_ai_bid_rag_temp")

        # 首次创建
        path1 = ensure_temp_dir(test_dir)
        # 再次调用不报错
        path2 = ensure_temp_dir(test_dir)
        assert path1 == path2

        # 清理
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir)
