from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

import argparse
import faulthandler
import http.server
import json
import logging
import printer as printer_lib
import queue
import signal
import tempfile
import threading
import urllib.parse

def simple_handler(content_type=None, response_code=200):
  def create_decorator(handler):
    def wrapper(self, *args, **kwargs):
      try:
        content = handler(self, *args, **kwargs)
      except Exception as e:
        self.send_response(500)
        self.end_headers()
        self._write_string(json.dumps({'error': str(e)}))
        raise
      self.send_response(response_code)
      if content_type:
        self.send_header('Content-type', content_type)
      self.end_headers()
      self._write_string(content)
    return wrapper
  return create_decorator

@dataclass
class GetProgressResponse:
  progress: printer_lib.ProgressResponse
  times: printer_lib.EstimatedPrintingTimes

@dataclass
class LogsResponse:
  logs: list[printer_lib.LoggedProgress]
  times: printer_lib.EstimatedPrintingTimes

class FlashForgeRequestHandler(http.server.BaseHTTPRequestHandler):

  def __init__(self, *args, **kwargs):
    self._handlers = {
        '/': self._dashboard_html,
        '/abort': self._abort,
        '/dashboard': self._dashboard_html,
        '/dashboard.css': self._dashboard_css,
        '/dashboard.js': self._dashboard_js,
        '/favicon.svg': self._favicon,
        '/handlers': self._handlers,
        '/head_position': self._head_position,
        '/info': self._info,
        '/pause': self._pause,
        '/resume': self._resume,
        '/progress': self._progress,
        '/debug_estimate': self._debug_estimate,
        '/reset_stream': self._reset_stream,
        '/status': self._status,
        '/stream': self._stream,
        '/temp': self._temp,
        '/thread_dump': self._thread_dump,
    }
    super().__init__(*args, **kwargs)

  def do_GET(self):
    self._handleRequest()

  def do_POST(self):
    self._handleRequest()

  def _handleRequest(self):
    url = urllib.parse.urlparse(self.path)
    handler = self._handlers.get(url.path) or self._not_found_handler
    handler()

  @simple_handler(content_type='text/html', response_code=404)
  def _not_found_handler(self):
    return f'unsupported path: {urllib.parse.urlparse(self.path).path}'

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

  @simple_handler(content_type='text/plain')
  def _thread_dump(self):
    with tempfile.TemporaryFile(mode='w+t') as tmp:
      faulthandler.dump_traceback(file=tmp, all_threads=True)
      tmp.seek(0)
      return tmp.read()

  @simple_handler(content_type='application/json')
  def _progress(self):
    printer = self._get_printer()
    return self._to_json(GetProgressResponse(
      printer.get_progress(),
      printer.estimate_printing_times()
    ))

  @simple_handler(content_type='application/json')
  def _debug_estimate(self):
    printer = self._get_printer()
    def serializer(obj):
      if isinstance(obj, datetime):
        return {
            'iso': obj.isoformat(),
            'timestamp': int(obj.timestamp())
        }
      raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
    return self._to_json(LogsResponse(
      list(printer._progress_monitor._logged_progress), printer.estimate_printing_times()), default=serializer)

  @simple_handler(content_type='application/json')
  def _status(self):
    return self._to_json(self._get_printer().get_status())

  @simple_handler()
  def _release_control(self):
    self._get_printer().release_control()
    return ''

  @simple_handler()
  def _pause(self):
    self._get_printer().pause()
    return ''

  @simple_handler()
  def _resume(self):
    self._get_printer().resume()
    return ''

  @simple_handler()
  def _abort(self):
    self._get_printer().abort()
    return ''

  def _stream(self):
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
        logging.info('client disconnected')
    finally:
      stream.stop_stream(subscriber_queue)

  @simple_handler()
  def _reset_stream(self):
    self._get_printer().get_camera_stream().reset_stream()
    return ''

  def _dashboard_html(self):
    self._send_file('static/dashboard.html', 'text/html')

  def _dashboard_js(self):
    self._send_file('static/dashboard.js', 'text/javascript')

  def _dashboard_css(self):
    self._send_file('static/dashboard.css', 'text/css')

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

  def _to_json(self, response, default=None):
    return json.dumps(asdict(response), default=default)

  def _write_string(self, string):
    self.wfile.write(string.encode('utf-8'))

  def _get_ip(self):
    ip = self._get_first_query_param('ip') or self.server.default_printer_ip
    if not ip:
      raise RuntimError('No ip or default ip provided')
    return ip


class FlashForgeHttpServer(http.server.ThreadingHTTPServer):
  def __init__(self, *args, default_printer_ip=None, printer_ips=None, file_cache=None, **kwargs):
    super().__init__(*args, **kwargs)
    self._printers = {}
    self.file_cache = file_cache
    self._lock = threading.Lock()
    self.default_printer_ip = default_printer_ip
    if printer_ips:
      for printer_ip in printer_ips:
        self.get_printer(printer_ip)

  def get_printer(self, ip):
    with self._lock:
      printer = self._printers.get(ip)
      if not printer:
        printer = printer_lib.Printer(ip)
        printer.init()
        self._printers[ip] = printer
      return printer

@dataclass
class CacheEntry:
  create_time: datetime
  content: bytes

class FileCache:
  def __init__(self, ttl):
    self._files = {}
    self._ttl = ttl

  def get_bytes(self, path):
    cache_entry = self._files.get(path)
    now = datetime.now()
    if not cache_entry or cache_entry.create_time + self._ttl < now:
      print(f'read {path}')
      file_bytes = Path(path).read_bytes()
      cache_entry = CacheEntry(now, file_bytes)
      self._files[path] = cache_entry
    return cache_entry.content

if __name__ == "__main__":
  faulthandler.register(signal.SIGUSR1)

  logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

  parser = argparse.ArgumentParser()
  parser.add_argument('--port', help="port to listen for http connections", type=int, default=8000)
  parser.add_argument('-p', "--printers", help="csv of printers to monitor on startup", default='')
  parser.add_argument('-t', "--ttl", help="ttl in seconds of static in memory content", type=int, default=600)
  args = parser.parse_args()
  printer_ips = [ip.strip() for ip in args.printers.split(',')]
  default_printer_ip = printer_ips[0] if printer_ips else None
  server = FlashForgeHttpServer(
      ('', args.port),
      FlashForgeRequestHandler,
      default_printer_ip=default_printer_ip,
      printer_ips=printer_ips,
      file_cache=FileCache(timedelta(seconds=args.ttl)))
  server.serve_forever()
