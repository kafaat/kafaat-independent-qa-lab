"""Reviewed offline acceptance identities, independent of test discovery."""

EXPECTED_UNIT_IDS = frozenset(
    'test_guard.GuardTests.' + name
    for name in (
        'test_real_three_samples',
        'test_identity_mismatch',
        'test_empty_identity',
        'test_window_grid_mismatch',
        'test_joint_window_metadata_lie',
        'test_full_grid_mismatch',
        'test_shape_mismatch',
        'test_missing_scale_rejected',
        'test_nonfinite_offset_rejected',
        'test_unknown_flag_is_not_default_false',
        'test_all_nodata_has_null_statistics',
        'test_nan_and_nodata_excluded_independently',
        'test_negative_and_zero_denominator_not_clamped',
        'test_scl_alignment_rejected',
        'test_scl_full_transform_mismatch',
        'test_scl_source_window_mismatch',
        'test_input_changed_during_run_rejected',
        'test_asset_crs_conflicts_rejected',
        'test_matching_asset_crs_overrides_accepted',
        'test_malformed_asset_crs_overrides_rejected',
        'test_huge_integer_encoding_fields_rejected',
        'test_decoded_reflectance_overflow_rejected',
        'test_ndvi_arithmetic_overflow_rejected',
        'test_finite_ndvi_ratio_preserved',
        'test_empty_decoded_support_cannot_infer_equivalence',
        'test_cli_numeric_rejection_is_structured',
    )
)

EXPECTED_INDEPENDENT_IDS = frozenset((
    'scl_asset_transform_mismatch', 'scl_source_window_mismatch',
    'nonfinite_raw', 'zero_denominator', 'product_mismatch', 'grid_mismatch',
    'missing_offset', 'shape_mismatch', 'unknown_flag', 'all_invalid',
    'nodata_zero_without_mask', 'scl_shift', 'scl_url_mismatch',
    'quality_flags_no_clamp', 'real_yemen', 'real_yemen_march_selected',
    'real_yemen_december_fixed_window', 'source_and_inputs_unchanged',
))

REQUIRED_FILES = frozenset((
    'acceptance_contract.py', 'run_checks.py', 'run_unit_checks.py',
    'test_run_checks.py', 'requirements.txt', 'encoding_guard/guard.py',
    'encoding_guard/test_guard.py', 'encoding_guard_review/review.py',
))
