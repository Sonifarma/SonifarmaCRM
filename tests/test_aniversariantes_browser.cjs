const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 try{
  const page=await browser.newPage(),errors=[];let posted=null,confirmed=false,queries=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  const ciclo=new Date().getFullYear()+'-'+String(new Date().getMonth()+1).padStart(2,'0')+'-01';
  const record=()=>({id:9,cliente_id:1,tipo_aviso:'aniversario',ean:'ANIVERSARIO',previsao:ciclo,status:confirmed?'Enviado confirmado':'WhatsApp aberto',criado_em:new Date().toISOString(),confirmado_em:confirmed?new Date().toISOString():null,confirmado_por:confirmed?'Atendente':null});
  await page.route('**/*',async route=>{
   const url=new URL(route.request().url());let data={};
   if(!url.pathname.startsWith('/api/'))return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'../integracao/painel/index.html'),'utf8')});
   if(url.pathname==='/api/aniversariantes'){
    queries.push(url.search);
    data={clientes:[{pessoaid:1,nome:'Cliente aniversariante',aniversario:'1980-01-15',ultima_compra:'2020-01-01',campanha:'aniversario',ciclo,consentimento:'autorizado',contato_recente:false,ultimo:confirmed?record():null},{pessoaid:2,nome:'Sem autorização',aniversario:'1990-01-20',campanha:'aniversario',ciclo,consentimento:'nao_informado'}],proximo_cursor:null};
   }
   if(url.pathname==='/api/ficha-cadastro')data={cadastro:{celular:'(19) 99999-1234'}};
   if(url.pathname==='/api/whatsapp-registros'){
    if(route.request().method()==='POST'){posted=route.request().postDataJSON();data={id:9};}
    else data={registros:posted?[record()]:[]};
   }
   if(url.pathname==='/api/whatsapp-confirmar'){confirmed=true;data={ok:true};}
   return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  const setup=()=>page.evaluate(()=>{document.getElementById('auth-overlay').hidden=true;window.open=()=>({close(){},location:{replace(){}}});});
  await page.goto('http://panel.test/');await setup();
  await page.locator('[data-tab=aniversariantes-tab]').click();
  assert.equal(await page.locator('#aniv-mes').inputValue(),String(new Date().getMonth()+1));
  await page.locator('#aniv-buscar').click();await page.waitForSelector('[data-aniv-whats="0"]');
  assert(queries[0].includes('mes='));assert.equal(queries[0].includes('janela'),false);
  await page.locator('[data-aniv-whats="1"]').click();assert(await page.locator('#reat-consent-modal').evaluate(e=>e.classList.contains('open')));
  await page.locator('#reat-consent-cancelar').click();
  assert.equal(await page.locator('#aniv-cupom').inputValue(),'NIVER10');
  assert((await page.locator('#aniv-condicoes').inputValue()).includes('não transferível'));
  assert((await page.locator('#aniv-condicoes').inputValue()).includes('somente para manipulados da Sonifarma'));
  await page.locator('[data-aniv-whats="0"]').click();
  assert((await page.locator('#aniv-status').textContent()).includes('código do cupom'));
  await page.locator('#aniv-cupom').fill('CUPOM_TESTE');
  await page.locator('#aniv-beneficio').fill('10% de desconto');
  await page.locator('#aniv-condicoes').fill('Condição de teste: válido até o fim do mês.');
  await page.locator('[data-aniv-whats="0"]').click();
  await page.waitForFunction(()=>!document.getElementById('reat-whats-abrir').disabled);
  const text=await page.locator('#reat-whats-texto').inputValue();assert(text.includes('aniversário'));assert(!text.includes('medicamento'));assert(text.includes('CUPOM_TESTE'));assert(text.includes('10% de desconto'));assert(text.includes('válido até o fim do mês'));assert(!text.includes('produto'));
  assert((await page.locator('#reat-whats-modal h2').textContent()).includes('aniversário'));
  await page.locator('#reat-whats-abrir').click();await page.waitForFunction(()=>!document.getElementById('reat-whats-confirmar').disabled);
  assert.equal(posted.tipo_aviso,'aniversario');assert.equal(posted.previsao,ciclo);assert.equal(posted.ean,'ANIVERSARIO');assert.equal(confirmed,false);
  await page.locator('#reat-whats-confirmar').click();
  await page.waitForFunction(()=>document.querySelector('[data-aniv-whats="0"]').disabled);
  assert((await page.locator('#aniv-lista tr').first().textContent()).includes('Enviado confirmado'));
  await page.reload();await setup();await page.locator('[data-tab=aniversariantes-tab]').click();await page.locator('#aniv-buscar').click();
  await page.waitForSelector('[data-aniv-whats="0"]');assert(await page.locator('[data-aniv-whats="0"]').isDisabled());
  assert.deepEqual(errors,[]);console.log('PASS: birthday month filter, consent, own text and type, manual open/confirm, date and persistent monthly status');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
