// Regression: a confirmed Farmacia Popular reminder applies to customer/cycle, not EAN.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),http=require('http'),assert=require('assert/strict');
const html=fs.readFileSync(require('path').join(__dirname,'../integracao/painel/index.html'));
let confirmed=false,historyFailed=false;
const server=http.createServer((req,res)=>{
 const url=new URL(req.url,'http://local');
 if(!url.pathname.startsWith('/api/')){res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html);return;}
 res.setHeader('Content-Type','application/json');
 let data={};
 if(url.pathname==='/api/auth/status')data={inicializado:false,autenticado:false};
 if(url.pathname==='/api/whatsapp-confirmar'){confirmed=true;historyFailed=true;data={ok:true};}
 if(url.pathname==='/api/whatsapp-validacao')data={validado:confirmed};
 if(url.pathname==='/api/whatsapp-registros'){
  if(!url.searchParams.has('cliente_id')&&historyFailed){res.statusCode=503;res.end(JSON.stringify({erro:'Falha simulada na listagem geral'}));return;}
  data={registros:[
   {id:8,cliente_id:1,ean:'FARMACIA_POPULAR',produto_descricao:'Farmácia Popular — aviso de retirada',tipo_aviso:'popular_amanha',previsao:'2026-10-21',status:confirmed?'Enviado confirmado':'WhatsApp aberto',criado_em:'2026-10-20T08:00:00',confirmado_em:confirmed?'2026-10-20T09:15:00':null,confirmado_por:'Atendente',telefone_final:'1234'},
   {id:9,cliente_id:2,ean:'FARMACIA_POPULAR',tipo_aviso:'popular_hoje',previsao:'2026-10-20',status:'Enviado confirmado',confirmado_em:'2026-10-20T08:00:00'},
   {id:10,cliente_id:1,ean:'REATIVACAO',tipo_aviso:'reativacao',previsao:'2026-10-22',status:'Enviado confirmado',confirmado_em:'2026-10-20T08:30:00'},
   {id:11,cliente_id:1,ean:'E',tipo_aviso:'recompra',previsao:'2026-10-21',status:'Enviado confirmado',confirmado_em:'2026-10-20T08:45:00'}
  ]};
 }
 res.end(JSON.stringify(data));
});
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;
 try{
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.waitForFunction(()=>typeof renderFicha==='function');
  await page.evaluate(()=>{
   document.getElementById('auth-overlay').hidden=true;
   fichaAtual={id:1,nome:'Cliente teste'};fichaAba='previsoes';fichaOrdem.previsoes={col:'produto',dir:'asc'};
   fichaDados={historico:[],erros:[],previsoes:['A','B','C','D','E'].map((ean,i)=>({produto:'Produto '+ean,ean,produtoid:i+1,proxima_compra:i===2?'2026-10-22':i===3?'2026-10-20':'2026-10-21',ultima_compra:'2026-09-21',quantidade_ultima_compra:1,duracao_dias:30}))};
   renderFicha();
  });
  await page.waitForFunction(()=>document.querySelector('.ultimo-envio')?.textContent.includes('aguardando confirmação'));
  let cells=await page.locator('.ultimo-envio').allTextContents();
  assert(cells[0].includes('Farmácia Popular'));assert(cells[1].includes('aguardando confirmação'));
  assert.equal(cells[2],'Não enviado');assert.equal(cells[3],'Não enviado');
  assert(cells[4].includes('20/10/2026 às 08:45')); // Direct product record still works.
  await page.evaluate(()=>{
   whatsContexto={clienteId:1,ean:'A',proximaCompra:'2026-10-21'};
   whatsRegistroAbertoId=8;window.confirm=()=>true;
   document.getElementById('whats-confirmar').disabled=false;
   document.getElementById('whats-confirmar').click();
  });
  await page.waitForFunction(()=>document.querySelector('.ultimo-envio')?.textContent.includes('20/10/2026 às 09:15'));
  cells=await page.locator('.ultimo-envio').allTextContents();
  assert(cells[0].includes('✓ Confirmado')&&cells[0].includes('Farmácia Popular · aviso para amanhã'));
  assert(cells[1].includes('20/10/2026 às 09:15'));
  assert.equal(cells[2],'Não enviado');assert.equal(cells[3],'Não enviado');
  assert(cells[4].includes('20/10/2026 às 08:45'));
  assert((await page.locator('.ultimo-envio').first().getAttribute('title')).includes('sem envio individual por medicamento'));
  assert.equal(await page.locator('#ficha-conteudo .whats-mini').first().textContent(),'✓');
  await page.evaluate(()=>carregarUltimosEnvios(1,true));
  assert((await page.locator('.ultimo-envio').first().textContent()).includes('20/10/2026 às 09:15'));
  await page.reload();
  await page.evaluate(()=>{fichaAtual={id:1};fichaEnvios=null;fichaEnviosPendente=null;});
  const result=await page.evaluate(async()=>{await carregarUltimosEnvios(1,true);return registroUltimoEnvio(1,'A','Produto A','2026-10-21');});
  assert.equal(result.status,'Enviado confirmado');assert.equal(result.confirmado_em,'2026-10-20T09:15:00');
  assert.deepEqual(errors,[]);
  console.log('Farmacia Popular OK: automatic confirmation/date refresh despite global-history failure; customer/date isolation; direct-product sends preserved; reload retains confirmed state.');
 }finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1;});
