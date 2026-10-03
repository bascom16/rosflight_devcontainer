#!/usr/bin/env python3
"""ROS 2 side of the sim launcher. Runs under the system Python (rclpy), not the launcher's venv.

Protocol: one JSON object per line.
  stdin  {"id": 1, "op": "call", "service": "/toggle_arm", "type": "std_srvs/srv/Trigger",
          "request": {}, "timeout": 10}
  stdout {"id": 1, "ok": true, "response": {...}}  or  {"id": 1, "ok": false, "error": "..."}
         {"event": "status", "armed": ..., "failsafe": ..., "rc_override": ..., "offboard": ...,
          "error_code": ...}            (on change, and at least every 2 s while publishing)
         {"event": "graph", "nodes": ["/rc", ...]}                              (every 2 s)
Logs go to stderr.
"""

import json
import sys
import threading
import time

import rclpy
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rosflight_msgs.msg import Status
from rosidl_runtime_py.convert import message_to_ordereddict
from rosidl_runtime_py.set_message import set_message_fields
from rosidl_runtime_py.utilities import get_service

_out_lock = threading.Lock()


def emit(obj):
    with _out_lock:
        sys.stdout.write(json.dumps(obj) + '\n')
        sys.stdout.flush()


class Bridge(Node):
    def __init__(self):
        super().__init__('sim_launcher_bridge')
        self._svc_clients = {}
        self._svc_clients_lock = threading.Lock()
        self._last_status = None
        self._last_status_emit = 0.0
        self.create_subscription(Status, 'status', self._on_status, 10)
        self.create_timer(2.0, self._emit_graph)

    def _on_status(self, msg):
        status = {
            'armed': msg.armed,
            'failsafe': msg.failsafe,
            'rc_override': msg.rc_override,
            'offboard': msg.offboard,
            'error_code': msg.error_code,
        }
        now = time.monotonic()
        if status != self._last_status or now - self._last_status_emit > 2.0:
            self._last_status = status
            self._last_status_emit = now
            emit({'event': 'status', **status})

    def _emit_graph(self):
        # Skip hidden nodes (leading '_', e.g. the ros2 CLI daemon), like `ros2 node list` does.
        names = sorted(
            (ns.rstrip('/') + '/' + name)
            for name, ns in self.get_node_names_and_namespaces()
            if not name.startswith('_')
        )
        emit({'event': 'graph', 'nodes': [n for n in names if n != '/sim_launcher_bridge']})

    def _client(self, service, srv_type):
        with self._svc_clients_lock:
            key = (service, srv_type)
            if key not in self._svc_clients:
                self._svc_clients[key] = self.create_client(get_service(srv_type), service)
            return self._svc_clients[key]

    def call(self, req):
        service, srv_type = req['service'], req['type']
        timeout = float(req.get('timeout', 10.0))
        deadline = time.monotonic() + timeout
        client = self._client(service, srv_type)
        if not client.wait_for_service(timeout_sec=timeout):
            raise TimeoutError(f'service {service} not available after {timeout:.0f} s')
        request = client.srv_type.Request()
        set_message_fields(request, req.get('request') or {})
        done = threading.Event()
        future = client.call_async(request)
        future.add_done_callback(lambda _: done.set())
        if not done.wait(max(deadline - time.monotonic(), 1.0)):
            future.cancel()
            raise TimeoutError(f'service {service} did not respond within {timeout:.0f} s')
        return message_to_ordereddict(future.result())


def handle(bridge, line):
    try:
        req = json.loads(line)
    except ValueError:
        print(f'bad request: {line!r}', file=sys.stderr)
        return
    try:
        if req.get('op') != 'call':
            raise ValueError(f'unknown op {req.get("op")!r}')
        emit({'id': req.get('id'), 'ok': True, 'response': bridge.call(req)})
    except Exception as exc:  # noqa: BLE001 - every failure goes back to the caller
        emit({'id': req.get('id'), 'ok': False, 'error': f'{type(exc).__name__}: {exc}'})


def main():
    rclpy.init()
    bridge = Bridge()
    executor = MultiThreadedExecutor()
    executor.add_node(bridge)
    threading.Thread(target=executor.spin, daemon=True).start()
    print('ros_bridge ready', file=sys.stderr, flush=True)
    try:
        for line in sys.stdin:  # exits when the launcher closes stdin
            if line.strip():
                threading.Thread(target=handle, args=(bridge, line), daemon=True).start()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        bridge.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()
