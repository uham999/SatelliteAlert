"""Native pass popups that close when their in-app alerts are acknowledged."""
import ctypes
from ctypes import wintypes
import logging
import threading

LOG = logging.getLogger("satellite_alert")
TITLE = "Satellite pass alert"
WNDENUMPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _user32():
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.MessageBoxW.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)
    api.MessageBoxW.restype = ctypes.c_int
    api.EnumThreadWindows.argtypes = (wintypes.DWORD, WNDENUMPROC, wintypes.LPARAM)
    api.EnumThreadWindows.restype = wintypes.BOOL
    for method in (api.GetClassNameW, api.GetWindowTextW):
        method.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        method.restype = ctypes.c_int
    api.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    api.PostMessageW.restype = wintypes.BOOL
    return api


def _close_owned_popup(api, thread_id):
    # Enumerate only the popup's own thread, never unrelated application windows.
    @WNDENUMPROC
    def close(window, unused):
        kind, title = ctypes.create_unicode_buffer(32), ctypes.create_unicode_buffer(128)
        api.GetClassNameW(window, kind, len(kind))
        api.GetWindowTextW(window, title, len(title))
        if kind.value == "#32770" and title.value == TITLE:
            api.PostMessageW(window, 0x111, 1, 0)  # WM_COMMAND / IDOK
        return True

    api.EnumThreadWindows(thread_id, close, 0)


def show_windows_popup(message, acknowledged, stop, api=None):
    """Closing the window alone does not acknowledge the saved pass alert."""
    if stop.is_set() or acknowledged():
        return
    api = api or _user32()
    owner_thread, done = threading.get_native_id(), threading.Event()

    def watch():
        while not done.wait(.1):
            try:
                if stop.is_set() or acknowledged():
                    _close_owned_popup(api, owner_thread)
            except Exception:
                LOG.exception("Unable to close acknowledged Windows pass popup")
                return

    watcher = threading.Thread(target=watch, name="pass-popup-close", daemon=True)
    watcher.start()
    try:
        if not api.MessageBoxW(None, message, TITLE, 0x40 | 0x10000 | 0x40000):
            raise OSError("Windows could not display the pass popup.")
    finally:
        done.set()
        watcher.join(timeout=.5)
