from pathlib import Path
from dataclasses import asdict

import http.server
import json
import printer as printer_lib
import queue
import threading
import urllib.parse

DEFAULT_IP = '192.168.107.3'

def simple_handler(content_type=None):
  def create_decorator(handler):
    def wrapper(self, *args, **kwargs):
      try:
        content = handler(self, *args, **kwargs)
      except:
        self.send_response(500)
        self.end_headers()
        raise
      self.send_response(200)
      if content_type:
        self.send_header('Content-type', content_type)
      self.end_headers()
      self._write_string(content)
    return wrapper
  return create_decorator

class FlashForgeRequestHandler(http.server.BaseHTTPRequestHandler):

  def __init__(self, *args, **kwargs):
    self._handlers = {
        '/': self._dashboard,
        '/dashboard': self._dashboard,
        '/handlers': self._handlers,
        '/info': self._info,
        '/head_position': self._head_position,
        '/temp': self._temp,
        '/progress': self._progress,
        '/status': self._status,
        '/stream': self._stream,
        '/favicon.svg': self._favicon,
    }
    super().__init__(*args, **kwargs)

  def do_GET(self):
    url = urllib.parse.urlparse(self.path)
    handler = self._handlers.get(url.path)
    if not handler:
      self.send_response(404)
      self.end_headers()
      return

    handler()

  @simple_handler(content_type='text/html')
  def _handlers(self):
    ip = self._get_ip()
    handler_items = ''.join([f'<li><a href=".{handler}?ip={ip}">{handler}</a></li>' for handler in self._handlers.keys()])
    return f'<html><ul>{handler_items}</ul></html>'

  def _get_printer(self):
    return self.server.get_printer(self._get_ip())

  @simple_handler(content_type='application/json')
  def _info(self):
    return self._to_json(self._get_printer().get_info())

  @simple_handler(content_type='application/json')
  def _head_position(self):
    return self._to_json(self._get_printer().get_head_position())

  @simple_handler(content_type='application/json')
  def _temp(self):
    return self._to_json(self._get_printer().get_temp())

  @simple_handler(content_type='application/json')
  def _progress(self):
    return self._to_json(self._get_printer().get_progress())

  @simple_handler(content_type='application/json')
  def _status(self):
    return self._to_json(self._get_printer().get_status())

  def _stream(self):
    ip = self._get_ip()
    subscriber_queue = queue.Queue()
    stream = self._get_printer().get_camera_stream()
    try:
      response_headers = stream.start_stream(subscriber_queue)
      self.send_response(200)
      for header in response_headers.keys():
        self.send_header(header, response_headers[header])
      self.end_headers()
      try:
        while True:
          self.wfile.write(subscriber_queue.get())
      except BrokenPipeError:
        print('client disconnected')
    finally:
      stream.stop_stream(subscriber_queue)
  
  def _dashboard(self):
    self._send_file('static/dashboard.html', 'text/html')

  def _favicon(self):
    self._send_file('static/favicon.svg', 'image/svg+xml')

  def _get_first_query_param(self, param):
    url = urllib.parse.urlparse(self.path)
    param_values = urllib.parse.parse_qs(url.query).get(param)
    return param_values[0] if param_values else None

  def _send_file(self, path, content_type):
    try:
      file_bytes = self.server.file_cache.get_bytes(path)
    except:
      self.send_response(500)
      raise
    self.send_response(200)
    if content_type:
      self.send_header('Content-type', content_type)
    self.end_headers()
    self.wfile.write(file_bytes)

  def _to_json(self, response):
    return json.dumps(asdict(response))

  def _write_string(self, string):
    self.wfile.write(string.encode('utf-8'))

  def _get_ip(self):
    return self._get_first_query_param('ip') or DEFAULT_IP


class FlashForgeHttpServer(http.server.ThreadingHTTPServer):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self._printers = {}
    self.file_cache = FileCache()
    self._lock = threading.Lock()

  def get_printer(self, ip):
    with self._lock:
      printer = self._printers.get(ip)
      if not printer:
        printer = printer_lib.Printer(ip)
        self._printers[ip] = printer
      return printer

class FileCache:
  def __init__(self):
    self._files = {}

  def get_bytes(self, path):
    file_bytes = self._files.get(path)
    if not file_bytes:
      file_bytes = Path(path).read_bytes()
      self._files[path] = file_bytes
    return file_bytes

if __name__ == "__main__":
  server = FlashForgeHttpServer(('', 8000), FlashForgeRequestHandler)
  server.serve_forever()
