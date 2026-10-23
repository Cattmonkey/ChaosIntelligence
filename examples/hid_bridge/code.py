"""Copy with hid_bridge.py and chaos/hid_protocol.py to a CircuitPython Pico.

Controller USB-UART: TX -> GP1, RX -> GP0, GND -> GND (3.3V logic).
Pico USB -> TARGET computer. Never wire 5V UART logic to the GPIO pins.
"""
import time
import board
import busio
import usb_hid
from hid_bridge import Bridge

keyboard = next(d for d in usb_hid.devices if d.usage_page == 1 and d.usage == 6)
mouse = next(d for d in usb_hid.devices if d.usage_page == 1 and d.usage == 2)
uart = busio.UART(board.GP0, board.GP1, baudrate=115200, timeout=0.01, receiver_buffer_size=2048)
bridge = Bridge(keyboard, mouse)
# Optional hardware watchdog. A reset disconnects/re-enumerates this device;
# it cannot guarantee what an unresponsive target application has consumed.
watchdog = None
try:
    from microcontroller import watchdog
    from watchdog import WatchDogMode
    watchdog.timeout = 2
    watchdog.mode = WatchDogMode.RESET
except (ImportError, AttributeError, NotImplementedError):
    watchdog = None
try:
    bridge.release()
    while True:
        if watchdog is not None:
            watchdog.feed()
        for response in bridge.feed(uart.read(128) or b'', time.monotonic()):
            # CircuitPython UART timeout bounds each write. Partial replies
            # become a host-side unknown outcome; commands are never replayed.
            uart.write(response)
finally:
    try:
        bridge.release()
    finally:
        uart.deinit()
