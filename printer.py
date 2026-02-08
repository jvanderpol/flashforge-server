import datetime
import functools
import http.client
import logging
import re
import socket
import threading
import time

from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Union

class ResponseParsingException(Exception):
  pass

class PrinterConnectionError(Exception):
  pass

class Printer:
  def __init__(self, ip):
    self._ip = ip
    self._camera_stream = CameraStream(self._ip)
    self._lock = threading.Lock()
    self._progress_monitor = None

  def init(self):
    self._progress_monitor = ProgressMonitor(self)
    self._progress_monitor.start_monitoring()

  def get_info(self):
    return self._call_rpc(INFO_RPC)

  def get_head_position(self):
    return self._call_rpc(HEAD_POSITION_RPC)

  def get_temp(self):
    return self._call_rpc(GET_TEMP_RPC)

  def get_progress(self):
    return self._call_rpc(PROGRESS_RPC)

  def estimate_printing_times(self):
    return self._progress_monitor.estimate_printing_times()

  def get_status(self):
    return self._call_rpc(STATUS_RPC)

  def pause(self):
    self._call_rpcs([
      REQUEST_CONTROL_RPC,
      PAUSE_RPC,
      RELEASE_CONTROL_RPC
    ])

  def resume(self):
    self._call_rpcs([
      REQUEST_CONTROL_RPC,
      RESUME_RPC,
      RELEASE_CONTROL_RPC
    ])

  def abort(self):
    self._call_rpcs([
      REQUEST_CONTROL_RPC,
      ABORT_RPC,
      RELEASE_CONTROL_RPC
    ])

  def get_camera_stream(self):
    return self._camera_stream

  def _call_rpc(self, rpc):
    return _call_with_retry(lambda: self._call_rpcs([rpc])[0], PrinterConnectionError)

  def _call_rpcs(self, rpcs):
    try:
      try:
        printer_socket = socket.socket()
        printer_socket.settimeout(1)
        printer_socket.connect((self._ip, 8899))
        responses = []
        for rpc in rpcs:
          printer_socket.send(rpc.request.encode())
          response = printer_socket.recv(1024)
          decoded_response = response.decode()
          try:
            responses.append(rpc.response_parser(decoded_response))
          except ResponseParsingException as e:
            logging.exception(f'Error parsing {decoded_response}')
            raise
        return responses
      finally:
        if printer_socket:
          printer_socket.close()
    except OSError as e:
      raise PrinterConnectionError(f'Error calling RPC: {e}') from e


@dataclass
class PrinterRpc:
  request: str
  response_parser: Callable[[str], Any]

def wrap(parse_function):
  def _wrapped(s):
    try:
      return parse_function(s)
    except ValueError as e:
      raise ResponseParsingException(f'Error parsing {s}') from e
  return _wrapped
wrapped_int = wrap(int)
wrapped_float = wrap(float)
wrapped_bool = wrap(bool)

def parse_field_with_regex(response_text, regex_str, match_parser=None):
  if not match_parser:
    match_parser = lambda s: s
  regex = re.compile(regex_str)
  for line in response_text.splitlines():
    field_match = regex.search(line)
    if field_match:
      return match_parser(field_match.group(1))
  raise ResponseParsingException(f'Unable to find {regex_str} in {response_text}')

def parse_delimited_field(response_text, field_name, include_colon=True, delimiter=' ', match_parser=None):
  optional_colon = ':' if include_colon else ''
  return parse_field_with_regex(
      response_text,
      f'(?:^|{delimiter}){field_name}{optional_colon} ?([^{delimiter}]+)(?:{delimiter}|$)',
      match_parser)

def parse_single_line_field(response_text, field_name, include_colon=True, match_parser=str):
  optional_colon = ':' if include_colon else ''
  return parse_field_with_regex(response_text, f'^{field_name}{optional_colon} ?(.*)$', match_parser=match_parser)

@dataclass
class Dimensions:
  x: Union[int, float]
  y: Union[int, float]
  z: Union[int, float]

@dataclass
class InfoResponse:
  # CMD M115 Received.
  # Machine Type: Flashforge Adventurer 5M Pro
  # Machine Name: SN12345
  # Firmware: v3.2.7
  # SN: SN12345
  # X: 220 Y: 220 Z: 220
  # Tool Count: 1
  # Mac Address:88:A9:A7:AA:BB:CC
  # ok
  machine_type: str
  machine_name: str
  firmware: str
  serial_number: str
  size: Dimensions
  tool_count: int
  mac_address: str

  def parse(response_text):
    return InfoResponse(
        machine_type = parse_single_line_field(response_text, 'Machine Type'),
        machine_name = parse_single_line_field(response_text, 'Machine Name'),
        firmware = parse_single_line_field(response_text, 'Firmware'),
        size = Dimensions(
          x = parse_delimited_field(response_text, 'X', match_parser=wrapped_int),
          y = parse_delimited_field(response_text, 'Y', match_parser=wrapped_int),
          z = parse_delimited_field(response_text, 'Z', match_parser=wrapped_int)
        ),
        serial_number = parse_single_line_field(response_text, 'SN'),
        tool_count = parse_single_line_field(response_text, 'Tool Count', match_parser=wrapped_int),
        mac_address = parse_single_line_field(response_text, 'Mac Address')
    )

@dataclass
class HeadPositionResponse:
  #CMD M114 Received.
  #X:100.000 Y:100.000 Z:96.926 A:0.000 B:0
  #ok
  position: Dimensions

  def parse(response_text):
    return HeadPositionResponse(
        position=Dimensions(
          x = parse_delimited_field(response_text, 'X', match_parser=wrapped_float),
          y = parse_delimited_field(response_text, 'Y', match_parser=wrapped_float),
          z = parse_delimited_field(response_text, 'Z', match_parser=wrapped_float)
        )
    )

@dataclass
class Temp:
  current: float
  target: float

  def parse(field_text):
    split = field_text.split('/')
    if len(split) != 2:
      raise ResponseParsingException(f'Unable to split {field_value}')
    return Temp(
        current=wrapped_float(split[0]),
        target=wrapped_float(split[1])
    )

@dataclass
class TempResponse:
  # CMD M105 Received.
  # T0:25.6/0.0 T1:0.0/0.0 B:23.6/0.0
  # ok
  nozzle: Temp
  bed: Temp

  def parse(response_text):
    return TempResponse(
      nozzle = parse_delimited_field(response_text, 'T0', match_parser=Temp.parse),
      bed = parse_delimited_field(response_text, 'B', match_parser=Temp.parse)
    )

@dataclass
class Progress:
  current: int
  total: int

  def parse(field_value):
    split = field_value.split('/')
    if len(split) != 2:
      raise ResponseParsingException(f'Unable to parse progress value {field_value}')
    return Progress(current=wrapped_int(split[0]), total=wrapped_int(split[1]))

@dataclass
class ProgressResponse:
  # CMD M27 Received.
  # SD printing byte 0/100
  # Layer: 0/0
  # ok
  printed_bytes: Progress
  layers: Progress

  def parse(response_text):
    return ProgressResponse(
      printed_bytes = parse_single_line_field(response_text, 'SD printing byte', include_colon=False, match_parser=Progress.parse),
      layers = parse_single_line_field(response_text, 'Layer', match_parser=Progress.parse)
    )
    
@dataclass
class StatusResponse:
  # CMD M119 Received.
  # Endstop: X-max: 110 Y-max: 110 Z-min: 0
  # MachineStatus: READY
  # MoveMode: READY
  # Status: S:1 L:0 J:0 F:0
  # LED: 1
  # CurrentFile:
  # ok
  status: str
  move_mode: str
  led: bool
  current_file: str

  def parse(response_text):
    return StatusResponse(
      status=parse_single_line_field(response_text, 'MachineStatus'),
      move_mode=parse_single_line_field(response_text, 'MoveMode'),
      led=parse_single_line_field(response_text, 'LED', match_parser=wrapped_bool),
      current_file=parse_single_line_field(response_text, 'CurrentFile')
    )

@dataclass
class EmptyResponse:
  def parse(response_text):
    return EmptyResponse()

  def validating_parser(success_regex):
    def parse(response_text):
      if not success_regex.search(response_text):
        raise Exception(f'Command failed: {response_text}')
      return EmptyResponse()
    return parse


INFO_RPC = PrinterRpc('~M115\r\n', InfoResponse.parse)
HEAD_POSITION_RPC = PrinterRpc('~M114\r\n', HeadPositionResponse.parse)
GET_TEMP_RPC = PrinterRpc('~M105\r\n', TempResponse.parse)
PROGRESS_RPC = PrinterRpc('~M27\r\n', ProgressResponse.parse)
STATUS_RPC = PrinterRpc('~M119\r\n', StatusResponse.parse)
PAUSE_RPC = PrinterRpc('~M25\r\n', EmptyResponse.parse)
RESUME_RPC = PrinterRpc('~M24\r\n', EmptyResponse.parse)
ABORT_RPC = PrinterRpc('~M26\r\n', EmptyResponse.parse)
REQUEST_CONTROL_RPC = PrinterRpc('~M601 S1\r\n', EmptyResponse.validating_parser(re.compile('Control Success')))
RELEASE_CONTROL_RPC = PrinterRpc('~M602\r\n', EmptyResponse.parse)

@dataclass
class LoggedProgress:
  progress: ProgressResponse
  status: StatusResponse
  timestamp: datetime.datetime

@dataclass
class EstimatedPrintingTimes:
  completed_seconds: int
  remaining_seconds: int


class ProgressMonitor:
  def __init__(self, printer):
    self._printer = printer
    self._logged_progress = deque([], maxlen=100000)
    self._lock = threading.Lock()
    self._monitor_thread = None
    self._monitor_thread_shutting_down = threading.Event()

  def start_monitoring(self):
    with self._lock:
      if self._monitor_thread:
        raise RuntimError('Monitor already running')
      self._monitor_thread = threading.Thread(target=self._monitor_loop)
      self._monitor_thread.start()

  def stop_monitoring(self):
    with self._lock:
      if self._monitor_thread:
        self._monitor_thread_shutting_down.set()
        self._monitor_thread.join()
        self._timer = None
        self._monitor_thread_shutting_down.clear()

  def _monitor_loop(self):
    while not self._monitor_thread_shutting_down.is_set():
      try:
        status = self._printer.get_status()
        if status.status == 'BUILDING_FROM_SD':
          progress = self._printer.get_progress()
          with self._lock:
            self._logged_progress.append(
                LoggedProgress(progress=progress, status=status, timestamp=datetime.datetime.now()))
        else:
          with self._lock:
            self._logged_progress.clear()
      except (PrinterConnectionError, ResponseParsingException) as e:
        logging.error(f'Error checking progress: {e}')
        pass
      self._monitor_thread_shutting_down.wait(10)

  def estimate_printing_times(self):
    with self._lock:
      if not self._logged_progress:
        return None

      first_printed_bytes = self._logged_progress[0].progress.printed_bytes
      last_printed_bytes = self._logged_progress[-1].progress.printed_bytes

      analyzed_logs = None
      if first_printed_bytes.current == 0:
        analyzed_logs = self._logged_progress
      else:
        # logging started mid print, try to find a more precise starting point
        for index, log in enumerate(self._logged_progress):
          if log.progress.printed_bytes.current > first_printed_bytes.current:
            first_printed_bytes = log.progress.printed_bytes
            analyzed_logs = list(self._logged_progress)[index:]
            break
        if not analyzed_logs:
          # Fall back to all logs if this has only seen logs in the same percent
          analyzed_logs = self._logged_progress

      logged_time = datetime.timedelta()
      printing_time_to_last_progress = None
      last_progress = None
      printing_time_after_last_progress = datetime.timedelta()
      for index, logged_progress in enumerate(analyzed_logs):
        if index > 0 and logged_progress.status.move_mode == 'MOVING':
          time_in_this_log = (
              logged_progress.timestamp - analyzed_logs[index - 1].timestamp)
          logged_time += time_in_this_log
          if (not printing_time_to_last_progress and logged_progress.progress.printed_bytes.current == last_printed_bytes.current):
            printing_time_to_last_progress = logged_time
            last_progress = logged_progress
          elif printing_time_to_last_progress:
            printing_time_after_last_progress += time_in_this_log

      if logged_time.total_seconds() == 0:
        return None

      bytes_with_logging = last_printed_bytes.current - first_printed_bytes.current
      if bytes_with_logging == 0:
        if first_printed_bytes.current == 0:
          return EstimatedPrintingTimes(
              completed_seconds=logged_time.total_seconds(),
              remaining_seconds=None)
        else:
          return None

      printing_rate = printing_time_to_last_progress / bytes_with_logging
      total_time = printing_rate * last_printed_bytes.total
      completed_time = printing_rate * last_progress.progress.printed_bytes.current + min(printing_rate, printing_time_after_last_progress)
      return EstimatedPrintingTimes(
          completed_seconds=completed_time.total_seconds(),
          remaining_seconds=max(0, (total_time - completed_time).total_seconds()))

class CameraStream:
  def __init__(self, ip):
    self._ip = ip
    self._queues = []
    self._lock = threading.RLock()
    self._camera_connection = None
    self._camera_response = None
    self._reading_thread = None
    self._pending_cleanup = None
    self._reader_thread_shutting_down = threading.Event()

  def consume_camera_bytes(self):
    buffer = bytearray(10240)
    while not self._reader_thread_shutting_down.is_set():
      read_bytes = self._camera_response.readinto(buffer)
      lock_acquired = False
      while not lock_acquired and not self._reader_thread_shutting_down.is_set():
        lock_acquired = self._lock.acquire(timeout=0.1)
        if lock_acquired:
          try:
            for subscriber_queue in self._queues:
              subscriber_queue.put(buffer[:read_bytes])
          finally:
            self._lock.release()

  def start_stream(self, subscriber_queue):
    with self._lock:
      if not self._camera_connection:
        self._connect()
      self._queues.append(subscriber_queue)

      return self._camera_response.headers

  def stop_stream(self, subscriber_queue):
    with self._lock:
      if subscriber_queue in self._queues:
        self._queues.remove(subscriber_queue)

        def maybe_cleanup():
          with self._lock:
            if not self._queues:
              self._cleanup()
              self._pending_cleanup = None

        if self._pending_cleanup:
          self._pending_cleanup.cancel()
        self._pending_cleanup = threading.Timer(2, maybe_cleanup)
        self._pending_cleanup.start()

  def reset_stream(self):
    with self._lock:
      self._cleanup()
      _call_with_retry(self._connect, PrinterConnectionError)

  def _connect(self):
    if self._camera_connection:
      raise RuntimError('Already connected')
    try:
      self._camera_connection = http.client.HTTPConnection(self._ip, 8080)
      self._camera_connection.request("GET", "/?action=stream")
      self._camera_response = self._camera_connection.getresponse()
      self._reading_thread = threading.Thread(
          target=self.consume_camera_bytes,
      )
      self._reading_thread.start()
    except Exception as e:
      self._cleanup()
      if isinstance(e, OSError):
        raise PrinterConnectionError('Error setting up camera connection') from e
      raise

  def _cleanup(self):
    try:
      self._reader_thread_shutting_down.set()
      if self._camera_response:
        self._camera_response.close()
      if self._camera_connection:
        self._camera_connection.close()
      if self._reading_thread:
        self._reading_thread.join()
      self._reading_thread = None
      self._camera_response = None
      self._camera_connection = None
      self._reader_thread_shutting_down.clear()
    except:
      logging.exception('error cleaning up connection') 
  
def _call_with_retry(retried, retried_exception_classes=(Exception)):
  sleeps = [.1, .25, .75]
  while True:
    try:
      return retried()
    except retried_exception_classes as e:
      if not sleeps:
        raise e
      time.sleep(sleeps.pop())
