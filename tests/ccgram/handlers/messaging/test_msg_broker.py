import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ccgram.handlers.messaging.msg_broker import (
    BROKER_CYCLE_INTERVAL,
    SWEEP_INTERVAL,
    _INJECTION_CHAR_LIMIT,
    _collect_eligible,
    _recover_stale_pending,
    broker_delivery_cycle,
    format_file_reference,
    format_injection_text,
    merge_injection_texts,
    write_delivery_file,
)
from ccgram.handlers.messaging.msg_delivery import (
    delivery_strategy,
    reset_delivery_state,
)
from ccgram.handlers.messaging.msg_delivery import (
    DeliveryState,
    MessageDeliveryStrategy,
    _LOOP_THRESHOLD,
    _RATE_WINDOW_SECONDS,
    _pair_key,
    clear_delivery_state,
)
from ccgram.mailbox import Mailbox


@pytest.fixture(autouse=True)
def _clean_strategy():
    reset_delivery_state()
    yield
    reset_delivery_state()


@pytest.fixture()
def mailbox(tmp_path: Path) -> Mailbox:
    return Mailbox(tmp_path / "mailbox")


class TestMessageDeliveryStrategy:
    def setup_method(self):
        self.strategy = MessageDeliveryStrategy()

    def test_get_state_creates_new(self):
        state = self.strategy.get_state("ccgram:@0")
        assert isinstance(state, DeliveryState)
        assert state.delivery_timestamps == []

    def test_get_state_returns_same_instance(self):
        s1 = self.strategy.get_state("ccgram:@0")
        s2 = self.strategy.get_state("ccgram:@0")
        assert s1 is s2

    def test_clear_state_removes(self):
        self.strategy.get_state("ccgram:@0")
        self.strategy.clear_state("ccgram:@0")
        assert "ccgram:@0" not in self.strategy._states

    def test_clear_state_nonexistent_is_noop(self):
        self.strategy.clear_state("ccgram:@999")

    def test_reset_all_state(self):
        self.strategy.get_state("ccgram:@0")
        self.strategy.get_state("ccgram:@5")
        self.strategy.reset_all_state()
        assert len(self.strategy._states) == 0


class TestRateLimiting:
    def setup_method(self):
        self.strategy = MessageDeliveryStrategy()

    def test_within_rate_limit(self):
        assert self.strategy.check_rate_limit("ccgram:@0", max_rate=10)

    def test_exceeds_rate_limit(self):
        for _ in range(10):
            self.strategy.record_delivery("ccgram:@0")
        assert not self.strategy.check_rate_limit("ccgram:@0", max_rate=10)

    def test_rate_limit_window_expires(self):
        state = self.strategy.get_state("ccgram:@0")
        old_time = time.monotonic() - _RATE_WINDOW_SECONDS - 1
        state.delivery_timestamps = [old_time] * 10
        assert self.strategy.check_rate_limit("ccgram:@0", max_rate=10)

    def test_record_delivery_sets_timestamp(self):
        self.strategy.record_delivery("ccgram:@0")
        state = self.strategy.get_state("ccgram:@0")
        assert len(state.delivery_timestamps) == 1
        assert state.delivery_timestamps[0] > 0


class TestLoopDetection:
    def setup_method(self):
        self.strategy = MessageDeliveryStrategy()

    def test_no_loop_initially(self):
        assert not self.strategy.check_loop("ccgram:@0", "ccgram:@5")

    def test_loop_detected_at_threshold(self):
        for _ in range(_LOOP_THRESHOLD):
            self.strategy.record_exchange("ccgram:@0", "ccgram:@5")
        assert self.strategy.check_loop("ccgram:@0", "ccgram:@5")

    def test_loop_detection_is_symmetric(self):
        for _ in range(_LOOP_THRESHOLD):
            self.strategy.record_exchange("ccgram:@0", "ccgram:@5")
        assert self.strategy.check_loop("ccgram:@5", "ccgram:@0")

    def test_loop_below_threshold(self):
        for _ in range(_LOOP_THRESHOLD - 1):
            self.strategy.record_exchange("ccgram:@0", "ccgram:@5")
        assert not self.strategy.check_loop("ccgram:@0", "ccgram:@5")

    def test_pause_and_unpause(self):
        self.strategy.pause_peer("ccgram:@0", "ccgram:@5")
        assert self.strategy.is_paused("ccgram:@0", "ccgram:@5")
        self.strategy.unpause_peer("ccgram:@0", "ccgram:@5")
        assert not self.strategy.is_paused("ccgram:@0", "ccgram:@5")

    def test_allow_more_clears_loop_state(self):
        for _ in range(_LOOP_THRESHOLD):
            self.strategy.record_exchange("ccgram:@0", "ccgram:@5")
        self.strategy.pause_peer("ccgram:@0", "ccgram:@5")
        self.strategy.allow_more("ccgram:@0", "ccgram:@5")
        assert not self.strategy.is_paused("ccgram:@0", "ccgram:@5")
        assert not self.strategy.check_loop("ccgram:@0", "ccgram:@5")


class TestPairKey:
    def test_order_independent(self):
        assert _pair_key("ccgram:@0", "ccgram:@5") == _pair_key(
            "ccgram:@5", "ccgram:@0"
        )

    def test_format(self):
        key = _pair_key("ccgram:@0", "ccgram:@5")
        assert "|" in key


class TestFormatInjectionText:
    def test_basic_request(self):
        text = format_injection_text(
            msg_id="123-abc",
            from_id="ccgram:@0",
            from_name="payment-svc",
            branch="feat/refund",
            subject="API query",
            body="What is your API?",
            msg_type="request",
        )
        assert "[MSG 123-abc from ccgram:@0" in text
        assert "payment-svc" in text
        assert "feat/refund" in text
        assert "API query:" in text
        assert "What is your API?" in text
        assert "REPLY WITH:" in text

    def test_notify_no_reply_hint(self):
        text = format_injection_text(
            msg_id="456-def",
            from_id="ccgram:@0",
            from_name="svc",
            branch="",
            subject="",
            body="FYI done",
            msg_type="notify",
        )
        assert "REPLY WITH:" not in text

    def test_newlines_replaced(self):
        text = format_injection_text(
            msg_id="1",
            from_id="ccgram:@0",
            from_name="svc",
            branch="",
            subject="",
            body="line1\n\nline2\nline3",
            msg_type="notify",
        )
        assert "\n" not in text
        assert "line1 | line2 line3" in text

    def test_truncation_at_limit(self):
        long_body = "x" * 1000
        text = format_injection_text(
            msg_id="1",
            from_id="ccgram:@0",
            from_name="svc",
            branch="",
            subject="",
            body=long_body,
            msg_type="notify",
        )
        assert len(text) <= _INJECTION_CHAR_LIMIT
        assert text.endswith("...")

    def test_no_subject(self):
        text = format_injection_text(
            msg_id="1",
            from_id="ccgram:@0",
            from_name="svc",
            branch="",
            subject="",
            body="hello",
            msg_type="notify",
        )
        assert "svc)]" in text
        assert text.count(":") == 1  # only the colon in "ccgram:@0"


class TestFormatFileReference:
    def test_format(self):
        ref = format_file_reference("123-abc", "/tmp/deliver-123-abc.txt")
        assert "[MSG 123-abc]" in ref
        assert "/tmp/deliver-123-abc.txt" in ref


class TestMergeInjectionTexts:
    def test_single(self):
        assert merge_injection_texts(["hello"]) == "hello"

    def test_multiple(self):
        result = merge_injection_texts(["msg1", "msg2", "msg3"])
        assert result == "msg1 --- msg2 --- msg3"


class TestWriteDeliveryFile:
    def test_writes_file(self, tmp_path):
        mb = Mailbox(tmp_path)
        path = write_delivery_file(mb, "ccgram:@0", "123-abc", "long body text")
        assert path.exists()
        assert path.read_text() == "long body text"
        assert "deliver-123-abc.txt" in path.name

    def test_creates_directories(self, tmp_path):
        mb = Mailbox(tmp_path / "mailbox")
        path = write_delivery_file(mb, "ccgram:@0", "456", "body")
        assert path.exists()
        assert path.parent.name == "tmp"


