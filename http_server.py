import os

from flask import Flask, request

from VehicleClient import VehicleClient

app = Flask(__name__)


@app.route("/force_refresh")
def force_refresh():
    vehicle_client.vm.check_and_refresh_token()
    vehicle_client.vm.force_refresh_vehicle_state(vehicle_client.vehicle.id)
    return "OK"


@app.route("/status")
def get_cached_status():
    vehicle_client.vm.check_and_refresh_token()
    vehicle_client.vm.update_all_vehicles_with_cached_state()
    return str(vehicle_client.vehicle)


@app.route("/battery")
def get_battery_soc():
    vehicle_client.vm.check_and_refresh_token()
    vehicle_client.vm.update_all_vehicles_with_cached_state()
    return str(vehicle_client.vehicle.ev_battery_percentage)


@app.route("/climate")
def toggle_climate():
    vehicle_client.vm.check_and_refresh_token()

    action = request.args.get('action')
    if action == "start":
        vehicle_client.vm.start_climate(vehicle_client.vehicle.id)
        return "Climate control ON"
    elif action == "stop":
        vehicle_client.vm.stop_climate(vehicle_client.vehicle.id)
        return "Climate control OFF"
    else:
        return f"unrecognised command: {action}. send start or stop."


@app.route("/charge")
def toggle_charge():
    vehicle_client.vm.check_and_refresh_token()

    action = request.args.get('action')
    if action == "start":
        vehicle_client.vm.start_charge(vehicle_client.vehicle.id)
        return "Charge ON"
    elif action == "stop":
        vehicle_client.vm.stop_charge(vehicle_client.vehicle.id)
        return "Charge OFF"
    else:
        return f"unrecognised command: {action}. send start or stop."


@app.route("/doors")
def toggle_doors():
    vehicle_client.vm.check_and_refresh_token()

    action = request.args.get('action')
    if action == "lock":
        vehicle_client.vm.lock(vehicle_client.vehicle.id)
        return "Doors LOCKED"
    elif action == "unlock":
        vehicle_client.vm.unlock(vehicle_client.vehicle.id)
        return "Doors UNLOCKED"
    else:
        return f"unrecognised command: {action}. send lock or unlock."


if __name__ == "__main__":
    vehicle_client = VehicleClient()
    vehicle_client.vm.check_and_refresh_token()
    vehicle_client.vehicle = vehicle_client.vm.get_vehicle(os.environ["KIA_VEHICLE_UUID"])

    app.run(port=8000, host='0.0.0.0', debug=True)
