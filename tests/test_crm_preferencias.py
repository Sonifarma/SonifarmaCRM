import unittest
from unittest.mock import patch
import test_whatsapp_validacao as validation
panel=validation.panel

class CRMPreferencesTests(unittest.TestCase):
    setUp=validation.ValidationTests.setUp
    stop_server=validation.ValidationTests.stop_server
    request=validation.ValidationTests.request
    opened=validation.ValidationTests.opened

    def test_favorites_private_and_templates_shared_persist(self):
        favoritos=[dict(tela='aniv',nome='Aniversariantes recentes',filtros={'campos':{'aniv-compras':'18'}})]
        modelos={'cupom':'NIVER10','beneficio':'Desconto de teste','condicoes':'Somente manipulados','aniversario':'Olá {nome}, cupom {cupom}','reativacao':'Olá {nome}!'}
        self.assertEqual(self.request('/api/crm-config',dict(chave='favoritos',valor=favoritos))[0],200)
        self.assertEqual(self.request('/api/crm-config',dict(chave='modelos',valor=modelos))[0],200)
        self.assertEqual(self.request('/api/crm-config')[1],dict(favoritos=favoritos,modelos=modelos))
        with patch.object(self.server.RequestHandlerClass,'autorizado',return_value={'login':'outro','csrf':'test'}):
            self.assertEqual(self.request('/api/crm-config')[1],dict(favoritos=[],modelos=modelos))
        self.assertEqual(self.request('/api/crm-config',dict(chave='favoritos',valor=[]))[0],200)
        self.assertEqual(self.request('/api/crm-config')[1]['favoritos'],[])

    def test_rejects_invalid_config(self):
        for data in [dict(chave='outro',valor={}),dict(chave='modelos',valor={'cupom':123}),dict(chave='modelos',valor={'aniversario':'a'*6001}),dict(chave='favoritos',valor=[dict(tela='inexistente',nome='x',filtros={})])]:
            self.assertEqual(self.request('/api/crm-config',data)[0],400)

    def test_long_templates_and_optional_campaign_models_are_saved(self):
        modelos={'aniversario':'a'*5000,'recompra':'Olá {nome}: {produto}', 'popular_hoje':'Olá {nome}, previsão {data}'}
        self.assertEqual(self.request('/api/crm-config',dict(chave='modelos',valor=modelos))[0],200)
        self.assertEqual(self.request('/api/crm-config')[1]['modelos'],modelos)

    def test_contact_states_open_waiting_confirmed_refused_and_missing(self):
        self.assertEqual(self.request('/api/crm-status?cliente_id=1')[1]['central'],'nao_enviado')
        rid=self.opened()
        self.assertEqual(self.request('/api/crm-status?cliente_id=1')[1]['central'],'aberto')
        self.request('/api/whatsapp-confirmar',{'id':rid})
        self.assertEqual(self.request('/api/crm-status?cliente_id=1')[1]['central'],'aguardando')
        self.request('/api/whatsapp-resultado',{'id':rid,'resultado':'Interessado'})
        self.assertEqual(self.request('/api/crm-status?cliente_id=1')[1]['central'],'confirmado')
        self.request('/api/consentimento-comercial',dict(cliente_id=1,status='recusado',origem='Pedido no balcão'))
        self.assertEqual(self.request('/api/crm-status?cliente_id=1')[1]['central'],'recusado')
        other=self.opened(cliente_id=2)
        self.request('/api/whatsapp-sem-conta',dict(cliente_id=2,telefone='5519999991234',registro_id=other))
        self.assertEqual(self.request('/api/crm-status?cliente_id=2')[1]['central'],'sem_whatsapp')
        self.assertEqual(self.request('/api/crm-status?cliente_id=abc')[0],400)
