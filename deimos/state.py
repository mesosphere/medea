import errno
from fcntl import LOCK_EX, LOCK_NB, LOCK_SH, LOCK_UN
import itertools
import os
import signal
import time

import deimos.docker
from deimos.err import *
from deimos.logger import log
from deimos._struct import _Struct


class State(_Struct):
    def __init__(self, root, docker_id=None, mesos_id=None, task_id=None):
        _Struct.__init__(self, root=os.path.abspath(root),
                               docker_id=docker_id,
                               mesos_id=mesos_id,
                               task_id=task_id)
    def resolve(self, *args, **kwargs):
        if self.mesos_id is not None:
            return self._mesos(*args, **kwargs)
        else:
            return self._docker(*args, **kwargs)
    def mesos_container_id(self):
        if self.mesos_id is None:
            self.mesos_id = self._readf("mesos-container-id")
        return self.mesos_id
    def tid(self):
        if self.task_id is None:
            self.task_id = self._readf("tid")
        return self.task_id
    def sandbox_symlink(self, value=None):
        p = self.resolve("fs")
        if value is not None:
            link(value, p)
        return p
    def pid(self, value=None):
        if value is not None:
            self._writef("pid", str(value))
        data = self._readf("pid")
        if data is not None:
            return int(data)
    def cid(self, refresh=False):
        if self.docker_id is None or refresh:
            self.docker_id = self._readf("cid")
        return self.docker_id
    def await_cid(self, seconds=60):
        base   = 0.05
        start  = time.time()
        steps  = [ 1.0, 1.25, 1.6, 2.0, 2.5, 3.2, 4.0, 5.0, 6.4, 8.0 ]
        scales = ( 10.0 ** n for n in itertools.count() )
        scaled = ( [scale * step for step in steps] for scale in scales )
        sleeps = itertools.chain.from_iterable(scaled)
        log.info("Awaiting CID file: %s", self.resolve("cid"))
        while self.cid(refresh=True) in [None, ""]:
            time.sleep(next(sleeps))
            if time.time() - start >= seconds:
                raise CIDTimeout("No CID file after %ds" % seconds)
    def await_launch(self):
        lk_l = self.lock("launch", LOCK_SH)
        self.ids(3)
        if self.cid() is None:
            lk_l.unlock()
            self.await_cid()
            lk_l = self.lock("launch", LOCK_SH)
        return lk_l
    def lock(self, name, flags, seconds=60):
        formatted = deimos.flock.format_lock_flags(flags)
        flags, seconds = deimos.flock.nb_seconds(flags, seconds)
        log.info("request // %s %s (%ds)", name, formatted, seconds)
        p = self.resolve(os.path.join("lock", name), mkdir=True)
        lk = deimos.flock.LK(p, flags, seconds)
        try:
            lk.lock()
        except deimos.flock.Err:
            log.error("failure // %s %s (%ds)", name, formatted, seconds)
            raise
        if (flags & LOCK_EX) != 0:
            lk.handle.write(isonow() + "\n")
        log.info("success // %s %s (%ds)", name, formatted, seconds)
        return lk
    def exit(self, value=None):
        if value is not None:
            self._writef("exit", str(value))
        else:
            data = self._readf("exit")
            if data is not None:
                return deimos.docker.read_wait_code(data)
    def push(self):
        self._mkdir()
        properties = [("cid", self.docker_id),
                      ("mesos-container-id", self.mesos_id),
                      ("tid", self.task_id)]
        for k, v in properties:
            if v is not None and not os.path.exists(self.resolve(k)):
                self._writef(k, v)
        if self.cid() is not None:
            docker = os.path.join(self.root, "docker", self.cid())
            link("../mesos/" + self.mesos_id, docker)
    def _mkdir(self):
        create(os.path.join(self.root, "mesos", self.mesos_id))
    def _readf(self, path):
        f = self.resolve(path)
        if os.path.exists(f):
            with open(f) as h:
                return h.read().strip()
    def _writef(self, path, value):
        f = self.resolve(path)
        with open(f, "w+") as h:
            h.write(value + "\n")
    def _docker(self, path, mkdir=False):
        p = os.path.join(self.root, "docker", self.docker_id, path)
        p = os.path.abspath(p)
        if mkdir:
            docker = os.path.join(self.root, "docker", self.docker_id)
            if not os.path.exists(docker):
                log.error("No Docker symlink (this should be impossible)")
                raise Err("Bad Docker symlink state")
            create(os.path.dirname(p))
        return p
    def _mesos(self, path, mkdir=False):
        p = os.path.join(self.root, "mesos", self.mesos_id, path)
        p = os.path.abspath(p)
        if mkdir:
            create(os.path.dirname(p))
        return p
    def ids(self, height=2):
        log = deimos.logger.logger(height)
        if self.tid() is not None:
            log.info("task   = %s", self.tid())
        if self.mesos_container_id() is not None:
            log.info("mesos  = %s", self.mesos_container_id())
        if self.cid() is not None:
            log.info("docker = %s", self.cid())

class CIDTimeout(Err): pass

def create(path):
    if not os.path.exists(path):
        os.makedirs(path)

def link(source, target):
    if not os.path.exists(target):
        create(os.path.dirname(target))
        os.symlink(source, target)

def isonow():
    t   = time.time()
    ms  = ("%0.03f" % (t % 1))[1:]
    iso = time.strftime("%FT%T", time.gmtime(t))
    return iso + ms + "Z"

