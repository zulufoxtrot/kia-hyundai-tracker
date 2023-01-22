BEGIN TRANSACTION;
CREATE TABLE IF NOT EXISTS "stats_per_day" (
	"date"	TEXT,
	"unix_timestamp"	INTEGER,
	"total_consumed_kwh"	REAL,
	"engine_consumption_kwh"	REAL,
	"climate_consumption_kwh"	REAL,
	"onboard_electronics_consumption_kwh"	REAL,
	"battery_care_consumption_kwh"	REAL,
	"regenerated_energy_kwh"	REAL,
	"distance"	INTEGER,
	"average_consumption_kwh"	REAL,
	"average_consumption_regen_deducted_kwh"	REAL
);
CREATE TABLE IF NOT EXISTS "log" (
	"battery_percentage"	INTEGER,
	"accessory_battery_percentage"	INTEGER,
	"estimated_range_km"	INTEGER,
	"timestamp"	TEXT,
	"unix_timestamp"	INTEGER,
	"last_vehicule_update_timestamp"	TEXT,
	"unix_last_vehicle_update_timestamp"	INTEGER,
	"latitude"	TEXT,
	"longitude"	TEXT,
	"odometer"	INTEGER,
	"charging"	INTEGER,
	"engine_is_running"	INTEGER,
	"rough_charging_power_estimate_kw"	REAL,
	"returned_api_status"	TEXT,
	"ac_charge_limit_percent"	INTEGER,
	"dc_charge_limit_percent"	INTEGER,
	"target_climate_temperature"	INTEGER,
	"raw_api_data"	TEXT
);
CREATE TABLE IF NOT EXISTS "errors" (
	"timestamp"	TEXT,
	"unix_timestamp"	INTEGER,
	"exc_type"	TEXT,
	"exc_args"	TEXT
);
CREATE TABLE IF NOT EXISTS "trips" (
	"unix_timestamp"	INTEGER,
	"date"	TEXT,
	"driving_time_minutes"	INTEGER,
	"idle_time_minutes"	INTEGER,
	"distance_km"	INTEGER,
	"avg_speed_kmh"	INTEGER,
	"max_speed_kmh"	INTEGER
);
COMMIT;
