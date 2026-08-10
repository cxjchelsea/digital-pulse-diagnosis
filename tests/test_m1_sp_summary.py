"""Semantic fingerprint v2 coverage and provenance exclusions."""

from __future__ import annotations

from dataclasses import fields, replace

import numpy as np
import pytest

from digital_pulse.m1_sp import (
    SP_RESULT_EXCLUDED_FIELDS,
    SP_RESULT_FINGERPRINT_VERSION,
    SP_RESULT_SEMANTIC_FIELDS,
    ProcessingEvidence,
    SPProcessingProvenance,
    SPProcessingResult,
    SPProcessor,
    compare_sp_results,
    sp_result_fingerprint,
    sp_result_sha256,
)

from _m1_sp_helpers import FIXED_SHA, record_scenario


@pytest.fixture(scope="module")
def normal_result():
    temporary, _, session, samples = record_scenario(
        "normal_high_quality", duration_s=8.0, random_seed=1001
    )
    try:
        yield SPProcessor().process(
            session, samples, provenance=SPProcessingProvenance(FIXED_SHA)
        )
    finally:
        temporary.cleanup()


def _rehash(result: SPProcessingResult) -> SPProcessingResult:
    return replace(result, result_sha256=sp_result_sha256(result))


def _assert_semantic_change(original: SPProcessingResult, changed: SPProcessingResult) -> None:
    changed = _rehash(changed)
    assert changed.result_sha256 != original.result_sha256
    assert compare_sp_results(original, changed) is False


def test_fingerprint_field_inventory_is_explicit_and_complete():
    model_fields = {item.name for item in fields(SPProcessingResult)}
    assert SP_RESULT_SEMANTIC_FIELDS.isdisjoint(SP_RESULT_EXCLUDED_FIELDS)
    assert SP_RESULT_SEMANTIC_FIELDS | SP_RESULT_EXCLUDED_FIELDS == model_fields


def test_fingerprint_contains_full_semantic_sections(normal_result):
    fingerprint = sp_result_fingerprint(normal_result)
    stage = fingerprint["stage_result"]
    assert fingerprint["fingerprint_version"] == SP_RESULT_FINGERPRINT_VERSION
    assert "integrity" in stage["preprocessing"]
    assert stage["filter_views_by_window"]
    assert stage["beats_by_window"]
    assert stage["reference_by_window"]
    causal = next(iter(stage["filter_views_by_window"].values()))["causal"]
    assert set(causal["values"]) == {"dtype", "shape", "sha256"}
    assert set(causal["valid_mask"]) == {"dtype", "shape", "sha256"}


def test_integrity_mutation_changes_hash_and_comparison(normal_result):
    integrity = replace(normal_result.integrity, missing_frame_count=1)
    preprocessing = replace(normal_result.stage_result.preprocessing, integrity=integrity)
    changed = replace(
        normal_result,
        stage_result=replace(normal_result.stage_result, preprocessing=preprocessing),
    )
    _assert_semantic_change(normal_result, changed)


def test_beat_removal_changes_hash_and_comparison(normal_result):
    changed = replace(
        normal_result,
        stage_result=replace(normal_result.stage_result, beats_by_window={}),
    )
    _assert_semantic_change(normal_result, changed)


def test_peak_device_time_mutation_changes_hash_and_comparison(normal_result):
    window_id, analysis = next(iter(normal_result.beats_by_window.items()))
    first = analysis.candidates[0]
    candidates = (replace(first, peak_device_time_us=first.peak_device_time_us + 1), *analysis.candidates[1:])
    changed_analysis = replace(analysis, candidates=candidates)
    changed_beats = dict(normal_result.beats_by_window)
    changed_beats[window_id] = changed_analysis
    changed = replace(
        normal_result,
        stage_result=replace(normal_result.stage_result, beats_by_window=changed_beats),
    )
    _assert_semantic_change(normal_result, changed)


def test_reference_removal_changes_hash_and_comparison(normal_result):
    changed = replace(
        normal_result,
        stage_result=replace(normal_result.stage_result, reference_by_window={}),
    )
    _assert_semantic_change(normal_result, changed)


def test_reference_pair_mutation_changes_hash_and_comparison(normal_result):
    window_id, reference = next(iter(normal_result.reference_by_window.items()))
    pulse_index, ppg_index, lag_ms = reference.matched_pairs[0]
    pairs = ((pulse_index, ppg_index + 1, lag_ms), *reference.matched_pairs[1:])
    changed_references = dict(normal_result.reference_by_window)
    changed_references[window_id] = replace(reference, matched_pairs=pairs)
    changed = replace(
        normal_result,
        stage_result=replace(normal_result.stage_result, reference_by_window=changed_references),
    )
    _assert_semantic_change(normal_result, changed)


def test_filter_value_mutation_changes_hash_and_comparison(normal_result):
    window_id, views = next(iter(normal_result.filter_views_by_window.items()))
    offline = views["offline_review"]
    values = np.array(offline.values, copy=True)
    values[0] += 1.0
    changed_views = dict(views)
    changed_views["offline_review"] = replace(offline, values=values)
    changed_filters = dict(normal_result.filter_views_by_window)
    changed_filters[window_id] = changed_views
    changed = replace(
        normal_result,
        stage_result=replace(normal_result.stage_result, filter_views_by_window=changed_filters),
    )
    _assert_semantic_change(normal_result, changed)


def test_software_sha_and_container_identity_are_excluded(normal_result):
    changed = replace(
        normal_result,
        software_commit_sha="d" * 40,
        session_id="different-container-id",
    )
    assert sp_result_sha256(changed) == normal_result.result_sha256
    assert compare_sp_results(normal_result, changed) is True


def test_non_finite_scalar_semantics_fail_canonical_json(normal_result):
    integrity = replace(
        normal_result.integrity,
        evidence=(ProcessingEvidence(code="NON_FINITE", severity="error", observed_value=float("nan")),),
    )
    preprocessing = replace(normal_result.stage_result.preprocessing, integrity=integrity)
    changed = replace(
        normal_result,
        stage_result=replace(normal_result.stage_result, preprocessing=preprocessing),
    )
    with pytest.raises(ValueError):
        sp_result_sha256(changed)
