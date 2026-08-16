"""core 导入边界测试：core 不 import 具体 memory/llm/scenarios/actions 实现。"""

import ast
from pathlib import Path

CORE_DIR = Path(__file__).parent.parent / "core"
FORBIDDEN = {"memory", "llm", "actions", "scenarios"}


def test_core_has_no_concrete_imports():
    for path in CORE_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in FORBIDDEN, (
                        f"{path.name} import 了具体实现: {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in FORBIDDEN, (
                    f"{path.name} import 了具体实现: {node.module}"
                )
