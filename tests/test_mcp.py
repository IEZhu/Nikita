import unittest
import unittest.mock
import json
import base64
import datetime
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import tests.mock_env
tests.mock_env.setup_mocks()

from src import globals as g
from src import mcp as m
from src import mcp_query as q

MODERN = "2026-07-28"


def modern_request(method, params=None, request_id=1, version=MODERN):
    params = dict(params or {})
    params["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def modern_headers(method, name=None, version=MODERN):
    headers = {"mcp-protocol-version": version, "mcp-method": method}
    if name is not None:
        headers["mcp-name"] = name
    return headers


class McpTestCase(unittest.TestCase):
    def setUp(self):
        patches = {
            "enabled": True,
            "clickhouse_user": "nikita_mcp",
            "max_rows": 100,
            "allowed_origins": ["https://allowed.example"],
            "max_body": 65536,
        }
        for name, value in patches.items():
            patcher = unittest.mock.patch.object(g.conf.mcp, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        debug_patcher = unittest.mock.patch.object(g.debug, "on", False)
        debug_patcher.start()
        self.addCleanup(debug_patcher.stop)

    def post(self, message, headers=None, raw=None):
        body = raw if raw is not None else json.dumps(message).encode("utf-8")
        status, response_headers, response_body = m.handle("POST", headers or {}, body)
        payload = json.loads(response_body) if response_body else None
        return status, response_headers, payload


class TestTransport(McpTestCase):
    def test_disabled_returns_404(self):
        with unittest.mock.patch.object(g.conf.mcp, "enabled", False):
            status, _, body = m.handle("POST", {}, b"{}")
        self.assertEqual(status, 404)
        self.assertEqual(body, b"")

    def test_get_returns_405(self):
        status, headers, _ = m.handle("GET", {}, b"")
        self.assertEqual(status, 405)
        self.assertEqual(headers["Allow"], "POST")

    def test_foreign_origin_returns_403(self):
        status, _, _ = m.handle("POST", {"origin": "https://evil.example"}, b"{}")
        self.assertEqual(status, 403)

    def test_allowed_origin_passes(self):
        status, _, payload = self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"}, {"origin": "https://allowed.example"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["result"], {})

    def test_body_too_large_returns_413(self):
        status, _, _ = m.handle("POST", {}, b" " * 70000)
        self.assertEqual(status, 413)

    def test_bad_json_returns_parse_error(self):
        status, _, payload = self.post(None, raw=b"{not json")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], -32700)

    def test_batch_is_invalid_request(self):
        status, _, payload = self.post([{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], -32600)

    def test_notification_returns_202(self):
        status, _, payload = self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(status, 202)
        self.assertIsNone(payload)


class TestLegacy(McpTestCase):
    def test_initialize_echoes_supported_version(self):
        status, _, payload = self.post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                                   "clientInfo": {"name": "c", "version": "1"}}})
        self.assertEqual(status, 200)
        self.assertEqual(payload["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(payload["result"]["capabilities"], {"tools": {}})
        self.assertEqual(payload["result"]["serverInfo"]["name"], "Nikita")
        self.assertNotIn("resultType", payload["result"])

    def test_initialize_with_unknown_version_offers_latest_legacy(self):
        _, _, payload = self.post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": "2024-11-05"}})
        self.assertEqual(payload["result"]["protocolVersion"], "2025-11-25")

    def test_tools_list(self):
        status, _, payload = self.post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                                       {"mcp-protocol-version": "2025-11-25"})
        self.assertEqual(status, 200)
        tools = payload["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], ["query"])
        self.assertEqual(tools[0]["inputSchema"]["properties"]["max_rows"]["maximum"], 100)
        self.assertTrue(tools[0]["annotations"]["readOnlyHint"])

    def test_missing_version_header_is_2025_03_26(self):
        status, _, payload = self.post({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["result"], {})

    def test_modern_header_without_meta_is_mismatch(self):
        status, _, payload = self.post({"jsonrpc": "2.0", "id": 2, "method": "ping"},
                                       {"mcp-protocol-version": MODERN})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], -32020)

    def test_unknown_method_is_jsonrpc_error_with_200(self):
        status, _, payload = self.post({"jsonrpc": "2.0", "id": 3, "method": "resources/list"},
                                       {"mcp-protocol-version": "2025-11-25"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["error"]["code"], -32601)

    def test_tools_call_runs_query(self):
        fake_result = {"columns": [{"name": "c", "type": "UInt64"}], "rows": [[5]], "row_count": 1,
                       "truncated": False, "elapsed_ms": 3}
        with unittest.mock.patch.object(q, "run_query", return_value=fake_result) as run_query:
            status, _, payload = self.post({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                            "params": {"name": "query", "arguments": {"sql": "SELECT count()", "max_rows": 5000}}},
                                           {"mcp-protocol-version": "2025-11-25"})
        self.assertEqual(status, 200)
        run_query.assert_called_once_with("SELECT count()", 100)                # max_rows обрезан до MCP_MAX_ROWS
        result = payload["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"], fake_result)
        self.assertEqual(json.loads(result["content"][0]["text"]), fake_result)


class TestModern(McpTestCase):
    def test_discover(self):
        status, _, payload = self.post(modern_request("server/discover"), modern_headers("server/discover"))
        self.assertEqual(status, 200)
        result = payload["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["supportedVersions"][0], MODERN)
        self.assertIn("2025-11-25", result["supportedVersions"])
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertEqual(result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"], "Nikita")

    def test_tools_list_has_result_type(self):
        status, _, payload = self.post(modern_request("tools/list"), modern_headers("tools/list"))
        self.assertEqual(status, 200)
        self.assertEqual(payload["result"]["resultType"], "complete")

    def test_version_header_mismatch(self):
        status, _, payload = self.post(modern_request("tools/list"), modern_headers("tools/list", version="2025-11-25"))
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], -32020)

    def test_method_header_mismatch(self):
        status, _, payload = self.post(modern_request("tools/list"), modern_headers("ping"))
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], -32020)

    def test_missing_name_header_on_tools_call(self):
        request = modern_request("tools/call", {"name": "query", "arguments": {"sql": "SELECT 1"}})
        status, _, payload = self.post(request, modern_headers("tools/call"))
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], -32020)

    def test_base64_name_header_is_decoded(self):
        fake_result = {"columns": [], "rows": [], "row_count": 0, "truncated": False, "elapsed_ms": 1}
        encoded = "=?base64?" + base64.b64encode(b"query").decode("ascii") + "?="
        request = modern_request("tools/call", {"name": "query", "arguments": {"sql": "SELECT 1"}})
        with unittest.mock.patch.object(q, "run_query", return_value=fake_result):
            status, _, payload = self.post(request, modern_headers("tools/call", name=encoded))
        self.assertEqual(status, 200)
        self.assertEqual(payload["result"]["resultType"], "complete")

    def test_unsupported_version(self):
        request = modern_request("tools/list", version="1900-01-01")
        status, _, payload = self.post(request, modern_headers("tools/list", version="1900-01-01"))
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], -32022)
        self.assertEqual(payload["error"]["data"]["requested"], "1900-01-01")
        self.assertIn(MODERN, payload["error"]["data"]["supported"])

    def test_unknown_method_returns_404(self):
        status, _, payload = self.post(modern_request("resources/list"), modern_headers("resources/list"))
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], -32601)

    def test_unknown_tool_is_invalid_params(self):
        request = modern_request("tools/call", {"name": "drop_everything", "arguments": {}})
        status, _, payload = self.post(request, modern_headers("tools/call", name="drop_everything"))
        self.assertEqual(status, 200)
        self.assertEqual(payload["error"]["code"], -32602)

    def test_query_error_is_tool_error(self):
        request = modern_request("tools/call", {"name": "query", "arguments": {"sql": "INSERT INTO t VALUES (1)"}})
        with unittest.mock.patch.object(q, "run_query", side_effect=q.QueryError("ClickHouse error 164: readonly")):
            status, _, payload = self.post(request, modern_headers("tools/call", name="query"))
        self.assertEqual(status, 200)
        self.assertTrue(payload["result"]["isError"])
        self.assertIn("164", payload["result"]["content"][0]["text"])

    def test_empty_sql_is_tool_error(self):
        request = modern_request("tools/call", {"name": "query", "arguments": {"sql": "  "}})
        with unittest.mock.patch.object(q, "run_query") as run_query:
            _, _, payload = self.post(request, modern_headers("tools/call", name="query"))
        run_query.assert_not_called()
        self.assertTrue(payload["result"]["isError"])


class TestRobustness(McpTestCase):
    def call(self, arguments, name="query"):
        request = modern_request("tools/call", {"name": name, "arguments": arguments})
        return self.post(request, modern_headers("tools/call", name=name))

    def test_non_finite_floats_become_null(self):
        fake_result = {"columns": [], "rows": [[float("nan"), float("inf"), 1.5]], "row_count": 1,
                       "truncated": False, "elapsed_ms": 1}

        def reject(constant):
            raise ValueError(constant)

        request = modern_request("tools/call", {"name": "query", "arguments": {"sql": "SELECT 1"}})
        with unittest.mock.patch.object(q, "run_query", return_value=fake_result):
            status, _, body = m.handle("POST", modern_headers("tools/call", name="query"), json.dumps(request).encode("utf-8"))
        self.assertEqual(status, 200)
        payload = json.loads(body, parse_constant=reject)
        self.assertEqual(payload["result"]["structuredContent"]["rows"], [[None, None, 1.5]])

    def test_datetime_becomes_string(self):
        moment = datetime.datetime(2026, 10, 17, 12, 30, 0)
        fake_result = {"columns": [], "rows": [[moment]], "row_count": 1, "truncated": False, "elapsed_ms": 1}
        with unittest.mock.patch.object(q, "run_query", return_value=fake_result):
            _, _, payload = self.call({"sql": "SELECT now()"})
        result = payload["result"]
        self.assertEqual(result["structuredContent"]["rows"], [[str(moment)]])
        self.assertEqual(json.loads(result["content"][0]["text"])["rows"], [[str(moment)]])

    def test_unexpected_exception_is_internal_error(self):
        with unittest.mock.patch.object(q, "run_query", side_effect=RuntimeError("secret-detail")):
            request = modern_request("tools/call", {"name": "query", "arguments": {"sql": "SELECT 1"}})
            status, headers, body = m.handle("POST", modern_headers("tools/call", name="query"), json.dumps(request).encode("utf-8"))
        self.assertEqual(status, 500)
        self.assertIn("application/json", headers["Content-Type"])
        payload = json.loads(body)
        self.assertEqual(payload["error"]["code"], -32603)
        self.assertEqual(payload["error"]["message"], "Internal error")
        self.assertNotIn(b"secret-detail", body)

    def test_corrupt_base64_name_header(self):
        request = modern_request("tools/call", {"name": "query", "arguments": {"sql": "SELECT 1"}})
        status, _, payload = self.post(request, modern_headers("tools/call", name="=?base64?!!!?="))
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], -32020)

    def test_arguments_not_object(self):
        status, _, payload = self.call("SELECT 1")
        self.assertEqual(status, 200)
        self.assertEqual(payload["error"]["code"], -32602)

    def test_invalid_max_rows_is_tool_error(self):
        for bad in (True, 0):
            with self.subTest(max_rows=bad), unittest.mock.patch.object(q, "run_query") as run_query:
                _, _, payload = self.call({"sql": "SELECT 1", "max_rows": bad})
            run_query.assert_not_called()
            self.assertTrue(payload["result"]["isError"])


if __name__ == '__main__':
    unittest.main()
