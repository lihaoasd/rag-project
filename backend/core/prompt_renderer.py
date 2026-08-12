"""
提示词变量渲染引擎 — 解析模板中的变量占位符并替换为实际值。
"""
import re
import string
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

    # 构建 required 集合
    required_vars = {
        vd["name"] for vd in variable_defs if vd.get("required", True)
    }

    # 补充缺失的非必填变量为默认值
    template_vars = extract_variables(template)
    safe_vars: dict[str, str] = {}
    for var_name in template_vars:
        if var_name in variables:
            safe_vars[var_name] = variables[var_name]
        elif var_name in required_vars:
            # 必填变量未提供 → 保留占位符
            safe_vars[var_name] = f"{{{var_name}}}"
        else:
            # 非必填变量 → 空字符串
            safe_vars[var_name] = ""

    # 使用 SafeFormatter 避免缺失 key 时抛异常
    formatter = _SafeFormatter()
    return formatter.format(template, **safe_vars)


class _SafeFormatter(string.Formatter):
    """安全的 str.format，对缺失的 key 保留原文。"""

    def __init__(self):
        super().__init__()
        self.missing_keys: list[str] = []

    def get_value(self, key, args, kwds):
        if isinstance(key, str):
            if key in kwds:
                return kwds[key]
            self.missing_keys.append(key)
            return f"{{{key}}}"
        return super().get_value(key, args, kwds)
