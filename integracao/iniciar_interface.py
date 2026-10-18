"""Painel unificado Sonifarma. HTTP local, PostgreSQL somente leitura, sem gravar dados pessoais."""
import argparse
import getpass
import json
import os
import re
import sqlite3
import threading
import time
import secrets
import hashlib
from datetime import timedelta
from collections import defaultdict
from statistics import median
import webbrowser
from datetime import date, datetime
from decimal import Decimal
from math import ceil, isfinite
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
    con.execute("""CREATE TABLE IF NOT EXISTS whatsapp_registros (
        id INTEGER PRIMARY KEY AUTOINCREMENT, cliente_id INTEGER NOT NULL,
        ean TEXT NOT NULL, produto_descricao TEXT NOT NULL, previsao TEXT NOT NULL,
        telefone_final TEXT NOT NULL, status TEXT NOT NULL, criado_em TEXT NOT NULL)""")
    # V20: migração aditiva, sem modificar dados antigos.
    cols={r[1] for r in con.execute("PRAGMA table_info(whatsapp_registros)")}
    if 'resultado' not in cols:
        con.execute("ALTER TABLE whatsapp_registros ADD COLUMN resultado TEXT NOT NULL DEFAULT 'Aguardando resposta'")
    if 'atualizado_em' not in cols:
        con.execute("ALTER TABLE whatsapp_registros ADD COLUMN atualizado_em TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS idx_whats_cliente_ean_previsao ON whatsapp_registros(cliente_id,ean,previsao)")
    return con


# Ajustes locais: NÃO alteram o ERP e não são inferências de prescrição.
POS_DB = BASE / "posologias_confirmadas.sqlite3"

def pos_conn():
    con = sqlite3.connect(str(POS_DB), timeout=5)
    con.execute("""CREATE TABLE IF NOT EXISTS posologias (
      cliente_id INTEGER NOT NULL, produto_id INTEGER NOT NULL,
      unidades_dia REAL NOT NULL CHECK(unidades_dia>0 AND unidades_dia<=100),
      confirmado_em TEXT NOT NULL,
      PRIMARY KEY(cliente_id,produto_id))""")
    return con

def posologias_clientes(ids):
    if not ids: return {}
    with TASK_LOCK:
        with pos_conn() as con:
            ph=','.join('?' for _ in ids)
            rows=con.execute('SELECT cliente_id,produto_id,unidades_dia FROM posologias WHERE cliente_id IN ('+ph+')',tuple(ids)).fetchall()
    return {(int(c),int(p)):float(d) for c,p,d in rows}

# Correções administrativas de quantidade por apresentação, por ID da embalagem.
# Nunca grava no ERP. Uma correção só é usada se foi confirmada pelo funcionário.
def apresentacao_conn():
    con=sqlite3.connect(str(POS_DB), timeout=5)
    con.execute("""CREATE TABLE IF NOT EXISTS apresentacoes_confirmadas (
      embalagem_id INTEGER PRIMARY KEY, unidades REAL NOT NULL CHECK(unidades>0 AND unidades<=10000),
      confirmado_em TEXT NOT NULL)""")
    return con

def apresentacoes_confirmadas(ids):
    ids=list({int(i) for i in ids if i is not None})
    if not ids:return {}
    with TASK_LOCK:
        with apresentacao_conn() as con:
            ph=','.join('?' for _ in ids)
            rows=con.execute('SELECT embalagem_id,unidades FROM apresentacoes_confirmadas WHERE embalagem_id IN ('+ph+')',ids).fetchall()
    return {int(k):float(v) for k,v in rows}

# Reconhece apenas unidades sólidas contáveis. Não interpreta mg/ml como unidades.
PADRAO_UNIDADES = re.compile(r'(?:C\s*[/\-]\s*)?(\d{1,4})\s*(?:CPR?S?|COMPRIMIDOS?|CPS?|CAPS?\.?|CÁPSULAS?|CAPSULAS?|DR(?:[ÁA]GEAS?|S?))\b',re.I)
def extrair_unidades(*descricoes):
    achados=[]
    for desc in descricoes:
        if desc:
            for m in PADRAO_UNIDADES.finditer(str(desc)):
                n=int(m.group(1))
                if 1<=n<=1000: achados.append(n)
    return set(achados)

def avaliar_embalagem(r, apresentacao_guia=None, overrides=None):
    emb_id=r.get('embalagemid')
    if emb_id is not None and int(emb_id) in (overrides or {}):
        return (overrides[int(emb_id)],'Confirmada manualmente')
    # Só usa a quantidade do cadastro se for um número válido e não conflitante.
    encontrados=extrair_unidades(r.get('produto'),r.get('embalagem_apresentacao'),apresentacao_guia)
    cadastrado=r.get('quantidade_por_embalagem')
    try:
        n=float(cadastrado)
        if isfinite(n) and n.is_integer() and 1<=n<=1000: encontrados.add(int(n))
    except (TypeError,ValueError): pass
    # Em alguns cadastros 1 representa a embalagem comercial, não 1 comprimido.
    # Quando há uma única quantidade explícita na descrição, mostrá-la como sugestão.
    descritos=extrair_unidades(r.get('produto'),r.get('embalagem_apresentacao'),apresentacao_guia)
    if encontrados=={1,*descritos} and len(descritos)==1 and 1 not in descritos:
        return next(iter(descritos)),'Sugestão da descrição; conferir'
    if len(encontrados)==1: return next(iter(encontrados)),'Quantidade identificada'
    if len(encontrados)>1: return None,'Quantidade conflitante; revisar cadastro'
    return None,'Quantidade não identificada'

def calcular_previsao(ultima_data, linhas_ultima, posologia=None, guia_por_ean=None, overrides=None):
    unidades=0.0; q_caixas=0.0; detalhes=[]
    for r in linhas_ultima:
        qtd=float(r.get('saldo',r.get('quantidade_liquida',0)) or 0)
        if qtd<=0: continue
        ean=str(r.get('ean') or '').strip()
        guia=(guia_por_ean or {}).get(ean,[])
        # Se o guia tem apresentações distintas, não escolher arbitrariamente.
        descricoes={str(x.get('apresentacao') or '').strip().upper() for x in guia}
        guia_ap=next(iter(descricoes)) if len(descricoes)==1 else None
        n,situacao=avaliar_embalagem(r,guia_ap,overrides)
        if len(descricoes)>1 and situacao!='Confirmada manualmente': n=None; situacao='Guia conflitante; revisão necessária'
        detalhes.append({'ean':ean,'embalagemid':r.get('embalagemid'),'caixas':qtd,'unidades_caixa':n,'situacao':situacao})
        q_caixas+=qtd
        if n is not None: unidades+=qtd*n
    quantidade_confiavel=bool(detalhes) and all(x['unidades_caixa'] is not None for x in detalhes)
    if posologia is not None and quantidade_confiavel and unidades>0:
        dias=ceil(unidades/posologia)
        metodo='Posologia confirmada'
        aviso_calculo=None
    else:
        dias=30
        metodo='Ciclo comercial provisório (30 dias)'
        aviso_calculo=('Posologia salva, mas não foi possível recalcular a data: quantidade de unidades por embalagem ausente ou conflitante. Confira o cadastro e a apresentação.' if posologia is not None else None)
    # Equivalência administrativa para um ciclo de 30 dias, NÃO posologia clínica.
    equivalente_30 = round(unidades/30,3) if quantidade_confiavel and unidades>0 else None
    return {'equivalente_comercial_unidades_dia':equivalente_30,
            'proxima_compra':(ultima_data+timedelta(days=dias)).isoformat(),
            'duracao_dias':dias,'metodo':metodo,'posologia':posologia,
            'posologia_exibida':posologia if posologia is not None else equivalente_30,
            'posologia_origem':'Confirmada manualmente' if posologia is not None else 'Automática comercial (não confirmada)',
            'unidades_totais':round(unidades,3) if quantidade_confiavel else None,
            'quantidade_ultima_compra':round(q_caixas,3),
            'detalhes_embalagem':detalhes,'quantidade_confiavel':quantidade_confiavel,'aviso_calculo':aviso_calculo}

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

# Ficha cadastral: campos existentes são descobertos sem alterar o esquema do ERP.
# Nunca gera informações fictícias e evita carregar todas as vendas do cliente.
def dados_cadastrais(cliente_id):
    import psycopg2
    from psycopg2 import sql
    con=psycopg2.connect(**DB)
    try:
        con.set_session(readonly=True, autocommit=False)
        with con.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '15000ms'")
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='pessoa'")
            cols={r[0] for r in cur.fetchall()}
            campos=['id','nome']+[x for x in ('datanascimento','sexo','email','telefone','celular') if x in cols]
            cur.execute(sql.SQL('SELECT {} FROM public.pessoa WHERE id=%s').format(sql.SQL(',').join(map(sql.Identifier,campos))),(cliente_id,))
            row=cur.fetchone()
            if row is None: return None
            dados={k:normalize(v) for k,v in zip(campos,row)}
            # Agregação no nível da venda: evita multiplicar valores por itens.
            cur.execute("""SELECT COUNT(*),MIN(v.datahorafechamento),MAX(v.datahorafechamento),
                              COALESCE(SUM(v.valortotal),0)
                           FROM public.venda v
                           WHERE v.pessoaid=%s AND v.status IN ('F','D')
                             AND v.datahorafechamento IS NOT NULL
                             AND NOT EXISTS(SELECT 1 FROM public.estornovenda e WHERE e.vendaid=v.id)""",(cliente_id,))
            quantidade,primeira,ultima,total=cur.fetchone()
            dados.update(compras_total=quantidade,primeira_compra=normalize(primeira),
                         ultima_compra=normalize(ultima),valor_total=normalize(total),
                         valor_medio=round(float(total)/quantidade,2) if quantidade else 0)
            # Contatos do ERP: combinar DDD e numero quando estiverem separados.
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='contato'")
            cc={r[0] for r in cur.fetchall()}
            if 'pessoaid' in cc:
                candidatos=('contato','valor','descricao','numero','telefone','celular','email',
                            'enderecocontato','enderecotelefonico','numerotelefone','numerocontato')
                campos_contato=[x for x in candidatos if x in cc]
                campos_ddd=[x for x in ('ddd','codigoddd','dddtelefone','dddcelular','codigoarea','areacode') if x in cc]
                tipos=[x for x in ('tipo','tipocontato','tipocontatoid','descricao_tipo') if x in cc]
                if campos_contato:
                    selecionados=list(dict.fromkeys(campos_contato+campos_ddd+tipos))
                    cur.execute(sql.SQL('SELECT {} FROM public.contato WHERE pessoaid=%s LIMIT 100').format(
                        sql.SQL(',').join(map(sql.Identifier,selecionados))),(cliente_id,))
                    telefones=[]; celulares=[]; emails=[]
                    import re
                    for linha in cur.fetchall():
                        registro=dict(zip(selecionados,linha))
                        rotulo=' '.join(str(registro.get(k) or '') for k in tipos).lower()
                        ddd=''
                        for chave in campos_ddd:
                            tentativa=re.sub(r'\D','',str(registro.get(chave) or ''))
                            if len(tentativa)==2:
                                ddd=tentativa
                                break
                        for campo in campos_contato:
                            valor=str(registro.get(campo) or '').strip()
                            if not valor: continue
                            if '@' in valor:
                                if valor not in emails: emails.append(valor)
                                continue
                            digitos=re.sub(r'\D','',valor)
                            # Aceitar 8/9 digitos locais ou telefone com DDD (10/11).
                            # Evitar usar CPF/CNPJ ou codigos internos como contato.
                            if len(digitos) not in (8,9,10,11,12,13): continue
                            if ddd and len(digitos) in (8,9):
                                digitos=ddd+digitos
                            # DDD separado so e acrescentado quando nao esta no numero.
                            if len(digitos)==10:
                                exibido=f'({digitos[:2]}) {digitos[2:6]}-{digitos[6:]}'
                            elif len(digitos)==11:
                                exibido=f'({digitos[:2]}) {digitos[2:7]}-{digitos[7:]}'
                            elif len(digitos)==12 and digitos.startswith('55'):
                                exibido=f'({digitos[2:4]}) {digitos[4:8]}-{digitos[8:]}'
                            elif len(digitos)==13 and digitos.startswith('55'):
                                exibido=f'({digitos[2:4]}) {digitos[4:9]}-{digitos[9:]}'
                            else:
                                exibido=valor  # sem DDD confirmado: nao inventar.
                            local=digitos[-9:] if len(digitos) in (9,11,13) else ''
                            eh_celular=('cel' in campo or 'cel' in rotulo or 'móvel' in rotulo
                                        or 'movel' in rotulo or 'whats' in rotulo
                                        or (len(local)==9 and local.startswith('9')))
                            destino=celulares if eh_celular else telefones
                            if exibido not in destino: destino.append(exibido)
                    if not dados.get('email') and emails: dados['email']=emails[0]
                    if not dados.get('celular') and celulares: dados['celular']=celulares[0]
                    if not dados.get('telefone') and telefones: dados['telefone']=telefones[0]
            return dados
    finally:
        con.rollback();con.close()

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
WITH itens AS (
 SELECT v.id AS vendaid, v.datahorafechamento AS data_compra,
        em.produtoid AS produtoid, pr.descricao AS produto,
        iv.quantidade AS quantidade_original,
        COALESCE((SELECT SUM(d.quantidade) FROM public.itemdevolucaovenda d
                  WHERE d.itemvendaid=iv.id),0) AS quantidade_devolvida,
        GREATEST(0::numeric,iv.quantidade-COALESCE((SELECT SUM(d.quantidade)
                  FROM public.itemdevolucaovenda d WHERE d.itemvendaid=iv.id),0)) AS quantidade_liquida,
        CASE WHEN iv.quantidade>0 THEN ROUND(iv.valortotal *
          GREATEST(0::numeric,iv.quantidade-COALESCE((SELECT SUM(d.quantidade)
          FROM public.itemdevolucaovenda d WHERE d.itemvendaid=iv.id),0))/iv.quantidade,2)
          ELSE 0 END AS valor_estimado
 FROM public.venda v
 JOIN public.itemvenda iv ON iv.vendaid=v.id
 JOIN public.embalagem em ON em.id=iv.embalagemid
 JOIN public.produto pr ON pr.id=em.produtoid
 WHERE v.pessoaid=%s AND v.status IN ('F','D') AND v.datahorafechamento IS NOT NULL
   AND iv.quantidade>0
   AND (iv.status='F' OR (iv.status='D' AND EXISTS
        (SELECT 1 FROM public.itemdevolucaovenda d WHERE d.itemvendaid=iv.id)))
   AND NOT EXISTS (SELECT 1 FROM public.estornovenda e WHERE e.vendaid=v.id)
)
SELECT vendaid, data_compra, produtoid, produto,
       SUM(quantidade_original) AS quantidade_original,
       SUM(quantidade_devolvida) AS quantidade_devolvida,
       SUM(quantidade_liquida) AS quantidade_liquida,
       ROUND(SUM(valor_estimado),2) AS valor_estimado
FROM itens
GROUP BY vendaid, data_compra, produtoid, produto
HAVING SUM(quantidade_liquida)>0
ORDER BY data_compra DESC, vendaid DESC, produtoid
LIMIT 100
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
 AND (%s::boolean = FALSE AND iv.pbmid IS DISTINCT FROM 349 AND iv.pbmsecundariaid IS DISTINCT FROM 349
      OR %s::boolean = TRUE AND (iv.pbmid=ANY(%s::bigint[]) OR iv.pbmsecundariaid=ANY(%s::bigint[])))

 AND (%s::boolean = FALSE OR pr.fabricanteid=ANY(%s::bigint[]))
 AND (%s::boolean = FALSE OR EXISTS (
     SELECT 1 FROM public.classificacaoproduto cp
     WHERE cp.produtoid=pr.id AND cp.classificacaoid=ANY(%s::bigint[])
 ))
ORDER BY v.datahorafechamento DESC, iv.id DESC LIMIT 500
"""
SQL_GUIA = """
SELECT codigobarras, nome, apresentacao
FROM public.guiamedicamento
WHERE codigobarras = ANY(%s)
ORDER BY codigobarras, id LIMIT 300
"""
def calcular_recompras(cliente_id, janela='todos', pbms=None, classificacoes=None):
    pbms = list(pbms or [])
    pbm_on = bool(pbms)
    classificacoes=list(classificacoes or [])
    rows=query(SQL_RECOMPRA,(cliente_id,pbm_on,pbm_on,pbms,pbms,False,[],bool(classificacoes),classificacoes),limit=500)
    eans=list(dict.fromkeys(str(r['ean']).strip() for r in rows if r['ean'] and str(r['ean']).strip()))[:100]
    guia=defaultdict(list)
    if eans:
        for r in query(SQL_GUIA,(eans,),limit=300): guia[str(r['codigobarras']).strip()].append(r)
    groups=defaultdict(list)
    for r in rows:
        if float(r['quantidade_liquida'] or 0)>0: groups[int(r['produtoid'])].append(r)
    pos=posologias_clientes([cliente_id]); overrides=apresentacoes_confirmadas([r['embalagemid'] for r in rows]); saida=[]; hoje=date.today()
    for produtoid,itens in groups.items():
        datas=sorted({date.fromisoformat(str(x['data_compra'])[:10]) for x in itens})
        ultima=datas[-1]; linhas=[dict(x,saldo=x['quantidade_liquida']) for x in itens if str(x['data_compra'])[:10]==ultima.isoformat()]
        previsao=calcular_previsao(ultima,linhas,pos.get((cliente_id,produtoid)),guia,overrides)
        prevista=date.fromisoformat(previsao['proxima_compra'])
        if janela=='atrasadas' and prevista>=hoje: continue
        if janela=='misto3' and not hoje-timedelta(days=3)<=prevista<=hoje+timedelta(days=3): continue
        if janela.startswith('anteriores') and not hoje-timedelta(days=int(janela[10:]))<=prevista<hoje: continue
        if janela in ('7','15','30') and not hoje<=prevista<=hoje+timedelta(days=int(janela)): continue
        if janela=='sem_previsao' and previsao['metodo']=='Posologia confirmada': continue
        latest=itens[0]; ean=str(latest['ean'] or '').strip()
        correspondencias=guia.get(ean,[])
        apresentacoes={str(x['apresentacao'] or '').strip().upper() for x in correspondencias}
        nomes_guia={str(x['nome'] or '').strip().upper() for x in correspondencias if str(x['nome'] or '').strip()}
        situacao_guia=('Sem correspondência' if not correspondencias else 'Único' if len(correspondencias)==1 else
                      'Duplicado: conferir' if len(apresentacoes)>1 else 'Duplicado equivalente')
        saida.append(dict(produtoid=produtoid,embalagemid=latest['embalagemid'],produto=latest['produto'],ean=ean,
            apresentacao_guia=correspondencias[0]['apresentacao'] if len(apresentacoes)==1 else None,
            nome_guia=next(iter(nomes_guia)) if len(nomes_guia)==1 and len(apresentacoes)==1 else None,
            situacao_guia=situacao_guia, apresentacao_cadastro=latest['embalagem_apresentacao'],
            compras_distintas=len(datas),ultima_compra=ultima.isoformat(),**previsao))
    saida.sort(key=lambda x:(x['proxima_compra'],x['produto']))
    return saida[:100],len(rows)==500


# Central V4: paginação por ID da pessoa, sem amostragem dos 120 mais recentes.
# Cada página examina até 60 clientes elegíveis; não afirma ranking global.
SQL_CANDIDATOS_V2 = """
SELECT p.id AS pessoaid, p.nome
FROM public.pessoa p
WHERE p.id > %s
  AND (%s = '' OR p.nome ILIKE %s)
  AND (%s::boolean = FALSE OR p.id=ANY(%s::bigint[]))
  AND (%s = '' OR p.tipo = %s)
  AND (%s = '' OR p.status = %s)
  AND (%s = 0 OR EXTRACT(MONTH FROM p.datanascimento) = %s)
  AND (%s = 0 OR EXTRACT(DAY FROM p.datanascimento) = %s)
  AND (%s = 'todos' OR (CASE WHEN p.crmrecebernotificacao IS NULL THEN 'desconhecido'
         ELSE UPPER(TRIM(p.crmrecebernotificacao)) END) = %s)
  AND EXISTS (SELECT 1 FROM public.venda v JOIN public.itemvenda iv ON iv.vendaid=v.id WHERE v.pessoaid=p.id
       AND v.status IN ('F','D') AND v.datahorafechamento >= CURRENT_TIMESTAMP - INTERVAL '730 days'
       AND (%s::boolean = FALSE AND iv.pbmid IS DISTINCT FROM 349 AND iv.pbmsecundariaid IS DISTINCT FROM 349
            OR %s::boolean = TRUE AND (iv.pbmid=ANY(%s::bigint[]) OR iv.pbmsecundariaid=ANY(%s::bigint[]))))
ORDER BY p.id ASC LIMIT 61
"""
SQL_CENTRAL_ITENS_V2 = """
SELECT v.pessoaid, v.id AS vendaid, v.datahorafechamento AS data_compra,
       em.id AS embalagemid, em.codigobarras AS ean,
       em.apresentacao AS embalagem_apresentacao, em.quantidadeporembalagem AS quantidade_por_embalagem,
       pr.id AS produtoid, pr.descricao AS produto,
       pr.usocontinuo AS uso_continuo,
       GREATEST(0::numeric,iv.quantidade-COALESCE((SELECT SUM(d.quantidade)
            FROM public.itemdevolucaovenda d WHERE d.itemvendaid=iv.id),0)) AS saldo
FROM public.venda v
JOIN public.itemvenda iv ON iv.vendaid=v.id
JOIN public.embalagem em ON em.id=iv.embalagemid
JOIN public.produto pr ON pr.id=em.produtoid
WHERE v.pessoaid=ANY(%s) AND v.status IN ('F','D')
 AND v.datahorafechamento >= CURRENT_TIMESTAMP - INTERVAL '730 days'
 AND v.datahorafechamento IS NOT NULL AND iv.quantidade>0
 AND (iv.status='F' OR (iv.status='D' AND EXISTS
     (SELECT 1 FROM public.itemdevolucaovenda d WHERE d.itemvendaid=iv.id)))
 AND NOT EXISTS (SELECT 1 FROM public.estornovenda e WHERE e.vendaid=v.id)
 AND (%s::boolean = FALSE AND iv.pbmid IS DISTINCT FROM 349 AND iv.pbmsecundariaid IS DISTINCT FROM 349
      OR %s::boolean = TRUE AND (iv.pbmid=ANY(%s::bigint[]) OR iv.pbmsecundariaid=ANY(%s::bigint[])))

 AND (%s::boolean = FALSE OR EXISTS (
     SELECT 1 FROM public.classificacaoproduto cp
     WHERE cp.produtoid=pr.id AND cp.classificacaoid=ANY(%s::bigint[])
 ))
ORDER BY v.datahorafechamento DESC, iv.id DESC LIMIT 12001
"""
# Pesquisa paginada e exclusivamente de leitura. Campos opcionais são usados
# somente quando existem no esquema real do ERP.
FILTROS_EMBALAGEM = {
    'fabricante': ('produto', 'fabricanteid'),
    'classificacao': ('classificacaoproduto', 'classificacaoid'),
    'etiqueta': ('embalagem', 'etiqueta'),
    'grupo_remarcacao': ('embalagem', 'gruporemarcacaoid'),
    'status_produto': ('produto', 'status'),
    'controle_sngpc': ('produto', 'listadcb'),
    'unidade_preco': ('embalagem', 'unidademedidapreco'),
    'preco_unidade': ('embalagem', 'possuiprecoporunidade'),
}


def consultar_pessoas_filtro(q,offset):
    import psycopg2
    con=psycopg2.connect(**DB)
    try:
        con.set_session(readonly=True,autocommit=False)
        with con.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='15000ms'")
            # Pesquisa por nome; CPF/telefone depende dos campos do cadastro ERP.
            cur.execute("SELECT id,nome FROM public.pessoa WHERE nome ILIKE %s ORDER BY nome,id LIMIT 51 OFFSET %s",('%'+q+'%',offset))
            rows=cur.fetchall()
            return {'pessoas':[{'pessoaid':int(r[0]),'nome':r[1]} for r in rows[:50]],'tem_proxima':len(rows)>50}
    finally:
        con.rollback();con.close()

def consultar_opcoes_filtro(tipo):
    if tipo not in ('fabricante','classificacao','grupo_remarcacao','pbm'):
        raise ValueError('Tipo de filtro inválido.')
    import psycopg2
    from psycopg2 import sql
    table={'fabricante':'fabricante','classificacao':'classificacao','grupo_remarcacao':'gruporemarcacao','pbm':'pbm'}[tipo]
    con=psycopg2.connect(**DB)
    try:
        con.set_session(readonly=True,autocommit=False)
        with con.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='15000ms'")
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",(table,))
            columns={r[0] for r in cur.fetchall()}
            label=next((x for x in ('nome','descricao','denominacao') if x in columns),None)
            if 'id' not in columns or not label:
                raise ValueError('Cadastro de '+tipo+' sem ID ou nome reconhecido.')
            if tipo=='classificacao':
                if not {'classificacaopaiid','profundidade'}.issubset(columns):
                    raise ValueError('Hierarquia de classificação indisponível.')
                cur.execute(sql.SQL('SELECT id, {}::text, classificacaopaiid, profundidade, {} FROM public.classificacao ORDER BY profundidade, {}, id LIMIT 2000').format(sql.Identifier(label),sql.Identifier('caminho') if 'caminho' in columns else sql.SQL('NULL::text'),sql.Identifier(label)))
                return {'opcoes':[dict(id=r[0],nome=r[1],paiid=r[2],nivel=r[3],caminho=r[4]) for r in cur.fetchall()]}
            cur.execute(sql.SQL('SELECT id, {}::text FROM public.{} ORDER BY {}, id LIMIT 5000').format(sql.Identifier(label),sql.Identifier(table),sql.Identifier(label)))
            return {'opcoes':[dict(id=r[0],nome=r[1],paiid=None,nivel=0,caminho=None) for r in cur.fetchall()]}
    finally:
        con.rollback()
        con.close()

def consultar_embalagens(f):
    """Pesquisa paginada no cadastro real. Nunca grava no PostgreSQL."""
    import psycopg2
    from psycopg2 import sql
    con = psycopg2.connect(**DB)
    try:
        con.set_session(readonly=True, autocommit=False)
        with con.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='15000ms'")
            cur.execute("""SELECT table_name, column_name FROM information_schema.columns
                           WHERE table_schema='public' AND table_name IN
                           ('produto','embalagem','fabricante','classificacao',
                            'classificacaoproduto','gruporemarcacao')""")
            cols = set(cur.fetchall())
            required = {('embalagem','id'), ('embalagem','produtoid'),
                        ('embalagem','codigobarras'), ('produto','id'), ('produto','descricao')}
            if not required.issubset(cols):
                raise ValueError('Cadastro de embalagens incompatível com os campos conhecidos.')
            def has(table, *fields):
                return all((table, field) in cols for field in fields)
            available = {
                'fabricante': has('produto','fabricanteid') and has('fabricante','id'),
                'classificacao': (has('classificacaoproduto','produtoid','classificacaoid')
                                  and has('classificacao','id')),
                'etiqueta': has('embalagem','etiqueta'),
                'grupo_remarcacao': (has('embalagem','gruporemarcacaoid')
                                     and has('gruporemarcacao','id')),
                'status_produto': has('produto','status'),
                'controle_sngpc': has('produto','listadcb'),
                'unidade_preco': has('embalagem','unidademedidapreco'),
                'preco_unidade': has('embalagem','possuiprecoporunidade'),
            }
            clauses = []
            params = []
            termo = f.get('busca','').strip()
            if termo:
                fields = [sql.SQL('pr.descricao ILIKE %s'),
                          sql.SQL('em.codigobarras::text ILIKE %s')]
                if has('embalagem','apresentacao'):
                    fields.append(sql.SQL('em.apresentacao::text ILIKE %s'))
                clauses.append(sql.SQL('(')+sql.SQL(' OR ').join(fields)+sql.SQL(')'))
                params.extend(['%'+termo+'%']*len(fields))
            produto = f.get('produto','').strip()
            if produto:
                clauses.append(sql.SQL('pr.descricao ILIKE %s'))
                params.append('%'+produto+'%')
            for key in FILTROS_EMBALAGEM:
                value = f.get(key,'').strip()
                if not value:
                    continue
                if not available[key]:
                    raise ValueError('Filtro não disponível no cadastro: '+key)
                if key == 'controle_sngpc':
                    if value not in ('controlado','nao_controlado'):
                        raise ValueError('Controle SNGPC inválido.')
                    controlled = sql.SQL("pr.listadcb IS NOT NULL AND BTRIM(pr.listadcb::text) NOT IN ('', '?')")
                    clauses.append(controlled if value == 'controlado' else
                                   sql.SQL("(pr.listadcb IS NULL OR BTRIM(pr.listadcb::text) IN ('', '?'))"))
                elif key == 'status_produto':
                    if value not in ('A','I'):
                        raise ValueError('Status do produto inválido.')
                    clauses.append(sql.SQL('pr.status = %s'))
                    params.append(value)
                elif key in ('fabricante','classificacao','grupo_remarcacao'):
                    table = {'fabricante':'fabricante', 'classificacao':'classificacao',
                             'grupo_remarcacao':'gruporemarcacao'}[key]
                    # Campos descritivos variam entre versões do ERP: selecionar
                    # apenas colunas efetivamente presentes na tabela de referência.
                    label = next((col for col in ('descricao','nome','denominacao')
                                  if has(table,col)), None)
                    if label is None:
                        raise ValueError('Descrição não encontrada para o filtro: '+key)
                    if key == 'classificacao':
                        expr = sql.SQL('EXISTS (SELECT 1 FROM public.classificacaoproduto cp '
                                       'JOIN public.classificacao c ON c.id=cp.classificacaoid '
                                       'WHERE cp.produtoid=pr.id AND c.{}::text ILIKE %s)').format(sql.Identifier(label))
                    else:
                        ref = 'pr.fabricanteid' if key == 'fabricante' else 'em.gruporemarcacaoid'
                        expr = sql.SQL('EXISTS (SELECT 1 FROM public.{} ref WHERE ref.id={} '
                                       'AND ref.{}::text ILIKE %s)').format(
                                           sql.Identifier(table),sql.SQL(ref),sql.Identifier(label))
                    clauses.append(expr)
                    params.append('%'+value+'%')
                else:
                    table, col = FILTROS_EMBALAGEM[key]
                    alias = 'pr' if table == 'produto' else 'em'
                    clauses.append(sql.SQL('{}.{}::text ILIKE %s').format(
                        sql.Identifier(alias), sql.Identifier(col)))
                    params.append('%'+value+'%')
            for key in ('fabricante','classificacao','grupo_remarcacao'):
                ids=f.get(key+'_ids',[])
                if not ids: continue
                if not available[key]: raise ValueError('Filtro não disponível: '+key)
                if key=='fabricante':
                    clauses.append(sql.SQL('pr.fabricanteid = ANY(%s)'))
                elif key=='grupo_remarcacao':
                    clauses.append(sql.SQL('em.gruporemarcacaoid = ANY(%s)'))
                else:
                    clauses.append(sql.SQL('EXISTS (SELECT 1 FROM public.classificacaoproduto cp WHERE cp.produtoid=pr.id AND cp.classificacaoid = ANY(%s))'))
                params.append(ids)
            where = (sql.SQL(' WHERE ')+sql.SQL(' AND ').join(clauses)) if clauses else sql.SQL('')
            apresentacao = sql.SQL('em.apresentacao') if has('embalagem','apresentacao') else sql.SQL('NULL::text')
            statement = sql.SQL('SELECT em.id AS embalagemid, em.codigobarras AS ean, pr.descricao AS produto, {} AS apresentacao FROM public.embalagem em JOIN public.produto pr ON pr.id=em.produtoid {} ORDER BY pr.descricao, em.id LIMIT 51 OFFSET %s').format(apresentacao,where)
            cur.execute(statement, tuple(params+[f['offset']]))
            linhas = cur.fetchall()
            return {'embalagens':[dict(embalagemid=x[0],ean=x[1],produto=x[2],apresentacao=x[3])
                                   for x in linhas[:50]],
                    'tem_proxima':len(linhas)>50,'campos_disponiveis':available}
    finally:
        con.rollback()
        con.close()

def ids_classificacao_query(args):
    raw=args.get('classificacoes',[''])[0].strip()
    if raw and (len(raw)>2400 or not re.fullmatch(r'[1-9][0-9]{0,17}(?:,[1-9][0-9]{0,17}){0,199}',raw)):
        raise ValueError('Seleção de classificações inválida (máximo 200).')
    return [int(x) for x in raw.split(',')] if raw else []

def central_consultas_v4(f):
    nome=f['nome']; tipo=f['tipo']; status=f['status']; mes=f['mes']; dia=f['dia']; notif=f['notificacao']
    embalagens=set(f.get('embalagens',[]))
    pbms=f.get('pbms',[])
    classificacoes=f.get('classificacoes',[])
    pbm_on=bool(pbms)
    args=(f['cursor'],nome,'%'+nome+'%',bool(f.get('pessoas')),f.get('pessoas',[]),tipo,tipo,status,status,mes,mes,dia,dia,notif,notif,pbm_on,pbm_on,pbms,pbms)
    encontrados=query(SQL_CANDIDATOS_V2,args,limit=61)
    candidatos=encontrados[:60]
    tem_proxima=len(encontrados)>60
    proximo_cursor=int(candidatos[-1]['pessoaid']) if candidatos and tem_proxima else None
    ids=[int(x['pessoaid']) for x in candidatos]
    if not ids:
        return {'clientes':[],'candidatos_avaliados':0,'proximo_cursor':None,'limite_itens':False,'observacao':'Fim da consulta.'}
    nomes={int(x['pessoaid']):x['nome'] for x in candidatos}
    rows=query(SQL_CENTRAL_ITENS_V2,(ids,pbm_on,pbm_on,pbms,pbms,bool(classificacoes),classificacoes),limit=12001)
    if len(rows)>12000:
        # Nunca apresentar resultados incompletos como classificação confiável.
        return {'clientes':[],'candidatos_avaliados':len(ids),'proximo_cursor':None,
                'limite_itens':True,'observacao':'Esta página ultrapassou 12.000 itens. Refine os filtros; nenhuma previsão parcial foi exibida.'}
    groups=defaultdict(list)
    for r in rows:
        if float(r['saldo'] or 0)<=0: continue
        if embalagens and int(r['embalagemid']) not in embalagens: continue
        if f['uso']!='todos' and bool(r['uso_continuo']) != (f['uso']=='sim'): continue
        if f['produto'] and f['produto'].lower() not in str(r['produto'] or '').lower() and f['produto'] not in str(r['ean'] or ''): continue
        groups[(int(r['pessoaid']),int(r['produtoid']))].append(r)
    hoje=date.today(); por_cliente=defaultdict(list)
    pos=posologias_clientes(ids)
    overrides=apresentacoes_confirmadas([r['embalagemid'] for r in rows])
    for (pid,produtoid),itens in groups.items():
        datas=sorted({date.fromisoformat(str(x['data_compra'])[:10]) for x in itens})
        ultima=datas[-1]; ultima_data=ultima.isoformat()
        ultimos=[x for x in itens if str(x['data_compra'])[:10]==ultima_data]
        previsao=calcular_previsao(ultima,ultimos,pos.get((pid,produtoid)),overrides=overrides)
        previsto=date.fromisoformat(previsao['proxima_compra'])
        if f['janela']=='atrasadas':
            if previsto>=hoje: continue
        elif f['janela']=='misto3':
            if not hoje-timedelta(days=3)<=previsto<=hoje+timedelta(days=3): continue
        elif f['janela'].startswith('anteriores'):
            if not hoje-timedelta(days=int(f['janela'][10:]))<=previsto<hoje: continue
        elif not hoje<=previsto<=hoje+timedelta(days=int(f['janela'])): continue
        gaps=[(b-a).days for a,b in zip(datas,datas[1:])]
        intervalo=int(round(median(gaps))) if gaps else None
        # Escore é heurística de prioridade, NÃO probabilidade de uso do medicamento.
        regularidade=max(0,1-(median([abs(g-intervalo) for g in gaps])/max(1,intervalo))) if len(gaps)>=2 else 0
        frequencia=min(1,max(0,len(datas)-1)/6)
        recencia=max(0,1-(hoje-ultima).days/180)
        proximidade=max(0,1-abs((previsto-hoje).days)/30)
        escore=round(40*regularidade+25*frequencia+20*recencia+15*proximidade)
        por_cliente[pid].append({'produto':itens[0]['produto'],'produtoid':produtoid,
          'ultima_compra':ultima_data,'compras_distintas':len(datas),'intervalo_dias':intervalo,
          'escore':escore,'prioridade':'Alta' if escore>=75 else 'Média' if escore>=50 else 'Baixa',
          **previsao})
    saida=[]
    for pid,produtos in por_cliente.items():
        produtos.sort(key=lambda p:(-p['escore'],p['proxima_compra']))
        saida.append({'pessoaid':pid,'nome':nomes.get(pid,'Cliente '+str(pid)),
            'escore':produtos[0]['escore'],'proxima_compra':min((x['proxima_compra'] for x in produtos), key=lambda d: ((0,0) if (date.fromisoformat(d)-hoje).days==0 else (1,(hoje-date.fromisoformat(d)).days) if date.fromisoformat(d)<hoje else (2,(date.fromisoformat(d)-hoje).days))) if f['ordem']=='data_misto' else min(x['proxima_compra'] for x in produtos),
            'produtos':produtos[:30]})
    if f['ordem']=='data_misto':
        def chave_mista(x):
            diferenca=(date.fromisoformat(x['proxima_compra'])-hoje).days
            return (0,0,-x['escore']) if diferenca==0 else (1,-diferenca,-x['escore']) if diferenca<0 else (2,diferenca,-x['escore'])
        saida.sort(key=chave_mista)
    elif f['ordem']=='data_desc': saida.sort(key=lambda x:(x['proxima_compra'],x['escore']),reverse=True)
    elif f['ordem']=='data': saida.sort(key=lambda x:(x['proxima_compra'],-x['escore']))
    elif f['ordem']=='frequencia': saida.sort(key=lambda x:(-max(p['compras_distintas'] for p in x['produtos']),-x['escore']))
    else: saida.sort(key=lambda x:(-x['escore'],x['proxima_compra']))
    return {'clientes':saida,'candidatos_avaliados':len(ids),'proximo_cursor':proximo_cursor,
      'limite_itens':False,'observacao':'Página de até 60 clientes elegíveis. Ordenação por prioridade apenas nesta página; use Próxima página para continuar. A ausência de resultado nesta página não significa ausência na base.'}

# V18: snapshot global em memória, construído em thread separada. Nenhum dado de clientes é salvo em disco.
GLOBAL_JOBS = {}
GLOBAL_LOCK = threading.Lock()
GLOBAL_MAX_CANDIDATOS = 20000  # proteção: nunca apresentar ranking parcial como global
GLOBAL_TTL = 900

def chave_global_cliente(c, janela, hoje):
    dt=date.fromisoformat(c['proxima_compra'])
    dias=(dt-hoje).days
    if janela=='misto3':
        grupo,dist=(0,0) if dias==0 else ((1,-dias) if dias<0 else (2,dias))
        return (grupo,dist,-c['escore'],c['pessoaid'])
    if janela.startswith('anteriores') or janela=='atrasadas':
        return (-dt.toordinal(),-c['escore'],c['pessoaid'])
    return (dt.toordinal(),-c['escore'],c['pessoaid'])

def construir_ranking(job_id, filtros):
    try:
        cursor=0; todos=[]; vistos=set(); avaliados=0
        inicio=time.monotonic()
        while True:
            if time.monotonic()-inicio>600:
                raise RuntimeError('A análise global ultrapassou 10 minutos. Refine os filtros e tente novamente.')
            f=dict(filtros,cursor=cursor)
            parte=central_consultas_v4(f)
            if parte.get('limite_itens'):
                raise RuntimeError(parte['observacao'])
            avaliados+=parte['candidatos_avaliados']
            if avaliados>GLOBAL_MAX_CANDIDATOS:
                raise RuntimeError('A consulta ultrapassou 20.000 candidatos. Refine os filtros para obter ordenação global completa.')
            for cliente in parte['clientes']:
                if cliente['pessoaid'] not in vistos:
                    vistos.add(cliente['pessoaid']);todos.append(cliente)
            with GLOBAL_LOCK:
                if job_id not in GLOBAL_JOBS: return
                GLOBAL_JOBS[job_id]['avaliados']=avaliados
                GLOBAL_JOBS[job_id]['encontrados']=len(todos)
            cursor=parte['proximo_cursor']
            if cursor is None: break
        todos.sort(key=lambda c:chave_global_cliente(c,filtros['janela'],date.today()))
        with GLOBAL_LOCK:
            if job_id in GLOBAL_JOBS:
                GLOBAL_JOBS[job_id].update(status='pronto',clientes=todos,avaliados=avaliados,encontrados=len(todos))
    except Exception as exc:
        # Não registrar dados pessoais, parâmetros ou SQL nos logs.
        with GLOBAL_LOCK:
            if job_id in GLOBAL_JOBS: GLOBAL_JOBS[job_id].update(status='erro',erro=str(exc) if isinstance(exc,RuntimeError) else 'Erro ao calcular ranking global. Confira o terminal do servidor.')
        if not isinstance(exc,RuntimeError): print('Falha na análise global:',type(exc).__name__)

def obter_ranking(f,job_id,offset):
    agora=time.monotonic()
    with GLOBAL_LOCK:
        for k,v in list(GLOBAL_JOBS.items()):
            if agora-v['criado']>GLOBAL_TTL: del GLOBAL_JOBS[k]
        if job_id:
            job=GLOBAL_JOBS.get(job_id)
            if not job: return (410,{'erro':'Consulta expirada. Clique em Consultar clientes novamente.'})
            if job['assinatura']!=hashlib.sha256(json.dumps(f,sort_keys=True).encode()).hexdigest():
                return (400,{'erro':'Os filtros mudaram. Inicie uma nova consulta.'})
            if job['status']=='erro':return (503,{'erro':job['erro']})
            if job['status']!='pronto':return (200,{'preparando':True,'job':job_id,'avaliados':job['avaliados'],'encontrados':job['encontrados']})
            clientes=job['clientes'][offset:offset+60]
            return (200,{'clientes':clientes,'job':job_id,'total':len(job['clientes']),
               'proximo_cursor':offset+60 if offset+60<len(job['clientes']) else None,
               'observacao':'Ordenação global concluída sobre todos os candidatos elegíveis.'})
        assinatura=hashlib.sha256(json.dumps(f,sort_keys=True).encode()).hexdigest()
        for k,v in GLOBAL_JOBS.items():
            if v['assinatura']==assinatura and v['status']!='erro':
                return (200,{'preparando':True,'job':k,'avaliados':v['avaliados'],'encontrados':v['encontrados']})
        if any(v['status']=='calculando' for v in GLOBAL_JOBS.values()):
            return (429,{'erro':'Já existe uma análise global em andamento. Aguarde a conclusão.'})
        novo=secrets.token_urlsafe(24)
        GLOBAL_JOBS[novo]={'assinatura':assinatura,'status':'calculando','criado':agora,'avaliados':0,'encontrados':0,'clientes':None}
    threading.Thread(target=construir_ranking,args=(novo,dict(f)),daemon=True).start()
    return (200,{'preparando':True,'job':novo,'avaliados':0,'encontrados':0})

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
        if self.path not in ('/api/agenda','/api/posologia','/api/ajuste_previsao','/api/whatsapp-registros','/api/whatsapp-resultado','/api/whatsapp-confirmar'): return self.respond(404,{'erro':'Rota inexistente'})
        origin=self.headers.get('Origin','')
        if origin not in ('http://127.0.0.1:8765','http://localhost:8765'):
            return self.respond(403,{'erro':'Origem não autorizada'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 1500: return self.respond(400,{'erro':'Tamanho inválido'})
            d=json.loads(self.rfile.read(length))
            if self.path=='/api/whatsapp-confirmar':
                rid=d.get('id')
                if not isinstance(rid,int) or isinstance(rid,bool) or rid<=0: raise ValueError()
                with TASK_LOCK:
                    with agenda_conn() as con:
                        cur=con.execute("UPDATE whatsapp_registros SET status='Enviado confirmado',atualizado_em=? WHERE id=? AND status='WhatsApp aberto'",(datetime.now().isoformat(timespec='seconds'),rid))
                        if cur.rowcount!=1:return self.respond(409,{'erro':'Registro não encontrado ou já confirmado'})
                return self.respond(200,{'ok':True})
            if self.path=='/api/whatsapp-resultado':
                rid=d.get('id'); resultado=d.get('resultado')
                permitidos=('Aguardando resposta','Interessado','Não precisa agora','Recompra realizada')
                if not isinstance(rid,int) or isinstance(rid,bool) or rid<=0 or resultado not in permitidos: raise ValueError()
                with TASK_LOCK:
                    with agenda_conn() as con:
                        cur=con.execute('UPDATE whatsapp_registros SET resultado=?,atualizado_em=? WHERE id=?',
                            (resultado,datetime.now().isoformat(timespec='seconds'),rid))
                        if cur.rowcount!=1:return self.respond(404,{'erro':'Registro não encontrado'})
                return self.respond(200,{'ok':True})
            if self.path=='/api/whatsapp-registros':
                cid=d.get('cliente_id'); status=d.get('status')
                if not isinstance(cid,int) or isinstance(cid,bool) or not 0<cid<10**17: raise ValueError()
                if status not in ('WhatsApp aberto','Enviado confirmado'): raise ValueError()
                ean=str(d.get('ean',''))[:18]; desc=str(d.get('produto_descricao',''))[:180]
                previsao=str(d.get('previsao',''))[:12]; final=str(d.get('telefone_final',''))
                if not final.isdecimal() or len(final)!=4 or not desc: raise ValueError()
                # Um envio por cliente/produto/ciclo, inclusive sob cliques simultâneos.
                if not previsao or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',previsao):
                    raise ValueError('Previsão inválida')
                with TASK_LOCK:
                    with agenda_conn() as con:
                        existente=con.execute('SELECT id FROM whatsapp_registros WHERE cliente_id=? AND ean=? AND previsao=? AND produto_descricao=? LIMIT 1',
                            (cid,ean,previsao,desc)).fetchone()
                        if existente:return self.respond(409,{'erro':'Este produto já possui registro neste ciclo de recompra.'})
                        con.execute('INSERT INTO whatsapp_registros(cliente_id,ean,produto_descricao,previsao,telefone_final,status,criado_em) VALUES(?,?,?,?,?,?,?)',
                            (cid,ean,desc,previsao,final,status,datetime.now().isoformat(timespec='seconds')))
                return self.respond(200,{'ok':True})
            if self.path=='/api/ajuste_previsao':
                cid=d.get('cliente_id'); pid=d.get('produto_id'); emb=d.get('embalagem_id')
                dose=d.get('unidades_dia'); unidades=d.get('unidades_apresentacao')
                if any(not isinstance(v,int) or isinstance(v,bool) or not 0<v<10**17 for v in (cid,pid,emb)):
                    raise ValueError('Identificadores inválidos')
                if dose is not None and (isinstance(dose,bool) or not isinstance(dose,(int,float)) or not isfinite(dose) or not 0<dose<=100):
                    raise ValueError('Posologia inválida')
                if unidades is not None and (isinstance(unidades,bool) or not isinstance(unidades,(int,float)) or not isfinite(unidades) or not 0<unidades<=10000):
                    raise ValueError('Quantidade de apresentação inválida')
                # Confirmação explícita de ao menos um campo. Nenhuma inferência clínica.
                if dose is None and unidades is None: raise ValueError('Informe posologia ou quantidade')
                with TASK_LOCK:
                    with apresentacao_conn() as con:
                        if unidades is not None:
                            con.execute('INSERT OR REPLACE INTO apresentacoes_confirmadas(embalagem_id,unidades,confirmado_em) VALUES(?,?,?)',
                                (emb,unidades,datetime.now().isoformat(timespec='seconds')))
                        if dose is not None:
                            con.execute('INSERT OR REPLACE INTO posologias(cliente_id,produto_id,unidades_dia,confirmado_em) VALUES(?,?,?,?)',
                                (cid,pid,dose,datetime.now().isoformat(timespec='seconds')))
                with GLOBAL_LOCK: GLOBAL_JOBS.clear()
                return self.respond(200,{'ok':True})
            if self.path=='/api/posologia':
                cid=d.get('cliente_id'); pid=d.get('produto_id'); dose=d.get('unidades_dia')
                if any(not isinstance(v,int) or isinstance(v,bool) or not 0<v<10**17 for v in (cid,pid)):
                    raise ValueError()
                with TASK_LOCK:
                    with pos_conn() as con:
                        if d.get('acao')=='salvar':
                            if isinstance(dose,bool) or not isinstance(dose,(int,float)) or not isfinite(dose) or not 0<dose<=100:
                                raise ValueError()
                            con.execute('INSERT OR REPLACE INTO posologias(cliente_id,produto_id,unidades_dia,confirmado_em) VALUES(?,?,?,?)',
                                (cid,pid,dose,datetime.now().isoformat(timespec='seconds')))
                        elif d.get('acao')=='remover':
                            con.execute('DELETE FROM posologias WHERE cliente_id=? AND produto_id=?',(cid,pid))
                        else: raise ValueError()
                with GLOBAL_LOCK:
                    GLOBAL_JOBS.clear()  # invalida previsões após ajuste de posologia
                return self.respond(200,{'ok':True})
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
                cid=d.get('cliente_id')
                if cid is not None and (not isinstance(cid,int) or isinstance(cid,bool) or not 0<cid<10**17): raise ValueError()
                with TASK_LOCK:
                    with agenda_conn() as con:
                        if cid is None:
                            cur=con.execute("UPDATE tarefas SET status='Concluída' WHERE id=? AND status='Pendente'",(tid,))
                        else:
                            cur=con.execute("UPDATE tarefas SET status='Concluída' WHERE id=? AND cliente_id=? AND status='Pendente'",(tid,cid))
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
        if parsed.path=='/api/whatsapp-registros':
            filtros=parse_qs(parsed.query);cid=filtros.get('cliente_id',[None])[0]
            if cid is not None and (not cid.isdecimal() or not 0<int(cid)<10**17): return self.respond(400,{'erro':'Cliente inválido'})
            with TASK_LOCK:
                with agenda_conn() as con:
                    colunas='id,cliente_id,ean,produto_descricao,previsao,telefone_final,status,criado_em,resultado,atualizado_em'
                    if cid is None: rows=con.execute('SELECT '+colunas+' FROM whatsapp_registros ORDER BY id DESC LIMIT 200').fetchall()
                    else: rows=con.execute('SELECT '+colunas+' FROM whatsapp_registros WHERE cliente_id=? ORDER BY id DESC LIMIT 200',(int(cid),)).fetchall()
            return self.respond(200,{'registros':[dict(zip(('id','cliente_id','ean','produto_descricao','previsao','telefone_final','status','criado_em','resultado','atualizado_em'),r)) for r in rows]})
        if parsed.path=='/api/agenda':
            filtros=parse_qs(parsed.query)
            cid_raw=filtros.get('cliente_id',[None])[0]
            if cid_raw is not None and (not cid_raw.isdecimal() or not 0<int(cid_raw)<10**17):
                return self.respond(400,{'erro':'Cliente inválido'})
            with TASK_LOCK:
                with agenda_conn() as con:
                    if cid_raw is not None:
                        rows=con.execute("SELECT id, cliente_id, data_prevista, motivo, status, criado_em FROM tarefas WHERE cliente_id=? ORDER BY CASE status WHEN 'Pendente' THEN 0 ELSE 1 END, data_prevista ASC, id DESC LIMIT 150",(int(cid_raw),)).fetchall()
                    else:
                        rows=con.execute("SELECT id, cliente_id, data_prevista, motivo, status, criado_em FROM tarefas ORDER BY CASE status WHEN 'Pendente' THEN 0 ELSE 1 END, data_prevista ASC, id DESC LIMIT 150").fetchall()
            return self.respond(200,{'tarefas':[dict(zip(('id','cliente_id','data_prevista','motivo','status','criado_em'),r)) for r in rows]})
        if parsed.path=='/api/status':
            return self.respond(200,{'erp':bool(DB),'modo':'local / somente leitura'})
        if not DB: return self.respond(503,{'erro':'ERP não conectado. Reinicie com --erp.'})
        args=parse_qs(parsed.query)
        try:
            if parsed.path=='/api/pessoas-filtro':
                q=args.get('q',[''])[0].strip();offset=args.get('offset',['0'])[0]
                if len(q)<3 or len(q)>70 or not offset.isdecimal() or int(offset)>100000:
                    return self.respond(400,{'erro':'Pesquisa de pessoas inválida.'})
                return self.respond(200,consultar_pessoas_filtro(q,int(offset)))
            if parsed.path=='/api/filtro-opcoes':
                try: return self.respond(200,consultar_opcoes_filtro(args.get('tipo',[''])[0]))
                except ValueError as e: return self.respond(400,{'erro':str(e)})
            if parsed.path=='/api/embalagens':
                filtros={k:args.get(k,[''])[0].strip() for k in ['busca','produto',*FILTROS_EMBALAGEM]}
                offset=args.get('offset',['0'])[0]
                if not offset.isdecimal() or int(offset)>10000 or any(len(v)>80 for v in filtros.values()):
                    return self.respond(400,{'erro':'Pesquisa de embalagens inválida.'})
                for key in ('fabricante','classificacao','grupo_remarcacao'):
                    raw=args.get(key+'_ids',[''])[0]
                    parts=raw.split(',') if raw else []
                    if len(parts)>500 or any(not x.isdecimal() or not 0<int(x)<10**17 for x in parts):
                        return self.respond(400,{'erro':'Seleção múltipla inválida: '+key})
                    filtros[key+'_ids']=[int(x) for x in parts]
                filtros['offset']=int(offset)
                try: return self.respond(200,consultar_embalagens(filtros))
                except ValueError as e: return self.respond(400,{'erro':str(e)})
            if parsed.path=='/api/central':
                f={k:args.get(k,[default])[0].strip() for k,default in {
                  'janela':'misto3','nome':'','uso':'todos','ordem':'data_misto','produto':'',
                  'tipo':'','status':'','mes':'0','dia':'0','notificacao':'todos','cursor':'0'}.items()}
                if (f['janela'] not in ('misto3','7','15','30','anteriores7','anteriores15','anteriores30','atrasadas') or
                    f['uso'] not in ('todos','sim','nao') or
                    f['ordem'] not in ('escore','data','data_desc','data_misto','frequencia') or
                    f['notificacao'] not in ('todos','S','N','desconhecido') or
                    len(f['nome'])>70 or (f['nome'] and len(f['nome'])<3) or
                    len(f['produto'])>80 or (f['produto'] and len(f['produto'])<3) or
                    not re.fullmatch(r'[A-Za-z]?',f['tipo']) or
                    not re.fullmatch(r'[A-Za-z]?',f['status']) or
                    not re.fullmatch(r'[0-9]{1,2}',f['mes']) or
                    not re.fullmatch(r'[0-9]{1,2}',f['dia']) or
                    not re.fullmatch(r'[0-9]{1,18}',f['cursor'])):
                    return self.respond(400,{'erro':'Filtros inválidos.'})
                raw_ids=args.get('embalagens',[''])[0].strip()
                if raw_ids and (len(raw_ids)>1500 or not re.fullmatch(r'[1-9][0-9]{0,17}(?:,[1-9][0-9]{0,17}){0,99}',raw_ids)):
                    return self.respond(400,{'erro':'Seleção de embalagens inválida (máximo 100).'})
                f['embalagens']=[int(x) for x in raw_ids.split(',')] if raw_ids else []
                pbm_raw=args.get('pbms',[''])[0].strip()
                if pbm_raw and (len(pbm_raw)>1500 or not re.fullmatch(r'[1-9][0-9]{0,17}(?:,[1-9][0-9]{0,17}){0,99}',pbm_raw)):
                    return self.respond(400,{'erro':'Seleção PBM inválida (máximo 100).'})
                f['pbms']=[int(x) for x in pbm_raw.split(',')] if pbm_raw else []
                for campo in ('pessoas','fabricantes'):
                    raw=args.get(campo,[''])[0].strip()
                    if raw and (len(raw)>1900 or not re.fullmatch(r'[1-9][0-9]{0,17}(?:,[1-9][0-9]{0,17}){0,99}',raw)):
                        return self.respond(400,{'erro':'Seleção inválida: '+campo+' (máximo 100).'})
                    f[campo]=[int(x) for x in raw.split(',')] if raw else []
                try: f['classificacoes']=ids_classificacao_query(args)
                except ValueError as e: return self.respond(400,{'erro':str(e)})
                f['mes']=int(f['mes']); f['dia']=int(f['dia']); f['cursor']=int(f['cursor'])
                if f['mes']>12 or f['dia']>31: return self.respond(400,{'erro':'Data inválida.'})
                job_id=args.get('job',[''])[0]
                if len(job_id)>64 or (job_id and not re.fullmatch(r'[A-Za-z0-9_-]+',job_id)):
                    return self.respond(400,{'erro':'Consulta inválida.'})
                offset=f.pop('cursor')
                if offset>GLOBAL_MAX_CANDIDATOS:return self.respond(400,{'erro':'Posição inválida.'})
                status,payload=obter_ranking(f,job_id,offset)
                return self.respond(status,payload)
            if parsed.path=='/api/guia-ean':
                ean=args.get('ean',[''])[0].strip()
                if not re.fullmatch(r'[0-9]{8,14}',ean):
                    return self.respond(400,{'erro':'EAN inválido.'})
                registros=query(SQL_GUIA,([ean],),limit=30)
                pares={(str(x.get('nome') or '').strip(),str(x.get('apresentacao') or '').strip()) for x in registros}
                if len(pares)!=1:
                    return self.respond(200,{'confirmado':False,'situacao':'Sem correspondência' if not pares else 'Guia divergente; conferir'})
                nome,apresentacao=next(iter(pares))
                return self.respond(200,{'confirmado':bool(nome),'nome':nome,'apresentacao':apresentacao,'situacao':'EAN conferido no Guia'})
            if parsed.path=='/api/recompra':
                ident=args.get('id',[''])[0]
                janela=args.get('janela',['todos'])[0]
                if not re.fullmatch(r'[1-9][0-9]{0,17}',ident) or janela not in ('todos','misto3','7','15','30','anteriores7','anteriores15','anteriores30','atrasadas','sem_previsao'):
                    return self.respond(400,{'erro':'Filtro ou cliente inválido.'})
                pbm_raw=args.get('pbms',[''])[0].strip()
                if pbm_raw and (len(pbm_raw)>1500 or not re.fullmatch(r'[1-9][0-9]{0,17}(?:,[1-9][0-9]{0,17}){0,99}',pbm_raw)):
                    return self.respond(400,{'erro':'Seleção PBM inválida (máximo 100).'})
                pbms=[int(x) for x in pbm_raw.split(',')] if pbm_raw else []
                try: classificacoes=ids_classificacao_query(args)
                except ValueError as e: return self.respond(400,{'erro':str(e)})
                previsoes,limite=calcular_recompras(int(ident),janela,pbms,classificacoes)
                return self.respond(200,{'previsoes':previsoes,'historico_limitado':limite,
                  'observacao':'Previsões estatísticas; não equivalem a prescrição nem confirmam necessidade de medicamento.'})
            if parsed.path=='/api/ficha-cadastro':
                ident=args.get('id',[''])[0]
                if not re.fullmatch(r'[1-9][0-9]{0,17}',ident):
                    return self.respond(400,{'erro':'Identificador inválido.'})
                dados=dados_cadastrais(int(ident))
                return self.respond(200,{'cadastro':dados}) if dados else self.respond(404,{'erro':'Cliente não encontrado.'})
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
    with pos_conn(): pass
    server=ThreadingHTTPServer(('127.0.0.1',8765),Handler)
    print('Painel unificado: http://127.0.0.1:8765')
    print('ERP:', 'conectado em modo leitura' if DB else 'desconectado (somente dashboard)')
    print('Ctrl+C para encerrar.')
    webbrowser.open('http://127.0.0.1:8765')
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=='__main__': main()
