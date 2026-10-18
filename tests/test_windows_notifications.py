"""Popup ownership and dismissal with fake Win32 APIs; opens no real windows."""
import threading
import unittest
from unittest.mock import Mock

from windows_notifications import TITLE, _close_owned_popup, show_windows_popup


class PopupAPI:
    def __init__(self):
        self.started, self.released = threading.Event(), threading.Event()
        self.owner, self.enumerated_threads, self.messages = None, [], []

    def MessageBoxW(self, owner, message, title, flags):
        self.owner = threading.get_native_id()
        self.started.set()
        if not self.released.wait(3):
            raise TimeoutError('Fake popup was not closed.')
        return 1

    def EnumThreadWindows(self, owner, callback, unused):
        self.enumerated_threads.append(owner)
        callback(1234, 0)
        return True

    def GetClassNameW(self, window, buffer, size):
        buffer.value = '#32770'
        return len(buffer.value)

    def GetWindowTextW(self, window, buffer, size):
        buffer.value = TITLE
        return len(buffer.value)

    def PostMessageW(self, window, message, first, second):
        self.messages.append((window, message, first, second))
        self.released.set()
        return True


class PopupTests(unittest.TestCase):
    def run_popup(self, close_via_stop=False):
        api, acknowledged, stop = PopupAPI(), threading.Event(), threading.Event()
        errors = []

        def show():
            try:
                show_windows_popup('Example alarm', acknowledged.is_set, stop, api=api)
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=show)
        thread.start()
        self.assertTrue(api.started.wait(2))
        self.assertEqual(api.messages, [])
        (stop if close_via_stop else acknowledged).set()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(api.messages)
        self.assertTrue(all(owner == api.owner for owner in api.enumerated_threads))
        self.assertEqual(api.messages[0], (1234, 0x111, 1, 0))

    def test_acknowledging_an_alert_closes_only_the_popup_threads_dialog(self):
        self.run_popup()

    def test_stopping_the_server_closes_the_popup(self):
        self.run_popup(close_via_stop=True)

    def test_user_closing_popup_does_not_acknowledge_the_saved_alert(self):
        api, acknowledged = PopupAPI(), threading.Event()
        api.released.set()
        show_windows_popup('Example', acknowledged.is_set, threading.Event(), api=api)
        self.assertFalse(acknowledged.is_set())
        self.assertEqual(api.messages, [])

    def test_already_acknowledged_alert_does_not_flash_a_stale_popup(self):
        api = Mock()
        show_windows_popup('Example', lambda: True, threading.Event(), api=api)
        api.MessageBoxW.assert_not_called()

    def test_unrelated_window_classes_and_titles_on_the_thread_are_ignored(self):
        for kind, title in (('OtherClass', TITLE), ('#32770', 'Another dialog')):
            api = PopupAPI()
            api.GetClassNameW = lambda window, buffer, size: setattr(buffer, 'value', kind)
            api.GetWindowTextW = lambda window, buffer, size: setattr(buffer, 'value', title)
            _close_owned_popup(api, 5678)
            self.assertEqual(api.enumerated_threads, [5678])
            self.assertEqual(api.messages, [])


if __name__ == '__main__':
    unittest.main()
