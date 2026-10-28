import subprocess
from unittest import mock

import project_agent


def _fail(stderr="gh: Not Found (HTTP 404)"):
    return subprocess.CalledProcessError(1, ["gh", "api", "x"], "", stderr)


def test_run_gh_command_error_is_called_process_error_with_stderr():
    with mock.patch("subprocess.run", side_effect=_fail()):
        try:
            project_agent.run_gh_command(["api", "x"])
        except subprocess.CalledProcessError as e:
            assert "404" in str(e)
        else:
            raise AssertionError("expected an error")


def test_missing_readme_returns_none():
    with mock.patch("subprocess.run", side_effect=_fail()):
        assert project_agent.fetch_readme("repo") is None


def test_missing_commits_returns_empty():
    with mock.patch("subprocess.run", side_effect=_fail()):
        assert project_agent.fetch_recent_commits("repo", project_agent.datetime.now()) == []


def test_humanizer_facts_hide_repo_urls():
    import humanizer
    text = humanizer._facts_text({"name": "x", "url": "https://github.com/a/b", "p": [{"url": "u"}]})
    assert "github.com" not in text and '"x"' in text
