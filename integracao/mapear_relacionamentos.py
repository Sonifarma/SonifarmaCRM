import os, json, getpass, traceback
from pathlib import Path
from datetime import datetime
import psycopg2

ROOT=Path(__file__).resolve().parent
TABLES=['pessoa','contato','cliente','consumidor','venda','itemvenda','produto','embalagem','pessoaliberacaotermolgpd','estornovenda','devolucaovenda','itemdevolucaovenda','unidadenegocio']

def env_file(path):
    result={}
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line=line.strip()
            if line and not line.startswith('#') and '=' in line:
                k,v=line.split('=',1)
                result[k.strip()]=v.strip().strip('"').strip("'")
    return result

def main():
    cfg=env_file(ROOT/'.env')
    password=getpass.getpass('Senha PostgreSQL (nao exibida): ')
    with psycopg2.connect(host=cfg.get('PGHOST','192.168.0.250'),port=int(cfg.get('PGPORT','5432')),dbname=cfg.get('PGDATABASE','sonifarma_loja01'),user=cfg.get('PGUSER','leitura_03012024'),password=password,connect_timeout=7,sslmode=cfg.get('PGSSLMODE','prefer'),application_name='sonifarma_mapeamento') as conn:
        conn.set_session(readonly=True,autocommit=False)
        with conn.cursor() as cur:
            cur.execute('SET LOCAL statement_timeout = 10000')
            cur.execute('SELECT current_database(), current_user, current_setting(%s), version()',('transaction_read_only',))
            database,user,readonly,version=cur.fetchone()
            cur.execute('''SELECT table_name,column_name,data_type,udt_name,is_nullable,ordinal_position
                           FROM information_schema.columns
                           WHERE table_schema = %s AND table_name = ANY(%s)
                           ORDER BY table_name,ordinal_position''',('public',TABLES))
            columns=cur.fetchall()
            cur.execute('''SELECT tc.table_name,kcu.column_name,tc.constraint_type
                           FROM information_schema.table_constraints tc
                           JOIN information_schema.key_column_usage kcu
                             ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
                           WHERE tc.table_schema=%s AND tc.table_name=ANY(%s)
                             AND tc.constraint_type IN ('PRIMARY KEY','UNIQUE')
                           ORDER BY tc.table_name,tc.constraint_type,kcu.ordinal_position''',('public',TABLES))
            keys=cur.fetchall()
            cur.execute('''SELECT tc.table_name,kcu.column_name,ccu.table_name,ccu.column_name
                           FROM information_schema.table_constraints tc
                           JOIN information_schema.key_column_usage kcu
                             ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
                           JOIN information_schema.constraint_column_usage ccu
                             ON ccu.constraint_name=tc.constraint_name AND ccu.table_schema=tc.table_schema
                           WHERE tc.table_schema=%s AND tc.constraint_type='FOREIGN KEY'
                             AND tc.table_name=ANY(%s)
                           ORDER BY tc.table_name,kcu.column_name''',('public',TABLES))
            fks=cur.fetchall()
        conn.rollback()
    report={'gerado_em':datetime.now().isoformat(timespec='seconds'),'banco':database,'usuario':user,'transacao_somente_leitura':readonly,'versao_postgresql':version,
            'colunas':[{'tabela':t,'coluna':c,'tipo':dt,'tipo_interno':udt,'permite_nulo':nullable,'ordem':ordem} for t,c,dt,udt,nullable,ordem in columns],
            'chaves':[{'tabela':t,'coluna':c,'tipo':typ} for t,c,typ in keys],
            'chaves_estrangeiras':[{'tabela':t,'coluna':c,'tabela_referenciada':rt,'coluna_referenciada':rc} for t,c,rt,rc in fks]}
    out=ROOT/'mapeamento_relacionamentos.json'
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Conexao OK. Somente leitura:',readonly)
    print('Colunas:',len(columns),'Chaves:',len(keys),'FKs:',len(fks))
    print('Relatorio:',out)

if __name__=='__main__':
    try: main()
    except Exception: traceback.print_exc()
