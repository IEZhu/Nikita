# -*- coding: utf-8 -*-
"""
Регресс: parser без связи с ClickHouse не должен двигать state и терять пачки.
"""
import os
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

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
             patch.object(P.time, "sleep") as sleep:
            self.parser.parse_file("/tmp/20250101000000.lgp", "BASE")
        parse_lgp.assert_not_called()
        parse_lgd.assert_not_called()
        sleep.assert_called_once_with(g.waits.clickhouse_reconnect)


if __name__ == '__main__':
    unittest.main()
