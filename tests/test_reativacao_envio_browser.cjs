const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),http=require('http'),assert=require('assert/strict');
const html=fs.readFileSync(path.join(__dirname,'../integracao/painel/index.html'));
let preference='nao_informado',records=[],posts=[];
const stamp=()=>new Date().toISOString().slice(0,19);
const server=http.createServer(async(req,res)=>{
 const u=new URL(req.url,'http://local');
 if(u.pathname==='/sonifarma-logo.png'){res.setHeader('Content-Type','image/png');res.end(fs.readFileSync(path.join(__dirname,'../integracao/painel/sonifarma-logo.png')));return;}
 if(!u.pathname.startsWith('/api/')){res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html);return;}
 res.setHeader('Content-Type','application/json');let data={};
 if(req.method==='POST'){
  let body='';for await(const chunk of req)body+=chunk;const d=JSON.parse(body);posts.push({path:u.pathname,data:d});
  if(u.pathname==='/api/consentimento-comercial'){preference=d.status;data={ok:true};}
  if(u.pathname==='/api/whatsapp-registros'){records.push({...d,id:1,criado_em:stamp(),confirmado_em:null,confirmado_por:null});data={id:1,ok:true};}
  if(u.pathname==='/api/whatsapp-confirmar'){assert.equal(d.id,1);records[0].status='Enviado confirmado';records[0].confirmado_em=stamp();records[0].confirmado_por='Atendente teste';data={ok:true};}
 }else{
  if(u.pathname==='/api/auth/status')data={inicializado:false,autenticado:false};
  if(u.pathname==='/api/ficha-cadastro')data={cadastro:{celular:'(19) 99999-1234',ultima_compra:'2026-09-01'}};
  if(u.pathname==='/api/produtos-comprados')data={produtos:[{produtoid:1,produto:'MEDICAMENTO CONFIDENCIAL',ultima_compra:'2026-09-01',primeira_compra:'2020-01-01',compras:3,quantidade_original:3,quantidade_devolvida:0,quantidade_liquida:3,valor_estimado:30}],total:1,proximo_offset:null};
  if(u.pathname==='/api/reativacao-contato')data={consentimento:preference,consentimento_origem:'No balcao com cliente',contato_recente:records.some(r=>r.status==='Enviado confirmado'),ultimo:records.at(-1)||null};
  if(u.pathname==='/api/whatsapp-registros')data={registros:records.slice().reverse()};
  if(u.pathname==='/api/whatsapp-validacao')data={validado:records.some(r=>r.status==='Enviado confirmado')};
 }
 res.end(JSON.stringify(data));
});
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;
 try{
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.evaluate(()=>{
   document.getElementById('auth-overlay').hidden=true;window.confirm=()=>true;
   window.open=()=>({location:{replace:url=>{window.testWhatsappLink=url;}},close:()=>{window.testPopupClosed=true;}});
   reatRows=[{pessoaid:1,nome:'Cliente teste',ultima_compra:'2026-09-01',dias_sem_compra:40,total_compras:3,consentimento:'nao_informado'}];
   return abrirFichaCliente(1,'Cliente teste',{origem:'reativacao'});
  });
  await page.waitForFunction(()=>fichaDados.reativacao?.consentimento==='nao_informado');
  assert.equal(await page.locator('#whats-ficha-btn').textContent(),'Reativar por WhatsApp');
  await page.locator('#whats-ficha-btn').click();await page.waitForSelector('#reat-consent-modal.open');
  assert.equal(await page.locator('#whats-modal.open').count(),0,'Generic recompra modal must not open');
  await page.locator('#reat-consent-status').selectOption('autorizado');
  await page.locator('#reat-consent-origem').fill('No balcao com cliente');
  await page.locator('#reat-consent-salvar').click();
  await page.waitForFunction(()=>fichaDados.reativacao?.consentimento==='autorizado');
  await page.locator('[data-ficha-reativar]').click();await page.waitForSelector('#reat-whats-modal.open');
  await page.waitForFunction(()=>!document.getElementById('reat-whats-abrir').disabled);
  assert.equal(await page.locator('#reat-whats-numero').inputValue(),'(19) 99999-1234');
  assert(!(await page.locator('#reat-whats-texto').inputValue()).includes('MEDICAMENTO CONFIDENCIAL'));
  await page.locator('#reat-whats-abrir').click();
  await page.waitForFunction(()=>!document.getElementById('reat-whats-confirmar').disabled);
  const opened=posts.find(p=>p.path==='/api/whatsapp-registros').data;
  assert.equal(opened.tipo_aviso,'reativacao');assert.equal(opened.ean,'REATIVACAO');assert.equal(opened.cliente_id,1);
  assert.equal(opened.previsao,'2026-09-01');assert.equal(opened.telefone,'5519999991234');assert.equal(opened.status,'WhatsApp aberto');
  assert((await page.evaluate(()=>window.testWhatsappLink)).startsWith('https://web.whatsapp.com/send?phone=5519999991234'));
  assert((await page.locator('#ficha-reativacao-envio').textContent()).includes('WhatsApp aberto'));
  await page.locator('#reat-whats-confirmar').click();
  await page.waitForFunction(()=>document.getElementById('ficha-reativacao-envio').textContent.includes('Enviado confirmado'));
  const saved=await page.locator('#ficha-reativacao-envio').textContent();assert(saved.includes('Atendente teste')&&saved.includes(' às '));
  const history=await page.locator('#whats-historico').textContent();assert(history.includes('Reativação')&&history.includes('Atendente teste')&&history.includes('Enviado confirmado'));
  assert(await page.locator('#whats-ficha-btn').isDisabled());
  await page.keyboard.press('Escape');assert.equal(await page.locator('#ficha-modal.open').count(),1);assert.equal(await page.locator('#reat-whats-modal.open').count(),0);
  await page.locator('#ficha-atualizar').click();
  await page.waitForFunction(()=>document.getElementById('ficha-reativacao-envio').textContent.includes('Enviado confirmado'));
  assert((await page.locator('#ficha-reativacao-envio').textContent()).includes('Atendente teste'));
  preference='recusado';records=[];
  await page.evaluate(()=>carregarReativacaoFicha(true));
  assert((await page.locator('#ficha-reativacao-status').textContent()).includes('solicitou não receber'));
  assert(await page.locator('[data-ficha-reativar]').isDisabled());
  assert.equal(posts.filter(p=>p.path==='/api/whatsapp-registros').length,1);
  assert.deepEqual(errors,[]);
  console.log('Customer-card reactivation OK: consent required; dedicated editor; typed opening/confirmation; phone validation/date/operator shown; reload retains contact; recency/refusal blocks; Escape keeps card; no medicine names in message.');
 }finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1;});
