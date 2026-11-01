const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 try{
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  const row=(id,status)=>({pessoaid:id,nome:'Cliente '+id,aniversario:'1980-11-01',ultima_compra:'2026-10-01',consentimento:'autorizado',ultimo:status?{status}:null});
  await page.route('**/*',route=>{
   const u=new URL(route.request().url());let data={};
   if(!u.pathname.startsWith('/api/'))return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'../integracao/painel/index.html'),'utf8')});
   if(u.pathname==='/api/aniversariantes')data=u.searchParams.get('cursor')==='0'?{clientes:[row(1,'Enviado confirmado'),row(2,null),row(3,'Aceito pela API')],total:152,proximo_cursor:50}:{clientes:[row(4,null)],total:152,proximo_cursor:null};
   return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://crm.test/');await page.evaluate(()=>{document.getElementById('auth-overlay').hidden=true;showTab('aniversariantes-tab')});
  assert(await page.locator('#aniv-ocultar-enviados').isChecked());
  await page.locator('#aniv-buscar').click();await page.waitForSelector('[data-aniv-ficha]');
  assert.equal(await page.locator('[data-aniv-ficha]').count(),2);
  assert((await page.locator('#aniv-contador').textContent()).includes('152'));
  assert((await page.locator('#aniv-contador').textContent()).includes('Carregados: 3'));
  await page.locator('#aniv-ocultar-enviados').uncheck();assert.equal(await page.locator('[data-aniv-ficha]').count(),3);
  await page.locator('#aniv-ocultar-enviados').check();await page.locator('#aniv-mais').click();await page.waitForFunction(()=>anivRows.length===4);
  assert.equal(await page.locator('[data-aniv-ficha]').count(),3);
  await page.evaluate(()=>{anivRows[1].ultimo={status:'Enviado confirmado'};renderAniversariantes()});
  assert.equal(await page.locator('[data-aniv-ficha]').count(),2);
  assert((await page.locator('#aniv-contador').textContent()).includes('Total de aniversariantes nos filtros: 152'));
  assert((await page.locator('#aniv-contador').textContent()).includes('Envios confirmados carregados: 2'));
  assert.equal(await page.evaluate(()=>dataHoraEnvio('2026-10-31T08:04:16')),'31/10/2026 às 08:04');
  assert.equal(await page.evaluate(()=>dataPrevisaoBR('2026-11-01')),'01/11/2026');
  assert.equal(await page.evaluate(()=>dataPrevisaoBR('2026-11')),'11/2026');
  assert.equal(await page.locator('.aniv-cupom-painel').getAttribute('open'),null);
  await page.locator('.aniv-cupom-painel summary').click();
  assert(await page.locator('#aniv-salvar-cupom').isVisible());
  await page.locator('.aniv-cupom-painel summary').click();
  await page.locator('.aniv-favoritos summary').click();
  assert(await page.locator('#crm-aniv-aplicar').isVisible());
  await page.locator('.aniv-favoritos summary').click();
  await page.screenshot({path:'/tmp/sonifarma-aniversariantes-desktop.png'});
  await page.setViewportSize({width:390,height:844});
  const checkbox=await page.locator('#aniv-ocultar-enviados').boundingBox();assert(checkbox.width<=20);
  const filter=await page.locator('.aniv-filtros').boundingBox();assert(filter.x+filter.width<=390);
  await page.screenshot({path:'/tmp/sonifarma-aniversariantes-mobile.png'});
  assert.deepEqual(errors,[]);console.log('PASS: confirmed sends hidden by default, toggle restores rows, API acceptance remains visible, real total separate from loaded rows, pagination and confirmation refresh');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
