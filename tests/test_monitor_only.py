"""Tests del build monitor-only."""


def test_monitor_only_flag_is_true():
    from config.loader import MONITOR_ONLY

    assert MONITOR_ONLY is True
