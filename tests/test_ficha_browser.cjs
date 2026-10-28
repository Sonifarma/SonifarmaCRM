// Browser regression test with a local mock ERP; requires Playwright and Chromium.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('fs'), http=require('http'), assert=require('assert/strict');
const html=fs.readFileSync(require('path').join(__dirname,'../integracao/painel/index.html'));
let requests=[],failHistory=false;
const products=Array.from({length:25},(_,i)=>({produto:'PRODUTO TESTE '+i,ean:String(12345670+i),produtoid:i+1,embalagemid:i+1,proxima_compra:'2026-10-19',ultima_compra:'2026-09-19',quantidade_ultima_compra:4,duracao_dias:30,detalhes_embalagem:[{embalagemid:i+1,unidades_caixa:60,situacao:'Confirmada manualmente'}]}));
const server=http.createServer((req,res)=>{
 const u=new URL(req.url,'http://local');
 if(!u.pathname.startsWith('/api/')){res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html);return;}
 requests.push(req.url);
 let data={},delay=0;
 if(u.pathname==='/api/auth/status')data={inicializado:false,autenticado:false};
 if(u.pathname==='/api/ficha-cadastro'){data={cadastro:{celular:'(19) 99999-1234',telefone:'(19) 3333-1234',datanascimento:'1960-01-01',email:'teste@example.test',compras_total:36,ultima_compra:'2026-09-19',valor_medio:45,valor_total:1600,primeira_compra:'2020-06-01'}};delay=30;}
 if(u.pathname==='/api/recompra'){data={previsoes:products};delay=u.searchParams.get('id')==='1'?90:40;}
 if(u.pathname==='/api/historico'){data={itens:[{vendaid:1,produto:'PRODUTO HISTORICO',data_compra:'2026-09-19'}]};delay=300;if(failHistory){data={erro:'Falha simulada'};res.statusCode=503;}}
 if(u.pathname==='/api/whatsapp-registros')data={registros:[]};
 setTimeout(()=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));},delay);
});
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;
 try{
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE ? {executablePath:process.env.CHROMIUM_EXECUTABLE} : {})});
  const page=await browser.newPage({viewport:{width:1280,height:720}});const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.waitForFunction(()=>typeof abrirFichaCliente==='function');
  await page.evaluate(()=>{document.getElementById('auth-overlay').hidden=true;});
  // Clearing a card must preserve the Central query, loaded rows and pagination.
  const centralState=()=>page.evaluate(()=>JSON.stringify({filters:centralFiltros(),html:document.getElementById('central-resultados').innerHTML,cursor:centralCursor,next:centralNext,job:centralJob,signature:centralSignature}));
  await page.evaluate(()=>{
   document.getElementById('central-janela').value='intervalo';
   document.getElementById('central-inicio').value='2026-10-01';
   document.getElementById('central-fim').value='2026-10-31';
   pbmApplied.set(7,{id:7});v16Applied.classificacao.set(8,{id:8});
   document.getElementById('central-resultados').innerHTML='<p>Lista de clientes já consultada</p>';
   centralCursor=50;centralNext=100;centralJob='consulta-em-andamento';centralSignature='filtros-originais';
  });
  const before=await centralState();
  await page.evaluate(()=>abrirFichaCliente(1,'CLIENTE DE TESTE'));
  await page.waitForSelector('#ficha-conteudo tbody tr');
  assert(requests.some(x=>x.includes('/api/recompra?')&&x.includes('inicio=2026-10-01')));
  requests=[];
  await page.locator('#ficha-limpar-filtros').click();
  await page.waitForSelector('#ficha-conteudo tbody tr');
  assert.equal(await centralState(),before,'Card clear changed the Central query');
  const cleared=requests.filter(x=>x.startsWith('/api/recompra?')).at(-1);
  assert(cleared.includes('janela=misto3')&&!cleared.includes('pbms=')&&!cleared.includes('classificacoes='));
  await page.locator('#ficha-atualizar').click();
  await page.waitForSelector('#ficha-conteudo tbody tr');
  assert.equal(await centralState(),before);
  assert(requests.filter(x=>x.startsWith('/api/recompra?')).at(-1).includes('janela=misto3'));
  await page.locator('#ficha-fechar').click();
  await page.evaluate(()=>abrirFichaCliente(2,'OUTRO CLIENTE'));
  await page.waitForSelector('#ficha-conteudo tbody tr');
  assert(requests.filter(x=>x.startsWith('/api/recompra?')).at(-1).includes('janela=intervalo'));
  await page.locator('#ficha-fechar').click();
  await page.evaluate(()=>document.getElementById('central-limpar').click());
  requests=[];
  await page.evaluate(()=>abrirFichaCliente(1,'CLIENTE DE TESTE'));
  await page.waitForSelector('#ficha-conteudo tbody tr');
  assert.equal(requests.filter(x=>x.startsWith('/api/historico')).length,0,'History must be deferred');
  const summary=()=>requests.filter(x=>x.includes('resumo=1')).length;
  assert.equal(summary(),1);
  for(let i=0;i<8;i++)await page.locator('#ficha-conteudo [data-ficha-sort-col="produto"]').click();
  assert.equal(summary(),1,'Sorting must not refetch send summary');
  assert.equal(await page.locator('#ficha-cadastro details').getAttribute('open'),null);
  const space=await page.locator('#ficha-conteudo').evaluate(el=>el.getBoundingClientRect().height);
  assert(space>=280,'Product area too small: '+space);
  await page.locator('#ficha-cadastro summary').click();
  assert(await page.locator('#ficha-cadastro details').getAttribute('open')!==null);
  assert(await page.locator('#ficha-cadastro').textContent().then(x=>x.includes('teste@example.test')));
  await page.locator('#ficha-cadastro summary').click();
  await page.locator('[data-ficha-tab="historico"]').click();
  await page.waitForFunction(()=>document.getElementById('ficha-conteudo').textContent.includes('PRODUTO HISTORICO'));
  assert.equal(requests.filter(x=>x.startsWith('/api/historico')).length,1);
  await page.locator('[data-ficha-tab="resumo"]').click();
  await page.locator('[data-ficha-tab="historico"]').click();
  assert.equal(requests.filter(x=>x.startsWith('/api/historico')).length,1,'History reused inside same open card');
  await page.locator('[data-ficha-tab="previsoes"]').click();
  assert.equal(summary(),1);
  await page.evaluate(()=>carregarUltimosEnvios(1,true));assert.equal(summary(),2,'Explicit force refresh must fetch');
  await page.evaluate(()=>{abrirFichaCliente(1,'ANTIGO');abrirFichaCliente(2,'NOVO');});
  await page.waitForFunction(()=>fichaAtual?.id===2&&fichaDados.previsoes?.length===25);
  await page.waitForTimeout(150);
  assert.equal(await page.locator('#ficha-titulo').textContent(),'NOVO');
  assert.equal(await page.locator('#ficha-conteudo .whats-produto').first().getAttribute('data-wcid'),'2');
  failHistory=true;
  await page.locator('[data-ficha-tab="historico"]').click();await page.waitForSelector('[data-ficha-retry]');
  failHistory=false;await page.locator('[data-ficha-retry]').click();
  await page.waitForFunction(()=>document.getElementById('ficha-conteudo').textContent.includes('PRODUTO HISTORICO'));
  await page.locator('[data-ficha-tab="previsoes"]').click();
  await page.setViewportSize({width:390,height:700});
  assert(await page.locator('#ficha-fechar').isVisible());
  assert(await page.locator('#ficha-cadastro summary').isVisible());
  const overflow=await page.locator('#ficha-modal>.client-dialog').evaluate(el=>el.scrollWidth>el.clientWidth+2);
  assert(!overflow,'Mobile card must not overflow horizontally');
  await page.evaluate(()=>{abrirFichaCliente(3,'FECHAR');fecharFicha();});
  await page.waitForTimeout(150);assert.equal(await page.evaluate(()=>fichaAtual),null);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({checks:'lazy history; shared pending requests; eight sorts without new send query; forced refresh; stale response protection; retry; close cancellation; responsive layout',desktopProductArea:space,errors}));
 }finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1;});
