# -*- coding: utf-8 -*-
import unittest
import sys
import os
import threading
from unittest.mock import MagicMock, patch

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import tests.mock_env
tests.mock_env.setup_mocks()

from clickhouse_driver import errors as ch_errors
from src import sender
from src import globals as g


class _FakeResponse:
    """Минимальная заглушка requests.Response для send_to_solr."""
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


# Реальное тело ответа Solr, когда ядро ещё грузится (взято из debug-лога старта)
SOLR_CORE_LOADING_BODY = (
    '{\n  "error":{\n'
    '    "metadata":["error-class","org.apache.solr.common.SolrException",'
    '"root-error-class","org.apache.solr.common.SolrException"],\n'
    '    "msg":"SolrCore is loading",\n'
    '    "code":503\n  }\n}'
)


class TestSendToSolr(unittest.TestCase):
    def setUp(self):
        # debug_print не должен писать в файл во время теста
        g.debug.on = False
        # чистая статистика на каждый тест
        g.stats.solr_total_errors = 0
        g.stats.solr_total_sent = 0
        g.stats.last_errors = []
        g.stats.solr_connection_ok = False
        g.stats.solr_last_error_msg = ""
        # восстановим started после теста, чтобы состояние не утекло в другие модули (порядок-независимость)
        self.addCleanup(setattr, g.execution.solr, "started", g.execution.solr.started)
        # по умолчанию Solr считаем уже запущенным
        g.execution.solr.started = True

    def _send(self, status_code, body=""):
        with patch.object(sender.requests, "post",
                          return_value=_FakeResponse(status_code, body)):
            return sender.send_to_solr(
                "http://127.0.0.1:8983/solr/TEST/update?wt=json&commit=true",
                [{"id": "1"}],
                "test",
            )

    def test_core_loading_503_after_started_is_not_counted(self):
        """Регресс: 503 'SolrCore is loading' пришёл ПОСЛЕ started=True (гонка
        /admin/ping vs /update) - это транзиентное состояние, не ошибка сервиса."""
        ret = self._send(503, SOLR_CORE_LOADING_BODY)
        self.assertEqual(ret, 503)                                   # retriable -> пачка повторится
        self.assertEqual(g.stats.solr_total_errors, 0)              # счётчик ошибок НЕ растёт
        self.assertEqual(len(g.stats.last_errors), 0)               # дашборд не засоряется
        self.assertFalse(g.stats.solr_connection_ok)               # но текущее здоровье -> not-ok (видимость залипшего ядра)

    def test_real_503_after_started_is_counted(self):
        """Контроль: 503 без 'SolrCore is loading' после старта - настоящая ошибка,
        подавлять её нельзя (иначе спрячем реальную недоступность Solr)."""
        ret = self._send(503, '{"error":{"msg":"Service Unavailable","code":503}}')
        self.assertEqual(ret, 503)
        self.assertEqual(g.stats.solr_total_errors, 1)
        self.assertEqual(len(g.stats.last_errors), 1)

    def test_real_500_is_counted(self):
        """Контроль: 500 после старта - настоящая ошибка, считается."""
        ret = self._send(500, "Internal Server Error")
        self.assertEqual(ret, 500)
        self.assertEqual(g.stats.solr_total_errors, 1)
        self.assertEqual(len(g.stats.last_errors), 1)

    def test_404_before_started_is_not_counted(self):
        """Существующее поведение: пока started=False, 404 (ядро ещё не создано)
        ожидаем и не считаем ошибкой."""
        g.execution.solr.started = False                        # восстановление зарегистрировано в setUp
        ret = self._send(404, '{"error":{"msg":"Not Found","code":404}}')
        self.assertEqual(ret, 404)
        self.assertEqual(g.stats.solr_total_errors, 0)
        self.assertEqual(len(g.stats.last_errors), 0)

    def test_success_200_increments_sent(self):
        """Успешная отправка увеличивает счётчик отправленных и не плодит ошибок."""
        ret = self._send(200, "{}")
        self.assertEqual(ret, 200)
        self.assertEqual(g.stats.solr_total_errors, 0)
        self.assertEqual(g.stats.solr_total_sent, 1)
        self.assertTrue(g.stats.solr_connection_ok)                 # успех восстанавливает здоровье (само-сброс сигнала core-loading)


class TestClickhouseWithoutClient(unittest.TestCase):
    """Регресс: без клиента ClickHouse пачка считалась отправленной, state уходил вперёд, данные терялись."""

    def setUp(self):
        g.debug.on = False
        self.addCleanup(setattr, g.conf.clickhouse, "enabled", g.conf.clickhouse.enabled)
        self.addCleanup(setattr, g.conf.solr, "enabled", g.conf.solr.enabled)
        self.addCleanup(setattr, g.conf.redis, "enabled", g.conf.redis.enabled)
        g.conf.solr.enabled = False
        g.conf.redis.enabled = False

    def test_enabled_clickhouse_without_client_is_a_failure(self):
        g.conf.clickhouse.enabled = True
        self.assertFalse(sender.send_to_clickhouse(None, [{"id": "1"}], "test", "test"))

    def test_disabled_clickhouse_without_client_is_skipped(self):
        g.conf.clickhouse.enabled = False
        self.assertTrue(sender.send_to_clickhouse(None, [{"id": "1"}], "test", "test"))

    def test_post_query_keeps_the_batch_when_clickhouse_is_unreachable(self):
        g.conf.clickhouse.enabled = True
        ret = sender.post_query(None, "", [{"id": "1"}], "test", "test", bypass_redis=True)
        self.assertNotEqual(ret, 200)                                # не 200 -> commit не пометит блок отправленным


class TestConnectClickhouse(unittest.TestCase):
    def setUp(self):
        g.debug.on = False

    def test_returns_client_checked_with_select_1(self):
        client = MagicMock()
        with patch.object(sender, "ch", return_value=client) as factory:
            self.assertIs(sender.connect_clickhouse("test", database="zhr1c"), client)
        client.execute.assert_called_once_with("SELECT 1")
        self.assertEqual(factory.call_args.kwargs["database"], "zhr1c")

    def test_without_database_does_not_pass_one(self):
        with patch.object(sender, "ch", return_value=MagicMock()) as factory:
            sender.connect_clickhouse("test")
        self.assertNotIn("database", factory.call_args.kwargs)        # базы может ещё не быть, её создаёт parser

    def test_returns_none_when_clickhouse_is_down(self):
        client = MagicMock()
        client.execute.side_effect = ch_errors.NetworkError("Connection refused")
        with patch.object(sender, "ch", return_value=client):
            self.assertIsNone(sender.connect_clickhouse("test"))

    def test_failed_connect_is_recorded_without_flooding_last_errors(self):
        self.addCleanup(setattr, g.stats, "last_errors", g.stats.last_errors)
        self.addCleanup(setattr, g.stats, "clickhouse_total_errors", g.stats.clickhouse_total_errors)
        self.addCleanup(setattr, g.stats, "clickhouse_last_error_msg", g.stats.clickhouse_last_error_msg)
        self.addCleanup(setattr, g.stats, "clickhouse_last_error_time", g.stats.clickhouse_last_error_time)
        g.stats.last_errors = []
        g.stats.clickhouse_total_errors = 0
        client = MagicMock()
        client.execute.side_effect = ch_errors.NetworkError("Connection refused")
        with patch.object(sender, "ch", return_value=client):
            sender.connect_clickhouse("test")
            sender.connect_clickhouse("test")                            # gate повторяет попытку каждые 10 с в каждом потоке
        self.assertEqual(g.stats.clickhouse_total_errors, 2)
        self.assertIn("Connection refused", g.stats.clickhouse_last_error_msg)
        self.assertIsNotNone(g.stats.clickhouse_last_error_time)
        self.assertEqual(len(g.stats.last_errors), 1)                   # одна и та же ошибка не вытесняет другие из последних 10


class TestSenderThread(unittest.TestCase):
    """Один оборот sender_thread.run: переподключение и судьба пачки из Redis."""

    def setUp(self):
        g.debug.on = False
        self.addCleanup(setattr, g.conf.clickhouse, "enabled", g.conf.clickhouse.enabled)
        self.addCleanup(setattr, g.conf.redis, "enabled", g.conf.redis.enabled)
        g.conf.clickhouse.enabled = True
        g.conf.redis.enabled = True
        self.thread = sender.sender_thread.__new__(sender.sender_thread)
        threading.Thread.__init__(self.thread)
        self.thread.name = "sender (test)"
        self.thread.stop_signal = False
        self.thread.chclient = None
        self.queue = MagicMock()

        def pop_once(timeout):
            self.thread.stop_signal = True                              # один оборот цикла
            return ("BASE", [{"id": "1"}], "payload")

        self.queue.pop.side_effect = pop_once

    def _run(self, client, ret_code):
        with patch.object(sender, "queue", self.queue), \
             patch.object(sender, "connect_clickhouse", return_value=client) as connect, \
             patch.object(sender, "post_query", return_value=ret_code), \
             patch.object(sender.time, "sleep"):
            self.thread.run()
        return connect

    def test_reconnects_and_requeues_while_clickhouse_is_down(self):
        connect = self._run(None, 500)
        connect.assert_called_once_with(self.thread.name, database=g.conf.clickhouse.database)
        self.queue.requeue.assert_called_once_with("payload")
        self.queue.ack.assert_not_called()

    def test_acks_after_reconnect_and_a_successful_send(self):
        client = MagicMock()
        self._run(client, 200)
        self.assertIs(self.thread.chclient, client)
        self.queue.ack.assert_called_once_with("payload")
        self.queue.requeue.assert_not_called()


if __name__ == '__main__':
    unittest.main()
