# coding=utf-8
__author__ = "Gina Häußge <osd@foosel.net>"
__license__ = 'GNU Affero General Public License http://www.gnu.org/licenses/agpl.html'

import os
import traceback
import sys
import time
import re
import tempfile
import threading
import logging
from flask import make_response

from octoprint.settings import settings, default_settings


logger = logging.getLogger(__name__)

def getFormattedSize(num):
	"""
	Taken from http://stackoverflow.com/a/1094933/2028598
	"""
	for x in ["bytes","KB","MB","GB"]:
		if num < 1024.0:
			return "%3.1f%s" % (num, x)
		num /= 1024.0
	return "%3.1f%s" % (num, "TB")


def isAllowedFile(filename, extensions):
	return "." in filename and filename.rsplit(".", 1)[1] in extensions


def getFormattedTimeDelta(d):
	if d is None:
		return None
	hours = d.days * 24 + d.seconds // 3600
	minutes = (d.seconds % 3600) // 60
	seconds = d.seconds % 60
	return "%02d:%02d:%02d" % (hours, minutes, seconds)


def getFormattedDateTime(d):
	if d is None:
		return None

	return d.strftime("%Y-%m-%d %H:%M")


def getClass(name):
	"""
	Taken from http://stackoverflow.com/a/452981/2028598
	"""
	parts = name.split(".")
	module = ".".join(parts[:-1])
	m = __import__(module)
	for comp in parts[1:]:
		m = getattr(m, comp)
	return m


def isDevVersion():
	gitPath = os.path.abspath(os.path.join(os.path.split(os.path.abspath(__file__))[0], "../../../.git"))
	return os.path.exists(gitPath)


def getExceptionString():
	locationInfo = traceback.extract_tb(sys.exc_info()[2])[0]
	return "%s: '%s' @ %s:%s:%d" % (str(sys.exc_info()[0].__name__), str(sys.exc_info()[1]), os.path.basename(locationInfo[0]), locationInfo[2], locationInfo[1])


def getGitInfo():
	gitPath = os.path.abspath(os.path.join(os.path.split(os.path.abspath(__file__))[0], "../../../.git"))
	if not os.path.exists(gitPath):
		return (None, None)

	headref = None
	with open(os.path.join(gitPath, "HEAD"), "r") as f:
		headref = f.readline().strip()

	if headref is None:
		return (None, None)

	headref = headref[len("ref: "):]
	branch = headref[headref.rfind("/") + 1:]
	with open(os.path.join(gitPath, headref)) as f:
		head = f.readline().strip()

	return (branch, head)


def getNewTimeout(type):
	now = time.time()

	if type not in default_settings["serial"]["timeout"].keys():
		# timeout immediately for unknown timeout type
		return now

	return now + settings().getFloat(["serial", "timeout", type])


def getFreeBytes(path):
	"""
	Taken from http://stackoverflow.com/a/2372171/2028598
	"""
	if sys.platform == "win32":
		import ctypes
		freeBytes = ctypes.c_ulonglong(0)
		ctypes.windll.kernel32.GetDiskFreeSpaceExW(ctypes.c_wchar_p(path), None, None, ctypes.pointer(freeBytes))
		return freeBytes.value
	else:
		st = os.statvfs(path)
		return st.f_bavail * st.f_frsize


def getRemoteAddress(request):
	forwardedFor = request.headers.get("X-Forwarded-For", None)
	if forwardedFor is not None:
		return forwardedFor.split(",")[0]
	return request.remote_addr


def getDosFilename(input, existingFilenames, extension=None):
	if input is None:
		return None

	if extension is None:
		extension = "gco"

	filename, ext = input.rsplit(".", 1)
	return findCollisionfreeName(filename, extension, existingFilenames)


def findCollisionfreeName(input, extension, existingFilenames):
	filename = re.sub(r"\s+", "_", input.lower().translate({ord(i):None for i in ".\"/\\[]:;=,"}))

	counter = 1
	power = 1
	while counter < (10 * power):
		result = filename[:(6 - power + 1)] + "~" + str(counter) + "." + extension
		if result not in existingFilenames:
			return result
		counter += 1
		if counter == 10 * power:
			power += 1

	raise ValueError("Can't create a collision free filename")


def safeRename(old, new):
	"""
	Safely renames a file.

	On Windows this is achieved by first creating a backup file of the new file (if it
	already exists), thus moving it, then renaming the old into the new file and finally removing the backup. If
	anything goes wrong during those steps, the backup (if already there) will be renamed to its old name and thus
	the operation hopefully result in a no-op.

	On other operating systems the atomic os.rename function will be used instead.

	@param old the path to the old file to be renamed
	@param new the path to the new file to be created/replaced
	"""

	if sys.platform == "win32":
		fh, backup = tempfile.mkstemp()
		os.close(fh)

		try:
			if os.path.exists(new):
				silentRemove(backup)
				os.rename(new, backup)
			os.rename(old, new)
			os.remove(backup)
		except OSError:
			# if anything went wrong, try to rename the backup file to its original name
			logger.error("Could not perform safe rename, trying to revert")
			if os.path.exists(backup):
				os.remove(new)
			os.rename(backup, new)
	else:
		# on anything else than windows it's ooooh so much easier...
		os.rename(old, new)


def silentRemove(file):
	"""
	Silently removes a file. Does not raise an error if the file doesn't exist.

	@param file the path of the file to be removed
	"""

	try:
		os.remove(file)
	except OSError:
		pass


def sanitizeAscii(line):
	return unicode(line, 'ascii', 'replace').encode('ascii', 'replace').rstrip()


def filterNonAscii(line):
	"""
	Returns True if the line contains non-ascii characters, false otherwise

	@param line the line to test
	"""

	try:
		unicode(line, 'ascii').encode('ascii')
		return False
	except ValueError:
		return True


def getJsonCommandFromRequest(request, valid_commands):
	if not "application/json" in request.headers["Content-Type"]:
		return None, None, make_response("Expected content-type JSON", 400)

	data = request.json
	if not "command" in data.keys() or not data["command"] in valid_commands.keys():
		return None, None, make_response("Expected valid command", 400)

	command = data["command"]
	for parameter in valid_commands[command]:
		if not parameter in data:
			return None, None, make_response("Mandatory parameter %s missing for command %s" % (parameter, command), 400)

	return command, data, None


def dict_merge(a, b):
	'''recursively merges dict's. not just simple a['key'] = b['key'], if
	both a and bhave a key who's value is a dict then dict_merge is called
	on both values and the result stored in the returned dictionary.

	Taken from https://www.xormedia.com/recursively-merge-dictionaries-in-python/'''

	from copy import deepcopy

	if not isinstance(b, dict):
		return b
	result = deepcopy(a)
	for k, v in b.iteritems():
		if k in result and isinstance(result[k], dict):
			result[k] = dict_merge(result[k], v)
		else:
			result[k] = deepcopy(v)
	return result


def dict_clean(a, b):

	from copy import deepcopy
	if not isinstance(b, dict):
		return a

	result = deepcopy(a)
	for k, v in a.iteritems():
		if not k in b:
			del result[k]
		elif isinstance(v, dict):
			result[k] = dict_clean(v, b[k])
		else:
			result[k] = deepcopy(v)
	return result


class Object(object):
	pass

def interface_addresses(family=None):
	import netifaces
	if not family:
		family = netifaces.AF_INET

	for interface in netifaces.interfaces():
		try:
			ifaddresses = netifaces.ifaddresses(interface)
		except:
			continue
		if family in ifaddresses:
			for ifaddress in ifaddresses[family]:
				if not ifaddress["addr"].startswith("169.254."):
					yield ifaddress["addr"]

def address_for_client(host, port):
	import socket

	for address in interface_addresses():
		try:
			sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
			sock.bind((address, 0))
			sock.connect((host, port))
			return address
		except Exception as e:
			pass


class CountedEvent(object):

	def __init__(self, value=0, max=None):
		self._counter = 0
		self._max = max
		self._mutex = threading.Lock()
		self._event = threading.Event()

		self._internal_set(value)

	def set(self):
		with self._mutex:
			self._internal_set(self._counter + 1)

	def clear(self, completely=False):
		with self._mutex:
			if completely:
				self._internal_set(0)
			else:
				self._internal_set(self._counter - 1)

	def wait(self, timeout=None):
		self._event.wait(timeout)

	def blocked(self):
		with self._mutex:
			return self._counter == 0

	def _internal_set(self, value):
		self._counter = value
		if self._counter <= 0:
			self._counter = 0
			self._event.clear()
		else:
			if self._max is not None and self._counter > self._max:
				self._counter = self._max
			self._event.set()


