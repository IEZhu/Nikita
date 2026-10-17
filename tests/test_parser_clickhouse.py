# -*- coding: utf-8 -*-
"""
Регресс: parser без связи с ClickHouse не должен двигать state и терять пачки.
"""
import os
import sys
import threading
import unittest
from unittest.mock import MagicMock, call, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import tests.mock_env
tests.mock_env.setup_mocks()

from src import parser as P
from src import globals as g


def _make_parser_instance():
    """parser без запуска потоков и подключения: только база Thread + нужные поля."""
    inst = P.parser.__new__(P.parser)
    threading.Thread.__init__(inst)
    inst.name = 'clickhouse parser (test)'
    inst.json_data = {inst.name: []}
    inst.stopMe = False
    inst.chclient = None
    return inst


class TestParserWithoutClickhouse(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, g.debug, "on", g.debug.on)
        self.addCleanup(setattr, g.conf.clickhouse, "enabled", g.conf.clickhouse.enabled)
        g.debug.on = False
        g.conf.clickhouse.enabled = True
        self.parser = _make_parser_instance()

    def test_failed_commit_drops_the_batch_and_keeps_the_state(self):
        name = self.parser.name
        self.parser.json_data[name] = [{"id": "a"}]
        file_state = {"filename": "f.lgp", "filesize": 10, "filesizeread": 10}
        with patch.object(P.state_manager, "is_block_committed", return_value=False), \
             patch.object(P.state_manager, "log_committed_block") as log_block, \
             patch.object(P.state_manager, "update_file_state") as update_state, \
             patch.object(self.parser, "solr_post_json_data", return_value=False):
            ok = self.parser.commit_json_data("f.lgp", "BASE", file_state, 0, [{"id": "a"}])
        self.assertFalse(ok)
        self.assertEqual(self.parser.json_data[name], [])                    # иначе остаток уйдёт с пачкой следующего файла, возможно другой базы
        log_block.assert_not_called()
        update_state.assert_not_called()

    def test_ensure_clickhouse_reconnects_and_keeps_the_database_client(self):
        server = MagicMock()
        server.execute.return_value = [("zhr1c",)]
        database = MagicMock()
        with patch.object(P.snd, "connect_clickhouse", side_effect=[server, database]) as connect:
            self.assertTrue(self.parser.ensure_clickhouse())
        self.assertIs(self.parser.chclient, database)
        self.assertNotIn("database", connect.call_args_list[0].kwargs)          # первое подключение без базы: её может ещё не быть
        self.assertEqual(connect.call_args_list[1].kwargs["database"], g.conf.clickhouse.database)

    def test_ensure_clickhouse_reports_an_unreachable_server(self):
        with patch.object(P.snd, "connect_clickhouse", return_value=None):
            self.assertFalse(self.parser.ensure_clickhouse())
        self.assertIsNone(self.parser.chclient)

    def test_ensure_clickhouse_is_a_no_op_when_disabled(self):
        g.conf.clickhouse.enabled = False
        with patch.object(P.snd, "connect_clickhouse") as connect:
            self.assertTrue(self.parser.ensure_clickhouse())
        connect.assert_not_called()

    def test_parse_file_waits_instead_of_parsing_while_clickhouse_is_down(self):
        with patch.object(self.parser, "ensure_clickhouse", return_value=False), \
             patch.object(self.parser, "parse_lgp_file") as parse_lgp, \
             patch.object(self.parser, "parse_lgd_file") as parse_lgd, \
             patch.object(g.waits, "clickhouse_reconnect", 1), \
             patch.object(g.waits, "stop_check", 0.5), \
             patch.object(P.time, "sleep") as sleep:
            self.parser.parse_file("/tmp/20250101000000.lgp", "BASE")
        parse_lgp.assert_not_called()
        parse_lgd.assert_not_called()
        self.assertEqual(sleep.call_args_list, [call(0.5), call(0.5)])           # пауза короткими шагами, чтобы остановка её не ждала

    def test_wait_ends_early_when_the_parser_stops(self):
        def stop_on_first_step(seconds):
            self.parser.stopMe = True
        with patch.object(P.time, "sleep", side_effect=stop_on_first_step) as sleep:
            self.parser.wait_or_stop(60)
        self.assertEqual(sleep.call_count, 1)

    def test_stopping_parser_does_not_reconnect(self):
        self.parser.stopMe = True
        with patch.object(P.snd, "connect_clickhouse") as connect, \
             patch.object(P.time, "sleep") as sleep:
            self.parser.parse_file("/tmp/20250101000000.lgp", "BASE")
        connect.assert_not_called()
        sleep.assert_not_called()

    def test_next_file_starts_with_an_empty_buffer_after_a_file_failed_mid_way(self):
        """Регресс: исключение в parse_lgd_file оставляло записи в буфере потока, и они уходили с пачкой следующего файла другой базы."""
        g.conf.clickhouse.enabled = False
        name = self.parser.name
        seen_at_start = []

        def fail_after_one_record(pf_name, pf_base):
            self.parser.json_data[name].append({"file_name": pf_name})
            raise ValueError("broken record")

        def remember_buffer(pf_name, pf_base):
            seen_at_start.append(list(self.parser.json_data[name]))

        with patch.object(self.parser, "parse_lgd_file", side_effect=fail_after_one_record):
            self.parser.parse_file("/tmp/a.lgd", "AAA")
        with patch.object(self.parser, "parse_lgd_file", side_effect=remember_buffer):
            self.parser.parse_file("/tmp/b.lgd", "BBB")
        self.assertEqual(seen_at_start, [[]])

    def test_failed_send_drops_the_client_so_the_next_file_waits_for_clickhouse(self):
        client = MagicMock()
        self.parser.chclient = client
        self.parser.json_data[self.parser.name] = [{"id": "a"}]
        file_state = {"filename": "f.lgp", "filesize": 10, "filesizeread": 10}
        with patch.object(P.state_manager, "is_block_committed", return_value=False), \
             patch.object(self.parser, "solr_post_json_data", return_value=False):
            self.parser.commit_json_data("f.lgp", "BASE", file_state, 0, [{"id": "a"}])
        client.disconnect.assert_called_once_with()
        self.assertIsNone(self.parser.chclient)                                  # следующий parse_file пройдёт через ensure_clickhouse и его паузу

    def test_connect_creates_a_missing_database_and_closes_the_server_client(self):
        server = MagicMock()
        server.execute.side_effect = lambda query: [("default",)] if "SHOW DATABASES" in query else None
        database = MagicMock()
        with patch.object(P.snd, "connect_clickhouse", side_effect=[server, database]):
            self.parser.connect_clickhouse()
        queries = [args[0] for args, _ in server.execute.call_args_list]
        self.assertTrue(any("CREATE DATABASE IF NOT EXISTS " + g.conf.clickhouse.database in q for q in queries))
        server.disconnect.assert_called_once_with()
        self.assertIs(self.parser.chclient, database)


if __name__ == '__main__':
    unittest.main()
