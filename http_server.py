import os
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, request, make_response, jsonify

from VehicleClient import VehicleClient
from hyundai_kia_connect_api import ClimateRequestOptions
from hyundai_kia_connect_api.const import OrderStatus

app = Flask(__name__)


def auth_required(f):
    """
    Authentication decorator
    Checks for the password passed as a GET argument.
    :param f: the function to decorate
    """

    @wraps(f)
    def decorator(*args, **kwargs):
        # check password.
        # password is passed as a GET argument in the HTTP request.
        if request.args.get('password') != app.config["SERVER_PASSWORD"]:
            return make_response({"error": "invalid password"}, 401)
        return f(*args, **kwargs)

    return decorator


@app.route("/force_refresh")
@auth_required
def force_refresh():
    vehicle_client.vm.check_and_refresh_token()
    vehicle_client.vm.force_refresh_vehicle_state(vehicle_client.vehicle.id)
    vehicle_client.vm.update_vehicle_with_cached_state(vehicle_client.vehicle.id)

    vehicle_client.save_log()

    return jsonify({"action": "force_refresh", "status": "success"})


@app.route("/status")
@auth_required
def get_cached_status():
    vehicle_client.vm.check_and_refresh_token()
    vehicle_client.vm.update_all_vehicles_with_cached_state()

    if vehicle_client.vehicle.last_updated_at.replace(
            tzinfo=None) > vehicle_client.db_client.get_last_update_timestamp():
        vehicle_client.save_log()

    result = {"battery_percentage": vehicle_client.vehicle.ev_battery_percentage,
              "accessory_battery_percentage": vehicle_client.vehicle.car_battery_percentage,
              "estimated_range_km": vehicle_client.vehicle.ev_driving_range,
              "last_vehicule_update_timestamp": vehicle_client.vehicle.last_updated_at,
              "odometer": vehicle_client.vehicle.odometer,
              "charging": vehicle_client.vehicle.ev_battery_is_charging,
              "engine_is_running": vehicle_client.vehicle.engine_is_running,
              "rough_charging_power_estimate_kw": vehicle_client.charging_power_in_kilowatts,
              "ac_charge_limit_percent": vehicle_client.vehicle.ev_charge_limits_ac,
              "dc_charge_limit_percent": vehicle_client.vehicle.ev_charge_limits_dc,
              }

    return jsonify(result)


@app.route("/battery")
@auth_required
def get_battery_soc():
    vehicle_client.vm.check_and_refresh_token()
    vehicle_client.vm.update_all_vehicles_with_cached_state()

    if vehicle_client.vehicle.last_updated_at.replace(
            tzinfo=None) > vehicle_client.db_client.get_last_update_timestamp():
        vehicle_client.save_log()

    return str(vehicle_client.vehicle.ev_battery_percentage)


@app.route("/charge")
@auth_required
def toggle_charge():
    """
    Available arguments:
    - action: [start, stop]
    - synchronous: [true, false]
    """
    vehicle_client.vm.check_and_refresh_token()

    action = request.args.get('action')
    wait_for_response = bool(request.args.get('synchronous', False))
    status = OrderStatus.PENDING

    if action == "start":
        os.environ["kia_action_id"] = vehicle_client.vm.start_charge(vehicle_client.vehicle.id)
    elif action == "stop":
        os.environ["kia_action_id"] = vehicle_client.vm.stop_charge(vehicle_client.vehicle.id)
    else:
        return f"unrecognised command: {action}. send start or stop."

    if wait_for_response:
        status = vehicle_client.vm.check_action_status(vehicle_client.vehicle.id, os.environ["kia_action_id"],
                                                       synchronous=True,
                                                       timeout=60)

    return jsonify({"component": "charge", "action": action, "status": status.value})


@app.route("/climate")
@auth_required
def toggle_climate():
    """
    Available arguments:
    - action: [start, stop]
    - temp: target temperature (degrees celcius)
    - duration: duration (minutes)
    - synchronous: [true, false]

    """

    options = ClimateRequestOptions()
    options.set_temp = float(request.args.get('temp', default=22))
    options.duration = request.args.get('duration', default=10)

    vehicle_client.vm.check_and_refresh_token()

    action = request.args.get('action')
    wait_for_response = bool(request.args.get('synchronous', False))
    status = OrderStatus.PENDING

    if action == "start":
        os.environ["kia_action_id"] = vehicle_client.vm.start_climate(vehicle_client.vehicle.id, options)
    elif action == "stop":
        os.environ["kia_action_id"] = vehicle_client.vm.stop_climate(vehicle_client.vehicle.id)
    else:
        return f"unrecognised command: {action}. send start or stop."

    if wait_for_response:
        status = vehicle_client.vm.check_action_status(vehicle_client.vehicle.id, os.environ["kia_action_id"],
                                                       synchronous=True,
                                                       timeout=60)

    return jsonify({"component": "climate", "action": action, "status": status.value})


@app.route("/doors")
@auth_required
def toggle_doors():
    """
    Available arguments:
    - action: [lock, unlock]
    - synchronous: [true, false]
    """
    vehicle_client.vm.check_and_refresh_token()

    action = request.args.get('action')
    wait_for_response = bool(request.args.get('synchronous', False))
    status = OrderStatus.PENDING

    if action == "lock":
        os.environ["kia_action_id"] = vehicle_client.vm.lock(vehicle_client.vehicle.id)
    elif action == "unlock":
        os.environ["kia_action_id"] = vehicle_client.vm.unlock(vehicle_client.vehicle.id)
    else:
        return f"unrecognised command: {action}. send lock or unlock."

    if wait_for_response:
        status = vehicle_client.vm.check_action_status(vehicle_client.vehicle.id, os.environ["kia_action_id"],
                                                       synchronous=True,
                                                       timeout=60)

    return jsonify({"component": "doors", "action": action, "status": status.value})


@app.route("/last_action_status")
def get_last_action_status():
    """
    Get status of the last known sent command
    """
    if os.environ.get("kia_action_id"):
        try:
            status = vehicle_client.vm.check_action_status(vehicle_client.vehicle.id, os.environ["kia_action_id"],
                                                           synchronous=True,
                                                           timeout=20)
            return jsonify({"status": status.value})
        except Exception as e:
            return jsonify({"error": str(e)})
    else:
        return jsonify({"error": "no known action ID"})


if __name__ == "__main__":

    # load env vars
    load_dotenv()

    app.config["SERVER_PASSWORD"] = os.environ["HTTP_SERVER_PASSWORD"]

    if not app.config["SERVER_PASSWORD"]:
        raise Exception("HTTP_SERVER_PASSWORD not set. Exiting.")

    vehicle_client = VehicleClient()
    vehicle_client.vm.check_and_refresh_token()
    vehicle_client.vehicle = vehicle_client.vm.get_vehicle(os.environ["KIA_VEHICLE_UUID"])

    app.run(port=8000, host='0.0.0.0', debug=True)
