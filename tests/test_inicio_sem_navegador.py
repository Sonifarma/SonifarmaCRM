import unittest
from unittest.mock import patch,MagicMock
import test_whatsapp_validacao as validation
panel=validation.panel

class StartupTests(unittest.TestCase):
    def test_default_does_not_open_browser_and_opt_in_opens_once(self):
        for args,expected in [([],0),(['--abrir-navegador'],1)]:
            with self.subTest(args=args),patch('sys.argv',['painel']+args),patch.object(panel,'DB',None),patch.object(panel,'threading') as threading,patch.object(panel,'ThreadingHTTPServer') as server,patch.object(panel.webbrowser,'open') as browser:
                with patch.object(panel,'agenda_conn',return_value=MagicMock()),patch.object(panel,'pos_conn',return_value=MagicMock()),patch.object(panel,'auth_conn',return_value=MagicMock()),patch.object(panel,'nomes_conn',return_value=MagicMock()):
                    server.return_value.serve_forever.side_effect=KeyboardInterrupt
                    panel.main()
                self.assertEqual(browser.call_count,expected)
                server.return_value.server_close.assert_called_once()
