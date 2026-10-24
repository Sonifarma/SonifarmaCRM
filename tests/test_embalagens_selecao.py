import unittest
from unittest.mock import patch
import test_whatsapp_validacao as validation
panel = validation.panel

class PackagingSelectionTests(unittest.TestCase):
    setUp = validation.ValidationTests.setUp
    stop_server = validation.ValidationTests.stop_server
    request = validation.ValidationTests.request

    def test_accepts_more_than_100_and_1500_characters(self):
        ids = list(range(100000, 100500))
        with patch.object(panel, 'DB', {'fixture': True}), patch.object(panel, 'obter_ranking', return_value=(200, {'ok': True})) as ranking:
            status, result = self.request('/api/central?embalagens=' + ','.join(map(str, ids)))
            self.assertEqual(status, 200, result)
            self.assertEqual(ranking.call_args.args[0]['embalagens'], ids)

    def test_still_rejects_invalid_identifiers(self):
        with patch.object(panel, 'DB', {'fixture': True}), patch.object(panel, 'obter_ranking') as ranking:
            for raw in ('1,0', '1,-2', '1,abc', '1,,2'):
                self.assertEqual(self.request('/api/central?embalagens=' + raw)[0], 400)
            ranking.assert_not_called()
