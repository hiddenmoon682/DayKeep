from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"


def test_readme_documents_key_facts():
    text = README.read_text(encoding="utf-8")
    assert "uv run daykeep" in text
    assert "~/DayKeep" in text
    assert "api_key" in text
    assert "第三方" in text
    assert "被覆盖" in text
    assert "budget" in text
