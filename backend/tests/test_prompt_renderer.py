"""
core/prompt_renderer.py 单元测试 — 变量渲染（含 JSON/花括号模板不被误解析）。
"""
from core.prompt_renderer import extract_variables, render_prompt, validate_template


class TestRenderPrompt:
    def test_replace_simple_variable(self):
        assert render_prompt("查询：{query}", {"query": "abc"}) == "查询：abc"

    def test_replace_all_occurrences(self):
        assert render_prompt("{a}-{a}", {"a": "x"}) == "x-x"

    def test_missing_required_keeps_placeholder(self):
        defs = [{"name": "query", "required": True}]
        assert render_prompt("Q:{query}", {}, defs) == "Q:{query}"

    def test_required_defaults_true_when_key_missing(self):
        # 变量定义未显式写 required 时按必填处理（与历史行为一致）
        defs = [{"name": "query"}]
        assert render_prompt("Q:{query}", {}, defs) == "Q:{query}"

    def test_missing_optional_removed(self):
        defs = [{"name": "history", "required": False}]
        assert render_prompt("A{history}B", {}, defs) == "AB"

    def test_missing_without_defs_removed(self):
        assert render_prompt("Q{query}E", {}) == "QE"

    def test_none_variables_treated_as_empty(self):
        assert render_prompt("X{query}Y", None) == "XY"

    def test_json_braces_in_template_not_parsed(self):
        # 回归：0016 实体抽取默认提示词含 JSON 示例花括号，
        # 旧实现（str.format）抛 ValueError: Invalid format specifier
        template = (
            "输出严格 JSON（不要其他文字，不要 markdown 代码块）:\n"
            '{"entities":[{"name":"...","type":"ORGANIZATION","description":"..."}],'
            ' "relationships":[{"source":"...","target":"...","type":"属于",'
            '"description":"..."}]}'
        )
        assert render_prompt(template, {}) == template

    def test_placeholder_alongside_json_braces(self):
        template = '按 JSON 输出：{"note":"{query}"}'
        assert render_prompt(template, {"query": "abc"}) == '按 JSON 输出：{"note":"abc"}'

    def test_provided_value_with_braces_kept_verbatim(self):
        # 变量值本身含花括号时原样插入，不二次解析
        assert render_prompt("R:{query}", {"query": '{"a":1}'}) == 'R:{"a":1}'


class TestExtractVariables:
    def test_order_and_dedupe(self):
        assert extract_variables("{b} {a} {b}") == ["b", "a"]

    def test_ignores_quoted_json_keys(self):
        # JSON 键带引号（{"type": ...}），不会误判为变量
        assert extract_variables('{"entities":[{...}], "x": 1}') == []


class TestValidateTemplate:
    def test_valid(self):
        result = validate_template("请回答：{query}")
        assert result["valid"] is True
        assert result["variables"] == ["query"]

    def test_empty_template_invalid(self):
        assert validate_template("   ")["valid"] is False

    def test_empty_braces_invalid(self):
        assert validate_template("a {} b")["valid"] is False
