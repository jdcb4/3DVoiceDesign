import json
import queue
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from voicedesign.storage import Conflict, Store

ROOT = Path(__file__).resolve().parent
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class PersistentWorker:
    """One structured-model worker; call only from Engine's serial executor.

    The process is a crash/timeout boundary, not a sandbox. Custom Python never
    enters it. A timed-out or crashed request is failed, never silently replayed.
    """

    def __init__(self, recycle_after=25):
        self.recycle_after = recycle_after
        self.process = None
        self.messages = None
        self.completed = 0

    def _start(self):
        self.messages = queue.Queue()
        self.process = subprocess.Popen(
            [sys.executable, "-m", "voicedesign.worker", "--persistent"],
            cwd=ROOT.parent,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            creationflags=NO_WINDOW,
        )
        process, messages = self.process, self.messages

        def read_messages():
            try:
                for line in process.stdout:
                    messages.put(line)
            finally:
                messages.put(None)

        threading.Thread(target=read_messages, name="cad-worker-output", daemon=True).start()
        self.completed = 0

    def _receive(self, deadline):
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError
        try:
            line = self.messages.get(timeout=remaining)
        except queue.Empty as error:
            raise TimeoutError from error
        if line is None:
            code = self.process.poll()
            raise RuntimeError(f"CAD worker exited unexpectedly (code {code}).")
        try:
            return json.loads(line)
        except ValueError as error:
            raise RuntimeError("CAD worker returned an invalid protocol response.") from error

    def run(self, snapshot, directory, timeout):
        started = time.perf_counter()
        deadline = started + timeout
        cold = self.process is None or self.process.poll() is not None
        if self.completed >= self.recycle_after:
            self.close()
            cold = True
        import_seconds = 0.0
        try:
            if cold:
                self.close()
                self._start()
                ready = self._receive(deadline)
                if ready.get("ready") is not True:
                    raise RuntimeError("CAD worker did not complete startup.")
                import_seconds = ready["import_seconds"]
            request_id = uuid.uuid4().hex
            self.process.stdin.write(
                json.dumps({"id": request_id, "snapshot": str(snapshot), "output": str(directory)})
                + "\n"
            )
            self.process.stdin.flush()
            response = self._receive(deadline)
            if response.get("id") != request_id:
                raise RuntimeError("CAD worker response did not match the current build.")
            self.completed += 1
            return {
                "worker_mode": "persistent",
                "worker_pid": self.process.pid,
                "worker_reused": not cold,
                "import_seconds": import_seconds,
                "cleanup_seconds": response.get("cleanup_seconds", 0),
                "process_seconds": time.perf_counter() - started,
            }
        except Exception:
            self.close()
            raise

    def close(self):
        process, self.process = self.process, None
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            if process.stdin:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()
        self.completed = 0


class Engine:
    def __init__(self, store: Store, cache: Path, timeout=90, recycle_after=25):
        self.store = store
        self.cache = cache.resolve()
        self.timeout = timeout
        self.states = {}
        self.lock = threading.RLock()
        # Both candidate transactions and ordinary builds share this one queue.
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cad")
        self.worker = PersistentWorker(recycle_after)

    @staticmethod
    def _state(fingerprint):
        return {
            "fingerprint": fingerprint,
            "status": "building",
            "error": None,
            "result": None,
            "directory": None,
            "seconds": None,
            "timings": {},
            "feature_errors": [],
        }

    def refresh(self, design_id: str):
        with self.store.lock:
            doc, source, fingerprint = self.store.read(design_id)
            with self.lock:
                state = self.states.get(design_id)
                if state is None or state["fingerprint"] != fingerprint:
                    state = self._state(fingerprint)
                    self.states[design_id] = state
                    self.pool.submit(
                        self._build, design_id, source, doc, state, time.perf_counter()
                    )
                return {
                    "id": design_id,
                    "design": doc.model_dump(),
                    "source_path": str(self.store.path(design_id) / "model.py"),
                    **{k: v for k, v in state.items() if k != "directory"},
                }

    def _build(self, design_id, source, doc, state, queued_at):
        # Skip queued work superseded by a more recent edit. An already-running
        # older build may finish, but cannot replace the newer state's object.
        with self.lock:
            if self.states.get(design_id) is not state:
                return
        terminal = self._execute(design_id, source, doc, state["fingerprint"], queued_at)
        with self.lock:
            state.update(terminal)

    def build_candidate(self, design_id, source, doc, fingerprint):
        """Validate one unsaved snapshot without publishing it or writing history."""
        self.store.path(design_id)  # Validate the cache directory identifier, too.
        return self.pool.submit(
            self._execute, design_id, source, doc, fingerprint, time.perf_counter()
        ).result()

    def adopt(self, design_id, state):
        """Publish a built candidate after committing that exact document to Store."""
        if state["status"] != "ready":
            raise ValueError("Only a successfully verified build can be adopted.")
        with self.store.lock:
            _, _, fingerprint = self.store.read(design_id)
            if fingerprint != state["fingerprint"]:
                raise Conflict("Design changed before the verified build could be published.")
            with self.lock:
                self.states[design_id] = state

    def _execute(self, design_id, source, doc, fingerprint, queued_at):
        started = time.perf_counter()
        state = self._state(fingerprint)
        timings = {
            "queue_seconds": started - queued_at,
            "import_seconds": None,
            "kernel_seconds": None,
            "validation_seconds": None,
            "mesh_seconds": None,
            "export_seconds": None,
        }
        directory = self.cache / design_id / (fingerprint + "-" + uuid.uuid4().hex[:8])
        directory.mkdir(parents=True, exist_ok=True)
        request = {"source": source, "parameters": {k: p.value for k, p in doc.parameters.items()}}
        if getattr(doc, "features", None) is not None:
            request["features"] = doc.model_dump()["features"]
        snapshot = directory / "request.json"
        snapshot.write_text(json.dumps(request), encoding="utf-8")
        process_started = time.perf_counter()
        try:
            if "features" in request:
                timings.update(self.worker.run(snapshot, directory, self.timeout))
            else:
                process = subprocess.run(
                    [sys.executable, "-m", "voicedesign.worker", str(snapshot), str(directory)],
                    cwd=directory,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=self.timeout,
                    check=False,
                    creationflags=NO_WINDOW,
                )
                timings.update(
                    worker_mode="isolated",
                    worker_reused=False,
                    process_seconds=time.perf_counter() - process_started,
                )
                if process.returncode and not (directory / "error.txt").exists():
                    raise ValueError(
                        process.stderr[-10000:]
                        or f"CAD process exited with code {process.returncode}."
                    )
            error_path = directory / "error.txt"
            if error_path.exists():
                details_path = directory / "error.json"
                if details_path.exists():
                    state["feature_errors"] = json.loads(details_path.read_text(encoding="utf-8"))
                raise ValueError(error_path.read_text(encoding="utf-8")[-10000:])
            result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
            state.update(status="ready", result=result, directory=directory)
        except (subprocess.TimeoutExpired, TimeoutError):
            state.update(
                status="error",
                error=f"Build exceeded {self.timeout} seconds. Simplify the model or fix a loop.",
            )
        except Exception as error:  # noqa: BLE001 -- surface any worker failure to the UI
            state.update(status="error", error=str(error))
        finally:
            timings.setdefault("process_seconds", time.perf_counter() - process_started)
            timing_path = directory / "timings.json"
            if timing_path.exists():
                recorded = json.loads(timing_path.read_text(encoding="utf-8"))
                # Runtime knows if an import occurred in this request.
                import_seconds = timings.get("import_seconds")
                if import_seconds is None:
                    import_seconds = recorded.get("import_seconds")
                timings.update(recorded)
                timings["import_seconds"] = import_seconds
            state["seconds"] = round(time.perf_counter() - started, 4)
            timings["total_seconds"] = time.perf_counter() - queued_at
            state["timings"] = {
                key: round(value, 6) if type(value) is float else value
                for key, value in timings.items()
            }
        return state

    def artifact(self, design_id: str, fingerprint: str, filename: str):
        status = self.refresh(design_id)
        with self.lock:
            state = self.states[design_id]
            if status["fingerprint"] != fingerprint or state["status"] != "ready":
                raise ValueError(
                    "This design is rebuilding or has changed. Wait for a successful current build."
                )
            if state["result"].get("empty"):
                raise ValueError("This design is blank. Add a solid before exporting geometry.")
            path = state["directory"] / filename
            if not path.is_file():
                raise FileNotFoundError(filename)
            return path

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.worker.close()
