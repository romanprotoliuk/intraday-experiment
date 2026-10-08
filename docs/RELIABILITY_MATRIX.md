# Deterministic reliability matrix

All cases below are synthetic, isolated failure models exercised in the complete suite. They do not claim deliberate live broker faults or physical Mac sleep. The LaunchAgent row models its audited lifecycle; actual service ownership is separately checked read-only. Terminal/Codex independence is a launchd process-tree property, not simulated financial execution. Production incidents remain in private reports, not fixtures.

| # | Scenario | Executed test family |
|---|---|---|
| 1 | Clean startup | `tests/test_oauth_refresh.py::test_valid_token_no_refresh_and_two_fresh_instances` (1 cases) |
| 2 | Worker crash premarket | `tests/test_system_recovery.py::test_synthetic_transport_restart_opening_sequence` (1 cases) |
| 3 | Worker crash during opening range | `tests/test_system_recovery.py::test_synthetic_transport_restart_opening_sequence` (1 cases) |
| 4 | Worker crash flat after opening | `tests/test_system_recovery.py::test_flat_recovery_preserves_history_and_permission` (1 cases) |
| 5 | Worker crash holding position | `tests/test_system_recovery.py::test_positions_keep_entry_hold_and_existing_protective_exit` (3 cases) |
| 6 | Watchdog restart | `tests/test_system_recovery.py::test_watchdog_root_cause_survives_observed_sigkill` (1 cases) |
| 7 | LaunchAgent lifecycle | `tests/test_reliability_review.py::test_controlled_flat_restart_is_reinvestigated_not_silently_whitelisted` (1 cases) |
| 8 | Host sleep | `tests/test_host_continuity.py::test_sleep_invalidates_cached_clock_and_requires_new_measurement` (1 cases) |
| 9 | AC disconnect | `tests/test_host_continuity.py::test_ac_required_for_system_sleep_protection_even_when_battery_full` (1 cases) |
| 10 | AC reconnect | `tests/test_host_continuity.py::test_ac_required_for_system_sleep_protection_even_when_battery_full` (1 cases) |
| 11 | Stale heartbeat | `tests/test_system_recovery.py::test_each_recovery_gate_fails_closed` (1 cases) |
| 12 | Stale host observation | `tests/test_host_continuity.py::test_post_read_clock_does_not_tolerate_genuine_stale_or_future` (2 cases) |
| 13 | Clock bound failure | `tests/test_system_recovery.py::test_each_recovery_gate_fails_closed` (1 cases) |
| 14 | Clock recertification | `tests/test_system_recovery.py::test_clock_data_and_reconciliation_sequence_recovers_only_after_proof` (1 cases) |
| 15 | Single read timeout | `tests/test_review_successor.py::test_local_sdk_timeout_classification` (3 cases) |
| 16 | Concurrent read batch timeout | `tests/test_review_successor.py::test_synthetic_three_read_timeout_regression_append_only` (1 cases) |
| 17 | Repeated read timeout | `tests/test_review_successor.py::test_repeated_failures_exceed_budget` (1 cases) |
| 18 | HTTP/2 GOAWAY | `tests/test_position_recovery.py::test_read_goaway_preserves_metadata` (1 cases) |
| 19 | Connection reset | `tests/test_reliability_review.py::test_read_mutation_partition_invariant` (5 cases) |
| 20 | Prolonged outage | `tests/test_position_recovery.py::test_prolonged_loss_classification_and_immutable_budget` (1 cases) |
| 21 | Network recovery | `tests/test_persistent.py::test_outages_count_episodes_and_recover_without_erasing_history` (1 cases) |
| 22 | Access token expiry | `tests/test_oauth_refresh.py::test_expiry_restored_or_refreshed_before_initialize` (5 cases) |
| 23 | Successful token refresh | `tests/test_oauth_refresh.py::test_401_refreshes_once_on_initialize` (1 cases) |
| 24 | Rejected refresh | `tests/test_oauth_refresh.py::test_refresh_rejection_persists_and_fresh_worker_cannot_spin` (5 cases) |
| 25 | Malformed refresh | `tests/test_oauth_refresh.py::test_malformed_refresh_response_is_sanitized` (3 cases) |
| 26 | Repeated 401 | `tests/test_oauth_refresh.py::test_second_401_is_durable_failure_not_infinite_refresh` (1 cases) |
| 27 | Account mismatch | `tests/test_system_recovery.py::test_each_recovery_gate_fails_closed` (1 cases) |
| 28 | Corrupt database | `tests/test_system_recovery.py::test_corrupted_database_never_starts_recovery` (1 cases) |
| 29 | Unexpected position | `tests/test_system_recovery.py::test_each_recovery_gate_fails_closed` (1 cases) |
| 30 | Unexpected order | `tests/test_system_recovery.py::test_each_recovery_gate_fails_closed` (1 cases) |
| 31 | Local/broker mismatch | `tests/test_position_recovery.py::test_integrated_broker_uncertainty_never_certifies` (7 cases) |
| 32 | Unresolved intent | `tests/test_system_recovery.py::test_any_symbol_pending_intent_blocks_global_recovery` (10 cases) |
| 33 | Mutation timeout before acknowledgment | `tests/test_system_recovery.py::test_mutation_failure_is_never_a_read_recovery` (3 cases) |
| 34 | Mutation timeout after acceptance | `tests/test_position_recovery.py::test_goaway_after_mutation_stays_ambiguous_without_duplicate` (2 cases) |
| 35 | Duplicate prevention | `tests/test_persistent.py::test_atomic_duplicate_reservation_same_symbol` (1 cases) |
| 36 | Partial fill | `tests/test_position_recovery.py::test_partial_fill_remains_owned_during_recovery` (1 cases) |
| 37 | Reconnect with position | `tests/test_position_recovery.py::test_integrated_incomplete_certification_eventually_exits` (8 cases) |
| 38 | Recovery budget exhaustion | `tests/test_reliability_review.py::test_preserved_kill_still_bounds_restarts` (3 cases) |
| 39 | Class C review | `tests/test_next_session_readiness.py::test_review_and_ack_require_explicit_attestation` (1 cases) |
| 40 | Class C invalidation | `tests/test_review_successor.py::test_successor_invalidates_without_rewriting_receipts` (5 cases) |
| 41 | Successor review | `tests/test_review_successor.py::test_synthetic_three_read_timeout_regression_append_only` (1 cases) |
| 42 | Multiple successors | `tests/test_reliability_review.py::test_multiple_successors_across_sessions_preserve_chain` (1 cases) |
| 43 | Unexplained event | `tests/test_review_successor.py::test_unexplained_or_hard_intervening_events_rejected` (16 cases) |
| 44 | Expiration and new investigation | `tests/test_reliability_review.py::test_new_investigation_can_follow_expired_receipt_without_reusing_it` (1 cases) |
| 45 | Restart loading valid receipt | `tests/test_review_successor.py::test_synthetic_three_read_timeout_regression_append_only` (1 cases) |
| 46 | Restart invalidates old worker proof | `tests/test_position_recovery.py::test_restart_never_certifies_previous_process_episode` (1 cases) |
| 47 | Session rollover | `tests/test_next_session_readiness.py::test_next_session_reset_preserves_spent_history` (1 cases) |
| 48 | MISSED state integrity | `tests/test_system_recovery.py::test_recovery_after_trigger_or_invalidation_never_rearms` (2 cases) |
| 49 | Cutoff integrity | `tests/test_persistent.py::test_cutoff_survives_clock_rollback` (1 cases) |
| 50 | Opening health not inherited | `tests/test_configuration.py::test_restart_requires_fresh_health_preserves_budget` (1 cases) |
| 51 | Data interruption during opening | `tests/test_persistent.py::test_opening_health_waits_for_real_range_and_host` (1 cases) |
| 52 | Data interruption after opening | `tests/test_system_recovery.py::test_each_recovery_gate_fails_closed` (1 cases) |
| 53 | VWAP unavailable | `tests/test_system_recovery.py::test_each_recovery_gate_fails_closed` (1 cases) |
| 54 | Risk veto | `tests/test_persistent.py::test_aggregate_cap` (2 cases) |
| 55 | Daily loss latch | `tests/test_persistent.py::test_daily_budget_is_not_refilled_by_profit_or_config_increase` (1 cases) |
| 56 | Attempt exhaustion | `tests/test_persistent.py::test_entry_attempt_cap_and_aborted_attempt_counts` (1 cases) |
| 57 | Entry cutoff | `tests/test_system_recovery.py::test_entry_cutoff_and_eod_never_recover` (3 cases) |
| 58 | Forced liquidation | `tests/test_persistent.py::test_exit_rules_independent` (1 cases) |
| 59 | Closed session | `tests/test_persistent.py::test_weekend_holiday_cutoff` (3 cases) |
| 60 | Sleep/stall legacy regression | `tests/test_reliability_review.py::test_historical_preserved_stalls_count_without_mutating_ledger` (1 cases) |
| 61 | Retained historical KILL session regression | `tests/test_next_session_readiness.py::test_readiness_separates_healthy_monitoring_from_hard_hold` (1 cases) |
| 62 | Earlier prolonged-labeled read regression | `tests/test_review_successor.py::test_multiple_spaced_incidents_all_included` (1 cases) |
| 63 | Earlier transient GOAWAY regression | `tests/test_review_successor.py::test_multiple_spaced_incidents_all_included` (1 cases) |
| 64 | Nested local MCP deadline regression | `tests/test_review_successor.py::test_local_sdk_timeout_classification` (3 cases) |
| 65 | Mixed unknown exception must stay hard | `tests/test_reliability_review.py::test_mixed_read_exception_is_not_benign_transport` (3 cases) |
| 66 | Atomic interrupted review write | `tests/test_review_successor.py::test_storage_interruption_is_atomic` (1 cases) |
| 67 | Review/hold race | `tests/test_review_successor.py::test_second_read_races_fail_closed` (5 cases) |
| 68 | Future host timestamp | `tests/test_host_continuity.py::test_post_read_clock_does_not_tolerate_genuine_stale_or_future` (2 cases) |
| 69 | Assertion ownership | `tests/test_host_continuity.py::test_assertions_require_exact_child_owner_and_both_types` (7 cases) |
| 70 | Build evidence cannot silently pass | `tests/test_build_verification.py::test_missing_or_forged_verification_never_reports_engineering_pass` (15 cases) |

The accompanying invariant families test every required recovery proof, mutation/READ partition, duplicate reservations, account pinning, session rollover, gross-loss accounting and immutable audit intervals. Passing tests establish these modeled behaviors; they do not guarantee network availability, broker uptime, host power or a valid future trading signal.
