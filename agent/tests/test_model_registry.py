"""Tests for the central model registry (context windows + pricing)."""

from app import model_registry


def test_known_model_context_window():
    assert model_registry.get_context_window("claude-sonnet-4-6") == 200_000
    assert model_registry.get_context_window("gpt-4o") == 128_000
    assert model_registry.get_context_window("gemini-1.5-pro") == 2_000_000


def test_dated_variant_resolves_via_substring():
    # A dated/suffixed model id still resolves to its base entry.
    assert model_registry.get_context_window("gpt-4o-2024-08-06") == 128_000
    assert model_registry.get_context_window("claude-sonnet-4-6-20260101") == 200_000


def test_longest_substring_wins():
    # "gpt-4o-mini" must not be shadowed by the shorter "gpt-4o" entry.
    assert model_registry.get_context_window("gpt-4o-mini") == 128_000
    assert model_registry.estimate_cost("gpt-4o-mini", 1_000_000, 0) == 0.15


def test_unknown_model_falls_back_to_default():
    assert model_registry.get_context_window("some-future-model") == model_registry.DEFAULT_CONTEXT_WINDOW
    assert model_registry.get_context_window("") == model_registry.DEFAULT_CONTEXT_WINDOW
    assert model_registry.is_known("some-future-model") is False


def test_estimate_cost():
    # gpt-4o: $2.50/1M in, $10.00/1M out
    cost = model_registry.estimate_cost("gpt-4o", 1_000_000, 1_000_000)
    assert abs(cost - 12.50) < 1e-9
    # Half a million input tokens only
    assert abs(model_registry.estimate_cost("gpt-4o", 500_000, 0) - 1.25) < 1e-9


def test_local_models_have_no_billing():
    # Open models have a context window but no price → cost is 0.
    assert model_registry.get_context_window("llama-3.1-70b") == 8_192
    assert model_registry.estimate_cost("llama-3.1-70b", 1_000_000, 1_000_000) == 0.0


def test_unknown_model_cost_is_zero():
    assert model_registry.estimate_cost("mystery-model", 1_000_000, 1_000_000) == 0.0
