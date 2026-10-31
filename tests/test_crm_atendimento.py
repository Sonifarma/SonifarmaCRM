from datetime import datetime,timedelta
import unittest
from unittest.mock import patch
import test_whatsapp_validacao as validation
panel=validation.panel

class AttendanceTests(unittest.TestCase):
    setUp=validation.ValidationTests.setUp
    stop_server=validation.ValidationTests.stop_server
    request=validation.ValidationTests.request
    opened=validation.ValidationTests.opened

    def change(self,op='assumir',rev=0,**fields):
        return self.request('/api/crm-atendimento',dict(cliente_id=1,cliente_nome='Cliente teste',revisao=rev,operacao=op,**fields))

    def test_claim_conflict_release_and_server_contact_protection(self):
        self.assertEqual(self.request('/api/crm-atendimento?cliente_id=1')[1]['atendimento']['revisao'],0)
        status,r=self.change()
        self.assertEqual(status,200,r)
        self.assertEqual(r['atendimento']['responsavel'],'tester')
        with patch.object(self.server.RequestHandlerClass,'autorizado',return_value={'login':'other','csrf':'test'}):
            self.assertEqual(self.change(rev=0)[0],409)
            self.assertEqual(self.change(rev=1)[0],409)
            status,r=self.request('/api/whatsapp-registros',dict(cliente_id=1,ean='12345678',produto_descricao='Teste',previsao='2026-10-31',telefone_final='1234',telefone='5519999991234',status='WhatsApp aberto'))
            self.assertEqual(status,409,r)
            self.assertEqual(self.change('salvar',rev=1,responsavel='other')[0],409)
        self.assertEqual(self.change('liberar',rev=1)[0],200)
        with patch.object(self.server.RequestHandlerClass,'autorizado',return_value={'login':'other','csrf':'test'}):
            self.assertEqual(self.change(rev=2)[0],200)

    def test_save_return_complete_and_date_filters(self):
        stamp=(datetime.now()-timedelta(days=1)).isoformat(timespec='minutes')
        status,r=self.change('salvar',responsavel='tester',acao='Retornar ao cliente',retorno_em=stamp,observacao='Contato operacional')
        self.assertEqual(status,200,r)
        self.assertEqual(self.request('/api/crm-retornos?filtro=meus')[1]['atendimentos'][0]['observacao'],'Contato operacional')
        self.assertEqual(len(self.request('/api/crm-retornos?filtro=atrasados')[1]['atendimentos']),1)
        self.assertEqual(self.request('/api/crm-retornos-resumo')[1]['atrasados'],1)
        self.assertEqual(self.request('/api/crm-retornos?filtro=hoje')[1]['atendimentos'],[])
        with patch.object(self.server.RequestHandlerClass,'autorizado',return_value={'login':'other','csrf':'test'}):
            self.assertEqual(self.request('/api/crm-retornos?filtro=meus')[1]['atendimentos'],[])
        self.assertEqual(self.change('concluir',rev=1)[0],200)
        self.assertEqual(self.request('/api/crm-retornos')[1]['atendimentos'],[])
        self.assertEqual(self.request('/api/crm-retornos-resumo')[1]['atrasados'],0)
        self.assertEqual(len(self.request('/api/crm-retornos?estado=concluidos')[1]['atendimentos']),1)

    def test_validates_fields_and_stale_version_without_losing_data(self):
        self.assertEqual(self.change('salvar',responsavel='tester',acao='Ligar',retorno_em='2026-02-30T10:00')[0],400)
        self.assertEqual(self.change('salvar',responsavel='tester',acao='Ligar')[0],400)
        self.assertEqual(self.change('salvar',responsavel='other')[0],400)
        self.assertEqual(self.change('salvar',responsavel='tester',observacao='Nota salva')[0],200)
        self.assertEqual(self.change('salvar',rev=0,responsavel='tester',observacao='Outra nota')[0],409)
        self.assertEqual(self.request('/api/crm-atendimento?cliente_id=1')[1]['atendimento']['observacao'],'Nota salva')
        self.assertEqual(self.change('salvar',rev=1,responsavel='tester',observacao='ação '*200)[0],200)

    def test_supervisor_can_reassign_to_active_user(self):
        self.change()
        with panel.auth_conn() as con:
            con.execute("INSERT INTO usuarios(login,senha_hash,perfil,ativo,criado_em) VALUES('other','fixture','atendente',1,'2026-01-01')")
        with patch.object(self.server.RequestHandlerClass,'autorizado',return_value={'login':'boss','perfil':'supervisor','csrf':'test'}):
            status,r=self.change('salvar',rev=1,responsavel='other')
            self.assertEqual(status,200,r)
            self.assertEqual(r['atendimento']['responsavel'],'other')

    def test_opening_contact_automatically_assigns_unowned_customer(self):
        self.opened()
        row=self.request('/api/crm-atendimento?cliente_id=1')[1]['atendimento']
        self.assertEqual(row['responsavel'],'tester')
        self.assertEqual(row['revisao'],1)
        with patch.object(self.server.RequestHandlerClass,'autorizado',return_value={'login':'other','csrf':'test'}):
            status,result=self.request('/api/whatsapp-registros',dict(cliente_id=1,ean='99999999',produto_descricao='Teste',previsao='2026-10-31',telefone_final='1234',telefone='5519999991234',status='WhatsApp aberto'))
            self.assertEqual(status,409,result)
