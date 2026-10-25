"""Exercise the installed Scrapling HTTP and Chromium engines via a local proxy.

This is offline: the proxy serves the fixture itself and never forwards traffic.
"""
import base64
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from config import settings
from utils import scrapling
from utils.flashproxy import ProxyRoute

HTML = '<html><head><title>Locations</title></head><body>Appartement T2 Rouen 76000 — 700 € — 40 m²</body></html>'
AUTH = 'Basic ' + base64.b64encode(b'proxy-user:p@ss:word').decode()


class LocalProxyTransportTests(unittest.TestCase):
    def test_both_real_engines_use_proxy_auth_and_preserve_session(self):
        try:
            from scrapling.fetchers import Fetcher, StealthyFetcher
            browser = scrapling._browser_path()
        except ImportError:
            self.skipTest('Install Scrapling fetchers to run the local transport check')
        if not browser:
            self.skipTest('Chromium is required for the local transport check')
        seen = []

        class Proxy(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.headers.get('Proxy-Authorization') != AUTH:
                    self.send_response(407)
                    self.send_header('Proxy-Authenticate', 'Basic realm="local-fixture"')
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                seen.append((self.path, self.headers.get('Cookie'), self.headers.get('Referer')))
                data = HTML.encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            route = ProxyRoute(f'http://proxy-user:p%40ss%3Aword@127.0.0.1:{server.server_port}')
            with patch.object(scrapling, 'get_proxy_route', return_value=route), \
                 patch.object(scrapling, 'scrapling_enabled', return_value=True), \
                 patch.object(settings, 'scrapling_timeout_seconds', 8):
                url = 'http://housing.test/results'
                result = scrapling.fetch_with_scrapling(url, referer='http://housing.test/', headers={'Cookie': 'fixture=1'})
                self.assertIn('Rouen', result or '')
                self.assertTrue(any(path == url and cookie == 'fixture=1' for path, cookie, _ in seen))
                seen.clear()
                # Simulate a challenge at the HTTP stage, then run Chromium.
                with patch.object(Fetcher, 'get', return_value=SimpleNamespace(status=403, html_content='captcha')):
                    result = scrapling.fetch_with_scrapling(url, referer='http://housing.test/', headers={'Cookie': 'fixture=1'})
                self.assertIn('Rouen', result or '')
                self.assertTrue(any(path == url for path, _, _ in seen))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
