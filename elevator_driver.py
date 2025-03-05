import socket
import threading
import time
import queue

POLL_RATE = 0.01

MD_UP   =  1
MD_DOWN = -1
MD_STOP =  0

BT_HALL_UP   = 0
BT_HALL_DOWN = 1
BT_CAB       = 2

class ElevatorEvent:
    def __init__(self, event_type, floor=None, button=None, value=None):
        self.event_type = event_type
        self.floor = floor
        self.button = button
        self.value = value

    def __repr__(self):
        return (f"ElevatorEvent("
                f"event_type={self.event_type}, "
                f"floor={self.floor}, "
                f"button={self.button}, "
                f"value={self.value})")

class ElevatorDriver:
    def __init__(self, host="localhost", port=15657, num_floors=4):
        self.host = host
        self.port = port
        self.num_floors = num_floors

        self._sock = None
        self._lock = threading.Lock()

        self._poll_thread = None
        self._running = False

        self._prev_button_state = [[False]*3 for _ in range(num_floors)]
        self._prev_floor = -1
        self._prev_stop = False
        self._prev_obstruction = False

        self.event_queue = queue.Queue()

    def connect(self):
        try:
            self._sock = socket.create_connection((self.host, self.port))
        except Exception as e:
            raise RuntimeError(f"Failed to connect to {self.host}:{self.port}: {e}")
        print(f"[ElevatorDriver] Connected to {self.host}:{self.port}")

    def start_polling(self):
        if not self._sock:
            raise RuntimeError("Cannot start polling: socket not connected.")

        if self._poll_thread and self._poll_thread.is_alive():
            print("[ElevatorDriver] Polling thread is already running.")
            return

        self._running = True
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="ElevatorPollThread", daemon=True
        )
        self._poll_thread.start()
        print("[ElevatorDriver] Polling thread started.")

    def close(self):
        self._running = False
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=1.0)

        if self._sock:
            with self._lock:
                try:
                    self._sock.close()
                except:
                    pass
            self._sock = None
        print("[ElevatorDriver] Closed connection and stopped polling.")

    def _poll_loop(self):
        while self._running:
            time.sleep(POLL_RATE)
            self._poll_once()

    def _poll_once(self):
        for f in range(self.num_floors):
            for b_type in (BT_HALL_UP, BT_HALL_DOWN, BT_CAB):
                curr_state = self.get_button(b_type, f)
                prev_state = self._prev_button_state[f][b_type]
                if curr_state and not prev_state:
                    evt = ElevatorEvent("button_press", floor=f, button=b_type)
                    self.event_queue.put(evt)
                self._prev_button_state[f][b_type] = curr_state

        curr_floor = self.get_floor()
        if curr_floor != self._prev_floor and curr_floor != -1:
            evt = ElevatorEvent("floor_sensor", floor=curr_floor)
            self.event_queue.put(evt)
        self._prev_floor = curr_floor

        curr_stop = self.get_stop()
        if curr_stop != self._prev_stop:
            evt = ElevatorEvent("stop_button", value=curr_stop)
            self.event_queue.put(evt)
        self._prev_stop = curr_stop

        curr_obstruction = self.get_obstruction()
        if curr_obstruction != self._prev_obstruction:
            evt = ElevatorEvent("obstruction", value=curr_obstruction)
            self.event_queue.put(evt)
        self._prev_obstruction = curr_obstruction

    def set_motor_direction(self, direction):
        self._write(bytes([1, self._to_byte_signed(direction), 0, 0]))

    def set_button_lamp(self, button_type, floor, on_off):
        self._write(bytes([
            2,
            self._to_byte_unsigned(button_type),
            self._to_byte_unsigned(floor),
            self._to_byte_bool(on_off)
        ]))

    def set_floor_indicator(self, floor):
        self._write(bytes([3, self._to_byte_unsigned(floor), 0, 0]))

    def set_door_open_lamp(self, on_off):
        self._write(bytes([4, self._to_byte_bool(on_off), 0, 0]))

    def set_stop_lamp(self, on_off):
        self._write(bytes([5, self._to_byte_bool(on_off), 0, 0]))

    def get_button(self, button_type, floor):
        resp = self._read(bytes([
            6,
            self._to_byte_unsigned(button_type),
            self._to_byte_unsigned(floor),
            0
        ]))
        return self._to_bool(resp[1])

    def get_floor(self):
        resp = self._read(bytes([7, 0, 0, 0]))
        if resp[1] != 0:
            return int(resp[2])
        else:
            return -1

    def get_stop(self):
        resp = self._read(bytes([8, 0, 0, 0]))
        return self._to_bool(resp[1])

    def get_obstruction(self):
        resp = self._read(bytes([9, 0, 0, 0]))
        return self._to_bool(resp[1])

    def _read(self, out_bytes: bytes) -> bytes:
        with self._lock:
            self._sock.sendall(out_bytes)
            return self._read_exactly(4)

    def _write(self, out_bytes: bytes):
        with self._lock:
            self._sock.sendall(out_bytes)

    def _read_exactly(self, num_bytes: int) -> bytes:
        buf = b""
        while len(buf) < num_bytes:
            chunk = self._sock.recv(num_bytes - len(buf))
            if not chunk:
                raise ConnectionError("Lost connection to Elevator Server.")
            buf += chunk
        return buf

    @staticmethod
    def _to_byte_bool(b_val: bool) -> int:
        return 1 if b_val else 0

    @staticmethod
    def _to_bool(byte_val: int) -> bool:
        return (byte_val != 0)

    @staticmethod
    def _to_byte_signed(i_val: int) -> int:
        if i_val < 0:
            return 256 + i_val
        return i_val

    @staticmethod
    def _to_byte_unsigned(i_val: int) -> int:
        return i_val & 0xFF
