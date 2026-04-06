from aflow._test_support import *  # noqa: F401,F403

class AdaptersTests(unittest.TestCase):

    def test_codex_without_effort(self) -> None:
        adapter = CodexAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='gpt-5.4', system_prompt='SYSTEM', user_prompt='USER')
        assert invocation.argv == ('codex', 'exec', '--dangerously-bypass-approvals-and-sandbox', '-C', '/repo', '--model', 'gpt-5.4', 'SYSTEM\n\nUSER')
        assert invocation.prompt_mode == 'prefix-system-into-user-prompt'
        assert invocation.effective_prompt == 'SYSTEM\n\nUSER'

    def test_codex_with_effort(self) -> None:
        adapter = CodexAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='gpt-5.4', system_prompt='SYSTEM', user_prompt='USER', effort='high')
        argv = invocation.argv
        assert '-c' in argv
        assert 'model_reasoning_effort=\'high\'' in argv
        prompt_index = argv.index('SYSTEM\n\nUSER')
        assert argv[prompt_index - 2] == '-c'
        assert argv[-1] == 'SYSTEM\n\nUSER'

    def test_codex_effort_preserves_prompt_as_final_element(self) -> None:
        adapter = CodexAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='gpt-5.4', system_prompt='PROMPT', user_prompt='INSTRUCTIONS', effort='low')
        assert invocation.argv[-1] == 'PROMPT\n\nINSTRUCTIONS'

    def test_codex_without_model_omits_model_flag(self) -> None:
        adapter = CodexAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model=None, system_prompt='SYSTEM', user_prompt='USER')
        assert '--model' not in invocation.argv
        assert invocation.argv[-1] == 'SYSTEM\n\nUSER'

    def test_copilot_without_effort(self) -> None:
        adapter = CopilotAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='gpt-5.4', system_prompt='SYSTEM', user_prompt='USER')
        assert invocation.argv == ('copilot', '-p', 'SYSTEM\n\nUSER', '-s', '--allow-all', '--no-ask-user', '--model', 'gpt-5.4')
        assert invocation.prompt_mode == 'prefix-system-into-user-prompt'
        assert invocation.effective_prompt == 'SYSTEM\n\nUSER'

    def test_copilot_with_effort(self) -> None:
        adapter = CopilotAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='gpt-5.4', system_prompt='SYSTEM', user_prompt='USER', effort='high')
        argv = invocation.argv
        assert '--reasoning-effort' in argv
        assert 'high' in argv
        assert argv[-2:] == ('--reasoning-effort', 'high')

    def test_copilot_without_model_omits_model_flag(self) -> None:
        adapter = CopilotAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model=None, system_prompt='SYSTEM', user_prompt='USER')
        assert '--model' not in invocation.argv
        assert invocation.argv[:6] == ('copilot', '-p', 'SYSTEM\n\nUSER', '-s', '--allow-all', '--no-ask-user')

    def test_copilot_without_model_and_with_effort_uses_reasoning_effort_flag(self) -> None:
        adapter = CopilotAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model=None, system_prompt='SYSTEM', user_prompt='USER', effort='low')
        argv = invocation.argv
        assert '--model' not in argv
        assert '--reasoning-effort' in argv
        assert argv[-2:] == ('--reasoning-effort', 'low')

    def test_pi_without_effort(self) -> None:
        adapter = PiAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='sonnet', system_prompt='SYSTEM', user_prompt='USER')
        assert invocation.argv == ('pi', '--print', '--system-prompt', 'SYSTEM', '--model', 'sonnet', '--tools', 'read,bash,edit,write,grep,find,ls', 'USER')
        assert invocation.prompt_mode == 'system-prompt-flag'

    def test_pi_with_effort(self) -> None:
        adapter = PiAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='sonnet', system_prompt='SYSTEM', user_prompt='USER', effort='high')
        argv = invocation.argv
        assert '--models' in argv
        assert 'sonnet:high' in argv
        assert '--model' not in argv
        models_index = argv.index('--models')
        assert argv[models_index + 1] == 'sonnet:high'

    def test_pi_with_effort_does_not_pass_both_model_flags(self) -> None:
        adapter = PiAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='sonnet', system_prompt='S', user_prompt='U', effort='high')
        assert '--models' in invocation.argv
        assert '--model' not in invocation.argv

    def test_pi_without_model_and_with_effort_uses_thinking_flag(self) -> None:
        adapter = PiAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model=None, system_prompt='SYSTEM', user_prompt='USER', effort='high')
        argv = invocation.argv
        assert '--thinking' in argv
        assert 'high' in argv
        assert '--models' not in argv
        assert '--model' not in argv

    def test_pi_without_model_and_without_effort_omits_model_flags(self) -> None:
        adapter = PiAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model=None, system_prompt='SYSTEM', user_prompt='USER')
        assert '--model' not in invocation.argv
        assert '--models' not in invocation.argv
        assert '--thinking' not in invocation.argv

    def test_claude_without_effort(self) -> None:
        adapter = ClaudeAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='claude-sonnet-4-6', system_prompt='SYSTEM', user_prompt='USER')
        assert '--effort' not in invocation.argv
        assert invocation.argv == ('claude', '-p', '--system-prompt', 'SYSTEM', '--model', 'claude-sonnet-4-6', '--permission-mode', 'bypassPermissions', '--dangerously-skip-permissions', '--tools=default', 'USER')

    def test_claude_with_effort(self) -> None:
        adapter = ClaudeAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='claude-sonnet-4-6', system_prompt='SYSTEM', user_prompt='USER', effort='low')
        argv = invocation.argv
        assert '--effort' in argv
        assert 'low' in argv
        effort_index = argv.index('--effort')
        assert argv[effort_index + 1] == 'low'

    def test_claude_without_model_omits_model_flag(self) -> None:
        adapter = ClaudeAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model=None, system_prompt='SYSTEM', user_prompt='USER')
        assert '--model' not in invocation.argv
        assert invocation.argv[0] == 'claude'

    def test_opencode_without_effort(self) -> None:
        adapter = OpencodeAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='glm-5-turbo', system_prompt='SYSTEM', user_prompt='USER')
        assert invocation.argv == ('opencode', 'run', '--dangerously-skip-permissions', '--model', 'glm-5-turbo', '--format', 'default', '--dir', '/repo', 'SYSTEM\n\nUSER')
        assert invocation.prompt_mode == 'prefix-system-into-user-prompt'
        assert invocation.effective_prompt == 'SYSTEM\n\nUSER'

    def test_opencode_with_effort_ignores_effort(self) -> None:
        adapter = OpencodeAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='glm-5-turbo', system_prompt='SYSTEM', user_prompt='USER', effort='high')
        assert not adapter.supports_effort
        argv = invocation.argv
        assert 'effort' not in ' '.join(argv).lower()
        assert argv == ('opencode', 'run', '--dangerously-skip-permissions', '--model', 'glm-5-turbo', '--format', 'default', '--dir', '/repo', 'SYSTEM\n\nUSER')

    def test_opencode_without_model_omits_model_flag(self) -> None:
        adapter = OpencodeAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model=None, system_prompt='SYSTEM', user_prompt='USER')
        assert '--model' not in invocation.argv
        assert invocation.argv[0] == 'opencode'

    def test_gemini_without_effort(self) -> None:
        adapter = GeminiAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='gemini-2.5-pro', system_prompt='SYSTEM', user_prompt='USER')
        assert invocation.argv == ('gemini', '--prompt', 'SYSTEM\n\nUSER', '--model', 'gemini-2.5-pro', '--approval-mode', 'yolo', '--sandbox=false', '--output-format', 'text')
        assert invocation.prompt_mode == 'prefix-system-into-user-prompt'
        assert invocation.effective_prompt == 'SYSTEM\n\nUSER'

    def test_gemini_with_effort_ignores_effort(self) -> None:
        adapter = GeminiAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='gemini-2.5-pro', system_prompt='SYSTEM', user_prompt='USER', effort='high')
        assert not adapter.supports_effort
        argv = invocation.argv
        assert 'effort' not in ' '.join(argv).lower()
        assert argv == ('gemini', '--prompt', 'SYSTEM\n\nUSER', '--model', 'gemini-2.5-pro', '--approval-mode', 'yolo', '--sandbox=false', '--output-format', 'text')

    def test_gemini_without_model_omits_model_flag(self) -> None:
        adapter = GeminiAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model=None, system_prompt='SYSTEM', user_prompt='USER')
        assert '--model' not in invocation.argv
        assert invocation.argv[0] == 'gemini'

    def test_kiro_without_effort(self) -> None:
        adapter = KiroAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='kiro-model', system_prompt='SYSTEM', user_prompt='USER')
        assert not adapter.supports_effort
        assert invocation.argv == ('kiro-cli', 'chat', '--no-interactive', '--trust-all-tools', '--model', 'kiro-model', 'SYSTEM\n\nUSER')
        assert invocation.prompt_mode == 'prefix-system-into-user-prompt'
        assert invocation.effective_prompt == 'SYSTEM\n\nUSER'

    def test_kiro_without_model_omits_model_flag(self) -> None:
        adapter = KiroAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model=None, system_prompt='SYSTEM', user_prompt='USER')
        assert '--model' not in invocation.argv
        assert invocation.argv == ('kiro-cli', 'chat', '--no-interactive', '--trust-all-tools', 'SYSTEM\n\nUSER')

    def test_kiro_ignores_effort(self) -> None:
        adapter = KiroAdapter()
        invocation = adapter.build_invocation(repo_root=Path('/repo'), model='kiro-model', system_prompt='SYSTEM', user_prompt='USER', effort='high')
        assert not adapter.supports_effort
        assert 'effort' not in ' '.join(invocation.argv).lower()
        assert invocation.argv == ('kiro-cli', 'chat', '--no-interactive', '--trust-all-tools', '--model', 'kiro-model', 'SYSTEM\n\nUSER')


class RetentionTests(unittest.TestCase):

    def test_retention_prune_old_runs_keeps_newest_twenty_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runs_root = Path(tmpdir)
            for index in range(23):
                run_dir = runs_root / f'20260329T120000Z-{22 - index:08x}'
                run_dir.mkdir()
                mtime_ns = 1700000000000000000 + index * 1000000
                os.utime(run_dir, ns=(mtime_ns, mtime_ns))
            prune_old_runs(runs_root, keep_runs=20)
            remaining = sorted((path.name for path in runs_root.iterdir()))
            assert len(remaining) == 20
            assert remaining == sorted((f'20260329T120000Z-{22 - index:08x}' for index in range(3, 23)))


class GitStatusTests(unittest.TestCase):

    def setUp(self) -> None:
        from aflow.git_status import capture_baseline, probe_worktree, summarize_since_baseline
        self._capture_baseline = capture_baseline
        self._probe_worktree = probe_worktree
        self._summarize_since_baseline = summarize_since_baseline

    def test_probe_worktree_clean_returns_not_dirty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            result = self._probe_worktree(repo)
            assert result is not None
            assert result.is_dirty is False
            assert result.modified_count == 0
            assert result.added_count == 0
            assert result.removed_count == 0

    def test_probe_worktree_modified_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            result = self._probe_worktree(repo)
            assert result is not None
            assert result.is_dirty is True
            assert result.modified_count == 1

    def test_probe_worktree_added_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            (repo / "new.py").write_text("x = 1\n", encoding="utf-8")
            result = self._probe_worktree(repo)
            assert result is not None
            assert result.is_dirty is True
            assert result.added_count >= 1

    def test_capture_baseline_returns_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            baseline = self._capture_baseline(repo)
            assert baseline is not None
            assert baseline.head_sha is not None
            assert len(baseline.tree_oid) == 40

    def test_summarize_clean_baseline_is_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            baseline = self._capture_baseline(repo)
            assert baseline is not None
            summary = self._summarize_since_baseline(repo, baseline)
            assert summary is not None
            assert summary.modified_count == 0
            assert summary.added_count == 0
            assert summary.removed_count == 0
            assert summary.lines_added == 0
            assert summary.lines_removed == 0
            assert summary.commit_count == 0
            assert summary.changed_paths == ()

    def test_summarize_modified_file_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            baseline = self._capture_baseline(repo)
            assert baseline is not None
            (repo / "README.md").write_text("line1\nline2\n", encoding="utf-8")
            summary = self._summarize_since_baseline(repo, baseline)
            assert summary is not None
            assert summary.modified_count == 1
            assert summary.added_count == 0
            assert summary.removed_count == 0
            assert "README.md" in summary.changed_paths

    def test_summarize_added_file_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            baseline = self._capture_baseline(repo)
            assert baseline is not None
            (repo / "new.py").write_text("x = 1\n", encoding="utf-8")
            summary = self._summarize_since_baseline(repo, baseline)
            assert summary is not None
            assert summary.added_count == 1
            assert summary.lines_added >= 1
            assert "new.py" in summary.changed_paths

    def test_summarize_deleted_file_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            baseline = self._capture_baseline(repo)
            assert baseline is not None
            (repo / "README.md").unlink()
            summary = self._summarize_since_baseline(repo, baseline)
            assert summary is not None
            assert summary.removed_count == 1

    def test_summarize_commit_after_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            baseline = self._capture_baseline(repo)
            assert baseline is not None
            (repo / "new.py").write_text("x = 1\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-m", "add new"], check=True, capture_output=True)
            summary = self._summarize_since_baseline(repo, baseline)
            assert summary is not None
            assert summary.commit_count == 1

    def test_summarize_dirty_at_start_reports_only_post_baseline_delta(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            (repo / "pre.py").write_text("pre = 1\n", encoding="utf-8")
            baseline = self._capture_baseline(repo)
            assert baseline is not None
            (repo / "post.py").write_text("post = 1\n", encoding="utf-8")
            summary = self._summarize_since_baseline(repo, baseline)
            assert summary is not None
            assert "post.py" in summary.changed_paths
            assert "pre.py" not in summary.changed_paths

    def test_summarize_returns_to_baseline_shows_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            original_content = (repo / "README.md").read_text(encoding="utf-8")
            baseline = self._capture_baseline(repo)
            assert baseline is not None
            (repo / "README.md").write_text("changed\n", encoding="utf-8")
            summary1 = self._summarize_since_baseline(repo, baseline)
            assert summary1 is not None
            assert summary1.modified_count == 1
            (repo / "README.md").write_text(original_content, encoding="utf-8")
            summary2 = self._summarize_since_baseline(repo, baseline)
            assert summary2 is not None
            assert summary2.modified_count == 0
            assert summary2.changed_paths == ()

    def test_capture_baseline_no_commits_returns_none_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True, capture_output=True)
            (repo / "f.txt").write_text("x\n", encoding="utf-8")
            baseline = self._capture_baseline(repo)
            assert baseline is not None
            assert baseline.head_sha is None

    def test_probe_returns_none_outside_git_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            result = self._probe_worktree(repo)
            assert result is None

    def test_classify_dirtiness_all_under_plans(self) -> None:
        from aflow.git_status import classify_dirtiness_by_prefix
        porcelain = "?? plans/a.txt\nM  plans/b.txt\nA  plans/c.txt\n"
        plan_paths, non_plan_paths = classify_dirtiness_by_prefix(porcelain)
        assert len(plan_paths) == 3
        assert len(non_plan_paths) == 0
        assert "plans/a.txt" in plan_paths
        assert "plans/b.txt" in plan_paths
        assert "plans/c.txt" in plan_paths

    def test_classify_dirtiness_all_outside_plans(self) -> None:
        from aflow.git_status import classify_dirtiness_by_prefix
        porcelain = "?? src/a.txt\nM  aflow/b.txt\nA  tests/c.txt\n"
        plan_paths, non_plan_paths = classify_dirtiness_by_prefix(porcelain)
        assert len(plan_paths) == 0
        assert len(non_plan_paths) == 3
        assert "src/a.txt" in non_plan_paths
        assert "aflow/b.txt" in non_plan_paths
        assert "tests/c.txt" in non_plan_paths

    def test_classify_dirtiness_mixed(self) -> None:
        from aflow.git_status import classify_dirtiness_by_prefix
        porcelain = "?? plans/a.txt\nM  src/b.txt\nA  plans/c.txt\nD  aflow/d.txt\n"
        plan_paths, non_plan_paths = classify_dirtiness_by_prefix(porcelain)
        assert len(plan_paths) == 2
        assert len(non_plan_paths) == 2
        assert "plans/a.txt" in plan_paths
        assert "plans/c.txt" in plan_paths
        assert "src/b.txt" in non_plan_paths
        assert "aflow/d.txt" in non_plan_paths

    def test_classify_dirtiness_rejects_similar_prefixes(self) -> None:
        from aflow.git_status import classify_dirtiness_by_prefix
        porcelain = "?? plans_backup/a.txt\nM  my-plans/b.txt\nA  xplans/c.txt\n"
        plan_paths, non_plan_paths = classify_dirtiness_by_prefix(porcelain)
        assert len(plan_paths) == 0
        assert len(non_plan_paths) == 3
        assert "plans_backup/a.txt" in non_plan_paths
        assert "my-plans/b.txt" in non_plan_paths
        assert "xplans/c.txt" in non_plan_paths

    def test_classify_dirtiness_empty_porcelain(self) -> None:
        from aflow.git_status import classify_dirtiness_by_prefix
        porcelain = ""
        plan_paths, non_plan_paths = classify_dirtiness_by_prefix(porcelain)
        assert len(plan_paths) == 0
        assert len(non_plan_paths) == 0


class RepoStateProbeTests(unittest.TestCase):

    def setUp(self) -> None:
        from aflow.git_status import probe_repo_state, RepoState
        self._probe_repo_state = probe_repo_state
        self._RepoState = RepoState

    def test_probe_repo_state_not_a_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            result = self._probe_repo_state(repo)
            assert result == self._RepoState.NOT_A_REPO

    def test_probe_repo_state_unborn(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            subprocess.run(
                ['git', 'init', '-b', 'main'], cwd=str(repo), check=True, capture_output=True
            )
            result = self._probe_repo_state(repo)
            assert result == self._RepoState.UNBORN

    def test_probe_repo_state_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _make_git_repo(repo)
            result = self._probe_repo_state(repo)
            assert result == self._RepoState.READY

    def test_probe_repo_state_no_git_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            with patch('aflow.git_status.shutil.which', return_value=None):
                result = self._probe_repo_state(repo)
            assert result == self._RepoState.NO_GIT_BINARY

    def test_probe_repo_state_file_not_found_is_no_git_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            with patch('subprocess.run', side_effect=FileNotFoundError):
                result = self._probe_repo_state(repo)
            assert result == self._RepoState.NO_GIT_BINARY

    def test_preflight_still_fails_when_committed_repo_main_branch_missing(self) -> None:
        """Committed repos with a missing main_branch must still fail after the split."""
        from aflow.workflow import run_workflow, WorkflowError
        from aflow.run_state import ControllerConfig
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = Path(tmpdir)
            _make_lifecycle_git_repo(repo_root, branch='main')
            plan_path = repo_root / 'plan.md'
            _write_plan(plan_path, _VALID_PLAN)
            _git_commit_file(repo_root, plan_path)
            wf_config = _make_branch_only_wf_config(main_branch='nonexistent')
            with pytest.raises(WorkflowError) as ctx:
                run_workflow(
                    ControllerConfig(repo_root=repo_root, plan_path=plan_path, max_turns=1),
                    wf_config, 'branch_wf', config_dir=repo_root,
                )
            assert 'nonexistent' in str(ctx.value)


class GitBannerTests(unittest.TestCase):

    def test_build_banner_renders_git_row_when_clean(self) -> None:
        from rich.console import Console
        from aflow.git_status import GitSummary
        state = ControllerState(last_snapshot=PlanSnapshot(None, 0, 0, False))
        summary = GitSummary(
            modified_count=0,
            added_count=0,
            removed_count=0,
            lines_added=0,
            lines_removed=0,
            commit_count=0,
            changed_paths=(),
        )
        panel = build_banner(
            config_max_turns=10,
            config_plan_path=Path("/fake/plan.md"),
            state=state,
            git_summary=summary,
        )
        assert panel is not None
        console = Console(record=True, width=100)
        console.print(panel)
        text = console.export_text()
        assert "Git" in text
        assert "clean since start" in text

    def test_build_banner_renders_git_row_with_changes(self) -> None:
        from rich.console import Console
        from aflow.git_status import GitSummary
        state = ControllerState(last_snapshot=PlanSnapshot(None, 0, 0, False))
        summary = GitSummary(
            modified_count=2,
            added_count=1,
            removed_count=0,
            lines_added=10,
            lines_removed=3,
            commit_count=1,
            changed_paths=("foo.py", "bar.py", "baz.py"),
        )
        panel = build_banner(
            config_max_turns=10,
            config_plan_path=Path("/fake/plan.md"),
            state=state,
            git_summary=summary,
        )
        assert panel is not None
        console = Console(record=True, width=120)
        console.print(panel)
        text = console.export_text()
        assert "Git" in text
        assert "M 2" in text
        assert "Files" in text
        assert "foo.py" in text

    def test_build_banner_files_row_respects_config_limit(self) -> None:
        from rich.console import Console
        from aflow.git_status import GitSummary
        state = ControllerState(last_snapshot=PlanSnapshot(None, 0, 0, False))
        summary = GitSummary(
            modified_count=5,
            added_count=0,
            removed_count=0,
            lines_added=0,
            lines_removed=0,
            commit_count=0,
            changed_paths=("a.py", "b.py", "c.py", "d.py", "e.py"),
        )
        panel = build_banner(
            config_max_turns=10,
            config_plan_path=Path("/fake/plan.md"),
            config_banner_files_limit=3,
            state=state,
            git_summary=summary,
        )
        assert panel is not None
        console = Console(record=True, width=120)
        console.print(panel)
        text = console.export_text()
        assert "+2 more" in text
        assert "d.py" not in text
        assert "a.py" in text

    def test_build_banner_no_git_summary_omits_git_rows(self) -> None:
        from rich.console import Console
        state = ControllerState(last_snapshot=PlanSnapshot(None, 0, 0, False))
        panel = build_banner(
            config_max_turns=10,
            config_plan_path=Path("/fake/plan.md"),
            state=state,
        )
        assert panel is not None
        console = Console(record=True, width=100)
        console.print(panel)
        text = console.export_text()
        assert "Git" not in text
        assert "Files" not in text

    def test_banner_renderer_refresh_thread_updates_live(self) -> None:
        import aflow.status as status_mod
        from unittest.mock import MagicMock, patch
        live_updates: list[object] = []

        class FakeLive:
            def __init__(self, panel, **kwargs: object) -> None:
                live_updates.append(panel)
            def start(self) -> None:
                pass
            def update(self, panel: object) -> None:
                live_updates.append(panel)
            def stop(self) -> None:
                pass

        state = ControllerState(last_snapshot=PlanSnapshot(None, 0, 0, False))
        with patch.object(status_mod, "_RICH_AVAILABLE", True), \
             patch.object(status_mod, "Live", FakeLive):
            renderer = status_mod.BannerRenderer(
                config_max_turns=10,
                config_plan_path=Path("/fake/plan.md"),
                refresh_interval_seconds=0.05,
                git_poll_interval_seconds=9999.0,
            )
            renderer.start(state)
            time.sleep(0.2)
            renderer.stop(state)
        assert len(live_updates) >= 3
