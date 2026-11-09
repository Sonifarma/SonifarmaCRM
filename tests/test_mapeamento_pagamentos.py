import importlib.util
from pathlib import Path
import unittest
from unittest.mock import MagicMock
spec=importlib.util.spec_from_file_location('mapear_pagamentos',Path(__file__).resolve().parents[1]/'integracao/painel/mapear_pagamentos.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class PaymentMetadataTests(unittest.TestCase):
    def test_discovery_uses_table_and_column_names_and_real_neighbors(self):
        columns=[('venda','id'),('liquidacao','formapagamentoid'),('formapagamento','id'),('bandeira','id'),('produto','id'),('cliente','id')]
        links=[('liquidacao','formapagamentoid','formapagamento','id'),('venda','clienteid','cliente','id')]
        found=module.selecionar(columns,links)
        self.assertIn('liquidacao',found);self.assertIn('formapagamento',found);self.assertIn('cliente',found)
        self.assertNotIn('produto',found)

    def test_catalog_only_readonly_and_rollback(self):
        conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value=('on',)
        cur.fetchall.side_effect=[[('formapagamento','id','bigint','int8','NO')],[]]
        report=module.coletar(conn)
        conn.set_session.assert_called_once_with(readonly=True,autocommit=False)
        conn.rollback.assert_called_once()
        self.assertEqual(report['tabelas'],['formapagamento'])
        for call in cur.execute.call_args_list:
            sql=call.args[0]
            self.assertNotIn('FROM public.',sql)
        self.assertNotIn('senha',str(report))

    def test_not_readonly_stops_and_rolls_back(self):
        conn=MagicMock();conn.cursor.return_value.__enter__.return_value.fetchone.return_value=('off',)
        with self.assertRaises(RuntimeError):module.coletar(conn)
        conn.rollback.assert_called_once()

if __name__=='__main__':unittest.main()
