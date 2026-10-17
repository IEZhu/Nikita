# -*- coding: utf-8 -*-
"""
Регресс: IB monitor берёт g.ibases_lock и вызывает conf.detect2, который берёт её ещё раз.
С нереентерабельной блокировкой поток вешался навсегда через waits.sleep_on_conf_detection
после старта, а за ним вставали парсеры (inc_parsed_size) и обновление списка файлов.
"""
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import tests.mock_env
tests.mock_env.setup_mocks()

from src import globals as g
import Nikita as N

BASE_ID = "7d815a87-692f-462c-81ba-4a8ad588e650"


class TestIbasesLock(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, g.debug, "on", g.debug.on)
        g.debug.on = False
        saved_ibases = list(g.parser.ibases or [])
        self.addCleanup(setattr, g.parser, "ibases", saved_ibases)
        g.parser.ibases = []
        self.srvinfo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.srvinfo)
        cluster_dir = os.path.join(self.srvinfo, "reg_1541")
        journal_dir = os.path.join(cluster_dir, BASE_ID, g.conf.c1.jr_dir)
        os.makedirs(journal_dir)
        open(os.path.join(journal_dir, g.conf.c1.jr_old_dict_fname), "w").close()
        with open(os.path.join(cluster_dir, g.conf.c1.cluster_file), "w", encoding="UTF8") as fh:
            fh.write('{%s,"plata_buh_prod","","","","","","","Srvr=localhost;Ref=plata_buh_prod"}' % BASE_ID)

    def _finishes(self, target):
        done = threading.Event()

        def run():
            target()
            done.set()

        worker = threading.Thread(target=run, daemon=True)                      # daemon: при регрессии зависший поток не держит тесты
        worker.start()
        return done.wait(5)

    def test_lock_can_be_taken_twice_by_the_same_thread(self):
        def nested():
            with g.ibases_lock:
                with g.ibases_lock:
                    pass
        self.assertTrue(self._finishes(nested))

    def test_ib_monitor_detect_under_the_lock_finishes(self):
        def monitor_step():
            with g.ibases_lock:                                                 # как IB monitor в Nikita.py
                N.conf.detect2(initial2=False, d2_srvinfo=self.srvinfo)
        self.assertTrue(self._finishes(monitor_step))
        self.assertEqual([ib[g.nms.ib.name] for ib in g.parser.ibases], ["PLATA_BUH_PROD"])


if __name__ == '__main__':
    unittest.main()
