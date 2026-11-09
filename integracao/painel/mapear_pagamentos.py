"""Descobre metadados de pagamentos, sem consultar clientes ou transações."""
import argparse
import getpass
import json
import os
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
TOKENS = ('pag', 'receb', 'cartao', 'cartão', 'bandeira', 'tef', 'conven', 'vidalink', 'parcel', 'financeir')


def configuracao():
    cfg = {}
    for path in (BASE.parent / '.env', BASE / '.env'):
        if path.is_file():
            for line in path.read_text(encoding='utf-8-sig').splitlines():
                if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    cfg[key.strip()] = value.strip().strip('\"').strip("'")
    cfg.update({k: v for k, v in os.environ.items() if k.startswith('PG')})
    return cfg


def selecionar(colunas, vinculos):
    seeds = {t for t, c, *_ in colunas if any(token in (t + ' ' + c).lower() for token in TOKENS)}
    seeds.update(t for t, *_ in colunas if t in ('venda', 'itemvenda', 'pbm'))
    # Uma vizinhança de relações reais, sem inferir joins pelo nome da coluna.
    selected = set(seeds)
    for source, _, target, _ in vinculos:
        if source in seeds or target in seeds:
            selected.update((source, target))
    return selected


def coletar(conn):
    conn.set_session(readonly=True, autocommit=False)
    try:
        with conn.cursor() as cur:
            cur.execute('SET LOCAL statement_timeout = 15000')
            cur.execute("SELECT current_setting('transaction_read_only')")
            if cur.fetchone()[0] != 'on':
                raise RuntimeError('A conexão não está em modo somente leitura')
            cur.execute("""SELECT table_name,column_name,data_type,udt_name,is_nullable
                FROM information_schema.columns WHERE table_schema='public'
                ORDER BY table_name,ordinal_position""")
            columns = cur.fetchall()
            # Pares por posição preservam FKs compostas.
            cur.execute("""SELECT src.relname,sa.attname,dst.relname,da.attname
                FROM pg_catalog.pg_constraint k
                JOIN pg_catalog.pg_class src ON src.oid=k.conrelid
                JOIN pg_catalog.pg_namespace sn ON sn.oid=src.relnamespace
                JOIN pg_catalog.pg_class dst ON dst.oid=k.confrelid
                JOIN pg_catalog.pg_namespace dn ON dn.oid=dst.relnamespace
                JOIN LATERAL unnest(k.conkey,k.confkey) AS keys(s,d) ON TRUE
                JOIN pg_catalog.pg_attribute sa ON sa.attrelid=src.oid AND sa.attnum=keys.s
                JOIN pg_catalog.pg_attribute da ON da.attrelid=dst.oid AND da.attnum=keys.d
                WHERE k.contype='f' AND sn.nspname='public' AND dn.nspname='public'
                ORDER BY src.relname,k.conname,sa.attnum""")
            links = cur.fetchall()
        tables = selecionar(columns, links)
        return dict(gerado_em=datetime.now().isoformat(timespec='seconds'),somente_leitura=True,
                    conteudo='Somente nomes de tabelas, colunas e vínculos. Sem dados de clientes ou pagamentos.',
                    tabelas=sorted(tables),
                    colunas=[dict(zip(('tabela','coluna','tipo','tipo_interno','permite_nulo'),r)) for r in columns if r[0] in tables],
                    vinculos=[dict(zip(('tabela','coluna','referencia_tabela','referencia_coluna'),r)) for r in links if r[0] in tables and r[2] in tables])
    finally:
        conn.rollback()


def main():
    import psycopg2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--saida', type=Path, default=BASE / 'mapeamento_pagamentos.json')
    args = parser.parse_args()
    cfg = configuracao()
    password = cfg.get('PGPASSWORD') or getpass.getpass('Senha PostgreSQL (oculta, somente neste computador): ')
    conn = psycopg2.connect(host=cfg.get('PGHOST','192.168.0.250'),port=cfg.get('PGPORT','5432'),
        dbname=cfg.get('PGDATABASE','sonifarma_loja01'),user=cfg.get('PGUSER','leitura_03012024'),
        password=password,sslmode=cfg.get('PGSSLMODE','prefer'),connect_timeout=7,application_name='sonifarma_metadados_pagamentos')
    try:
        report = coletar(conn)
    finally:
        conn.close()
    args.saida.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Mapeamento concluído:',args.saida)
    print('Tabelas:',len(report['tabelas']),'| Somente estrutura; sem clientes ou transações.')


if __name__ == '__main__':
    main()
