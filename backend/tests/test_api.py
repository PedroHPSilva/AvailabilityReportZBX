from unittest import TestCase
from unittest.mock import patch
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from src.api import create_app
from src.integrations.zabbix_client import ZabbixEvent, ZabbixJsonRpcError
from src.services.availability_service import AvailabilityService


class FakeZabbixClient:
    def get_hosts(self, search=None, limit=50):
        return [{"hostid": "10084", "name": "Zabbix server", "host": "Zabbix server"}]

    def get_hostgroups(self, search=None, limit=50):
        groups = [{"groupid": "2", "name": "Linux servers"}]
        return groups[:limit] if limit is not None else groups

    def get_hosts_by_groups(self, group_ids, search=None, limit=100):
        hosts = [
            {"hostid": "10084", "name": "Zabbix server", "host": "Zabbix server"},
            {"hostid": "10085", "name": "App server", "host": "app-server"},
        ]
        return hosts[:limit] if limit is not None else hosts

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
        triggers = [self.get_trigger("13075")]
        return triggers[:limit] if limit is not None else triggers

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
        ]
        return triggers[:limit] if limit is not None else triggers

    def get_last_event_before(self, trigger_id, period_start_epoch):
        return ZabbixEvent(eventid="1", clock=period_start_epoch - 60, value=0, objectid=trigger_id)

    def get_events_in_window(self, trigger_id, period_start_epoch, period_end_epoch):
        return [
            ZabbixEvent(eventid="2", clock=period_start_epoch + 60, value=1, objectid=trigger_id),
            ZabbixEvent(eventid="3", clock=period_start_epoch + 120, value=0, objectid=trigger_id),
        ]


class FakeZabbixClientWithoutPreviousEvent(FakeZabbixClient):
    def get_last_event_before(self, trigger_id, period_start_epoch):
        return None

    def get_events_in_window(self, trigger_id, period_start_epoch, period_end_epoch):
        return []


class FakeAuthenticatedZabbixClient(FakeZabbixClient):
    def __init__(self, _url):
        self.auth_token = None

    def login(self, username, password):
        if username != "viewer" or password != "valid":
            raise ZabbixJsonRpcError("Login invalido.")
        self.auth_token = "session-token"

    def use_auth_token(self, auth_token):
        self.auth_token = auth_token


class ApiTests(TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app(lambda: AvailabilityService(FakeZabbixClient())))
        self.payload = {
            "triggerid": "13075",
            "period_start": "2026-03-01T00:00:00-03:00",
            "period_end": "2026-03-01T01:00:00-03:00",
            "timezone": "America/Sao_Paulo",
        }

    def test_lists_hosts_and_triggers(self) -> None:
        hosts = self.client.get("/api/hosts")
        triggers = self.client.get("/api/hosts/10084/triggers")
        filters = self.client.get("/api/filters")
        groups = self.client.get("/api/hostgroups")
        grouped_triggers = self.client.get("/api/triggers?groupids=2")

        self.assertEqual(hosts.status_code, 200)
        self.assertEqual(hosts.json()["hosts"][0]["hostid"], "10084")
        self.assertEqual(triggers.json()["triggers"][0]["triggerid"], "13075")
        self.assertEqual(filters.json()["max_list_limit"], 0)
        self.assertNotIn("unknown_initial_state_policies", filters.json())
        self.assertEqual(groups.json()["groups"][0]["groupid"], "2")
        self.assertEqual(grouped_triggers.json()["count"], 2)
        self.assertEqual({trigger["host_count"] for trigger in grouped_triggers.json()["triggers"]}, {1})

    def test_calculates_result_and_timeline(self) -> None:
        result = self.client.post("/api/availability/calculate", json=self.payload)
        timeline = self.client.post("/api/availability/timeline", json=self.payload)
        grouped = self.client.post(
            "/api/availability/group-trigger/calculate",
            json={
                "trigger_keys": ["trigger:13075", "trigger:23075"],
                "groupids": ["2"],
                "period_start": self.payload["period_start"],
                "period_end": self.payload["period_end"],
                "timezone": self.payload["timezone"],
            },
        )

        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["problem_seconds"], 60)
        self.assertEqual(timeline.json()["intervals"][1]["state"], "PROBLEM")
        self.assertTrue(timeline.json()["audit"]["previous_event_found"])
        self.assertEqual(timeline.json()["audit"]["events_in_window_count"], 2)
        self.assertEqual(timeline.json()["audit"]["initial_state_source"], "PREVIOUS_EVENT")
        self.assertFalse(timeline.json()["audit"]["maintenance_considered"])
        self.assertEqual(grouped.status_code, 200)
        self.assertEqual(grouped.json()["calculated_count"], 2)

    def test_rejects_invalid_period(self) -> None:
        response = self.client.post(
            "/api/availability/calculate",
            json={**self.payload, "period_end": self.payload["period_start"]},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "HTTP_ERROR")
        self.assertEqual(response.json()["message"], "O fim do periodo deve ser posterior ao inicio.")

    def test_rejects_period_older_than_two_years(self) -> None:
        timezone = ZoneInfo("America/Sao_Paulo")
        old_start = datetime.now(timezone) - timedelta(days=731)
        old_end = old_start + timedelta(hours=1)
        response = self.client.post(
            "/api/availability/calculate",
            json={
                **self.payload,
                "period_start": old_start.isoformat(),
                "period_end": old_end.isoformat(),
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["message"], "O periodo inicial nao pode ser anterior a 730 dias.")

    def test_keeps_zabbix_compatible_initial_state_rule_in_backend(self) -> None:
        client = TestClient(create_app(lambda: AvailabilityService(FakeZabbixClientWithoutPreviousEvent())))
        response = client.post(
            "/api/availability/calculate",
            json={**self.payload, "unknown_initial_state_policy": "INCONCLUSIVE"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["initial_state"], "ASSUMED_OK")
        self.assertEqual(response.json()["availability_percent"], 100.0)

    def test_returns_validation_error_envelope(self) -> None:
        response = self.client.post("/api/availability/calculate", json={"triggerid": "13075"})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"], "VALIDATION_ERROR")
        self.assertEqual(response.json()["message"], "Payload invalido.")

    def test_allows_local_frontend_cors(self) -> None:
        response = self.client.options(
            "/api/hosts",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:3000")

    def test_requires_login_and_uses_authenticated_session(self) -> None:
        with patch.dict("os.environ", {"ZABBIX_URL": "http://zabbix.local"}, clear=False):
            client = TestClient(create_app(zabbix_client_factory=FakeAuthenticatedZabbixClient))

            protected = client.get("/api/hosts")
            invalid_login = client.post("/api/auth/login", json={"username": "viewer", "password": "wrong"})
            valid_login = client.post("/api/auth/login", json={"username": "viewer", "password": "valid"})
            authenticated = client.get("/api/hosts")
            logout = client.post("/api/auth/logout")
            after_logout = client.get("/api/hosts")

        self.assertEqual(protected.status_code, 401)
        self.assertEqual(invalid_login.status_code, 401)
        self.assertEqual(valid_login.status_code, 200)
        self.assertTrue(valid_login.json()["authenticated"])
        self.assertNotIn("session-token", valid_login.text)
        self.assertEqual(authenticated.status_code, 200)
        self.assertFalse(logout.json()["authenticated"])
        self.assertEqual(after_logout.status_code, 401)
