import asyncio
from contextlib import contextmanager
import json
import os
import subprocess
import sys
import time
from .engine import run_job
from .settings import Settings
from .store import Store


@contextmanager
def worker_lock(settings):
    path = settings.prepare().data_dir / "worker.lock"
    handle = path.open("a+b")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                locked = True
            except OSError:
                pass
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except BlockingIOError:
                pass
        yield locked
    finally:
        if locked and os.name == "nt":
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


def ensure_worker(settings):
    heartbeat = settings.data_dir / "worker-heartbeat.json"
    if heartbeat.exists() and time.time()-heartbeat.stat().st_mtime < 10:
        return
    log = (settings.prepare().data_dir / "worker.log").open("ab")
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": log,
              "env": {**os.environ, "RAINBO_SCRAPE_DATA": str(settings.data_dir)}}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([sys.executable, "-m", "rainbo_scrape.cli", "worker"], **kwargs)
    log.close()


async def serve(settings=None, once=False):
    settings = settings or Settings()
    store = Store(settings)
    with worker_lock(settings) as locked:
        if not locked:
            return
        store.recover()
        async def heartbeat():
            path = settings.data_dir / "worker-heartbeat.json"
            while True:
                temp = path.with_suffix(".tmp")
                temp.write_text(json.dumps({"pid": os.getpid(), "ts": time.time()}))
                os.replace(temp, path)
                await asyncio.sleep(2)
        heart = asyncio.create_task(heartbeat())
        try:
            while True:
                item = store.claim_job()
                if item:
                    ident, options = item
                    try:
                        await run_job(store, ident, options)
                    except Exception as exc:
                        store.finish(ident, "failed", type(exc).__name__+": "+str(exc)[:300])
                elif once:
                    return
                else:
                    await asyncio.sleep(0.5)
        finally:
            heart.cancel()
            await asyncio.gather(heart, return_exceptions=True)
