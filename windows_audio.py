"""Windows playback-device volume through Core Audio, with no extra library."""
from contextlib import contextmanager
import ctypes
import math
import sys
import uuid

HRESULT = ctypes.c_int32
DWORD = ctypes.c_uint32
BOOL = ctypes.c_int32


class GUID(ctypes.Structure):
    _fields_ = [('data1', DWORD), ('data2', ctypes.c_uint16), ('data3', ctypes.c_uint16), ('data4', ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def _check(result):
    if result < 0:
        raise OSError(f'Windows audio API failed (HRESULT 0x{result & 0xffffffff:08X}).')


def _method(pointer, index, result_type, *argument_types):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(result_type, ctypes.c_void_p, *argument_types)(table[index])


class _Volume:
    def __init__(self, pointer):
        self.pointer = pointer

    def set_level(self, value):
        _check(_method(self.pointer, 7, HRESULT, ctypes.c_float, ctypes.c_void_p)(self.pointer, value, None))

    def set_mute(self, value):
        _check(_method(self.pointer, 14, HRESULT, BOOL, ctypes.c_void_p)(self.pointer, value, None))

    def state(self):
        level, muted = ctypes.c_float(), BOOL()
        _check(_method(self.pointer, 9, HRESULT, ctypes.POINTER(ctypes.c_float))(self.pointer, ctypes.byref(level)))
        _check(_method(self.pointer, 15, HRESULT, ctypes.POINTER(BOOL))(self.pointer, ctypes.byref(muted)))
        return dict(level=level.value, muted=bool(muted.value))


@contextmanager
def _endpoint():
    if sys.platform != 'win32':
        raise OSError('Playback volume control is available on Windows only.')
    ole32 = ctypes.WinDLL('ole32')
    ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, DWORD)
    ole32.CoInitializeEx.restype = HRESULT
    ole32.CoUninitialize.argtypes = ()
    ole32.CoUninitialize.restype = None
    ole32.CoCreateInstance.argtypes = (ctypes.POINTER(GUID), ctypes.c_void_p, DWORD, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
    ole32.CoCreateInstance.restype = HRESULT
    result = ole32.CoInitializeEx(None, 0)
    # An already initialized STA thread can use these interfaces without another
    # CoUninitialize; a successful CoInitializeEx always needs its own release.
    initialized = result >= 0
    if result != -2147417850:  # RPC_E_CHANGED_MODE
        _check(result)
    pointers = []
    try:
        enumerator = ctypes.c_void_p()
        _check(ole32.CoCreateInstance(ctypes.byref(GUID.parse('BCDE0395-E52F-467C-8E3D-C4579291692E')), None, 23,
                                     ctypes.byref(GUID.parse('A95664D2-9614-4F35-A746-DE8DB63617E6')), ctypes.byref(enumerator)))
        pointers.append(enumerator)
        device = ctypes.c_void_p()
        _check(_method(enumerator, 4, HRESULT, ctypes.c_int32, ctypes.c_int32, ctypes.POINTER(ctypes.c_void_p))
               (enumerator, 0, 1, ctypes.byref(device)))  # eRender, eMultimedia
        pointers.append(device)
        volume = ctypes.c_void_p()
        _check(_method(device, 3, HRESULT, ctypes.POINTER(GUID), DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
               (device, ctypes.byref(GUID.parse('5CDF2C82-841E-4546-9722-0CF74078229A')), 23, None, ctypes.byref(volume)))
        pointers.append(volume)
        yield _Volume(volume)
    finally:
        for pointer in reversed(pointers):
            _method(pointer, 2, DWORD)(pointer)  # Release every counted COM reference.
        if initialized:
            ole32.CoUninitialize()


def get_windows_volume():
    """Read the default multimedia playback device without changing it."""
    with _endpoint() as volume:
        return volume.state()


def validate_volume_state(state):
    if not isinstance(state, dict) or not isinstance(state.get('muted'), bool):
        raise ValueError('Saved playback state must contain a level and mute state.')
    level = state.get('level')
    if isinstance(level, bool) or not isinstance(level, (int, float)) or not math.isfinite(level) or not 0 <= level <= 1:
        raise ValueError('Saved playback volume must be between zero and one.')
    return dict(level=float(level), muted=state['muted'])


def restore_windows_volume(state):
    """Restore an original scalar level (including zero) and its mute state."""
    saved = validate_volume_state(state)
    with _endpoint() as volume:
        if saved['muted']:
            volume.set_mute(True)
        volume.set_level(saved['level'])
        volume.set_mute(saved['muted'])
        actual = volume.state()
        if actual['muted'] != saved['muted'] or abs(actual['level'] - saved['level']) > .001:
            raise OSError('Windows did not confirm the restored playback volume and mute state.')
        return actual


def maximize_windows_volume():
    """Set 100 percent, unmute, and confirm the playback endpoint's readback."""
    return set_windows_volume(100)


def set_windows_volume(percent):
    """Set an explicit 1–100 percent level, unmute and confirm readback."""
    if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not math.isfinite(percent) or not 1 <= percent <= 100:
        raise ValueError('Windows alarm volume must be between 1 and 100 percent.')
    level = percent / 100
    with _endpoint() as volume:
        volume.set_level(level)
        volume.set_mute(False)
        state = volume.state()
        if state['muted'] or abs(state['level'] - level) > .001:
            raise OSError(f'Windows did not confirm {percent:g}% unmuted playback volume.')
        return state
