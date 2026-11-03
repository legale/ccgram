from ccgram.session import SessionManager


def test_session_manager_cwd_tracking():
    sm = SessionManager()
    assert sm.get_window_cwd("s1:@1") == ""

    sm.set_window_cwd("s1:@1", "/home/user/project")
    assert sm.get_window_cwd("s1:@1") == "/home/user/project"

    sm.clear_window_state("s1:@1")
    assert sm.get_window_cwd("s1:@1") == ""
    sm.flush_state()
