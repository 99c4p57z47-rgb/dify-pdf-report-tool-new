import ast
from pathlib import Path


def test_health_route_disables_invalid_union_response_model() -> None:
    source = Path("app/main.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    health = next(
        node
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "health"
    )
    decorator = next(
        item
        for item in health.decorator_list
        if isinstance(item, ast.Call)
        and isinstance(item.func, ast.Attribute)
        and item.func.attr == "get"
    )
    response_model = next(
        (keyword.value for keyword in decorator.keywords if keyword.arg == "response_model"),
        None,
    )
    assert isinstance(response_model, ast.Constant)
    assert response_model.value is None
