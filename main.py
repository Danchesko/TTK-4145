import os 
import argparse
import asyncio

from elevator import DistributedElevatorController
from network import PeerNetwork
from elevator_driver import MD_UP, MD_DOWN, MD_STOP

async def main():
    parser = argparse.ArgumentParser(description="Distributed Elevator Control System")
    parser.add_argument("--id", type=int, required=True, help="Unique elevator ID")
    parser.add_argument("--driver-host", type=str, default=os.environ.get('SERVER_IP'), help="Elevator driver host")
    parser.add_argument("--driver-port", type=int, default=15657, help="Elevator driver port")
    parser.add_argument("--listen-port", type=int, required=True, help="TCP port to listen on for networking")
    parser.add_argument("--floors", type=int, default=4, help="Number of floors")
    parser.add_argument("--peers", type=str, nargs="*", default=[], help="Peer addresses (format: host:port)")
    args = parser.parse_args()

    peer_addresses = []
    for peer in args.peers:
        if ":" in peer:
            host, port_str = peer.split(":")
            port = int(port_str)
        else:
            host = peer  # Assume the provided value is an IP without a port
            port = 10000 + int(peer.split(".")[-1]) % 100  # Default port logic (can be adjusted)

        if host == args.driver_host and port == args.listen_port:
            continue
        peer_addresses.append((host, port))

    elevator = DistributedElevatorController(
        host=args.driver_host,
        port=args.driver_port,
        num_floors=args.floors,
        network=None
    )
    elevator.id = args.id

    def get_status():
        return {
            "id": elevator.id,
            "floor": elevator.current_floor,
            "direction": {MD_UP: "up", MD_DOWN: "down", MD_STOP: 'stop'}.get(elevator.current_direction),
            "state": "idle" if elevator.state == 0 else "moving" if elevator.state == 1 else "door_open"
        }
    elevator.get_status = get_status

    network = PeerNetwork(
        elevator_id=args.id,
        listen_host='0.0.0.0',
        listen_port=args.listen_port,
        peer_addresses=peer_addresses,
        elevator=elevator
    )
    elevator.network = network

    await network.start()

    async def status_updater():
        while True:
            await network.send_status_update()
            await asyncio.sleep(0.5)
    asyncio.create_task(status_updater())

    await elevator.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Shutting down elevator system.")