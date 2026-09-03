"""
提示词变量渲染引擎 — 解析模板中的变量占位符并替换为实际值。

只识别 `{变量名}` 形式的占位符；模板中的其它花括号（如 JSON 示例、
输出格式说明）原样保留，不会被 str.format 之类的解析器误处理。
"""
import re
from typing import Any

# 变量占位符模式: {variable_name}
VARIABLE_PATTERN = re.compile(r"\{(\w+)\}")


def extract_variables(template: str) -> list[str]:
    """提取模板中所有变量名（去重，保持出现顺序）。"""
    seen = set()
    result = []
    for match in VARIABLE_PATTERN.finditer(template):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            result.append(name)
    return result


def validate_template(template: str) -> dict[str, Any]:
    """校验模板完整性。

    Returns:
        dict with keys:
            - valid: bool — 模板是否合法
            - variables: list[str] — 发现的变量列表
            - error: str | None — 错误信息
    """
    if not template or not template.strip():
        return {"valid": False, "variables": [], "error": "模板内容不能为空"}

    variables = extract_variables(template)

    # 检查是否包含非法的变量名（空变量名 {}）
    if re.search(r"\{\}", template):
        return {"valid": False, "variables": variables, "error": "变量名不能为空（{}）"}

    return {"valid": True, "variables": variables, "error": None}


def render_prompt(
    template: str,
    variables: dict[str, str],
    variable_defs: list[dict] | None = None,
) -> str:
    """渲染模板，将变量占位符替换为实际值。

    只替换 `{变量名}` 形式的占位符；模板中其它花括号（JSON 示例等）
    原样保留。变量值原样插入，不二次解析。

    对于模板中声明但未提供值的变量：
        - 必填变量：保留占位符原文（调用方应提前校验）
        - 非必填变量：替换为空字符串

    Args:
        template: 含 {variable_name} 占位符的模板字符串
        variables: 变量名到值的映射
        variable_defs: 可选的变量定义列表（含 required 字段），用于区分必填/非必填

    Returns:
        渲染后的字符串
    """
    if variable_defs is None:
        variable_defs = []
    variables = variables or {}

    # 必填变量集合（未提供时保留占位符原文）
    required_vars = {
        vd["name"] for vd in variable_defs if vd.get("required", True)
    }

    def _replace(match: re.Match) -> str:
        name = match.group(1)
        if name in variables:
            return variables[name]
        if name in required_vars:
            # 必填变量未提供 → 保留占位符原文
            return match.group(0)
        # 非必填变量未提供 → 替换为空字符串
        return ""

    return VARIABLE_PATTERN.sub(_replace, template)
