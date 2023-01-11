import argparse
import datetime
import logging
import os
import sqlite3
import subprocess
import time
from enum import Enum

import coloredlogs
from sqlite3 import Error

from VehicleClient import VehicleClient
from hyundai_kia_connect_api.VehicleManager import VehicleManager


def log_error_to_database(exception: Exception):
    try:
        conn = sqlite3.connect("log.db",
                               detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
    except Error as e:
        print(e)

    cur = conn.cursor()
    cur.execute(''' INSERT INTO errors(
               timestamp,
               unix_timestamp,
               exc_type,
               exc_args
     )
                 VALUES(?, ?, ?, ?)
                 ''',
                (
                    datetime.datetime.now(),
                    round(datetime.datetime.timestamp(datetime.datetime.now())),
                    type(exception).__name__,
                    str(exception.args)
                ))
    conn.commit()


def check_if_laptop_is_asleep():
    """
    It looks like the program resumes execution periodically while my macbook sleeps.
    This causes urllib to hang.
    To mitigate this problem, this function checks whether the laptop is awake.
    source: https://stackoverflow.com/questions/42635378/detect-whether-host-is-in-sleep-or-awake-state-in-macos
    """
    result = subprocess.run(['system_profiler', 'SPDisplaysDataType'], stdout=subprocess.PIPE)
    if "Display Asleep" in result.stdout.decode():
        return True
    else:
        return False


if __name__ == '__main__':

    logger = logging.getLogger(__name__)
    coloredlogs.install(level='DEBUG', isatty=True)

    parser = argparse.ArgumentParser()

    parser.add_argument("--interval", type=int)
    args = parser.parse_args()

    vm = VehicleManager(region=1, brand=1, username=os.environ["KIA_USERNAME"], password=os.environ["KIA_PASSWORD"],
                        pin="")

    vehicle_client = VehicleClient()

    if args.interval:
        vehicle_client.interval_in_seconds = args.interval

    while True:

        if check_if_laptop_is_asleep():
            logger.info("Laptop asleep, will check back in 60 seconds")
            time.sleep(60)
            continue

        logger.info("refreshing token...")
        if len(vm.vehicles) == 0 and vm.token:
            # supposed bug in lib: if initialization fails due to rate limiting, vehicles list is never filled
            # reset token to login again, the lib will then fill the list correctly
            vm.token = None
        try:
            # this command does NOT refresh vehicles (at least for EU and if there is not a preexisting token)
            vm.check_and_refresh_token()
            vehicle_client.vehicle = vm.get_vehicle(os.environ["KIA_VEHICLE_UUID"])
            # fetch cached status, but do not retrieve driving info (driving stats) just yet, to prevent making too
            # many API calls. yes, cached calls also increment the API limit counter.
            response = vm.api._get_cached_vehicle_state(vm.token, vehicle_client.vehicle)
            vm.api._update_vehicle_properties(vehicle_client.vehicle, response)
            #vm.update_vehicle_with_cached_state(vehicle_client.vehicle)
        except Exception as e:
            logger.exception("failed to refresh token and pull cached data:", exc_info=e)
            log_error_to_database(exception=e)
            logger.info("sleeping for 60 seconds before next attempt")
            time.sleep(60)
            continue

        if vehicle_client.vehicle.last_updated_at.replace(tzinfo=None) > vehicle_client.get_last_update_timestamp_from_database():
            # it's not time to force refresh yet, but we still have data on the server
            # that is more recent that our last saved data, so we save it
            response = vm.api._get_driving_info(vm.token, vehicle_client.vehicle)
            vm.api._update_vehicle_drive_info(vehicle_client.vehicle, response)

            vehicle_client.save_data()

        if vehicle_client.vehicle.engine_is_running:
            # for an EV: "engine running" supposedly means the contact is set and the car is "ready to drive"
            # engine is also reported as "running" in utility mode.
            vehicle_client.interval_in_seconds = 300  # 5 minutes
            charging_power_in_kilowatts = 0
        elif vehicle_client.vehicle.ev_battery_is_charging:
            # battery is charging, we can poll more often without draining the 12v battery
            if vehicle_client.charge_type == ChargeType.DC:
                vehicle_client.interval_in_seconds = 300  # 5 minutes
            elif vehicle_client.charge_type in (ChargeType.AC, ChargeType.UNKNOWN):
                vehicle_client.interval_in_seconds = 1800  # 30 minutes

        delta = datetime.datetime.now() - vehicle_client.get_last_update_timestamp_from_database()
        if delta.total_seconds() <= vehicle_client.interval_in_seconds:
            logger.info(f"{str(int((vehicle_client.interval_in_seconds - delta.total_seconds()) / 60))} minutes left "
                        f"before next force refresh")
            time.sleep(60)
            continue

        logger.info("performing force refresh...")
        try:
            vm.force_refresh_vehicle_state(vehicle_client.vehicle)
        except Exception as e:
            logger.exception(f"failed getting forced vehicle data:", exc_info=e)
            log_error_to_database(exception=e)
            logger.info("sleeping for 60 seconds before next attempt")
            time.sleep(60)
            continue

        logger.info(f"Data retrieved from car.")
        vm.update_vehicle_with_cached_state(vehicle_client.vehicle)
        vehicle_client.save_data()
