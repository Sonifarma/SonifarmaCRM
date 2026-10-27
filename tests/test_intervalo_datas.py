from datetime import date
import unittest
from unittest.mock import patch
import test_whatsapp_validacao as validation
panel = validation.panel

class DateRangeTests(unittest.TestCase):
    setUp = validation.ValidationTests.setUp
    stop_server = validation.ValidationTests.stop_server
    request = validation.ValidationTests.request

    def test_bounds_are_inclusive(self):
        for day, expected in [(19,False),(20,True),(25,True),(26,True),(27,False)]:
            self.assertEqual(panel.previsao_no_periodo(date(2026,10,day), 'intervalo', date(2026,10,27), '2026-10-20', '2026-10-26'), expected)
        panel.validar_intervalo('intervalo', '2026-10-26', '2026-10-26')

    def test_central_receives_dates(self):
        with patch.object(panel, 'DB', {'fixture':True}), patch.object(panel,'obter_ranking',return_value=(200,{})) as ranking:
            self.assertEqual(self.request('/api/central?janela=intervalo&inicio=2026-10-20&fim=2026-10-26')[0],200)
            self.assertEqual(ranking.call_args.args[0]['inicio'],'2026-10-20')
            self.assertEqual(ranking.call_args.args[0]['fim'],'2026-10-26')

    def test_card_receives_same_dates(self):
        with patch.object(panel, 'DB', {'fixture':True}), patch.object(panel,'calcular_recompras',return_value=([],False)) as calculate:
            self.assertEqual(self.request('/api/recompra?id=1&janela=intervalo&inicio=2026-10-20&fim=2026-10-26')[0],200)
            self.assertEqual(calculate.call_args.args[-2:],('2026-10-20','2026-10-26'))

    def test_invalid_ranges_are_rejected_on_both_routes(self):
        with patch.object(panel,'DB',{'fixture':True}):
            for route in ('/api/central?', '/api/recompra?id=1&'):
                for dates in ('', '&inicio=2026-10-27&fim=2026-10-20', '&inicio=2026-02-30&fim=2026-10-20'):
                    self.assertEqual(self.request(route+'janela=intervalo'+dates)[0],400)
