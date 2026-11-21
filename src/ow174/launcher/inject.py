"""Loading the relay DLL into the game process, and finding running copies of the game (Windows)."""

import csv
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

k32 = None
_k32_lock = threading.RLock()

PROCESS_INJECT = 0x0002 | 0x0008 | 0x0010 | 0x0020 | 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
MEM_COMMIT_RESERVE, MEM_RELEASE, PAGE_READWRITE = 0x3000, 0x8000, 0x04
WAIT_OBJECT_0, WAIT_TIMEOUT, WAIT_FAILED = 0, 258, 0xFFFFFFFF
INVALID_HANDLE = ctypes.c_void_p(-1).value
TH32CS_SNAPPROCESS = 0x02
DONT_RESOLVE_DLL_REFERENCES = 0x00000001


class ProcessExitedError(OSError):
    """Raised when the bound target process terminates before an operation completes."""


class ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", wt.CHAR * 260),
    ]


class ModuleEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("th32ModuleID", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("GlblcntUsage", wt.DWORD),
        ("ProccntUsage", wt.DWORD),
        ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wt.DWORD),
        ("hModule", wt.HMODULE),
        ("szModule", wt.WCHAR * 256),
        ("szExePath", wt.WCHAR * 260),
    ]


def _kernel32():
    global k32
    if k32 is not None:
        return k32
    with _k32_lock:
        if k32 is not None:
            return k32
        if sys.platform != "win32":
            raise OSError("kernel32 process injection is only available on Windows")
        dll = ctypes.WinDLL("kernel32", use_last_error=True)

        def _declare(name, restype, *argtypes):
            fn = getattr(dll, name)
            fn.restype, fn.argtypes = restype, list(argtypes)

        _declare("OpenProcess", wt.HANDLE, wt.DWORD, wt.BOOL, wt.DWORD)
        _declare("CloseHandle", wt.BOOL, wt.HANDLE)
        _declare(
            "VirtualAllocEx",
            ctypes.c_void_p,
            wt.HANDLE,
            ctypes.c_void_p,
            ctypes.c_size_t,
            wt.DWORD,
            wt.DWORD,
        )
        _declare("VirtualFreeEx", wt.BOOL, wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD)
        _declare(
            "WriteProcessMemory",
            wt.BOOL,
            wt.HANDLE,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        )
        _declare("GetModuleHandleW", wt.HMODULE, wt.LPCWSTR)
        _declare("GetModuleHandleExW", wt.BOOL, wt.DWORD, wt.LPCWSTR, ctypes.POINTER(wt.HMODULE))
        _declare("GetModuleFileNameW", wt.DWORD, wt.HMODULE, wt.LPWSTR, wt.DWORD)
        _declare("LoadLibraryExW", wt.HMODULE, wt.LPCWSTR, wt.HANDLE, wt.DWORD)
        _declare("FreeLibrary", wt.BOOL, wt.HMODULE)
        _declare("GetProcAddress", ctypes.c_void_p, wt.HMODULE, wt.LPCSTR)
        _declare(
            "CreateRemoteThread",
            wt.HANDLE,
            wt.HANDLE,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wt.DWORD,
            ctypes.POINTER(wt.DWORD),
        )
        _declare("WaitForSingleObject", wt.DWORD, wt.HANDLE, wt.DWORD)
        _declare("GetExitCodeThread", wt.BOOL, wt.HANDLE, ctypes.POINTER(wt.DWORD))
        _declare(
            "GetProcessTimes",
            wt.BOOL,
            wt.HANDLE,
            ctypes.POINTER(wt.FILETIME),
            ctypes.POINTER(wt.FILETIME),
            ctypes.POINTER(wt.FILETIME),
            ctypes.POINTER(wt.FILETIME),
        )
        _declare(
            "QueryFullProcessImageNameW",
            wt.BOOL,
            wt.HANDLE,
            wt.DWORD,
            wt.LPWSTR,
            ctypes.POINTER(wt.DWORD),
        )
        _declare("CreateToolhelp32Snapshot", wt.HANDLE, wt.DWORD, wt.DWORD)
        _declare("Process32First", wt.BOOL, wt.HANDLE, ctypes.POINTER(ProcessEntry))
        _declare("Process32Next", wt.BOOL, wt.HANDLE, ctypes.POINTER(ProcessEntry))
        _declare("Module32FirstW", wt.BOOL, wt.HANDLE, ctypes.POINTER(ModuleEntry))
        _declare("Module32NextW", wt.BOOL, wt.HANDLE, ctypes.POINTER(ModuleEntry))
        k32 = dll
        return k32


# While a process is still loading, its module list is unstable: the snapshot can fail with
# ERROR_BAD_LENGTH (24, MSDN says to retry), come back empty (18) or be cut short (299).
TRANSIENT_SNAPSHOT_ERRORS = frozenset({24, 18, 299})


def _modules(pid, patience=2.0):
    """Modules of a process, retrying briefly while the target is still loading."""
    deadline = time.monotonic() + patience
    while True:
        try:
            return _snapshot_modules(pid)
        except OSError as error:
            if (
                getattr(error, "winerror", None) not in TRANSIENT_SNAPSHOT_ERRORS
                or time.monotonic() >= deadline
            ):
                raise
            time.sleep(0.01)


def _snapshot_modules(pid):
    k32 = _kernel32()
    handle = k32.CreateToolhelp32Snapshot(0x08 | 0x10, pid)
    if handle == INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = ModuleEntry()
        entry.dwSize = ctypes.sizeof(entry)
        if not k32.Module32FirstW(handle, ctypes.byref(entry)):
            raise ctypes.WinError(ctypes.get_last_error())
        result = []
        while True:
            result.append((entry.szExePath, entry.modBaseAddr))
            if not k32.Module32NextW(handle, ctypes.byref(entry)):
                error = ctypes.get_last_error()
                if error != 18:
                    raise ctypes.WinError(error)
                return result
    finally:
        k32.CloseHandle(handle)


def _loaded_module(pid, path):
    wanted = os.path.normcase(str(Path(path).resolve()))
    return next(
        (base for name, base in _modules(pid) if os.path.normcase(str(Path(name).resolve())) == wanted), 0
    )


def remote_load_library(pid):
    """Resolve the remote export by its owning DLL, including forwarded exports."""
    k32 = _kernel32()
    address = k32.GetProcAddress(k32.GetModuleHandleW("kernel32.dll"), b"LoadLibraryW")
    if not address:
        raise ctypes.WinError(ctypes.get_last_error())
    owner = wt.HMODULE()
    if not k32.GetModuleHandleExW(0x06, ctypes.cast(address, wt.LPCWSTR), ctypes.byref(owner)):
        raise ctypes.WinError(ctypes.get_last_error())
    name = ctypes.create_unicode_buffer(32768)
    if not k32.GetModuleFileNameW(owner, name, len(name)):
        raise ctypes.WinError(ctypes.get_last_error())
    wanted = Path(name.value).name.lower()
    for path, base in _modules(pid):
        if Path(path).name.lower() == wanted:
            return base + address - owner.value
    raise RuntimeError(f"Loader DLL {wanted} not found in target {pid}; check process architecture")


def image_path(pid):
    """Full path of a process's executable, or None when it cannot be read."""
    k32 = _kernel32()
    process = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        return None
    try:
        size = wt.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        return (
            buffer.value if k32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)) else None
        )
    finally:
        k32.CloseHandle(process)


def process_exited(pid):
    """Confirm exit when Windows still lists the PID and a handle can be opened."""
    k32 = _kernel32()
    process = k32.OpenProcess(SYNCHRONIZE, False, pid)
    if not process:
        return False
    try:
        return k32.WaitForSingleObject(process, 0) == WAIT_OBJECT_0
    finally:
        k32.CloseHandle(process)


def find_pids(name="Overwatch.exe"):
    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        # tasklist uses the Windows OEM code page even when Python UTF-8 mode is enabled.
        encoding="oem",
        errors="replace",
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return [
        int(row[1])
        for row in csv.reader(result.stdout.splitlines())
        if len(row) > 1 and row[0].lower() == name.lower()
    ]


def inject(dll_path: str, pid: int, timeout_ms: int = 10000) -> int:
    path = Path(dll_path).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"DLL path is not a file: {path}")
    if pid <= 0 or not 0 < timeout_ms < 0xFFFFFFFF:
        raise ValueError("PID and timeout must be positive and finite")
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError("Use 64-bit Python for the x64 client")
    loaded = _loaded_module(pid, path)
    if loaded:
        return loaded
    loader = remote_load_library(pid)
    dll = (str(path) + "\0").encode("utf-16-le")
    k32 = _kernel32()
    process = k32.OpenProcess(PROCESS_INJECT, False, pid)
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())
    remote, thread, finished = None, None, False
    try:
        remote = k32.VirtualAllocEx(process, None, len(dll), MEM_COMMIT_RESERVE, PAGE_READWRITE)
        if not remote:
            raise ctypes.WinError(ctypes.get_last_error())
        written = ctypes.c_size_t()
        if not k32.WriteProcessMemory(process, remote, dll, len(dll), ctypes.byref(written)):
            raise ctypes.WinError(ctypes.get_last_error())
        if written.value != len(dll):
            raise OSError(f"Incomplete remote write: {written.value}/{len(dll)} bytes")
        thread = k32.CreateRemoteThread(process, None, 0, loader, remote, 0, None)
        if not thread:
            raise ctypes.WinError(ctypes.get_last_error())
        wait = k32.WaitForSingleObject(thread, timeout_ms)
        if wait == WAIT_TIMEOUT:
            raise TimeoutError(f"DLL loader in PID {pid} timed out; remote path retained until process exit")
        if wait != WAIT_OBJECT_0:
            raise ctypes.WinError(ctypes.get_last_error())
        finished = True
        code = wt.DWORD()
        if not k32.GetExitCodeThread(thread, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        loaded = _loaded_module(pid, path)
        if not loaded:
            raise RuntimeError(f"DLL did not load in PID {pid} (thread exit {code.value:#x}): {path}")
        return loaded
    finally:
        if remote and (not thread or finished):
            k32.VirtualFreeEx(process, remote, 0, MEM_RELEASE)
        if thread:
            k32.CloseHandle(thread)
        k32.CloseHandle(process)


def find_process_entries(name: str = "Overwatch.exe") -> list[tuple[int, str]]:
    """Enumerate matching processes via Toolhelp32 (works under Windows and Wine without tasklist)."""
    k32 = _kernel32()
    handle = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if handle == INVALID_HANDLE:
        raise ctypes.WinError(ctypes.get_last_error())
    entry = ProcessEntry()
    entry.dwSize = ctypes.sizeof(entry)
    wanted = name.lower()
    found: list[tuple[int, str]] = []
    try:
        if not k32.Process32First(handle, ctypes.byref(entry)):
            error = ctypes.get_last_error()
            if error != 18:
                raise ctypes.WinError(error)
            return found
        while True:
            exe = entry.szExeFile.decode("mbcs", errors="replace")
            if exe.lower() == wanted:
                found.append((int(entry.th32ProcessID), exe))
            if not k32.Process32Next(handle, ctypes.byref(entry)):
                error = ctypes.get_last_error()
                if error != 18:
                    raise ctypes.WinError(error)
                return found
    finally:
        k32.CloseHandle(handle)


def _query_image_path(k32, handle: int) -> str | None:
    size = wt.DWORD(32768)
    buffer = ctypes.create_unicode_buffer(size.value)
    return buffer.value if k32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)) else None


def _query_creation_time(k32, handle: int) -> int | None:
    created, exited, kernel, user = wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
    if not k32.GetProcessTimes(
        handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user)
    ):
        return None
    return (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)


def _remote_load_library_from_modules(k32, pid: int, modules: list[tuple[str, int]]) -> int:
    address = k32.GetProcAddress(k32.GetModuleHandleW("kernel32.dll"), b"LoadLibraryW")
    if not address:
        raise ctypes.WinError(ctypes.get_last_error())
    owner = wt.HMODULE()
    if not k32.GetModuleHandleExW(0x06, ctypes.cast(address, wt.LPCWSTR), ctypes.byref(owner)):
        raise ctypes.WinError(ctypes.get_last_error())
    name = ctypes.create_unicode_buffer(32768)
    if not k32.GetModuleFileNameW(owner, name, len(name)):
        raise ctypes.WinError(ctypes.get_last_error())
    wanted = Path(name.value).name.lower()
    for path, base in modules:
        if Path(path).name.lower() == wanted:
            return base + address - owner.value
    raise RuntimeError(f"Loader DLL {wanted} not found in target {pid}; check process architecture")


class BoundProcess:
    """Single retained Windows process handle bound to one live process instance.

    All identity checks and modifying operations use the same open handle, preventing
    silent retargeting if the process exits and a new process reuses its PID.
    """

    def __init__(self, pid: int, handle: int) -> None:
        self.pid = pid
        self.handle: int | None = handle

    @classmethod
    def open(cls, pid: int, access: int = PROCESS_INJECT | SYNCHRONIZE) -> "BoundProcess":
        if pid <= 0:
            raise ValueError("PID must be positive")
        k32 = _kernel32()
        handle = k32.OpenProcess(access, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        bound = cls(pid, handle)
        try:
            bound.require_alive("before binding")
        except Exception:
            bound.close()
            raise
        return bound

    def __enter__(self) -> "BoundProcess":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def close(self) -> None:
        if self.handle is not None:
            handle = self.handle
            self.handle = None
            _kernel32().CloseHandle(handle)

    def _raw_handle(self) -> int:
        if self.handle is None:
            raise ProcessExitedError(f"Process handle for PID {self.pid} is already closed")
        return self.handle

    def is_alive(self) -> bool:
        handle = self._raw_handle()
        wait = _kernel32().WaitForSingleObject(handle, 0)
        if wait == WAIT_TIMEOUT:
            return True
        if wait == WAIT_OBJECT_0:
            return False
        raise ctypes.WinError(ctypes.get_last_error())

    def require_alive(self, stage: str = "during operation") -> None:
        if not self.is_alive():
            raise ProcessExitedError(f"Bound process PID {self.pid} exited {stage}")

    def image_path(self) -> str | None:
        return _query_image_path(_kernel32(), self._raw_handle())

    def creation_time(self) -> int | None:
        return _query_creation_time(_kernel32(), self._raw_handle())

    def modules(self, patience: float = 2.0) -> list[tuple[str, int]]:
        self.require_alive("before module snapshot")
        result = _modules(self.pid, patience=patience)
        self.require_alive("during module snapshot")
        return result

    def loaded_module(self, path: Path) -> int:
        wanted = os.path.normcase(str(Path(path).resolve()))
        return next(
            (base for name, base in self.modules() if os.path.normcase(str(Path(name).resolve())) == wanted),
            0,
        )

    def inject_dll(self, dll_path: str | Path, timeout_ms: int = 10000) -> int:
        path = Path(dll_path).resolve(strict=True)
        if not path.is_file():
            raise ValueError(f"DLL path is not a file: {path}")
        if not 0 < timeout_ms < 0xFFFFFFFF:
            raise ValueError("timeout_ms must be positive and finite")
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            raise RuntimeError("Use 64-bit Python for the x64 client")
        loaded = self.loaded_module(path)
        if loaded:
            return loaded
        k32 = _kernel32()
        handle = self._raw_handle()
        loader = _remote_load_library_from_modules(k32, self.pid, self.modules())
        dll = (str(path) + "\0").encode("utf-16-le")
        self.require_alive("before remote allocation")
        remote, thread, finished = None, None, False
        try:
            remote = k32.VirtualAllocEx(handle, None, len(dll), MEM_COMMIT_RESERVE, PAGE_READWRITE)
            if not remote:
                raise ctypes.WinError(ctypes.get_last_error())
            written = ctypes.c_size_t()
            if not k32.WriteProcessMemory(handle, remote, dll, len(dll), ctypes.byref(written)):
                raise ctypes.WinError(ctypes.get_last_error())
            if written.value != len(dll):
                raise OSError(f"Incomplete remote write: {written.value}/{len(dll)} bytes")
            self.require_alive("before CreateRemoteThread")
            thread = k32.CreateRemoteThread(handle, None, 0, loader, remote, 0, None)
            if not thread:
                raise ctypes.WinError(ctypes.get_last_error())
            wait = k32.WaitForSingleObject(thread, timeout_ms)
            if wait == WAIT_TIMEOUT:
                raise TimeoutError(
                    f"DLL loader in PID {self.pid} timed out; outcome unknown; "
                    "remote path retained until process exit"
                )
            if wait != WAIT_OBJECT_0:
                err = ctypes.get_last_error()
                raise RuntimeError(
                    f"Waiting for DLL loader thread in PID {self.pid} failed (wait={wait:#x}, "
                    f"winerror={err}); outcome unknown; remote path retained until process exit"
                )
            finished = True
            code = wt.DWORD()
            if not k32.GetExitCodeThread(thread, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            loaded = self.loaded_module(path)
            if not loaded:
                raise RuntimeError(
                    f"DLL did not load in PID {self.pid} (thread exit {code.value:#x}): {path}"
                )
            return loaded
        finally:
            if remote and (not thread or finished):
                k32.VirtualFreeEx(handle, remote, 0, MEM_RELEASE)
            if thread:
                k32.CloseHandle(thread)

    def start_export(
        self,
        dll_path: str | Path,
        module_base: int,
        export_name: bytes = b"worker",
        wait_ms: int = 100,
    ) -> None:
        """Invoke an exported worker entry point in the bound process without running DllMain locally."""
        path = Path(dll_path).resolve(strict=True)
        k32 = _kernel32()
        ours = k32.LoadLibraryExW(str(path), None, DONT_RESOLVE_DLL_REFERENCES)
        if not ours:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            export_addr = k32.GetProcAddress(ours, export_name)
            if not export_addr:
                raise RuntimeError(f"Export {export_name.decode('ascii', 'replace')} not found in {path}")
            remote_entry = module_base + (export_addr - ours)
        finally:
            k32.FreeLibrary(ours)

        self.require_alive("before starting worker")
        thread = k32.CreateRemoteThread(self._raw_handle(), None, 0, remote_entry, None, 0, None)
        if not thread:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            wait = k32.WaitForSingleObject(thread, wait_ms)
            if wait == WAIT_TIMEOUT:
                # worker loops indefinitely when it wins the single-start guard
                self.require_alive("while worker started")
                return
            if wait != WAIT_OBJECT_0:
                err = ctypes.get_last_error()
                raise RuntimeError(
                    f"Waiting for remote {export_name.decode('ascii', 'replace')} thread in PID "
                    f"{self.pid} failed (wait={wait:#x}, winerror={err}); outcome unknown"
                )
            code = wt.DWORD()
            if not k32.GetExitCodeThread(thread, ctypes.byref(code)):
                raise ctypes.WinError(ctypes.get_last_error())
            if code.value != 0:
                raise RuntimeError(
                    f"Remote {export_name.decode('ascii', 'replace')} exited with {code.value:#x}"
                )
            self.require_alive("after worker check")
        finally:
            k32.CloseHandle(thread)
