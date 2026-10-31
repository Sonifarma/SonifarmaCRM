const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 try{
  const page=await browser.newPage(),errors=[];let posts=[],delivered=false;
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());let data={};
   if(!u.pathname.startsWith('/api/'))return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'../integracao/painel/index.html'),'utf8')});
   if(u.pathname==='/api/whatsapp-meta/modelos')data={modelos:[{name:'sonifarma_teste',language:'pt_BR',components:[{type:'body',text:'Olá {{1}}, seu cupom é {{2}}.',count:2}]}]};
   if(u.pathname==='/api/whatsapp-meta/enviar'){posts.push(route.request().postDataJSON());data={id:10,estado:'aceito',retry_safe:false,request_id:posts.at(-1).request_id};}
   if(u.pathname==='/api/whatsapp-meta/envio')data={id:10,estado:delivered?'delivered':'aceito',retry_safe:false,request_id:posts.at(-1).request_id};
   if(u.pathname==='/api/whatsapp-registros')data={registros:[]};
   return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://panel.test/');
  await page.evaluate(()=>{document.getElementById('auth-overlay').hidden=true;reatWhatsCliente={pessoaid:1,nome:'Cliente Teste',campanha:'aniversario',ciclo:'2026-10-01'};document.getElementById('reat-whats-numero').value='(19) 99999-1234';document.getElementById('reat-whats-modal').classList.add('open');window.open=()=>{throw Error('API must not open WhatsApp Web')};});
  await page.locator('#reat-whats-api').click();await page.waitForFunction(()=>!document.getElementById('meta-envio-enviar').disabled);
  assert.equal(posts.length,0);assert((await page.locator('#meta-envio-previa').textContent()).includes('Olá Cliente'));
  await page.locator('#meta-envio-enviar').click();assert.equal(posts.length,0);
  await page.locator('[data-meta-pos="1"]').fill('NIVER10');await page.locator('#meta-envio-enviar').click();
  await page.waitForFunction(()=>document.getElementById('meta-envio-aviso').textContent.includes('Aceito pela Meta'));
  assert.equal(posts.length,1);assert.equal(posts[0].tipo_aviso,'aniversario');assert.equal(posts[0].telefone,'5519999991234');assert.deepEqual(posts[0].template_parameters.body,['Cliente','NIVER10']);
  assert(await page.locator('#meta-envio-enviar').isDisabled());assert(await page.locator('#reat-whats-confirmar').isDisabled());assert(await page.locator('#meta-envio-modelo').isDisabled());
  delivered=true;await page.locator('#meta-envio-consultar').click();await page.waitForFunction(()=>document.getElementById('meta-envio-aviso').textContent.includes('entregue'));assert.equal(posts.length,1);
  await page.keyboard.press('Escape');assert(await page.locator('#reat-whats-modal').evaluate(e=>e.classList.contains('open')));
  assert.deepEqual(errors,[]);console.log('PASS: approved-template preview, required parameters, one explicit API send, acceptance separate from delivery, no manual confirmation or WhatsApp Web, status refresh and nested Escape');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
