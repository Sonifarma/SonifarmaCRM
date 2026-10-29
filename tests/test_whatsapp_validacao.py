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

    def test_missing_number_updates_all_pending_attempts_even_without_current_id(self):
        first=self.opened(tipo_aviso='popular_hoje')
        second=self.opened(tipo_aviso='popular_amanha')
        other=self.opened(phone='5519888881234', ean='99999999')
        self.assertEqual(self.request('/api/whatsapp-sem-conta',dict(
            cliente_id=1,telefone='5519999991234'))[0],200)
        for route in ('/api/whatsapp-registros', '/api/whatsapp-registros?cliente_id=1&resumo=1'):
            rows={r['id']:r for r in self.request(route)[1]['registros']}
            self.assertEqual(rows[first]['status'],'Número sem WhatsApp')
            self.assertEqual(rows[second]['status'],'Número sem WhatsApp')
            self.assertEqual(rows[other]['status'],'WhatsApp aberto')
        # Older installations may already contain a marker without updated attempts.
        with panel.agenda_conn() as con:
            con.execute("UPDATE whatsapp_registros SET status='WhatsApp aberto',criado_em='2020-01-01' WHERE id=?",(first,))
        rows={r['id']:r for r in self.request('/api/whatsapp-registros?cliente_id=1&resumo=1')[1]['registros']}
        self.assertEqual(rows[first]['status'],'Número sem WhatsApp')

    def test_missing_whatsapp_is_specific_to_customer_and_phone(self):
        rid = self.opened()
        status, result = self.request('/api/whatsapp-sem-conta', dict(cliente_id=1,
            telefone='5519999991234', registro_id=rid))
        self.assertEqual(status, 200, result)
        self.assertFalse(self.valid())
        state = self.request('/api/whatsapp-validacao?cliente_id=1&numero=5519999991234')[1]
        self.assertTrue(state['sem_whatsapp'])
        self.assertFalse(self.request('/api/whatsapp-validacao?cliente_id=2&numero=5519999991234')[1]['sem_whatsapp'])
        self.assertFalse(self.request('/api/whatsapp-validacao?cliente_id=1&numero=5519888881234')[1]['sem_whatsapp'])
        self.assertEqual(self.request('/api/whatsapp-confirmar', {'id':rid})[0],409)
        rows=self.request('/api/whatsapp-registros?cliente_id=1')[1]['registros']
        self.assertEqual(rows[0]['status'],'Número sem WhatsApp')
        self.assertIsNone(rows[0]['confirmado_em'])

    def test_missing_whatsapp_rejects_unrelated_attempt_and_can_be_revalidated(self):
        rid=self.opened()
        self.assertEqual(self.request('/api/whatsapp-sem-conta', dict(cliente_id=2,
            telefone='5519999991234',registro_id=rid))[0],409)
        self.assertEqual(self.request('/api/whatsapp-confirmar',{'id':rid})[0],200)
        self.assertTrue(self.valid())
        self.assertEqual(self.request('/api/whatsapp-sem-conta',dict(cliente_id=1,
            telefone='5519999991234'))[0],200)
        self.assertFalse(self.valid())
        with panel.agenda_conn() as con:
            con.execute("UPDATE whatsapp_registros SET criado_em='2020-01-01' WHERE id=?",(rid,))
        newer=self.opened(ean='87654321',previsao='2026-10-25')
        self.assertEqual(self.request('/api/whatsapp-confirmar',{'id':newer})[0],200)
        self.assertTrue(self.valid())

    def test_overdue_popular_reminder_is_recorded_confirmed_and_deduplicated(self):
        rid = self.opened(tipo_aviso='popular_atrasado', previsao='2020-01-01')
        self.assertEqual(self.request('/api/whatsapp-confirmar', {'id': rid})[0], 200)
        status, result = self.request('/api/whatsapp-registros', dict(
            cliente_id=1, ean='12345678', produto_descricao='Teste',
            tipo_aviso='popular_atrasado', previsao='2020-01-01',
            telefone_final='1234', telefone='5519999991234', status='WhatsApp aberto'))
        self.assertEqual(status, 409, result)
        self.assertTrue(self.valid())

    def test_summary_keeps_popular_reminder_types_and_confirmation_dates(self):
        tomorrow = self.opened(tipo_aviso='popular_amanha')
        today = self.opened(tipo_aviso='popular_hoje')
        self.assertEqual(self.request('/api/whatsapp-confirmar', {'id': tomorrow})[0], 200)
        rows = self.request('/api/whatsapp-registros?cliente_id=1&resumo=1')[1]['registros']
        self.assertEqual(len(rows), 2)
        self.assertEqual({r['tipo_aviso'] for r in rows}, {'popular_amanha', 'popular_hoje'})
        self.assertEqual({r['ean'] for r in rows}, {'FARMACIA_POPULAR'})
        confirmed = next(r for r in rows if r['id'] == tomorrow)
        self.assertEqual(confirmed['status'], 'Enviado confirmado')
        self.assertTrue(confirmed['confirmado_em'])
        self.assertIsNone(next(r for r in rows if r['id'] == today)['confirmado_em'])

    def test_legacy_confirmation_date_uses_only_explicit_audit_event(self):
        rid = self.opened(tipo_aviso='popular_amanha')
        with closing(panel.agenda_conn()) as con, con:
            con.execute("UPDATE whatsapp_registros SET status='Enviado confirmado',atualizado_em='2026-10-20T12:00:00' WHERE id=?", (rid,))
        row = self.request('/api/whatsapp-registros?cliente_id=1&resumo=1')[1]['registros'][0]
        self.assertIsNone(row['confirmado_em'])
        panel.registrar('outro_cliente', 'Envio confirmado', 2, rid)
        row = self.request('/api/whatsapp-registros?cliente_id=1&resumo=1')[1]['registros'][0]
        self.assertIsNone(row['confirmado_em'])
        panel.registrar('atendente', 'Envio confirmado', 1, rid)
        with closing(panel.auth_conn()) as con, con:
            con.execute("UPDATE auditoria SET momento='2026-10-20T09:15:00' WHERE atividade='Envio confirmado'")
        row = self.request('/api/whatsapp-registros?cliente_id=1&resumo=1')[1]['registros'][0]
        self.assertEqual(row['confirmado_em'], '2026-10-20T09:15:00')
        self.assertEqual(row['confirmado_por'], 'atendente')
        with closing(panel.agenda_conn()) as con:
            self.assertIsNone(con.execute('SELECT confirmado_em FROM whatsapp_registros WHERE id=?',(rid,)).fetchone()[0])

    def authorize_reactivation(self, status='autorizado'):
        code, result = self.request('/api/consentimento-comercial', {
            'cliente_id': 1, 'status': status, 'origem': 'Autorizacao no balcao',
            'data_consentimento': panel.date.today().isoformat()})
        self.assertEqual(code, 200, result)

    def test_reactivation_state_persists_opening_confirmation_and_number_validation(self):
        status, state = self.request('/api/reativacao-contato?cliente_id=1')
        self.assertEqual(status, 200)
        self.assertEqual(state['consentimento'], 'nao_informado')
        self.assertIsNone(state['ultimo'])
        self.authorize_reactivation()
        rid = self.opened(tipo_aviso='reativacao')
        state = self.request('/api/reativacao-contato?cliente_id=1')[1]
        self.assertEqual(state['ultimo']['tipo_aviso'], 'reativacao')
        self.assertEqual(state['ultimo']['status'], 'WhatsApp aberto')
        self.assertFalse(state['contato_recente'])
        self.assertEqual(self.request('/api/whatsapp-confirmar', {'id': rid})[0], 200)
        for _ in range(2):
            state = self.request('/api/reativacao-contato?cliente_id=1')[1]
            self.assertTrue(state['contato_recente'])
            self.assertEqual(state['ultimo']['status'], 'Enviado confirmado')
            self.assertEqual(state['ultimo']['confirmado_por'], 'tester')
            self.assertTrue(state['ultimo']['confirmado_em'])
        self.assertTrue(self.valid())
        other = self.request('/api/reativacao-contato?cliente_id=2')[1]
        self.assertIsNone(other['ultimo'])
        self.assertFalse(other['contato_recente'])

    def test_reactivation_requires_consent_and_respects_contacts_from_other_modules(self):
        payload = dict(cliente_id=1, tipo_aviso='reativacao', status='WhatsApp aberto',
                       previsao='2026-10-20', telefone_final='1234', telefone='5519999991234')
        self.assertEqual(self.request('/api/whatsapp-registros', payload)[0], 403)
        self.authorize_reactivation()
        rid = self.opened(tipo_aviso='popular_hoje')
        self.assertEqual(self.request('/api/whatsapp-confirmar', {'id': rid})[0], 200)
        state = self.request('/api/reativacao-contato?cliente_id=1')[1]
        self.assertTrue(state['contato_recente'])
        self.assertIsNone(state['ultimo'])
        self.assertEqual(self.request('/api/whatsapp-registros', payload)[0], 409)
        self.authorize_reactivation('recusado')
        self.assertEqual(self.request('/api/whatsapp-registros', payload)[0], 403)
        self.assertEqual(self.request('/api/reativacao-contato?cliente_id=0')[0], 400)

    def test_existing_database_migrates_without_changing_history(self):
        with closing(sqlite3.connect(panel.TASK_DB)) as con, con:
            con.execute('CREATE TABLE whatsapp_registros(id INTEGER PRIMARY KEY,cliente_id INTEGER,ean TEXT,produto_descricao TEXT,previsao TEXT,telefone_final TEXT,status TEXT,criado_em TEXT)')
            con.execute("INSERT INTO whatsapp_registros VALUES(7,1,'ean','old','2026-10-01','1234','Enviado confirmado','2026-10-01')")
        self.assertFalse(self.valid())
        with closing(panel.agenda_conn()) as con:
            self.assertEqual(con.execute('SELECT id,status,telefone_hash FROM whatsapp_registros').fetchall(), [(7,'Enviado confirmado','')])


if __name__ == '__main__':
    unittest.main()
