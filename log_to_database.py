import argparse
import logging

import coloredlogs

from VehicleClient import VehicleClient

logger = logging.getLogger(__name__)
coloredlogs.install(level='DEBUG', isatty=True)

if __name__ == '__main__':
    vehicle_client = VehicleClient()
    vehicle_client.logger = logger

    parser = argparse.ArgumentParser()

    parser.add_argument("--interval", type=int)
    args = parser.parse_args()

    if args.interval:
        vehicle_client.interval_in_seconds = args.interval
    else:
        vehicle_client.interval_in_seconds = vehicle_client.CACHED_REFRESH_INTERVAL

    vehicle_client.loop()
