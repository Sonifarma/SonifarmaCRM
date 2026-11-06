const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 try{
  const page=await browser.newPage(),errors=[],names=new Map(),links=new Map();let delayed=null;
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const u=new URL(route.request().url());let data={};
   if(!u.pathname.startsWith('/api/'))return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'../integracao/painel/index.html'),'utf8')});
   if(u.pathname==='/api/ficha-cadastro')data={cadastro:{celular:'(19) 99999-1234'}};
   if(u.pathname==='/api/whatsapp-registros')data={registros:[]};
   if(['/api/nome-comercial','/api/link-produto'].includes(u.pathname)){
    const isName=u.pathname.includes('nome-comercial'),map=isName?names:links,field=isName?'nome':'url';
    if(route.request().method()==='POST'){const body=route.request().postDataJSON();map.set(body.chave,body[field]);data={ok:true};}
    else {const key=u.searchParams.get('chave');if(key==='ean:SLOW'&&isName){delayed=route;return;}data={[field]:map.get(key)||''};}
   }
   return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://crm.test/');await page.evaluate(()=>{document.getElementById('auth-overlay').hidden=true;});
  async function select(product,ean='',id='42',emb=''){
   await page.evaluate(async({product,ean,id,emb})=>{
    const b=document.createElement('button');Object.assign(b.dataset,{wcid:'1',wcnome:'Cliente',wproduto:product,wean:ean,wprodutoid:id,wembalagemid:emb,wdata:'2026-11-06'});
    await whatsSelecionarProduto(b);
   },{product,ean,id,emb});
  }
  await select('PRODUTO ANTERIOR','7891234567890');
  await page.locator('#whats-nome-comercial').fill('NOME ANTERIOR');await page.locator('#whats-nome-salvar').click();
  await page.waitForFunction(()=>document.getElementById('whats-nome-status').textContent.includes('salvo'));
  await select('CAPS GASTROSHIELD 50MG 30CPS');
  assert(await page.locator('#whats-nome-comercial').isVisible());assert(await page.locator('#whats-link').isVisible());
  assert.equal(await page.locator('#whats-nome-comercial').inputValue(),'CAPS GASTROSHIELD 50MG 30CPS');
  assert(!(await page.locator('#whats-mensagem').inputValue()).includes('NOME ANTERIOR'));
  await page.locator('#whats-nome-comercial').fill('Gastroshield 50 mg — 30 cápsulas');
  await page.locator('#whats-nome-salvar').click();
  await page.locator('#whats-link').fill('https://www.sonifarma.com.br/gastroshield');await page.locator('#whats-link-salvar').click();
  await page.waitForFunction(()=>document.getElementById('whats-link-status').textContent.includes('salvo'));
  assert.equal(names.get('prod:42'),'Gastroshield 50 mg — 30 cápsulas');assert(links.has('prod:42'));
  await select('OUTRO PRODUTO','','99');await select('CAPS GASTROSHIELD 50MG 30CPS');
  await page.waitForFunction(()=>document.getElementById('whats-nome-comercial').value.startsWith('Gastroshield'));
  assert.equal(await page.locator('#whats-link').inputValue(),'https://www.sonifarma.com.br/gastroshield');
  await select('RESPOSTA LENTA','SLOW');await page.waitForFunction(()=>document.getElementById('whats-nome-comercial').value==='RESPOSTA LENTA');
  await select('PRODUTO ATUAL','','99');assert(delayed);
  await delayed.fulfill({contentType:'application/json',body:JSON.stringify({nome:'NOME ATRASADO'})});
  await page.waitForTimeout(100);assert.equal(await page.locator('#whats-nome-comercial').inputValue(),'PRODUTO ATUAL');
  await select('SEM IDENTIFICADOR','','');assert(await page.locator('#whats-nome-comercial').isVisible());assert(await page.locator('#whats-nome-salvar').isDisabled());
  await page.locator('#whats-nome-comercial').fill('Edição desta mensagem');assert((await page.locator('#whats-mensagem').inputValue()).includes('Edição desta mensagem'));
  assert.deepEqual(errors,[]);console.log('PASS: no-EAN editor, internal product key persistence, stale name reset and delayed responses, unidentified product edit');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
