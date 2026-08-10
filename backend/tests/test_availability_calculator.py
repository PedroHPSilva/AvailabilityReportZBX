from datetime import datetime
from unittest import TestCase
from zoneinfo import ZoneInfo

from src.domain.availability_calculator import (
    ASSUME_OK_WHEN_NO_EVENTS,
    ASSUME_OK_WHEN_NO_PREVIOUS_EVENT,
    calculate_availability,
)
from src.integrations.zabbix_client import ZabbixEvent


class AvailabilityCalculatorTests(TestCase):
    def setUp(self) -> None:
        self.timezone = ZoneInfo("America/Sao_Paulo")
        self.period_start = datetime(2026, 3, 1, 0, 0, tzinfo=self.timezone)
        self.period_end = datetime(2026, 3, 2, 0, 0, tzinfo=self.timezone)

    def test_assumes_ok_without_previous_event_or_window_events(self) -> None:
        result, timeline = calculate_availability(
            triggerid="13436",
            trigger_name="Example trigger",
            period_start=self.period_start,
            period_end=self.period_end,
            timezone_name="America/Sao_Paulo",
            previous_event=None,
            events_in_window=[],
            calculated_at=self.period_end,
            unknown_initial_state_policy=ASSUME_OK_WHEN_NO_EVENTS,
        )

        self.assertEqual(result.initial_state, "ASSUMED_OK")
        self.assertEqual(result.calculation_status, "PARCIAL")
        self.assertEqual(result.availability_percent, 100.0)
        self.assertEqual(result.problem_percent, 0.0)
        self.assertEqual(timeline[0].state, "OK")

    def test_keeps_inconclusive_when_window_has_events_without_previous_event(self) -> None:
        result, timeline = calculate_availability(
            triggerid="13436",
            trigger_name="Example trigger",
            period_start=self.period_start,
            period_end=self.period_end,
            timezone_name="America/Sao_Paulo",
            previous_event=None,
            events_in_window=[
                ZabbixEvent(
                    eventid="10",
                    clock=int(self.period_start.timestamp()) + 60,
                    value=0,
                    objectid="13436",
                )
            ],
            calculated_at=self.period_end,
            unknown_initial_state_policy=ASSUME_OK_WHEN_NO_EVENTS,
        )

        self.assertEqual(result.initial_state, "UNKNOWN")
        self.assertEqual(result.calculation_status, "INCONCLUSIVO")
        self.assertIsNone(result.availability_percent)
        self.assertEqual(timeline[0].state, "UNKNOWN")

    def test_frontend_compatibility_assumes_ok_before_first_event(self) -> None:
        result, timeline = calculate_availability(
            triggerid="23595",
            trigger_name="Disk space is critically low",
            period_start=self.period_start,
            period_end=self.period_end,
            timezone_name="America/Sao_Paulo",
            previous_event=None,
            events_in_window=[
                ZabbixEvent(
                    eventid="10",
                    clock=int(self.period_start.timestamp()) + 3600,
                    value=1,
                    objectid="23595",
                )
            ],
            calculated_at=self.period_end,
            unknown_initial_state_policy=ASSUME_OK_WHEN_NO_PREVIOUS_EVENT,
        )

        self.assertEqual(result.initial_state, "ASSUMED_OK")
        self.assertEqual(result.calculation_status, "PARCIAL")
        self.assertEqual(result.ok_seconds, 3600)
        self.assertEqual(result.problem_seconds, 82800)
        self.assertEqual(timeline[0].state, "OK")
