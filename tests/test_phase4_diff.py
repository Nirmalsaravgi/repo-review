"""Phase 4 B2 — unified-diff patch parsing + symbol mapping (pure)."""

from __future__ import annotations

from api.bot.diff import (
    SymbolSpan,
    changed_symbols,
    parse_patch,
    parse_pr_files,
)

_PATCH = """@@ -1,4 +1,6 @@
 import os
+import sys
 def existing():
-    return 1
+    return 2
+    # trailing
 def tail():
"""


def test_parse_patch_extracts_added_lines_with_numbers() -> None:
    fd = parse_patch("app.py", "modified", _PATCH)
    nums = {a.line: a.text for a in fd.added}
    # new file: 1 import os, 2 import sys(+), 3 def existing, 4 return 2(+), 5 # trailing(+), 6 def tail
    assert 2 in nums and nums[2] == "import sys"
    assert 4 in nums and nums[4].strip() == "return 2"
    assert 5 in nums
    assert fd.added_line_numbers == {2, 4, 5}


def test_touches_signature_true_when_def_added() -> None:
    fd = parse_patch("m.py", "added", "@@ -0,0 +1,2 @@\n+def brand_new():\n+    pass\n")
    assert fd.touches_signature is True


def test_touches_signature_false_for_body_only() -> None:
    fd = parse_patch("m.py", "modified", "@@ -1,1 +1,2 @@\n x = 1\n+    y = 2\n")
    assert fd.touches_signature is False


def test_binary_or_missing_patch_is_empty() -> None:
    fd = parse_patch("logo.png", "added", None)
    assert fd.added == []
    assert fd.touches_signature is False


def test_parse_pr_files_skips_pathless() -> None:
    diffs = parse_pr_files(
        [
            {"filename": "a.py", "status": "modified", "patch": "@@ -1 +1,2 @@\n x\n+y\n"},
            {"status": "modified", "patch": "ignored"},
        ]
    )
    assert [d.path for d in diffs] == ["a.py"]


def test_changed_symbols_maps_ranges_and_flags_signature() -> None:
    fd = parse_patch(
        "svc.py",
        "modified",
        "@@ -10,2 +10,3 @@\n def charge(x):\n+    audit(x)\n     return x\n",
    )
    # added line is 11 (audit) inside charge's span; def line 10 not added -> body change
    symbols = [
        SymbolSpan("s1", "charge", "function", "svc.py", start_line=10, end_line=12),
        SymbolSpan("s2", "other", "function", "svc.py", start_line=30, end_line=40),
    ]
    changed = changed_symbols([fd], symbols)
    assert len(changed) == 1
    assert changed[0].name == "charge"
    assert changed[0].is_signature_change is False


def test_changed_symbols_signature_change_when_def_line_added() -> None:
    fd = parse_patch(
        "svc.py",
        "modified",
        "@@ -9,3 +9,3 @@\n x = 1\n-def charge(x):\n+def charge(x, y):\n     return x\n",
    )
    # new def line is at 10 (added)
    symbols = [SymbolSpan("s1", "charge", "function", "svc.py", start_line=10, end_line=12)]
    changed = changed_symbols([fd], symbols)
    assert changed[0].is_signature_change is True


def test_changed_symbols_path_normalization() -> None:
    fd = parse_patch("./pkg/svc.py", "modified", "@@ -1 +1,2 @@\n a\n+b\n")
    symbols = [SymbolSpan("s1", "f", "function", "pkg\\svc.py", start_line=1, end_line=5)]
    assert len(changed_symbols([fd], symbols)) == 1
