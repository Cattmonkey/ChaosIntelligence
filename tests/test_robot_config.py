import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from chaos.extensions import Registry
from chaos.robot_config import load_robot_registry


class RobotConfigTests(unittest.TestCase):
    def load(self, document, registry=None):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'robots.json'; path.write_text(json.dumps(document))
            return load_robot_registry(path, registry)

    def test_config_load_only_registers_copied_redacted_metadata(self):
        with patch('chaos.robot_openbot.SerialOpenBot', side_effect=AssertionError('must remain cold')):
            registry = self.load({'robots': [{'name': 'bot', 'port': '/private/serial', 'telemetry_ms': 50}]})
        info = registry.robot_snapshot()['bot'].describe()
        self.assertEqual(info['profile'], 'openbot-serial/1'); self.assertEqual(info['maxObservationAgeMs'], 1000)
        self.assertNotIn('port', info); self.assertEqual(set(info['actuators']), {'left', 'right'})

    def test_invalid_row_is_atomic_and_duplicate_names_reject(self):
        registry = Registry()
        for rows in ([{'name': 'valid', 'port': '/a'}, {'name': 'wrong', 'port': '/b', 'timeout': True}],
                     [{'name': 'same', 'port': '/a'}, {'name': 'same', 'port': '/b'}]):
            with self.assertRaises(ValueError): self.load({'robots': rows}, registry)
            self.assertEqual(dict(registry.robot_snapshot()), {})

    def test_unknown_keys_transport_and_wrong_shapes_reject(self):
        for document in ({'robots': {}, 'extra': 1}, {'robots': [{'name': 'x', 'port': '/p', 'transport': 'model'}]},
                         {'robots': [{'name': 'x', 'port': '/p', 'maxObservationAgeMs': 2}]}, {'robots': [None]}):
            with self.assertRaises(ValueError): self.load(document)

    def test_existing_registry_is_preserved_and_snapshot_is_immutable(self):
        registry = self.load({'robots': [{'name': 'first', 'port': '/a'}]})
        snapshot = registry.robot_snapshot()
        self.assertIs(self.load({'robots': [{'name': 'second', 'port': '/b'}]}, registry), registry)
        self.assertEqual(set(snapshot), {'first'}); self.assertEqual(set(registry.robot_snapshot()), {'first', 'second'})
        with self.assertRaises(TypeError): snapshot['third'] = None
        with self.assertRaises(ValueError): self.load({'robots': [{'name': 'first', 'port': '/other'}]}, registry)
