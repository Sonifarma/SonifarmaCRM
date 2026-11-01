import hashlib,hmac,json,time,urllib.request,urllib.error
import unittest
from unittest.mock import patch
import test_whatsapp_validacao as validation
panel=validation.panel

TXT='''31/10/2026, 10:00 - Ana Cliente: Olá, qual o horário?
31/10/2026, 10:01 - Sonifarma: Olá Ana Cliente! Nosso horário foi combinado.
31/10/2026, 10:02 - Ana Cliente: Meu CPF é 123.456.789-00 e meu email é ana@example.test
31/10/2026, 10:03 - Sonifarma: Recebido. Vou verificar.
31/10/2026, 10:04 - Ana Cliente: Quantos comprimidos posso tomar?
31/10/2026, 10:05 - Sonifarma: Tome 1 comprimido por dia.
'''

class IAAttendanceTests(unittest.TestCase):
    setUp=validation.ValidationTests.setUp
    stop_server=validation.ValidationTests.stop_server
    request=validation.ValidationTests.request
    opened=validation.ValidationTests.opened

    def admin(self):return patch.object(self.server.RequestHandlerClass,'autorizado',return_value={'login':'super','perfil':'supervisor','csrf':'test'})

    def receive(self,text='Qual o horário?',tipo='text',wamid='incoming.1',phone='5519999991234'):
        cfg=dict(APP_SECRET='fixture-secret',PHONE_NUMBER_ID='123456789',WEBHOOK_VERIFY_TOKEN='verify')
        msg={'id':wamid,'from':phone,'type':tipo,'timestamp':str(int(time.time())),'text':{'body':text}}
        data={'object':'whatsapp_business_account','entry':[{'changes':[{'value':{'metadata':{'phone_number_id':cfg['PHONE_NUMBER_ID']},'contacts':[{'wa_id':phone,'profile':{'name':'Ana Cliente'}}],'messages':[msg]}}]}]}
        raw=json.dumps(data).encode();sig='sha256='+hmac.new(cfg['APP_SECRET'].encode(),raw,hashlib.sha256).hexdigest()
        req=urllib.request.Request(self.url+'/api/whatsapp-meta/webhook',data=raw,headers={'Content-Type':'application/json','X-Hub-Signature-256':sig})
        with patch.object(panel,'meta_config',return_value=cfg):
            with urllib.request.urlopen(req) as response:self.assertEqual(response.status,200)
        return self.request('/api/ia/mensagens')[1]['mensagens'][0]

    def test_incoming_is_stored_once_and_does_not_send_or_validate(self):
        m=self.receive();self.receive()
        rows=self.request('/api/ia/mensagens')[1]['mensagens']
        self.assertEqual(len(rows),1);self.assertEqual(m['estado'],'novo')
        self.assertIsNone(m['cliente_id']);self.assertEqual(m['telefone_final'],'1234')
        self.assertNotIn('5519999991234',json.dumps(rows))
        with panel.agenda_conn() as con:self.assertEqual(con.execute('SELECT COUNT(*) FROM whatsapp_registros').fetchone()[0],0)
        self.assertFalse(panel.whatsapp_validado(1,'5519999991234'))
        self.assertFalse(self.request('/api/ia/status')[1]['envio_automatico'])
        foreign=self.receive(wamid='incoming.foreign',phone='14155551234')
        self.assertIsNone(foreign['cliente_id']);self.assertEqual(foreign['telefone_final'],'1234')

    def test_export_import_is_anonymized_pending_deduplicated_and_not_clinical(self):
        self.assertEqual(self.request('/api/ia/importar',dict(texto=TXT,atendentes=['Sonifarma']))[0],403)
        with self.admin():
            status,r=self.request('/api/ia/importar',dict(texto=TXT,atendentes=['Sonifarma']))
            self.assertEqual(status,200,r);self.assertEqual(r['novos'],2)
            self.assertEqual(self.request('/api/ia/importar',dict(texto=TXT,atendentes=['Sonifarma']))[1]['novos'],0)
            rows=self.request('/api/ia/base')[1]['exemplos']
            text=json.dumps(rows,ensure_ascii=False)
            self.assertNotIn('Ana Cliente',text);self.assertNotIn('123.456.789-00',text);self.assertNotIn('ana@example.test',text)
            self.assertNotIn('Tome 1 comprimido',text);self.assertTrue(all(not r['aprovado'] for r in rows))

    def test_only_approved_references_are_returned_and_drafts_are_manual(self):
        m=self.receive()
        with self.admin():
            self.request('/api/ia/base',dict(operacao='criar',tipo='politica',pergunta='Horário atual',resposta='Horário de teste informado pela equipe'))
            rid=self.request('/api/ia/base')[1]['exemplos'][0]['id']
        status,r=self.request('/api/ia/referencias',dict(id=m['id'],revisao=m['revisao']))
        self.assertEqual(status,200,r);self.assertEqual(r['referencias'],[]);self.assertEqual(r['mensagem']['rascunho'],'')
        with self.admin():self.request('/api/ia/base',dict(operacao='aprovar',id=rid))
        r=self.request('/api/ia/referencias',dict(id=m['id'],revisao=0))[1]
        self.assertEqual(r['referencias'][0]['id'],rid);self.assertFalse(r['enviado'])
        status,r=self.request('/api/ia/revisar',dict(id=m['id'],revisao=0,estado='revisado',rascunho='Rascunho da equipe'))
        self.assertEqual(status,200,r);self.assertFalse(r['enviado']);self.assertEqual(r['mensagem']['estado'],'revisado')
        self.assertEqual(self.request('/api/ia/revisar',dict(id=m['id'],revisao=0,estado='revisado',rascunho='Sobrescrever'))[0],409)

    def test_clinical_media_optout_and_urgency_are_flagged_for_humans(self):
        for i,(text,kind) in enumerate([('Posso tomar 2 comprimidos?','farmaceutico'),('Não quero receber mensagens','descadastro'),('Estou com dor no peito','urgencia')]):
            m=self.receive(text,wamid='incoming.'+str(i))
            self.assertEqual(m['triagem'],kind)
            status,r=self.request('/api/ia/referencias',dict(id=m['id'],revisao=0))
            self.assertEqual(status,200,r);self.assertEqual(r['mensagem']['estado'],'humano');self.assertFalse(r['enviado'])
        self.assertEqual(self.receive(tipo='image',wamid='incoming.media')['triagem'],'farmaceutico')

    def test_existing_responsible_is_respected(self):
        self.opened();m=self.receive()
        self.assertEqual(m['cliente_id'],1)
        with patch.object(self.server.RequestHandlerClass,'autorizado',return_value={'login':'other','csrf':'test'}):
            self.assertEqual(self.request('/api/ia/referencias',dict(id=m['id'],revisao=0))[0],409)

    def test_ios_export_and_unknown_operators(self):
        ia=panel.ia_modulo()
        rows=ia.importar_txt('[31/10/2026, 10:00:01] Cliente: Entregam?\n[31/10/2026, 10:00:04] Sonifarma: Aqui é da Sonifarma. Vou conferir a região.',['Sonifarma'])
        self.assertEqual(len(rows),1)
        self.assertIn('Sonifarma',rows[0]['resposta'])
        with self.assertRaises(ValueError):ia.importar_txt(TXT,['Nome errado'])
