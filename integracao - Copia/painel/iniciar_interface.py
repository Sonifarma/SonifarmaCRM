"""Painel unificado Sonifarma. HTTP local, PostgreSQL somente leitura, sem gravar dados pessoais."""
import argparse
import getpass
import json
import os
import re
import sqlite3
import threading
from datetime import timedelta
from collections import defaultdict
from statistics import median
import webbrowser
from datetime import date, datetime
from decimal import Decimal
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE = Path(__file__).resolve().parent
os.chdir(BASE)
DB = None
TASK_DB = BASE / "agenda_posvenda.sqlite3"
TASK_LOCK = threading.Lock()

def agenda_conn():
    con=sqlite3.connect(str(TASK_DB), timeout=5)
    con.execute("CREATE TABLE IF NOT EXISTS tarefas (id INTEGER PRIMARY KEY AUTOINCREMENT, cliente_id INTEGER NOT NULL, data_prevista TEXT NOT NULL, motivo TEXT NOT NULL CHECK(motivo IN ('Acompanhamento','Retorno solicitado','Conferir cadastro')), status TEXT NOT NULL DEFAULT 'Pendente' CHECK(status IN ('Pendente','Concluída')), criado_em TEXT NOT NULL)")
    return con


def config():
    vals = {}
    for path in [BASE.parent / '.env', BASE / '.env']:
        if path.exists():
            for line in path.read_text(encoding='utf-8-sig').splitlines():
                if '=' in line and not line.lstrip().startswith('#'):
                    k, v = line.split('=', 1)
                    vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals

def normalize(v):
    if isinstance(v, Decimal): return float(v)
    if isinstance(v, (date, datetime)): return v.isoformat()
    return v

def query(sql, args=(), limit=101):
    import psycopg2
    con = psycopg2.connect(**DB)
    try:
        con.set_session(readonly=True, autocommit=False)
        with con.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '15000ms'")
            cur.execute("SET LOCAL lock_timeout = '1000ms'")
            cur.execute(sql, args)
            keys = [c[0] for c in cur.description]
            return [{k:normalize(v) for k,v in zip(keys,row)} for row in cur.fetchmany(limit)]
    finally:
        con.rollback()
        con.close()

# Busca por nome com limite e somente pessoas com vendas registradas.
SQL_CLIENTES = """
SELECT p.id AS pessoaid, p.nome,
       (SELECT MAX(v.datahorafechamento) FROM public.venda v
        WHERE v.pessoaid=p.id AND v.status IN ('F','D')
          AND v.datahorafechamento IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM public.estornovenda e WHERE e.vendaid=v.id)) AS ultima_compra,
       (SELECT COUNT(*) FROM public.venda v
        WHERE v.pessoaid=p.id AND v.status IN ('F','D')
          AND v.datahorafechamento IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM public.estornovenda e WHERE e.vendaid=v.id)) AS vendas_candidatas
FROM public.pessoa p
WHERE p.nome ILIKE %s
  AND EXISTS (SELECT 1 FROM public.venda v WHERE v.pessoaid=p.id AND v.status IN ('F','D'))
ORDER BY p.nome, p.id LIMIT 50
"""
# Para cada item calcula o total devolvido; não multiplica linhas da venda.
SQL_HISTORICO = """
SELECT v.id AS vendaid, v.datahorafechamento AS data_compra,
       iv.id AS itemid, pr.descricao AS produto, iv.quantidade AS quantidade_original,
       COALESCE((SELECT SUM(d.quantidade) FROM public.itemdevolucaovenda d
                 WHERE d.itemvendaid=iv.id),0) AS quantidade_devolvida,
       GREATEST(0::numeric,iv.quantidade-COALESCE((SELECT SUM(d.quantidade)
                 FROM public.itemdevolucaovenda d WHERE d.itemvendaid=iv.id),0)) AS quantidade_liquida,
       CASE WHEN iv.quantidade>0 THEN ROUND(iv.valortotal *
         GREATEST(0::numeric,iv.quantidade-COALESCE((SELECT SUM(d.quantidade)
         FROM public.itemdevolucaovenda d WHERE d.itemvendaid=iv.id),0))/iv.quantidade,2)
         ELSE 0 END AS valor_estimado,
       v.status AS status_venda, iv.status AS status_item
FROM public.venda v
JOIN public.itemvenda iv ON iv.vendaid=v.id
JOIN public.embalagem em ON em.id=iv.embalagemid
JOIN public.produto pr ON pr.id=em.produtoid
WHERE v.pessoaid=%s AND v.status IN ('F','D') AND v.datahorafechamento IS NOT NULL
 AND iv.quantidade>0
 AND (iv.status='F' OR (iv.status='D' AND EXISTS
      (SELECT 1 FROM public.itemdevolucaovenda d WHERE d.itemvendaid=iv.id)))
 AND NOT EXISTS (SELECT 1 FROM public.estornovenda e WHERE e.vendaid=v.id)
 AND iv.quantidade>COALESCE((SELECT SUM(d.quantidade) FROM public.itemdevolucaovenda d
                              WHERE d.itemvendaid=iv.id),0)
ORDER BY v.datahorafechamento DESC, iv.id DESC LIMIT 100
"""


# Módulo 4: histórico recente por produto, sempre limitado a um cliente.
# O EAN é consultado no guia separadamente para não multiplicar itens com EAN duplicado.
SQL_RECOMPRA = """
SELECT v.datahorafechamento AS data_compra, v.id AS vendaid,
       iv.id AS itemid, em.id AS embalagemid, em.codigobarras AS ean,
       em.apresentacao AS embalagem_apresentacao,
       em.quantidadeporembalagem AS quantidade_por_embalagem,
       pr.descricao AS produto, pr.id AS produtoid,
       GREATEST(0::numeric, iv.quantidade - COALESCE((
           SELECT SUM(d.quantidade) FROM public.itemdevolucaovenda d
           WHERE d.itemvendaid=iv.id),0)) AS quantidade_liquida
FROM public.venda v
JOIN public.itemvenda iv ON iv.vendaid=v.id
JOIN public.embalagem em ON em.id=iv.embalagemid
JOIN public.produto pr ON pr.id=em.produtoid
WHERE v.pessoaid=%s AND v.status IN ('F','D')
  AND v.datahorafechamento IS NOT NULL
  AND v.datahorafechamento >= CURRENT_TIMESTAMP - INTERVAL '730 days'
  AND iv.quantidade>0
  AND (iv.status='F' OR (iv.status='D' AND EXISTS
      (SELECT 1 FROM public.itemdevolucaovenda d WHERE d.itemvendaid=iv.id)))
  AND NOT EXISTS (SELECT 1 FROM public.estornovenda e WHERE e.vendaid=v.id)
ORDER BY v.datahorafechamento DESC, iv.id DESC LIMIT 500
"""
SQL_GUIA = """
SELECT codigobarras, nome, apresentacao
FROM public.guiamedicamento
WHERE codigobarras = ANY(%s)
ORDER BY codigobarras, id LIMIT 300
"""
def calcular_recompras(cliente_id, janela='todos'):
    rows=query(SQL_RECOMPRA,(cliente_id,),limit=500)
    eans=list(dict.fromkeys(str(r['ean']).strip() for r in rows if r['ean'] and str(r['ean']).strip()))[:100]
    guia=defaultdict(list)
    if eans:
        for r in query(SQL_GUIA,(eans,),limit=300):
            guia[str(r['codigobarras']).strip()].append(r)
    groups=defaultdict(list)
    for r in rows:
        if float(r['quantidade_liquida'] or 0)>0:
            groups[r['embalagemid']].append(r)
    saida=[]
    hoje=date.today()
    for embalagemid, itens in groups.items():
        latest=itens[0]
        # Mesmo produto em compras distintas no mesmo dia não conta como recorrência separada.
        datas=sorted({date.fromisoformat(str(x['data_compra'])[:10]) for x in itens})
        intervalos=[(b-a).days for a,b in zip(datas,datas[1:]) if (b-a).days>0]
        intervalo=int(round(median(intervalos))) if len(datas)>=3 and len(intervalos)>=2 else None
        prevista=datas[-1]+timedelta(days=intervalo) if intervalo else None
        ean=str(latest['ean'] or '').strip()
        correspondencias=guia.get(ean,[])
        apresentacoes={str(x['apresentacao'] or '').strip().upper() for x in correspondencias}
        situacao_guia=('Sem correspondência' if not correspondencias else
                       'Único' if len(correspondencias)==1 else
                       'Duplicado: conferir' if len(apresentacoes)>1 else 'Duplicado equivalente')
        if janela=='atrasadas' and not(prevista and prevista<hoje): continue
        if janela in ('7','15','30') and not(prevista and hoje<=prevista<=hoje+timedelta(days=int(janela))): continue
        if janela=='sem_previsao' and prevista: continue
        saida.append(dict(embalagemid=embalagemid,produto=latest['produto'],
            ean=ean, apresentacao_guia=correspondencias[0]['apresentacao'] if len(apresentacoes)==1 else None,
            situacao_guia=situacao_guia, apresentacao_cadastro=latest['embalagem_apresentacao'],
            quantidade_por_embalagem=latest['quantidade_por_embalagem'],
            compras_distintas=len(datas), ultima_compra=datas[-1].isoformat(),
            intervalo_mediano_dias=intervalo, proxima_compra=prevista.isoformat() if prevista else None,
            metodo='Intervalo histórico (estimativa)' if prevista else 'Dados insuficientes'))
    saida.sort(key=lambda x:(x['proxima_compra'] is None,x['proxima_compra'] or '9999',x['produto']))
    return saida[:100],len(rows)==500

class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control','no-store, private')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
        super().end_headers()
    def log_message(self, fmt, *args):
        # Não registra termos de pesquisa nem identificadores pessoais no log.
        if not self.path.startswith('/api/'): super().log_message(fmt,*args)
    def respond(self, status, payload):
        raw=json.dumps(payload,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def do_POST(self):
        if self.path != '/api/agenda': return self.respond(404,{'erro':'Rota inexistente'})
        origin=self.headers.get('Origin','')
        if origin not in ('http://127.0.0.1:8765','http://localhost:8765'):
            return self.respond(403,{'erro':'Origem não autorizada'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 1500: return self.respond(400,{'erro':'Tamanho inválido'})
            d=json.loads(self.rfile.read(length))
            if d.get('acao')=='criar':
                cid=d.get('cliente_id')
                dt=date.fromisoformat(d.get('data_prevista',''))
                motivo=d.get('motivo')
                if not isinstance(cid,int) or isinstance(cid,bool) or cid<1 or cid>10**17: raise ValueError()
                if not date.today()<=dt<=date.today()+timedelta(days=365): raise ValueError()
                if motivo not in ('Acompanhamento','Retorno solicitado','Conferir cadastro'): raise ValueError()
                with TASK_LOCK:
                    with agenda_conn() as con:
                        con.execute('INSERT INTO tarefas(cliente_id,data_prevista,motivo,criado_em) VALUES(?,?,?,?)',(cid,dt.isoformat(),motivo,datetime.now().isoformat(timespec='seconds')))
                return self.respond(200,{'ok':True})
            if d.get('acao')=='concluir':
                tid=d.get('id')
                if not isinstance(tid,int) or isinstance(tid,bool) or tid<1: raise ValueError()
                with TASK_LOCK:
                    with agenda_conn() as con:
                        cur=con.execute("UPDATE tarefas SET status='Concluída' WHERE id=? AND status='Pendente'",(tid,))
                return self.respond(200,{'ok':cur.rowcount>0})
            raise ValueError()
        except (ValueError,TypeError,KeyError,json.JSONDecodeError):
            return self.respond(400,{'erro':'Dados inválidos'})
        except Exception:
            return self.respond(503,{'erro':'Agenda indisponível'})

    def do_GET(self):
        parsed=urlparse(self.path)
        if not parsed.path.startswith('/api/'):
            if parsed.path not in ('/','/index.html','/modulo1_resultado_agregado.json'):
                self.send_error(404)
                return
            return super().do_GET()
        if parsed.path=='/api/agenda':
            with TASK_LOCK:
                with agenda_conn() as con:
                    rows=con.execute("SELECT id, cliente_id, data_prevista, motivo, status, criado_em FROM tarefas ORDER BY CASE status WHEN 'Pendente' THEN 0 ELSE 1 END, data_prevista ASC, id DESC LIMIT 150").fetchall()
            return self.respond(200,{'tarefas':[dict(zip(('id','cliente_id','data_prevista','motivo','status','criado_em'),r)) for r in rows]})
        if parsed.path=='/api/status':
            return self.respond(200,{'erp':bool(DB),'modo':'local / somente leitura'})
        if not DB: return self.respond(503,{'erro':'ERP não conectado. Reinicie com --erp.'})
        args=parse_qs(parsed.query)
        try:
            if parsed.path=='/api/recompra':
                ident=args.get('id',[''])[0]
                janela=args.get('janela',['todos'])[0]
                if not re.fullmatch(r'[1-9][0-9]{0,17}',ident) or janela not in ('todos','7','15','30','atrasadas','sem_previsao'):
                    return self.respond(400,{'erro':'Filtro ou cliente inválido.'})
                previsoes,limite=calcular_recompras(int(ident),janela)
                return self.respond(200,{'previsoes':previsoes,'historico_limitado':limite,
                  'observacao':'Previsões estatísticas; não equivalem a prescrição nem confirmam necessidade de medicamento.'})
            if parsed.path=='/api/clientes':
                nome=args.get('q',[''])[0].strip()
                if len(nome)<3 or len(nome)>70:
                    return self.respond(400,{'erro':'Digite ao menos 3 e no máximo 70 caracteres.'})
                return self.respond(200,{'clientes':query(SQL_CLIENTES,('%'+nome+'%',))})
            if parsed.path=='/api/historico':
                ident=args.get('id',[''])[0]
                if not re.fullmatch(r'[1-9][0-9]{0,17}',ident):
                    return self.respond(400,{'erro':'Identificador inválido.'})
                return self.respond(200,{'itens':query(SQL_HISTORICO,(int(ident),))})
            return self.respond(404,{'erro':'Rota inexistente'})
        except Exception:
            # Evita expor detalhes de SQL/servidor a quem acessa a interface.
            return self.respond(503,{'erro':'Consulta indisponível ou excedeu 15 segundos. Confira o terminal do servidor.'})

def main():
    global DB
    parser=argparse.ArgumentParser()
    parser.add_argument('--erp',action='store_true',help='Habilitar consulta ao PostgreSQL')
    a=parser.parse_args()
    if a.erp:
        import psycopg2
        cfg=config()
        senha=getpass.getpass('Senha PostgreSQL (não será salva): ')
        if not senha: raise SystemExit('Senha vazia.')
        DB=dict(host=cfg.get('PGHOST','192.168.0.250'),port=int(cfg.get('PGPORT','5432')),
                dbname=cfg.get('PGDATABASE','sonifarma_loja01'),user=cfg.get('PGUSER','leitura_03012024'),
                password=senha,sslmode=cfg.get('PGSSLMODE','prefer'),connect_timeout=6,
                application_name='sonifarma_painel_consulta_readonly')
        # Confirma conexão e permissões antes de servir a interface.
        result=query('SELECT current_setting(\'transaction_read_only\') AS somente_leitura')
        if result[0]['somente_leitura']!='on': raise SystemExit('Acesso sem garantia de somente leitura.')
    with agenda_conn(): pass
    server=ThreadingHTTPServer(('127.0.0.1',8765),Handler)
    print('Painel unificado: http://127.0.0.1:8765')
    print('ERP:', 'conectado em modo leitura' if DB else 'desconectado (somente dashboard)')
    print('Ctrl+C para encerrar.')
    webbrowser.open('http://127.0.0.1:8765')
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=='__main__': main()
