import datetime
import sqlite3
import sys
from enum import Enum
from sqlite3 import Error

from hyundai_kia_connect_api import Vehicle
from log_to_database import logger


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
        #     logger.info("Most recent vehicle report already saved to database. Skipping.")
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
                logger.debug(f'deleting previously saved day: {day.date.strftime("%Y-%m-%d")}')
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

    def save_data(self):

        logger.info(f"Battery: {self.vehicle.ev_battery_percentage}%")

        if self.vehicle.ev_battery_is_charging:

            self.get_estimated_charging_power()

            estimated_end_datetime = datetime.datetime.now() + datetime.timedelta(
                minutes=self.vehicle.ev_estimated_current_charge_duration)
            logger.info(f"Estimated end time: {estimated_end_datetime.strftime('%d/%m/%Y at %H:%M')}")
        else:
            # battery is not charging nor is the engine running
            # reduce polling interval to prevent draining the 12 battery
            self.interval_in_seconds = 3600
            self.charging_power_in_kilowatts = 0

        # todo only log if we got more recent telemetry from the car.
        # OR insert a record saying the latest telemetry was not updated.
        self.insert_data_to_database()
