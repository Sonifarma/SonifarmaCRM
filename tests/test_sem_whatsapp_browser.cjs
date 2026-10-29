const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 try{
  const page=await browser.newPage(),errors=[];let posted;
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const url=route.request().url();let data={};
   if(!url.includes('/api/'))return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'../integracao/painel/index.html'),'utf8')});
   if(url.includes('/api/whatsapp-sem-conta')){posted=route.request().postDataJSON();data={ok:true};}
   if(url.includes('/api/whatsapp-validacao'))data={validado:false,sem_whatsapp:true};
   return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://panel.test/');
  await page.evaluate(()=>{
   document.getElementById('auth-overlay').hidden=true;
   fichaAtual={id:1,nome:'Teste'};whatsContexto={clienteId:1,descricao:'Teste'};whatsRegistroAbertoId=42;
   document.getElementById('whats-numero').value='(19) 99999-1234';
   document.getElementById('whats-modal').classList.add('open');document.getElementById('whats-confirmar').disabled=false;
  });
  for(const width of [1280,390]){
   await page.setViewportSize({width,height:844});
   const primary=await page.locator('#whats-modal .whats-primary').boundingBox();
   const secondary=await page.locator('#whats-modal .whats-secondary').boundingBox();
   assert(secondary.y<primary.y,'Secondary actions should be above primary actions');
   for(const id of ['whats-abrir','whats-confirmar','whats-sem-conta']){
    const box=await page.locator('#'+id).boundingBox();
    assert(box.x>=0&&box.x+box.width<=width,'Action extends beyond viewport');
   }
  }
  await page.locator('#whats-sem-conta').click();
  await page.waitForFunction(()=>document.getElementById('whats-aviso').textContent.includes('Nenhum envio'));
  assert.equal(posted.registro_id,42);assert.equal(posted.telefone,'5519999991234');
  assert(await page.locator('#whats-confirmar').isDisabled());
  await page.waitForFunction(()=>document.getElementById('whats-validacao-numero').textContent.includes('sem WhatsApp'));
  await page.evaluate(()=>{
   document.getElementById('whats-modal').classList.remove('open');reatWhatsCliente={pessoaid:2};reatWhatsRegistro=43;
   document.getElementById('reat-whats-numero').value='(19) 99999-5678';
   document.getElementById('reat-whats-modal').classList.add('open');document.getElementById('reat-whats-confirmar').disabled=false;
  });
  await page.locator('#reat-whats-sem-conta').click();
  await page.waitForFunction(()=>document.getElementById('reat-whats-aviso').textContent.includes('Nenhum envio'));
  assert.equal(posted.cliente_id,2);assert.equal(posted.registro_id,43);
  assert(await page.locator('#reat-whats-confirmar').isDisabled());
  assert.deepEqual(errors,[]);
  console.log('PASS: missing WhatsApp buttons, phone normalization, pending attempt, disabled confirmation, warning and reactivation');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
