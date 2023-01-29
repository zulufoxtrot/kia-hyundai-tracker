import os

from flask import Flask, request

from VehicleClient import VehicleClient
from hyundai_kia_connect_api import ClimateRequestOptions

app = Flask(__name__)


@app.route("/force_refresh")
def force_refresh():
    vehicle_client.vm.check_and_refresh_token()
    vehicle_client.vm.force_refresh_vehicle_state(vehicle_client.vehicle.id)
    vehicle_client.vm.update_vehicle_with_cached_state(vehicle_client.vehicle.id)

    vehicle_client.save_data()

    return "OK"


@app.route("/status")
def get_cached_status():
    vehicle_client.vm.check_and_refresh_token()
    vehicle_client.vm.update_all_vehicles_with_cached_state()

    if vehicle_client.vehicle.last_updated_at.replace(
            tzinfo=None) > vehicle_client.db_client.get_last_update_timestamp():
        vehicle_client.save_data()

    return str(vehicle_client.vehicle)


@app.route("/battery")
def get_battery_soc():
    vehicle_client.vm.check_and_refresh_token()
    vehicle_client.vm.update_all_vehicles_with_cached_state()

    if vehicle_client.vehicle.last_updated_at.replace(
            tzinfo=None) > vehicle_client.db_client.get_last_update_timestamp():
        vehicle_client.save_data()

    return str(vehicle_client.vehicle.ev_battery_percentage)


@app.route("/climate")
def toggle_climate():
    """
    Available arguments:
    - action: [start, stop]
    - temp: target temperature (degrees celcius)
    - duration: duration (minutes)
    """

    options = ClimateRequestOptions()
    options.set_temp = request.args.get('temp', default=22)
    options.duration = request.args.get('duration', default=10)

    vehicle_client.vm.check_and_refresh_token()

    action = request.args.get('action')
    if action == "start":
        vehicle_client.vm.start_climate(vehicle_client.vehicle.id, options)
        return "Climate control ON"
    elif action == "stop":
        vehicle_client.vm.stop_climate(vehicle_client.vehicle.id)
        return "Climate control OFF"
    else:
        return f"unrecognised command: {action}. send start or stop."


@app.route("/charge")
def toggle_charge():
    """
    Available arguments:
    - action: [start, stop]
    """
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
    """
    Available arguments:
    - action: [start, stop]
    """
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
