"""Sonifarma - Modulo 1: diagnostico agregado de historico de compras.
Somente leitura. Nao exporta dados pessoais ou nomes de medicamentos.
Requer: psycopg2-binary, .env (PGHOST, PGPORT, PGDATABASE, PGUSER, PGSSLMODE).
"""
import os
import json
import getpass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import psycopg2

BASE = Path(__file__).resolve().parent

def env_local():
    d = {}
    p = BASE / '.env'
    if p.exists():
        for line in p.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            d[k.strip()] = v.strip().strip('"').strip("'")
    return d

def normalize(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat()
    return v

def run(cur, query):
    cur.execute(query)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, [normalize(v) for v in row])) for row in cur.fetchall()]

# As devolucoes sao agrupadas antes do JOIN para evitar multiplicar itens.
# Item F e valido quando o saldo e positivo. Item D exige devolucao registrada
# e saldo positivo. Essa regra e conservadora ate validacao no ERP.
VALID_ITEMS = """
WITH devolvido AS (
 SELECT itemvendaid, SUM(quantidade) AS qtd_devolvida
 FROM public.itemdevolucaovenda
 GROUP BY itemvendaid
),
validos AS (
 SELECT v.id AS vendaid, v.pessoaid, v.datahorafechamento,
        iv.id AS itemid,
        GREATEST(0::numeric, iv.quantidade - COALESCE(d.qtd_devolvida,0)) AS qtd_liquida,
        CASE WHEN iv.quantidade > 0
             THEN iv.valortotal * GREATEST(0::numeric, iv.quantidade - COALESCE(d.qtd_devolvida,0)) / iv.quantidade
             ELSE 0::numeric END AS valor_estimado
 FROM public.venda v
 JOIN public.itemvenda iv ON iv.vendaid = v.id
 LEFT JOIN devolvido d ON d.itemvendaid = iv.id
 WHERE v.status IN ('F','D')
   AND v.datahorafechamento IS NOT NULL
   AND v.pessoaid IS NOT NULL
   AND v.pessoaid > 0
   AND iv.quantidade > 0
   AND (iv.status = 'F' OR (iv.status = 'D' AND d.itemvendaid IS NOT NULL))
   AND NOT EXISTS (SELECT 1 FROM public.estornovenda ev WHERE ev.vendaid = v.id)
),
compras AS (
 SELECT vendaid, pessoaid, MAX(datahorafechamento) AS data_compra,
        SUM(qtd_liquida) AS quantidade_liquida,
        SUM(valor_estimado) AS valor_estimado
 FROM validos WHERE qtd_liquida > 0
 GROUP BY vendaid, pessoaid
)
"""

QUERIES = {
 'resumo': VALID_ITEMS + """
 SELECT COUNT(*) AS compras_validas, COUNT(DISTINCT pessoaid) AS clientes_identificados,
        COALESCE(SUM(quantidade_liquida),0) AS unidades_liquidas,
        ROUND(COALESCE(SUM(valor_estimado),0),2) AS valor_liquido_estimado,
        MIN(data_compra) AS primeira_compra, MAX(data_compra) AS ultima_compra
 FROM compras
 """,
 'faixas_inatividade': VALID_ITEMS + """
 , clientes AS (SELECT pessoaid, MAX(data_compra) AS ultima_compra,
                COUNT(*) AS compras_total
               FROM compras GROUP BY pessoaid)
 SELECT CASE WHEN ultima_compra >= CURRENT_TIMESTAMP - INTERVAL '30 days' THEN '0-29 dias'
             WHEN ultima_compra >= CURRENT_TIMESTAMP - INTERVAL '60 days' THEN '30-59 dias'
             WHEN ultima_compra >= CURRENT_TIMESTAMP - INTERVAL '90 days' THEN '60-89 dias'
             ELSE '90 dias ou mais' END AS faixa,
        COUNT(*) AS clientes
 FROM clientes GROUP BY 1 ORDER BY 1
 """,
 'frequencia_365_dias': VALID_ITEMS + """
 , frequencia AS (
   SELECT pessoaid, COUNT(*) AS compras_365
   FROM compras
   WHERE data_compra >= CURRENT_TIMESTAMP - INTERVAL '365 days'
   GROUP BY pessoaid)
 SELECT CASE WHEN compras_365 = 1 THEN '1 compra'
             WHEN compras_365 BETWEEN 2 AND 3 THEN '2-3 compras'
             WHEN compras_365 BETWEEN 4 AND 6 THEN '4-6 compras'
             ELSE '7 ou mais compras' END AS faixa,
        COUNT(*) AS clientes
 FROM frequencia GROUP BY 1 ORDER BY 1
 """,
 'compras_mensais_12_meses': VALID_ITEMS + """
 SELECT TO_CHAR(DATE_TRUNC('month', data_compra),'YYYY-MM') AS mes,
        COUNT(*) AS compras, COUNT(DISTINCT pessoaid) AS clientes,
        ROUND(COALESCE(SUM(valor_estimado),0),2) AS valor_estimado
 FROM compras
 WHERE data_compra >= DATE_TRUNC('month', CURRENT_TIMESTAMP) - INTERVAL '11 months'
 GROUP BY DATE_TRUNC('month', data_compra)
 ORDER BY DATE_TRUNC('month', data_compra)
 """
}

def main():
    cfg = env_local()
    pwd = getpass.getpass('Senha PostgreSQL (nao exibida): ')
    if not pwd:
        raise SystemExit('Senha vazia. Execute novamente.')
    params = dict(host=cfg.get('PGHOST', os.getenv('PGHOST', '192.168.0.250')),
                  port=int(cfg.get('PGPORT', os.getenv('PGPORT', '5432'))),
                  dbname=cfg.get('PGDATABASE', os.getenv('PGDATABASE', 'sonifarma_loja01')),
                  user=cfg.get('PGUSER', os.getenv('PGUSER', 'leitura_03012024')),
                  password=pwd, sslmode=cfg.get('PGSSLMODE','prefer'),
                  connect_timeout=8, application_name='sonifarma_modulo1_readonly')
    result = {'modulo':'historico_compras_agregado',
              'gerado_em':datetime.now().isoformat(timespec='seconds'),
              'criterios':{'status_venda':['F','D'], 'status_item':['F','D com devolucao comprovada'],
                          'exclui_venda_com_estorno':True,
                          'quantidade_liquida':'max(0, vendida - devolvida)',
                          'valor':'estimativa proporcional ao valor total do item, nao faturamento contabil',
                          'observacao':'Analise exploratoria; conferir regras do ERP e devolucoes antes de uso operacional.'},
              'resultados':{}, 'erros':{}}
    with psycopg2.connect(**params) as conn:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '120000ms'")
            cur.execute("SET LOCAL lock_timeout = '2000ms'")
            for name, sql in QUERIES.items():
                print('Executando:', name, flush=True)
                try:
                    cur.execute('SAVEPOINT analise')
                    result['resultados'][name] = run(cur, sql)
                    cur.execute('RELEASE SAVEPOINT analise')
                    print('  OK')
                except psycopg2.Error as exc:
                    cur.execute('ROLLBACK TO SAVEPOINT analise')
                    cur.execute('RELEASE SAVEPOINT analise')
                    result['erros'][name] = {'codigo':exc.pgcode, 'mensagem':str(exc).splitlines()[0]}
                    print('  Erro:', result['erros'][name]['mensagem'])
        conn.rollback()
    out = BASE / 'modulo1_resultado_agregado.json'
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('\nRelatorio gerado:', out)
    print('Nenhum nome, telefone ou registro individual foi exportado.')

if __name__ == '__main__':
    try:
        main()
    except (psycopg2.Error, OSError, ValueError) as exc:
        raise SystemExit('Falha: ' + str(exc).splitlines()[0])
