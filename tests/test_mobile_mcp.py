import base64
import copy
import json
import unittest
from chaos.mcp_http import MCPToolError, MCPTransportError
from chaos.mobile_mcp import ELEMENTS_PREFIX, MobileMCP, _tree, mobile_mcp_computer


class Client:
    def __init__(self, *args, **kwargs):
        self.calls = []
        self.rows = [{'ref': '@e5', 'type': 'android.widget.EditText', 'text': 'Existing',
                      'coordinates': {'x': 10, 'y': 20, 'width': 100, 'height': 40}}]
        self.closed = False
        self.fail = None
        self.after_screenshot = None
    def call_tool(self, name, arguments, checkpoint, *, mutation=False):
        checkpoint()
        self.calls.append((name, copy.deepcopy(arguments), mutation))
        if name == self.fail:
            raise MCPToolError('fixture partial effect')
        if name == 'mobile_list_available_devices':
            text = json.dumps({'devices': [{'id': 'phone-a', 'state': 'online', 'platform': 'ios', 'type': 'real'}]})
        elif name == 'mobile_get_screen_size':
            text = 'Screen size is 390x844 pixels'
        elif name == 'mobile_list_elements_on_screen':
            text = ELEMENTS_PREFIX + json.dumps(self.rows)
        elif name == 'mobile_take_screenshot':
            if self.after_screenshot:
                self.after_screenshot(self)
            return {'content': [{'type': 'text', 'text': 'Screenshot is 1170x2532. Screen coordinates are 390x844.'},
                                {'type': 'image', 'data': base64.b64encode(b'image-fixture').decode(), 'mimeType': 'image/png'}]}
        else:
            text = 'MUTATION result text intentionally not parsed'
        return {'content': [{'type': 'text', 'text': text}]}
    def close(self):
        self.closed = True


class MobileMCPTests(unittest.TestCase):
    def adapter(self):
        client = Client()
        value = MobileMCP('http://127.0.0.1:3000/mcp', 'phone-a', client_factory=lambda *a, **kw: client)
        return value, client
    def command(self, observation, action, **args):
        return {'action': action, '_observation': [observation[key] for key in ('sessionId', 'controlEpoch', 'snapshotId')], 'image': False, **args}

    def test_constructor_is_cold_and_device_is_fixed(self):
        adapter, client = self.adapter()
        self.assertEqual(client.calls, [])
        observation = adapter.call({'action': 'observe', 'image': False})
        self.assertEqual(observation['viewport'], {'width': 390, 'height': 844})
        self.assertEqual(observation['url'], '')
        self.assertEqual(observation['title'], 'Mobile device')
        self.assertNotIn('phone-a', json.dumps(observation))
        self.assertEqual(observation['targets'][0]['ref'], 'e1')
        self.assertNotIn('"ref":"@e5"', observation['snapshot'])
        self.assertIn('"targetRef":"e1"', observation['snapshot'])
        for name, args, _ in client.calls:
            self.assertEqual(args.get('device'), None if name == 'mobile_list_available_devices' else 'phone-a')

    def test_click_uses_native_ref_and_cannot_replay_old_observation(self):
        adapter, client = self.adapter()
        observation = adapter.call({'action': 'observe', 'image': False})
        command = self.command(observation, 'click', ref='e1')
        after = adapter.call(command)
        mutations = [row for row in client.calls if row[2]]
        self.assertEqual(mutations, [('mobile_click_on_screen_at_coordinates', {'ref': '@e5', 'device': 'phone-a'}, True)])
        self.assertGreater(after['snapshotId'], observation['snapshotId'])
        with self.assertRaises(MCPTransportError):
            adapter.call(command)
        self.assertEqual(len([row for row in client.calls if row[2]]), 1)

    def test_input_is_tap_then_append_not_fill(self):
        adapter, client = self.adapter()
        observation = adapter.call({'action': 'observe', 'image': False})
        adapter.call(self.command(observation, 'input', ref='e1', text=' + suffix'))
        mutations = [row for row in client.calls if row[2]]
        self.assertEqual([row[0] for row in mutations], ['mobile_click_on_screen_at_coordinates', 'mobile_type_keys'])
        self.assertEqual(mutations[1][1], {'text': ' + suffix', 'submit': False, 'device': 'phone-a'})
        self.assertNotIn('mobile_batch_commands', [row[0] for row in client.calls])

    def test_browser_fill_press_and_scroll_are_not_silently_reinterpreted(self):
        for action in ('type', 'press', 'scroll'):
            adapter, client = self.adapter()
            observation = adapter.call({'action': 'observe', 'image': False})
            with self.assertRaises(MCPTransportError):
                adapter.call(self.command(observation, action, ref='e1', text='x'))
            self.assertEqual([row for row in client.calls if row[2]], [])

    def test_fresh_full_fingerprint_prevents_stale_coordinate_and_native_ref(self):
        for native_ref in (True, False):
            adapter, client = self.adapter()
            if not native_ref:
                del client.rows[0]['ref']
            observation = adapter.call({'action': 'observe', 'image': False})
            client.rows.append({'type': 'Text', 'text': 'new hidden row', 'coordinates': {'x': 900, 'y': 900, 'width': 10, 'height': 10}})
            with self.assertRaises(MCPTransportError):
                adapter.call(self.command(observation, 'click', ref='e1'))
            self.assertEqual([row for row in client.calls if row[2]], [])

    def test_coordinate_fallback_uses_logical_screen_not_image_pixels(self):
        adapter, client = self.adapter()
        del client.rows[0]['ref']
        observation = adapter.call({'action': 'observe', 'image': False})
        adapter.call(self.command(observation, 'click', ref='e1'))
        mutation = next(row for row in client.calls if row[2])
        self.assertEqual(mutation[1], {'x': 60.0, 'y': 40.0, 'device': 'phone-a'})

    def test_partial_input_failure_never_replays_tap_or_auto_observes(self):
        adapter, client = self.adapter()
        observation = adapter.call({'action': 'observe', 'image': False})
        client.fail = 'mobile_type_keys'
        command = self.command(observation, 'input', ref='e1', text='suffix')
        with self.assertRaises(MCPToolError):
            adapter.call(command)
        self.assertIsNone(adapter._latest)
        self.assertEqual([row[0] for row in client.calls if row[2]], ['mobile_click_on_screen_at_coordinates', 'mobile_type_keys'])
        with self.assertRaises(MCPTransportError):
            adapter.call(command)
        self.assertEqual(len([row for row in client.calls if row[2]]), 2)

    def test_explicit_button_launch_swipe_use_exact_device_and_distance(self):
        for action, args, expected, sent in (
                ('button', {'button': 'HOME'}, 'mobile_press_button', {'button': 'HOME'}),
                ('launch', {'packageName': 'com.example.app'}, 'mobile_launch_app', {'packageName': 'com.example.app'}),
                ('swipe', {'direction': 'up', 'distance': 120}, 'mobile_swipe_on_screen', {'direction': 'up', 'distance': 120, 'x': 195, 'y': 422})):
            adapter, client = self.adapter()
            observation = adapter.call({'action': 'observe', 'image': False})
            adapter.call(self.command(observation, action, **args))
            self.assertEqual([row for row in client.calls if row[2]], [(expected, {**sent, 'device': 'phone-a'}, True)])

    def test_one_screenshot_feeds_both_variants_without_geometry_inference(self):
        adapter, client = self.adapter()
        captured = []
        expected = {'preview': {'width': 1170}, 'model': {'width': 480}}
        def prepare(raw, mime, *, checkpoint=None):
            captured.append((raw, mime))
            return expected
        adapter._image_preparer = prepare
        observation = adapter.call({'action': 'observe', 'image': True})
        self.assertEqual(observation['_images'], expected)
        self.assertEqual(captured, [(b'image-fixture', 'image/png')])
        self.assertEqual(observation['viewport'], {'width': 390, 'height': 844})
        self.assertEqual(len([row for row in client.calls if row[0] == 'mobile_take_screenshot']), 1)

    def test_screen_changed_during_capture_is_not_published(self):
        adapter, client = self.adapter()
        adapter._image_preparer = lambda *args, **kwargs: {'preview': {}, 'model': {}}
        client.after_screenshot = lambda c: c.rows[0].update(text='changed')
        with self.assertRaises(MCPTransportError):
            adapter.call({'action': 'observe', 'image': True})
        self.assertIsNone(adapter._latest)

    def test_duplicate_ref_and_malformed_structured_tree_are_rejected(self):
        adapter, client = self.adapter()
        client.rows.append(copy.deepcopy(client.rows[0]))
        with self.assertRaises(MCPTransportError):
            adapter.call({'action': 'observe', 'image': False})
        for value in ('Found these elements on screen: {"not":"an array"}', 'Tap the Login button',
                      ELEMENTS_PREFIX + '[{"type":"Text","coordinates":{"x":0,"y":0,"width":NaN,"height":2}}]'):
            with self.assertRaises(MCPTransportError):
                _tree({'content': [{'type': 'text', 'text': value}]})

    def test_close_does_not_touch_user_device(self):
        adapter, client = self.adapter()
        adapter.close()
        self.assertEqual(client.calls, [])
        with self.assertRaises(MCPTransportError):
            adapter.call({'action': 'observe', 'image': False})

    def test_cursor_remains_in_bounds_after_viewport_shrink(self):
        adapter, client = self.adapter()
        adapter._cursor = {'x': 1000, 'y': 1000}
        observation = adapter.call({'action': 'observe', 'image': False})
        self.assertEqual(observation['cursor'], {'x': 389, 'y': 843})

    def test_registration_is_cold_and_alias_identity_ignores_credentials(self):
        first = mobile_mcp_computer('first', 'http://LOCALHOST:80/mcp', 'phone-a', token_env='UNSET_FIXTURE_KEY')
        second = mobile_mcp_computer('second', 'http://localhost/mcp', 'phone-a', token_env='DIFFERENT_FIXTURE_KEY')
        self.assertEqual(first.resource_id, second.resource_id)
        metadata = json.dumps(first.describe())
        for private in ('localhost', 'phone-a', 'UNSET_FIXTURE_KEY'):
            self.assertNotIn(private, metadata)
        class Context:
            def checkpoint(self):
                pass
        adapter = first.factory(Context())
        self.assertIsNone(adapter._client)
        adapter.close()

    def test_device_override_or_arbitrary_tool_fields_never_dispatch(self):
        for fields in ({'device': 'phone-b'}, {'tool': 'mobile_uninstall_app'}, {'arguments': {}}):
            adapter, client = self.adapter()
            observation = adapter.call({'action': 'observe', 'image': False})
            with self.assertRaises(MCPTransportError):
                adapter.call(self.command(observation, 'button', button='HOME', **fields))
            self.assertEqual([row for row in client.calls if row[2]], [])
