from datetime import date,timedelta
from pathlib import Path
import unittest
import sqlite3
import zipfile
from unittest.mock import patch
import test_whatsapp_validacao as validation
panel=validation.panel

class BulkConsentTests(unittest.TestCase):
    setUp=validation.ValidationTests.setUp
    stop_server=validation.ValidationTests.stop_server
    request=validation.ValidationTests.request

    def post(self, **changes):
        data=dict(origem='Balcão',data_consentimento='2020-01-01',clientes_exibidos=[1,2,3])
        data.update(changes)
        return self.request('/api/consentimento-comercial/lote',data)

    def admin(self):
        self.enterContext(patch.object(self.server.RequestHandlerClass,'autorizado',return_value={'login':'master','perfil':'master','csrf':'test'}))
        self.enterContext(patch.object(panel,'DB',{'fixture':True}))
        return self.enterContext(patch.object(panel,'backup_autorizacoes',return_value=Path('/backup/teste.zip')))

    def test_all_customers_authorized_with_history_refusals_and_old_authorizations_preserved(self):
        self.request('/api/consentimento-comercial',dict(cliente_id=2,status='recusado',origem='Solicitado pelo cliente'))
        self.request('/api/consentimento-comercial',dict(cliente_id=3,status='autorizado',origem='WhatsApp anterior',data_consentimento='2019-01-01'))
        backup=self.admin()
        with patch.object(panel,'query',return_value=[{'pessoaid':i} for i in (1,2,3)]):
            status,result=self.post()
            self.assertEqual(status,200,result)
            self.assertEqual((result['atualizados'],result['ja_autorizados'],result['recusas_preservadas']),(1,1,1))
            self.assertEqual(result['preferencias']['1']['status'],'autorizado')
            self.assertEqual(result['preferencias']['2']['status'],'recusado')
            self.assertEqual(self.post()[1]['atualizados'],0)
        backup.assert_called()
        with panel.agenda_conn() as con:
            self.assertEqual(con.execute('SELECT origem,data_consentimento,usuario FROM consentimento_comercial WHERE cliente_id=1').fetchone(),('Balcão','2020-01-01','master'))
            self.assertEqual(con.execute('SELECT origem,data_consentimento FROM consentimento_comercial WHERE cliente_id=3').fetchone(),('WhatsApp anterior','2019-01-01'))
            self.assertEqual(con.execute('SELECT COUNT(*) FROM consentimento_historico WHERE cliente_id=1').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM whatsapp_registros').fetchone()[0],0)
            self.assertIsNone(con.execute('SELECT status FROM consentimento_comercial WHERE cliente_id=99').fetchone())

    def test_requires_admin_and_valid_date(self):
        with patch.object(panel,'query') as query:
            self.assertEqual(self.post()[0],403);query.assert_not_called()
        self.admin()
        with patch.object(panel,'query') as query:
            self.assertEqual(self.post(data_consentimento=(date.today()+timedelta(days=1)).isoformat())[0],400)
            self.assertEqual(self.post(origem='')[0],400);query.assert_not_called()

    def test_failed_backup_or_partial_erp_read_never_authorizes_partial_list(self):
        self.admin()
        with patch.object(panel,'query',return_value=[{'pessoaid':1}]),patch.object(panel,'backup_autorizacoes',side_effect=OSError('backup failed')):
            self.assertEqual(self.post()[0],503)
        with patch.object(panel,'query',side_effect=[[{'pessoaid':i} for i in range(1,1001)],RuntimeError('ERP unavailable')]):
            self.assertEqual(self.post()[0],503)
        with panel.agenda_conn() as con:self.assertEqual(con.execute('SELECT COUNT(*) FROM consentimento_comercial').fetchone()[0],0)

    def test_real_backup_precedes_the_authorization(self):
        self.enterContext(patch.object(self.server.RequestHandlerClass,'autorizado',return_value={'login':'master','perfil':'master','csrf':'test'}))
        self.enterContext(patch.object(panel,'DB',{'fixture':True}))
        self.enterContext(patch.object(panel,'TASK_DB',panel.BASE/'agenda_posvenda.sqlite3'))
        self.enterContext(patch.dict(panel.os.environ,{'BACKUP_DESTINATION':str(panel.BASE/'backups')}))
        with patch.object(panel,'query',return_value=[{'pessoaid':1}]):
            status,result=self.post()
        self.assertEqual(status,200,result)
        with zipfile.ZipFile(result['backup']) as z:
            before=panel.BASE/'antes.sqlite3';before.write_bytes(z.read('agenda_posvenda.sqlite3'))
        with sqlite3.connect(before) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM consentimento_comercial').fetchone()[0],0)
        with panel.agenda_conn() as con:
            self.assertEqual(con.execute('SELECT status FROM consentimento_comercial WHERE cliente_id=1').fetchone()[0],'autorizado')

    def test_more_than_one_page_is_processed(self):
        self.admin()
        with patch.object(panel,'query',side_effect=[[{'pessoaid':i} for i in range(1,1001)],[{'pessoaid':1001}]]) as query:
            result=self.post()[1]
            self.assertEqual(result['atualizados'],1001)
            self.assertEqual(query.call_args_list[1].args[1],(1000,))
