"""Phase 4 B1 — in-repo config parser (untrusted, safe defaults)."""

from __future__ import annotations

from api.bot.config import CHECK_NAMES, ReviewConfig, parse_review_config
from repo_core.models import TENANT_TABLES


def test_pr_reviews_in_tenant_tables() -> None:
    assert "pr_reviews" in TENANT_TABLES


def test_defaults_when_no_file() -> None:
    cfg = parse_review_config(None)
    assert cfg.enabled is True
    assert cfg.min_confidence == 0.75
    assert cfg.max_comments == 3
    assert all(cfg.checks[name] for name in CHECK_NAMES)
    assert cfg.check_enabled("duplicate") is True


def test_server_defaults_seed_thresholds() -> None:
    cfg = parse_review_config(None, default_min_confidence=0.9, default_max_comments=1)
    assert cfg.min_confidence == 0.9
    assert cfg.max_comments == 1


def test_overrides_and_check_toggles() -> None:
    cfg = parse_review_config(
        """
        enabled: true
        min_confidence: 0.6
        max_comments: 5
        checks:
          duplicate: false
          pattern_consistency: off
        """
    )
    assert cfg.min_confidence == 0.6
    assert cfg.max_comments == 5
    assert cfg.check_enabled("duplicate") is False
    assert cfg.check_enabled("pattern_consistency") is False
    assert cfg.check_enabled("blast_radius") is True


def test_master_switch_disables_all_checks() -> None:
    cfg = parse_review_config("enabled: false")
    assert cfg.check_enabled("blast_radius") is False


def test_malformed_file_is_defaults() -> None:
    for bad in (": : :\n  bad", "- just\n- a\n- list", "42", "!!python/object/apply:os.system []"):
        cfg = parse_review_config(bad)
        assert isinstance(cfg, ReviewConfig)
        assert cfg.max_comments == 3  # fell back to defaults, did not raise


def test_out_of_range_values_are_clamped() -> None:
    cfg = parse_review_config("min_confidence: 9\nmax_comments: 999")
    assert cfg.min_confidence == 1.0
    assert cfg.max_comments == 20


def test_unknown_keys_ignored() -> None:
    cfg = parse_review_config("post_everything: true\nchecks:\n  made_up: true")
    assert cfg.max_comments == 3
    assert "made_up" not in cfg.checks
