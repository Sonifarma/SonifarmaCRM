"""Painel unificado Sonifarma. HTTP local, PostgreSQL somente leitura, sem gravar dados pessoais."""
import argparse
from contextlib import closing
import getpass
import json
import os
import re
import sqlite3
import threading
import time
import secrets
import hashlib
import hmac
import ssl
import shutil
import zipfile
import tempfile
from http.cookies import SimpleCookie
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

# V20.4: autenticação, auditoria e backup separados do ERP.
AUTH_DB = BASE / 'acesso_auditoria.sqlite3'
AUTH_LOCK = threading.RLock()
SESSIONS = {}  # Sessões somente em memória: reiniciar encerra todas.
LOGIN_FAILURES = {}
SESSION_TTL = 8 * 3600

def auth_conn():
    con=sqlite3.connect(str(AUTH_DB),timeout=15)
    con.execute('PRAGMA busy_timeout=15000')
    con.execute('PRAGMA journal_mode=WAL')
    con.execute("""CREATE TABLE IF NOT EXISTS usuarios(id INTEGER PRIMARY KEY AUTOINCREMENT,
        login TEXT NOT NULL UNIQUE, senha_hash TEXT NOT NULL, perfil TEXT NOT NULL
        CHECK(perfil IN ('master','supervisor','atendente')), ativo INTEGER NOT NULL DEFAULT 1,
        criado_em TEXT NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS auditoria(id INTEGER PRIMARY KEY AUTOINCREMENT,
        momento TEXT NOT NULL, usuario TEXT NOT NULL, atividade TEXT NOT NULL,
        cliente_id TEXT NOT NULL DEFAULT '', registro_id TEXT NOT NULL DEFAULT '',
        detalhe TEXT NOT NULL DEFAULT '', ip TEXT NOT NULL DEFAULT '')""")
    con.execute('CREATE INDEX IF NOT EXISTS idx_audit_momento ON auditoria(momento)')
    con.execute('CREATE INDEX IF NOT EXISTS idx_audit_usuario ON auditoria(usuario)')
    return con

def senha_hash(senha):
    salt=secrets.token_bytes(16)
    digest=hashlib.pbkdf2_hmac('sha256',senha.encode(),salt,310000)
    return salt.hex()+':'+digest.hex()

def senha_valida(senha,guardada):
    try:
        sal,hashhex=guardada.split(':')
        atual=hashlib.pbkdf2_hmac('sha256',senha.encode(),bytes.fromhex(sal),310000)
        return hmac.compare_digest(atual,bytes.fromhex(hashhex))
    except (ValueError,TypeError): return False

def registrar(usuario,atividade,cliente_id='',registro_id='',detalhe='',ip=''):
    with AUTH_LOCK:
        with auth_conn() as con:
            con.execute('INSERT INTO auditoria(momento,usuario,atividade,cliente_id,registro_id,detalhe,ip) VALUES(?,?,?,?,?,?,?)',
              (datetime.now().isoformat(timespec='seconds'),str(usuario)[:70],str(atividade)[:90],str(cliente_id)[:40],str(registro_id)[:40],str(detalhe)[:180],str(ip)[:50]))

def backup_local():
    from manutencao import create_backup
    arquivo=create_backup(BASE, BASE/'backups')
    # Uma cópia externa é opcional e só é considerada concluída após verificação.
    externo=config().get('BACKUP_DESTINATION')
    if externo:
        from manutencao import verify_backup
        destino=Path(externo);destino.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destino,delete=False) as f:
            temporario=Path(f.name)
        try:
            shutil.copyfile(arquivo,temporario)
            verify_backup(temporario)
            temporario.replace(destino/arquivo.name)
        finally:temporario.unlink(missing_ok=True)
    return arquivo.name

def criar_certificado():
    crt=BASE/'sonifarma_tls.crt';key=BASE/'sonifarma_tls.key'
    if crt.exists() and key.exists():return crt,key
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from datetime import timezone
        import ipaddress
    except ImportError:
        raise SystemExit('Instale cryptography no Python do servidor: python -m pip install cryptography')
    k=rsa.generate_private_key(public_exponent=65537,key_size=3072)
    nome=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Sonifarma Rede Interna')])
    now=datetime.now(timezone.utc)
    cert=(x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(k.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1))
          .not_valid_after(now+timedelta(days=365))
          .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('192.168.0.239')),
            x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
          .sign(k,hashes.SHA256()))
    key.write_bytes(k.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.TraditionalOpenSSL,serialization.NoEncryption()))
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return crt,key

# V20.5: nomes comerciais locais, sem modificar o ERP.
NOMES_DB = BASE / 'nomes_comerciais.sqlite3'
NOMES_LOCK = threading.RLock()
def nomes_conn():
    con=sqlite3.connect(str(NOMES_DB),timeout=15)
    con.execute('PRAGMA busy_timeout=15000')
    con.execute("""CREATE TABLE IF NOT EXISTS nomes_comerciais (
        chave TEXT PRIMARY KEY, nome TEXT NOT NULL, atualizado_em TEXT NOT NULL,
        usuario TEXT NOT NULL)""")
    con.execute("CREATE TABLE IF NOT EXISTS links_produtos (chave TEXT PRIMARY KEY, url TEXT NOT NULL, atualizado_em TEXT NOT NULL, usuario TEXT NOT NULL)")
    return con

def telefone_whatsapp_hash(valor):
    numero=re.sub(r'[^0-9]','',str(valor or ''))
    if numero.startswith('00'):numero=numero[2:]
    if len(numero) in (10,11):numero='55'+numero
    if not re.fullmatch(r'55[0-9]{10,11}',numero):return ''
    return hashlib.sha256(numero.encode('ascii')).hexdigest()


def whatsapp_indisponivel(cliente_id, numero):
    digest=telefone_whatsapp_hash(numero)
    if not digest:return False
    with TASK_LOCK:
        with closing(agenda_conn()) as con:
            with con:
                return con.execute('SELECT 1 FROM whatsapp_indisponivel WHERE cliente_id=? AND telefone_hash=?', (cliente_id,digest)).fetchone() is not None


def whatsapp_validado(cliente_id,numero):
    digest=telefone_whatsapp_hash(numero)
    if not digest or whatsapp_indisponivel(cliente_id,numero):return False
    with TASK_LOCK:
        with closing(agenda_conn()) as con:
            # A migração é aditiva; registros antigos sem número completo não validam outro celular.
            with con:
                return con.execute("SELECT 1 FROM whatsapp_registros WHERE cliente_id=? AND telefone_hash=? AND status='Enviado confirmado' LIMIT 1",
                                   (cliente_id,digest)).fetchone() is not None


def completar_datas_confirmacao(registros):
    pendentes={str(r['id']):r for r in registros if r['status']=='Enviado confirmado' and not r.get('confirmado_em')}
    if not pendentes or not AUTH_DB.exists():return registros
    # Apenas o evento explícito de confirmação pode recuperar a data de registros antigos.
    try:
        with AUTH_LOCK, closing(sqlite3.connect(AUTH_DB.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as con:
            ids=list(pendentes)
            for inicio in range(0,len(ids),500):
                lote=ids[inicio:inicio+500]
                sql="SELECT registro_id,cliente_id,momento,usuario FROM auditoria WHERE atividade='Envio confirmado' AND registro_id IN ("+','.join('?' for _ in lote)+') ORDER BY momento'
                for rid,cid,momento,usuario in con.execute(sql,lote):
                    registro=pendentes.get(str(rid))
                    if registro and str(registro['cliente_id'])==str(cid) and not registro.get('confirmado_em'):
                        registro['confirmado_em']=momento
                        registro['confirmado_por']=registro.get('confirmado_por') or usuario
    except sqlite3.Error:
        pass  # Sem auditoria disponível, a interface informa Data não registrada.
    return registros


def estado_reativacao_cliente(cliente_id):
    with TASK_LOCK, closing(agenda_conn()) as con, con:
        pref=con.execute('SELECT status,origem,data_consentimento FROM consentimento_comercial WHERE cliente_id=?',(cliente_id,)).fetchone()
        recente=con.execute("SELECT 1 FROM whatsapp_registros WHERE cliente_id=? AND status='Enviado confirmado' AND criado_em>=? LIMIT 1",(cliente_id,(datetime.now()-timedelta(days=3)).isoformat(timespec='seconds'))).fetchone()
        campos='id,cliente_id,ean,produto_descricao,previsao,telefone_final,status,criado_em,tipo_aviso,confirmado_em,confirmado_por'
        row=con.execute('SELECT '+campos+" FROM whatsapp_registros WHERE cliente_id=? AND tipo_aviso='reativacao' ORDER BY id DESC LIMIT 1",(cliente_id,)).fetchone()
    ultimo=dict(zip(campos.split(','),row)) if row else None
    if ultimo:completar_datas_confirmacao([ultimo])
    return {'consentimento':pref[0] if pref else 'nao_informado',
            'consentimento_origem':pref[1] if pref else '',
            'data_consentimento':pref[2] if pref else None,
            'contato_recente':bool(recente),'ultimo':ultimo}


def agenda_conn():
    con=sqlite3.connect(str(TASK_DB), timeout=5)
    con.execute("CREATE TABLE IF NOT EXISTS tarefas (id INTEGER PRIMARY KEY AUTOINCREMENT, cliente_id INTEGER NOT NULL, data_prevista TEXT NOT NULL, motivo TEXT NOT NULL CHECK(motivo IN ('Acompanhamento','Retorno solicitado','Conferir cadastro')), status TEXT NOT NULL DEFAULT 'Pendente' CHECK(status IN ('Pendente','Concluída')), criado_em TEXT NOT NULL)")
    con.execute("""CREATE TABLE IF NOT EXISTS whatsapp_registros (
        id INTEGER PRIMARY KEY AUTOINCREMENT, cliente_id INTEGER NOT NULL,
        ean TEXT NOT NULL, produto_descricao TEXT NOT NULL, previsao TEXT NOT NULL,
        telefone_final TEXT NOT NULL, status TEXT NOT NULL, criado_em TEXT NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS whatsapp_indisponivel (
        cliente_id INTEGER NOT NULL, telefone_hash TEXT NOT NULL,
        registrado_em TEXT NOT NULL, registrado_por TEXT NOT NULL,
        PRIMARY KEY(cliente_id, telefone_hash))""")
    # V20: migração aditiva, sem modificar dados antigos.
    cols={r[1] for r in con.execute("PRAGMA table_info(whatsapp_registros)")}
    for campo in ('confirmado_em','confirmado_por'):
        if campo not in cols:con.execute('ALTER TABLE whatsapp_registros ADD COLUMN '+campo+' TEXT')
    if 'telefone_hash' not in cols:
        con.execute("ALTER TABLE whatsapp_registros ADD COLUMN telefone_hash TEXT NOT NULL DEFAULT ''")
    if 'resultado' not in cols:
        con.execute("ALTER TABLE whatsapp_registros ADD COLUMN resultado TEXT NOT NULL DEFAULT 'Aguardando resposta'")
    if 'atualizado_em' not in cols:
        con.execute("ALTER TABLE whatsapp_registros ADD COLUMN atualizado_em TEXT")
    if 'tipo_aviso' not in cols:
        con.execute("ALTER TABLE whatsapp_registros ADD COLUMN tipo_aviso TEXT NOT NULL DEFAULT 'recompra'")
    con.execute("""CREATE TABLE IF NOT EXISTS consentimento_comercial (
        cliente_id INTEGER PRIMARY KEY, status TEXT NOT NULL CHECK(status IN ('autorizado','nao_informado','recusado')),
        origem TEXT NOT NULL DEFAULT '', data_consentimento TEXT, atualizado_em TEXT NOT NULL, usuario TEXT NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS consentimento_historico (
        id INTEGER PRIMARY KEY AUTOINCREMENT, cliente_id INTEGER NOT NULL, status_anterior TEXT NOT NULL,
        status_novo TEXT NOT NULL, origem TEXT NOT NULL, data_consentimento TEXT,
        atualizado_em TEXT NOT NULL, usuario TEXT NOT NULL)""")
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

# Reativação: todos os períodos e produtos do cliente, sem o limite do histórico recente.
SQL_PRODUTOS_COMPRADOS = """
WITH itens AS (
 SELECT v.id AS vendaid, v.datahorafechamento AS data_compra,
        em.produtoid, pr.descricao AS produto, iv.quantidade AS quantidade_original,
        COALESCE(dev.quantidade_devolvida,0) AS quantidade_devolvida,
        GREATEST(0::numeric,iv.quantidade-COALESCE(dev.quantidade_devolvida,0)) AS quantidade_liquida,
        ROUND(iv.valortotal*GREATEST(0::numeric,iv.quantidade-COALESCE(dev.quantidade_devolvida,0))/iv.quantidade,2) AS valor_estimado
 FROM public.venda v
 JOIN public.itemvenda iv ON iv.vendaid=v.id
 JOIN public.embalagem em ON em.id=iv.embalagemid
 JOIN public.produto pr ON pr.id=em.produtoid
 LEFT JOIN LATERAL (
   SELECT SUM(d.quantidade) AS quantidade_devolvida, COUNT(*) AS devolucoes
   FROM public.itemdevolucaovenda d WHERE d.itemvendaid=iv.id
 ) dev ON TRUE
 WHERE v.pessoaid=%s AND v.status IN ('F','D') AND v.datahorafechamento IS NOT NULL
   AND iv.quantidade>0
   AND (iv.status='F' OR (iv.status='D' AND dev.devolucoes>0))
   AND NOT EXISTS(SELECT 1 FROM public.estornovenda e WHERE e.vendaid=v.id)
), produtos AS (
 SELECT produtoid,produto,MIN(data_compra) AS primeira_compra,MAX(data_compra) AS ultima_compra,
        COUNT(DISTINCT vendaid) AS compras,
        SUM(quantidade_original) AS quantidade_original,
        SUM(quantidade_devolvida) AS quantidade_devolvida,
        SUM(quantidade_liquida) AS quantidade_liquida,
        ROUND(SUM(valor_estimado),2) AS valor_estimado
 FROM itens GROUP BY produtoid,produto
)
SELECT *,COUNT(*) OVER() AS total_produtos FROM produtos
ORDER BY ultima_compra DESC,produtoid ASC
LIMIT %s OFFSET %s
"""


def consultar_produtos_comprados(cliente_id,offset=0):
    tamanho=50
    rows=query(SQL_PRODUTOS_COMPRADOS,(cliente_id,tamanho+1,offset),limit=tamanho+1)
    total=int(rows[0]['total_produtos']) if rows else 0
    return {'produtos':[{k:v for k,v in r.items() if k!='total_produtos'} for r in rows[:tamanho]],
            'total':total,'offset':offset,'proximo_offset':offset+tamanho if len(rows)>tamanho else None}


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
def validar_intervalo(janela, inicio='', fim=''):
    if janela != 'intervalo':
        return
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', inicio) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', fim):
            raise ValueError()
        if date.fromisoformat(inicio) > date.fromisoformat(fim):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('Informe datas válidas: a data inicial deve ser anterior ou igual à final.')


def previsao_no_periodo(prevista, janela, hoje, inicio='', fim=''):
    if janela == 'intervalo':
        return date.fromisoformat(inicio) <= prevista <= date.fromisoformat(fim)
    if janela == 'atrasadas':
        return prevista < hoje
    if janela == 'misto3':
        return hoje-timedelta(days=3) <= prevista <= hoje+timedelta(days=3)
    if janela.startswith('anteriores'):
        return hoje-timedelta(days=int(janela[10:])) <= prevista < hoje
    if janela in ('7', '15', '30'):
        return hoje <= prevista <= hoje+timedelta(days=int(janela))
    return True


def calcular_recompras(cliente_id, janela='todos', pbms=None, classificacoes=None, inicio='', fim=''):
    validar_intervalo(janela, inicio, fim)
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
        if not previsao_no_periodo(prevista, janela, hoje, inicio, fim): continue
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
        if not previsao_no_periodo(previsto, f['janela'], hoje, f.get('inicio',''), f.get('fim','')): continue
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

# V20.5.5: reativação manual; última venda válida do cliente (não previsão por produto).
# Paginação por pessoa evita carregar toda a base em memória; valores são candidatos.
SQL_ANIVERSARIANTES = """
SELECT p.id AS pessoaid, p.nome, p.datanascimento,
       (SELECT MAX(v.datahorafechamento) FROM public.venda v
        WHERE v.pessoaid=p.id AND v.status IN ('F','D')
          AND v.datahorafechamento IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM public.estornovenda e WHERE e.vendaid=v.id)) AS ultima_compra
FROM public.pessoa p
WHERE EXTRACT(MONTH FROM p.datanascimento)=%s
  AND EXISTS (SELECT 1 FROM public.venda v WHERE v.pessoaid=p.id AND v.status IN ('F','D')
      AND v.datahorafechamento IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM public.estornovenda e WHERE e.vendaid=v.id))
ORDER BY EXTRACT(DAY FROM p.datanascimento), p.nome, p.id
LIMIT 51 OFFSET %s
"""

def consultar_aniversariantes(mes, cursor):
    rows=query(SQL_ANIVERSARIANTES,(mes,cursor),limit=51)
    ciclo=date(date.today().year,mes,1).isoformat()
    dados=[]
    with TASK_LOCK:
        with closing(agenda_conn()) as con:
            with con:
                for row in rows[:50]:
                    cid=int(row['pessoaid'])
                    pref=con.execute('SELECT status,origem FROM consentimento_comercial WHERE cliente_id=?',(cid,)).fetchone()
                    ultimo=con.execute("SELECT id,status,criado_em,confirmado_em,confirmado_por FROM whatsapp_registros WHERE cliente_id=? AND tipo_aviso='aniversario' AND previsao=? ORDER BY id DESC LIMIT 1",(cid,ciclo)).fetchone()
                    recente=con.execute("SELECT 1 FROM whatsapp_registros WHERE cliente_id=? AND status='Enviado confirmado' AND criado_em>=? LIMIT 1",(cid,(datetime.now()-timedelta(days=3)).isoformat(timespec='seconds'))).fetchone()
                    dados.append(dict(pessoaid=cid,nome=row['nome'],aniversario=str(row['datanascimento'])[:10],
                        ultima_compra=str(row['ultima_compra'])[:10] if row['ultima_compra'] else '',
                        campanha='aniversario',ciclo=ciclo,consentimento=pref[0] if pref else 'nao_informado',
                        consentimento_origem=pref[1] if pref else '',contato_recente=bool(recente),
                        ultimo=dict(zip(('id','status','criado_em','confirmado_em','confirmado_por'),ultimo)) if ultimo else None))
    return dict(clientes=dados,proximo_cursor=cursor+50 if len(rows)>50 else None)


SQL_REATIVACAO = """
WITH elegiveis AS (
 SELECT v.pessoaid, MAX(v.datahorafechamento) AS ultima_compra,
        COUNT(DISTINCT v.id) AS total_compras
 FROM public.venda v
 WHERE v.status IN ('F','D') AND v.datahorafechamento IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM public.estornovenda e WHERE e.vendaid=v.id)
 GROUP BY v.pessoaid
 HAVING MAX(v.datahorafechamento) < CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')
)
SELECT p.id AS pessoaid,p.nome,e.ultima_compra,e.total_compras,
       p.crmrecebernotificacao AS notificacao
FROM elegiveis e JOIN public.pessoa p ON p.id=e.pessoaid
WHERE (%s = 'todos' OR (%s = 'recorrentes' AND e.total_compras>=2) OR (%s = 'unicos' AND e.total_compras=1))
ORDER BY e.ultima_compra DESC, p.id ASC LIMIT 41 OFFSET %s
"""

def consultar_reativacao(cursor,dias,perfil):
    linhas=query(SQL_REATIVACAO,(dias,perfil,perfil,perfil,cursor),limit=41)
    mais=len(linhas)>40
    dados=[]
    hoje=date.today()
    with agenda_conn() as con:
        for r in linhas[:40]:
            pid=int(r['pessoaid'])
            ultimo=str(r['ultima_compra'])[:10]
            dias_sem=(hoje-date.fromisoformat(ultimo)).days
            registros=con.execute("SELECT status,criado_em FROM whatsapp_registros WHERE cliente_id=? AND criado_em>=? ORDER BY id DESC LIMIT 1",(pid,(datetime.now()-timedelta(days=3)).isoformat(timespec='seconds'))).fetchone()
            pref=con.execute('SELECT status,origem,data_consentimento FROM consentimento_comercial WHERE cliente_id=?',(pid,)).fetchone()
            notificacao=str(r.get('notificacao') or '').strip().upper()
            # Somente S é autorização registrada; confirmação humana continua obrigatória.
            dados.append({'pessoaid':pid,'nome':r['nome'],'ultima_compra':ultimo,'dias_sem_compra':dias_sem,
                'total_compras':int(r['total_compras']),'notificacao':notificacao or 'Não informado',
                'contato_recente':bool(registros), 'status_recente':registros[0] if registros else '', 'consentimento':pref[0] if pref else 'nao_informado', 'consentimento_origem':pref[1] if pref else ''})
    return {'clientes':dados,'proximo_cursor':cursor+40 if mais else None,
      'observacao':'Consulta por última venda válida, sem considerar devoluções parciais ou compras fora do ERP. Contato somente com autorização verificada; consulte o cadastro antes de enviar.'}

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
    def usuario_atual(self):
        c=SimpleCookie()
        try:c.load(self.headers.get('Cookie',''))
        except Exception:return None
        token=c.get('sonifarma_sessao')
        if not token:return None
        with AUTH_LOCK:
            sess=SESSIONS.get(token.value)
            if not sess:return None
            if time.time()>sess['expira']:
                SESSIONS.pop(token.value,None);return None
            with auth_conn() as con:
                row=con.execute('SELECT ativo,perfil FROM usuarios WHERE id=?',(sess['id'],)).fetchone()
            if not row or not row[0]:
                SESSIONS.pop(token.value,None);return None
            sess['perfil']=row[1]
            return sess

    def identidade(self):
        sess=self.usuario_atual()
        return sess['login'] if sess else 'anonimo'

    def origem_valida(self):
        origem=self.headers.get('Origin','')
        host=self.headers.get('Host','')
        return bool(origem) and origem==('https://' if getattr(self.server,'tls',False) else 'http://')+host

    def autorizado(self,master=False):
        u=self.usuario_atual()
        if not u:self.respond(401,{'erro':'Sessão encerrada. Faça login.'});return None
        if master and u['perfil']!='master':self.respond(403,{'erro':'Acesso exclusivo do MASTER'});return None
        return u

    def log_operacao(self,acao,d,registro=''):
        cid=d.get('cliente_id','') if isinstance(d,dict) else ''
        try:registrar(self.identidade(),acao,cid,registro,ip=self.client_address[0])
        except Exception:pass

    def cookie(self,token='',limpar=False):
        value=token if not limpar else ''
        self.send_header('Set-Cookie',f'sonifarma_sessao={value}; Path=/; HttpOnly; SameSite=Strict'+('; Secure' if getattr(self.server,'tls',False) else '')+('; Max-Age=0' if limpar else '; Max-Age=28800'))

    def do_AUTH_POST(self,d):
        rota=self.path
        if rota=='/api/auth/setup':
            if self.client_address[0] not in ('127.0.0.1','::1'):return self.respond(403,{'erro':'Primeiro cadastro somente no servidor local'})
            login='admin';senha=d.get('senha','')
            if not isinstance(senha,str) or len(senha)<6 or len(senha)>128: return self.respond(400,{'erro':'Senha inicial: mínimo de 6 caracteres.'})
            with AUTH_LOCK:
                with auth_conn() as con:
                    if con.execute('SELECT COUNT(*) FROM usuarios').fetchone()[0]:return self.respond(409,{'erro':'MASTER já cadastrado'})
                    con.execute('INSERT INTO usuarios(login,senha_hash,perfil,criado_em) VALUES(?,?,?,?)',(login,senha_hash(senha),'master',datetime.now().isoformat(timespec='seconds')))
            registrar('admin','Configuração inicial',ip=self.client_address[0]);return self.respond(200,{'ok':True})
        if rota=='/api/auth/login':
            ip=self.client_address[0]
            with AUTH_LOCK:
                recentes=[t for t in LOGIN_FAILURES.get(ip,[]) if time.time()-t<900]
                LOGIN_FAILURES[ip]=recentes
                if len(recentes)>=8:return self.respond(429,{'erro':'Muitas tentativas. Aguarde 15 minutos.'})
            login=str(d.get('login','')).strip().lower();senha=d.get('senha','')
            if not isinstance(senha,str) or len(senha)>128:return self.respond(400,{'erro':'Credenciais inválidas'})
            with AUTH_LOCK:
                with auth_conn() as con:
                    row=con.execute('SELECT id,senha_hash,perfil,ativo FROM usuarios WHERE login=?',(login,)).fetchone()
            if not row or not row[3] or not senha_valida(senha,row[1]):
                with AUTH_LOCK:LOGIN_FAILURES.setdefault(ip,[]).append(time.time())
                registrar(login or 'desconhecido','Login recusado',ip=self.client_address[0]);return self.respond(401,{'erro':'Usuário ou senha inválidos'})
            with AUTH_LOCK:LOGIN_FAILURES.pop(ip,None)
            token=secrets.token_urlsafe(40);csrf=secrets.token_urlsafe(30)
            with AUTH_LOCK:SESSIONS[token]={'id':row[0],'login':login,'perfil':row[2],'csrf':csrf,'expira':time.time()+SESSION_TTL}
            registrar(login,'Login realizado',ip=self.client_address[0]);return self.respond(200,{'ok':True,'login':login,'perfil':row[2],'csrf':csrf},cookie=token)
        u=self.autorizado(master=rota in ('/api/usuarios','/api/backup'))
        if not u:return
        if rota=='/api/auth/logout':
            cookie=SimpleCookie();cookie.load(self.headers.get('Cookie',''))
            if cookie.get('sonifarma_sessao'):
                with AUTH_LOCK:SESSIONS.pop(cookie['sonifarma_sessao'].value,None)
            registrar(u['login'],'Logout',ip=self.client_address[0]);return self.respond(200,{'ok':True},clear_cookie=True)
        if rota=='/api/usuarios':
            acao=d.get('acao');login=str(d.get('login','')).strip().lower()
            if not re.fullmatch('[a-z0-9_.-]{3,32}',login):return self.respond(400,{'erro':'Login inválido'})
            if login=='admin' and acao in ('bloquear','desbloquear','perfil'):return self.respond(403,{'erro':'MASTER protegido'})
            with AUTH_LOCK:
                with auth_conn() as con:
                    if acao=='criar':
                        senha=d.get('senha','');perfil=d.get('perfil','atendente')
                        if not isinstance(senha,str) or len(senha)<6 or perfil not in ('atendente','supervisor'):
                            return self.respond(400,{'erro':'Senha de 12+ caracteres e perfil válido obrigatórios'})
                        if con.execute('SELECT 1 FROM usuarios WHERE login=?',(login,)).fetchone():return self.respond(409,{'erro':'Usuário já existe'})
                        con.execute('INSERT INTO usuarios(login,senha_hash,perfil,criado_em) VALUES(?,?,?,?)',(login,senha_hash(senha),perfil,datetime.now().isoformat(timespec='seconds')))
                    elif acao=='senha':
                        senha=d.get('senha','')
                        if not isinstance(senha,str) or len(senha)<6:return self.respond(400,{'erro':'Senha mínima de 6 caracteres'})
                        cur=con.execute('UPDATE usuarios SET senha_hash=? WHERE login=?',(senha_hash(senha),login))
                        if not cur.rowcount:return self.respond(404,{'erro':'Usuário inexistente'})
                        with AUTH_LOCK:
                            for t,ss in list(SESSIONS.items()):
                                if ss['login']==login:SESSIONS.pop(t,None)
                    elif acao in ('bloquear','desbloquear'):
                        cur=con.execute('UPDATE usuarios SET ativo=? WHERE login=?',(int(acao=='desbloquear'),login))
                        if not cur.rowcount:return self.respond(404,{'erro':'Usuário inexistente'})
                    elif acao=='perfil':
                        perfil=d.get('perfil')
                        if perfil not in ('atendente','supervisor'):return self.respond(400,{'erro':'Perfil inválido'})
                        cur=con.execute('UPDATE usuarios SET perfil=? WHERE login=?',(perfil,login))
                        if not cur.rowcount:return self.respond(404,{'erro':'Usuário inexistente'})
                    else:return self.respond(400,{'erro':'Ação inválida'})
            registrar(u['login'],'Usuário '+acao,detalhe=login,ip=self.client_address[0]);return self.respond(200,{'ok':True})
        if rota=='/api/backup':
            with AUTH_LOCK:
                nome=backup_local()
            registrar(u['login'],'Backup criado',detalhe=nome,ip=self.client_address[0]);return self.respond(200,{'ok':True,'arquivo':nome})
        return self.respond(404,{'erro':'Rota não encontrada'})

    def end_headers(self):
        self.send_header('Cache-Control','no-store, private')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
        super().end_headers()
    def log_message(self, fmt, *args):
        # Não registra termos de pesquisa nem identificadores pessoais no log.
        if not getattr(self, 'path', '').startswith('/api/'):
            super().log_message(fmt, *args)
    def respond(self, status, payload, cookie=None, clear_cookie=False):
        raw=json.dumps(payload,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        if cookie is not None or clear_cookie:self.cookie(cookie or '',clear_cookie)
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def do_POST(self):
        if self.path not in ('/api/auth/setup','/api/auth/login','/api/auth/logout','/api/usuarios','/api/backup','/api/agenda','/api/posologia','/api/ajuste_previsao','/api/whatsapp-registros','/api/whatsapp-resultado','/api/whatsapp-confirmar','/api/whatsapp-sem-conta','/api/nome-comercial','/api/link-produto','/api/consentimento-comercial'): return self.respond(404,{'erro':'Rota inexistente'})
        origin=self.headers.get('Origin','')
        if not self.origem_valida():
            return self.respond(403,{'erro':'Origem não autorizada'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 4096: return self.respond(400,{'erro':'Tamanho inválido'})
            d=json.loads(self.rfile.read(length))
            if not isinstance(d,dict):raise ValueError()
            if self.path in ('/api/auth/setup','/api/auth/login'):
                return self.do_AUTH_POST(d)
            u=self.autorizado()
            if not u:return
            if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),u['csrf']):return self.respond(403,{'erro':'Token de segurança inválido'})
            if self.path in ('/api/auth/logout','/api/usuarios','/api/backup'):return self.do_AUTH_POST(d)
            if self.path=='/api/whatsapp-sem-conta':
                cid=d.get('cliente_id'); digest=telefone_whatsapp_hash(d.get('telefone',''))
                if not isinstance(cid,int) or isinstance(cid,bool) or not 0<cid<10**17 or not digest:
                    raise ValueError('Cliente ou telefone inválido')
                rid=d.get('registro_id')
                if rid is not None and (not isinstance(rid,int) or isinstance(rid,bool) or rid<=0):
                    raise ValueError('Registro inválido')
                agora=datetime.now().isoformat(timespec='seconds')
                with TASK_LOCK:
                    with closing(agenda_conn()) as con:
                        with con:
                            if rid is not None:
                                reg=con.execute('SELECT cliente_id,telefone_hash,status FROM whatsapp_registros WHERE id=?',(rid,)).fetchone()
                                if not reg or reg[:2]!=(cid,digest):
                                    return self.respond(409,{'erro':'Registro não corresponde ao cliente e telefone informados.'})
                                if reg[2]!='WhatsApp aberto':
                                    return self.respond(409,{'erro':'Este atendimento já foi finalizado.'})
                            con.execute("UPDATE whatsapp_registros SET status='Número sem WhatsApp',atualizado_em=? WHERE cliente_id=? AND telefone_hash=? AND status='WhatsApp aberto'",(agora,cid,digest))
                            con.execute('INSERT OR REPLACE INTO whatsapp_indisponivel VALUES(?,?,?,?)',(cid,digest,agora,u['login']))
                self.log_operacao('Número sem WhatsApp',{'cliente_id':cid},rid)
                return self.respond(200,{'ok':True})
            if self.path=='/api/consentimento-comercial':
                cid=d.get('cliente_id'); status=str(d.get('status','')).strip()
                origem=str(d.get('origem','')).strip()[:180]
                data_c=str(d.get('data_consentimento','')).strip()
                if not isinstance(cid,int) or isinstance(cid,bool) or not 0<cid<10**17 or status not in ('autorizado','nao_informado','recusado'):raise ValueError('Dados inválidos')
                if status=='autorizado':
                    if len(origem)<5 or not data_c:raise ValueError('Informe origem e data da autorização já concedida')
                    try: dt=date.fromisoformat(data_c)
                    except ValueError:raise ValueError('Data inválida')
                    if dt>date.today():raise ValueError('Data futura inválida')
                else: data_c=''
                if status=='recusado' and len(origem)<3:raise ValueError('Informe a origem do pedido de bloqueio')
                agora=datetime.now().isoformat(timespec='seconds')
                with TASK_LOCK:
                    with agenda_conn() as con:
                        antigo=con.execute('SELECT status FROM consentimento_comercial WHERE cliente_id=?',(cid,)).fetchone()
                        anterior=antigo[0] if antigo else 'nao_informado'
                        con.execute('INSERT OR REPLACE INTO consentimento_comercial(cliente_id,status,origem,data_consentimento,atualizado_em,usuario) VALUES(?,?,?,?,?,?)',(cid,status,origem,data_c,agora,u['login']))
                        con.execute('INSERT INTO consentimento_historico(cliente_id,status_anterior,status_novo,origem,data_consentimento,atualizado_em,usuario) VALUES(?,?,?,?,?,?,?)',(cid,anterior,status,origem,data_c,agora,u['login']))
                self.log_operacao('Preferência comercial atualizada',{'cliente_id':cid,'status':status,'origem':origem})
                return self.respond(200,{'ok':True,'status':status})
            if self.path=='/api/nome-comercial':
                chave=str(d.get('chave','')).strip()[:90]
                nome=str(d.get('nome','')).strip()[:150]
                if not chave or not re.fullmatch(r'[A-Za-z0-9:_\-]+',chave) or len(nome)<3:
                    raise ValueError('Nome comercial inválido')
                with NOMES_LOCK:
                    with nomes_conn() as con:
                        con.execute('INSERT OR REPLACE INTO nomes_comerciais(chave,nome,atualizado_em,usuario) VALUES(?,?,?,?)',
                            (chave,nome,datetime.now().isoformat(timespec='seconds'),self.identidade()))
                registrar(self.identidade(),'Nome comercial atualizado',detalhe=chave,ip=self.client_address[0])
                return self.respond(200,{'ok':True,'nome':nome})
            if self.path=='/api/link-produto':
                chave=str(d.get('chave','')).strip()[:90]
                url=str(d.get('url','')).strip()[:500]
                if not chave or not re.fullmatch(r'[A-Za-z0-9:_\-]+',chave):raise ValueError('Produto inválido')
                if url and not re.fullmatch(r'https://(?:www\.)?sonifarma\.com\.br/[A-Za-z0-9_./%\-]+(?:\?[A-Za-z0-9_=&%\-]+)?',url,re.I):
                    raise ValueError('Use um link HTTPS do site sonifarma.com.br')
                with NOMES_LOCK:
                    with nomes_conn() as con:
                        if url:con.execute('INSERT OR REPLACE INTO links_produtos(chave,url,atualizado_em,usuario) VALUES(?,?,?,?)',(chave,url,datetime.now().isoformat(timespec='seconds'),self.identidade()))
                        else:con.execute('DELETE FROM links_produtos WHERE chave=?',(chave,))
                registrar(self.identidade(),'Link do produto atualizado',detalhe=chave,ip=self.client_address[0])
                return self.respond(200,{'ok':True,'url':url})
            if self.path=='/api/whatsapp-resultado':
                rid=d.get('id'); resultado=d.get('resultado')
                permitidos=('Aguardando resposta','Interessado','Não precisa agora','Recompra realizada')
                if not isinstance(rid,int) or isinstance(rid,bool) or rid<=0 or resultado not in permitidos: raise ValueError()
                with TASK_LOCK:
                    with agenda_conn() as con:
                        cur=con.execute('UPDATE whatsapp_registros SET resultado=?,atualizado_em=? WHERE id=?',
                            (resultado,datetime.now().isoformat(timespec='seconds'),rid))
                        if cur.rowcount!=1:return self.respond(404,{'erro':'Registro não encontrado'})
                self.log_operacao('Resultado WhatsApp atualizado',d,rid)
                return self.respond(200,{'ok':True})
            if self.path=='/api/whatsapp-registros':
                cid=d.get('cliente_id'); status=d.get('status')
                if isinstance(cid,str) and cid.isdecimal(): cid=int(cid)
                if not isinstance(cid,int) or isinstance(cid,bool) or not 0<cid<10**17: raise ValueError()
                if status!='WhatsApp aberto': raise ValueError()
                ean=str(d.get('ean',''))[:18]; desc=str(d.get('produto_descricao',''))[:180]
                tipo_aviso=str(d.get('tipo_aviso','recompra'))
                if tipo_aviso not in ('recompra','popular_amanha','popular_hoje','popular_atrasado','reativacao','aniversario'): raise ValueError('Tipo de aviso inválido')
                # V20.5.9: recusa registrada bloqueia todo contato comercial,
                # inclusive recompra e avisos que possam conter dados sensíveis.
                with agenda_conn() as ccons:
                    consent=ccons.execute('SELECT status FROM consentimento_comercial WHERE cliente_id=?',(cid,)).fetchone()
                if consent and consent[0]=='recusado':
                    return self.respond(403,{'erro':'Cliente solicitou não receber mensagens. Contato bloqueado.'})
                if tipo_aviso in ('reativacao','aniversario'):
                    if not consent or consent[0]!='autorizado':return self.respond(403,{'erro':'Contato bloqueado: autorização comercial não registrada.'})
                    ean='ANIVERSARIO' if tipo_aviso=='aniversario' else 'REATIVACAO';desc='Aniversariantes do mês' if tipo_aviso=='aniversario' else 'Reativação de clientes'
                if tipo_aviso.startswith('popular_'):
                    ean='FARMACIA_POPULAR';desc='Farmácia Popular — aviso de retirada'
                previsao=str(d.get('previsao',''))[:12]; final=str(d.get('telefone_final',''))
                telefone=d.get('telefone','')
                digest=telefone_whatsapp_hash(telefone)
                if telefone and (not digest or re.sub(r'[^0-9]','',str(telefone))[-4:]!=final):raise ValueError('Telefone inválido')
                if re.fullmatch(r'\d{2}/\d{2}/\d{4}',previsao):
                    previsao=datetime.strptime(previsao,'%d/%m/%Y').date().isoformat()
                if not final.isdecimal() or len(final)!=4 or not desc: raise ValueError()
                # V20.5: uma tentativa por cliente/data prevista/tipo de aviso/dia;
                # permite contato em dias posteriores e lembretes distintos de Farmácia Popular.
                if not previsao or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',previsao):
                    raise ValueError('Previsão inválida')
                if tipo_aviso=='aniversario':
                    try: previsao=date.fromisoformat(previsao).replace(day=1).isoformat()
                    except ValueError:raise ValueError('Mês de aniversário inválido')
                    if date.fromisoformat(previsao).year!=date.today().year:raise ValueError('Ano de aniversário inválido')
                with TASK_LOCK:
                    with agenda_conn() as con:
                        if tipo_aviso=='aniversario' and con.execute("SELECT 1 FROM whatsapp_registros WHERE cliente_id=? AND tipo_aviso='aniversario' AND previsao=? AND status='Enviado confirmado' LIMIT 1",(cid,previsao)).fetchone():
                            return self.respond(409,{'erro':'Mensagem de aniversário já confirmada neste mês e ano.'})
                        existente=con.execute("SELECT id FROM whatsapp_registros WHERE cliente_id=? AND ean=? AND previsao=? AND tipo_aviso=? AND substr(criado_em,1,10)=? LIMIT 1",
                            (cid,ean,previsao,tipo_aviso,date.today().isoformat())).fetchone()
                        if existente:
                            return self.respond(409,{'erro':'Já existe contato deste tipo para este cliente e data hoje. Consulte o Pós-venda.'})
                        if tipo_aviso=='recompra':
                            recente=con.execute("SELECT id,criado_em FROM whatsapp_registros WHERE cliente_id=? AND ean=? AND tipo_aviso='recompra' AND status='Enviado confirmado' AND criado_em>=? ORDER BY id DESC LIMIT 1",(cid,ean,(datetime.now()-timedelta(days=3)).isoformat(timespec='seconds'))).fetchone()
                            if recente:return self.respond(409,{'erro':'Recompra já confirmada nos últimos 3 dias ('+str(recente[1])+'). Novo contato bloqueado.'})
                        if tipo_aviso in ('reativacao','aniversario'):
                            recente=con.execute("SELECT id FROM whatsapp_registros WHERE cliente_id=? AND status='Enviado confirmado' AND criado_em>=? LIMIT 1",(cid,(datetime.now()-timedelta(days=3)).isoformat(timespec='seconds'))).fetchone()
                            if recente:return self.respond(409,{'erro':'Cliente com envio confirmado nos últimos 3 dias. Contato bloqueado.'})
                        cur=con.execute('INSERT INTO whatsapp_registros(cliente_id,ean,produto_descricao,previsao,telefone_final,status,criado_em,tipo_aviso,telefone_hash) VALUES(?,?,?,?,?,?,?,?,?)',
                            (cid,ean,desc,previsao,final,status,datetime.now().isoformat(timespec='seconds'),tipo_aviso,digest))
                        registro_id=cur.lastrowid
                registrar(self.identidade(),'WhatsApp aberto',cid,registro_id,str(d.get('cliente_nome',''))[:100],self.client_address[0])
                return self.respond(200,{'ok':True,'id':registro_id,'status':status})
            if self.path=='/api/whatsapp-confirmar':
                rid=d.get('id')
                if not isinstance(rid,int) or isinstance(rid,bool) or rid<=0: raise ValueError('Registro inválido')
                with TASK_LOCK:
                    with agenda_conn() as con:
                        reg=con.execute('SELECT cliente_id,tipo_aviso FROM whatsapp_registros WHERE id=?',(rid,)).fetchone()
                        if reg:
                            consent=con.execute('SELECT status FROM consentimento_comercial WHERE cliente_id=?',(reg[0],)).fetchone()
                            if consent and consent[0]=='recusado':return self.respond(403,{'erro':'Cliente solicitou não receber mensagens. Confirmação bloqueada.'})
                            if reg[1] in ('reativacao','aniversario') and (not consent or consent[0]!='autorizado'):
                                return self.respond(403,{'erro':'Autorização comercial ausente.'})
                        if reg and reg[1]=='aniversario':
                            ciclo=con.execute('SELECT previsao FROM whatsapp_registros WHERE id=?',(rid,)).fetchone()[0]
                            if con.execute("SELECT 1 FROM whatsapp_registros WHERE cliente_id=? AND tipo_aviso='aniversario' AND previsao=? AND status='Enviado confirmado' AND id!=?",(reg[0],ciclo,rid)).fetchone():
                                return self.respond(409,{'erro':'Aniversário já confirmado neste mês e ano.'})
                        agora=datetime.now().isoformat(timespec='seconds')
                        cur=con.execute("UPDATE whatsapp_registros SET status='Enviado confirmado',atualizado_em=?,confirmado_em=?,confirmado_por=? WHERE id=? AND status='WhatsApp aberto'",(agora,agora,u['login'],rid))
                        if cur.rowcount!=1:return self.respond(409,{'erro':'Registro não encontrado ou já confirmado'})
                        con.execute('DELETE FROM whatsapp_indisponivel WHERE (cliente_id,telefone_hash) IN (SELECT cliente_id,telefone_hash FROM whatsapp_registros WHERE id=?)',(rid,))
                with TASK_LOCK:
                    with agenda_conn() as con:cliente=con.execute('SELECT cliente_id FROM whatsapp_registros WHERE id=?',(rid,)).fetchone()
                self.log_operacao('Envio confirmado',{'cliente_id': cliente[0] if cliente else ''},rid)
                return self.respond(200,{'ok':True})
            if self.path=='/api/ajuste_previsao':
                self.log_operacao('Ajuste de previsão',d)
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
                self.log_operacao('Posologia confirmada',d)
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
                self.log_operacao('Agenda criada',d)
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
                self.log_operacao('Agenda concluída',d)
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
        if parsed.path=='/api/auth/status':
            with AUTH_LOCK:
                with auth_conn() as con:inicializado=bool(con.execute('SELECT COUNT(*) FROM usuarios').fetchone()[0])
            u=self.usuario_atual()
            return self.respond(200,{'inicializado':inicializado,'autenticado':bool(u),'login':u['login'] if u else None,'perfil':u['perfil'] if u else None,'csrf':u['csrf'] if u else None})
        if parsed.path.startswith('/api/'):
            u=self.autorizado(master=parsed.path in ('/api/usuarios','/api/auditoria'))
            if not u:return
            if parsed.path=='/api/usuarios':
                with AUTH_LOCK:
                    with auth_conn() as con:rows=con.execute('SELECT login,perfil,ativo,criado_em FROM usuarios ORDER BY login').fetchall()
                return self.respond(200,{'usuarios':[dict(zip(('login','perfil','ativo','criado_em'),r)) for r in rows]})
            if parsed.path=='/api/auditoria':
                args=parse_qs(parsed.query);cond=[];vals=[]
                for key,col in (('data_inicio','momento'),('data_fim','momento'),('hora_inicio','momento'),('hora_fim','momento'),('usuario','usuario'),('atividade','atividade'),('cliente_id','cliente_id')):
                    v=args.get(key,[''])[0].strip()
                    if not v:continue
                    if len(v)>60:return self.respond(400,{'erro':'Filtro inválido'})
                    if key in ('data_inicio','data_fim'):
                        try:date.fromisoformat(v)
                        except ValueError:return self.respond(400,{'erro':'Data inválida'})
                        cond.append(col+(' >= ?' if key=='data_inicio' else ' <= ?'));vals.append(v+('T00:00:00' if key=='data_inicio' else 'T23:59:59'))
                    elif key in ('hora_inicio','hora_fim'):
                        if not re.fullmatch(r'\d{2}:\d{2}',v) or v>'23:59':return self.respond(400,{'erro':'Hora inválida'})
                        cond.append('substr(momento,12,5)'+(' >= ?' if key=='hora_inicio' else ' <= ?'));vals.append(v)
                    else:cond.append(col+' = ?');vals.append(v)
                where=(' WHERE '+' AND '.join(cond)) if cond else ''
                with AUTH_LOCK:
                    with auth_conn() as con:
                        rows=con.execute('SELECT momento,usuario,atividade,cliente_id,registro_id,detalhe,ip FROM auditoria'+where+' ORDER BY id DESC LIMIT 500',vals).fetchall()
                return self.respond(200,{'registros':[dict(zip(('momento','usuario','atividade','cliente_id','registro_id','detalhe','ip'),r)) for r in rows]})
            # Audita consultas sensíveis, sem armazenar nomes ou termos pesquisados.
            if parsed.path in ('/api/ficha-cadastro','/api/historico','/api/recompra','/api/produtos-comprados'):
                args0=parse_qs(parsed.query)
                try:registrar(u['login'],'Consulta '+parsed.path.rsplit('/',1)[-1],args0.get('id',args0.get('cliente_id',['']))[0],ip=self.client_address[0])
                except Exception:pass
        if not parsed.path.startswith('/api/'):
            if parsed.path=='/modulo1_resultado_agregado.json' and not self.autorizado():return
            if parsed.path not in ('/','/index.html','/sonifarma-logo.png','/modulo1_resultado_agregado.json'):
                self.send_error(404)
                return
            return super().do_GET()
        if parsed.path=='/api/aniversariantes':
            if not DB:return self.respond(503,{'erro':'ERP não conectado. Reinicie com --erp.'})
            args=parse_qs(parsed.query)
            try:
                mes=int(args.get('mes',[str(date.today().month)])[0]);cursor=int(args.get('cursor',['0'])[0])
                if not 1<=mes<=12 or not 0<=cursor<=100000:raise ValueError()
                resultado=consultar_aniversariantes(mes,cursor)
                registrar(u['login'],'Consulta aniversariantes',detalhe=f'mes={mes};cursor={cursor}',ip=self.client_address[0])
                return self.respond(200,resultado)
            except ValueError:return self.respond(400,{'erro':'Mês ou posição inválidos.'})
            except Exception as e:
                print('Falha aniversariantes:',repr(e))
                return self.respond(503,{'erro':'Consulta de aniversariantes indisponível. Confira os logs.'})
        if parsed.path=='/api/reativacao':
            args=parse_qs(parsed.query)
            try:
                dias=int(args.get('dias',['40'])[0]);cursor=int(args.get('cursor',['0'])[0]);perfil=args.get('perfil',['recorrentes'])[0]
                if dias not in (40,60,90,180) or cursor<0 or perfil not in ('recorrentes','unicos','todos'):raise ValueError()
                resultado=consultar_reativacao(cursor,dias,perfil)
                registrar(u['login'],'Consulta reativação',detalhe=f'dias={dias};perfil={perfil};cursor={cursor}',ip=self.client_address[0])
                return self.respond(200,resultado)
            except ValueError:return self.respond(400,{'erro':'Filtros inválidos'})
            except Exception as e:
                print('Falha reativação:',repr(e))
                return self.respond(503,{'erro':'Consulta de reativação indisponível. Verifique o log do servidor.'})
        if parsed.path=='/api/nome-comercial':
            chave=parse_qs(parsed.query).get('chave',[''])[0][:90]
            if not chave or not re.fullmatch(r'[A-Za-z0-9:_\-]+',chave):return self.respond(400,{'erro':'Identificador inválido'})
            with NOMES_LOCK:
                with nomes_conn() as con:
                    row=con.execute('SELECT nome FROM nomes_comerciais WHERE chave=?',(chave,)).fetchone()
            return self.respond(200,{'nome':row[0] if row else ''})
        if parsed.path=='/api/link-produto':
            chave=parse_qs(parsed.query).get('chave',[''])[0][:90]
            if not chave or not re.fullmatch(r'[A-Za-z0-9:_\-]+',chave):return self.respond(400,{'erro':'Identificador inválido'})
            with NOMES_LOCK:
                with nomes_conn() as con:
                    row=con.execute('SELECT url FROM links_produtos WHERE chave=?',(chave,)).fetchone()
            return self.respond(200,{'url':row[0] if row else ''})
        if parsed.path=='/api/reativacao-contato':
            cid=parse_qs(parsed.query).get('cliente_id',[''])[0]
            if not re.fullmatch(r'[1-9][0-9]{0,16}',cid):
                return self.respond(400,{'erro':'Cliente inválido'})
            return self.respond(200,estado_reativacao_cliente(int(cid)))
        if parsed.path=='/api/whatsapp-validacao':
            filtros=parse_qs(parsed.query)
            cid=filtros.get('cliente_id',[''])[0]
            numero=filtros.get('numero',[''])[0]
            if not cid.isdecimal() or not 0<int(cid)<10**17 or not telefone_whatsapp_hash(numero):
                return self.respond(400,{'erro':'Cliente ou telefone inválido'})
            return self.respond(200,{'validado':whatsapp_validado(int(cid),numero),'sem_whatsapp':whatsapp_indisponivel(int(cid),numero)})
        if parsed.path=='/api/whatsapp-registros':
            filtros=parse_qs(parsed.query);cid=filtros.get('cliente_id',[None])[0]
            if cid is not None and (not cid.isdecimal() or not 0<int(cid)<10**17): return self.respond(400,{'erro':'Cliente inválido'})
            with TASK_LOCK:
                with agenda_conn() as con:
                    colunas="id,cliente_id,ean,produto_descricao,previsao,telefone_final,CASE WHEN status='WhatsApp aberto' AND EXISTS (SELECT 1 FROM whatsapp_indisponivel wi WHERE wi.cliente_id=wr.cliente_id AND wi.telefone_hash=wr.telefone_hash AND wi.registrado_em>=wr.criado_em) THEN 'Número sem WhatsApp' ELSE status END,criado_em,resultado,atualizado_em,tipo_aviso,(telefone_hash!='' AND status='Enviado confirmado'),confirmado_em,confirmado_por"
                    if filtros.get('resumo',[''])[0]=='1':
                        if cid is None:return self.respond(400,{'erro':'Informe o cliente'})
                        rows=con.execute('SELECT '+colunas+" FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY ean,CASE WHEN ean='' THEN produto_descricao ELSE '' END,previsao,tipo_aviso ORDER BY id DESC) AS ordem FROM whatsapp_registros WHERE cliente_id=?) AS wr WHERE ordem=1 ORDER BY id DESC",(int(cid),)).fetchall()
                    elif cid is None: rows=con.execute('SELECT '+colunas+' FROM whatsapp_registros AS wr ORDER BY id DESC LIMIT 200').fetchall()
                    else: rows=con.execute('SELECT '+colunas+' FROM whatsapp_registros AS wr WHERE cliente_id=? ORDER BY id DESC LIMIT 200',(int(cid),)).fetchall()
            registros=[dict(zip(('id','cliente_id','ean','produto_descricao','previsao','telefone_final','status','criado_em','resultado','atualizado_em','tipo_aviso','whatsapp_validado','confirmado_em','confirmado_por'),r)) for r in rows]
            return self.respond(200,{'registros':completar_datas_confirmacao(registros)})
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
                  'inicio':'','fim':'','janela':'misto3','nome':'','uso':'todos','ordem':'data_misto','produto':'',
                  'tipo':'','status':'','mes':'0','dia':'0','notificacao':'todos','cursor':'0'}.items()}
                if (f['janela'] not in ('misto3','7','15','30','anteriores7','anteriores15','anteriores30','atrasadas','intervalo') or
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
                try: validar_intervalo(f['janela'], f['inicio'], f['fim'])
                except ValueError as e: return self.respond(400,{'erro':str(e)})
                raw_ids=args.get('embalagens',[''])[0].strip()
                if raw_ids and not all(re.fullmatch(r'[1-9][0-9]{0,17}', item) for item in raw_ids.split(',')):
                    return self.respond(400,{'erro':'Seleção de embalagens inválida.'})
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
                if not re.fullmatch(r'[1-9][0-9]{0,17}',ident) or janela not in ('todos','misto3','7','15','30','anteriores7','anteriores15','anteriores30','atrasadas','sem_previsao','intervalo'):
                    return self.respond(400,{'erro':'Filtro ou cliente inválido.'})
                pbm_raw=args.get('pbms',[''])[0].strip()
                if pbm_raw and (len(pbm_raw)>1500 or not re.fullmatch(r'[1-9][0-9]{0,17}(?:,[1-9][0-9]{0,17}){0,99}',pbm_raw)):
                    return self.respond(400,{'erro':'Seleção PBM inválida (máximo 100).'})
                pbms=[int(x) for x in pbm_raw.split(',')] if pbm_raw else []
                try: classificacoes=ids_classificacao_query(args)
                except ValueError as e: return self.respond(400,{'erro':str(e)})
                inicio=args.get('inicio',[''])[0]; fim=args.get('fim',[''])[0]
                try: validar_intervalo(janela, inicio, fim)
                except ValueError as e: return self.respond(400,{'erro':str(e)})
                previsoes,limite=calcular_recompras(int(ident),janela,pbms,classificacoes,inicio,fim)
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
            if parsed.path=='/api/produtos-comprados':
                ident=args.get('id',[''])[0]
                offset=args.get('offset',['0'])[0]
                if not re.fullmatch(r'[1-9][0-9]{0,17}',ident) or not re.fullmatch(r'[0-9]{1,9}',offset):
                    return self.respond(400,{'erro':'Cliente ou página inválida.'})
                return self.respond(200,consultar_produtos_comprados(int(ident),int(offset)))
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
    parser.add_argument('--lan',action='store_true',help='Ativar HTTPS na rede interna')
    a=parser.parse_args()
    if a.erp:
        import psycopg2
        cfg=config()
        senha=os.environ.get('SONIFARMA_PG_PASSWORD') or getpass.getpass('Senha PostgreSQL (não será salva): ')
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
    with auth_conn(): pass
    with nomes_conn(): pass
    def rotina_backup():
        while True:
            try:
                with AUTH_LOCK:backup_local()
                arquivos=sorted((BASE/'backups').glob('sonifarma_backup_*.zip'),reverse=True)
                for antigo in arquivos[30:]:antigo.unlink(missing_ok=True)
            except Exception as e:print('Aviso: backup automático falhou:',type(e).__name__)
            time.sleep(24*3600)
    threading.Thread(target=rotina_backup,daemon=True).start()
    try:
        server=ThreadingHTTPServer(('192.168.0.239' if a.lan else '127.0.0.1',8765),Handler)
    except OSError as exc:
        raise SystemExit('Nao foi possivel abrir 192.168.0.239:8765. Verifique IP e processo antigo na porta 8765. Detalhe: '+str(exc)) from exc
    server.tls=bool(a.lan)
    if a.lan:
        crt,key=criar_certificado()
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version=ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(str(crt),str(key))
        server.socket=ctx.wrap_socket(server.socket,server_side=True)
    endereco=('https://192.168.0.239:8765' if a.lan else 'http://127.0.0.1:8765')
    print('Painel unificado:',endereco)
    print('ERP:', 'conectado em modo leitura' if DB else 'desconectado (somente dashboard)')
    print('Ctrl+C para encerrar.')
    webbrowser.open(endereco)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__=='__main__': main()
