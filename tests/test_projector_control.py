import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import projector_control
from projector_control import Controller


class FakeProjector:
    def __init__(self):
        self.power = 'off'
        self.calls = []

    def power_on(self):
        self.calls.append('on')
        self.power = 'on'

    def power_off(self):
        self.calls.append('off')
        self.power = 'cooling'

    def power_state(self):
        return self.power

    def set_light_source(self, mode):
        self.calls.append(f'mode:{mode}')

    def set_input(self, name):
        self.calls.append(f'input:{name}')

    def set_aspect(self, name):
        self.calls.append(f'aspect:{name}')

    def set_volume(self, level):
        self.calls.append(f'volume:{level}')

    def light_source_hours(self):
        return 1234

    def close(self):
        pass


def make_controller(monkeypatch, mode='eco'):
    projector = FakeProjector()
    monkeypatch.setattr(projector_control, 'from_config', lambda cfg: projector)
    monkeypatch.setattr(projector_control, 'mode_at', lambda when, loc, cfg: state['mode'])
    state = {'t': 0.0, 'ups': {'OL'}, 'mode': mode, 'shutdowns': 0}
    config = {'location': {'lat': 41.88, 'lon': -87.63},
              'projector': {'serial': 'x', 'brightness': {}, 'power_loss_debounce_s': 10}}

    def shutdown():
        state['shutdowns'] += 1

    c = Controller(config, clock=lambda: state['t'], ups_reader=lambda ups: state['ups'], shutdown=shutdown)
    return c, projector, state


def test_boot_powers_on_then_sets_brightness_once(monkeypatch):
    c, projector, state = make_controller(monkeypatch)
    c.step()
    assert projector.calls == ['on', 'input:hdmi1', 'aspect:16:9', 'mode:eco']
    state['t'] = 2
    c.step()
    assert projector.calls == ['on', 'input:hdmi1', 'aspect:16:9', 'mode:eco']   # nothing resent
    state['mode'], state['t'] = 'supereco', 4
    c.step()
    assert projector.calls[-1] == 'mode:supereco'


def test_boot_waits_while_on_battery(monkeypatch):
    c, projector, state = make_controller(monkeypatch)
    state['ups'] = {'OB'}
    c.step()
    assert 'on' not in projector.calls


def test_power_loss_turns_projector_off_then_shuts_down(monkeypatch):
    c, projector, state = make_controller(monkeypatch)
    c.step()
    state['ups'] = {'OB', 'DISCHRG'}
    for t in (2, 4, 12):
        state['t'] = t
        c.step()
    assert projector.calls[-1] == 'off'
    assert state['shutdowns'] == 0
    projector.power = 'off'
    state['t'] = 14
    c.step()
    assert state['shutdowns'] == 1


def test_power_back_mid_sequence_turns_projector_on_and_reapplies_mode(monkeypatch):
    c, projector, state = make_controller(monkeypatch)
    c.step()
    state['ups'] = {'OB'}
    for t in (2, 12):
        state['t'] = t
        c.step()
    state['ups'], state['t'] = {'OL'}, 14
    projector.calls.clear()
    c.step()           # power back: turn on (projector reports warming, so no mode yet)
    assert projector.calls[0] == 'on'
    state['t'] = 16
    c.step()
    assert projector.calls[-1] == 'mode:eco'
    assert state['shutdowns'] == 0


def test_setup_waits_until_projector_has_warmed_up(monkeypatch):
    c, projector, state = make_controller(monkeypatch)
    c.cfg['volume'] = 4
    projector.power_on = lambda: (projector.calls.append('on'), setattr(projector, 'power', 'warming'))
    c.step()
    assert projector.calls == ['on']                      # warming: nothing else sent yet
    projector.power = 'on'
    state['t'] = 2
    c.step()
    assert projector.calls == ['on', 'input:hdmi1', 'aspect:16:9', 'volume:4', 'mode:eco']


def test_idle_steps_do_not_query_the_projector(monkeypatch):
    c, projector, state = make_controller(monkeypatch)
    c.step()
    queries = []
    projector.power_state = lambda: queries.append(1) or 'on'
    for t in range(2, 60, 2):
        state['t'] = t
        c.step()
    assert queries == []                                  # setup done, mode current, hours logged
