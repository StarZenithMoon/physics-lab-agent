"""Small arithmetic model expressions; no statements, attributes or arbitrary calls."""
import ast
import keyword
import numpy as np

FUNCTIONS = {name: getattr(np, name) for name in ("exp", "log", "sqrt", "sin", "cos", "tan", "abs")}
CONSTANTS = {"pi": np.pi, "e": np.e}
ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Load,
           ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.UAdd, ast.USub)


def valid_parameter(name):
    return isinstance(name, str) and name.isidentifier() and not keyword.iskeyword(name) and not name.startswith("_") and name not in {*FUNCTIONS, *CONSTANTS, "x"}


def expression(source, names):
    if not isinstance(source, str) or not source.strip() or len(source) > 2000:
        raise ValueError("model expression must be nonempty arithmetic text (max 2000 chars)")
    tree = ast.parse(source, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 300:
        raise ValueError("model expression too complex")
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED):
            raise ValueError(f"unsupported expression element: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in {*names, *FUNCTIONS, *CONSTANTS}:
            raise ValueError(f"unknown expression name: {node.id}")
        if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS or node.keywords):
            raise ValueError("only declared mathematical functions are allowed")
        if isinstance(node, ast.Constant) and (isinstance(node.value, bool) or not isinstance(node.value, (int, float)) or not np.isfinite(node.value)):
            raise ValueError("only finite numerical constants are allowed")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow) and isinstance(node.right, ast.Constant) and abs(node.right.value) > 100:
            raise ValueError("literal exponent too large")
    code = compile(tree, "<measurement-expression>", "eval")

    def evaluate(**values):
        with np.errstate(over="raise", invalid="raise", divide="raise"):
            return eval(code, {"__builtins__": {}}, {**FUNCTIONS, **CONSTANTS, **values})
    return evaluate
