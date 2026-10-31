const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 try{
  const page=await browser.newPage(),errors=[];let config={favoritos:[],modelos:{}};
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());let data={};
   if(!u.pathname.startsWith('/api/'))return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'../integracao/painel/index.html'),'utf8')});
   if(u.pathname==='/api/crm-config'){
    if(route.request().method()==='POST'){const p=route.request().postDataJSON();config[p.chave]=p.valor;data={ok:true};}else data=config;
   }
   if(u.pathname==='/api/auth/status')data={autenticado:true,inicializado:true,csrf:'test',login:'Tester',perfil:'atendente'};
   if(u.pathname==='/api/ficha-cadastro')data={cadastro:{celular:'(19) 99999-1234'}};
   if(u.pathname==='/api/recompra')data={previsoes:[]};
   if(u.pathname==='/api/whatsapp-registros')data={registros:[]};
   return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  async function setup(){await page.waitForFunction(()=>typeof crmReady!=='undefined'&&crmReady&&document.getElementById('auth-overlay').hidden);}
  await page.goto('http://crm.test/');await setup();
  await page.locator('[data-tab=modelos-tab]').click();
  await page.locator('#modelo-beneficio').fill('Benefício de teste');
  await page.locator('#modelo-aniversario').fill('Olá {nome}! Cupom {cupom}: {beneficio}. {condicoes}');
  await page.locator('#modelos-salvar').click();await page.waitForFunction(()=>document.getElementById('modelos-status').textContent.includes('Salvo'));
  assert.equal(config.modelos.beneficio,'Benefício de teste');
  await page.reload();await setup();
  assert.equal(await page.locator('#aniv-beneficio').inputValue(),'Benefício de teste');
  assert.equal(await page.locator('#modelo-aniversario').inputValue(),'Olá {nome}! Cupom {cupom}: {beneficio}. {condicoes}');
  await page.evaluate(()=>{document.getElementById('central-janela').value='intervalo';document.getElementById('central-inicio').value='2026-10-01';document.getElementById('central-fim').value='2026-10-31';pessoaApplied.set(3,{nome:'Pessoa'});pbmApplied.set(7,{nome:'PBM'});v16Applied.classificacao.set(8,{nome:'Classe'});embAplicadas=[{embalagemid:99,produto:'Teste'}];});
  page.once('dialog',d=>d.accept('Consulta favorita'));
  await page.locator('#crm-central-salvar').click();await page.waitForFunction(()=>document.getElementById('crm-central-aviso').textContent.includes('salvo'));
  assert.equal(config.favoritos.length,1);
  await page.evaluate(()=>{document.getElementById('central-janela').value='misto3';pessoaApplied.clear();pbmApplied.clear();v16Applied.classificacao.clear();embAplicadas=[];});
  await page.locator('#crm-central-aplicar').click();
  const restored=await page.evaluate(()=>({f:centralFiltros(),count:embAplicadas.length}));
  assert.equal(restored.f.janela,'intervalo');assert.equal(restored.f.inicio,'2026-10-01');assert.equal(restored.f.pessoas,'3');assert.equal(restored.f.pbms,'7');assert.equal(restored.f.classificacoes,'8');assert.equal(restored.count,1);
  await page.evaluate(()=>{document.getElementById('central-resultados').innerHTML=[['1','nao_enviado'],['2','aguardando']].map(([id,status])=>`<details data-crm-status="${status}"><summary><button data-ficha-id="${id}" data-ficha-nome="Cliente ${id}">Cliente ${id}</button></summary></details>`).join('');});
  await page.selectOption('#crm-central-status','confirmado');
  assert.equal(await page.locator('#central-resultados>details:not([hidden])').count(),1);
  await page.selectOption('#crm-central-status','todos');
  const before=await page.locator('#central-resultados').innerHTML();
  await page.evaluate(()=>abrirFichaCliente(1,'Cliente 1'));
  await page.waitForFunction(()=>fichaAtual?.id===1&&!document.getElementById('ficha-proximo').disabled);
  await page.locator('#ficha-proximo').click();await page.waitForFunction(()=>fichaAtual?.id===2);
  assert(await page.locator('#ficha-proximo').isDisabled());assert.equal(await page.locator('#central-resultados').innerHTML(),before);
  await page.locator('#ficha-fechar').click();
  await page.evaluate(()=>{showTab('aniversariantes-tab');anivRows=[1,2].map(i=>({pessoaid:i,nome:'Cliente '+i,aniversario:'1980-01-15',campanha:'aniversario',ciclo:'2026-01-01',consentimento:'autorizado',status_contato:i===1?'nao_enviado':'sem_whatsapp'}));renderAniversariantes();});
  await page.selectOption('#crm-aniv-status','sem_whatsapp');assert.equal(await page.locator('[data-aniv-whats]').count(),1);
  await page.selectOption('#crm-aniv-status','todos');
  await page.locator('[data-aniv-whats="0"]').click();await page.waitForFunction(()=>!document.getElementById('reat-whats-proximo').disabled);
  assert((await page.locator('#reat-whats-texto').inputValue()).includes('Olá Cliente! Cupom NIVER10'));
  await page.locator('#reat-whats-proximo').click();await page.waitForFunction(()=>reatWhatsCliente?.pessoaid===2);
  assert(await page.locator('#reat-whats-proximo').isDisabled());
  const custom=await page.evaluate(()=>{crmModelos.recompra='Olá {nome}, confira {produto}';whatsContexto={nome:'Joana Teste',descricao:'Produto teste'};document.getElementById('whats-nome-comercial').value='Produto teste';document.getElementById('whats-tipo-aviso').value='recompra';return whatsTexto()});
  assert.equal(custom,'Olá Joana, confira Produto teste');
  assert.deepEqual(errors,[]);console.log('PASS: server-saved models/coupon, reload persistence, favorites restoring all filter IDs, loaded status filter, next customer in card/editor without clearing results');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
