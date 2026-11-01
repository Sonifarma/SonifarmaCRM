const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 try{
  const page=await browser.newPage(),errors=[],posts=[];
  let base=[],messages=[{id:1,nome:'Cliente',telefone_final:'1234',tipo:'text',texto:'Qual o horário?',recebida_em:'2026-11-01T10:00',cliente_id:null,triagem:'operacional',estado:'novo',rascunho:'',revisao:0},{id:2,nome:'Cliente 2',telefone_final:'5678',tipo:'text',texto:'Posso tomar dois comprimidos?',recebida_em:'2026-11-01T10:00',cliente_id:null,triagem:'farmaceutico',estado:'novo',rascunho:'',revisao:0}];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());let data={};
   if(!u.pathname.startsWith('/api/'))return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'../integracao/painel/index.html'),'utf8')});
   if(u.pathname==='/api/auth/status')data={autenticado:true,inicializado:true,csrf:'test',login:'Supervisor',perfil:'supervisor'};
   if(u.pathname==='/api/crm-config')data={favoritos:[],modelos:{}};
   if(u.pathname==='/api/ia/status')data={modo:'preparacao',envio_automatico:false,motor_definido:false,gestor:true};
   if(u.pathname==='/api/ia/mensagens')data={mensagens:messages,proximo_offset:null};
   if(u.pathname==='/api/ia/base'){
    if(route.request().method()==='POST'){const p=route.request().postDataJSON();posts.push(u.pathname);const e=base.find(e=>e.id===p.id);if(e)e.aprovado=p.operacao==='aprovar'?1:0;data={ok:true};}
    else data={exemplos:base,proximo_offset:null};
   }
   if(u.pathname==='/api/ia/importar'){
    posts.push(u.pathname);const p=route.request().postDataJSON();assert.deepEqual(p.atendentes,['Sonifarma']);
    base=[{id:1,tipo:'estilo',pergunta:'Qual o horário?',resposta:'Vou conferir o horário com a equipe.',aprovado:0,origem:'WhatsApp exportado'}];data={novos:1,candidatos:1};
   }
   if(u.pathname==='/api/ia/referencias'){
    posts.push(u.pathname);const p=route.request().postDataJSON(),m=messages.find(m=>m.id===p.id);
    if(m.triagem!=='operacional'){m.estado='humano';m.rascunho='Vou encaminhar seu atendimento ao farmacêutico.';m.revisao++;}
    data={mensagem:m,referencias:m.triagem==='operacional'?base.filter(e=>e.aprovado):[],enviado:false};
   }
   if(u.pathname==='/api/ia/revisar'){
    posts.push(u.pathname);const p=route.request().postDataJSON(),m=messages.find(m=>m.id===p.id);m.estado=p.estado;m.rascunho=p.rascunho;m.revisao++;data={mensagem:m,enviado:false};
   }
   return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://crm.test/');await page.waitForFunction(()=>typeof crmReady!=='undefined'&&crmReady);
  await page.locator('[data-tab=ia-tab]').click();await page.locator('#ia-atualizar').click();await page.waitForSelector('[data-ia-abrir]');
  assert((await page.locator('#ia-status').textContent()).includes('motor a definir'));assert(await page.locator('#ia-base-painel').isVisible());
  await page.locator('#ia-base-painel details summary').first().click();
  await page.locator('#ia-arquivo').setInputFiles({name:'historico.txt',mimeType:'text/plain',buffer:Buffer.from('31/10/2026, 10:00 - Cliente: Olá\n31/10/2026, 10:01 - Sonifarma: Olá!')});
  await page.locator('#ia-atendentes').fill('Sonifarma');await page.locator('#ia-importar').click();await page.waitForFunction(()=>document.getElementById('ia-base-aviso').textContent.includes('pendentes'));
  assert.equal(base[0].aprovado,0);
  await page.locator('#ia-base-lista details summary').click();await page.locator('[data-ia-base-op=aprovar]').click();await page.waitForFunction(()=>document.querySelector('[data-ia-base-op=desativar]'));
  await page.locator('[data-ia-abrir="0"]').click();await page.locator('#ia-sugerir').click();await page.waitForFunction(()=>document.getElementById('ia-referencias').textContent.includes('conferir'));
  assert.equal(await page.locator('#ia-rascunho').inputValue(),'');
  await page.locator('#ia-rascunho').fill('Rascunho revisado pela equipe.');await page.locator('#ia-salvar').click();await page.waitForFunction(()=>document.getElementById('ia-revisao-aviso').textContent.includes('não enviado'));
  await page.keyboard.press('Escape');await page.locator('[data-ia-abrir="1"]').click();await page.locator('#ia-sugerir').click();await page.waitForFunction(()=>document.getElementById('ia-rascunho').value.includes('farmacêutico'));
  assert(posts.every(p=>p.startsWith('/api/ia/')));assert.deepEqual(errors,[]);
  console.log('PASS: inbox, TXT import pending, explicit example approval, references without generated text, manual draft review and human clinical queue; no sends or AI-provider calls');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
