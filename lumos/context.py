import sys
import os
import time
import yaml
import argparse
import logging.config
from pprint import pprint, pformat

from .util import isImageFile, isVideoFile, isRemote


class Context:
  """Application context class to store global data, configuration and objects."""
  
  default_description = "An awesome computer vision application"  # applications should override this when calling Context.createInstance()
  default_base_dir = os.path.dirname(__file__)  # NOTE this context module must be in top-level package
  default_config_filename = os.path.join(default_base_dir, "config.yaml")  # primary configuration file
  alt_config_filename = os.path.join(default_base_dir, "res", "config", "config.yaml")  # configuration file in alternate location
  default_res_path = os.path.join(default_base_dir, "res")  # resource path
  default_log_file = os.path.join(default_base_dir, "logs", "lumos.log")
  default_delay = 10  # delay between subsequent update iterations, in ms
  
  @classmethod
  def createChoiceParser(cls, choices, description="", add_help=False, required=False):
    """Create a simple choice parser (mutually exclusive group) from a list/iterable.
    
    E.g.:
    Context.createChoiceParser(['--apples', '--oranges', '--bananas'])
    Context.createChoiceParser([('--their-algo', 'published in their paper')
                                ('--my-algo', 'the one I worked on this year')])
    
    """
    argParser = argparse.ArgumentParser(add_help=add_help)
    choiceGroup = argParser.add_mutually_exclusive_group(required=required)
    for choice in choices:
      if not isinstance(choice, (tuple, list)):
        choice = (choice,)
      try:
        choiceGroup.add_argument(choice[0],
                                 action='store_true',
                                 help=choice[1] if len(choice) > 1 else "")
      except Exception as e:
        print "Context.createSimpleArgParser(): Unable to add arg: {}".format(e)
    return argParser
  
  @classmethod
  def createInstance(cls, *args, **kwargs):
    if not hasattr(cls, 'instance'):
      cls.instance = Context(*args, **kwargs)
    else:
      print "Context.createInstance(): [WARNING] Context already created."
    return cls.instance
  
  @classmethod
  def getInstance(cls):
    try:
      return cls.instance
    except AttributeError:
      raise Exception("Context.getInstance(): Called before context was created.")
      # TODO: Seriously, find a better way to resolve this - return partial context? parse arguments later? always create an instance on module load?
  
  def __init__(self, argv=None, description=default_description, parent_argparsers=[]):
    """Create a singleton, global application context, parse command-line args (with possible parent parsers passed in), and try to initialize input source parameters."""
    
    # * Ensure singleton
    if hasattr(self.__class__, 'instance'):
      raise Exception("Context.__init__(): Singleton instance already exists!")
    
    # * Setup and parse common command-line arguments
    self.argParser = argparse.ArgumentParser(description=description, parents=parent_argparsers)
    self.argParser.add_argument('--config', dest='config_file', default=self.default_config_filename, help='configuration filename')
    self.argParser.add_argument('--res', dest='res_path', default=self.default_res_path, help='path to resource directory')
    self.argParser.add_argument('--log', dest='log_file', default='auto', help="where to log messages ('none' to turn off logging)")
    self.argParser.add_argument('--debug', action="store_true", help="show debug output?")
    self.argParser.add_argument('--rpc', dest='rpc_port', type=int, nargs='?', default=-1, help="run RPC server at specified (or default) port")
    #self.argParser.add_argument('--gui', action="store_true", help="display GUI interface/windows?")  # use mutually exclusive [--gui | --no_gui] group instead
    guiGroup = self.argParser.add_mutually_exclusive_group()
    guiGroup.add_argument('--gui', dest='gui', action='store_true', default=True, help="display GUI interface/windows?")
    guiGroup.add_argument('--no_gui', dest='gui', action='store_false', default=False, help="suppress GUI interface/windows?")
    self.argParser.add_argument('--delay', dest='delay', type=int, default=None, help="delay between subsequent update iterations, in ms (default: 10ms for GUI mode, none otherwise)")
    self.argParser.add_argument('--loop_video', action="store_true", help="keep replaying video?")
    self.argParser.add_argument('--sync_video', action="store_true", help="synchronize video playback to real-time?")
    self.argParser.add_argument('--video_fps', default='auto', help="desired video frame rate (for sync)")
    self.argParser.add_argument('--camera_width', default='auto', help="desired camera frame width")
    self.argParser.add_argument('--camera_height', default='auto', help="desired camera frame height")
    self.argParser.add_argument('input_source', nargs='?', default='0', help="input image/video/camera device no.")
    self.options = self.argParser.parse_args(argv)  # parse_known_args()?
    if self.options.debug:
      print "Context.__init__(): Options: {}".format(pformat(self.options))
    
    # * Read config file
    self.config = {}
    try:
      with open(self.options.config_file, 'r') as f:
        self.config = yaml.load(f)
    except IOError:
      print "Context.__init__(): Error reading config file: {}".format(self.options.config_file)
      raise
    else:
      if self.options.debug:
        print "Context.__init__(): Loaded configuration: {}".format(pformat(self.config))
    
    # * Obtain resource path and other parameters
    # TODO Provide unified configuration capability with config file and command-line overrides
    self.resPath = os.path.abspath(self.options.res_path)  # NOTE only absolute path seems to work properly
    if self.options.debug:
      print "Context.__init__(): Resource path: {}".format(self.resPath)
    
    # * Setup logging (before any other object is initialized that obtains a logger)
    self.setupLogging()
    
    # * Get a logger instance
    self.logger = logging.getLogger(self.__class__.__name__)
    
    # * Perform any option-dependent initialization
    if self.options.delay is None and self.options.gui:
      self.options.delay = self.default_delay
    
    # * Initialize input source parameters (TODO move this logic into InputDevice?)
    self.isDir = False
    self.isImage = False
    self.isVideo = False
    self.isRemote = False
    self.remoteEndpoint = None
    if self.options.input_source is not None:  # TODO include a way to specify None; currently defaults to device #0
      # ** Obtain camera device no. or input video/image filename
      try:
        self.options.input_source = int(self.options.input_source)  # works if input_source is an int (a device no.)
      except ValueError:
        self.isRemote, self.remoteEndpoint = isRemote(self.options.input_source, parts=True)  # check if this is a network address (endpoint)
        if not self.isRemote:
          self.options.input_source = os.path.abspath(self.options.input_source)  # fallback: treat input_source as string (filename)
          if os.path.exists(self.options.input_source):
            if os.path.isdir(self.options.input_source):
              self.isDir = True
            elif isImageFile(self.options.input_source):
              self.isImage = True
            elif isVideoFile(self.options.input_source):
              self.isVideo = True
            else:
              self.logger.warn("Input source type could not be determined: {}".format(self.options.input_source))
          else:
            self.logger.warn("Input source doesn't exist: {}".format(self.options.input_source))
    
    # * Start RPC server if requested
    self.isRPCEnabled = False
    if self.options.rpc_port != -1:
      import rpc  # import locally to avoid depending on ZMQ and other RPC-related stuff when not needed
      if self.options.rpc_port is None:
        self.options.rpc_port = rpc.default_port
      rpc.start_server_thread(port=self.options.rpc_port)
      self.isRPCEnabled = True
    
    # * Timing
    self.resetTime()
  
  def setupLogging(self):
    if self.options.log_file == 'none':
      if self.options.debug:
        print "Context.setupLogging(): No logging requested; adding dummy handler"
      logging.getLogger().addHandler(logging.NullHandler())  # dummy logger
    else:
      '''
      # ** Load configuration from .conf file [old method, deprecated]
      logConfigFile = self.getResourcePath("config", "logging.conf")  # TODO make this an optional arg
      if self.options.debug:
        print "Context.setupLogging(): Log config file: {}".format(logConfigFile)
      startupDir = os.getcwd()  # save original startup dir
      os.chdir(os.path.dirname(logConfigFile))  # change to log config file's directory (it contains relative paths)
      logging.config.fileConfig(logConfigFile)  # load configuration
      os.chdir(startupDir)  # change back to original startup directory
      '''
      
      # ** Load configuration from .yaml file [new method]
      logConfigFile = self.getResourcePath("config", "logging.yaml")  # TODO make this an optional arg
      if self.options.debug:
        print "Context.setupLogging(): Log config file: {}".format(logConfigFile)
      try:
        with open(logConfigFile, 'r') as f:
          logConfig = yaml.load(f)
        if self.options.log_file == 'auto':
          logConfig['handlers']['file_handler']['filename'] = self.default_log_file
        else:
          logConfig['handlers']['file_handler']['filename'] = self.options.log_file
        if self.options.debug:
          print "Context.setupLogging(): Log config:-"
          pprint(logConfig)
        # TODO Handle log rolling?
        logging.config.dictConfig(logConfig)
      except IOError:
        # Catch missing/incorrect log config file issues
        print "Context.setupLogging(): Error reading log config file: {}; adding dummy handler".format(logConfigFile)
        logging.getLogger().addHandler(logging.NullHandler())  # dummy
        return
      except Exception as e:
        # Catch incorrect file path, permission issues
        print "Context.setupLogging(): Logging setup failed; adding dummy handler: {}".format(e)
        logging.getLogger().addHandler(logging.NullHandler())  # dummy
        return
      
      # ** Tweak root logger configuration based on command-line arguments
      if self.options.debug and logging.getLogger().getEffectiveLevel() > logging.DEBUG:
        logging.getLogger().setLevel(logging.DEBUG)
      elif not self.options.debug and logging.getLogger().getEffectiveLevel() <= logging.DEBUG:
        logging.getLogger().setLevel(logging.INFO)  # one level above DEBUG
        # NOTE Logging level order: DEBUG < INFO < WARN < ERROR < CRITICAL
  
  def resetTime(self):
    self.timeStart = time.time()  # [system time]
    # self.timeStart = cv2.getTickCount() / cv2.getTickFrequency()  # [OpenCV time]
    self.timeNow = 0.0
    self.isPaused = False
    self.timePaused = self.timeStart
  
  def update(self):
    self.timeNow = time.time() - self.timeStart  # [system time]
    # self.timeNow = (cv2.getTickCount() / cv2.getTickFrequency()) - self.timeStart  # [OpenCV time]
  
  def pause(self):
    self.timePaused = time.time()  # [system time]
    # self.ticksPaused = cv2.getTickCount()  # [OpenCV time]
    self.isPaused = True
  
  def resume(self):
    self.timeStart += time.time() - self.timePaused  # [system time]
    # self.timeStart += (cv2.getTickCount() - self.ticksPaused) / cv2.getTickFrequency()  # [OpenCV time]
    self.isPaused = False
  
  def getResourcePath(self, subdir, filename):
    return os.path.abspath(os.path.join(self.resPath, subdir, filename))
