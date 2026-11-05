import os
import threading

from document_import import ImportProblem, parse_document


class IngestionWorker:
    def __init__(self, store):
        self.store = store
        self.event = threading.Event()
        self.thread = None
        self.lock = None

    def run_once(self):
        job = self.store.claim_import()
        if not job:
            return False
        try:
            result = parse_document(job["filename"], bytes(job["file_bytes"]))
            self.store.finish_import(job["id"], result)
        except ImportProblem as error:
            self.store.fail_import(job["id"], error.code, error.message)
        except Exception:
            self.store.fail_import(job["id"], "IMPORT_FAILED", "解析未完成；请重试或换用普通文本文件。")
        return True

    def start(self):
        import fcntl
        if self.thread:
            return True
        descriptor = os.open(self.store.path.parent / "ingestion.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return False
        self.lock = descriptor
        def run():
            try:
                while not self.event.is_set():
                    try:
                        worked = self.run_once()
                    except Exception:
                        worked = False
                    if not worked:
                        self.event.wait(0.5)
            finally:
                os.close(descriptor)
                self.lock = None
        self.thread = threading.Thread(target=run, name="workbench-import", daemon=True)
        try:
            self.store.recover_imports()
            self.thread.start()
        except Exception:
            os.close(descriptor)
            self.lock = None
            self.thread = None
            raise
        return True

    def stop(self):
        self.event.set()
        if self.thread:
            self.thread.join(timeout=3)
