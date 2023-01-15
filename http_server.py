import os

from flask import Flask

from VehicleClient import VehicleClient

app = Flask(__name__)


@app.route("/force_refresh")
def force_refresh():
    vehicle_client = VehicleClient()
    vehicle_client.vm.check_and_refresh_token()
    vehicle_client.vehicle = vehicle_client.vm.get_vehicle(os.environ["KIA_VEHICLE_UUID"])
    vehicle_client.vm.force_refresh_vehicle_state(vehicle_client.vehicle.id)
    return "OK"


if __name__ == "__main__":
    app.run(port=8000, host='0.0.0.0', debug=True)
