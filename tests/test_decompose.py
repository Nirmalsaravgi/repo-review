"""Graph decomposition: cohesion clusters and cross-folder features."""

from __future__ import annotations

from repo_parsing.decompose import (
    CapabilitySeed,
    FileEdge,
    FileUnit,
    GraphInput,
    SymbolLink,
    SymbolRef,
    apply_labels,
    decompose,
)


def _billing_graph() -> GraphInput:
    files = [
        FileUnit("apps/api/routes/invoices.py", "f1", ["s1"], 1),
        FileUnit("apps/api/services/billing.py", "f2", ["s2"], 1),
        FileUnit("apps/api/models/invoice.py", "f3", ["s3"], 1),
        FileUnit("apps/api/notifications/email.py", "f4", ["s4"], 1),
        FileUnit("apps/api/routes/auth.py", "f5", ["s5"], 1),
        FileUnit("apps/api/services/auth.py", "f6", ["s6"], 1),
        FileUnit("apps/api/models/user.py", "f7", ["s7"], 1),
    ]
    edges = [
        FileEdge("apps/api/routes/invoices.py", "apps/api/services/billing.py", 8),
        FileEdge("apps/api/services/billing.py", "apps/api/models/invoice.py", 8),
        FileEdge("apps/api/services/billing.py", "apps/api/notifications/email.py", 2),
        FileEdge("apps/api/routes/auth.py", "apps/api/services/auth.py", 8),
        FileEdge("apps/api/services/auth.py", "apps/api/models/user.py", 8),
        FileEdge("apps/api/routes/invoices.py", "apps/api/routes/auth.py", 1),
    ]
    symbols = [
        SymbolRef("s1", "approve_invoice", "apps/api/routes/invoices.py"),
        SymbolRef("s2", "charge", "apps/api/services/billing.py"),
        SymbolRef("s3", "save_invoice", "apps/api/models/invoice.py"),
        SymbolRef("s4", "send_email", "apps/api/notifications/email.py"),
        SymbolRef("s5", "login", "apps/api/routes/auth.py"),
        SymbolRef("s6", "check_user", "apps/api/services/auth.py"),
        SymbolRef("s7", "save_user", "apps/api/models/user.py"),
    ]
    links = [
        SymbolLink("s1", "s2", "charge", "calls", 0.9),
        SymbolLink("s2", "s3", "save_invoice", "calls", 0.9),
        SymbolLink("s2", "s4", "send_email", "calls", 0.8),
        SymbolLink("s5", "s6", "check_user", "calls", 0.9),
        SymbolLink("s6", "s7", "save_user", "calls", 0.9),
    ]
    seeds = [
        CapabilitySeed(
            title="POST /invoices/{id}/approve",
            kind="http",
            symbol_id="s1",
            file_path="apps/api/routes/invoices.py",
            name="approve_invoice",
        )
    ]
    return GraphInput(files=files, edges=edges, symbols=symbols, symbol_links=links, seeds=seeds)


def test_modules_follow_responsibility_not_folders() -> None:
    result = decompose(_billing_graph())
    again = decompose(_billing_graph())
    leaves = [m for m in result.modules if m.kind == "module"]
    by_name = {m.name: m.files for m in leaves}
    assert by_name["Billing"] == [
        "apps/api/models/invoice.py",
        "apps/api/routes/invoices.py",
        "apps/api/services/billing.py",
    ]
    assert by_name["Auth"] == [
        "apps/api/models/user.py",
        "apps/api/routes/auth.py",
        "apps/api/services/auth.py",
    ]
    assert "Routes" not in by_name
    assert "Services" not in by_name
    assert "Models" not in by_name
    billing = next(m for m in leaves if m.name == "Billing")
    assert billing.cohesion > billing.coupling
    assert billing.cohesion > 0.7
    assert billing.evidence["method"] == "dependency_cluster"
    assert billing.evidence["edges"]
    assert all(m.evidence.get("summary") is None for m in result.modules)
    assert [m.key for m in result.modules] == [m.key for m in again.modules]
    assert [m.files for m in result.modules] == [m.files for m in again.modules]


def test_cross_folder_feature_has_entry_and_evidence() -> None:
    result = decompose(_billing_graph())
    feature = next(f for f in result.features if f.name.startswith("POST /invoices"))
    assert feature.entry_point_symbol_ids == ["s1"]
    assert feature.evidence["path"][:3] == ["approve_invoice", "charge", "save_invoice"]
    assert "send_email" in feature.evidence["path"]
    assert len(feature.module_keys) >= 2
    folders = set(feature.evidence["folders"])
    assert "apps/api/routes" in folders
    assert "apps/api/services" in folders
    assert "apps/api/notifications" in folders
    assert feature.evidence["resolution"] == "call_graph"
    assert feature.confidence > 0


def test_edgeless_repo_falls_back_to_package_boundaries() -> None:
    graph = GraphInput(
        files=[
            FileUnit("apps/api/routes/orders.py", "a"),
            FileUnit("apps/api/services/orders.py", "b"),
            FileUnit("apps/web/src/app/page.tsx", "c"),
        ]
    )
    result = decompose(graph)
    leaves = [m for m in result.modules if m.kind == "module"]
    groups = [set(m.files) for m in leaves]
    assert {"apps/api/routes/orders.py", "apps/api/services/orders.py"} in groups
    assert {"apps/web/src/app/page.tsx"} in groups
    assert all(m.evidence["method"] == "structural_fallback" for m in leaves)
    assert result.features == []
    empty = decompose(GraphInput())
    assert empty.features == []


def test_large_cluster_splits_on_a_bridge() -> None:
    files = []
    edges = []
    for i in range(6):
        files.append(FileUnit(f"billing/a{i}.py", f"a{i}"))
        files.append(FileUnit(f"notify/b{i}.py", f"b{i}"))
    for i in range(6):
        for j in range(i + 1, 6):
            edges.append(FileEdge(f"billing/a{i}.py", f"billing/a{j}.py", 5))
            edges.append(FileEdge(f"notify/b{i}.py", f"notify/b{j}.py", 5))
    edges.append(FileEdge("billing/a0.py", "notify/b0.py", 3))
    result = decompose(GraphInput(files=files, edges=edges))
    leaves = [m for m in result.modules if m.kind == "module"]
    assert len(leaves) == 2
    assert {len(m.files) for m in leaves} == {6}


def test_labels_require_member_evidence() -> None:
    result = decompose(_billing_graph())
    billing = next(m for m in result.modules if m.name == "Billing")
    feature = result.features[0]
    applied = apply_labels(
        result.modules,
        result.features,
        {
            "modules": [
                {"key": billing.key, "name": "Invoice billing", "evidence_paths": ["apps/api/services/billing.py"]},
                {"key": billing.key, "name": "Invented", "evidence_paths": ["apps/secret/nope.py"]},
            ],
            "features": [
                {
                    "key": feature.key,
                    "name": "Approve invoice",
                    "evidence_paths": ["nope.py"],
                }
            ],
        },
    )
    assert applied == 1
    assert billing.name == "Invoice billing"
    assert feature.name != "Approve invoice"
    assert billing.evidence["name_source"] == "llm"
