const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 try{
  const page=await browser.newPage(),errors=[];
  let row={cliente_id:1,cliente_nome:'Cliente teste',responsavel:'',acao:'',retorno_em:'',observacao:'',concluido:0,revisao:0,atualizado_em:'',atualizado_por:''};
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());let data={},status=200;
   if(!u.pathname.startsWith('/api/'))return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'../integracao/painel/index.html'),'utf8')});
   if(u.pathname==='/api/auth/status')data={autenticado:true,inicializado:true,csrf:'test',login:'Tester',perfil:'atendente'};
   if(u.pathname==='/api/crm-config')data={favoritos:[],modelos:{}};
   if(u.pathname==='/api/ficha-cadastro')data={cadastro:{celular:'(19) 99999-1234'}};
   if(u.pathname==='/api/recompra')data={previsoes:[]};
   if(u.pathname==='/api/whatsapp-registros')data={registros:[]};
   if(u.pathname==='/api/crm-atendimento'){
    if(route.request().method()==='POST'){
     const p=route.request().postDataJSON();
     if(p.revisao!==row.revisao){status=409;data={erro:'O atendimento foi alterado por outra pessoa. Atualize antes de salvar.'};}
     else{
      if(p.operacao==='assumir')row.responsavel='Tester';
      if(p.operacao==='liberar')row.responsavel='';
      if(p.operacao==='salvar')Object.assign(row,{responsavel:p.responsavel,acao:p.acao,retorno_em:p.retorno_em,observacao:p.observacao,concluido:0});
      if(p.operacao==='concluir')row.concluido=1;
      row.revisao++;row.atualizado_por='Tester';row.atualizado_em='2026-10-31T10:00:00';data={atendimento:row};
     }
    }else data={atendimento:row,usuario:'Tester',gestor:false,usuarios:['Tester','Other']};
   }
   if(u.pathname==='/api/crm-retornos'){
    const done=u.searchParams.get('estado')==='concluidos';
    data={atendimentos:row.acao&&!!row.concluido===done?[row]:[],proximo_offset:null,agora:'2026-10-31T10:00'};
   }
   if(u.pathname==='/api/crm-retornos-resumo')data={atrasados:row.acao&&!row.concluido?1:0,hoje:0};
   return route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://crm.test/');await page.waitForFunction(()=>typeof crmReady!=='undefined'&&crmReady);
  await page.evaluate(()=>abrirFichaCliente(1,'Cliente teste'));
  await page.locator('#ficha-atendimento').click();await page.waitForFunction(()=>!document.getElementById('crm-atendimento-assumir').disabled);
  await page.locator('#crm-atendimento-assumir').click();await page.waitForFunction(()=>document.getElementById('crm-atendimento-aviso').textContent.includes('Tester'));
  await page.locator('#crm-atendimento-acao').fill('Retornar para confirmar interesse');
  await page.locator('#crm-atendimento-data').fill('2026-10-30T09:00');
  await page.locator('#crm-atendimento-obs').fill('Observação operacional');
  await page.locator('#crm-atendimento-salvar').click();await page.waitForFunction(()=>document.getElementById('crm-atendimento-aviso').textContent.includes('salvo'));
  assert.equal(row.responsavel,'Tester');assert.equal(row.observacao,'Observação operacional');
  await page.waitForFunction(()=>document.querySelector('[data-tab="retornos-tab"]').textContent.includes('atrasado'));
  await page.keyboard.press('Escape');
  assert(await page.locator('#ficha-modal').evaluate(e=>e.classList.contains('open')));
  await page.locator('#ficha-fechar').click();await page.locator('[data-tab="retornos-tab"]').click();await page.locator('#ret-buscar').click();
  await page.waitForSelector('[data-ret-editar]');assert((await page.locator('#ret-lista').textContent()).includes('Atrasado'));
  await page.locator('[data-ret-editar="0"]').click();await page.waitForFunction(()=>!document.getElementById('crm-atendimento-concluir').disabled);
  await page.locator('#crm-atendimento-concluir').click();await page.waitForFunction(()=>document.getElementById('crm-atendimento-aviso').textContent.includes('concluída'));
  assert.equal(row.concluido,1);await page.locator('#crm-atendimento-fechar').click();
  await page.selectOption('#ret-estado','concluidos');await page.locator('#ret-buscar').click();await page.waitForSelector('[data-ret-editar]');
  assert((await page.locator('#ret-lista').textContent()).includes('Concluído'));
  await page.locator('[data-ret-editar="0"]').click();await page.waitForFunction(()=>!document.getElementById('crm-atendimento-salvar').disabled);
  await page.locator('#crm-atendimento-obs').fill('Rascunho não salvo');row.revisao++;row.observacao='Alteração de outro atendente';
  await page.locator('#crm-atendimento-salvar').click();await page.waitForFunction(()=>document.getElementById('crm-atendimento-aviso').textContent.includes('outra pessoa'));
  assert.equal(await page.locator('#crm-atendimento-obs').inputValue(),'Rascunho não salvo');assert.equal(row.observacao,'Alteração de outro atendente');
  row.responsavel='Other';await page.locator('#crm-atendimento-recarregar').click();
  await page.waitForFunction(()=>document.getElementById('crm-atendimento-aviso').textContent.includes('Other'));
  assert(await page.locator('#crm-atendimento-salvar').isDisabled());assert(await page.locator('#crm-atendimento-assumir').isDisabled());
  await page.setViewportSize({width:390,height:844});const box=await page.locator('.crm-atendimento-dialog').boundingBox();assert(box.width<=390);
  assert.deepEqual(errors,[]);console.log('PASS: assignment, saved return/note, reminder, nested Escape, return filters/completion, conflict keeps draft, other owner protected, mobile layout');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
