// Runs the application's exact aggregate SQL on PostgreSQL via PGlite, using fixtures only.
const {PGlite}=require(process.env.PGLITE_MODULE||'@electric-sql/pglite');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
(async()=>{
 const source=fs.readFileSync(path.join(__dirname,'../integracao/painel/iniciar_interface.py'),'utf8');
 let parameter=0;const sql=source.match(/SQL_PRODUTOS_COMPRADOS = """([\s\S]*?)"""/)[1].replace(/%s/g,()=>'$'+(++parameter));
 const db=new PGlite();
 try{
  await db.exec(`CREATE TABLE venda(id bigint PRIMARY KEY,pessoaid bigint,status text,datahorafechamento timestamp);
   CREATE TABLE itemvenda(id bigint PRIMARY KEY,vendaid bigint,embalagemid bigint,quantidade numeric,valortotal numeric,status text);
   CREATE TABLE embalagem(id bigint PRIMARY KEY,produtoid bigint);
   CREATE TABLE produto(id bigint PRIMARY KEY,descricao text);
   CREATE TABLE itemdevolucaovenda(itemvendaid bigint,quantidade numeric);
   CREATE TABLE estornovenda(vendaid bigint);
   INSERT INTO produto SELECT n,'Produto '||n FROM generate_series(1,140) n;
   INSERT INTO embalagem SELECT id,id FROM produto;
   INSERT INTO venda SELECT n,1,'F',timestamp '2020-01-01'+n*interval '1 day' FROM generate_series(1,140) n;
   INSERT INTO itemvenda SELECT n,n,n,2,20,'F' FROM generate_series(1,140) n;
   INSERT INTO embalagem VALUES(141,1);
   INSERT INTO venda VALUES(150,1,'F','2026-10-20');
   INSERT INTO itemvenda VALUES(150,150,1,3,30,'F'),(151,150,141,4,40,'F');
   INSERT INTO itemdevolucaovenda VALUES(3,1),(4,2);
   UPDATE itemvenda SET status='D' WHERE id IN (3,4);
   UPDATE produto SET descricao='Produto 1' WHERE id=2;
   INSERT INTO produto VALUES(200,'Cancelado'),(201,'Outro cliente'),(202,'Estornado'),(203,'Sem data'),(204,'Zero'),(205,'Devolvido sem registro');
   INSERT INTO embalagem SELECT id,id FROM produto WHERE id>=200;
   INSERT INTO venda VALUES(200,1,'C','2026-10-20'),(201,2,'F','2026-10-20'),(202,1,'F','2026-10-20'),(203,1,'F',NULL),(204,1,'F','2026-10-20'),(205,1,'D','2026-10-20');
   INSERT INTO itemvenda SELECT id,id,id,2,20,'F' FROM produto WHERE id>=200;
   UPDATE itemvenda SET quantidade=0 WHERE id=204;
   UPDATE itemvenda SET status='D' WHERE id=205;
   INSERT INTO estornovenda VALUES(202);`);
  let rows=[];
  for(const offset of [0,50,100]){
   const page=(await db.query(sql,[1,51,offset])).rows;
   assert(page.every(r=>Number(r.total_produtos)===140));
   rows.push(...page.slice(0,50));
  }
  assert.equal(rows.length,140);assert.equal(new Set(rows.map(r=>Number(r.produtoid))).size,140);
  const first=rows.find(r=>Number(r.produtoid)===1);
  assert.equal(Number(first.compras),2);assert.equal(Number(first.quantidade_original),9);assert.equal(Number(first.valor_estimado),90);
  const partial=rows.find(r=>Number(r.produtoid)===3);
  assert.equal(Number(partial.quantidade_liquida),1);assert.equal(Number(partial.quantidade_devolvida),1);assert.equal(Number(partial.valor_estimado),10);
  const returned=rows.find(r=>Number(r.produtoid)===4);
  assert.equal(Number(returned.quantidade_liquida),0);assert.equal(Number(returned.quantidade_devolvida),2);assert.equal(Number(returned.valor_estimado),0);
  assert.equal(rows.filter(r=>r.produto==='Produto 1').length,2,'Different IDs with same description must stay distinct');
  assert.equal(Number(rows[0].produtoid),1,'Most recent product first');
  assert((await db.query(sql,[999,51,0])).rows.length===0);
  assert.equal((await db.query(sql,[2,51,0])).rows.length,1);
  console.log('PostgreSQL fixtures passed: all 140 products across three pages; repeated purchases and packaging grouped; returns separated; cancelled/reversed/invalid sales excluded; customers isolated.');
 }finally{await db.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
