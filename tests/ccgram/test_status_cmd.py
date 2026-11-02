"""Tests for ccgram status command."""

import contextlib

from ccgram.status_cmd import _list_managed_sessions, status_main


class TestListManagedSessions:
    def test_filters_prefix_and_keeps_topic_identity(self, monkeypatch) -> None:
        result = type(
            "R",
            (),
            {
                "returncode": 0,
                "stdout": "cc_foo\t/tmp/foo\t-100:42\nother\t/tmp/other\t\n",
            },
        )()
        monkeypatch.setattr("ccgram.status_cmd.subprocess.run", lambda *a, **kw: result)

        assert _list_managed_sessions("cc_") == [
            {"name": "cc_foo", "cwd": "/tmp/foo", "topic": "-100:42"}
        ]


class TestStatusMain:
    def test_no_managed_sessions(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr("ccgram.status_cmd._list_managed_sessions", lambda _: [])

        with contextlib.suppress(SystemExit):
            status_main()

        out = capsys.readouterr().out
        assert "ccgram" in out
        assert "Managed tmux sessions: 0" in out

    def test_bound_and_unbound_sessions(self, monkeypatch, capsys) -> None:
        monkeypatch.setenv("TMUX_SESSION_PREFIX", "cc_")
        monkeypatch.setattr(
            "ccgram.status_cmd._list_managed_sessions",
            lambda _: [
                {"name": "cc_foo", "cwd": "/tmp/foo", "topic": "-100:42"},
                {"name": "cc_bar", "cwd": "/tmp/bar", "topic": ""},
            ],
        )

        with contextlib.suppress(SystemExit):
            status_main()

        out = capsys.readouterr().out
        assert "Managed tmux sessions: 2" in out
        assert "cc_foo -> -100:42 /tmp/foo" in out
        assert "cc_bar -> unbound /tmp/bar" in out

    def test_shows_provider_info(self, monkeypatch, capsys) -> None:
        monkeypatch.setenv("CCGRAM_PROVIDER", "shell")
        monkeypatch.setattr("ccgram.status_cmd._list_managed_sessions", lambda _: [])

        with contextlib.suppress(SystemExit):
            status_main()

        out = capsys.readouterr().out
        assert "Provider: shell" in out
        assert "hook" not in out.split("Provider:")[1].split("\n")[0]
