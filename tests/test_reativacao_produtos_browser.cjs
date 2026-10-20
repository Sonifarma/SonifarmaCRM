const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),http=require('http'),assert=require('assert/strict');
const html=fs.readFileSync(path.join(__dirname,'../integracao/painel/index.html'));
let requests=[],failPage=true;
const server=http.createServer((req,res)=>{
 const u=new URL(req.url,'http://local');
 if(u.pathname==='/sonifarma-logo.png'){res.setHeader('Content-Type','image/png');res.end(fs.readFileSync(path.join(__dirname,'../integracao/painel/sonifarma-logo.png')));return;}
 if(!u.pathname.startsWith('/api/')){res.setHeader('Content-Type','text/html; charset=utf-8');res.end(html);return;}
 requests.push(req.url);res.setHeader('Content-Type','application/json');let data={},delay=0;
 if(u.pathname==='/api/auth/status')data={inicializado:false,autenticado:false};
 if(u.pathname==='/api/ficha-cadastro')data={cadastro:{celular:'(19) 99999-1234'}};
 if(u.pathname==='/api/whatsapp-registros')data={registros:[]};
 if(u.pathname==='/api/recompra')data={previsoes:[]};
 if(u.pathname==='/api/produtos-comprados'){
  const offset=Number(u.searchParams.get('offset')||0),id=u.searchParams.get('id');delay=id==='1'?80:20;
  if(offset===50&&failPage){failPage=false;res.statusCode=503;data={erro:'Falha simulada'};}
  else data={produtos:Array.from({length:Math.min(50,123-offset)},(_,i)=>({produtoid:offset+i+1,produto:`Cliente ${id} · Produto ${offset+i+1}`,ultima_compra:'2020-01-01',primeira_compra:'2018-01-01',compras:3,quantidade_original:6,quantidade_devolvida:1,quantidade_liquida:5,valor_estimado:50})),total:123,proximo_offset:offset+50<123?offset+50:null};
 }
 setTimeout(()=>res.end(JSON.stringify(data)),delay);
});
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;
 try{
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.evaluate(()=>{
   document.getElementById('auth-overlay').hidden=true;
   document.getElementById('central-janela').value='30';pbmApplied.set(7,{});v16Applied.classificacao.set(8,{});
   showTab('reativacao-tab');reatRows=[{pessoaid:1,nome:'Consumidor inativo',ultima_compra:'2020-01-01',dias_sem_compra:500,total_compras:3,consentimento:'nao_informado'}];renderReativacao();
  });
  requests=[];await page.locator('[data-reat-ficha]').click();
  await page.waitForFunction(()=>fichaDados.produtos?.length===50);
  assert.equal(await page.locator('[data-ficha-tab="produtos"]').getAttribute('class'),'selected');
  assert((await page.locator('#ficha-aviso').textContent()).includes('todos os períodos'));
  assert.equal(requests.filter(r=>r.startsWith('/api/recompra')||r.startsWith('/api/historico')).length,0);
  assert(requests.filter(r=>r.startsWith('/api/produtos-comprados')).every(r=>!r.includes('pbm')&&!r.includes('classificacao')&&!r.includes('janela')));
  assert.equal(await page.locator('#ficha-conteudo tbody tr').count(),50);
  await page.locator('[data-ficha-produtos-mais]').click();
  await page.waitForFunction(()=>fichaDados.produtosErro===true);
  assert.equal(await page.locator('#ficha-conteudo tbody tr').count(),50);
  await page.locator('[data-ficha-produtos-mais]').click();await page.waitForFunction(()=>fichaDados.produtos?.length===100);
  await page.locator('[data-ficha-produtos-mais]').click();await page.waitForFunction(()=>fichaDados.produtos?.length===123);
  assert((await page.locator('#ficha-conteudo').textContent()).includes('Todos os produtos encontrados foram carregados'));
  assert.equal(await page.locator('[data-ficha-produtos-mais]').count(),0);
  const count=requests.filter(r=>r.startsWith('/api/produtos-comprados')).length;
  await page.locator('[data-ficha-sort-col="produto"]').click();assert.equal(requests.filter(r=>r.startsWith('/api/produtos-comprados')).length,count);
  await page.locator('#ficha-atualizar').click();await page.waitForFunction(()=>fichaDados.produtos?.length===50);
  assert.equal(await page.evaluate(()=>fichaAba),'produtos');
  await page.locator('#ficha-limpar-filtros').click();await page.waitForFunction(()=>fichaDados.produtos?.length===50);
  assert.equal(await page.evaluate(()=>fichaAba),'produtos');
  await page.evaluate(()=>{abrirFichaCliente(1,'Antigo',{origem:'reativacao'});abrirFichaCliente(2,'Novo',{origem:'reativacao'});});
  await page.waitForFunction(()=>fichaAtual?.id===2&&fichaDados.produtos?.length===50);
  assert((await page.locator('#ficha-conteudo tbody tr').first().textContent()).includes('Cliente 2'));
  await page.evaluate(()=>abrirFichaCliente(3,'Central'));
  assert.equal(await page.evaluate(()=>fichaAba),'previsoes');
  assert(!requests.some(r=>r.startsWith('/api/produtos-comprados')&&r.includes('id=3')));
  assert.deepEqual(errors,[]);
  console.log('Reactivation UI OK: opens all-period products without inherited filters; 123 products across pages; failed page preserves loaded rows; sort/retry/refresh/clear/stale-client guards; Central still opens forecasts.');
 }finally{if(browser)await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1;});
