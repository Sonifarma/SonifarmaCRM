from datetime import date, timedelta
import unittest
from unittest.mock import patch
import test_whatsapp_validacao as validation
panel=validation.panel

class BirthdayTests(unittest.TestCase):
    setUp=validation.ValidationTests.setUp
    stop_server=validation.ValidationTests.stop_server
    request=validation.ValidationTests.request

    def authorize(self):
        return self.request('/api/consentimento-comercial',dict(cliente_id=1,status='autorizado',origem='Balcão autorizado',data_consentimento=date.today().isoformat()))

    def open_birthday(self, **changes):
        data=dict(cliente_id=1,ean='ANIVERSARIO',produto_descricao='Aniversário',tipo_aviso='aniversario',previsao=date.today().replace(day=1).isoformat(),telefone='5519999991234',telefone_final='1234',status='WhatsApp aberto')
        data.update(changes)
        return self.request('/api/whatsapp-registros',data)

    def test_month_pagination_and_local_contact_state(self):
        rows=[dict(pessoaid=i+1,nome=f'Cliente {i}',datanascimento='1990-02-15',ultima_compra='2020-01-01') for i in range(51)]
        with patch.object(panel,'DB',{'fixture':True}),patch.object(panel,'query',return_value=rows) as query,patch.object(panel,'registrar'):
            status,result=self.request('/api/aniversariantes?mes=2&cursor=0')
            self.assertEqual(status,200,result)
            self.assertEqual(len(result['clientes']),50)
            self.assertEqual(result['proximo_cursor'],50)
            self.assertEqual(query.call_args.args[1],(2,True,0))
            self.assertEqual(result['clientes'][0]['campanha'],'aniversario')
            self.assertEqual(result['clientes'][0]['ciclo'],date(date.today().year,2,1).isoformat())
            self.assertEqual(result['clientes'][0]['consentimento'],'nao_informado')
            self.assertEqual(self.request('/api/aniversariantes?mes=2&compras=todos')[0],200)
            self.assertEqual(query.call_args.args[1],(2,False,0))
            for params in ('mes=0','mes=13','mes=abc','cursor=-1','compras=19'):
                self.assertEqual(self.request('/api/aniversariantes?'+params)[0],400)

    def test_authorization_confirmation_and_monthly_duplicate(self):
        self.assertEqual(self.open_birthday()[0],403)
        self.assertEqual(self.authorize()[0],200)
        status,r=self.open_birthday()
        self.assertEqual(status,200,r)
        rid=r['id']
        self.assertEqual(self.request('/api/whatsapp-confirmar',{'id':rid})[0],200)
        self.assertTrue(panel.whatsapp_validado(1,'5519999991234'))
        with panel.agenda_conn() as con:
            con.execute('UPDATE whatsapp_registros SET criado_em=? WHERE id=?',((date.today()-timedelta(days=10)).isoformat(),rid))
        self.assertEqual(self.open_birthday()[0],409)
        record=self.request('/api/whatsapp-registros?cliente_id=1')[1]['registros'][0]
        self.assertEqual(record['tipo_aviso'],'aniversario')
        self.assertEqual(record['ean'],'ANIVERSARIO')
        self.assertTrue(record['confirmado_em'])
        self.assertTrue(record['confirmado_por'])

    def test_two_pending_attempts_cannot_both_be_confirmed_for_same_month(self):
        self.authorize()
        status,first=self.open_birthday()
        self.assertEqual(status,200,first)
        with panel.agenda_conn() as con:
            con.execute('UPDATE whatsapp_registros SET criado_em=? WHERE id=?',((date.today()-timedelta(days=1)).isoformat(),first['id']))
        status,second=self.open_birthday()
        self.assertEqual(status,200,second)
        self.assertEqual(self.request('/api/whatsapp-confirmar',{'id':first['id']})[0],200)
        self.assertEqual(self.request('/api/whatsapp-confirmar',{'id':second['id']})[0],409)

    def test_refusal_after_opening_prevents_confirmation(self):
        self.authorize()
        status,r=self.open_birthday()
        self.assertEqual(status,200,r)
        self.request('/api/consentimento-comercial',dict(cliente_id=1,status='recusado',origem='Pedido no balcão'))
        self.assertEqual(self.request('/api/whatsapp-confirmar',dict(id=r['id']))[0],403)
