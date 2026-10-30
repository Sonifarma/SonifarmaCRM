// Runs the production SQL with PostgreSQL via PGlite; no ERP access.
const {PGlite}=require(process.env.PGLITE_MODULE||'@electric-sql/pglite');
const fs=require('fs'),path=require('path'),assert=require('assert/strict');
(async()=>{
 const db=new PGlite();
 try{
  await db.exec(`CREATE TABLE pessoa(id integer PRIMARY KEY,nome text,datanascimento date);
   CREATE TABLE venda(id integer PRIMARY KEY,pessoaid integer,status text,datahorafechamento timestamp);
   CREATE TABLE estornovenda(vendaid integer);
   INSERT INTO pessoa SELECT i,'Cliente '||i,make_date(1990,EXTRACT(MONTH FROM CURRENT_DATE)::int,15) FROM generate_series(1,7) i;
   INSERT INTO venda VALUES
    (1,1,'F',CURRENT_DATE-INTERVAL '18 months'),
    (2,2,'F',CURRENT_DATE-INTERVAL '18 months'-INTERVAL '1 day'),
    (3,3,'D',CURRENT_DATE-INTERVAL '1 day'),
    (4,4,'F',CURRENT_DATE-INTERVAL '1 day'),
    (6,6,'F',CURRENT_DATE+INTERVAL '2 days'),
    (7,7,'C',CURRENT_DATE-INTERVAL '1 day');
   INSERT INTO estornovenda VALUES(4);`);
  const source=fs.readFileSync(path.join(__dirname,'../integracao/painel/iniciar_interface.py'),'utf8');
  let n=0;const sql=source.match(/SQL_ANIVERSARIANTES = """([\s\S]*?)"""/)[1].replace(/%s/g,()=>'$'+(++n));
  const month=(await db.query('SELECT EXTRACT(MONTH FROM CURRENT_DATE)::int AS mes')).rows[0].mes;
  const ids=async recent=>(await db.query(sql,[month,recent,0])).rows.map(r=>r.pessoaid);
  assert.deepEqual(await ids(true),[1,3]);
  assert.deepEqual(await ids(false),[1,2,3,6]);
  assert.equal((await db.query(sql,[month,true,1])).rows[0].pessoaid,3);
  console.log('PASS: calendar 18-month boundary included, older/future/cancelled/reversed sales excluded, all-history option, offset');
 }finally{await db.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
