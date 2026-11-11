import json
import threading
import urllib.request
from unittest import mock

import linkedin_auth
import project_agent


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def _run_flow(path_for_state, token_response=None, monkeypatch=None):
    httpd, url = linkedin_auth.start_oauth_server()
    port = httpd.server_address[1]
    holder = {}
    t = threading.Thread(target=lambda: holder.update(linkedin_auth.wait_for_callback(httpd, 20)))
    t.start()
    status = _get(f"http://127.0.0.1:{port}{path_for_state(httpd.expected_state)}")
    t.join(25)
    return url, status, holder


def test_oauth_flow_success_saves_token(monkeypatch):
    monkeypatch.setattr(linkedin_auth, "_callback_port", lambda: 0)
    saved = {}
    monkeypatch.setattr(linkedin_auth, "exchange_code_for_token",
                        lambda code: {"access_token": "T-" + code, "expires_in": 86400})
    monkeypatch.setattr(linkedin_auth, "save_token_response",
                        lambda data: saved.update(data) or "2026-11-12")
    url, status, result = _run_flow(lambda st: f"/callback?code=abc&state={st}")
    assert "state=" in url and status == 200
    assert result == {"ok": True, "message": mock.ANY, "expires_at": "2026-11-12"}
    assert saved["access_token"] == "T-abc"


def test_oauth_flow_bad_state_keeps_waiting(monkeypatch):
    monkeypatch.setattr(linkedin_auth, "_callback_port", lambda: 0)
    httpd, _ = linkedin_auth.start_oauth_server()
    port = httpd.server_address[1]
    holder = {}
    t = threading.Thread(target=lambda: holder.update(linkedin_auth.wait_for_callback(httpd, 20)))
    t.start()
    assert _get(f"http://127.0.0.1:{port}/callback?code=x&state=forged") == 400
    assert _get(f"http://127.0.0.1:{port}/callback?error=user_cancelled_login") == 400
    t.join(25)
    assert holder["ok"] is False and "user_cancelled" in holder["message"]


def test_token_ok_false_on_401(monkeypatch):
    monkeypatch.setenv("LINKEDIN_TOKEN_EXPIRES_AT", "2099-01-01")
    with mock.patch.object(project_agent, "get_user_info",
                           side_effect=RuntimeError("LinkedIn API returned 401 Unauthorized")):
        assert project_agent.linkedin_token_ok() is False


def test_token_ok_true_on_network_error(monkeypatch):
    monkeypatch.setenv("LINKEDIN_TOKEN_EXPIRES_AT", "2099-01-01")
    with mock.patch.object(project_agent, "get_user_info", side_effect=ConnectionError("x")):
        assert project_agent.linkedin_token_ok() is True


def test_run_agent_defers_post_when_token_dead(tmp_path, monkeypatch):
    pending_file = tmp_path / "pending_post.json"
    pending_file.write_text(json.dumps(
        {"repo": "r", "post_de": "de", "post_uk": "uk", "status": "approved"}))
    monkeypatch.setattr(project_agent, "PENDING_POST_FILE", pending_file)
    monkeypatch.setattr(project_agent, "linkedin_token_ok", lambda: False)
    sent = []
    monkeypatch.setattr(project_agent, "send_telegram", lambda *a, **k: sent.append((a, k)))
    publish = mock.Mock()
    monkeypatch.setattr(project_agent, "_publish", publish)

    project_agent.run_agent()

    publish.assert_not_called()
    kept = json.loads(pending_file.read_text())
    assert kept["deferred_auth"] is True and kept["post_de"] == "de"
    assert sent[0][1]["buttons"][0][0]["callback_data"] == "token_renew"
