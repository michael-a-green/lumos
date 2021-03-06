"""Network-based interfaces, including RPC-enabled components."""

from __future__ import print_function

import time
import logging
import numpy as np
import cv2

from .context import Context
from .input import InputDevice
from .output import OutputDevice
import rpc


class ImageServer(OutputDevice):
  """An image output device that posts the latest written image over RPC."""
  
  default_port = 61616
  default_read_call = 'ImageServer.read'
  wait_interval = 0.1  # s; time to sleep while waiting for first valid image
  max_wait_duration = 2.0  # s; total time to wait for valid image
  
  def __init__(self, port=default_port, start_server=True, *args, **kwargs):
    OutputDevice.__init__(self)
    self.isFresh = True  # used to prevent clients from getting a None as the first image
    rpc.export(self)  # NOTE prepends class name to all RPC-enabled method names
    if start_server:
      rpc.start_server_thread(port=port, *args, **kwargs)
  
  @rpc.enable_image  # NOTE implicitly specifies RPC call name to be the same as function name
  def read(self):
    if self.isFresh:
      waitStarted = time.time()
      while self.image is None and (time.time() - waitStarted) < self.max_wait_duration:
        time.sleep(self.wait_interval)
      self.isFresh = False
    return self.image
  
  def stop(self):
    self.image = None  # so that anyone requesting in the meantime will get an indication that we're done
    self.isFresh = True  # can be useful if we want to reset an existing ImageServer
    rpc.stop_server()
  
  def __enter__(self):
    return self
  
  def __exit__(self, *_):
    self.stop()


class ImageClient(rpc.Client):
  """A lightweight client exposing a simple read() method for retrieving images from a remote RPC server."""
  
  image_recv_timeout = 10000  # have a relatively large timeout to allow remote servers to start, but prevent getting hung up at the end
  
  def __init__(self, read_call=ImageServer.default_read_call, port=ImageServer.default_port, timeout=image_recv_timeout, *args, **kwargs):
    rpc.Client.__init__(self, port=port, timeout=timeout, *args, **kwargs)
    self.logger = logging.getLogger(self.__class__.__name__)
    self.read_call = read_call
  
  def read(self):
    #self.logger.debug("REQ: %s", read_call)  # [verbose]
    image = self.call(self.read_call)
    if isinstance(image, np.ndarray):
      #self.logger.debug("REP[image]: shape: {}, dtype: {}".format(image.shape, image.dtype))  # [verbose]
      return True, image
    else:
      #self.logger.debug("REP[unknown]:", str(image))  # [verbose]
      return False, None
  
  def release(self):
    # Try to match VideoCapture API, somewhat
    self.close()


class EventLogger(object):
  """A simple timed event logger that dumps incoming log messages to a flat file with a prefixed stream ID and common logger time."""
  
  default_port = 62626
  default_sep = "\t"
  timestamp_format = '%Y-%m-%d_%H-%M-%S'  # used to generate filenames
  
  def __init__(self, filename=None, sep=default_sep, port=default_port, rpc_export=True, start_server=True, *args, **kwargs):
    self.filename = filename
    self.sep = sep
    self.port = port
    self.serverStarted = False
    
    self.initTime = time.time()
    self.logger = logging.getLogger(self.__class__.__name__)
    if self.filename is None:
      self.filename = "logs/events_{}.log".format(time.strftime(self.timestamp_format, time.localtime(self.initTime)))
    try:
      self.out = open(self.filename, 'w')
      if rpc_export:
        rpc.export(self)  # NOTE prepends class name to all RPC-enabled method names
        if start_server:
          rpc.start_server_thread(port=self.port, *args, **kwargs)
          self.serverStarted = True
    except Exception as e:
      self.logger.error("Error opening log file or starting RPC server: %s", str(e))
    self.logger.info("Logger successfully initialized; filename: %s", self.filename)
  
  @rpc.enable
  def log(self, tag, msg):
    print(tag, time.time(), msg, sep=self.sep, file=self.out)  # log absolute time; for relative time, use: time.time() - self.initTime
  
  def stop(self, stop_server=True):
    if self.out is not None:
      self.out.close()
    if stop_server and self.serverStarted:
      rpc.stop_server()
    self.logger.info("Logger stopped")
  
  def __enter__(self):
    return self
  
  def __exit__(self, *_):
    self.stop()


def image_server(inputDevice=None, port=ImageServer.default_port, *args, **kwargs):
  """A demo RPC server that exposes a method to retrieve images from a given InputDevice."""
  if inputDevice is None:
    Context.createInstance()  # ensure we have a context
    inputDevice = InputDevice()  # picks up options from context
  with ImageServer(port=port, *args, **kwargs) as imageServer:  # starts RPC server by default
    while True:
      try:
        if not inputDevice.read():
          break
        imageServer.write(inputDevice.image)
      except KeyboardInterrupt:
        break


def image_client(read_call=ImageServer.default_read_call, port=ImageServer.default_port, gui=True, delay=20, *args, **kwargs):
  """A demo client that repeatedly makes RPC calls to get an image and display it (must have server running)."""
  
  logger = logging.getLogger('image_client')
  
  with ImageClient(read_call=read_call, port=port, *args, **kwargs) as imageClient:
    logger.info("Starting display loop")
    while True:
      try:
        isOkay, image = imageClient.read()
        if not isOkay:  # no reply/timeout
          break
        
        # NOTE Qt (and possibly other backends) can only display from the main thread of a process
        if gui:
          cv2.imshow("image_client", image)
          key = cv2.waitKey(delay)
          if key & 0x00007f == 0x1b:
            break
      except KeyboardInterrupt:
        break
  
  logger.info("Done.")


def event_logger():
  Context.createInstance()  # ensure we have a context
  with EventLogger() as eventLogger:  # starts RPC server by default
    while True:
      try:
        time.sleep(60.0)
      except KeyboardInterrupt:
        break


if __name__ == "__main__":
  choices = [('--image_server', "Run an image server (from output module)"),
             ('--image_client', "Run an image client"),
             ('--event_logger', "Run an event logger (server)")]
  context = Context.createInstance(parent_argparsers=[Context.createChoiceParser(choices)])
  if context.options.image_server:
    image_server()
  elif context.options.image_client:
    image_client()
  elif context.options.event_logger:
    event_logger()
  else:
    print("Usage: python -m", __loader__.fullname, "[", " | ".join(choice[0] for choice in choices), "]")
