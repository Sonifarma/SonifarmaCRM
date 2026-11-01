const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 try{
  const page=await browser.newPage(),errors=[];let consultas=0;
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',route=>{
   const url=route.request().url();if(url.includes('/api/aniversariantes'))consultas++;
   return route.fulfill({contentType:url.includes('/api/')?'application/json':'text/html',body:url.includes('/api/')?'{}':fs.readFileSync(path.join(__dirname,'../integracao/painel/index.html'),'utf8')});
  });
  await page.goto('http://crm.test/');
  await page.evaluate(()=>{
   document.getElementById('auth-overlay').hidden=true;showTab('aniversariantes-tab');
   anivRows=[{pessoaid:1,nome:'Zélia',aniversario:'1980-11-02',ultima_compra:'2026-09-01',consentimento:'autorizado'},{pessoaid:2,nome:'Ana',aniversario:'1980-11-03',ultima_compra:'2026-10-15',consentimento:'autorizado',ultimo:{status:'Enviado confirmado'}},{pessoaid:3,nome:'Bruno',aniversario:'1980-11-01',ultima_compra:'2025-01-01',consentimento:'recusado'},{pessoaid:4,nome:'Sem data',aniversario:'1980-11-04',ultima_compra:null,consentimento:'nao_informado'}];renderAniversariantes();
  });
  const ids=()=>page.locator('[data-aniv-ficha]').evaluateAll(es=>es.map(e=>e.dataset.anivFicha));
  assert.deepEqual(await ids(),['1','0','2','3']);
  assert.equal(await page.locator('#aniv-lista tr').first().locator('td').nth(2).textContent(),'15/10/2026');
  assert.equal(await page.locator('[data-aniv-sort=ultima_compra]').evaluate(e=>e.closest('th').getAttribute('aria-sort')),'descending');
  await page.locator('[data-aniv-sort=ultima_compra]').click();assert.deepEqual(await ids(),['2','0','1','3']);
  await page.locator('[data-aniv-sort=nome]').click();assert.deepEqual(await ids(),['1','2','3','0']);
  await page.locator('[data-aniv-sort=aniversario]').click();assert.deepEqual(await ids(),['2','0','1','3']);
  await page.locator('[data-aniv-sort=contato]').click();assert.deepEqual(await ids(),['0','1','2','3']);
  await page.locator('[data-aniv-sort=mensagem]').click();assert.equal(await page.locator('[data-aniv-sort=mensagem]').evaluate(e=>e.closest('th').getAttribute('aria-sort')),'ascending');
  await page.locator('[data-aniv-sort=ultima_compra]').click();
  await page.evaluate(()=>{anivRows.push({pessoaid:5,nome:'Mais recente',aniversario:'1980-11-05',ultima_compra:'2026-11-01'});renderAniversariantes();});
  assert.equal((await ids())[0],'4');assert.equal(consultas,0);assert.deepEqual(errors,[]);
  console.log('PASS: newest purchase default, dates displayed, ascending/descending, missing dates last, all header sorts, appended results and stable action indices without extra queries');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
