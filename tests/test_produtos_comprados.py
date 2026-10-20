import unittest
from unittest.mock import patch
import test_whatsapp_validacao as validation
panel = validation.panel


class ProductsAPITests(unittest.TestCase):
    setUp = validation.ValidationTests.setUp
    stop_server = validation.ValidationTests.stop_server
    request = validation.ValidationTests.request

    def test_pages_cover_all_products_and_use_bounded_fetch(self):
        calls = []
        def query(sql, args, limit):
            calls.append((args, limit))
            cid, size, offset = args
            self.assertEqual(cid, 1)
            self.assertEqual(size, 51)
            return [dict(produtoid=i, produto='Produto '+str(i), total_produtos=123)
                    for i in range(offset, min(offset+size, 123))]
        with patch.object(panel, 'DB', {'fixture': True}), patch.object(panel, 'query', side_effect=query):
            rows = []
            offset = 0
            while offset is not None:
                status, result = self.request(f'/api/produtos-comprados?id=1&offset={offset}')
                self.assertEqual(status, 200, result)
                self.assertEqual(result['total'], 123)
                self.assertLessEqual(len(result['produtos']), 50)
                rows.extend(result['produtos'])
                offset = result['proximo_offset']
        self.assertEqual(len(rows), 123)
        self.assertEqual(len({r['produtoid'] for r in rows}), 123)
        self.assertTrue(all('total_produtos' not in r for r in rows))
        self.assertEqual([c[0][2] for c in calls], [0, 50, 100])
        self.assertTrue(all(c[1] == 51 for c in calls))

    def test_invalid_id_or_page_never_queries_erp(self):
        with patch.object(panel, 'DB', {'fixture': True}), patch.object(panel, 'query') as query:
            for suffix in ('id=0', 'id=-1', 'id=abc', 'id=1&offset=-1', 'id=1&offset=oops', 'id=1&offset=9999999999'):
                self.assertEqual(self.request('/api/produtos-comprados?'+suffix)[0], 400)
            query.assert_not_called()

    def test_empty_history_is_not_an_error(self):
        with patch.object(panel, 'DB', {'fixture': True}), patch.object(panel, 'query', return_value=[]):
            status, result = self.request('/api/produtos-comprados?id=1')
        self.assertEqual(status, 200)
        self.assertEqual(result, {'produtos': [], 'total': 0, 'offset': 0, 'proximo_offset': None})
