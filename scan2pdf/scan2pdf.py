#!/usr/bin/env python3

"""scan2pdf

Usage:
  scan2pdf -L
  scan2pdf --create-configuration DEVICE [CONFIG]
  scan2pdf [--debug] [-C CONFIG] DEVICE [TARGET]

Options:
  -L, --list-devices                     show available scanner devices
  DEVICE                                 device to use for scanning
  TARGET                                 target filename for scan
  CONFIG                                 configuration file
  -C <CONFIG>, --configuration <CONFIG>  configuration options in JSON format
  --debug                                print debug information on error
  --create-configuration                 create a configuration file with defaults
"""

import sys
import json
import os
import os.path
import ctypes
import re
import io

from copy import deepcopy
from collections import OrderedDict

from docopt import docopt
import pyinsane.abstract as pyinsane

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.pagesizes import inch, cm, A4
from reportlab.platypus import Image as LabImage


class Error(Exception):
    '''
    Generic Error class.
    Message should be end-user readable.
    '''
    def __init__(self, message, *args, **kwargs):
        super().__init__(*args)

        self.message = message
        for name,value in kwargs.items():
            setattr(self, name, value)

    def __str__(self):
        return self.message

def main(cmdline):
    global __doc__
    cmdline = docopt(__doc__, version='scan2pdf 0.1.0')

    try:
        if cmdline['--list-devices']:
            main_list_devices(cmdline)
        elif cmdline['--create-configuration']:
            main_create_configuration(cmdline)
        else:
            main_scan(cmdline)
    except Error as ex:
        print('Error:', ex.message, file=sys.stderr)
        if cmdline['--debug'] and hasattr(ex, 'inner'):
            raise ex.inner
        else:
            sys.exit(-1)

def main_list_devices(cmdline):
    try:
        devices = pyinsane.get_devices()
    except Exception as ex:
        raise Error('Unable to list devices. Is sane installed?', inner=ex)

    if len(devices) == 0:
        raise Error('no devices found')

    for device in devices:
        print(device.name)

def main_create_configuration(cmdline):
    device = pyinsane.Scanner(name=cmdline['DEVICE'])

    try:
        device._open()
    except Exception as ex:
        raise Error('Unable to open device "%s"' % device.name, inner=ex)


    def iter_options():
        try:
            nb_options = pyinsane.rawapi.sane_get_option_value(pyinsane.sane_dev_handle[1], 0)

            for opt_idx in range(1, nb_options):
                opt_desc = pyinsane.rawapi.sane_get_option_descriptor(pyinsane.sane_dev_handle[1], opt_idx)
                opt = pyinsane.ScannerOption.build_from_rawapi(device, opt_idx, opt_desc)
                yield opt

        except Exception as ex:
            raise Error('Unable to retrieve options for device "%s"' % device.name, inner=ex)

    def iter_filtered_options():
        grp = None

        for opt in iter_options():

            # group
            if opt.val_type == pyinsane.rawapi.SaneValueType.GROUP:
                grp = opt
                continue

            # if both of these are set, option is invalid
            if (opt.capabilities._SaneFlags__flags & pyinsane.SaneCapabilities.SOFT_SELECT) and (opt.capabilities._SaneFlags__flags & pyinsane.SaneCapabilities.HARD_SELECT):
                continue

            # invalid to select but not detect
            if (opt.capabilities._SaneFlags__flags & pyinsane.SaneCapabilities.SOFT_SELECT) and not (opt.capabilities._SaneFlags__flags & pyinsane.SaneCapabilities.SOFT_DETECT):
                continue

            # standard allows this, though it makes little sense
            # if (opt.capabilities._SaneFlags__flags & pyinsane.SaneCapabilities.HARD_SELECT) and not (opt.capabilities._SaneFlags__flags & pyinsane.SaneCapabilities.SOFT_DETECT):
            #    continue

            # if one of these three is not set, option is useless, skip it
            if not (opt.capabilities._SaneFlags__flags & (pyinsane.SaneCapabilities.SOFT_SELECT | pyinsane.SaneCapabilities.HARD_SELECT | pyinsane.SaneCapabilities.SOFT_DETECT)):
                continue

            # only worry about settable values
            if not (opt.capabilities._SaneFlags__flags & pyinsane.SaneCapabilities.SOFT_SELECT):
                continue

            # yield group with first valid option
            if grp is not None:
                yield grp
                grp_idx, grp_desc, grp = None, None, None

            # yield option
            yield opt

    def iter_config():

        yield '# configuration options for %s' % device.name
        yield ''

        for opt in iter_filtered_options():

            # group
            if opt.val_type == pyinsane.rawapi.SaneValueType.GROUP:
                yield '[%(name)s]' % dict(
                    name = opt.name
                )
                continue

            # option

            yield '# %(title)s' % dict(
                title = opt.title
            )
            yield '# %(desc)s' % dict(
                desc = opt.desc
            )

            valid = ''
            if opt.val_type == pyinsane.rawapi.SaneValueType.BOOL:
                valid = 'yes|no'
            elif opt.val_type != pyinsane.rawapi.SaneValueType.BUTTON:

                valid = ''
                if opt.constraint_type == pyinsane.rawapi.SaneConstraintType.NONE:
                    if opt.val_type == pyinsane.rawapi.SaneValueType.INT:
                        valid = '<int>'
                    elif opt.val_type == pyinsane.rawapi.SaneValueType.FIXED:
                        valid = '<float>'
                    elif opt.val_type == pyinsane.rawapi.SaneValueType.STRING:
                        valid = '<string>'

                    if opt.val_type != pyinsane.rawapi.SaneValueType.STRING and opt.size > ctypes.sizeof(ctypes.c_int):
                        valid = valid + ',...'

                elif opt.constraint_type == pyinsane.rawapi.SaneConstraintType.RANGE:
                    # ToDo: see scanimage. might need to adjust x and y
                    valid_from, valid_to, valid_step = opt.constraint
                    valid_unit = get_unit(opt.unit._SaneEnum__value)

                    if opt.val_type != pyinsane.rawapi.SaneValueType.STRING and opt.size > ctypes.sizeof(ctypes.c_int):
                        valid_extra = ',...'
                    else:
                        valid_extra = ''

                    if opt.val_type == pyinsane.rawapi.SaneValueType.INT:
                        valid = '%(from_)d..%(to)d%(unit)s%(extra)s (in steps of %(step)d)' % dict(
                            from_ = valid_from,
                            to = valid_to,
                            extra = valid_extra,
                            step = valid_step,
                            unit = valid_unit
                        )
                    else:
                        valid = '%(from_)g..%(to)g%(unit)s%(extra)s (in steps of %(step)g)' % dict(
                            from_ = unfix(valid_from),
                            to = unfix(valid_to),
                            extra = valid_extra,
                            step = unfix(valid_step),
                            unit = valid_unit
                        )
                elif opt.constraint_type == pyinsane.rawapi.SaneConstraintType.WORD_LIST:
                    if opt.val_type == pyinsane.rawapi.SaneValueType.INT:
                        valid_words = ('%d' % word for word in opt.constraint)
                    else:
                        valid_words = ('%g' % unfix(word) for word in opt.constraint)
                    valid = '|'.join(valid_words)

                    if opt.val_type != pyinsane.rawapi.SaneValueType.STRING and opt.size > ctypes.sizeof(ctypes.c_int):
                        valid = valid + ',...'
                elif opt.constraint_type == pyinsane.rawapi.SaneConstraintType.STRING_LIST:
                    valid = '|'.join('%r' % string for string in opt.constraint)


            if opt.capabilities._SaneFlags__flags & pyinsane.SaneCapabilities.AUTOMATIC:
                valid = 'auto|' + valid

            flags = []
            if (opt.capabilities._SaneFlags__flags & pyinsane.SaneCapabilities.INACTIVE):
                flags.append('[inactive]')
            if (opt.capabilities._SaneFlags__flags & pyinsane.SaneCapabilities.HARD_SELECT):
                flags.append('[hardware]')
            if not (opt.capabilities._SaneFlags__flags & pyinsane.SaneCapabilities.SOFT_SELECT) and (opt.capabilities._SaneFlags__flags & pyinsane.SaneCapabilities.SOFT_DETECT):
                flags.append('[read-only]')

            if len(flags) != 0:
                flags = ' ' + ' '.join(flags)
            else:
                flags = ''


            yield '# %(name)s = %(valid)s%(flags)s' % dict(
                name = opt.name,
                valid = valid,
                flags = flags
            )

            if opt.val_type == pyinsane.rawapi.SaneValueType.STRING or opt.size == ctypes.sizeof(ctypes.c_int):
                if not (opt.capabilities._SaneFlags__flags & pyinsane.SaneCapabilities.INACTIVE):
                    value = ''
                    if opt.val_type == pyinsane.rawapi.SaneValueType.BOOL:
                        value = 'yes' if opt.val_type else 'no'
                    elif opt.val_type == pyinsane.rawapi.SaneValueType.INT:
                        # ToDo: see scanimage
                        value = '%d' % opt.value
                    elif opt.val_type == pyinsane.rawapi.SaneValueType.FIXED:
                        # ToDo: see scanimage
                        value = '%g' % unfix(opt.value)
                    elif opt.val_type == pyinsane.rawapi.SaneValueType.STRING:
                        value = '%r' % opt.value

                    yield '%(name)s = %(value)s' % dict(
                        name = opt.name,
                        value = value
                    )
                else:
                    yield '# %(name)s = ' % dict(
                        name = opt.name
                    )
            else:
                yield '# %(name)s = ' % dict(
                    name=opt.name
                )

            yield ''

    def get_unit(value):
        try:
            return ['', 'pixel', 'bit', 'mm', 'dpi', '%', 'µs'][value]
        except IndexError as ex:
            return ''

    def unfix(value):
        return float(value) / (1 << 16)

    if cmdline['<CONFIG>'] is not None:
        filename = cmdline['<CONFIG>']
        try:
            fp = open(filename, 'wt')
        except Exception as ex:
            raise Error('Unable to write configuration file "%s"' % filename, inner=ex)

        with fp:
            for line in iter_config():
                print(line, file=fp)
    else:
        for line in iter_config():
            print(line)

def main_scan(cmdline):
    device = pyinsane.Scanner(name=cmdline['DEVICE'])
    try:
        device._open()
    except Exception as ex:
        raise Error('Unable to open device "%s"' % device.name, inner=ex)

    apply_configuration(cmdline, device)

    def iter_scan():
        try:
            session = device.scan(multiple=True)
        except StopIteration:
            raise Error('Nothing to scan')

        while True:
            try:
                session.scan.read()
            except EOFError:
                img = session.images[-1]

                # Set DPI if possible
                if 'dpi' not in img.info and 'resolution' in device.options:
                    img.info['dpi'] = (device.options['resolution'].value, device.options['resolution'].value)

                yield img
            except StopIteration:
                return

    images2pdf(iter_scan(), cmdline['TARGET'])

def apply_configuration(cmdline, device):
    filename = cmdline['--configuration']

    if filename is None:
        return

    def iter_settings():
        try:
            fp = open(filename, 'r')
        except Exception as ex:
            raise Error('Unable to read configuration file "%s"' % filename, inner=ex)

        re_comment = re.compile(r'^#.*$')
        re_empty = re.compile(r'^\s*$')
        re_group = re.compile(r'^\s*\[(?P<group>.*)\]\s*$')
        re_option = re.compile(r'^\s*(?P<name>.*?)\s*=\s*(?P<value>.*?)\s*$')

        with fp:
            for iline, line in enumerate(fp):
                # comment
                if re_comment.match(line):
                    continue

                # empty
                if re_empty.match(line):
                    continue

                # group
                match = re_group.match(line)
                if match:
                    continue

                # option
                match = re_option.match(line)
                if match:
                    yield (match.group('name'), match.group('value'))
                    continue

                raise Error('Invalid syntax on line %d of configuration file "%s"' % (iline + 1, filename))

    re_value_string = re.compile(r'(?P<quote>\"|\')(?P<value>.*)(?P=quote)')

    for name, value in iter_settings():

        if name not in device.options:
            raise Error('Unknown option "%s" in configuration file "%s"' % (name, filename))

        option = device.options[name]

        if value.lower() == 'auto' and (option.capabilities._SaneFlags__flags & pyinsane.SaneCapabilities.AUTOMATIC):
            pass
        elif option.val_type == pyinsane.rawapi.SaneValueType.BOOL:
            value = value.lower()
            if value == 'yes':
                value = True
            elif value == 'no':
                value = False
            else:
                raise Error('invalid value for option "%s" in configuration file "%s"' % (name, filename))

        elif option.val_type == pyinsane.rawapi.SaneValueType.INT:
            try:
                value = int(value)
            except ValueError as ex:
                raise Error('invalid value for option "%s" in configuration file "%s"' % (name, filename), inner = ex)
        elif option.val_type == pyinsane.rawapi.SaneValueType.FIXED:
            try:
                value = float(value)
            except ValueError as ex:
                raise Error('invalid value for option "%s" in configuration file "%s"' % (name, filename), inner = ex)

            value = int(value*(1 << 16))
        elif option.val_type == pyinsane.rawapi.SaneValueType.STRING:
            match = re_value_string.match(value)
            if match is None:
                raise Error('invalid value for option "%s" in configuration file "%s"' % (name, filename), inner=ex)

            value = match.group('value')
            value = bytes(value, 'utf-8').decode('unicode_escape')
        else:
            continue

        try:
            option.value = value
        except Exception as ex:
            raise Error('unable to set option "%s"', inner=ex)

def pil2lab(img):
    dpiw, dpih = img.info['dpi'] if 'dpi' in img.info else (1, 1)

    buf = io.BytesIO()
    img.save(buf, 'TIFF')
    buf.seek(0)
    return LabImage(buf, width=float(img.width) / dpiw * inch, height=float(img.height) / dpih * inch)

def images2pdf(images, filename):
    images = iter(images)

    try:
        img = next(images)
    except StopIteration:
        raise Error('Nothing scanned')

    img = pil2lab(img)

    doc = SimpleDocTemplate(
        filename,
        pagesize=(
            img._width + 1.5 * cm,
            img._height + 2.0 * cm
        ),
        showBoundary=1,
        leftMargin=0.5 * cm, rightMargin=0.5 * cm, topMargin=0.5 * cm, bottomMargin=1.0 * cm
        # leftMargin=0, rightMargin=0, topMargin=0, bottomMargin=0
    )

    def iter_flowables():
        nonlocal img
        yield img

        while True:
            try:
                img = next(images)
            except StopIteration:
                break

            img = pil2lab(img)

            yield PageBreak()
            yield img

    # ToDo: Change this into something that can consume less memory?
    doc.build(list(iter_flowables()))

if __name__ == '__main__':
    main()