"""
标题识别模块单元测试 — is_heading() 正则匹配验证。
"""
import pytest

from core.parser import is_heading


class TestHeadingPatterns:
    """标题正则匹配 — 正例和负例。"""

    # === 正例：应识别为标题 ===

    @pytest.mark.parametrize("text,expected_level", [
        # Level 1: 章/节/条
        ("第一章 招标公告", 1),
        ("第1章 项目概况", 1),
        ("第十二章 技术方案", 1),
        ("第一节 商务条款", 1),
        ("第一条 合同标的", 1),
        ("附件1：投标函", 1),
        ("附表一", 1),
        ("附录A", 1),
        ("附件", 1),

        # Level 2: 中文数字 / 阿拉伯数字
        ("一、报价说明", 2),
        ("二、技术规格", 2),
        ("十一、验收标准", 2),
        ("1、项目概况", 2),
        ("1. 项目概况", 2),
        ("12. 其他事项", 2),

        # Level 3: 多级数字 / 括号中文数字
        ("1.1 项目名称", 3),
        ("2.3.5 详细参数", 3),
        ("（一）资质要求", 3),
        ("(二) 财务状况", 3),

        # Level 4: 括号数字 / 圆圈数字
        ("(1) 营业执照", 4),
        ("（2）资质证书", 4),
        ("① 网络设备", 4),
        ("⑩ 验收文件", 4),

        # Level 5: 字母序号
        ("a. 技术指标", 5),
        ("b) 商务条款", 5),
        ("(a) 分项报价", 5),
    ])
    def test_positive_matches(self, text, expected_level):
        result, level = is_heading(text)
        assert result is True, f"'{text}' 应识别为标题"
        assert level == expected_level, f"'{text}' 层级应为 {expected_level}，实际 {level}"

    # === 负例：不应识别为标题 ===

    @pytest.mark.parametrize("text", [
        # 非标题前缀
        "注：本条款解释权归招标人所有",
        "注意：以上内容必须逐条响应",
        "备注：详见附件",
        "说明：此表由投标人填写",
        "例如：技术方案中需包含以下内容",
        "示例：正确的报价应为含税价",
        "其中包括但不限于",
        "特别说明：本项为加分项",
        "包括以下内容",
        "如下所示",
        "以上条款均为实质性条款",
        "以下内容必须逐条响应",

        # 正文标点结尾（列表项）
        "1. 具有独立法人资格；",
        "2. 注册资本不低于500万元。",
        "3. 近三年无重大违法记录。",

        # 超过长度限制
        "第一章 这是一段非常长的文本，超过了标题长度限制，不应该被识别为标题，" + "包含大量详细描述。" * 10,

        # 长文本但不是标题
        "本次采购主要包括以下方面的内容，投标人需要逐条响应并提供相应的资质文件",

        # 空文本
        "",
        "   ",
    ])
    def test_negative_matches(self, text):
        result, level = is_heading(text)
        assert result is False, f"'{text[:30]}...' 不应被识别为标题"

    # === 样式辅助判定 ===

    def test_style_heading_whitelist(self):
        """Heading 样式 → 直接判定为标题"""
        result, level = is_heading("项目概况", style_name="Heading 2")
        assert result is True
        assert level == 2

    def test_style_heading_no_number(self):
        """Heading 样式无数字 → level=1"""
        result, level = is_heading("投标须知", style_name="Heading")
        assert result is True
        assert level == 1

    def test_style_heading_beyond_5(self):
        """Heading 6+ → 截断为 5"""
        result, level = is_heading("细项", style_name="Heading 6")
        assert result is True
        assert level == 5

    def test_style_list_blacklist(self):
        """List Paragraph 样式 → 不识别为标题"""
        result, level = is_heading("1. 具有独立法人资格", style_name="List Paragraph")
        assert result is False

    def test_style_toc_blacklist(self):
        """TOC 样式 → 不识别为标题"""
        result, level = is_heading("第一章 总则", style_name="TOC 1")
        assert result is False

    # === 边界情况 ===

    def test_heading_with_colon(self):
        """附件带冒号"""
        result, level = is_heading("附件1：投标函格式")
        assert result is True
        assert level == 1

    def test_chinese_number_heading_no_punctuation(self):
        """中文数字标题无标点"""
        # 这种更像是正文，不应匹配
        result, _ = is_heading("一是必须符合资格要求")
        # 正则要求 [、．] 结尾，'是' 不符合 → 不应匹配
        assert result is False

    def test_date_not_heading(self):
        """2024年1月... 不应匹配"""
        result, _ = is_heading("2024年1月1日")
        assert result is False

    def test_standalone_number_with_dot(self):
        """纯数字+句号 → 应匹配 level 2"""
        result, level = is_heading("1. 项目概况")
        assert result is True
        assert level == 2
