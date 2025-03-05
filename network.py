import asyncio
import json

class PeerNetwork:

    def __init__(self, elevator_id, listen_host, listen_port, peer_addresses, elevator):
        self.id = elevator_id
        self.host = listen_host
        self.port = listen_port
        self.peer_addresses = peer_addresses  
        self.elevator = elevator 
        self.server = None
        self.connections = {}    # Map: peer_id -> (reader, writer)
        self.peers_status = {}  
        self.hall_calls = {}  

    async def start(self):
        self.server = await asyncio.start_server(self._handle_client, self.host, self.port)
        print(f"Elevator {self.id}: Listening on {self.host}:{self.port}")

        for (phost, pport) in self.peer_addresses:
            if phost == self.host and pport == self.port:
                continue
            asyncio.create_task(self._connect_to_peer(phost, pport))

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        remote_id = None
        try:
            data = await reader.readline()
            if not data:
                writer.close()
                await writer.wait_closed()
                return
            msg = json.loads(data.decode().strip())
            if msg.get("type") == "intro" and "id" in msg:
                remote_id = msg["id"]
            else:
                writer.close()
                await writer.wait_closed()
                return
            intro_msg = {"type": "intro", "id": self.id}
            writer.write((json.dumps(intro_msg) + "\n").encode())
            await writer.drain()
            # Handle duplicate connections.
            if remote_id in self.connections:
                if remote_id < self.id:
                    old_reader, old_writer = self.connections.pop(remote_id)
                    try:
                        old_writer.close()
                    except:
                        pass
                    self.connections[remote_id] = (reader, writer)
                else:
                    writer.close()
                    await writer.wait_closed()
                    return
            else:
                self.connections[remote_id] = (reader, writer)
            print(f"Elevator {self.id}: Connected with elevator {remote_id}")

            while True:
                data = await reader.readline()
                if not data:
                    break
                try:
                    message = json.loads(data.decode().strip())
                except json.JSONDecodeError:
                    continue
                await self._handle_message(message, remote_id)
        except Exception as e:
            print(f"Elevator {self.id}: Error in client handler: {e}")
        finally:
            if writer:
                try:
                    writer.close()
                    await writer.wait_closed()
                except:
                    pass
            if remote_id:
                self.connections.pop(remote_id, None)
                self.peers_status.pop(remote_id, None)
                print(f"Elevator {self.id}: Peer {remote_id} disconnected.")
                await self._handle_peer_offline(remote_id)

    async def _connect_to_peer(self, host: str, port: int):

        remote_id = None
        while True:
            try:
                reader, writer = await asyncio.open_connection(host, port)
            except Exception as e:
                await asyncio.sleep(1)
                print(e)
                continue
            try:

                intro_msg = {"type": "intro", "id": self.id}
                writer.write((json.dumps(intro_msg) + "\n").encode())
                await writer.drain()

                data = await reader.readline()
                if not data:
                    raise ConnectionError("No intro response")
                msg = json.loads(data.decode().strip())
                if msg.get("type") == "intro" and "id" in msg:
                    remote_id = msg["id"]
                else:
                    raise ConnectionError("Invalid intro handshake")
                # Check for duplicate connections.
                if remote_id in self.connections:
                    if remote_id < self.id:
                        writer.close()
                        await writer.wait_closed()
                        return
                    else:
                        old_reader, old_writer = self.connections.pop(remote_id)
                        try:
                            old_writer.close()
                        except:
                            pass
                self.connections[remote_id] = (reader, writer)
                print(f"Elevator {self.id}: Connected to elevator {remote_id} at {host}:{port}")
                while True:
                    data = await reader.readline()
                    if not data:
                        break
                    try:
                        message = json.loads(data.decode().strip())
                    except json.JSONDecodeError:
                        continue
                    await self._handle_message(message, remote_id)
            except Exception as e:
                print(f"Elevator {self.id}: Connection to {host}:{port} failed ({e}), retrying...")
            finally:
                if writer:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except:
                        pass
                if remote_id:
                    self.connections.pop(remote_id, None)
                    self.peers_status.pop(remote_id, None)
                    print(f"Elevator {self.id}: Disconnected from {remote_id}.")
                    await self._handle_peer_offline(remote_id)
                await asyncio.sleep(2)

    async def _handle_message(self, message: dict, sender_id: int):

        mtype = message.get("type")
        if mtype == "status":
            self.peers_status[sender_id] = message
        elif mtype == "hall_call":
            floor = message.get("floor")
            direction = message.get("direction")
            if floor is None or direction is None:
                return
            key = (floor, direction)
            if key not in self.hall_calls or self.hall_calls[key] is None:
                self.hall_calls[key] = None
            await self._assign_hall_call(floor, direction)
        elif mtype == "assign":
            floor = message.get("floor")
            direction = message.get("direction")
            assigned_to = message.get("assigned_to")
            if floor is None or direction is None or assigned_to is None:
                return
            key = (floor, direction)
            self.hall_calls[key] = assigned_to
            if assigned_to == self.id:
                print(f"Elevator {self.id}: Assigned hall call at floor {floor} {direction}")
                self.elevator.orders.add(floor)
            else:
                print(f"Elevator {self.id}: Hall call at floor {floor} {direction} assigned to {assigned_to}")
        elif mtype == "completed":
            floor = message.get("floor")
            direction = message.get("direction")
            done_by = message.get("by")
            if floor is None or direction is None or done_by is None:
                return
            key = (floor, direction)
            if key in self.hall_calls:
                self.hall_calls.pop(key, None)
            print(f"Elevator {self.id}: Hall call at floor {floor} {direction} completed by {done_by}")

    async def _assign_hall_call(self, floor: int, direction: str):

        def cost(status, call_floor):
            current_floor = status.get("floor", 0)
            return abs(call_floor - current_floor)

        key = (floor, direction)
        my_status = self.elevator.get_status() if hasattr(self.elevator, "get_status") else {}
        best_id = self.id
        best_cost = cost(my_status, floor) if my_status else float('inf')
        for pid, status in self.peers_status.items():
            if status:
                c = cost(status, floor)
                if c < best_cost or (c == best_cost and pid < best_id):
                    best_cost = c
                    best_id = pid
        if best_id == self.id:
            if self.hall_calls.get(key) is None:
                self.hall_calls[key] = self.id
                print(f"Elevator {self.id}: Taking hall call at floor {floor} {direction}")
                self.elevator.orders.add(floor)
                assign_msg = {"type": "assign", "floor": floor, "direction": direction, "assigned_to": self.id}
                await self._broadcast(assign_msg)

    async def send_status_update(self):

        if not self.connections:
            return
        status = self.elevator.get_status() if hasattr(self.elevator, "get_status") else {}
        msg = {"type": "status", "id": self.id}
        msg.update(status)
        data = json.dumps(msg) + "\n"
        for pid, (reader, writer) in list(self.connections.items()):
            try:
                writer.write(data.encode())
            except:
                continue
        for pid, (reader, writer) in list(self.connections.items()):
            try:
                await writer.drain()
            except:
                continue

    async def send_hall_call(self, floor: int, direction: str):

        key = (floor, direction)
        print(f"Elevator {self.id}: Hall call requested at floor {floor} {direction}")
        if not self.connections:
            self.hall_calls[key] = self.id
            self.elevator.orders.add(floor)
        else:
            self.hall_calls[key] = None
            msg = {"type": "hall_call", "floor": floor, "direction": direction}
            await self._broadcast(msg)
            await self._assign_hall_call(floor, direction)

    async def send_completed(self, floor: int, direction: str):

        key = (floor, direction)
        if self.connections and key in self.hall_calls:
            msg = {"type": "completed", "floor": floor, "direction": direction, "by": self.id}
            await self._broadcast(msg)
        self.hall_calls.pop(key, None)

    async def _handle_peer_offline(self, peer_id: int):

        to_reassign = []
        for (floor, direction), assigned in list(self.hall_calls.items()):
            if assigned == peer_id:
                self.hall_calls[(floor, direction)] = None
                to_reassign.append((floor, direction))
        if to_reassign:
            print(f"Elevator {self.id}: Peer {peer_id} offline, reassigning hall calls: {to_reassign}")
            active_ids = [self.id] + list(self.connections.keys())
            if min(active_ids) == self.id:
                for floor, direction in to_reassign:
                    msg = {"type": "hall_call", "floor": floor, "direction": direction}
                    await self._broadcast(msg)
                    await self._assign_hall_call(floor, direction)

    async def _broadcast(self, message: dict):
        if not self.connections:
            return
        data = json.dumps(message) + "\n"
        for pid, (reader, writer) in list(self.connections.items()):
            try:
                writer.write(data.encode())
            except:
                continue
        for pid, (reader, writer) in list(self.connections.items()):
            try:
                await writer.drain()
            except:
                continue