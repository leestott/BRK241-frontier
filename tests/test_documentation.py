"""Public documentation references and generated walkthrough contracts."""
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def test_contributor_workflow_is_explicitly_offline_and_checks_dependencies():
    text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert '-m pip install -e . --no-deps' in text
    assert '-m pip check' in text
    assert '$env:FIBREOPS_AGENT_BACKEND = "local"' in text
    assert r'node --test tests\test_voice_client.cjs' in text
    assert 'Do not install the preview publisher SDK into `.venv`' in text
    assert 'Azure CLI, Docker and Azure' in text


def test_public_markdown_links_resolve():
    documents = list(ROOT.glob("*.md")) + list((ROOT / "docs").rglob("*.md"))
    documents += list((ROOT / ".github" / "skills").rglob("SKILL.md"))
    failures = []
    for document in documents:
        for target in re.findall(r"!?\[[^\]]*\]\(([^)\s]+)\)", document.read_text(encoding="utf-8")):
            parsed = urlsplit(target.strip("<>"))
            if parsed.scheme or not parsed.path:
                continue
            path = (document.parent / unquote(parsed.path)).resolve()
            if not path.exists():
                failures.append(f"{document.relative_to(ROOT)}: {target}")
    assert not failures, "\n".join(failures)


def test_generated_walkthrough_and_skill_are_discoverable():
    html = (ROOT / "docs" / "services-architecture.html").read_text(encoding="utf-8")
    assert "__NODE_DATA__" not in html
    nodes = json.loads(re.search(r"const nodes = (\{[^\n]+\});", html)[1])
    assert set(nodes) == {str(number) for number in range(1, 15)}
    assert 'prefers-reduced-motion: reduce' in html
    assert 'aria-live="polite"' in html
    assert 'images/services-architecture.png' in html
    assert 'not live telemetry' in html
    skill = ROOT / ".github" / "skills" / "foundry-voice-preview-evaluation" / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    frontmatter = text.split("---", 2)[1]
    assert f"name: {skill.parent.name}" in frontmatter
    assert re.search(r"^description: '.+'$", frontmatter, re.MULTILINE)
    assert "python -m scripts.evaluate_voice_responses" in text
