"""Extract symbols from source via tree-sitter.

Uses tree-sitter-language-pack for prebuilt grammars. Extraction is a structured
walk (not only flat queries) so methods nest under classes with parent links.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from tree_sitter import Node, Parser

from repo_parsing.languages import detect_language

# Caps keep a single huge generated file from blowing memory / DB rows.
_MAX_FILE_BYTES = 1_500_000
_MAX_SYMBOLS_PER_FILE = 500
_MAX_DOCSTRING_CHARS = 2_000
_MAX_SIGNATURE_CHARS = 500


@dataclass(frozen=True, slots=True)
class ExtractedSymbol:
    """One symbol relative to a single file (parent via local index)."""

    name: str
    qualified_name: str
    kind: str  # function|class|method|interface|const|import
    signature: str | None
    docstring: str | None
    start_line: int  # 1-based
    end_line: int
    start_byte: int
    end_byte: int
    parent_index: int | None  # index into the returned list


@lru_cache(maxsize=16)
def _parser_for(language: str) -> Parser | None:
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        return None
    try:
        return get_parser(language)
    except Exception:
        return None


def extract_file(path: str | Path, source: bytes | str | None = None) -> list[ExtractedSymbol]:
    """Parse a file by path. Reads from disk when `source` is omitted."""
    p = Path(path)
    lang = detect_language(p.as_posix())
    if lang is None:
        return []
    if source is None:
        try:
            raw = p.read_bytes()
        except OSError:
            return []
    elif isinstance(source, str):
        raw = source.encode("utf-8", errors="replace")
    else:
        raw = source
    return extract_symbols(p.as_posix(), raw, language=lang)


def extract_symbols(
    path: str,
    source: bytes,
    *,
    language: str | None = None,
) -> list[ExtractedSymbol]:
    """Extract symbols from in-memory source. Never raises on parse failure."""
    lang = language or detect_language(path)
    if lang is None or not source or len(source) > _MAX_FILE_BYTES:
        return []
    parser = _parser_for(lang)
    if parser is None:
        return []
    try:
        tree = parser.parse(source)
    except Exception:
        return []
    root = tree.root_node
    if root is None:
        return []

    out: list[ExtractedSymbol] = []
    if lang == "python":
        _walk_python(root, source, out, parent_index=None, scope="")
    else:
        _walk_js_family(root, source, out, parent_index=None, scope="", language=lang)
    return out[:_MAX_SYMBOLS_PER_FILE]


def _text(source: bytes, node: Node | None) -> str:
    if node is None:
        return ""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _lines(node: Node) -> tuple[int, int]:
    # tree-sitter points are 0-based; product citations are 1-based.
    return node.start_point[0] + 1, node.end_point[0] + 1


def _clip(text: str | None, limit: int) -> str | None:
    if not text:
        return None
    text = text.strip()
    if not text:
        return None
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _append(
    out: list[ExtractedSymbol],
    *,
    name: str,
    kind: str,
    signature: str | None,
    docstring: str | None,
    node: Node,
    parent_index: int | None,
    scope: str,
) -> int:
    qn = f"{scope}.{name}" if scope else name
    start_line, end_line = _lines(node)
    out.append(
        ExtractedSymbol(
            name=name,
            qualified_name=qn,
            kind=kind,
            signature=_clip(signature, _MAX_SIGNATURE_CHARS),
            docstring=_clip(docstring, _MAX_DOCSTRING_CHARS),
            start_line=start_line,
            end_line=end_line,
            start_byte=node.start_byte,
            end_byte=node.end_byte,
            parent_index=parent_index,
        )
    )
    return len(out) - 1


# --------------------------------------------------------------------------- #
# Python
# --------------------------------------------------------------------------- #


def _python_docstring(source: bytes, body: Node | None) -> str | None:
    if body is None or body.type != "block" or not body.children:
        return None
    first = body.children[0]
    # Skip leading comments / newlines — expression_statement with string.
    for child in body.children:
        if child.type in ("comment", "\n"):
            continue
        if child.type == "expression_statement" and child.child_count:
            expr = child.children[0]
            if expr.type in ("string", "concatenated_string"):
                raw = _text(source, expr)
                return raw.strip().strip("\"'") if raw else None
            break
        break
    _ = first
    return None


def _walk_python(
    node: Node,
    source: bytes,
    out: list[ExtractedSymbol],
    *,
    parent_index: int | None,
    scope: str,
) -> None:
    if len(out) >= _MAX_SYMBOLS_PER_FILE:
        return

    t = node.type

    if t == "function_definition":
        name_node = node.child_by_field_name("name")
        name = _text(source, name_node) or "<anonymous>"
        params = _text(source, node.child_by_field_name("parameters"))
        ret = node.child_by_field_name("return_type")
        sig = f"def {name}{params}"
        if ret is not None:
            sig += f" -> {_text(source, ret)}"
        kind = "method" if parent_index is not None else "function"
        body = node.child_by_field_name("body")
        idx = _append(
            out,
            name=name,
            kind=kind,
            signature=sig,
            docstring=_python_docstring(source, body),
            node=node,
            parent_index=parent_index,
            scope=scope,
        )
        if body is not None:
            for child in body.children:
                _walk_python(child, source, out, parent_index=idx, scope=f"{scope}.{name}" if scope else name)
        return

    if t == "class_definition":
        name_node = node.child_by_field_name("name")
        name = _text(source, name_node) or "<anonymous>"
        supers = node.child_by_field_name("superclasses")
        sig = f"class {name}{_text(source, supers)}" if supers else f"class {name}"
        body = node.child_by_field_name("body")
        idx = _append(
            out,
            name=name,
            kind="class",
            signature=sig,
            docstring=_python_docstring(source, body),
            node=node,
            parent_index=parent_index,
            scope=scope,
        )
        if body is not None:
            for child in body.children:
                _walk_python(child, source, out, parent_index=idx, scope=f"{scope}.{name}" if scope else name)
        return

    if t == "decorated_definition":
        # Recurse into the definition; keep decorator text on the inner symbol via walk.
        for child in node.children:
            if child.type in ("function_definition", "class_definition", "decorated_definition"):
                _walk_python(child, source, out, parent_index=parent_index, scope=scope)
        return

    if t in ("import_statement", "import_from_statement"):
        text = _text(source, node).strip()
        # Prefer the first imported name for the symbol row.
        name = text
        for child in node.children:
            if child.type in ("dotted_name", "aliased_import", "relative_import", "identifier"):
                name = _text(source, child) or name
                break
        _append(
            out,
            name=name.split()[0] if name else "import",
            kind="import",
            signature=text,
            docstring=None,
            node=node,
            parent_index=parent_index,
            scope=scope,
        )
        return

    for child in node.children:
        _walk_python(child, source, out, parent_index=parent_index, scope=scope)


# --------------------------------------------------------------------------- #
# JavaScript / TypeScript / TSX
# --------------------------------------------------------------------------- #


def _js_doc_before(source: bytes, node: Node) -> str | None:
    prev = node.prev_named_sibling
    if prev is not None and prev.type == "comment":
        text = _text(source, prev).strip()
        if text.startswith("/**") or text.startswith("/*"):
            return text
    return None


def _walk_js_family(
    node: Node,
    source: bytes,
    out: list[ExtractedSymbol],
    *,
    parent_index: int | None,
    scope: str,
    language: str,
) -> None:
    if len(out) >= _MAX_SYMBOLS_PER_FILE:
        return

    t = node.type

    if t in ("function_declaration", "generator_function_declaration"):
        name_node = node.child_by_field_name("name")
        name = _text(source, name_node) or "<anonymous>"
        params = _text(source, node.child_by_field_name("parameters"))
        kind = "method" if parent_index is not None else "function"
        idx = _append(
            out,
            name=name,
            kind=kind,
            signature=f"function {name}{params}",
            docstring=_js_doc_before(source, node),
            node=node,
            parent_index=parent_index,
            scope=scope,
        )
        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.children:
                _walk_js_family(
                    child, source, out, parent_index=idx, scope=f"{scope}.{name}" if scope else name, language=language
                )
        return

    if t in ("class_declaration", "abstract_class_declaration"):
        name_node = node.child_by_field_name("name")
        name = _text(source, name_node) or "<anonymous>"
        sig = f"class {name}"
        idx = _append(
            out,
            name=name,
            kind="class",
            signature=sig,
            docstring=_js_doc_before(source, node),
            node=node,
            parent_index=parent_index,
            scope=scope,
        )
        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.children:
                _walk_js_family(
                    child, source, out, parent_index=idx, scope=f"{scope}.{name}" if scope else name, language=language
                )
        return

    if t == "method_definition":
        name_node = node.child_by_field_name("name")
        name = _text(source, name_node) or "<anonymous>"
        params = _text(source, node.child_by_field_name("parameters"))
        idx = _append(
            out,
            name=name,
            kind="method",
            signature=f"{name}{params}",
            docstring=_js_doc_before(source, node),
            node=node,
            parent_index=parent_index,
            scope=scope,
        )
        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.children:
                _walk_js_family(
                    child, source, out, parent_index=idx, scope=f"{scope}.{name}" if scope else name, language=language
                )
        return

    if t in ("interface_declaration", "type_alias_declaration"):
        name_node = node.child_by_field_name("name")
        name = _text(source, name_node) or "<anonymous>"
        kind = "interface" if t == "interface_declaration" else "const"
        _append(
            out,
            name=name,
            kind=kind,
            signature=_clip(_text(source, node).split("\n")[0], _MAX_SIGNATURE_CHARS),
            docstring=_js_doc_before(source, node),
            node=node,
            parent_index=parent_index,
            scope=scope,
        )
        return

    if t == "lexical_declaration":
        # export const foo = () => {} / const Foo = class ...
        for child in node.children:
            if child.type != "variable_declarator":
                continue
            name_node = child.child_by_field_name("name")
            value = child.child_by_field_name("value")
            name = _text(source, name_node)
            if not name or value is None:
                continue
            if value.type in (
                "arrow_function",
                "function",
                "function_expression",
                "generator_function",
                "class",
            ):
                kind = "class" if value.type == "class" else "function"
                if parent_index is not None and kind == "function":
                    kind = "method"
                params = ""
                if value.type != "class":
                    params = _text(source, value.child_by_field_name("parameters"))
                idx = _append(
                    out,
                    name=name,
                    kind=kind,
                    signature=f"const {name}{params}" if kind != "class" else f"const {name} = class",
                    docstring=_js_doc_before(source, node),
                    node=node,
                    parent_index=parent_index,
                    scope=scope,
                )
                body = value.child_by_field_name("body")
                if body is not None and kind == "class":
                    for c in body.children:
                        _walk_js_family(
                            c,
                            source,
                            out,
                            parent_index=idx,
                            scope=f"{scope}.{name}" if scope else name,
                            language=language,
                        )
        # still walk children for nested oddities? skip — declarators handled.
        return

    if t in ("import_statement",):
        text = _text(source, node).strip()
        name = text
        for child in node.children:
            if child.type in ("string", "identifier"):
                name = _text(source, child).strip("\"'") or name
        _append(
            out,
            name=name[:200] if name else "import",
            kind="import",
            signature=text,
            docstring=None,
            node=node,
            parent_index=parent_index,
            scope=scope,
        )
        return

    if t == "export_statement":
        for child in node.children:
            _walk_js_family(child, source, out, parent_index=parent_index, scope=scope, language=language)
        return

    for child in node.children:
        _walk_js_family(child, source, out, parent_index=parent_index, scope=scope, language=language)
