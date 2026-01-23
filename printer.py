import functools
import http.client
import logging
import re
import socket
import threading
import time

from typing import Any, Callable, Union
from dataclasses import dataclass

class Printer:
  def __init__(self, ip):
    self._ip = ip
    self._camera_stream = CameraStream(self._ip)
    self._lock = threading.Lock()

  def get_info(self):
    return self._call_rpc(INFO_RPC)

  def get_head_position(self):
    return self._call_rpc(HEAD_POSITION_RPC)

  def get_temp(self):
    return self._call_rpc(GET_TEMP_RPC)

  def get_progress(self):
    return self._call_rpc(PROGRESS_RPC)

  def get_status(self):
    return self._call_rpc(STATUS_RPC)

  def pause(self):
    self._call_rpcs([
      REQUEST_CONTROL_RPC,
      PAUSE_RPC,
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

  def _address(self):
    return {'ip': self._ip, 'port': 8899}

  def _call_rpc(self, rpc):
    return self._call_rpcs([rpc])[0]

  def _call_rpcs(self, rpcs):
    printer_socket = socket.socket()
    printer_socket.settimeout(5)
    printer_socket.connect((self._ip, 8899))
    responses = []
    for rpc in rpcs:
      printer_socket.send(rpc.request.encode())
      data = printer_socket.recv(1024)
      responses.append(rpc.response_parser(data.decode()))
    printer_socket.close()
    return responses

@dataclass
class PrinterRpc:
  request: str
  response_parser: Callable[[str], Any]

def parse_field_with_regex(response_text, regex_str, post_processor):
  regex = re.compile(regex_str)
  for line in response_text.splitlines():
    field_match = regex.search(line)
    if field_match:
      return post_processor(field_match.group(1))
  return None

def parse_delimited_field(response_text, field_name, include_colon=True, delimiter=' ', post_processor=str):
  optional_colon = ':' if include_colon else ''
  return parse_field_with_regex(
      response_text,
      f'(?:^|{delimiter}){field_name}{optional_colon} ?([^{delimiter}]+)(?:{delimiter}|$)',
      post_processor)

def parse_single_line_field(response_text, field_name, include_colon=True, post_processor=str):
  optional_colon = ':' if include_colon else ''
  return parse_field_with_regex(response_text, f'^{field_name}{optional_colon} ?(.*)$', post_processor=post_processor)

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
          x = parse_delimited_field(response_text, 'X', post_processor=int),
          y = parse_delimited_field(response_text, 'Y', post_processor=int),
          z = parse_delimited_field(response_text, 'Z', post_processor=int)
        ),
        serial_number = parse_single_line_field(response_text, 'SN'),
        tool_count = parse_single_line_field(response_text, 'Tool Count', int),
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
          x = parse_delimited_field(response_text, 'X', post_processor=float),
          y = parse_delimited_field(response_text, 'Y', post_processor=float),
          z = parse_delimited_field(response_text, 'Z', post_processor=float)
        )
    )

@dataclass
class Temp:
  current: float
  target: float

  def parse(field_text):
    split = field_text.split('/')
    if len(split) != 2:
      return None
    target = float(split[1])
    return Temp(
        current=float(split[0]),
        target=target or None
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
      nozzle = parse_delimited_field(response_text, 'T0', post_processor=Temp.parse),
      bed = parse_delimited_field(response_text, 'B', post_processor=Temp.parse)
    )

@dataclass
class Progress:
  current: int
  total: int

  def parse(field_value):
    split = field_value.split('/')
    if len(split) != 2:
      return None
    return Progress(current=int(split[0]), total=int(split[1]))

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
      printed_bytes = parse_single_line_field(response_text, 'SD printing byte', include_colon=False, post_processor=Progress.parse),
      layers = parse_single_line_field(response_text, 'Layer', post_processor=Progress.parse)
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
      led=parse_single_line_field(response_text, 'LED', post_processor=bool),
      current_file=parse_single_line_field(response_text, 'CurrentFile')
    )

@dataclass
class EmptyResponse:
  def parse(response_text):
    print(f'Ignoring response: {response_text}')
    return EmptyResponse()

  def validating_parser(success_regex):
    def parse(response_text):
      print(f'checking for {success_regex} in {response_text}')
      if not success_regex.search(response_text):
        print('not found')
        raise Exception(f'Command failed: {response_text}')
      print('found')
      return EmptyResponse()
    return parse


INFO_RPC = PrinterRpc('~M115\r\n', InfoResponse.parse)
HEAD_POSITION_RPC = PrinterRpc('~M114\r\n', HeadPositionResponse.parse)
GET_TEMP_RPC = PrinterRpc('~M105\r\n', TempResponse.parse)
PROGRESS_RPC = PrinterRpc('~M27\r\n', ProgressResponse.parse)
STATUS_RPC = PrinterRpc('~M119\r\n', StatusResponse.parse)
PAUSE_RPC = PrinterRpc('~M25\r\n', EmptyResponse.parse)
ABORT_RPC = PrinterRpc('~M26\r\n', EmptyResponse.parse)
REQUEST_CONTROL_RPC = PrinterRpc('~M601 S1\r\n', EmptyResponse.validating_parser(re.compile('Control Success')))
RELEASE_CONTROL_RPC = PrinterRpc('~M602\r\n', EmptyResponse.parse)

class CameraStream:
  def __init__(self, ip):
    self._ip = ip
    self._queues = []
    self._lock = threading.RLock()
    self._camera_connection = None
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
      _call_with_retry(self._connect)

  def _connect(self):
    if self._camera_connection:
      raise Exception('Already connected')
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
      raise Exception('Error setting up camera connection') from e

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
  
def _call_with_retry(retried):
  sleeps = [.1, .25, .75]
  while True:
    try:
      return retried()
    except Exception as e:
      if not sleeps:
        raise e
      time.sleep(sleeps.pop())

  for sleep in [0, 100, 250, 750]:
    time.sleep(sleep)


