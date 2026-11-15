import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "dodeed"


def _forbidden(name: str) -> bool:
    return (
        name == "aiogram"
        or name.startswith("aiogram.")
        or name == "dodeed.channels.telegram"
        or name.startswith("dodeed.channels.telegram.")
    )


def test_agent_and_restaurant_do_not_import_channel_specific_code():
    violations = []
    for pkg in ("agent", "restaurant"):
        for path in (SRC / pkg).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
                violations += [f"{path}: {n}" for n in names if _forbidden(n)]
    assert violations == []
