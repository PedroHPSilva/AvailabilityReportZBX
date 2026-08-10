from datetime import datetime
from unittest import TestCase
from zoneinfo import ZoneInfo

from src.domain.availability_calculator import ASSUME_OK_WHEN_NO_EVENTS
from src.integrations.zabbix_client import ZabbixEvent
from src.services.availability_service import AvailabilityService, AvailabilityWindow


class FakeZabbixClient:
    def get_hosts(self, search=None, limit=50):
        return [{"hostid": "10084", "name": "Zabbix server", "host": "Zabbix server"}]

    def get_hostgroups(self, search=None, limit=50):
        return [{"groupid": "2", "name": "Linux servers"}][:limit]

    def get_hosts_by_groups(self, group_ids, search=None, limit=100):
        return [
            {"hostid": "10084", "name": "Zabbix server", "host": "Zabbix server"},
            {"hostid": "10085", "name": "App server", "host": "app-server"},
        ][:limit]

    def get_trigger(self, trigger_id):
        return {
            "triggerid": trigger_id,
            "description": "Zabbix server: Value cache",
            "status": "0",
            "value": "0",
            "templateid": "9100",
            "hosts": [{"hostid": "10084", "name": "Zabbix server", "host": "Zabbix server"}],
        }

    def get_triggers_for_host(self, host_id, search=None, limit=100):
        return [self.get_trigger("13075"), self.get_trigger("13558")][:limit]

    def get_triggers_for_hosts(self, host_ids, search=None, limit=500):
        triggers = [
            {
                "triggerid": "13075",
                "description": "Zabbix server: Value cache",
                "status": "0",
                "value": "0",
                "templateid": "9100",
                "hosts": [{"hostid": "10084", "name": "Zabbix server", "host": "Zabbix server"}],
            },
            {
                "triggerid": "23075",
                "description": "App server: Value cache",
                "status": "0",
                "value": "0",
                "templateid": "9100",
                "hosts": [{"hostid": "10085", "name": "App server", "host": "app-server"}],
            },
            {
                "triggerid": "33075",
                "description": '{HOST.NAME} -> PortFibreChannel ID - "1", node name - "node1" running status is {ITEM.VALUE}',
                "status": "0",
                "value": "0",
                "discoveryPrototype": {"triggerid": "9201", "templateid": "9200", "description": "PortFibreChannel status"},
                "hosts": [{"hostid": "10084", "name": "Zabbix server", "host": "Zabbix server"}],
            },
            {
                "triggerid": "23594",
                "description": r"C:\ Label: Serial Number e4a30760: Disk space is critically low",
                "status": "0",
                "value": "0",
                "discoveryPrototype": {
                    "triggerid": "23490",
                    "templateid": "22512",
                    "description": r"{#FSNAME}: Disk space is critically low",
                },
                "hosts": [{"hostid": "10587", "name": "CENTRALINK-SIEMENS", "host": "CENTRALINK-SIEMENS"}],
            },
            {
                "triggerid": "23595",
                "description": r"D:\ Label:Volume  Serial Number 8a11b30: Disk space is critically low",
                "status": "0",
                "value": "1",
                "discoveryPrototype": {
                    "triggerid": "23490",
                    "templateid": "22512",
                    "description": r"{#FSNAME}: Disk space is critically low",
                },
                "hosts": [{"hostid": "10587", "name": "CENTRALINK-SIEMENS", "host": "CENTRALINK-SIEMENS"}],
            },
        ]
        if search:
            return [
                trigger for trigger in triggers
                if search.casefold() in str(trigger["description"]).casefold()
            ][:limit]
        return triggers[:limit]

    def get_last_event_before(self, trigger_id, period_start_epoch):
        return ZabbixEvent(eventid="1", clock=period_start_epoch - 60, value=0, objectid=trigger_id)

    def get_events_in_window(self, trigger_id, period_start_epoch, period_end_epoch):
        if trigger_id != "13075":
            return []
        return [
            ZabbixEvent(eventid="2", clock=period_start_epoch + 60, value=1, objectid=trigger_id),
            ZabbixEvent(eventid="3", clock=period_start_epoch + 120, value=0, objectid=trigger_id),
        ]


class AvailabilityServiceTests(TestCase):
    def setUp(self) -> None:
        timezone = ZoneInfo("America/Sao_Paulo")
        self.window = AvailabilityWindow(
            period_start=datetime(2026, 3, 1, 0, 0, tzinfo=timezone),
            period_end=datetime(2026, 3, 1, 1, 0, tzinfo=timezone),
            timezone_name="America/Sao_Paulo",
            calculated_at=datetime(2026, 3, 1, 1, 1, tzinfo=timezone),
            unknown_initial_state_policy=ASSUME_OK_WHEN_NO_EVENTS,
        )
        self.service = AvailabilityService(FakeZabbixClient())

    def test_lists_hosts_and_triggers_as_domain_summaries(self) -> None:
        host = self.service.list_hosts()[0]
        trigger = self.service.list_triggers_for_host(host.hostid)[0]

        self.assertEqual(host.name, "Zabbix server")
        self.assertEqual(trigger.triggerid, "13075")
        self.assertEqual(trigger.hosts[0].hostid, "10084")

    def test_calculates_single_trigger_and_host(self) -> None:
        single = self.service.calculate_trigger("13075", self.window)
        host_results = self.service.calculate_host("10084", self.window, limit=2)

        self.assertEqual(single.result.problem_seconds, 60)
        self.assertEqual(single.timeline[1].state, "PROBLEM")
        self.assertTrue(single.audit.previous_event_found)
        self.assertEqual(single.audit.events_in_window_count, 2)
        self.assertEqual(single.audit.initial_state_source, "PREVIOUS_EVENT")
        self.assertEqual(len(host_results), 2)
        self.assertEqual(host_results[1].result.availability_percent, 100.0)

    def test_lists_real_triggers_and_calculates_selected_item(self) -> None:
        groups = self.service.list_hostgroups()
        hosts = self.service.list_hosts_by_groups(["2"])
        trigger_groups = self.service.list_trigger_groups(group_ids=["2"])
        value_cache = next(group for group in trigger_groups if group.description == "Zabbix server: Value cache")
        calculation = self.service.calculate_trigger_group(value_cache.key, self.window, group_ids=["2"])

        self.assertEqual(groups[0].name, "Linux servers")
        self.assertEqual(len(hosts), 2)
        self.assertEqual(value_cache.host_count, 1)
        self.assertEqual(value_cache.trigger_count, 1)
        self.assertEqual(calculation.calculated_count, 1)
        self.assertEqual(calculation.average_availability_percent, 98.3333)

    def test_trigger_description_preserves_zabbix_instance_name(self) -> None:
        trigger_groups = self.service.list_trigger_groups(group_ids=["2"])
        fibre_channel = next(group for group in trigger_groups if "PortFibreChannel" in group.description)

        self.assertEqual(
            fibre_channel.description,
            '{HOST.NAME} -> PortFibreChannel ID - "1", node name - "node1" running status is {ITEM.VALUE}',
        )

    def test_finds_trigger_from_frontend_label_with_host_and_collapsed_spaces(self) -> None:
        trigger_groups = self.service.list_trigger_groups(
            group_ids=["2"],
            search=r"CENTRALINK-SIEMENS(10587) D:\ Label:Volume Serial Number 8a11b30: Disk space is critically low",
        )

        self.assertEqual(len(trigger_groups), 1)
        self.assertIn("23595", {trigger.triggerid for trigger in trigger_groups[0].triggers})
        self.assertEqual(trigger_groups[0].grouping_source, "DISCOVERY_TEMPLATE_PROTOTYPE")
        self.assertEqual(trigger_groups[0].grouping_source_id, "22512")
        self.assertEqual(trigger_groups[0].description, r"D:\ Label:Volume  Serial Number 8a11b30: Disk space is critically low")
        self.assertEqual(trigger_groups[0].trigger_count, 1)

    def test_does_not_group_triggers_without_stable_origin_by_description(self) -> None:
        triggers = [
            {
                "triggerid": "1",
                "description": "Same description",
                "hosts": [{"hostid": "10", "name": "One", "host": "one"}],
            },
            {
                "triggerid": "2",
                "description": "Same description",
                "hosts": [{"hostid": "20", "name": "Two", "host": "two"}],
            },
        ]
        original = self.service.client.get_triggers_for_hosts
        self.service.client.get_triggers_for_hosts = lambda host_ids, search=None, limit=500: triggers
        try:
            groups = self.service.list_trigger_groups(host_ids=["10", "20"])
        finally:
            self.service.client.get_triggers_for_hosts = original

        self.assertEqual(len(groups), 2)
        self.assertEqual({group.grouping_source for group in groups}, {"EXACT_TRIGGER"})
