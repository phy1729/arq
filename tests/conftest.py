import asyncio
import contextlib
import io
import logging
import os

import pytest
import aioredis

from .fixtures import TestActor, MockRedisTestActor, MockRedisWorker


@contextlib.contextmanager
def loop_context(existing_loop=None):
    if existing_loop:
        # loop already exists, pass it straight through
        yield existing_loop
    else:
        _loop = asyncio.new_event_loop()

        yield _loop

        _loop.stop()
        _loop.run_forever()
        _loop.close()


def pytest_pycollect_makeitem(collector, name, obj):
    """
    Fix pytest collecting for coroutines.
    """
    if collector.funcnamefilter(name) and asyncio.iscoroutinefunction(obj):
        return list(collector._genfunctions(name, obj))


def pytest_pyfunc_call(pyfuncitem):
    """
    Run coroutines in an event loop instead of a normal function call.
    """
    if asyncio.iscoroutinefunction(pyfuncitem.function):
        existing_loop = pyfuncitem.funcargs.get('loop', None)
        with loop_context(existing_loop) as _loop:
            testargs = {arg: pyfuncitem.funcargs[arg]
                        for arg in pyfuncitem._fixtureinfo.argnames}

            task = _loop.create_task(pyfuncitem.obj(**testargs))
            _loop.run_until_complete(task)

        return True


@pytest.yield_fixture
def loop():
    with loop_context() as _loop:
        yield _loop


@pytest.yield_fixture
def tmpworkdir(tmpdir):
    """
    Create a temporary working working directory.
    """
    cwd = os.getcwd()
    os.chdir(tmpdir.strpath)

    yield tmpdir

    os.chdir(cwd)


@pytest.yield_fixture
def redis_conn(loop):
    async def _get_conn():
        conn = await aioredis.create_redis(('localhost', 6379), loop=loop)
        await conn.flushall()
        return conn
    conn = loop.run_until_complete(_get_conn())
    yield conn

    conn.close()
    loop.run_until_complete(conn.wait_closed())


class StreamLog:
    def __init__(self):
        self.stream = self.handler = None
        self.loggers = []
        self.set_logger()

    def set_logger(self, log_names=('arq.main', 'arq.work'), level=logging.INFO):
        if self.loggers:
            self.finish()
        self.loggers = [logging.getLogger(log_name) for log_name in log_names]
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(stream=self.stream)
        for logger in self.loggers:
            logger.addHandler(self.handler)
        self.set_level(level)

    def set_level(self, level):
        for logger in self.loggers:
            logger.setLevel(level)

    @property
    def log(self):
        self.stream.seek(0)
        return self.stream.read()

    def finish(self):
        for logger in self.loggers:
            logger.removeHandler(self.handler)

    def __contains__(self, item):
        return item in self.log

    def __str__(self):
        return 'logcap:\n' + self.log


@pytest.yield_fixture
def logcap():
    stream_log = StreamLog()

    yield stream_log

    stream_log.finish()


@pytest.yield_fixture
def debug_logger():
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    fmt = logging.Formatter('%(asctime)s %(name)8s %(levelname)8s: %(message)s')
    handler.setFormatter(fmt)
    for logger_name in ('arq.main', 'arq.work'):
        logger = logging.getLogger(logger_name)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

    yield

    for logger_name in ('arq.main', 'arq.work'):
        logger = logging.getLogger(logger_name)
        logger.removeHandler(handler)
        logger.setLevel(logging.NOTSET)


@pytest.yield_fixture
def actor(loop):
    _actor = TestActor(loop=loop)
    yield _actor
    loop.run_until_complete(_actor.close())


@pytest.yield_fixture
def mock_actor(loop):
    _actor = MockRedisTestActor(loop=loop)
    yield _actor
    loop.run_until_complete(_actor.close())


@pytest.yield_fixture
def mock_actor_worker(mock_actor):
    _worker = MockRedisWorker(loop=mock_actor.loop, batch_mode=True)
    _worker.mock_data = mock_actor.mock_data
    yield mock_actor, _worker
    mock_actor.loop.run_until_complete(_worker.close())
