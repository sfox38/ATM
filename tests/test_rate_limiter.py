"""Tests for rate_limiter.py."""

from __future__ import annotations

from unittest.mock import patch

from custom_components.atm.rate_limiter import (
    WINDOW_SECONDS,
    RateLimitResult,
    RateLimiter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rl() -> RateLimiter:
    return RateLimiter()


def _check(rl: RateLimiter, token_id: str = "tok", requests: int = 5, burst: int = 3) -> RateLimitResult:
    return rl.check(token_id, rate_limit_requests=requests, rate_limit_burst=burst)


def _make_times(start: float, count: int, step: float = 0.5) -> list[float]:
    """Generate a list of evenly-spaced timestamps."""
    return [start + i * step for i in range(count)]


# ---------------------------------------------------------------------------
# Disabled rate limiting
# ---------------------------------------------------------------------------


class TestDisabled:
    def test_zero_requests_always_allowed(self):
        rl = _rl()
        result = rl.check("tok", rate_limit_requests=0, rate_limit_burst=0)
        assert result.allowed is True

    def test_zero_requests_rate_limiting_not_enabled(self):
        rl = _rl()
        result = rl.check("tok", rate_limit_requests=0, rate_limit_burst=0)
        assert result.rate_limiting_enabled is False

    def test_zero_requests_always_allowed_repeatedly(self):
        rl = _rl()
        for _ in range(200):
            result = rl.check("tok", rate_limit_requests=0, rate_limit_burst=0)
            assert result.allowed is True

    def test_zero_requests_no_window_created(self):
        rl = _rl()
        rl.check("tok", rate_limit_requests=0, rate_limit_burst=0)
        assert rl.active_token_count() == 0


# ---------------------------------------------------------------------------
# Allowed requests and header values
# ---------------------------------------------------------------------------


class TestAllowedRequests:
    def test_first_request_allowed(self):
        rl = _rl()
        result = _check(rl, requests=5, burst=3)
        assert result.allowed is True

    def test_rate_limiting_enabled_flag(self):
        rl = _rl()
        result = _check(rl, requests=5, burst=3)
        assert result.rate_limiting_enabled is True

    def test_limit_header_matches_configured_requests(self):
        rl = _rl()
        result = _check(rl, requests=10, burst=5)
        assert result.limit == 10

    def test_remaining_decrements_with_each_request(self):
        rl = _rl()
        for expected in [4, 3, 2, 1, 0]:
            result = _check(rl, requests=5, burst=10)
            assert result.allowed is True
            assert result.remaining == expected

    def test_remaining_is_zero_on_last_allowed_request(self):
        rl = _rl()
        for _ in range(4):
            _check(rl, requests=5, burst=10)
        result = _check(rl, requests=5, burst=10)
        assert result.allowed is True
        assert result.remaining == 0

    def test_reset_is_future_epoch_second(self):
        import time
        rl = _rl()
        now = time.time()
        result = _check(rl, requests=5, burst=10)
        assert result.reset >= int(now + WINDOW_SECONDS - 1)
        assert result.reset <= int(now + WINDOW_SECONDS + 1)

    def test_reset_based_on_oldest_entry(self):
        t0 = 1_000_000.0
        rl = _rl()
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            _check(rl, requests=5, burst=10)
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0 + 10), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0 + 10):
            result = _check(rl, requests=5, burst=10)
        assert result.reset == int(t0 + WINDOW_SECONDS)


# ---------------------------------------------------------------------------
# Sliding window limit
# ---------------------------------------------------------------------------


class TestSlidingWindow:
    def test_at_limit_denied(self):
        t0 = 1_000_000.0
        rl = _rl()
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            for _ in range(5):
                result = _check(rl, requests=5, burst=10)
                assert result.allowed is True
            result = _check(rl, requests=5, burst=10)
            assert result.allowed is False

    def test_denied_remaining_is_zero(self):
        t0 = 1_000_000.0
        rl = _rl()
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            for _ in range(5):
                _check(rl, requests=5, burst=10)
            result = _check(rl, requests=5, burst=10)
        assert result.remaining == 0

    def test_denied_does_not_increment_window(self):
        """A denied request must not be recorded in the window."""
        t0 = 1_000_000.0
        rl = _rl()
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            for _ in range(5):
                _check(rl, requests=5, burst=10)
            # This should be denied and NOT recorded
            _check(rl, requests=5, burst=10)
            _check(rl, requests=5, burst=10)
        # Window size must still be exactly 5 (the original allowed requests)
        assert len(rl._windows["tok"]) == 5

    def test_window_expiry_allows_new_requests(self):
        t0 = 1_000_000.0
        rl = _rl()
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            for _ in range(5):
                _check(rl, requests=5, burst=10)
            denied = _check(rl, requests=5, burst=10)
            assert denied.allowed is False
        # Advance 61 seconds - all entries have expired
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0 + 61), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0 + 61):
            result = _check(rl, requests=5, burst=10)
            assert result.allowed is True

    def test_partial_window_expiry(self):
        """Entries older than 60 s expire; newer ones remain counted."""
        t0 = 1_000_000.0
        rl = _rl()
        # Make 3 requests at t0
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            for _ in range(3):
                _check(rl, requests=5, burst=10)
        # Make 2 more requests at t0+30 (within window)
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0 + 30), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0 + 30):
            for _ in range(2):
                _check(rl, requests=5, burst=10)
        # At t0+61 the first 3 have expired, leaving 2; 3 more should be allowed
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0 + 61), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0 + 61):
            for _ in range(3):
                result = _check(rl, requests=5, burst=10)
                assert result.allowed is True

    def test_retry_after_on_window_denial(self):
        t0 = 1_000_000.0
        rl = _rl()
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            for _ in range(5):
                _check(rl, requests=5, burst=10)
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0 + 10), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0 + 10):
            result = _check(rl, requests=5, burst=10)
        assert result.allowed is False
        # Oldest entry is at t0, window expires at t0+60, now is t0+10, so retry_after = 50
        assert result.retry_after == 50

    def test_retry_after_minimum_is_one(self):
        """retry_after must be at least 1 even if the calculation rounds to 0."""
        t0 = 1_000_000.0
        rl = _rl()
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            for _ in range(5):
                _check(rl, requests=5, burst=10)
        # At t0+59.999 the retry is < 1 s; must still return 1
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0 + 59.999), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0 + 59.999):
            result = _check(rl, requests=5, burst=10)
        assert result.retry_after >= 1

    def test_reset_time_on_denial(self):
        t0 = 1_000_000.0
        rl = _rl()
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            for _ in range(5):
                _check(rl, requests=5, burst=10)
            result = _check(rl, requests=5, burst=10)
        assert result.reset == int(t0 + WINDOW_SECONDS)

    def test_single_request_limit_enforced(self):
        t0 = 1_000_000.0
        rl = _rl()
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            first = rl.check("tok", rate_limit_requests=1, rate_limit_burst=10)
            second = rl.check("tok", rate_limit_requests=1, rate_limit_burst=10)
        assert first.allowed is True
        assert second.allowed is False


# ---------------------------------------------------------------------------
# Burst limit
# ---------------------------------------------------------------------------


class TestBurstLimit:
    def test_burst_exceeded_denied(self):
        t0 = 1_000_000.0
        rl = _rl()
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            for _ in range(3):
                r = rl.check("tok", rate_limit_requests=100, rate_limit_burst=3)
                assert r.allowed is True
            result = rl.check("tok", rate_limit_requests=100, rate_limit_burst=3)
            assert result.allowed is False

    def test_burst_denial_does_not_increment_window(self):
        t0 = 1_000_000.0
        rl = _rl()
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            for _ in range(3):
                rl.check("tok", rate_limit_requests=100, rate_limit_burst=3)
            rl.check("tok", rate_limit_requests=100, rate_limit_burst=3)
            rl.check("tok", rate_limit_requests=100, rate_limit_burst=3)
        assert len(rl._windows["tok"]) == 3

    def test_burst_window_expiry_allows_new_requests(self):
        t0 = 1_000_000.0
        rl = _rl()
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            for _ in range(3):
                rl.check("tok", rate_limit_requests=100, rate_limit_burst=3)
            denied = rl.check("tok", rate_limit_requests=100, rate_limit_burst=3)
            assert denied.allowed is False
        # Advance just over 1 second; burst window has cleared
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0 + 1.001), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0 + 1.001):
            result = rl.check("tok", rate_limit_requests=100, rate_limit_burst=3)
            assert result.allowed is True

    def test_burst_zero_disables_burst_check(self):
        """burst=0 means no burst limiting; only the 60s window applies."""
        t0 = 1_000_000.0
        rl = _rl()
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            for _ in range(10):
                result = rl.check("tok", rate_limit_requests=100, rate_limit_burst=0)
                assert result.allowed is True

    def test_burst_retry_after_is_at_most_one_second(self):
        t0 = 1_000_000.0
        rl = _rl()
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            for _ in range(3):
                rl.check("tok", rate_limit_requests=100, rate_limit_burst=3)
            result = rl.check("tok", rate_limit_requests=100, rate_limit_burst=3)
        # Oldest burst entry is at t0; retry after = ceil(t0 + 1 - t0) = 1
        assert result.allowed is False
        assert result.retry_after == 1

    def test_burst_allowed_after_some_entries_age_out(self):
        """Once older burst entries leave the 1 s window, new requests should be allowed."""
        t0 = 1_000_000.0
        rl = _rl()
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            for _ in range(2):
                rl.check("tok", rate_limit_requests=100, rate_limit_burst=3)
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0 + 0.5), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0 + 0.5):
            rl.check("tok", rate_limit_requests=100, rate_limit_burst=3)
            denied = rl.check("tok", rate_limit_requests=100, rate_limit_burst=3)
            assert denied.allowed is False
        # Advance 1.1 s from t0; the two entries at t0 have aged out of burst window
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0 + 1.1), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0 + 1.1):
            result = rl.check("tok", rate_limit_requests=100, rate_limit_burst=3)
            assert result.allowed is True

    def test_sliding_window_still_enforced_when_burst_passes(self):
        """Both checks must be active simultaneously."""
        t0 = 1_000_000.0
        rl = _rl()
        # Fill the 60 s window completely (spread across time so burst doesn't trigger)
        for i in range(5):
            with patch("custom_components.atm.rate_limiter.time.time", return_value=t0 + i * 5), \
                 patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0 + i * 5):
                rl.check("tok", rate_limit_requests=5, rate_limit_burst=3)
        # Now at t0+25 there's only 1 request in the last second, but the 60 s window is full
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0 + 25), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0 + 25):
            result = rl.check("tok", rate_limit_requests=5, rate_limit_burst=3)
            assert result.allowed is False


# ---------------------------------------------------------------------------
# destroy() and destroy_all()
# ---------------------------------------------------------------------------


class TestDestroy:
    def test_destroy_clears_single_token_state(self):
        rl = _rl()
        _check(rl, "tok1", requests=5, burst=10)
        _check(rl, "tok2", requests=5, burst=10)
        rl.destroy("tok1")
        assert "tok1" not in rl._windows
        assert "tok2" in rl._windows

    def test_destroy_resets_counter_for_that_token(self):
        t0 = 1_000_000.0
        rl = _rl()
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            for _ in range(5):
                _check(rl, requests=5, burst=10)
            denied = _check(rl, requests=5, burst=10)
            assert denied.allowed is False
        rl.destroy("tok")
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            result = _check(rl, requests=5, burst=10)
            assert result.allowed is True

    def test_destroy_nonexistent_token_no_error(self):
        rl = _rl()
        rl.destroy("no-such-token")

    def test_destroy_all_clears_all_tokens(self):
        rl = _rl()
        for name in ["t1", "t2", "t3"]:
            _check(rl, name, requests=5, burst=10)
        assert rl.active_token_count() == 3
        rl.destroy_all()
        assert rl.active_token_count() == 0

    def test_destroy_all_allows_requests_again(self):
        t0 = 1_000_000.0
        rl = _rl()
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            for _ in range(5):
                _check(rl, requests=5, burst=10)
        rl.destroy_all()
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            result = _check(rl, requests=5, burst=10)
            assert result.allowed is True


# ---------------------------------------------------------------------------
# Multiple tokens are independent
# ---------------------------------------------------------------------------


class TestTokenIsolation:
    def test_exhausting_one_token_does_not_affect_another(self):
        t0 = 1_000_000.0
        rl = _rl()
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            for _ in range(5):
                _check(rl, "tok1", requests=5, burst=10)
            denied = _check(rl, "tok1", requests=5, burst=10)
            allowed = _check(rl, "tok2", requests=5, burst=10)
        assert denied.allowed is False
        assert allowed.allowed is True

    def test_different_limits_per_token(self):
        t0 = 1_000_000.0
        rl = _rl()
        with patch("custom_components.atm.rate_limiter.time.time", return_value=t0), \
             patch("custom_components.atm.rate_limiter.time.monotonic", return_value=t0):
            for _ in range(3):
                rl.check("tok_small", rate_limit_requests=3, rate_limit_burst=10)
            denied = rl.check("tok_small", rate_limit_requests=3, rate_limit_burst=10)
            allowed = rl.check("tok_large", rate_limit_requests=100, rate_limit_burst=10)
        assert denied.allowed is False
        assert allowed.allowed is True

    def test_active_token_count_tracks_correctly(self):
        rl = _rl()
        assert rl.active_token_count() == 0
        _check(rl, "tok1", requests=5, burst=10)
        assert rl.active_token_count() == 1
        _check(rl, "tok2", requests=5, burst=10)
        assert rl.active_token_count() == 2
        rl.destroy("tok1")
        assert rl.active_token_count() == 1


# ---------------------------------------------------------------------------
# RateLimitResult dataclass
# ---------------------------------------------------------------------------


class TestRateLimitResult:
    def test_allowed_result_fields(self):
        result = RateLimitResult(
            allowed=True,
            rate_limiting_enabled=True,
            limit=60,
            remaining=59,
            reset=1_000_060,
            retry_after=0,
        )
        assert result.allowed is True
        assert result.limit == 60
        assert result.remaining == 59

    def test_denied_result_fields(self):
        result = RateLimitResult(
            allowed=False,
            rate_limiting_enabled=True,
            limit=60,
            remaining=0,
            reset=1_000_060,
            retry_after=30,
        )
        assert result.allowed is False
        assert result.retry_after == 30

    def test_disabled_result(self):
        result = RateLimitResult(allowed=True, rate_limiting_enabled=False)
        assert result.allowed is True
        assert result.rate_limiting_enabled is False
        assert result.limit == 0
