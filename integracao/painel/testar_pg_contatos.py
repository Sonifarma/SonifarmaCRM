"""Diagnostico somente leitura do esquema do ERP; nao imprime dados de pacientes."""
import getpass
import os
from pathlib import Path
import psycopg2

base=Path(__file__).resolve().parent
cfg={}
for path in (base.parent/'.env',base/'.env'):
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k,v=line.split('=',1)
                cfg[k.strip()]=v.strip().strip('"').strip("'")
senha=getpass.getpass('Senha PostgreSQL (nao sera salva): ')
if not senha: raise SystemExit('Senha vazia')
try:
    with psycopg2.connect(host=cfg.get('PGHOST','192.168.0.250'),port=int(cfg.get('PGPORT','5432')),
                          dbname=cfg.get('PGDATABASE','sonifarma_loja01'),
                          user=cfg.get('PGUSER','leitura_03012024'),password=senha,
                          sslmode=cfg.get('PGSSLMODE','prefer'),connect_timeout=6,
                          application_name='sonifarma_diagnostico_contatos_readonly') as con:
        con.set_session(readonly=True)
        with con.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '10000ms'")
            cur.execute('SHOW transaction_read_only')
            print('Conexao somente leitura:',cur.fetchone()[0])
            cur.execute("""SELECT table_name,column_name,data_type FROM information_schema.columns
                           WHERE table_schema='public' AND table_name IN ('pessoa','contato')
                           ORDER BY table_name,ordinal_position""")
            for tab,col,typ in cur.fetchall(): print(f'{tab}.{col}: {typ}')
        con.rollback()
except psycopg2.Error as e:
    print('Falha de conexao/SQL:',e.pgcode or type(e).__name__)
    print('Confira acesso de rede, credenciais e estrutura da tabela.')
