import datetime
import logging
import os
from enum import Enum

from dateutil.relativedelta import relativedelta
from dotenv import load_dotenv

from DatabaseClient import DatabaseClient
from hyundai_kia_connect_api import Vehicle, VehicleManager
from hyundai_kia_connect_api.exceptions import RateLimitingError, APIError, RequestTimeoutError


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

        # load env vars from .env file
        load_dotenv()

        self.db_client = DatabaseClient(self)

        self.interval_in_seconds: int = 3600 * 4  # default
        self.charging_power_in_kilowatts: int = 0  # default = 0 (not charging)
        self.charge_type: ChargeType = ChargeType.UNKNOWN
        self.vehicle: [Vehicle, None] = None
        self.vm = None
        self.logger = None
        self.trips = None  # vehicle trips. better motel than the one in the library

        # interval in seconds between checks for cached requests
        # we are limited to 200 requests a day, including cached
        # that's about one every 8 minutes
        # we set it to 4 hours for cached refreshes.
        self.CACHED_REFRESH_INTERVAL = 3600 * 4

        self.CAR_OFF_FORCE_REFRESH_INTERVAL = 3600 * 6

        self.ENGINE_RUNNING_FORCE_REFRESH_INTERVAL = 600
        self.DC_CHARGE_FORCE_REFRESH_INTERVAL = 1800
        self.AC_CHARGE_FORCE_REFRESH_INTERVAL = 1800

        self.vm = VehicleManager(region=1, brand=1, username=os.environ["KIA_USERNAME"],
                                 password=os.environ["KIA_PASSWORD"],
                                 pin="")

    def get_estimated_charging_power(self):
        """
        Roughly estimates charging speed based on:
        - charge limits for both AC and DC charging
        - current battery percentage (SoC) as reported by the car
        - external temperature
        - charging time remaining as reported by the car
        :return:
        """

        if not self.vehicle.ev_battery_is_charging:
            return 0

        estimated_niro_total_kwh_needed = 70  # 64 usable kwh + unusable kwh + charger losses

        percent_remaining = 100 - self.vehicle.ev_battery_percentage
        kwh_remaining = estimated_niro_total_kwh_needed * percent_remaining / 100

        print(f"Kilowatthours needed for full battery: {kwh_remaining} kWh")

        # todo: there is a bug here: kwh_remaining does not take charge limits into account.
        #  however the "estimated charge time" provided by the car does.
        #  so this formula returns too high values (ex: 20kw when AC charging at home).
        charging_power_in_kilowatts = kwh_remaining / (self.vehicle.ev_estimated_current_charge_duration / 60)

        # the delta calculation between ac limits and percentage is a temporary fix for the todo above
        if (charging_power_in_kilowatts > 8
                and self.vehicle.ev_charge_limits_ac - self.vehicle.ev_battery_percentage > 15):

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

            if self.vehicle.ev_battery_percentage > 95:
                charging_power_in_kilowatts = min(5, charging_power_in_kilowatts)
            elif self.vehicle.ev_battery_percentage > 90:
                charging_power_in_kilowatts = min(10, charging_power_in_kilowatts)
            elif self.vehicle.ev_battery_percentage > 80:
                charging_power_in_kilowatts = min(20, charging_power_in_kilowatts)
            elif self.vehicle.ev_battery_percentage > 75:
                charging_power_in_kilowatts = min(35, charging_power_in_kilowatts)
            elif self.vehicle.ev_battery_percentage > 55:
                charging_power_in_kilowatts = min(55, charging_power_in_kilowatts)
            elif self.vehicle.ev_battery_percentage > 40:
                charging_power_in_kilowatts = min(70, charging_power_in_kilowatts)
            elif self.vehicle.ev_battery_percentage > 27:
                charging_power_in_kilowatts = min(77, charging_power_in_kilowatts)

        else:
            self.charge_type = ChargeType.AC

        print(f"Estimated charging power: {round(charging_power_in_kilowatts, 1)} kW")
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
        oldest_saved_date = self.db_client.get_most_recent_saved_trip_timestamp() or datetime.datetime(2020, 1, 1)
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
                    if datetime.datetime.strptime(day.yyyymmdd,
                                                  "%Y%m%d") < self.db_client.get_most_recent_saved_trip_timestamp():
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
                            self.db_client.save_trip(day, trip)

    def save_log(self):

        if self.vehicle.ev_battery_is_charging:

            self.get_estimated_charging_power()

            estimated_end_datetime = datetime.datetime.now() + datetime.timedelta(
                minutes=self.vehicle.ev_estimated_current_charge_duration)
            logging.info(f"Estimated end time: {estimated_end_datetime.strftime('%d/%m/%Y at %H:%M')}")
        else:
            # battery is not charging nor is the engine running
            self.charging_power_in_kilowatts = 0

        self.db_client.save_log()

    def handle_api_exception(self, exc: Exception):
        """
        In case of API error, this function defines what to do:
        - log error
        - sleep
        :param exc: the Exception returned by the library
        """

        # rate limiting: we are blocked for 24 hours
        if isinstance(exc, RateLimitingError):
            self.logger.exception(
                "we got rate limited, probably exceeded 200 requests. exiting",
                exc_info=exc)
            self.db_client.log_error(exception=exc)
            # time.sleep(3600 * 4)
            return

        # request timeout: vehicle could not be reached.
        # to prevent too many unsuccessful requests in a row (which would lead to rate limiting) we sleep for a while.
        elif isinstance(exc, RequestTimeoutError):
            self.logger.exception(
                "The vehicle did not respond. Exiting to prevent too many unsuccessful requests "
                "that would lead to rate limiting ",
                exc_info=exc)
            self.db_client.log_error(exception=exc)
            return
            # time.sleep(3600)

        # broad API error
        elif isinstance(exc, APIError):
            self.logger.exception("server responded with error:", exc_info=exc)
            self.db_client.log_error(exception=exc)
            return
            # self.logger.info("sleeping for 60 seconds before next attempt")
            # time.sleep(60)

        # any other exception
        else:
            self.logger.exception("generic error:", exc_info=exc)
            self.db_client.log_error(exception=exc)
            return
            # self.logger.info("sleeping for 60 seconds before next attempt")
            # time.sleep(60)

    def refresh(self):
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
            return

        self.vehicle = self.vm.get_vehicle(os.environ["KIA_VEHICLE_UUID"])
        # fetch cached status, but do not retrieve driving info (driving stats) just yet, to prevent making too
        # many API calls. yes, cached calls also increment the API limit counter.

        try:
            response = self.vm.api._get_cached_vehicle_state(self.vm.token, self.vehicle)
        except Exception as e:
            self.handle_api_exception(e)
            return

        self.vm.api._update_vehicle_properties(self.vehicle, response)

        self.get_estimated_charging_power()

        self.set_interval()

        # compare odometers. higher odo means we drove and new data must be pulled
        if self.vehicle.odometer > self.db_client.get_last_update_odometer():
            # it's not time to force refresh yet, but we might still have data on the server
            # that is more recent that our last saved data, so we save it

            try:
                response = self.vm.api._get_driving_info(self.vm.token, self.vehicle)
            except Exception as e:
                self.handle_api_exception(e)
                return

            self.vm.api._update_vehicle_drive_info(self.vehicle, response)
            self.db_client.save_daily_stats()
            self.get_estimated_charging_power()
            # process_trips() does at least 2 API calls even when there are no new trips.
            self.process_trips()

        db_last_update_ts = self.db_client.get_last_update_timestamp()

        # if vehicle state has changed, then save an entry
        if self.vehicle.last_updated_at.replace(tzinfo=None) > db_last_update_ts:
            self.logger.info("Cached data found, saving log...")
            self.save_log()

        delta = datetime.datetime.now() - self.vehicle.last_updated_at.replace(tzinfo=None)

        self.logger.info(f"Delta between last saved update and current time: {int(delta.total_seconds())} seconds")

        if delta.total_seconds() < 0:
            self.logger.error(
                f"Negative delta ({delta.total_seconds()}s), probably a timezone issue. Check your logic.")
            raise RuntimeError()

        if delta.total_seconds() > self.interval_in_seconds:
            self.logger.info("Performing force refresh...")
            try:
                self.vm.force_refresh_vehicle_state(self.vehicle.id)
            except Exception as e:
                self.handle_api_exception(e)
                return

            self.logger.info(f"Data received by server. Now retrieving from server...")

            try:
                self.vm.update_vehicle_with_cached_state(self.vehicle.id)
            except Exception as e:
                self.handle_api_exception(e)
                return

            self.get_estimated_charging_power()

            self.set_interval()

            # process and save data to database.
            self.save_log()

    def set_interval(self):
        if self.vehicle.engine_is_running and not self.vehicle.ev_battery_is_charging:
            # for an EV: "engine running" supposedly means the contact is set and the car is "ready to drive"
            # engine is also reported as "running" in utility mode.
            self.interval_in_seconds = self.ENGINE_RUNNING_FORCE_REFRESH_INTERVAL
            self.charging_power_in_kilowatts = 0
        elif self.vehicle.ev_battery_is_charging:
            # battery is charging, we can poll more often without draining the 12v battery
            if self.charge_type == ChargeType.DC:
                self.interval_in_seconds = self.DC_CHARGE_FORCE_REFRESH_INTERVAL
            elif self.charge_type in (ChargeType.AC, ChargeType.UNKNOWN):
                self.interval_in_seconds = self.AC_CHARGE_FORCE_REFRESH_INTERVAL
        else:
            # car is off
            self.interval_in_seconds = self.CAR_OFF_FORCE_REFRESH_INTERVAL
