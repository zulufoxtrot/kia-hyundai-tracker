import datetime
import logging
import os
import sqlite3
from sqlite3 import Connection

import VehicleClient
from hyundai_kia_connect_api.Vehicle import TripInfo


class DatabaseClient:

    def __init__(self, vehicle_client: VehicleClient):
        self.db_path = os.environ["KIA_DB_PATH"]

        if not self.db_path:
            raise NameError("KIA_DB_PATH env var is empty or undefined")

        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"DB file not found: {self.db_path}")

        self.vehicle_client = vehicle_client

    def create_connection(self) -> Connection:
        conn = sqlite3.connect(self.db_path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
        return conn

    def get_last_update_timestamp(self) -> datetime.datetime:

        conn = self.create_connection()
        cur = conn.cursor()

        sql = 'SELECT MAX(unix_last_vehicle_update_timestamp) FROM log;'
        cur.execute(sql)
        rows = cur.fetchone()

        return datetime.datetime.fromtimestamp(rows[0])

    def get_last_update_odometer(self) -> float:

        conn = self.create_connection()
        cur = conn.cursor()

        sql = 'SELECT MAX(odometer) FROM log;'
        cur.execute(sql)
        rows = cur.fetchone()

        return rows[0]

    def get_most_recent_saved_trip_timestamp(self):
        conn = self.create_connection()

        cur = conn.cursor()

        # # fetch the last known vehicule force refresh timestamp.
        sql = 'SELECT MAX(unix_timestamp) FROM trips;'
        cur.execute(sql)
        rows = cur.fetchone()

        try:
            return datetime.datetime.fromtimestamp(int(rows[0]))
        except Exception as e:
            logging.exception(e)
            return None

    def save_trip(self, date: datetime.datetime, trip: TripInfo):
        """
        Saves a trip into the database.
        :param date: date of the trip
        :param trip: the trip
        :return:
        """
        conn = self.create_connection()
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

    def save_log(self):
        """
        Inserts a data point into the log database
        """

        conn = self.create_connection()
        cur = conn.cursor()

        latitude = self.vehicle_client.vehicle.location_latitude or 'NULL'
        longitude = self.vehicle_client.vehicle.location_longitude or 'NULL'

        if self.vehicle_client.vehicle.odometer:
            odometer = int(self.vehicle_client.vehicle.odometer)
        else:
            odometer = 0

        # when performing a force refresh, the server is supposed to return updated timestamps for
        # both vehicle properties AND vehicle location. For some reason it sometimes only updates the location time.
        # this causes problems down the line because the timestamp we compare is too old. to prevent this,
        # store the max value.
        last_vehicle_update_ts = max(self.vehicle_client.vehicle.last_updated_at,
                                     self.vehicle_client.vehicle.location_last_updated_at
                                     )

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
                      {self.vehicle_client.vehicle.ev_battery_percentage},
                      {self.vehicle_client.vehicle.car_battery_percentage},
                      {self.vehicle_client.vehicle.ev_driving_range},
                      '{datetime.datetime.now()}',
                      {round(datetime.datetime.timestamp(datetime.datetime.now()))},
                      '{last_vehicle_update_ts}',
                      {round(datetime.datetime.timestamp(last_vehicle_update_ts))},
                      {latitude},
                      {longitude},
                      {odometer},
                      {1 if self.vehicle_client.vehicle.ev_battery_is_charging else 0},
                      {1 if self.vehicle_client.vehicle.engine_is_running else 0},
                      {self.vehicle_client.charging_power_in_kilowatts},
                      {self.vehicle_client.vehicle.ev_charge_limits_ac or 100},
                      {self.vehicle_client.vehicle.ev_charge_limits_dc or 100},
                      {self.vehicle_client.vehicle.air_temperature},
                      "{self.vehicle_client.vehicle.data}"
                  ) '''
        print(sql)
        cur.execute(sql)
        conn.commit()

    def save_daily_stats(self):
        conn = self.create_connection()
        cur = conn.cursor()

        # for each day, check if day already saved in database to prevent duplicates
        sql = 'SELECT date FROM stats_per_day;'
        cur.execute(sql)
        rows = cur.fetchall()

        for day in self.vehicle_client.vehicle.daily_stats:

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

    def log_error(self, exception: Exception):
        conn = self.create_connection()
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
