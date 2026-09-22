# Test cases

Full inventory of test cases across the backend (`tests/`, pytest) and frontend
(`frontend/src/`, Vitest + Testing Library) suites, plus a summary of
overlapping/duplicate coverage found while reading them.

---

## Backend (`tests/`, pytest)

### tests/orchestrator/test_cli.py
Subsystem: `backend.orchestrator.cli` — CLI argument parser and the `status` table renderer.

| Test | What it verifies |
|---|---|
| `test_print_status_table_empty` | Printing an empty job list shows "No jobs recorded yet." |
| `test_print_status_table_renders_columns_and_truncated_detail` | Table output includes column headers and job field values, and truncates a long `detail` field to 60 chars |
| `test_run_requires_feature_id_or_all` | `run` with neither `--feature-id` nor `--all` exits via argparse error |
| `test_run_feature_id_and_all_are_mutually_exclusive` | Passing both `--feature-id` and `--all` to `run` causes a parse error |
| `test_run_parses_repeated_feature_ids` | Repeated `--feature-id` flags accumulate into a list; `--all` defaults false |
| `test_run_parses_max_concurrency_override` | `--max-concurrency 5` is parsed onto `args.max_concurrency` |
| `test_status_watch_flag_defaults_false` | `status` subcommand's `--watch` flag defaults to `False` |
| `test_answer_parses_sandbox_name_and_text` | `answer <box> <text>` parses both positional args correctly |
| `test_answer_requires_both_positional_args` | `answer` with only one positional arg fails to parse |

### tests/orchestrator/test_config.py
Subsystem: `backend.orchestrator.config` — YAML config/feature loading and the model/provider/env/kit/permission resolution precedence chain (spec > profile > config).

| Test | What it verifies |
|---|---|
| `test_config_load_new_style_status_file` | `status_file`/`max_concurrency` load correctly from the new-style `run:` block |
| `test_config_load_falls_back_to_done_marker` | Legacy `done_marker` key populates `status_file` when the new key is absent |
| `test_config_load_max_concurrency_defaults_to_three` | `max_concurrency` defaults to 3 when omitted |
| `test_config_load_status_file_defaults_when_neither_key_present` | `status_file` defaults to `.agent_status.json` when neither key present |
| `test_feature_load_all_no_agents_key` | Feature with no `agents:` key gets empty agents list; description stripped |
| `test_feature_load_all_string_shorthand_agents` | Agents given as a bare string list become `AgentSpec(agent_id=...)` |
| `test_feature_load_all_dict_agents_with_run_label` | Dict-form agents with `run_label` parse into distinct `AgentSpec`s |
| `test_feature_load_all_dict_agents_with_model_and_command` | Dict-form agents with `model`/`command` override fields parse correctly |
| `test_feature_load_all_missing_acceptance_criteria_defaults_empty` | Omitted `acceptance_criteria` defaults to `[]` |
| `test_feature_load_all_string_shorthand_agent_kit_defaults_empty` | String-shorthand agent's `kit` defaults to `[]` |
| `test_feature_load_all_dict_agents_with_skip_permissions_kit_and_provider` | Dict-form agents parse `dangerously_skip_permissions`, `kit` list, `provider` |
| `test_config_load_agents_section_parses_agent_profiles` | Top-level `agents:` map parses into `config.agent_profiles` keyed by agent id |
| `test_config_load_agent_profiles_defaults_to_empty_dict` | `agent_profiles` defaults to `{}` when no `agents:` section |
| `test_config_load_provider_defaults_to_none` | `config.provider` defaults to `None` |
| `test_resolve_model_spec_wins_over_profile_and_config` | `resolve_model`: spec model beats both profile and config |
| `test_resolve_model_profile_wins_over_config_when_spec_unset` | Profile model wins over config model when spec has none |
| `test_resolve_model_falls_back_to_default_models_when_no_overrides` | Falls back to `DEFAULT_MODELS[agent_id]` when nothing overrides it |
| `test_resolve_model_falls_back_to_config_for_agent_with_no_default` | Agent with no `DEFAULT_MODELS` entry falls back to `config.model` |
| `test_resolve_model_profile_wins_over_default_models` | Profile model beats `DEFAULT_MODELS` even when a default exists |
| `test_default_models_are_all_known_models` | Every `DEFAULT_MODELS` value is present in `KNOWN_MODELS` |
| `test_validate_model_accepts_known_model` | A known model for a known agent does not raise |
| `test_validate_model_rejects_unknown_model_for_known_agent` | Unknown model for a known agent raises `CommandError` with "Unknown model" |
| `test_validate_model_skips_agent_with_no_known_models_entry` | Agents absent from `KNOWN_MODELS` skip validation entirely |
| `test_resolve_provider_spec_wins_over_profile_and_config` | `resolve_provider`: spec beats profile and config |
| `test_resolve_provider_defaults_to_none_when_nothing_sets_it` | Returns `None` when nothing sets provider |
| `test_resolve_provider_profile_wins_over_config` | Profile provider beats config provider when spec unset |
| `test_resolve_dangerously_skip_permissions_spec_overrides_profile_true_to_false` | Explicit spec `False` overrides `True` profile/config value |
| `test_resolve_dangerously_skip_permissions_profile_wins_over_config` | Profile value wins over config when spec doesn't set it |
| `test_resolve_dangerously_skip_permissions_falls_back_to_config_when_profile_none` | Falls back to config value when neither spec nor profile set it |
| `test_resolve_kit_paths_concatenates_profile_then_spec` | `resolve_kit_paths` concatenates profile kit paths before spec kit paths |
| `test_resolve_kit_paths_with_no_profile_returns_spec_kit_only` | No profile → only spec's kit list returned |
| `test_resolve_kit_paths_with_neither_returns_empty_list` | Neither set → empty list |
| `test_resolve_env_merges_profile_and_spec_with_spec_winning` | `resolve_env` merges dicts; spec overrides profile on key conflict |
| `test_resolve_env_with_no_profile_returns_spec_env_only` | No profile → spec env only |
| `test_resolve_env_with_neither_returns_empty_dict` | Neither set → `{}` |
| `test_uses_custom_model_endpoint_true_for_anthropic_base_url` | True when `ANTHROPIC_BASE_URL` is present |
| `test_uses_custom_model_endpoint_false_for_unrelated_env` | False for unrelated env keys |
| `test_uses_custom_model_endpoint_false_for_empty_env` | False for empty env dict |
| `test_feature_load_all_parses_agent_env_mapping` | Agent-level `env:` mapping in features.yaml parses into `AgentSpec.env` |
| `test_feature_load_all_stringifies_non_string_env_values` | Non-string env values get stringified on load |
| `test_feature_load_all_rejects_non_mapping_env` | A list-shaped `env:` raises `CommandError` |
| `test_feature_load_all_env_defaults_empty_when_omitted` | Omitted `env:` defaults to `{}` |
| `test_config_load_parses_agent_profile_env` | `agents:` profile section's `env:` mapping loads into `AgentProfile.env` |

### tests/orchestrator/test_config_writer.py
Subsystem: `backend.orchestrator.config_writer` — YAML writer/serializer, verified by round-tripping through `Config.load`/`Feature.load_all`.

| Test | What it verifies |
|---|---|
| `test_config_survives_a_round_trip` | A written `Config` reloads to an equal object |
| `test_config_round_trip_keeps_agent_profiles` | `agent_profiles` (including `env`) survive a dump/reload round trip |
| `test_config_round_trip_preserves_non_default_scalars` | Non-default scalar fields (`max_turns`, `provider`, `verify_on`, `telemetry`, etc.) round-trip |
| `test_features_survive_a_round_trip` | Minimal and maximal `Feature` round-trip, descriptions normalized (stripped) |
| `test_an_all_default_agent_entry_stays_a_single_key` | All-default agent entry serializes to `{"agent_id": "claude"}`, not a bloated dict |
| `test_multi_line_description_is_written_as_a_block_scalar` | Multi-line descriptions written using YAML `\|` block scalar syntax |
| `test_written_files_carry_the_managed_header` | Written files start with `HEADER` and remain valid YAML underneath |
| `test_a_failed_round_trip_leaves_the_original_file_alone` | Failed post-write reload check leaves original file untouched, no leftover `.tmp` |
| `test_is_bundled_preset_flags_the_checked_in_demo` | `is_bundled_preset` true for a file under `demo/`, false otherwise |
| `test_description_whitespace_is_normalized_not_lost` | Leading/trailing whitespace stripped but internal content preserved |

### tests/orchestrator/test_events.py
Subsystem: `backend.orchestrator.events` — in-process `EventBus` pub/sub and replay buffer, used to bridge worker threads to SSE.

| Test | What it verifies |
|---|---|
| `test_publish_delivers_to_every_subscriber` | A published event reaches all subscribers with correct type/data |
| `test_event_ids_increase_monotonically` | Sequential publishes get ids 1, 2, 3, ... |
| `test_unsubscribe_stops_delivery` | Calling the unsubscribe callback stops further delivery |
| `test_a_failing_subscriber_cannot_break_the_publisher` | A raising subscriber doesn't block delivery to others or crash `publish` |
| `test_replay_returns_only_events_after_the_last_seen_id` | `replay_since(id)` returns only events published after that id ⚠️ see overlap #1 |
| `test_fresh_connection_replays_nothing` | `replay_since(None)` (new client) returns `[]` |
| `test_replay_signals_resync_when_the_gap_is_too_large` | `replay_since` returns `None` once the requested id has aged out ⚠️ see overlap #1 |
| `test_replay_at_the_buffer_boundary_still_succeeds` | Replay succeeds exactly at the buffer's retention boundary |
| `test_buffer_is_bounded` | The ring buffer never holds more than `buffer_size` events |
| `test_last_event_id_tracks_the_newest_event` | `bus.last_event_id` updates as events are published |
| `test_publishing_from_many_threads_assigns_unique_ids` | Concurrent publishes from 8 threads (160 total) all get unique ids |

### tests/orchestrator/test_job.py
Subsystem: `backend.orchestrator.job` — per-job lifecycle: naming, expanding features into jobs, polling, finalizing success, `run_job`/`resume_job`/`run_job_safe`/`run_all`, host-vs-container path handling.

| Test | What it verifies |
|---|---|
| `test_sandbox_name_for_without_run_label` | Sandbox name is `arena-<feature>-<agent>` with no run label |
| `test_sandbox_name_for_with_run_label` | Run label is appended to the sandbox name |
| `test_sandbox_name_for_truncates_to_60_chars` | Very long feature ids get truncated so the name stays ≤60 chars |
| `test_branch_for_without_run_label` | Branch is `agent/<feature>/<agent>` with no run label |
| `test_branch_for_with_run_label` | Run label is appended to the branch name |
| `test_expand_jobs_no_agents_key_defaults_to_config_agent` | No `agents` → single job using the config-level default agent |
| `test_expand_jobs_multiple_agents` | Multiple agent specs expand to one job per spec, in order |
| `test_expand_jobs_unknown_feature_id_raises` | Unknown feature id raises `CommandError` |
| `test_expand_jobs_duplicate_sandbox_name_raises` | Two specs deriving the same sandbox name raise `CommandError` |
| `test_expand_jobs_unsupported_agent_without_command_raises` | Agent id with no CLI builder and no `command` override raises `CommandError` |
| `test_expand_jobs_unsupported_agent_with_command_is_allowed` | Same agent allowed through with a `command` override |
| `test_expand_jobs_accepts_copilot_agent_id` | `copilot` is accepted as a valid agent id |
| `test_expand_jobs_unknown_model_raises` | Unrecognized model for a validated agent raises `CommandError` |
| `test_expand_jobs_unknown_model_error_names_the_feature` | The error names the offending feature id |
| `test_expand_jobs_unvalidated_agent_model_is_allowed` | Agents outside `KNOWN_MODELS` allow any model string |
| `test_expand_jobs_command_override_skips_model_validation` | `command` override skips model validation |
| `test_expand_jobs_custom_endpoint_env_skips_model_validation` | `ANTHROPIC_BASE_URL` in spec env skips model validation |
| `test_expand_jobs_env_without_base_url_still_validates_model` | Env without `ANTHROPIC_BASE_URL` still validates the model |
| `test_expand_jobs_custom_endpoint_from_agent_profile_skips_validation` | Custom endpoint via agent *profile* also skips model validation |
| `test_expand_jobs_selects_only_requested_features` | Explicit id list filters to just those features |
| `test_find_spec_for_sandbox_matches_by_derived_name` | Matches a spec by recomputing its derived sandbox name |
| `test_find_spec_for_sandbox_no_agents_key_uses_config_default` | Falls back to config-default `AgentSpec` when feature has no `agents` |
| `test_find_spec_for_sandbox_no_match_raises` | No matching spec raises `CommandError` |
| `test_poll_until_signal_returns_done_when_status_file_says_so` | Returns `"done"`, records status/detail from status file |
| `test_poll_until_signal_returns_awaiting_input` | Returns `"awaiting_input"`, records prompt detail |
| `test_poll_until_signal_container_crash_with_no_status_returns_crashed` | Crashed status + no status file → `"crashed"` |
| `test_poll_until_signal_container_crash_but_status_file_says_done` | A valid "done" status file wins even if container looks crashed |
| `test_poll_until_signal_times_out_when_deadline_already_passed` | Already-past deadline immediately returns `"timed_out"` |
| `test_poll_until_signal_unknown_state_keeps_polling_until_done` | "unknown" state is not terminal; polling continues |
| `test_finalize_success_pushes_branch_and_records_metrics` | On success: branch pushed, PR opened, metrics recorded, sandbox removed |
| `test_finalize_success_skips_the_pr_when_open_pr_is_off` | `open_pr=False` means no PR opened, branch still pushes |
| `test_finalize_success_verifies_on_the_host_by_default` | Default `verify_on=host` verifies on the host — one sandbox exec (teardown read) ⚠️ see overlap #3 |
| `test_finalize_success_verify_on_sandbox_uses_the_sandbox` | `verify_on=sandbox` verifies inside the sandbox (2 sbx execs) ⚠️ see overlap #3 |
| `test_finalize_success_test_failure_skips_push_and_removes_sandbox` | Failing host verification → `tests_failed`, push skipped, sandbox removed |
| `test_run_job_skips_when_awaiting_input` | Already-`awaiting_input` job returns early without touching the sandbox |
| `test_run_job_skips_already_succeeded` | Already-`succeeded` job returns early |
| `test_run_job_full_success_path` | Fresh job creates sandbox, launches agent, polls to done, finalizes |
| `test_run_job_reattaches_to_running_sandbox_without_relaunching` | An existing "running" sandbox is reattached to, not recreated |
| `test_run_job_removes_and_relaunches_after_previous_crash` | A "crashed" sandbox is removed, then recreated and relaunched |
| `test_run_job_resolves_model_and_kit_from_agent_profile` | Model/kit passed to create/launch reflect merged profile+spec resolution |
| `test_run_job_spec_model_overrides_agent_profile` | Spec-level model wins over profile model when launching ⚠️ see overlap #5 |
| `test_run_job_merges_and_passes_resolved_env_to_sbx_create` | Merged env (spec wins on conflict) passed to `sbx_create_detached` |
| `test_run_job_resolves_provider_and_skip_permissions_from_agent_profile` | Provider/skip-permissions resolution flows through to create/launch calls |
| `test_resume_job_unknown_sandbox_raises` | Resuming an unknown sandbox name raises `CommandError` |
| `test_resume_job_not_awaiting_input_raises` | Resuming a job not in `awaiting_input` raises `CommandError` |
| `test_resume_job_sandbox_gone_marks_lost_and_raises` | Missing sandbox → status `"lost"` + `CommandError` |
| `test_resume_job_happy_path_finalizes` | Resuming to `"done"` calls `_finalize_success` |
| `test_resume_job_resolves_model_and_skip_permissions_from_agent_profile` | Resume launch uses profile-resolved model/skip-permissions |
| `test_resume_job_awaiting_input_again_does_not_finalize` | Polling back to `awaiting_input` does not finalize |
| `test_run_job_safe_records_error_status_on_exception` | `run_job_safe` catches exceptions, records status `"error"` with message |
| `test_run_all_respects_max_concurrency` | `run_all` never runs more than `max_workers` jobs concurrently |
| `test_teardown_read_survives_an_absolute_host_repo_path` | Teardown recovers run log even when `cd <host_repo_path>` fails in-container |
| `test_verify_on_sandbox_uses_the_discovered_container_dir` | In-sandbox verification `cd`s into the container-discovered dir ⚠️ see overlap #3 |
| `test_teardown_read_without_a_dir_marker_still_returns_the_log` | Missing directory marker still returns the run log |
| `test_verify_on_sandbox_falls_back_to_the_default_workdir` | Empty `container_dir` → no explicit `workdir` kwarg |

### tests/orchestrator/test_metrics.py
Subsystem: `backend.orchestrator.metrics` — parsing token/turn usage from agent CLI logs and git diff numstat.

| Test | What it verifies |
|---|---|
| `test_parses_claude_result_object` | Extracts tokens/turns from a Claude `--output-format json` result object |
| `test_parses_codex_jsonl_taking_the_final_cumulative_counts` | Codex JSONL uses the *last* cumulative `token_count` event, not a sum |
| `test_claude_json_survives_surrounding_prose` | Parsing tolerates extra shell/log noise around the JSON payload |
| `test_agents_without_structured_usage_report_nothing` | cursor/opencode/copilot always return empty `Usage()` |
| `test_unparseable_log_yields_empty_usage_rather_than_raising` | Empty/garbage/truncated JSON yields empty `Usage` without raising |
| `test_missing_fields_stay_none_rather_than_zero` | Absent fields stay `None`, not `0` |
| `test_booleans_are_not_mistaken_for_counts` | A boolean `true` for `input_tokens` is not treated as a valid count |
| `test_numstat_sums_files_and_lines` | `parse_diff_numstat` sums file count, additions, deletions correctly |
| `test_numstat_counts_binary_files_without_line_totals` | Binary files count toward file count but not line totals |
| `test_numstat_ignores_malformed_lines` | Empty/garbage input yields `(0, 0, 0)` without raising |

### tests/orchestrator/test_process.py
Subsystem: `backend.orchestrator.process` — shared subprocess wrapper (`run`), secret redaction and env passthrough.

| Test | What it verifies |
|---|---|
| `test_redacts_secret_values_from_the_log_line` | A `redact=`-listed secret never appears in stdout logging (`***` instead) |
| `test_redacts_secret_values_from_the_error_message` | `CommandError`'s string representation also redacts the secret |
| `test_logs_the_command_verbatim_when_nothing_is_redacted` | No `redact` list → command logs unmodified |
| `test_empty_redact_value_does_not_mangle_the_command` | `redact=[""]` doesn't insert `***` between every character |
| `test_env_replaces_the_child_environment` | Explicit `env=` dict is passed through to the child process |
| `test_env_defaults_to_inheriting_the_parent_environment` | Omitting `env` inherits the parent's environment |

### tests/orchestrator/test_prompt.py
Subsystem: `backend.orchestrator.prompt` — building initial/resume prompts sent to coding agents.

| Test | What it verifies |
|---|---|
| `test_build_prompt_includes_branch_and_status_file` | Prompt includes branch, status file path, description, criteria, status-protocol vocabulary |
| `test_build_prompt_no_acceptance_criteria_shows_placeholder` | Empty acceptance criteria renders a `(none specified)` placeholder |
| `test_build_resume_prompt_includes_answer_and_status_file` | Resume prompt includes the user's answer text and the status file path |

### tests/orchestrator/test_sandbox_cost.py
Subsystem: End-to-end `job_module.run_job` through a stubbed `sbx.run` — pins exact costly `sbx exec` call counts per job.

| Test | What it verifies |
|---|---|
| `test_default_job_makes_exactly_three_sandbox_execs` | Default job issues exactly 3 execs: launch, status read, teardown read |
| `test_verification_does_not_run_in_the_sandbox_by_default` | With host-default verification, `pytest` never appears in any exec |
| `test_verify_on_sandbox_costs_one_extra_exec` | `verify_on=sandbox` adds exactly one more exec (4 total) ⚠️ see overlap #3 |
| `test_liveness_polling_uses_the_host_side_listing` | Liveness polled via `sbx ls --json` (host-side, free), not exec |
| `test_status_read_avoids_the_login_shell` | Status/teardown reads use `bash -c`, not a login shell |
| `test_sandbox_is_removed_once_the_job_is_done` | Sandbox removed via `sbx rm --force` after completion |
| `test_no_pr_is_opened_by_default` | With `open_pr` off, job succeeds with `pr_url=None`, no `gh` calls |

### tests/orchestrator/test_sbx.py
Subsystem: `backend.orchestrator.sbx` — building agent CLI command lines, dispatch/override layer, shell-wrapping, and the `sbx` binary wrapper functions.

| Test | What it verifies |
|---|---|
| `test_build_claude_cmd_default_flags` | Builds `claude --dangerously-skip-permissions --model <m> --print <prompt>` |
| `test_build_claude_cmd_without_skip_permissions_or_model` | Omitting skip-permissions/model omits those flags |
| `test_build_claude_cmd_with_max_turns` | `--max-turns <n>` added correctly |
| `test_build_claude_cmd_resume_appends_continue` | `resume=True` adds `--continue` before the trailing prompt |
| `test_build_codex_cmd_default_flags` | Builds `codex exec --dangerously-bypass-approvals-and-sandbox --model <m> <prompt>` |
| `test_build_codex_cmd_resume_inserts_resume_last_before_flags` | Resume inserts `codex exec resume --last` ahead of other flags |
| `test_build_cursor_cmd_default_flags` | Builds `cursor-agent --print --force --model <m> <prompt>` |
| `test_build_cursor_cmd_resume_appends_resume_flag` | Resume adds `--resume` |
| `test_build_opencode_cmd_default_flags` | Builds `opencode run --model <m> <prompt>` |
| `test_build_opencode_cmd_resume_appends_continue` | Resume adds `--continue` |
| `test_build_agent_cmd_known_agent_delegates_to_builder` | `build_agent_cmd` dispatches to the right per-agent builder |
| `test_build_agent_cmd_model_override_wins_over_config_model` | `model_override` replaces the config model in the built command |
| `test_build_agent_cmd_unknown_agent_raises` | Unknown agent id raises `CommandError` naming the agent |
| `test_build_agent_invocation_known_agent_returns_shlex_joined_argv` | `build_agent_invocation` returns a shlex-quoted shell string |
| `test_capture_usage_adds_the_claude_json_output_flag` | `capture_usage=True` adds `--output-format json` for claude |
| `test_capture_usage_adds_the_codex_json_flag` | `capture_usage=True` adds `--json` for codex |
| `test_capture_usage_off_leaves_the_agent_output_human_readable` | `capture_usage=False` omits both flags |
| `test_capture_usage_is_accepted_and_ignored_by_agents_without_usage` | Agents without structured usage silently ignore `capture_usage=True` |
| `test_build_agent_invocation_command_override_exports_env_and_runs_verbatim` | Command override exports `ORCH_PROMPT`/`ORCH_STATUS_FILE`/`ORCH_RESUME` then runs verbatim |
| `test_build_agent_invocation_command_override_resume_sets_orch_resume_1` | Resume sets `ORCH_RESUME=1` in exported env |
| `test_build_agent_shell_command_captures_exit_code_and_falls_back` | Wrapped shell command captures `$?`, writes fallback status via `python3 -c`, exits with captured code |
| `test_build_agent_shell_command_redirects_only_final_line` | Only the final command line gets stdout/stderr redirected to the log file |
| `test_sbx_list_uses_the_host_side_json_listing` | `sbx_list()` calls exactly `sbx ls --json` ⚠️ see overlap #2 |
| `test_sbx_exists_true_when_name_in_listing` | `sbx_exists` reflects presence/absence in the listing |
| `test_sbx_status_reads_the_status_field` | `sbx_status` reads each sandbox's status field |
| `test_sbx_status_unknown_when_not_listed` | Unlisted sandbox reports `"unknown"` status |
| `test_sbx_list_empty_when_sbx_fails` | Non-zero `sbx` exit code yields empty listing rather than raising |
| `test_sbx_list_empty_on_malformed_json` | Non-JSON stdout yields empty listing |
| `test_sbx_list_empty_when_payload_shape_is_unexpected` | Wrong-shaped payload yields empty listing |
| `test_status_read_skips_the_login_shell` | Polled status read uses `bash -c` (no login shell) |
| `test_sbx_read_status_missing_file_returns_none` | Failed read (nonzero exit) returns `None` |
| `test_sbx_read_status_valid_json` | Valid JSON status content parses into a dict |
| `test_sbx_read_status_malformed_json_returns_unknown_state` | Malformed JSON yields `{"state": "unknown", ...}` rather than raising |
| `test_build_copilot_cmd_default_flags` | Builds `copilot -p <prompt> --allow-all --model <m>` |
| `test_build_copilot_cmd_without_skip_permissions_or_model` | Omits optional flags when unset |
| `test_build_copilot_cmd_resume_raises_command_error` | Copilot resume is unsupported, raises `CommandError` |
| `test_copilot_in_agent_cli_builders` | `copilot` is registered in `AGENT_CLI_BUILDERS` |
| `test_build_agent_cmd_skip_permissions_override_false_beats_config_true` | Explicit `False` override beats `True` config value |
| `test_build_agent_cmd_skip_permissions_override_none_falls_back_to_config` | `None` override falls back to config value |
| `test_sbx_create_detached_no_kit_or_provider_matches_today` | Baseline `sbx create` command shape with no optional flags |
| `test_sbx_create_detached_passes_repeated_kit_flags` | Multiple `--kit` flags emitted, one per kit path, in order |
| `test_sbx_create_detached_passes_provider_and_model` | `--provider`/`--model` flags added when set |
| `test_sbx_create_detached_emits_sorted_env_flags_before_workspace` | `-e KEY=VAL` flags sorted by key, positioned before the workspace path |
| `test_sbx_create_detached_empty_env_adds_no_flags` | Empty env dict adds no `-e` flags |
| `test_exec_capture_emits_workdir_flag_only_when_set` | `sbx_exec_capture` only adds `-w <dir>` when `workdir` is passed |

### tests/orchestrator/test_state.py
Subsystem: `backend.orchestrator.state` — SQLite-backed `StateStore` for job records and telemetry samples, legacy schema migration, column-binding safety.

| Test | What it verifies |
|---|---|
| `test_upsert_then_get_round_trips` | A new job upserted then fetched returns the same field values |
| `test_get_unknown_sandbox_returns_none` | Fetching a nonexistent sandbox returns `None` |
| `test_upsert_updates_existing_row` | A partial upsert updates only given fields, others left intact |
| `test_all_jobs_orders_most_recently_updated_first` | `all_jobs()` orders by most-recently-updated first |
| `test_all_jobs_empty_store_returns_empty_list` | Empty store returns `[]` |
| `test_wal_journal_mode_enabled` | Connection's `PRAGMA journal_mode` is WAL |
| `test_concurrent_writers_do_not_raise_database_locked` | Two `StateStore` connections write concurrently without "database locked" |
| `test_close_releases_the_connection` | After `close()`, further queries raise `sqlite3.ProgrammingError` |
| `test_context_manager_closes_on_exit` | Using `StateStore` as a context manager closes the connection on exit |
| `test_upsert_rejects_unknown_column` | Unrecognized kwarg to `upsert` raises `ValueError` |
| `test_opening_a_legacy_database_adds_the_metric_columns` | Opening a pre-metrics legacy schema adds new metric columns via `ALTER TABLE` |
| `test_migration_is_idempotent_across_reopens` | Reopening an already-migrated DB doesn't fail on duplicate `ADD COLUMN` |
| `test_metric_columns_round_trip` | All metric fields round-trip; unreported fields stay `None` |
| `test_samples_round_trip_in_chronological_order` | Telemetry samples come back in insertion order |
| `test_samples_are_scoped_per_sandbox` | Samples for one sandbox don't leak into another's query results |
| `test_unreported_sample_fields_stay_null` | `None` sample fields stay `None`, not `0` |
| `test_upsert_sql_column_list_matches_writable_columns` | `INSERT INTO jobs (...)` column list matches `_WRITABLE_COLUMNS` order |
| `test_every_writable_column_survives_a_round_trip` | Every column in `_WRITABLE_COLUMNS` independently round-trips its own value |
| `test_repo_column_is_added_to_a_legacy_database` | Pre-shared-DB legacy schema gets `repo` column added; old rows get `NULL` |
| `test_all_jobs_filters_by_repo` | `all_jobs(repo=...)` filters correctly; unfiltered call returns all rows |
| `test_opens_a_database_in_a_directory_that_does_not_exist_yet` | `StateStore` creates missing parent directories for the DB file |

### tests/orchestrator/test_telemetry.py
Subsystem: `backend.orchestrator.telemetry` — cgroup probe parsing, CPU-rate computation, `TelemetrySampler` background thread.

| Test | What it verifies |
|---|---|
| `test_parses_a_real_sandbox_probe` | Parses `cpu_usage_usec`, `mem_bytes`, `pids` from a real observed probe string |
| `test_unlimited_memory_falls_back_to_host_total` | `mem_max=max` falls back to the host's total memory |
| `test_explicit_memory_limit_is_used_when_set` | An explicit numeric `mem_max` is used directly |
| `test_missing_files_yield_none_rather_than_raising` | All-empty probe values yield an empty reading, no exception |
| `test_garbage_output_is_tolerated` | Malformed/binary garbage input doesn't raise; fields become `None` |
| `test_cpu_cores_is_the_rate_between_cumulative_readings` | `compute_cpu_cores` computes CPU-time delta / wall-time delta |
| `test_cpu_cores_is_none_on_the_first_sample` | No prior reading yields `None` |
| `test_cpu_cores_is_none_when_the_counter_goes_backwards` | Decreasing counter (e.g. restart) yields `None`, not negative |
| `test_cpu_cores_is_none_for_a_non_positive_interval` | Zero/negative wall-time interval yields `None` |
| `test_sampler_does_not_start_a_thread_when_telemetry_is_off` | `telemetry=False` → `start()` spawns no thread |
| `test_sampler_starts_a_thread_when_enabled` | `telemetry=True` spawns a thread that `stop()` cleanly joins |
| `test_sample_once_probes_each_live_sandbox` | `sample_once` execs into each sandbox from `sbx_list()` exactly once |
| `test_cpu_rate_appears_on_the_second_pass` | First pass has no `cpu_cores`; second pass computes a rate |
| `test_a_failed_probe_yields_an_empty_sample_not_an_error` | Nonzero-exit probe yields empty sample rather than raising/dropping |
| `test_on_sample_callback_receives_each_sample` | `on_sample` callback fires once per collected sample |
| `test_probe_never_uses_a_login_shell` | Probe exec call passes `login_shell=False` |

### tests/test_api_integrations.py
Subsystem: `backend.api.clone`, `backend.api.secrets`, `backend.api.issues`, `backend.api.routes_integrations` — cloning, `sbx secret` management, GitHub issue import (FastAPI `TestClient`).

| Test | What it verifies |
|---|---|
| `test_clone_rejects_a_dangerous_url_without_running_git` | A rejected clone URL never reaches the subprocess call (422) |
| `test_clone_accepts_a_github_url_and_returns_202` | Valid GitHub URL returns 202 with `state: running`, `target_path` ending in `owner-repo` |
| `test_clone_worker_uses_safe_git_flags` | Clone command disables `ext`/`file` protocols, uses `--` before URL, omits `--depth`, sets `GIT_TERMINAL_PROMPT=0` |
| `test_clone_status_404s_for_an_unknown_id` | Querying an unknown clone id returns 404 |
| `test_parses_the_secret_table` | `parse_secret_table` extracts name/state rows from `sbx secret ls` output |
| `test_secret_table_skips_malformed_rows` | A garbage trailing line doesn't break parsing or add a spurious row |
| `test_store_secret_with_a_command_builds_the_right_argv` | POST `/secrets` with `command` builds `sbx secret set <service> --command <cmd>` |
| `test_a_stored_token_never_reaches_the_logs` | A stored token never appears in captured stdout or the response body |
| `test_secret_requires_exactly_one_source` | Providing both `token`/`ref`, or neither, is rejected (422) |
| `test_secret_rejects_an_unknown_service` | An unrecognized `service` value is rejected (422) |
| `test_list_secrets_reports_unavailable_without_sbx` | GET `/secrets` reports unavailable when `sbx` is missing |
| `test_lifts_acceptance_criteria_from_a_task_list` | `acceptance_criteria_from_body` extracts checked/unchecked checklist items |
| `test_an_issue_without_a_checklist_gets_no_criteria` | Plain-prose issue body yields `[]` criteria |
| `test_drafting_a_feature_from_an_issue` | `draft_feature_from_issue` builds a `Feature` with id `issue-<n>` and extracted criteria |
| `test_github_issues_503s_without_gh` | GET `/github/issues` returns 503 when `gh` is unavailable |
| `test_import_issues_writes_features` | POST `/features/import-issues` writes only the selected issue(s) |
| `test_import_issues_is_idempotent` | Re-importing the same issue number updates rather than duplicating |
| `test_import_issues_404s_for_an_unknown_number` | Requesting an unlisted issue number yields 404 |
| `test_import_issues_rejects_an_empty_list` | An empty `issues` list is rejected (422) |

### tests/test_api.py
Subsystem: `backend.main`/`backend.api.runner`/`backend.api.sandboxes` — core HTTP API: health/preflight/agents/features, run launching, sandbox control.

| Test | What it verifies |
|---|---|
| `test_health_still_works` | GET `/health` returns `{"status": "ok"}` |
| `test_preflight_reports_each_check` | GET `/preflight` includes checks for `sbx`, `sandboxd`, `gh`, `demo_repo` |
| `test_agents_endpoint_distinguishes_verified_from_best_effort` | `/agents` marks claude/codex verified, cursor not; only claude reports usage |
| `test_features_endpoint_lists_the_configured_features` | `/features` lists configured feature ids and agent counts |
| `test_start_run_returns_a_run_id` | POST `/runs` returns a `run-` prefixed id, dispatches exactly one submission |
| `test_unknown_feature_is_rejected_before_anything_is_created` | Unknown `feature_id` rejected (422) before any dispatch |
| `test_unsupported_agent_is_rejected` | Unsupported `agent_id` rejected (422) |
| `test_unknown_model_is_rejected` | Unknown model for a validated agent rejected (422) |
| `test_empty_agent_list_is_rejected_by_the_model` | Empty `agents: []` rejected by Pydantic model (422) |
| `test_malformed_memory_limit_is_rejected` | Non-numeric `memory` value rejected (422) |
| `test_unknown_run_is_404` | GET on an unknown run id returns 404 |
| `test_runs_and_jobs_listings_are_empty_initially` | `/runs` and `/jobs` both start as empty lists |
| `test_ping_reports_agent_liveness` | POST `/sandboxes/{name}/ping` reports reachable/agent_alive/status_file_present/latency |
| `test_ping_distinguishes_a_dead_agent_from_a_gone_container` | Distinguishes dead-agent-in-live-container from gone container |
| `test_kill_all_requires_the_typed_confirmation` | Missing/wrong confirmation text rejected (422) |
| `test_kill_all_with_confirmation_removes_and_marks_jobs_lost` | Correct confirmation removes all sandboxes, reports them removed |
| `test_sandboxes_listing_uses_the_json_form` | GET `/sandboxes` parses the JSON listing form ⚠️ see overlap #2 |
| `test_list_jobs_filters_by_repo` | GET `/jobs?repo=...` narrows to matching jobs |
| `test_list_jobs_rejects_an_over_long_repo_filter` | Excessively long `repo` query param rejected (422) |
| `test_start_run_carries_the_full_agent_spec` | POST `/runs` preserves env/kit/provider/run_label/model through to `AgentSpec` |

### tests/test_api_setup.py
Subsystem: `backend.api.workspace`/`backend.api.routes_setup` — repo picker (`/workspaces/*`) and config/features editors over HTTP.

| Test | What it verifies |
|---|---|
| `test_inspect_reports_a_repo` | POST `/workspaces/inspect` reports `is_git_repo`, `github_repo`, `has_config: False` |
| `test_inspect_does_not_select_the_workspace` | Inspecting a path doesn't change the active workspace |
| `test_inspect_404s_on_a_missing_directory` | Inspecting a nonexistent path returns 404 |
| `test_inspect_rejects_a_dangerous_path` | A path like `/proc/self` is rejected (422) |
| `test_select_then_features_follow_the_workspace` | Selecting a repo repoints `/features` at that repo's own file |
| `test_get_config_returns_a_draft_when_none_is_saved` | GET `/config` with none saved returns `exists: False` with a prefilled draft |
| `test_put_config_round_trips_through_the_workspace_file` | PUT `/config` writes a config that reloads correctly; server always stamps `repo_path` |
| `test_put_config_without_a_workspace_is_a_conflict` | PUT `/config` with no active workspace returns 409 |
| `test_put_config_rejects_an_unknown_verify_on` | An invalid `verify_on` value is rejected (422) |
| `test_the_bundled_demo_preset_is_never_written_over` | Saving while pointed at the bundled demo returns 409, file untouched |
| `test_put_features_round_trips_a_full_agent_spec` | PUT `/features` preserves full `AgentSpec` fields through to saved YAML |
| `test_put_features_rejects_duplicate_ids` | Two features with the same `id` rejected (422) |
| `test_put_features_rejects_an_id_with_spaces` | An id containing a space is rejected (422) |
| `test_get_features_returns_the_full_agent_spec` | GET `/features` returns the complete agent spec needed to prefill the form |

### tests/test_sse.py
Subsystem: `backend.api.stream` — SSE bridge: frame formatting, `Last-Event-ID` parsing, resync signaling, worker-thread→asyncio delivery/backpressure/disconnect.

| Test | What it verifies |
|---|---|
| `test_frame_carries_id_event_and_json_data` | `format_sse` produces a well-formed `id:`/`event:`/JSON-data frame |
| `test_non_serialisable_values_do_not_break_a_frame` | A non-JSON-serializable event value doesn't break frame formatting |
| `test_resync_frame_names_the_resume_point` | `format_resync` embeds the numeric `resume_from` id |
| `test_last_event_id_header_parsing` (6 cases) | `parse_last_event_id` handles valid/None/empty/junk/negative/zero — all invalid cases return `None` |
| `test_a_worker_thread_publish_reaches_an_async_subscriber` | A `bus.publish` from a worker thread reaches an asyncio subscriber |
| `test_replay_delivers_exactly_what_was_missed` | `replay_since` returns exactly the missed events ⚠️ see overlap #1 |
| `test_a_client_gone_too_long_is_told_to_resync` | `replay_since` returns `None` when the gap exceeds the buffer ⚠️ see overlap #1 |
| `test_subscriber_is_removed_when_the_stream_ends` | Closing an SSE generator removes its subscriber from the bus (no leak) |
| `test_slow_client_does_not_block_the_publisher` | A full/slow subscriber queue drops events rather than blocking the publisher |

### tests/test_validators.py
Subsystem: `backend.api.validators`/`backend.api.paths` — security boundary for clone URLs, local filesystem paths, directory names.

| Test | What it verifies |
|---|---|
| `test_rejects_dangerous_or_malformed_clone_urls` (14 cases) | Rejects `ext::` RCE, flag injection, non-https/git transports, embedded credentials, off-allowlist hosts, malformed paths, query strings, injection, non-URLs, empty string |
| `test_accepts_ordinary_github_urls` (5 cases) | Accepts normal https/git@ GitHub URLs with/without `.git`, with dashes |
| `test_clone_host_allowlist_is_configurable` | `ALLOWED_CLONE_HOSTS_ENV` can restrict/redirect the allowed host |
| `test_rejects_bad_local_paths` (8 cases) | Rejects empty/whitespace, null bytes, newlines, known-sensitive system paths |
| `test_canonicalizes_a_local_path` | Path traversal resolves to the canonical absolute path |
| `test_expands_a_home_relative_path` | A `~/...` path expands to an absolute path |
| `test_a_nonexistent_path_is_allowed_through` | A path that doesn't exist yet passes validation |
| `test_workspace_root_confinement` | Paths outside `WORKSPACE_ROOT_ENV` are rejected |
| `test_confinement_survives_a_symlink_pointing_outside` | A symlink pointing outside the root is still rejected (validated against resolved path) |
| `test_rejects_bad_directory_names` (6 cases) | Rejects empty, `.`, `..`, slashes, spaces, over-length, null bytes |
| `test_accepts_an_ordinary_directory_name` | A normal hyphenated name passes through unchanged |

### tests/test_workspace.py
Subsystem: `backend.api.workspace`/`backend.api.settings` — workspace registry, repo inspection, GitHub URL parsing, config/features path resolution order.

| Test | What it verifies |
|---|---|
| `test_registry_round_trips` | Saving then loading a `WorkspaceRegistry` preserves `active` and entries |
| `test_saving_the_registry_leaves_no_temp_file` | No `workspaces.json.tmp` remains after a save (atomic write) |
| `test_a_missing_registry_reads_as_empty` | No registry file → `load_registry()` returns empty workspaces |
| `test_a_corrupt_registry_reads_as_empty_rather_than_raising` | Malformed JSON tolerated, returns empty rather than raising |
| `test_re_registering_the_same_path_does_not_duplicate_it` | Calling `select()` twice on the same path results in one entry |
| `test_inspect_reports_a_missing_directory` | `inspect()` on a nonexistent path reports `exists: False` |
| `test_inspect_reports_a_plain_directory_as_not_a_repo` | An existing non-git directory reports `is_git_repo: False` |
| `test_inspect_finds_the_origin_and_github_repo` | `inspect()` extracts origin URL and derived `owner/repo` |
| `test_inspect_detects_existing_agentshowdown_files` | Detects `.agentshowdown/config.yaml` presence, features file absence |
| `test_parse_github_repo` (6 cases) | `parse_github_repo` extracts `owner/repo` from https/git@/ssh forms, `None` otherwise |
| `test_config_path_prefers_the_env_override` | `CONFIG_ENV` override always wins over active workspace |
| `test_config_path_follows_the_active_workspace` | No env override → paths follow the selected workspace's `.agentshowdown/` dir |
| `test_config_path_falls_back_to_the_bundled_demo` | Neither env nor active workspace → falls back to bundled demo config |
| `test_load_config_pins_the_shared_jobs_database` | `load_config()` always overrides `state_db_path` to the single shared jobs database |

---

## Frontend (`frontend/src/`, Vitest + Testing Library)

### frontend/src/api.test.ts
Tests: `frontend/src/api.ts` — the typed fetch client.

- **request handling**: prefixes calls with `/api`; sets JSON content-type only when there's a body; surfaces the server's `detail` message on error; falls back to status text when the error body isn't JSON; reports the status code via `ApiError`; URI-encodes ids in the path.
- **endpoints unwrap their envelope**: each fetch function (agents/features/runs/sandboxes/samples) unwraps its `{key: [...]}` envelope into a flat array.
- **mutations**: `pingSandbox` resolves with its payload; `removeSandbox` resolves with removed ids; `killAllSandboxes` POSTs the exact confirmation phrase.
- **openEventStream**: the returned stream's `.url` uses the `/api` prefix.
- **ApiError**: constructing it sets `.status`/`.name` correctly.

### frontend/src/App.test.tsx
Tests: `App` — root component.

- **App**: renders the masthead; shows a job card once jobs load; renders 3 comparison charts alongside a full data table; reports environment ready when preflight passes; alerts/blocks launching when preflight fails; says "nothing has run yet" for empty jobs.
- **App sections**: shows the Compare tab first; switches to Setup on click; offers a repo filter only when jobs span multiple repos; narrows the board to one repo when filtered.

### frontend/src/charts/ComparisonSpread.test.tsx
Tests: `ComparisonSpread` — multi-metric bar-chart comparison.

- Renders one chart per metric instead of combining scales; includes a legend with 2+ series; omits the legend for a single series; labels an unreported metric "no data" instead of a zero bar; describes each chart via `aria-label`; shows a prompt instead of empty axes with zero jobs; direct-labels every bar with visible text.

### frontend/src/charts/palette.test.ts
Tests: `charts/palette.ts` — series-key derivation and color assignment.

- **seriesKey**: same agent under two run labels is two series; bare agent id used when no run label.
- **assignSeriesColors**: distinct palette slot per series; color stays attached to its agent when the view is filtered; explicitly documents that recomputing colors from a filtered subset is the anti-pattern to avoid; color assignment doesn't depend on array order; series past the palette cap fold into one overflow color.

### frontend/src/charts/TelemetryChart.test.tsx
Tests: `TelemetryChart` — CPU/memory sandbox telemetry chart.

- Explains the cost when telemetry is off instead of an empty chart; renders CPU and memory as separate charts; shows the memory limit as denominator when set; waits rather than plotting with no samples; tolerates unreported readings without crashing.

### frontend/src/components/BrandMark.test.tsx
Tests: `BrandMark` — decorative logo SVG.

- Stays out of the accessibility tree; draws one sword per contender using series-color theme tokens; renders at the requested size.

### frontend/src/components/ConfigEditor.test.tsx
Tests: `ConfigEditor` — repo config form.

- Prefills from the saved config; shows "no configuration saved" when unsaved; does not load demo-seeded draft values into a fresh form (regression test, ⚠️ see overlap #2); offers detected remote/default model as placeholders; reports loaded doc and marks it saved after save; marks the bundled demo read-only and blocks saving; saves edited values via PUT; requires typed "RUN ON HOST" confirmation for host-run test commands; skips confirmation when tests run in the sandbox; surfaces a rejected save.

### frontend/src/components/FeatureEditor.test.tsx
Tests: `FeatureEditor` — features.yaml editing form.

- Prefills from existing features; adds/removes a feature (⚠️ see overlap #1); adds an agent with the full spec available (⚠️ see overlap #1); reveals the Command field behind Advanced; edits env vars as key/value pairs (⚠️ see overlap #1); posts edited features with env carried through; surfaces a rejected save as an alert.

### frontend/src/components/featuresReducer.test.ts
Tests: `featuresReducer`/`emptyAgent`/`emptyFeature` — pure reducer backing `FeatureEditor`.

- Loads a feature list; adds/removes features (⚠️ see overlap #1); edits a field without touching sibling state; adds/edits/removes acceptance criteria; adds/removes agents (⚠️ see overlap #1); keeps `agent_id` a string but blanks other empty fields to null; sets env/kit/skip-permissions on an agent (⚠️ see overlap #1); `emptyAgent()` defaults to a sane agent/env.

### frontend/src/components/IssueImporter.test.tsx
Tests: `IssueImporter` — imports GitHub issues into features.

- Lists issues as checkboxes once loaded; imports only the selected issues; disables import with nothing selected; says "no open issues" when empty; surfaces an unavailable `gh` CLI as an alert.

### frontend/src/components/JobCard.test.tsx
Tests: `JobCard` — single job summary card.

- **JobCard**: shows agent/status/summed tokens; renders unreported metrics as "not reported", never zero (⚠️ see overlap #3); distinguishes runs of the same agent by run label; links to the PR only when one was opened.
- **JobCard partial metrics**: counts a reported half even when the other half is missing; shows a diff with only additions; marks missing duration/model/branch as not reported; formats sub-minute duration in seconds; omits the Inspect button when no handler is given.

### frontend/src/components/PreflightBanner.test.tsx
Tests: `PreflightBanner` — environment-readiness banner.

- Reports readiness when every check passes (⚠️ see overlap #4); names the failing check as an alert (⚠️ see overlap #4); renders nothing before preflight has loaded.

### frontend/src/components/RunLauncher.test.tsx
Tests: `RunLauncher` — run-launch form.

- Starts a run with the chosen feature/agents; disables launch with no agent selected; stays disabled when the environment can't run sandboxes; marks agents with unverified flags / no token data; omits agents with no native builder; states the cost of telemetry and of opening a PR; surfaces a rejected launch as an alert.

### frontend/src/components/SandboxControls.test.tsx
Tests: `SandboxControls` — sandbox ping/wipe controls.

- **SandboxControls**: keeps wipe disabled until the exact phrase is typed; warns the wipe reaches sandboxes this app didn't create; reports a live agent from a ping; distinguishes a dead agent from a gone container; says so when nothing is running.
- **failure paths**: alerts when a ping fails; reports a sandbox that's already gone; reports how many jobs a wipe invalidated; alerts when the wipe itself fails.

### frontend/src/components/SecretModal.test.tsx
Tests: `SecretModal` — "store a secret" dialog.

- Reachable as a dialog by its accessible name (⚠️ see overlap #6); moves focus to the first field on open; defaults to the command method (never stores the value here); warns that a pasted token is less secure; posts the chosen method; closes on Escape; closes on Cancel (⚠️ see overlap #6); surfaces a server error without closing.

### frontend/src/components/SecretsManager.test.tsx
Tests: `SecretsManager` — lists stored secrets, opens `SecretModal`.

- Lists which services have a secret stored; reports what it loaded via `onSecretsChange`; says so when nothing is stored; warns when the sbx secret store can't be read; re-reads the list after the store dialog closes (⚠️ see overlap #6).

### frontend/src/components/Standings.test.tsx
Tests: `Standings` — sortable leaderboard table.

- Lists every job in placement order by default; sorts by column and reverses on a second click; shows an unreported metric as a dash, never zero (⚠️ see overlap #3); reports test/lint results as pass or fail.

### frontend/src/components/ThemeSwitch.test.tsx
Tests: `ThemeSwitch` — system/light/dark toggle.

- Offers system/light/dark as a labelled group; marks exactly the current choice as pressed; moves the pressed state to the clicked button; adds no images that would be announced.

### frontend/src/components/ui/Field.test.tsx
Tests: `Field` — label+hint wrapper.

- Labels the control it wraps; shows a hint only when given one.

### frontend/src/components/ui/Notice.test.tsx
Tests: `Notice` — tone-based inline message.

- Announces a "danger" tone as an alert; announces a "warn" tone as an alert; announces an "ok" tone as status, not alert.

### frontend/src/components/ui/Panel.test.tsx
Tests: `Panel` — collapsible section wrapper.

- Names the section with heading/status/summary text; starts collapsed unless asked to open; opens/closes from its summary; keeps collapsed content mounted so form state survives; reads `defaultOpen` once, so late data never snaps an open panel shut.

### frontend/src/components/ui/StatusChip.test.tsx
Tests: `StatusChip` — status pill.

- Carries its meaning in text regardless of tone (not color alone).

### frontend/src/components/WorkspacePicker.test.tsx
Tests: `WorkspacePicker` — repo selection (local path / clone / switching).

- **WorkspacePicker**: offers both local-path and clone-URL modes; swaps the input field when clone mode is chosen; reports what inspecting found; surfaces a failed inspection as an alert; selects a repo and tells its parent; shows a progress indicator while cloning; lists known repositories with a switch action.
- **clone progress**: clears the progress bar once the clone finishes; reports a failed clone instead of spinning forever.

### frontend/src/hooks/useJobStream.test.ts
Tests: `useJobStream` — initial fetch + SSE reconnection/resync hook.

- Loads jobs on mount; refetches full state when a `job_changed` event arrives; reconciles over HTTP on every reconnect, not just the first; treats a `resync` event as a full refetch; reports disconnection without dropping already-held jobs; closes the stream on unmount; surfaces a load failure instead of showing an empty list.

### frontend/src/lib/config.test.ts
Tests: `withoutDraftValues` (lib/config.ts) — strips demo-preset values from a config.

- Clears every field describing the demo rather than the real repo (⚠️ see overlap #2); keeps generic settings and plumbing fields a save must round-trip; does not mutate the draft object it was given.

### frontend/src/lib/jobs.test.ts
Tests: `lib/jobs.ts` — placement ranking, null-safe comparison, metric formatting.

- **placements**: ranks a finished run above a faster failed one; breaks ties on tests-passed then duration; restarts numbering per feature; never treats a missing duration as instant.
- **compareNullableAscending**: orders values ascending with nulls last; two nulls compare equal.
- **formatters**: keeps a missing metric distinct from zero (⚠️ see overlap #3); formats durations/diffs for reading.
- **statusTone**: maps each status family to the right tone.

### frontend/src/lib/theme.test.ts
Tests: `lib/theme.ts` storage helpers, `useTheme` hook, consistency with `index.html`'s pre-paint script.

- **theme storage**: defaults to "system" when nothing saved; remembers an explicit choice; stores nothing for "system" (clears the key); ignores an unrecognized stored value; falls back to "system" when storage read is blocked; still applies a choice when it can't be saved.
- **applyTheme**: stamps an explicit choice on `<html>`, clears it for "system".
- **useTheme**: starts from the saved choice, applies and saves changes.
- **index.html pre-paint script**: reads the same storage key as the app module (guards drift between the anti-FOUC script and the app).

### frontend/src/views/SetupView.test.tsx
Tests: `SetupView` — Setup tab panel orchestration.

- Opens only the Repository panel when nothing is selected; collapses everything and says "Ready to run" once the workspace is ready; flags a configuration that hasn't been saved yet.

---

## Overlap / duplicate-coverage summary

### Backend

1. **`EventBus.replay_since` gap/resync behavior tested twice, almost identically**, in `tests/orchestrator/test_events.py` (`test_replay_returns_only_events_after_the_last_seen_id`, `test_replay_signals_resync_when_the_gap_is_too_large`) and `tests/test_sse.py` (`test_replay_delivers_exactly_what_was_missed`, `test_a_client_gone_too_long_is_told_to_resync`). Same mechanism, different parameters only. `test_sse.py` doesn't need to re-verify `EventBus` internals since `test_events.py` covers them exhaustively (plus buffer-boundary and bounded-size cases with no SSE-side counterpart) — worth consolidating so `test_sse.py` focuses only on `stream.py`-specific behavior.
2. **`sbx list --json` "uses the JSON form" checked at two layers** — `test_sbx.py::test_sbx_list_uses_the_host_side_json_listing` (unit, exact argv) and `test_api.py::test_sandboxes_listing_uses_the_json_form` (integration, through `/sandboxes`). Legitimate unit-vs-integration layering, not a true duplicate, but the near-identical names are worth a comment noting the API test intentionally re-verifies through the HTTP layer.
3. **`verify_on=sandbox` exec-count checked at overlapping angles** — `test_job.py::test_finalize_success_verify_on_sandbox_uses_the_sandbox` (stubbed at the function level, count == 2) and `test_sandbox_cost.py::test_verify_on_sandbox_costs_one_extra_exec` (stubbed at the subprocess level, count == 4, includes launch+status execs). Legitimately distinct in scope but a reviewer fixing an exec-count regression needs to update both.
4. **Duplicate helper definition within a single file**: `tests/orchestrator/test_job.py` defines `_ok_process()` twice (once near the top with no args, again later with a `stdout` parameter) — the second silently shadows the first. Not a duplicate test, but duplicate setup code worth consolidating to avoid an ordering-sensitive footgun.
5. **Model/provider/env/kit/skip-permissions precedence unit-tested in `test_config.py`, then re-verified end-to-end in `test_job.py`** (e.g. `test_resolve_model_profile_wins_over_config_when_spec_unset` vs. `test_run_job_resolves_model_and_kit_from_agent_profile`/`test_run_job_spec_model_overrides_agent_profile`). Sound belt-and-suspenders layering (unit + wiring), not wasteful, but worth recognizing as intentional rather than accidental overlap.

No exact duplicate test bodies (same assertions, same inputs, same file) were found in the backend suite.

### Frontend

1. **`FeatureEditor.test.tsx` vs. `featuresReducer.test.ts` — the most significant overlap.** The reducer test file exists explicitly to cover "the branchiest part of the editor, with no rendering" (add/remove feature, add/remove agent, env/kit/skip-permissions edits). `FeatureEditor.test.tsx` re-exercises much of the same branching through the rendered UI. Likely intentional (unit + integration), but a bug in the reducer's env handling would be caught redundantly by both.
2. **`ConfigEditor.test.tsx`'s demo-draft regression test vs. `lib/config.test.ts` (`withoutDraftValues`)**. The component test re-verifies, through a full render + submit, essentially the same demo-value-clearing behavior the pure function already covers in isolation. The component test legitimately guards the *wiring* of the function into the form, but the clearing logic itself is tested twice.
3. **Null/unreported-metric formatting tested at three layers**: `lib/jobs.test.ts` unit-tests the formatters returning `null`; `JobCard.test.tsx` re-verifies via rendered "not reported" text; `Standings.test.tsx` re-verifies again via a rendered "—" dash. Reasonable defense-in-depth (each component renders nulls differently), but a regression in the shared formatting helpers would be flagged three times, while a presentation-only bug (wrong dash character) would only ever be caught by the two component tests.
4. **`App.test.tsx` duplicates `PreflightBanner.test.tsx`** — App's ready/failed-preflight assertions repeat, at the integration level, what `PreflightBanner.test.tsx` already covers in isolation. Standard smoke-test-at-integration-level practice.
5. **`App.test.tsx` duplicates `ComparisonSpread.test.tsx`** on the "3 charts" assertion — App's own test adds new coverage (a data table alongside the charts) but restates a check the chart component's own suite already makes.
6. **`SecretsManager.test.tsx`'s "re-reads the list after the store dialog closes" incidentally re-exercises `SecretModal`'s already-tested dialog/cancel semantics.** The unique behavior under test (refetch after close) is real and non-duplicated; the modal open/cancel mechanics it touches along the way are covered again.

No outright identical frontend test cases were found — all overlaps are the same underlying behavior verified at two different layers (pure function vs. component, component vs. parent).

### Cross-stack note

Several behaviors are guarded on both sides of the stack by design rather than accident — e.g. "unreported metric never renders/serializes as zero" is enforced in the backend (`test_state.py::test_unreported_sample_fields_stay_null`, `test_metrics.py::test_missing_fields_stay_none_rather_than_zero`) and independently in the frontend (`lib/jobs.test.ts`, `JobCard.test.tsx`, `Standings.test.tsx`). This isn't wasted effort — a null can be turned into a false zero at either the Python layer or the TypeScript layer — but it's worth knowing both halves must be kept in sync if the "never show zero for missing data" convention ever changes.
