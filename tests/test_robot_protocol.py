import unittest
from chaos.robot_protocol import control_frame, interval_frame, telemetry_line, MAX_TX_FRAME
from chaos.robots import RobotSpec, validate_channels


class RobotProtocolTests(unittest.TestCase):
    def test_exact_pinned_controller_frames_and_cast(self):
        self.assertEqual(control_frame({'left': 0.5, 'right': -0.5}), b'c127,-127\n')
        self.assertEqual(control_frame({'left': -1, 'right': 1}), b'c-255,255\n')
        for kind in ('h', 'v', 's', 'w'):
            self.assertEqual(interval_frame(kind, 750), (kind + '750\n').encode())
        self.assertLessEqual(len(control_frame({'left': -1, 'right': -1})), MAX_TX_FRAME)

    def test_invalid_channels_and_nonfinite_or_boolean_values(self):
        for channels in ({'left': True, 'right': 0}, {'left': 2, 'right': 0}, {'left': float('nan'), 'right': 0},
                         {'left': 0}, {'left': 0, 'right': 0, 'extra': 0}):
            with self.assertRaises(ValueError):
                control_frame(channels)
        for value in (0, True, 10001, 1.5):
            with self.assertRaises(ValueError):
                interval_frame('h', value)

    def test_real_packet_units_reset_and_sonar_sentinel(self):
        self.assertEqual(telemetry_line(b'v12.34'), ('telemetry', {'voltage': {'value': 12.34, 'unit': 'V'}}))
        self.assertEqual(telemetry_line(b's65535'), ('telemetry', {'sonar': {'value': None, 'unit': 'cm'}}))
        self.assertEqual(telemetry_line(b's30\r'), ('telemetry', {'sonar': {'value': 30, 'unit': 'cm'}}))
        self.assertEqual(telemetry_line(b'w-10.20,30.40')[1]['leftRpm'], {'value': -10.2, 'unit': 'rpm'})
        self.assertEqual(telemetry_line(b'r'), ('reset', None))

    def test_debug_does_not_make_observations_malformed_packets_fail(self):
        for line in (b'', b'f1,0,0', b'vmin', b'vlow', b'vmax12.3', b'OpenBot firmware'):
            self.assertIsNone(telemetry_line(line))
        for line in (b'vnan', b'vinf', b'v1e99', b'w1,2,3', b's-1', b's65536', b'w1,nan', b'v' + b'1' * 130):
            with self.assertRaises(ValueError):
                telemetry_line(line)

    def test_profile_channels_are_extensible_and_static(self):
        effects = []
        actuators = {'pan': {'minimum': -90, 'maximum': 90, 'unit': 'deg'}, 'grip': {'minimum': 0, 'maximum': 1, 'unit': 'normalized'}}
        spec = RobotSpec('arm', lambda context: effects.append('opened'), actuators, profile='arm/1')
        actuators['pan']['maximum'] = 999
        described = spec.describe(); described['actuators']['pan']['maximum'] = 888
        self.assertEqual(spec.actuators['pan']['maximum'], 90)
        self.assertEqual(validate_channels({'pan': 20, 'grip': 0.5}, spec.actuators), {'pan': 20, 'grip': 0.5})
        self.assertEqual(effects, [])
        with self.assertRaises(TypeError):
            spec.actuators['pan']['maximum'] = 100
