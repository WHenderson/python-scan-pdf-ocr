scan2pdf
========

Usage:
  scan2pdf -L
  scan2pdf --create-configuration DEVICE [CONFIG]
  scan2pdf [--debug] [-C CONFIG] DEVICE TARGET

Options:
  -L, --list-devices                     show available scanner devices
  DEVICE                                 device to use for scanning
  TARGET                                 target filename for scan
  CONFIG                                 configuration file
  -C <CONFIG>, --configuration <CONFIG>  configuration options in JSON format
  --debug                                print debug information on error
  --create-configuration                 create a configuration file with defaults