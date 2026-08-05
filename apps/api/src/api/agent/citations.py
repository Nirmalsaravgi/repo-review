"""Citation verification — a hard gate, per the plan.

Every `[[path:start-end]]` token in a draft answer is re-read from disk. Tokens
that don't resolve (missing file, out-of-range lines) are stripped from the text;
those that do become structured `Citation`s and render as `` `path:start-end` ``.
A hallucinated file path costs more trust than a slow answer.
"""

from __future__ import annotations

import re
from pathlib import Path

from api.agent.events import Citation
from api.agent.tools.base import ToolError
from api.agent.tools.filesystem import read_file

# [[path:start]] or [[path:start-end]] — path has no whitespace, brackets, or colons.
CITATION_RE = re.compile(r"\[\[\s*([^\[\]\s:]+):(\d+)(?:\s*-\s*(\d+))?\s*\]\]")
# Any leftover [[...]] the model wrote without a line range — unwrap to inline code.
LEFTOVER_RE = re.compile(r"\[\[\s*([^\[\]]+?)\s*\]\]")


def verify_citations(root: Path, text: str) -> tuple[str, list[Citation]]:
    """Return (cleaned_text, verified_citations)."""
    seen: dict[tuple[str, int, int], Citation] = {}

    def replace(match: re.Match[str]) -> str:
        path = match.group(1)
        start = int(match.group(2))
        end = int(match.group(3)) if match.group(3) else start
        if end < start:
            start, end = end, start
        try:
            result = read_file(root, path, start, end)
        except ToolError:
            return ""  # unresolved path — drop it
        if result.total_lines == 0 or start > result.total_lines:
            return ""  # line range doesn't exist — drop it
        end = min(end, result.total_lines)
        key = (result.path, start, end)
        if key not in seen:
            seen[key] = Citation(path=result.path, start_line=start, end_line=end)
        return f"`{result.path}:{start}-{end}`"

    cleaned = CITATION_RE.sub(replace, text)
    # A citation-shaped token without line numbers isn't verifiable — unwrap it to
    # inline code so no raw [[...]] ever reaches the user.
    cleaned = LEFTOVER_RE.sub(r"`\1`", cleaned)
    return cleaned, list(seen.values())
