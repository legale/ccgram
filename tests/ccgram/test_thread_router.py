import pytest

from ccgram.thread_router import ThreadRouter


@pytest.fixture
def router() -> ThreadRouter:
    return ThreadRouter()


class TestBindThread:
    def test_bind_and_get(self, router: ThreadRouter) -> None:
        router.bind_thread(100, 1, "@1")
        assert router.get_window_for_thread(100, 1) == "@1"

    def test_bind_sets_display_name(self, router: ThreadRouter) -> None:
        router.bind_thread(100, 1, "@1", window_name="proj")
        assert router.get_display_name("@1") == "proj"

    def test_bind_without_name_no_display(self, router: ThreadRouter) -> None:
        router.bind_thread(100, 1, "@1")
        assert router.get_display_name("@1") == "@1"

    def test_bind_evicts_stale(self, router: ThreadRouter) -> None:
        router.bind_thread(100, 1, "@1")
        router.bind_thread(100, 2, "@1")
        assert router.get_window_for_thread(100, 1) is None
        assert router.get_window_for_thread(100, 2) == "@1"

    def test_rebind_same_thread(self, router: ThreadRouter) -> None:
        router.bind_thread(100, 1, "@1")
        router.bind_thread(100, 1, "@2")
        assert router.get_window_for_thread(100, 1) == "@2"


class TestUnbindThread:
    def test_unbind_returns_window_id(self, router: ThreadRouter) -> None:
        router.bind_thread(100, 1, "@1")
        assert router.unbind_thread(100, 1) == "@1"

    def test_unbind_removes_binding(self, router: ThreadRouter) -> None:
        router.bind_thread(100, 1, "@1")
        router.unbind_thread(100, 1)
        assert router.get_window_for_thread(100, 1) is None

    def test_unbind_nonexistent_returns_none(self, router: ThreadRouter) -> None:
        assert router.unbind_thread(100, 999) is None

    def test_unbind_cleans_group_chat_id(self, router: ThreadRouter) -> None:
        router.bind_thread(100, 1, "@1")
        router.set_group_chat_id(100, 1, -999)
        router.unbind_thread(100, 1)
        assert router.resolve_chat_id(100, 1) == 100

    def test_unbind_removes_empty_user(self, router: ThreadRouter) -> None:
        router.bind_thread(100, 1, "@1")
        router.unbind_thread(100, 1)
        assert 100 not in router.thread_bindings

    def test_unbind_cleans_display_name(self, router: ThreadRouter) -> None:
        router.bind_thread(100, 1, "@1", window_name="proj")
        router.unbind_thread(100, 1)
        assert router.get_display_name("@1") == "@1"


class TestReverseIndex:
    def test_get_thread_for_window(self, router: ThreadRouter) -> None:
        router.bind_thread(100, 42, "@5")
        assert router.get_thread_for_window(100, "@5") == 42

    def test_reverse_cleared_on_unbind(self, router: ThreadRouter) -> None:
        router.bind_thread(100, 42, "@5")
        router.unbind_thread(100, 42)
        assert router.get_thread_for_window(100, "@5") is None

    def test_reverse_updated_on_evict(self, router: ThreadRouter) -> None:
        router.bind_thread(100, 1, "@1")
        router.bind_thread(100, 2, "@1")
        assert router.get_thread_for_window(100, "@1") == 2


class TestIterThreadBindings:
    def test_iter_all(self, router: ThreadRouter) -> None:
        router.bind_thread(100, 1, "@1")
        router.bind_thread(100, 2, "@2")
        router.bind_thread(200, 3, "@3")
        result = set(router.iter_thread_bindings())
        assert result == {(100, 1, "@1"), (100, 2, "@2"), (200, 3, "@3")}

    def test_iter_empty(self, router: ThreadRouter) -> None:
        assert list(router.iter_thread_bindings()) == []


class TestGetAllThreadWindows:
    def test_returns_user_bindings(self, router: ThreadRouter) -> None:
        router.bind_thread(100, 1, "@1")
        router.bind_thread(100, 2, "@2")
        assert router.get_all_thread_windows(100) == {1: "@1", 2: "@2"}

    def test_unknown_user_returns_empty(self, router: ThreadRouter) -> None:
        assert router.get_all_thread_windows(999) == {}


class TestResolveWindowForThread:
    def test_none_thread_id(self, router: ThreadRouter) -> None:
        assert router.resolve_window_for_thread(100, None) is None

    def test_unbound_thread(self, router: ThreadRouter) -> None:
        assert router.resolve_window_for_thread(100, 42) is None

    def test_bound_thread(self, router: ThreadRouter) -> None:
        router.bind_thread(100, 42, "@3")
        assert router.resolve_window_for_thread(100, 42) == "@3"


class TestResolveChatId:
    def test_with_stored_group_id(self, router: ThreadRouter) -> None:
        router.set_group_chat_id(100, 1, -999)
        assert router.resolve_chat_id(100, 1) == -999

    def test_without_group_id_fallback(self, router: ThreadRouter) -> None:
        assert router.resolve_chat_id(100, 1) == 100

    def test_none_thread_id_fallback(self, router: ThreadRouter) -> None:
        router.set_group_chat_id(100, 1, -999)
        assert router.resolve_chat_id(100) == 100


class TestGetWindowForChatThread:
    def test_resolves_window(self, router: ThreadRouter) -> None:
        router.bind_thread(100, 1, "@1")
        router.set_group_chat_id(100, 1, -999)
        assert router.get_window_for_chat_thread(-999, 1) == "@1"

    def test_no_match(self, router: ThreadRouter) -> None:
        assert router.get_window_for_chat_thread(-999, 1) is None

    def test_fallback_to_user_id(self, router: ThreadRouter) -> None:
        router.bind_thread(100, 1, "@1")
        assert router.get_window_for_chat_thread(100, 1) == "@1"


class TestDisplayNames:
    def test_get_fallback(self, router: ThreadRouter) -> None:
        assert router.get_display_name("@99") == "@99"

    def test_set_and_get(self, router: ThreadRouter) -> None:
        router.set_display_name("@1", "myproject")
        assert router.get_display_name("@1") == "myproject"


class TestReset:
    def test_reset_clears_all(self, router: ThreadRouter) -> None:
        router.bind_thread(100, 1, "@1", window_name="proj")
        router.set_group_chat_id(100, 1, -999)
        router.reset()
        assert router.get_window_for_thread(100, 1) is None
        assert router.resolve_chat_id(100, 1) == 100
        assert router.get_display_name("@1") == "@1"
        assert list(router.iter_thread_bindings()) == []
