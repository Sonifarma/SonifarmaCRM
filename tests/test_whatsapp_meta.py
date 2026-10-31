import hashlib,hmac,json,time,urllib.request,urllib.error,io
import unittest
from unittest.mock import patch
import test_whatsapp_validacao as validation
panel=validation.panel
meta=panel.meta
CFG=dict(ENABLED='true',GRAPH_VERSION='v99.0',PHONE_NUMBER_ID='1234567890',WABA_ID='2345678901',ACCESS_TOKEN='test-secret-token',APP_SECRET='test-app-secret',WEBHOOK_VERIFY_TOKEN='test-verify')
MODEL=dict(name='teste_sonifarma',language='pt_BR',components=[dict(type='body',text='Olá {{1}}',count=1)])

class MetaTests(unittest.TestCase):
    setUp=validation.ValidationTests.setUp
    stop_server=validation.ValidationTests.stop_server
    request=validation.ValidationTests.request

    def prepare(self):
        self.enterContext(patch.object(panel,'meta_config',return_value=CFG))
        self.enterContext(patch.object(panel,'meta_modelos',return_value=[MODEL]))
        self.request('/api/consentimento-comercial',dict(cliente_id=1,status='autorizado',origem='Autorização no balcão',data_consentimento='2020-01-01'))

    def send(self,rid='request_meta_000001'):
        return self.request('/api/whatsapp-meta/enviar',dict(cliente_id=1,cliente_nome='Teste',ean='12345678',produto_descricao='Teste',previsao='2026-10-31',telefone_final='1234',telefone='5519999991234',status='WhatsApp aberto',tipo_aviso='recompra',request_id=rid,template_name=MODEL['name'],template_language='pt_BR',template_parameters={'body':['Cliente']}))

    def event(self,status='delivered',request_id='request_meta_000001',phone='5519999991234'):
        return {'object':'whatsapp_business_account','entry':[{'changes':[{'field':'messages','value':{'metadata':{'phone_number_id':CFG['PHONE_NUMBER_ID']},'statuses':[{'id':'wamid.test.1','status':status,'timestamp':str(int(time.time())),'recipient_id':phone,'biz_opaque_callback_data':request_id}]}}]}]}

    def webhook(self,data,signature=True):
        raw=json.dumps(data).encode();sig='sha256='+hmac.new(CFG['APP_SECRET'].encode(),raw,hashlib.sha256).hexdigest()
        req=urllib.request.Request(self.url+'/api/whatsapp-meta/webhook',data=raw,headers={'Content-Type':'application/json','X-Hub-Signature-256':sig if signature else 'sha256=invalid'})
        try:
            with urllib.request.urlopen(req) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as r:return r.code,json.load(r)

    def test_acceptance_is_not_delivery_and_request_is_idempotent(self):
        self.prepare()
        with patch.object(meta,'graph',return_value={'messages':[{'id':'wamid.test.1'}]}) as graph:
            status,result=self.send();self.assertEqual(status,200,result);self.assertEqual(result['estado'],'aceito')
            self.assertEqual(self.send()[1]['id'],result['id']);self.assertEqual(graph.call_count,1)
            self.assertEqual(self.send('request_meta_000002')[0],409)
        self.assertFalse(panel.whatsapp_validado(1,'5519999991234'))
        self.assertEqual(self.request('/api/whatsapp-confirmar',dict(id=result['id']))[0],409)
        self.assertEqual(self.webhook(self.event(),False)[0],403)
        self.assertFalse(panel.whatsapp_validado(1,'5519999991234'))
        self.assertEqual(self.webhook(self.event())[0],200)
        self.assertTrue(panel.whatsapp_validado(1,'5519999991234'))
        self.assertEqual(self.webhook(self.event('read'))[0],200)
        self.assertEqual(self.webhook(self.event('sent'))[0],200)
        rows=self.request('/api/whatsapp-registros?cliente_id=1&resumo=1')[1]['registros']
        self.assertEqual(rows[0]['api_estado'],'read');self.assertEqual(rows[0]['confirmado_por'],'WhatsApp API')

    def test_uncertain_send_is_not_retried_and_late_webhook_recovers(self):
        self.prepare()
        with patch.object(meta,'graph',side_effect=meta.MetaError('timeout',uncertain=True)) as graph:
            self.assertEqual(self.send()[1]['estado'],'incerto')
            self.assertEqual(self.send()[1]['estado'],'incerto');self.assertEqual(graph.call_count,1)
        self.webhook(self.event('delivered'))
        self.assertEqual(self.request('/api/whatsapp-meta/envio?request_id=request_meta_000001')[1]['estado'],'delivered')

    def test_webhook_can_arrive_before_send_returns(self):
        self.prepare()
        def send_first(*args):
            panel.meta_webhook_eventos(self.event('read'),CFG)
            return {'messages':[{'id':'wamid.test.1'}]}
        with patch.object(meta,'graph',side_effect=send_first):self.assertEqual(self.send()[1]['estado'],'read')
        self.assertTrue(panel.whatsapp_validado(1,'5519999991234'))

    def test_wrong_recipient_does_not_update_customer(self):
        self.prepare()
        with patch.object(meta,'graph',return_value={'messages':[{'id':'wamid.test.1'}]}):self.send()
        self.webhook(self.event(phone='5519888881234'))
        self.assertFalse(panel.whatsapp_validado(1,'5519999991234'))
        self.assertEqual(self.request('/api/whatsapp-meta/envio?request_id=request_meta_000001')[1]['estado'],'aceito')

    def test_configuration_secrets_not_exposed_and_challenge_checked(self):
        self.prepare()
        status,r=self.request('/api/whatsapp-meta/status');self.assertEqual(status,200)
        self.assertNotIn(CFG['ACCESS_TOKEN'],json.dumps(r));self.assertNotIn(CFG['APP_SECRET'],json.dumps(r))
        self.assertEqual(self.request('/api/whatsapp-meta/webhook?hub.mode=subscribe&hub.verify_token=test-verify&hub.challenge=123456'),(200,123456))
        self.assertEqual(self.request('/api/whatsapp-meta/webhook?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=123456')[0],403)

    def test_templates_are_approved_text_only_and_include_footer(self):
        rows=[dict(name='ok',language='pt_BR',status='APPROVED',components=[{'type':'BODY','text':'Olá {{1}}'},{'type':'FOOTER','text':'Condições'}]),dict(name='pending',language='pt_BR',status='PENDING',components=[]),dict(name='named',language='pt_BR',status='APPROVED',components=[{'type':'BODY','text':'Olá {{nome}}'}]),dict(name='media',language='pt_BR',status='APPROVED',components=[{'type':'HEADER','format':'IMAGE'}])]
        with patch.object(meta,'graph',return_value={'data':rows}):
            modelos=meta.templates(CFG)
        self.assertEqual([m['name'] for m in modelos],['ok'])
        self.assertEqual(modelos[0]['components'][-1]['text'],'Condições')

    def test_http_5xx_is_uncertain_and_error_never_exposes_token(self):
        response=urllib.error.HTTPError('https://graph.facebook.com',500,'error',{},io.BytesIO(json.dumps({'error':{'code':123,'message':CFG['ACCESS_TOKEN']}}).encode()))
        opener=unittest.mock.Mock();opener.open.side_effect=response
        with patch.object(meta.urllib.request,'build_opener',return_value=opener):
            with self.assertRaises(meta.MetaError) as caught:meta.graph(CFG,'123/messages',{'test':True})
        self.assertTrue(caught.exception.uncertain)
        self.assertNotIn(CFG['ACCESS_TOKEN'],str(caught.exception))

    def test_consent_refusal_and_template_parameters_prevent_send(self):
        self.prepare();self.request('/api/consentimento-comercial',dict(cliente_id=1,status='recusado',origem='Pedido no balcão'))
        with patch.object(meta,'graph') as graph:
            self.assertEqual(self.send()[0],403);graph.assert_not_called()
        with self.assertRaises(ValueError):meta.template_payload(MODEL,{'body':[]},'5519999991234','request_meta_000001')
