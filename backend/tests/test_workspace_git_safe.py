"""safe.directory injection for workspace git reads (issue #30)."""
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import app.api.workspace as ws

GIT = shutil.which('git')
REPO_ROOT = Path(__file__).resolve().parents[2]


def _capture_run(calls):
    """Fake subprocess.run: record argv, answer like a healthy git."""
    def _run(cmd, **kwargs):
        calls.append(list(cmd))
        out = MagicMock()
        out.returncode = 0
        out.stdout = 'main\n'
        out.stderr = ''
        return out
    return _run


def _fake_repo(tmp_path):
    repo = tmp_path / 'repo'
    (repo / '.git').mkdir(parents=True)
    return repo


def _safe_values(cmd):
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == '-c']


def test_run_git_injects_safe_directory_with_realpath(tmp_path, monkeypatch):
    repo = _fake_repo(tmp_path)
    calls = []
    monkeypatch.setattr(ws.subprocess, 'run', _capture_run(calls))
    info = ws.git_info_for_workspace(repo)
    assert info is not None and info['branch'] == 'main'
    expected = f'safe.directory={os.path.realpath(repo)}'
    assert calls, 'git must have been invoked'
    for cmd in calls:
        assert cmd[0] == 'git'
        assert expected in _safe_values(cmd), f'missing exact {expected!r} in {cmd}'
        for v in _safe_values(cmd):
            assert not v.endswith('/'), 'no trailing slash (rc=128 in git 2.43.0)'
            assert '*' not in v, 'no wildcard (weakens ownership protection)'


def test_run_git_adds_gitdir_entry_when_dotgit_is_dir(tmp_path, monkeypatch):
    repo = _fake_repo(tmp_path)          # .git is a directory
    calls = []
    monkeypatch.setattr(ws.subprocess, 'run', _capture_run(calls))
    ws.git_info_for_workspace(repo)
    vals = _safe_values(calls[0])
    assert f'safe.directory={os.path.realpath(repo)}' in vals
    assert f'safe.directory={os.path.realpath(repo)}/.git' in vals


def test_run_git_skips_gitdir_entry_when_git_is_file(tmp_path, monkeypatch):
    repo = tmp_path / 'wt'
    repo.mkdir()
    (repo / '.git').write_text('gitdir: /elsewhere/.git/worktrees/wt\n')
    calls = []
    monkeypatch.setattr(ws.subprocess, 'run', _capture_run(calls))
    ws.git_info_for_workspace(repo)
    vals = _safe_values(calls[0])
    assert f'safe.directory={os.path.realpath(repo)}' in vals
    assert not any(v.endswith('/.git') for v in vals), 'no gitdir entry for a .git file'


def test_run_git_injects_realpath_for_symlinked_workspace(tmp_path, monkeypatch):
    repo = _fake_repo(tmp_path)
    link = tmp_path / 'link'
    link.symlink_to(repo)
    calls = []
    monkeypatch.setattr(ws.subprocess, 'run', _capture_run(calls))
    ws.git_info_for_workspace(link)
    expected = f'safe.directory={os.path.realpath(repo)}'
    assert calls
    for cmd in calls:
        assert expected in _safe_values(cmd)
        assert f'safe.directory={link}' not in cmd, 'unresolved symlink path must not be injected'


def test_workspace_info_log_injects_safe_directory(tmp_path, monkeypatch):
    repo = _fake_repo(tmp_path)
    calls = []
    monkeypatch.setattr(ws.subprocess, 'run', _capture_run(calls))
    overview = ws.workspace_info(repo)
    assert overview['git'] is not None
    log_calls = [c for c in calls if 'log' in c]
    assert log_calls, 'workspace_info must run git log'
    for cmd in log_calls:
        assert f'safe.directory={os.path.realpath(repo)}' in _safe_values(cmd)


@pytest.mark.skipif(GIT is None, reason='git not installed')
@pytest.mark.skipif(os.geteuid() != 0, reason='needs root to chown')
def test_dubious_ownership_repo_shows_branch_and_log(tmp_path):
    """End-to-end repro of issue #30; must fail before the fix."""
    if os.path.realpath(tmp_path).startswith(os.path.realpath(REPO_ROOT)):
        pytest.skip('repro repo must live outside the checkout (CI whitelists $GITHUB_WORKSPACE)')
    if shutil.which('chown') is None:
        pytest.skip('chown not available')
    repo = tmp_path / 'foreign'
    repo.mkdir()
    subprocess.run([GIT, 'init', '-q', '-b', 'main', str(repo)],
                   check=True, capture_output=True)
    subprocess.run(
        [GIT, '-c', 'user.email=t@t', '-c', 'user.name=t',
         'commit', '--allow-empty', '-m', 'init'],
        cwd=str(repo), check=True, capture_output=True,
    )

    def _global_safe():
        return subprocess.run(
            [GIT, 'config', '--global', '--get-all', 'safe.directory'],
            capture_output=True, text=True,
        ).stdout

    before = _global_safe()
    subprocess.run(['chown', '-R', '65534:65534', str(repo)], check=True)

    info = ws.git_info_for_workspace(repo)
    assert info is not None, 'issue #30: foreign-owned repo must yield git info without manual git config'
    assert info['branch'] == 'main'
    overview = ws.workspace_info(repo)
    assert overview['git'], 'workspace_info must carry git data'
    assert overview['git']['log'], 'commit history must be readable'
    assert overview['git']['log'][0]['message'] == 'init'
    assert _global_safe() == before, 'the fix must not write gitconfig'


def test_normal_owner_repo_git_info_unchanged(tmp_path):
    if GIT is None:
        pytest.skip('git not installed')
    repo = tmp_path / 'owned'
    repo.mkdir()
    subprocess.run([GIT, 'init', '-q', '-b', 'main', str(repo)],
                   check=True, capture_output=True)
    subprocess.run(
        [GIT, '-c', 'user.email=t@t', '-c', 'user.name=t',
         'commit', '--allow-empty', '-m', 'init'],
        cwd=str(repo), check=True, capture_output=True,
    )
    info = ws.git_info_for_workspace(repo)
    assert info is not None
    assert info['branch'] == 'main'
    assert info['is_git'] is True
    (repo / 'new.txt').write_text('x')
    info2 = ws.git_info_for_workspace(repo)
    assert info2 is not None
    assert info2['untracked'] >= 1, 'dirty stats must keep working'
