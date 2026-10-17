import unittest
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import tests.mock_env
tests.mock_env.setup_mocks()

from clickhouse_driver import errors as ch_errors
from src import mcp_query as q


class FakeClient:
    """execute_iter как у clickhouse_driver: первым элементом — [(имя, тип)], дальше строки."""

    def __init__(self, columns=None, rows=None, error=None):
        self.columns = columns or []
        self.rows = rows or []
        self.error = error
        self.yielded = 0
        self.disconnected = False

    def execute_iter(self, sql, with_column_types=False):
        assert with_column_types
        if self.error:
            raise self.error
        yield self.columns
        for row in self.rows:
            self.yielded += 1
            yield row

    def disconnect(self):
        self.disconnected = True


class TestRunQuery(unittest.TestCase):
    def test_returns_columns_and_rows(self):
        client = FakeClient([("n", "UInt64"), ("s", "String")], [(1, "a"), (2, "b")])
        result = q.run_query("SELECT 1", 10, client_factory=lambda: client)
        self.assertEqual(result["columns"], [{"name": "n", "type": "UInt64"}, {"name": "s", "type": "String"}])
        self.assertEqual(result["rows"], [[1, "a"], [2, "b"]])
        self.assertEqual(result["row_count"], 2)
        self.assertFalse(result["truncated"])
        self.assertTrue(client.disconnected)

    def test_truncates_and_stops_reading(self):
        client = FakeClient([("n", "UInt64")], [(i,) for i in range(1000)])
        result = q.run_query("SELECT number FROM numbers(1000)", 3, client_factory=lambda: client)
        self.assertEqual(result["rows"], [[0], [1], [2]])
        self.assertTrue(result["truncated"])
        self.assertEqual(client.yielded, 4)                                     # прочитана одна лишняя строка, не весь поток
        self.assertTrue(client.disconnected)

    def test_server_error_becomes_query_error(self):
        client = FakeClient(error=ch_errors.ServerException("Cannot execute query in readonly mode", code=164))
        with self.assertRaises(q.QueryError) as ctx:
            q.run_query("INSERT INTO t VALUES (1)", 10, client_factory=lambda: client)
        self.assertIn("164", str(ctx.exception))
        self.assertIn("readonly", str(ctx.exception))
        self.assertTrue(client.disconnected)

    def test_server_error_drops_the_stack_trace(self):
        message = "Unknown identifier 'x'. Stack trace:\n\n0. DB::Exception::Exception() @ 0x000000000c1d2e3b\n1. DB::f() @ 0x1"
        client = FakeClient(error=ch_errors.ServerException(message, code=47))
        with self.assertRaises(q.QueryError) as ctx:
            q.run_query("SELECT x", 10, client_factory=lambda: client)
        self.assertEqual(str(ctx.exception), "ClickHouse error 47: Unknown identifier 'x'")

    def test_long_server_error_is_capped(self):
        client = FakeClient(error=ch_errors.ServerException("e" * 10000, code=62))
        with self.assertRaises(q.QueryError) as ctx:
            q.run_query("SELECT", 10, client_factory=lambda: client)
        self.assertLessEqual(len(str(ctx.exception)), len("ClickHouse error 62: ") + q.MAX_ERROR_CHARS)

    def test_network_error_becomes_query_error(self):
        client = FakeClient(error=ch_errors.NetworkError("Connection refused"))
        with self.assertRaises(q.QueryError) as ctx:
            q.run_query("SELECT 1", 10, client_factory=lambda: client)
        self.assertIn("недоступен", str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
