import datetime
import logging
import os
import sqlite3
import subprocess
import sys
import time
from enum import Enum
from sqlite3 import Error

from dateutil.relativedelta import relativedelta
from hyundai_kia_connect_api.Vehicle import TripInfo
from hyundai_kia_connect_api.exceptions import RateLimitingError, APIError

from hyundai_kia_connect_api import Vehicle, VehicleManager


class ChargeType(Enum):
    DC = "DC"
    AC = "AC"
    UNKNOWN = "UNKNOWN"


class VehicleClient:
    """
    Vehicle client class
    Role:
    - store data into database
    - handle additional (calculated) attributes that the API does not provide
    """

    def __init__(self):
        self.interval_in_seconds: int = 3600  # default
        self.charging_power_in_kilowatts: int = 0  # default = 0 (not charging)
        self.charge_type: ChargeType = ChargeType.UNKNOWN
        self.vehicle: [Vehicle, None] = None
        self.vm = None
        self.logger = None
        self.trips = None  # vehicle trips. better motel than the one in the library

        # interval in seconds between checks for cached requests
        # we are limited to 200 requests a day, including cached
        # that's about one every 8 minutes
        # we set it to 30 minutes for cached refreshes.
        self.CACHED_REFRESH_INTERVAL = 1800

        self.ENGINE_RUNNING_FORCE_REFRESH_INTERVAL = 300
        self.DC_CHARGE_FORCE_REFRESH_INTERVAL = 300
        self.AC_CHARGE_FORCE_REFRESH_INTERVAL = 1800

        self.vm = VehicleManager(region=1, brand=1, username=os.environ["KIA_USERNAME"],
                                 password=os.environ["KIA_PASSWORD"],
                                 pin="")

    def get_last_update_timestamp_from_database(self) -> datetime.datetime:
        try:
            conn = sqlite3.connect("log.db",
                                   detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
        except Error as e:
            print(e)

        cur = conn.cursor()

        sql = 'SELECT MAX(unix_last_vehicle_update_timestamp) FROM log;'
        cur.execute(sql)
        rows = cur.fetchone()

        return datetime.datetime.fromtimestamp(rows[0])

    def insert_data_to_database(self):
        """
        Inserts a data point into the log database
        """

        try:
            conn = sqlite3.connect("log.db",
                                   detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
        except Error as e:
            print(e)
            sys.exit()

        latitude = self.vehicle.location_latitude or 'NULL'
        longitude = self.vehicle.location_longitude or 'NULL'

        if self.vehicle.odometer:
            odometer = int(self.vehicle.odometer)
        else:
            odometer = 0

        cur = conn.cursor()

        #
        # # fetch the last known vehicule force refresh timestamp.
        # sql = 'SELECT MAX(unix_last_vehicle_update_timestamp) FROM log;'
        # cur.execute(sql)
        # rows = cur.fetchone()
        #
        # # if we already have that timestamp logged, we don't need to store the data again.
        # if rows[0] == round(datetime.datetime.timestamp(self.vehicle.last_updated_at)):
        #     logging.info("Most recent vehicle report already saved to database. Skipping.")
        # else:
        sql = f'''INSERT INTO log(
                    battery_percentage,
                    accessory_battery_percentage,
                    estimated_range_km,
                    timestamp,
                    unix_timestamp,
                    last_vehicule_update_timestamp,
                    unix_last_vehicle_update_timestamp,
                    latitude,
                    longitude,
                    odometer,
                    charging,
                    engine_is_running,
                    rough_charging_power_estimate_kw,
                    ac_charge_limit_percent,
                    dc_charge_limit_percent,
                    target_climate_temperature,
                    raw_api_data
      )
                  VALUES(
                      {self.vehicle.ev_battery_percentage},
                      {self.vehicle.car_battery_percentage},
                      {self.vehicle.ev_driving_range},
                      '{datetime.datetime.now()}',
                      {round(datetime.datetime.timestamp(datetime.datetime.now()))},
                      '{self.vehicle.last_updated_at}',
                      {round(datetime.datetime.timestamp(self.vehicle.last_updated_at))},
                      {latitude},
                      {longitude},
                      {odometer},
                      {1 if self.vehicle.ev_battery_is_charging else 0},
                      {1 if self.vehicle.engine_is_running else 0},
                      {self.charging_power_in_kilowatts},
                      {self.vehicle.ev_charge_limits_ac or 100},
                      {self.vehicle.ev_charge_limits_dc or 100},
                      {self.vehicle.air_temperature},
                      "{self.vehicle.data}"
                  ) '''
        print(sql)
        cur.execute(sql)
        conn.commit()

        # for each day, check if day already saved in database to prevent duplicates
        sql = 'SELECT date FROM stats_per_day;'
        cur.execute(sql)
        rows = cur.fetchall()

        for day in self.vehicle.daily_stats:

            if any(day.date.strftime("%Y-%m-%d") == row[0] for row in rows):
                # delete saved day (we'll replace it with the most up-to-date data for this day)
                logging.debug(f'deleting previously saved day: {day.date.strftime("%Y-%m-%d")}')
                sql = f"""DELETE FROM stats_per_day
                WHERE date = '{day.date.strftime("%Y-%m-%d")}'"""
                cur.execute(sql)

            average_consumption = 0
            average_consumption_regen_deducted = 0
            if day.distance > 0:
                average_consumption = day.total_consumed / (100 / day.distance)
                average_consumption_regen_deducted = (day.total_consumed - day.regenerated_energy) / (
                        100 / day.distance)

            sql = f''' INSERT INTO stats_per_day(
                       date,
                       unix_timestamp,
                       total_consumed_kwh,
                       engine_consumption_kwh,
                       climate_consumption_kwh,
                       onboard_electronics_consumption_kwh,
                       battery_care_consumption_kwh,
                       regenerated_energy_kwh,
                       distance,
                       average_consumption_kwh,
                       average_consumption_regen_deducted_kwh
             )
                         VALUES(
                             '{day.date.strftime("%Y-%m-%d")}',
                             {round(datetime.datetime.timestamp(day.date))},
                             {round(day.total_consumed / 1000, 1)},
                             {round(day.engine_consumption / 1000, 1)},
                             {round(day.climate_consumption / 1000, 1)},
                             {round(day.onboard_electronics_consumption / 1000, 1)},
                             {round(day.battery_care_consumption / 1000, 1)},
                             {round(day.regenerated_energy / 1000, 1)},
                             {day.distance},
                             {round(average_consumption / 1000, 1)},
                             {round(average_consumption_regen_deducted / 1000, 1)}
                         ) '''
            cur = conn.cursor()
            cur.execute(sql)
            conn.commit()

    def get_estimated_charging_power(self):
        """
        Roughly estimates charging speed based on:
        - charge limits for both AC and DC charging
        - current battery percentage (SoC) as reported by the car
        - external temperature
        - charging time remaining as reported by the car
        :return:
        """
        estimated_niro_total_kwh_needed = 70  # 64 usable kwh + unusable kwh + charger losses

        percent_remaining = self.vehicle.ev_charge_limits_ac - self.vehicle.ev_battery_percentage
        kwh_remaining = estimated_niro_total_kwh_needed * percent_remaining / 100

        print(f"Kilowatthours needed for full battery: {kwh_remaining} kWh")

        charging_power_in_kilowatts = kwh_remaining / (self.vehicle.ev_estimated_current_charge_duration / 60)

        if charging_power_in_kilowatts > 11:

            # the car's onboard AC charger cannot exceed 7kW, or 11kW with the optional upgrade
            # if power > 11kW, then assume we are DC charging. recalculate values to take DC charge limits into account
            self.charge_type = ChargeType.DC
            percent_remaining = self.vehicle.ev_charge_limits_dc - self.vehicle.ev_battery_percentage
            kwh_remaining = estimated_niro_total_kwh_needed * percent_remaining / 100
            self.charging_power_in_kilowatts = kwh_remaining / (self.vehicle.ev_estimated_current_charge_duration / 60)

            # DC charging coldgate simulation
            # if the temperature of the battery drops below a certain value, then the BMS will limit the
            # charging power.
            # the rules are roughly:
            # - below 5°c: limited to 22kW
            # - below 15°c: limited to 43kW
            # - below 25°c: limited to 56kW
            # - above 25°c: max: 77kW (except maybe if battery gets too hot)
            # here, we assume that the battery temperature is roughly 5°c above reported outside temperature.
            # we apply a 5°c delta.
            # source: https://www.mojelektromobil.sk/pomale-rychlo-nabijanie-v-chladnom-pocasi-alias-coldgate-blog

            # DISABLED: we don't have access to the outside air temperature through the API
            # if self.vehicle.air_temperature <= 0:
            #     charging_power_in_kilowatts = min(22, charging_power_in_kilowatts)
            # elif self.vehicle.air_temperature <= 10:
            #     charging_power_in_kilowatts = min(43, charging_power_in_kilowatts)
            # elif self.vehicle.air_temperature <= 20:
            #     charging_power_in_kilowatts = min(56, charging_power_in_kilowatts)

            # simulate DC charging power curve for 64kWh e-niro
            # source: https://support.fastned.nl/hc/fr/articles/4408899202193-Kia
            if self.vehicle.ev_battery_percentage > 90:
                charging_power_in_kilowatts = min(20, charging_power_in_kilowatts)
            elif self.vehicle.ev_battery_percentage > 80:
                charging_power_in_kilowatts = min(25, charging_power_in_kilowatts)
            elif self.vehicle.ev_battery_percentage > 70:
                charging_power_in_kilowatts = min(38, charging_power_in_kilowatts)
            elif self.vehicle.ev_battery_percentage > 55:
                charging_power_in_kilowatts = min(58, charging_power_in_kilowatts)
            elif self.vehicle.ev_battery_percentage > 40:
                charging_power_in_kilowatts = min(70, charging_power_in_kilowatts)

        else:
            self.charge_type = ChargeType.AC

        print(f"Estimated charging speed: {round(charging_power_in_kilowatts, 1)} kW")
        self.charging_power_in_kilowatts = round(charging_power_in_kilowatts, 1)

    def process_trips(self):
        """
        Get, process and save trip info
        A trip contains the following data:
        - timestamp
        - engine time
        - idle time
        - distance
        - max speed
        - average speed
        """

        # using 2020-01-01 as default date
        # we don't want to go too far back to prevent rate limiting
        oldest_saved_date = self.get_most_recent_saved_trip() or datetime.datetime(2020, 1, 1)
        current_date = datetime.datetime.now()

        months_list = []

        # create a list of months to iterate through, in the API's format:
        # 202001 (jan 2020)
        # 202002 (feb 2020)
        # 202003 (mar 2020)
        # etc...

        while oldest_saved_date < current_date:
            # expected format: YYYYMM
            months_list.append(oldest_saved_date.strftime("%Y%m"))
            oldest_saved_date += relativedelta(months=1)

        for yyyymm in months_list:
            try:
                self.vm.update_month_trip_info(self.vehicle.id, yyyymm)
            except Exception as e:
                self.handle_api_exception(e)
                return

            if self.vehicle.month_trip_info is not None:
                for day in self.vehicle.month_trip_info.day_list:  # ordered on day
                    # warning: this causes an API call.
                    # skip this day if already saved in db
                    if datetime.datetime.strptime(day.yyyymmdd, "%Y%m%d") < self.get_most_recent_saved_trip():
                        continue

                    try:
                        self.vm.update_day_trip_info(self.vehicle.id, day.yyyymmdd)
                    except Exception as e:
                        self.handle_api_exception(e)
                        return

                    # we need to save to database in this loop, because we depend on the currently selected day
                    if self.vehicle.day_trip_info is not None:
                        day = datetime.datetime.strptime(self.vehicle.day_trip_info.yyyymmdd, "%Y%m%d")
                        for trip in reversed(self.vehicle.day_trip_info.trip_list):  # show oldest first
                            self.save_trip_to_database(day, trip)

    def get_most_recent_saved_trip(self):
        try:
            conn = sqlite3.connect("log.db",
                                   detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
        except Error as e:
            print(e)
            sys.exit()

        cur = conn.cursor()

        # # fetch the last known vehicule force refresh timestamp.
        sql = 'SELECT MAX(unix_timestamp) FROM trips;'
        cur.execute(sql)
        rows = cur.fetchone()

        try:
            return datetime.datetime.fromtimestamp(int(rows[0]))
        except Exception as e:
            self.logger.exception(e)
            return None

    def save_trip_to_database(self, date: datetime.datetime, trip: TripInfo):
        """
        Saves a trip into the database.
        :param date: date of the trip
        :param trip: the trip
        :return:
        """
        try:
            conn = sqlite3.connect("log.db",
                                   detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
        except Error as e:
            print(e)
            sys.exit()

        cur = conn.cursor()

        hours = int(trip.hhmmss[:2])
        minutes = int(trip.hhmmss[2:4])
        seconds = int(trip.hhmmss[4:])

        timestamp = date + datetime.timedelta(hours=hours, minutes=minutes, seconds=seconds)

        sql = f'''
        INSERT INTO trips(
            	unix_timestamp,
            	date,
                driving_time_minutes,
                idle_time_minutes,
                distance_km,
                avg_speed_kmh,
                max_speed_kmh
        )
                    VALUES(
                        {round(datetime.datetime.timestamp(timestamp))},
                        "{timestamp.strftime("%Y-%m-%d %H:%M")}",
                        {trip.drive_time},
                        {trip.idle_time},
                        {trip.distance},
                        {trip.avg_speed},
                        {trip.max_speed}
                    )'''
        print(sql)
        cur.execute(sql)
        conn.commit()

    def save_data(self):

        logging.info(f"Battery: {self.vehicle.ev_battery_percentage}%")

        if self.vehicle.ev_battery_is_charging:

            self.get_estimated_charging_power()

            estimated_end_datetime = datetime.datetime.now() + datetime.timedelta(
                minutes=self.vehicle.ev_estimated_current_charge_duration)
            logging.info(f"Estimated end time: {estimated_end_datetime.strftime('%d/%m/%Y at %H:%M')}")
        else:
            # battery is not charging nor is the engine running
            # reduce polling interval to prevent draining the 12 battery
            self.interval_in_seconds = 3600
            self.charging_power_in_kilowatts = 0

        self.process_trips()

        self.insert_data_to_database()

    def check_if_laptop_is_asleep(self):
        """
        It looks like the program resumes execution periodically while my macbook sleeps.
        This causes urllib to hang.
        To mitigate this problem, this function checks whether the laptop is awake.
        source: https://stackoverflow.com/questions/42635378/detect-whether-host-is-in-sleep-or-awake-state-in-macos
        """
        try:
            result = subprocess.run(['system_profiler', 'SPDisplaysDataType'], stdout=subprocess.PIPE)
        except FileNotFoundError:
            self.logger.debug("Can't check laptop status. Running on a non-mac device?")
            return False
        if "Display Asleep" in result.stdout.decode():
            return True
        else:
            return False

    def log_error_to_database(self, exception: Exception):
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

    def handle_api_exception(self, exc: Exception):

        if type(exc) == RateLimitingError:
            self.logger.exception(
                "we got rate limited, probably exceeded 200 requests. sleeping for 1 hour before next attempt",
                exc_info=exc)
            self.log_error_to_database(exception=exc)
            time.sleep(3600)

        elif type(exc) == APIError:
            self.logger.exception("server responded with error:", exc_info=exc)
            self.log_error_to_database(exception=exc)
            self.logger.info("sleeping for 60 seconds before next attempt")
            time.sleep(60)

        elif type(exc) == Exception:
            self.logger.exception("generinc error:", exc_info=exc)
            self.log_error_to_database(exception=exc)
            self.logger.info("sleeping for 60 seconds before next attempt")
            time.sleep(60)

    def loop(self):
        while True:

            if self.check_if_laptop_is_asleep():
                self.logger.info("Laptop asleep, will check back in 60 seconds")
                time.sleep(60)
                continue

            self.logger.info("refreshing token...")

            if len(self.vm.vehicles) == 0 and self.vm.token:
                # supposed bug in lib: if initialization fails due to rate limiting, vehicles list is never filled
                # reset token to login again, the lib will then fill the list correctly
                self.vm.token = None
            # this command does NOT refresh vehicles (at least for EU and if there is not a preexisting token)
            try:
                self.vm.check_and_refresh_token()
            except Exception as e:
                self.handle_api_exception(e)
                continue

            self.vehicle = self.vm.get_vehicle(os.environ["KIA_VEHICLE_UUID"])
            # fetch cached status, but do not retrieve driving info (driving stats) just yet, to prevent making too
            # many API calls. yes, cached calls also increment the API limit counter.

            try:
                response = self.vm.api._get_cached_vehicle_state(self.vm.token, self.vehicle)
            except Exception as e:
                self.handle_api_exception(e)
                continue

            self.vm.api._update_vehicle_properties(self.vehicle, response)

            if self.vehicle.last_updated_at.replace(
                    tzinfo=None) > self.get_last_update_timestamp_from_database():
                # it's not time to force refresh yet, but we still have data on the server
                # that is more recent that our last saved data, so we save it

                # perform get_driving_info only now that we're sure there is no data.
                # otherwise we waste precious API calls (rate limiting)
                response = self.vm.api._get_driving_info(self.vm.token, self.vehicle)
                self.vm.api._update_vehicle_drive_info(self.vehicle, response)

                self.save_data()

            if self.vehicle.engine_is_running:
                # for an EV: "engine running" supposedly means the contact is set and the car is "ready to drive"
                # engine is also reported as "running" in utility mode.
                self.interval_in_seconds = self.ENGINE_RUNNING_FORCE_REFRESH_INTERVAL
                charging_power_in_kilowatts = 0
            elif self.vehicle.ev_battery_is_charging:
                # battery is charging, we can poll more often without draining the 12v battery
                if self.charge_type == ChargeType.DC:
                    self.interval_in_seconds = self.DC_CHARGE_FORCE_REFRESH_INTERVAL
                elif self.charge_type in (ChargeType.AC, ChargeType.UNKNOWN):
                    self.interval_in_seconds = self.AC_CHARGE_FORCE_REFRESH_INTERVAL

            delta = datetime.datetime.now() - self.get_last_update_timestamp_from_database()
            if delta.total_seconds() <= self.interval_in_seconds:
                self.logger.info(f"{str(int((self.interval_in_seconds - delta.total_seconds()) / 60))} minutes left "
                                 f"before next force refresh")
                time.sleep(min(self.CACHED_REFRESH_INTERVAL, self.interval_in_seconds - delta.total_seconds()))
                continue

            self.logger.info("Performing force refresh...")
            try:
                self.vm.force_refresh_vehicle_state(self.vehicle.id)
            except Exception as e:
                self.handle_api_exception(e)
                continue

            self.logger.info(f"Data received by server. Now retrieving from server...")

            try:
                self.vm.update_vehicle_with_cached_state(self.vehicle.id)
            except Exception as e:
                self.handle_api_exception(e)
                continue

            self.save_data()
