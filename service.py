import json
import os
import sys
import threading
import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_PATH = ADDON.getAddonInfo('path')

ACTION_PREVIOUS_MENU = 10
ACTION_NAV_BACK = 92


def log(message, level=xbmc.LOGDEBUG):
    xbmc.log(f'[{ADDON_ID}] {message}', level)


class SkiptroDialog(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.skip_type = None
        self.seek_target = None
        self.stinger_target = None
        self.autoclose_seconds = 10
        self.window_end = None
        self._timer = None
        self._window_checker = None
        self._closed = False

    def set_skip_info(self, skip_type, seek_target=None, stinger_target=None,
                      autoclose_seconds=10, window_end=None):
        self.skip_type = skip_type
        self.seek_target = seek_target
        self.stinger_target = stinger_target
        self.autoclose_seconds = autoclose_seconds
        self.window_end = window_end

    def _start_timer(self):
        self._cancel_timer()
        self._timer = threading.Timer(self.autoclose_seconds, self._on_timeout)
        self._timer.daemon = True
        self._timer.start()

    def _cancel_timer(self):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _on_timeout(self):
        if not self._closed:
            log('Dialog auto-closed after timeout')
            self.close()

    def _start_window_checker(self):
        if self.window_end is None:
            return
        self._cancel_window_checker()
        self._window_checker = threading.Timer(0.5, self._check_window_end)
        self._window_checker.daemon = True
        self._window_checker.start()

    def _cancel_window_checker(self):
        if self._window_checker is not None:
            self._window_checker.cancel()
            self._window_checker = None

    def _check_window_end(self):
        if self._closed or self.window_end is None:
            return
        try:
            current_time = xbmc.Player().getTime()
            if current_time >= self.window_end:
                log('Dialog closed - playback past skip window')
                self.close()
                return
        except RuntimeError:
            pass
        self._start_window_checker()

    def onInit(self):
        if self.skip_type == 'intro':
            self.getControl(101).setVisible(True)
            self.getControl(102).setVisible(False)
            self.getControl(103).setVisible(False)
            self.setFocusId(101)
        elif self.skip_type == 'credits':
            self.getControl(101).setVisible(False)
            self.getControl(102).setVisible(True)
            self.getControl(103).setVisible(self.stinger_target is not None)
            self.setFocusId(103 if self.stinger_target else 102)
        self._start_timer()
        self._start_window_checker()

    def onClick(self, controlId):
        player = xbmc.Player()

        if controlId == 101 and self.skip_type == 'intro':
            if self.seek_target is not None:
                log(f'Skipping intro, seeking to {self.seek_target}s')
                player.seekTime(self.seek_target)

        elif controlId == 102 and self.skip_type == 'credits':
            try:
                total_time = player.getTotalTime()
                seek_to = max(0, total_time - 2)
                log(f'Skipping credits, seeking to {seek_to}s')
                player.seekTime(seek_to)
            except RuntimeError:
                pass

        elif controlId == 103 and self.skip_type == 'credits':
            if self.stinger_target is not None:
                log(f'Skipping to stinger at {self.stinger_target}s')
                player.seekTime(self.stinger_target)

        self.close()

    def onAction(self, action):
        if self.window_end is not None:
            try:
                current_time = xbmc.Player().getTime()
                if current_time >= self.window_end:
                    log('Dialog closed - playback past skip window')
                    self.close()
                    return
            except RuntimeError:
                pass

        # Reset timer on any input to keep dialog open while user interacts
        self._start_timer()

        if action.getId() in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK):
            log('Dialog dismissed by user')
            self.close()

    def close(self):
        self._closed = True
        self._cancel_timer()
        self._cancel_window_checker()
        super().close()


class SkiptroPlayer(xbmc.Player):
    def __init__(self):
        super().__init__()
        self.skiptro_data = None
        self.current_file = None

    def onAVStarted(self):
        self._load_skiptro_data()

    def onPlayBackStopped(self):
        self._reset()

    def onPlayBackEnded(self):
        self._reset()

    def _reset(self):
        log('Playback ended, resetting state')
        self.skiptro_data = None
        self.current_file = None

    def _load_skiptro_data(self):
        folder = xbmc.getInfoLabel('Player.Folderpath')
        filename = xbmc.getInfoLabel('Player.Filename')

        if not folder or not filename:
            log('Could not get folder/filename from InfoLabels', xbmc.LOGWARNING)
            return

        self.current_file = folder + filename
        base, _ = os.path.splitext(filename)
        skiptro_path = folder + base + '.skiptro.json'

        log(f'Checking for skiptro data: {skiptro_path}')

        if not xbmcvfs.exists(skiptro_path):
            log('No skiptro file found')
            self.skiptro_data = None
            return

        try:
            with xbmcvfs.File(skiptro_path, 'r') as f:
                self.skiptro_data = json.loads(f.read())
            log(f'Loaded skiptro data: {self.skiptro_data}')
            self._validate_skiptro_data()
        except json.JSONDecodeError as e:
            log(f'Invalid JSON in skiptro file: {e}', xbmc.LOGWARNING)
            self.skiptro_data = None
        except Exception as e:
            log(f'Error reading skiptro file: {e}', xbmc.LOGWARNING)
            self.skiptro_data = None

    def _validate_skiptro_data(self):
        if not self.skiptro_data:
            return

        intro = self.skiptro_data.get('intro')
        if intro:
            start = intro.get('start', 0)
            end = intro.get('end', 0)
            if start < 0 or end < 0:
                log('Invalid intro times: negative values', xbmc.LOGWARNING)
                del self.skiptro_data['intro']
            elif end <= start:
                log('Invalid intro times: end must be after start', xbmc.LOGWARNING)
                del self.skiptro_data['intro']

        credits = self.skiptro_data.get('credits')
        if credits:
            start = credits.get('start', 0)
            if start < 0:
                log('Invalid credits time: negative value', xbmc.LOGWARNING)
                del self.skiptro_data['credits']

        stinger = self.skiptro_data.get('stinger')
        if stinger:
            start = stinger.get('start', 0)
            if start < 0:
                log('Invalid stinger time: negative value', xbmc.LOGWARNING)
                del self.skiptro_data['stinger']


class SkiptroService:
    def __init__(self):
        self.monitor = xbmc.Monitor()
        self.player = SkiptroPlayer()
        self.active_ranges = set()
        self.prompted_ranges = set()

    def run(self):
        log('Service started')

        while not self.monitor.abortRequested():
            if self.monitor.waitForAbort(0.5):
                break

            if not self.player.isPlayingVideo():
                if self.active_ranges or self.prompted_ranges:
                    self.active_ranges.clear()
                    self.prompted_ranges.clear()
                continue

            if self.player.skiptro_data is None:
                continue

            try:
                current_time = self.player.getTime()
            except RuntimeError:
                continue

            self._check_skiptro_ranges(current_time)

        log('Service stopped')

    def _check_skiptro_ranges(self, current_time):
        currently_active = set()
        data = self.player.skiptro_data

        intro = data.get('intro')
        if intro:
            start = intro.get('start', 0)
            end = intro.get('end', 0)
            if start <= current_time < end:
                currently_active.add('intro')

        credits = data.get('credits')
        if credits:
            start = credits.get('start', 0)
            if current_time >= start:
                currently_active.add('credits')

        past_ranges = self.active_ranges - currently_active
        self.prompted_ranges -= past_ranges
        self.active_ranges = currently_active

        for range_type in currently_active:
            if range_type not in self.prompted_ranges:
                if range_type == 'intro':
                    end = intro.get('end', 0)
                    self._show_dialog('intro', seek_target=end, window_end=end)
                elif range_type == 'credits':
                    stinger = data.get('stinger')
                    stinger_target = stinger.get('start') if stinger else None
                    self._show_dialog('credits', stinger_target=stinger_target)
                return

    def _show_dialog(self, skip_type, seek_target=None, stinger_target=None,
                      window_end=None):
        self.prompted_ranges.add(skip_type)

        autoclose_seconds = int(ADDON.getSetting('autoclose_seconds') or 10)

        log(f'Showing {skip_type} dialog (autoclose: {autoclose_seconds}s)')

        dialog = SkiptroDialog(
            'service.skiptro-SkipDialog.xml',
            ADDON_PATH,
            'default',
            '1080i'
        )
        dialog.set_skip_info(skip_type, seek_target, stinger_target,
                             autoclose_seconds, window_end)
        dialog.doModal()
        del dialog


def run_command(command):
    if command == 'autoclose_setting':
        dialog = xbmcgui.Dialog()
        values = [2, 3, 5, 7, 10, 15, 20, 30]
        seconds_str = xbmc.getLocalizedString(37129)
        options = [seconds_str.format(v) for v in values]

        current = int(ADDON.getSetting('autoclose_seconds') or 10)
        try:
            preselect = values.index(current)
        except ValueError:
            preselect = 4

        selection = dialog.select(ADDON.getLocalizedString(32020), options, preselect=preselect)
        if selection >= 0:
            ADDON.setSetting('autoclose_seconds', str(values[selection]))
            log(f'Auto-close set to {values[selection]} seconds')


if __name__ == '__main__':
    if len(sys.argv) > 1:
        run_command(sys.argv[1])
    else:
        service = SkiptroService()
        service.run()
