import json, os, getpass, traceback
from pathlib import Path
import psycopg2
from psycopg2 import sql
ROOT=Path(__file__).resolve().parent

def load_env():
    p=ROOT/'.env'; out={}
    if p.exists():
        for line in p.read_text(encoding='utf-8').splitlines():
            line=line.strip()
            if line and not line.startswith('#') and '=' in line:
                k,v=line.split('=',1);out[k.strip()]=v.strip().strip('"').strip("'")
    return out

def main():
    cfg=load_env()
    password=getpass.getpass('Senha PostgreSQL (oculta): ')
    conn=psycopg2.connect(host=cfg.get('PGHOST','192.168.0.250'),port=int(cfg.get('PGPORT','5432')),dbname=cfg.get('PGDATABASE','sonifarma_loja01'),user=cfg.get('PGUSER','leitura_03012024'),password=password,connect_timeout=7,sslmode=cfg.get('PGSSLMODE','prefer'))
    conn.set_session(readonly=True,autocommit=False)
    targets=['pessoa','contato','cliente','consumidor','venda','itemvenda','produto','embalagem','pessoaliberacaotermolgpd','estornovenda','devolucaovenda','itemdevolucaovenda','unidadenegocio']
    report={'observacao':'Somente metadados e contagens agregadas. Nenhum registro pessoal exportado.','tabelas':{},'chaves_estrangeiras':[]}
    try:
        with conn.cursor() as cur:
            cur.execute('SET LOCAL statement_timeout = 8000')
            cur.execute('''SELECT tc.table_name,kcu.column_name,ccu.table_name,ccu.column_name
              FROM information_schema.table_constraints tc
              JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
              JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name=tc.constraint_name AND ccu.table_schema=tc.table_schema
              WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='public'
              AND (tc.table_name=ANY(%s) OR ccu.table_name=ANY(%s))''',(targets,targets))
            report['chaves_estrangeiras']=[dict(zip(['origem_tabela','origem_coluna','destino_tabela','destino_coluna'],r)) for r in cur.fetchall()]
            for table in targets:
                cur.execute('SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s)',('public',table))
                if not cur.fetchone()[0]:continue
                entry={'colunas_status_data':[],'total_registros':None,'status_distribuicao':None}
                cur.execute('SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s',('public',table))
                cols={r[0] for r in cur.fetchall()}
                entry['colunas_status_data']=[c for c in cols if 'status' in c or 'datahora' in c or c in ('data','tipo','origem')]
                # Counts may be expensive; do not scan production tables in this diagnostic.
                report['tabelas'][table]=entry
        (ROOT/'diagnostico_posvenda.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print('Diagnostico concluido. Arquivo:',ROOT/'diagnostico_posvenda.json')
    finally:
        conn.rollback();conn.close()
if __name__=='__main__':
    try:main()
    except Exception as e:print('Falha:',type(e).__name__,str(e)[:300]);raise SystemExit(1)
