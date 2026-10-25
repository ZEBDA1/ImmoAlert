"""Verify the real CLI's environment authentication against a local API only."""
import json
import shutil
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from config import settings
from utils import firecrawl as fc


@unittest.skipUnless(shutil.which('firecrawl'), 'Firecrawl CLI not installed')
class FirecrawlCliPoolTests(unittest.TestCase):
    def test_real_cli_fails_over_from_empty_account_to_second_credential(self):
        seen = []
        class API(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
                auth = self.headers.get('Authorization')
                seen.append((self.path, auth, body))
                if auth == 'Bearer fc-local-empty':
                    code, data = 402, {'success': False, 'error': 'Insufficient credits'}
                elif auth == 'Bearer fc-local-valid':
                    code, data = 200, {'success': True, 'data': {'rawHtml': '<html>Appartement Rouen</html>', 'metadata': {'statusCode': 200}}}
                else:
                    code, data = 401, {'success': False, 'error': 'Unauthorized'}
                raw = json.dumps(data).encode()
                self.send_response(code)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        server = ThreadingHTTPServer(('127.0.0.1',0),API)
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with patch.object(settings,'firecrawl_enabled',True), \
                 patch.object(settings,'firecrawl_api_key',None), \
                 patch.object(settings,'firecrawl_api_keys',('fc-local-empty','fc-local-valid')), \
                 patch.object(fc,'_KEY_PAUSES',{}), patch.object(fc,'_KEY_CURSOR',0), \
                 patch.object(fc,'_RATE_LIMITED_UNTIL',0), \
                 patch.dict('os.environ',{'FIRECRAWL_API_URL': f'http://127.0.0.1:{server.server_port}', 'FIRECRAWL_NO_ENDPOINT_FEEDBACK':'1'}):
                html = fc.FirecrawlClient(timeout_seconds=40).fetch_html('https://fixture.example/results', use_cache=False)
                self.assertIn('Appartement Rouen',html or '')
                self.assertEqual(fc.key_pool_status()['keys_paused'],1)
            self.assertTrue(any(auth=='Bearer fc-local-empty' for _,auth,_ in seen))
            self.assertTrue(any(auth=='Bearer fc-local-valid' for _,auth,_ in seen))
            self.assertTrue(all(path=='/v2/scrape' for path,_,_ in seen))
            self.assertTrue(all('rawHtml' in body['formats'] for _,_,body in seen))
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)
