from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error

PANEL = Path(__file__).resolve().parents[1] / 'integracao/painel/iniciar_interface.py'
previous = os.getcwd()
spec = importlib.util.spec_from_file_location('panel_validacao', PANEL)
panel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(panel)
os.chdir(previous)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        bindings = patch.multiple(panel, BASE=base, TASK_DB=base/'agenda.sqlite3', AUTH_DB=base/'auth.sqlite3')
        bindings.start()
        self.addCleanup(bindings.stop)
        class Handler(panel.Handler):
            def autorizado(self, master=False):
                return {'login': 'tester', 'csrf': 'test'}
            def log_operacao(self, *args):
                pass
        self.server = panel.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, data=None):
        raw = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(self.url+path, data=raw,
            headers={'Origin': self.url, 'Content-Type': 'application/json', 'X-CSRF-Token': 'test'})
        try:
            with urllib.request.urlopen(req) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as response:
            return response.code, json.load(response)

    def opened(self, phone='5519999991234', **overrides):
        data = dict(cliente_id=1, ean='12345678', produto_descricao='Teste', previsao='2026-10-20',
                    telefone_final='1234', telefone=phone, status='WhatsApp aberto')
        data.update(overrides)
        status, result = self.request('/api/whatsapp-registros', data)
        self.assertEqual(status, 200, result)
        return result['id']

    def valid(self, cid=1, number='5519999991234'):
        status, result = self.request(f'/api/whatsapp-validacao?cliente_id={cid}&numero={number}')
        self.assertEqual(status, 200, result)
        return result['validado']

    def test_only_confirmed_exact_customer_and_number_are_validated(self):
        rid = self.opened()
        self.assertFalse(self.valid())
        self.assertEqual(self.request('/api/whatsapp-confirmar', {'id': rid})[0], 200)
        self.assertTrue(self.valid())
        self.assertTrue(self.valid(number='19999991234'))
        self.assertFalse(self.valid(cid=2))
        self.assertFalse(self.valid(number='5519888881234'))  # Same final digits.
        self.assertEqual(self.request('/api/whatsapp-confirmar', {'id': rid})[0], 409)
        rows = self.request('/api/whatsapp-registros')[1]['registros']
        self.assertTrue(rows[0]['whatsapp_validado'])
        self.assertNotIn('telefone_hash', rows[0])

    def test_legacy_last_four_digits_never_validate_a_number(self):
        rid = self.opened(phone='')
        self.assertEqual(self.request('/api/whatsapp-confirmar', {'id': rid})[0], 200)
        self.assertFalse(self.valid())

    def test_confirmation_cannot_bypass_consent_or_opening(self):
        self.assertEqual(self.request('/api/whatsapp-registros', dict(cliente_id=1, status='Enviado confirmado'))[0], 400)
        rid = self.opened()
        with closing(panel.agenda_conn()) as con, con:
            con.execute("INSERT INTO consentimento_comercial VALUES(1,'recusado','','','2026-10-19','tester')")
        self.assertEqual(self.request('/api/whatsapp-confirmar', {'id': rid})[0], 403)
        self.assertFalse(self.valid())

    def test_confirmation_metadata_survives_result_changes(self):
        rid = self.opened()
        self.assertEqual(self.request('/api/whatsapp-confirmar', {'id': rid})[0], 200)
        row = self.request('/api/whatsapp-registros?cliente_id=1&resumo=1')[1]['registros'][0]
        self.assertTrue(row['confirmado_em'])
        self.assertEqual(row['confirmado_por'], 'tester')
        self.assertEqual(self.request('/api/whatsapp-resultado', {'id': rid, 'resultado': 'Interessado'})[0], 200)
        updated = self.request('/api/whatsapp-registros?cliente_id=1&resumo=1')[1]['registros'][0]
        self.assertEqual(updated['confirmado_em'], row['confirmado_em'])
        self.assertEqual(updated['confirmado_por'], row['confirmado_por'])

    def test_summary_isolates_product_cycle_customer_and_is_not_limited_to_200(self):
        rid = self.opened()
        self.assertEqual(self.request('/api/whatsapp-confirmar', {'id': rid})[0], 200)
        with closing(panel.agenda_conn()) as con, con:
            sql = "INSERT INTO whatsapp_registros(cliente_id,ean,produto_descricao,previsao,telefone_final,status,criado_em) VALUES(?,?,?,?,?,'WhatsApp aberto','2026-10-19')"
            con.executemany(sql, [(1, 'OTHER', 'Outro', '2026-10-21', '1234')]*205)
            con.execute(sql, (2, '12345678', 'Teste', '2026-10-20', '1234'))
            con.execute(sql, (1, '12345678', 'Teste', '2026-11-20', '1234'))
        rows = self.request('/api/whatsapp-registros?cliente_id=1&resumo=1')[1]['registros']
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r['cliente_id'] == 1 for r in rows))
        self.assertEqual(next(r for r in rows if r['id'] == rid)['status'], 'Enviado confirmado')
        self.assertEqual(self.request('/api/whatsapp-registros?resumo=1')[0], 400)

    def test_existing_database_migrates_without_changing_history(self):
        with closing(sqlite3.connect(panel.TASK_DB)) as con, con:
            con.execute('CREATE TABLE whatsapp_registros(id INTEGER PRIMARY KEY,cliente_id INTEGER,ean TEXT,produto_descricao TEXT,previsao TEXT,telefone_final TEXT,status TEXT,criado_em TEXT)')
            con.execute("INSERT INTO whatsapp_registros VALUES(7,1,'ean','old','2026-10-01','1234','Enviado confirmado','2026-10-01')")
        self.assertFalse(self.valid())
        with closing(panel.agenda_conn()) as con:
            self.assertEqual(con.execute('SELECT id,status,telefone_hash FROM whatsapp_registros').fetchall(), [(7,'Enviado confirmado','')])


if __name__ == '__main__':
    unittest.main()
