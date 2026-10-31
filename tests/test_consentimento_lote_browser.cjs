const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 try{
  const page=await browser.newPage(),errors=[],posts=[];let perfil='master';
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());let data={};
   if(!u.pathname.startsWith('/api/'))return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'../integracao/painel/index.html'),'utf8')});
   if(route.request().method()==='POST')posts.push({path:u.pathname,data:route.request().postDataJSON()});
   if(u.pathname==='/api/auth/status')data={autenticado:true,inicializado:true,csrf:'test',login:'Operador',perfil};
   if(u.pathname==='/api/crm-config')data={favoritos:[],modelos:{}};
   if(u.pathname==='/api/consentimento-comercial/lote')data={atualizados:1,ja_autorizados:1,recusas_preservadas:1,total_clientes:3,backup:'D:\\BackupsSonifarma\\teste.zip',preferencias:{1:{status:'autorizado',origem:'Balcão'},2:{status:'recusado',origem:'Pedido do cliente'},3:{status:'autorizado',origem:'Autorização anterior'}}};
   return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://crm.test/');await page.waitForFunction(()=>typeof crmReady!=='undefined'&&crmReady);
  await page.locator('[data-tab=modelos-tab]').click();assert(await page.locator('#consent-lote-painel').isVisible());
  assert.equal(await page.locator('#consent-lote-origem').inputValue(),'Balcão');assert.equal(await page.locator('#consent-lote-data').inputValue(),'2026-10-31');
  await page.evaluate(()=>{anivRows=[1,2,3].map(id=>({pessoaid:id,nome:'Cliente '+id,aniversario:'1990-10-15',consentimento:id===2?'recusado':id===3?'autorizado':'nao_informado'}));});
  await page.locator('#consent-lote-aplicar').click();await page.waitForFunction(()=>document.getElementById('consent-lote-aviso').textContent.includes('recusas preservadas'));
  assert.deepEqual(posts.map(p=>p.path),['/api/consentimento-comercial/lote']);assert.equal(posts[0].data.origem,'Balcão');assert.equal(posts[0].data.data_consentimento,'2026-10-31');
  assert.deepEqual(await page.evaluate(()=>anivRows.map(c=>c.consentimento)),['autorizado','recusado','autorizado']);
  perfil='atendente';await page.reload();await page.waitForFunction(()=>typeof crmReady!=='undefined'&&crmReady);
  assert(await page.locator('#consent-lote-painel').evaluate(e=>e.hidden));assert.deepEqual(errors,[]);
  console.log('PASS: master bulk form, confirmed source/date, refreshed statuses with refusal preserved, no message sends, hidden for attendants');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
