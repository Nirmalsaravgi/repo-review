"""Extract call sites and imports from source via tree-sitter (Phase 3 C2).

Deterministic, DB-free companion to `extract.py`. Resolution (mapping a call
site to a defining symbol across files) is the worker's job — this module only
reports *what* is referenced and *where*, for both Python and the JS/TS family.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Node

from repo_parsing.extract import _parser_for, _text
from repo_parsing.languages import detect_language

_MAX_FILE_BYTES = 1_500_000
_MAX_CALLS_PER_FILE = 2_000
_MAX_IMPORTS_PER_FILE = 500


@dataclass(frozen=True, slots=True)
class CallSite:
    """A call expression: `receiver.name(...)` or `name(...)`."""

    name: str  # simple callee name (method/function)
    receiver: str | None  # object/module before the dot, if any
    line: int  # 1-based line of the call
    byte: int  # start byte of the call
    str_arg: str | None = None  # first string-literal argument, if any (for event/route edges)


@dataclass(frozen=True, slots=True)
class ImportRef:
    """One import: `module` plus the names bound locally."""

    module: str  # e.g. "os.path", "./utils", "react"
    names: tuple[tuple[str, str], ...]  # (imported_name, local_alias)
    is_relative: bool
    line: int


@dataclass(frozen=True, slots=True)
class ExtractedReferences:
    calls: list[CallSite] = field(default_factory=list)
    imports: list[ImportRef] = field(default_factory=list)


def extract_references(
    path: str,
    source: bytes | str | None = None,
    *,
    language: str | None = None,
) -> ExtractedReferences:
    """Extract call sites + imports. Never raises on parse failure."""
    lang = language or detect_language(path)
    if lang is None:
        return ExtractedReferences()
    if source is None:
        try:
            raw = Path(path).read_bytes()
        except OSError:
            return ExtractedReferences()
    elif isinstance(source, str):
        raw = source.encode("utf-8", errors="replace")
    else:
        raw = source
    if not raw or len(raw) > _MAX_FILE_BYTES:
        return ExtractedReferences()

    parser = _parser_for(lang)
    if parser is None:
        return ExtractedReferences()
    try:
        tree = parser.parse(raw)
    except Exception:
        return ExtractedReferences()
    root = tree.root_node
    if root is None:
        return ExtractedReferences()

    refs = ExtractedReferences()
    if lang == "python":
        _walk_python(root, raw, refs)
    else:
        _walk_js(root, raw, refs)
    return refs


# --------------------------------------------------------------------------- #
# Python
# --------------------------------------------------------------------------- #


def _first_string_arg(source: bytes, call_node: Node) -> str | None:
    """First string-literal argument of a call, unquoted. None if not a literal."""
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return None
    for child in args.named_children:
        if child.type in ("string", "template_string"):
            raw = _text(source, child).strip()
            # Strip surrounding quotes / backticks; ignore interpolated templates.
            if raw and raw[0] in "\"'`":
                inner = raw.strip("\"'`")
                return inner if inner else None
            return None
        # First positional arg is not a plain string → give up (keep it simple).
        return None
    return None


def _py_callee(source: bytes, fn: Node | None) -> tuple[str, str | None] | None:
    if fn is None:
        return None
    if fn.type == "identifier":
        return _text(source, fn), None
    if fn.type == "attribute":
        attr = fn.child_by_field_name("attribute")
        obj = fn.child_by_field_name("object")
        name = _text(source, attr)
        if not name:
            return None
        receiver = _text(source, obj) if obj is not None else None
        return name, receiver or None
    return None


def _walk_python(node: Node, source: bytes, refs: ExtractedReferences) -> None:
    t = node.type
    if t == "call" and len(refs.calls) < _MAX_CALLS_PER_FILE:
        callee = _py_callee(source, node.child_by_field_name("function"))
        if callee is not None:
            name, receiver = callee
            refs.calls.append(
                CallSite(
                    name=name,
                    receiver=receiver,
                    line=node.start_point[0] + 1,
                    byte=node.start_byte,
                    str_arg=_first_string_arg(source, node),
                )
            )
    elif t == "import_statement" and len(refs.imports) < _MAX_IMPORTS_PER_FILE:
        for child in node.named_children:
            _py_import_name(child, source, refs, module_from=None, relative=False)
    elif t == "import_from_statement" and len(refs.imports) < _MAX_IMPORTS_PER_FILE:
        module_node = node.child_by_field_name("module_name")
        module = _text(source, module_node) if module_node is not None else ""
        is_relative = module.startswith(".") or (
            module_node is not None and module_node.type == "relative_import"
        )
        names: list[tuple[str, str]] = []
        for child in node.named_children:
            if child == module_node:
                continue
            pair = _py_dotted_or_alias(child, source)
            if pair is not None:
                names.append(pair)
        refs.imports.append(
            ImportRef(
                module=module,
                names=tuple(names),
                is_relative=is_relative,
                line=node.start_point[0] + 1,
            )
        )
        return

    for child in node.children:
        _walk_python(child, source, refs)


def _py_dotted_or_alias(node: Node, source: bytes) -> tuple[str, str] | None:
    if node.type == "dotted_name" or node.type == "identifier":
        name = _text(source, node)
        return (name, name) if name else None
    if node.type == "aliased_import":
        name_node = node.child_by_field_name("name")
        alias_node = node.child_by_field_name("alias")
        name = _text(source, name_node)
        alias = _text(source, alias_node) or name
        return (name, alias) if name else None
    return None


def _py_import_name(
    node: Node, source: bytes, refs: ExtractedReferences, *, module_from: str | None, relative: bool
) -> None:
    # `import a.b.c` / `import a as b`
    if node.type == "dotted_name":
        module = _text(source, node)
        local = module.split(".")[0]
        refs.imports.append(
            ImportRef(
                module=module,
                names=((module, local),),
                is_relative=False,
                line=node.start_point[0] + 1,
            )
        )
    elif node.type == "aliased_import":
        name_node = node.child_by_field_name("name")
        alias_node = node.child_by_field_name("alias")
        module = _text(source, name_node)
        alias = _text(source, alias_node) or module
        if module:
            refs.imports.append(
                ImportRef(
                    module=module,
                    names=((module, alias),),
                    is_relative=False,
                    line=node.start_point[0] + 1,
                )
            )


# --------------------------------------------------------------------------- #
# JavaScript / TypeScript / TSX
# --------------------------------------------------------------------------- #


def _js_callee(source: bytes, fn: Node | None) -> tuple[str, str | None] | None:
    if fn is None:
        return None
    if fn.type == "identifier":
        return _text(source, fn), None
    if fn.type == "member_expression":
        prop = fn.child_by_field_name("property")
        obj = fn.child_by_field_name("object")
        name = _text(source, prop)
        if not name:
            return None
        receiver = _text(source, obj) if obj is not None else None
        return name, receiver or None
    return None


def _walk_js(node: Node, source: bytes, refs: ExtractedReferences) -> None:
    t = node.type
    if t == "call_expression" and len(refs.calls) < _MAX_CALLS_PER_FILE:
        callee = _js_callee(source, node.child_by_field_name("function"))
        if callee is not None:
            name, receiver = callee
            refs.calls.append(
                CallSite(
                    name=name,
                    receiver=receiver,
                    line=node.start_point[0] + 1,
                    byte=node.start_byte,
                    str_arg=_first_string_arg(source, node),
                )
            )
    elif t == "import_statement" and len(refs.imports) < _MAX_IMPORTS_PER_FILE:
        _js_import(node, source, refs)
        return

    for child in node.children:
        _walk_js(child, source, refs)


def _js_import(node: Node, source: bytes, refs: ExtractedReferences) -> None:
    source_node = node.child_by_field_name("source")
    module = _text(source, source_node).strip("\"'`") if source_node is not None else ""
    if not module:
        return
    is_relative = module.startswith(".")
    names: list[tuple[str, str]] = []
    for clause in node.named_children:
        if clause.type != "import_clause":
            continue
        for spec in clause.named_children:
            if spec.type == "identifier":  # default import
                nm = _text(source, spec)
                if nm:
                    names.append(("default", nm))
            elif spec.type == "namespace_import":
                nm = _text(source, spec.named_children[-1]) if spec.named_children else ""
                if nm:
                    names.append(("*", nm))
            elif spec.type == "named_imports":
                for imp in spec.named_children:
                    if imp.type != "import_specifier":
                        continue
                    name_node = imp.child_by_field_name("name")
                    alias_node = imp.child_by_field_name("alias")
                    nm = _text(source, name_node)
                    alias = _text(source, alias_node) or nm
                    if nm:
                        names.append((nm, alias))
    refs.imports.append(
        ImportRef(
            module=module,
            names=tuple(names),
            is_relative=is_relative,
            line=node.start_point[0] + 1,
        )
    )
