from pathlib import Path

OUTPUT_CONSTRAINT = "只输出合法JSON对象，禁止任何Markdown代码块、注释和前缀文字。"


def load_skill(path: str) -> str:
    """Load skill prompt content and append JSON-only constraint."""
    content = Path(path).read_text(encoding="utf-8")
    content = content.rstrip()
    return f"{content}\n\n{OUTPUT_CONSTRAINT}\n"
