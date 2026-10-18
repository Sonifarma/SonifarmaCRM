import os, json, getpass, sys, traceback
from pathlib import Path
from datetime import datetime
import psycopg2
from psycopg2 import OperationalError

ROOT = Path(__file__).resolve().parent

def load_env(path):
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out

def main():
    config = load_env(ROOT / '.env')
    host = config.get('PGHOST', '192.168.0.250')
    port = int(config.get('PGPORT', '5432'))
    db = config.get('PGDATABASE', 'sonifarma_loja01')
    user = config.get('PGUSER', 'leitura_03012024')
    sslmode = config.get('PGSSLMODE', 'prefer')
    password = getpass.getpass('Senha do PostgreSQL (nao sera exibida): ')
    conn = None
    try:
        conn = psycopg2.connect(host=host, port=port, dbname=db, user=user,
                                password=password, connect_timeout=7,
                                sslmode=sslmode, application_name='sonifarma_diagnostico')
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor() as cur:
            cur.execute('SET LOCAL statement_timeout = 10000')
            cur.execute('SELECT current_database(), current_user, current_setting(%s), version()',
                        ('transaction_read_only',))
            database, username, readonly, version = cur.fetchone()
            cur.execute('''SELECT table_schema, table_name
                           FROM information_schema.tables
                           WHERE table_type=%s AND table_schema NOT IN (%s,%s)
                           ORDER BY table_schema,table_name''',
                        ('BASE TABLE','pg_catalog','information_schema'))
            tables = cur.fetchall()
            cur.execute('''SELECT table_schema, table_name, column_name, data_type
                           FROM information_schema.columns
                           WHERE table_schema NOT IN (%s,%s)
                           ORDER BY table_schema,table_name,ordinal_position''',
                        ('pg_catalog','information_schema'))
            cols = cur.fetchall()
            cur.execute('''SELECT table_schema,table_name,
                           has_table_privilege(current_user, format('%%I.%%I',table_schema,table_name), 'INSERT') OR
                           has_table_privilege(current_user, format('%%I.%%I',table_schema,table_name), 'UPDATE') OR
                           has_table_privilege(current_user, format('%%I.%%I',table_schema,table_name), 'DELETE') OR
                           has_table_privilege(current_user, format('%%I.%%I',table_schema,table_name), 'TRUNCATE')
                           FROM information_schema.tables
                           WHERE table_type='BASE TABLE' AND table_schema NOT IN (%s,%s)
                           ORDER BY table_schema,table_name''',
                        ('pg_catalog','information_schema'))
            permissions = cur.fetchall()
        conn.rollback()
        report = {
            'gerado_em': datetime.now().isoformat(timespec='seconds'),
            'banco': database, 'usuario': username,
            'transacao_somente_leitura': readonly,
            'versao_postgresql': version,
            'tabelas': [{'schema': s, 'tabela': t} for s,t in tables],
            'colunas': [{'schema': s, 'tabela': t, 'coluna': c, 'tipo': d} for s,t,c,d in cols],
            'permissoes_escrita': [{'schema': s, 'tabela': t, 'pode_escrever': p} for s,t,p in permissions]
        }
        output = ROOT / 'relatorio_estrutura.json'
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Conexao OK | banco:', database, '| usuario:', username)
        print('Transacao somente leitura:', readonly)
        print('Tabelas encontradas:', len(tables))
        print('Tabelas com permissao de escrita:', sum(1 for _,_,p in permissions if p))
        print('Relatorio:', output)
        if any(p for _,_,p in permissions):
            print('ATENCAO: usuario tem permissao de escrita; solicite revogacao ao DBA.')
    except OperationalError:
        print('Falha na conexao. Verifique usuario, senha, pg_hba.conf, SSL e versao do PostgreSQL.')
        traceback.print_exc()
        sys.exit(1)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    finally:
        if conn is not None:
            conn.close()

if __name__ == '__main__':
    main()
