-- 000_baseline.sql — schema-only baseline from v0.2.0 tag
-- Generated via: git checkout v0.2.0 && create_all (no seed) && sqlite3 .schema
-- The gap to current models is exactly the two columns added in 001 and 002.
-- This file must contain zero DDL for either of those columns (verified by grep).

CREATE TABLE IF NOT EXISTS equipment (
	id INTEGER NOT NULL,
	name VARCHAR NOT NULL,
	load_floor FLOAT,
	min_step FLOAT,
	load_unit VARCHAR(11) NOT NULL,
	available_phase VARCHAR(2) NOT NULL,
	notes VARCHAR,
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_equipment_name ON equipment (name);
CREATE TABLE IF NOT EXISTS bandpair (
	id INTEGER NOT NULL,
	label VARCHAR NOT NULL,
	bottom_lb FLOAT NOT NULL,
	peak_lb FLOAT NOT NULL,
	calibration_status VARCHAR(8) NOT NULL,
	inspection_date DATE,
	usable BOOLEAN NOT NULL,
	PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS phasepolicy (
	id INTEGER NOT NULL,
	phase VARCHAR(11) NOT NULL,
	default_objective VARCHAR(8) NOT NULL,
	rpe_band_low FLOAT NOT NULL,
	rpe_band_high FLOAT NOT NULL,
	hard_cap FLOAT NOT NULL,
	top_set_rpe FLOAT NOT NULL,
	progression_attempted BOOLEAN NOT NULL,
	volume_posture VARCHAR NOT NULL,
	meaningful_drop_pct FLOAT,
	meaningful_drop_sessions INTEGER,
	PRIMARY KEY (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_phasepolicy_phase ON phasepolicy (phase);
CREATE TABLE IF NOT EXISTS enginestate (
	id INTEGER NOT NULL,
	current_phase VARCHAR(11) NOT NULL,
	bodyweight FLOAT,
	rhr_down BOOLEAN NOT NULL,
	sleep_ok BOOLEAN NOT NULL,
	no_rpe_creep BOOLEAN NOT NULL,
	bw_stable_2wk BOOLEAN NOT NULL,
	strength_bounce BOOLEAN NOT NULL,
	subjective_ok BOOLEAN NOT NULL,
	PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS session (
	id INTEGER NOT NULL,
	date DATE NOT NULL,
	day_role VARCHAR NOT NULL,
	phase VARCHAR NOT NULL,
	status VARCHAR(11) NOT NULL,
	generated_at DATETIME NOT NULL,
	approved_at DATETIME,
	signature JSON,
	rationale VARCHAR,
	notes VARCHAR,
	PRIMARY KEY (id)
);
CREATE TABLE IF NOT EXISTS stickingpointtaxonomy (
	id INTEGER NOT NULL,
	lift_category VARCHAR NOT NULL,
	option_code VARCHAR NOT NULL,
	order_index INTEGER NOT NULL,
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_stickingpointtaxonomy_lift_category ON stickingpointtaxonomy (lift_category);
CREATE TABLE IF NOT EXISTS movement (
	id INTEGER NOT NULL,
	name VARCHAR NOT NULL,
	base_name VARCHAR NOT NULL,
	region VARCHAR(5) NOT NULL,
	lift_category VARCHAR(11) NOT NULL,
	is_primary BOOLEAN NOT NULL,
	is_tracked BOOLEAN NOT NULL,
	status VARCHAR(8) NOT NULL,
	load_equipment_id INTEGER,
	equipment_tags JSON,
	progression_mode VARCHAR(12) NOT NULL,
	assist_subtype VARCHAR(10),
	assist_unit VARCHAR(10),
	scheme VARCHAR(18) NOT NULL,
	objective_override VARCHAR(8),
	increment_ladder JSON,
	min_step FLOAT,
	load_floor FLOAT,
	cap FLOAT,
	rpe_capped BOOLEAN NOT NULL,
	rpe_cap_exempt BOOLEAN NOT NULL,
	family VARCHAR,
	is_family_anchor BOOLEAN NOT NULL,
	derived_from_id INTEGER,
	start_ratio FLOAT,
	band_eligible BOOLEAN NOT NULL,
	notes VARCHAR,
	PRIMARY KEY (id),
	FOREIGN KEY(load_equipment_id) REFERENCES equipment (id),
	FOREIGN KEY(derived_from_id) REFERENCES movement (id)
);
CREATE INDEX IF NOT EXISTS ix_movement_family ON movement (family);
CREATE UNIQUE INDEX IF NOT EXISTS ix_movement_name ON movement (name);
CREATE TABLE IF NOT EXISTS exercisegroup (
	id INTEGER NOT NULL,
	session_id INTEGER NOT NULL,
	order_index INTEGER NOT NULL,
	group_type VARCHAR(9) NOT NULL,
	rounds INTEGER NOT NULL,
	rest_seconds INTEGER,
	label VARCHAR,
	PRIMARY KEY (id),
	FOREIGN KEY(session_id) REFERENCES session (id)
);
CREATE INDEX IF NOT EXISTS ix_exercisegroup_session_id ON exercisegroup (session_id);
CREATE TABLE IF NOT EXISTS movementstate (
	id INTEGER NOT NULL,
	movement_id INTEGER NOT NULL,
	calibration_status VARCHAR(11) NOT NULL,
	e1rm FLOAT,
	e1rm_updated_at DATETIME,
	current_load FLOAT,
	current_increment_tier INTEGER NOT NULL,
	current_rep_scheme VARCHAR,
	rep_scheme_locked_until DATE,
	consecutive_ceiling_sessions INTEGER NOT NULL,
	assist_level FLOAT,
	ht_plates FLOAT,
	ht_band_pair_id INTEGER,
	ht_felt_peak FLOAT,
	PRIMARY KEY (id),
	FOREIGN KEY(movement_id) REFERENCES movement (id),
	FOREIGN KEY(ht_band_pair_id) REFERENCES bandpair (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_movementstate_movement_id ON movementstate (movement_id);
CREATE TABLE IF NOT EXISTS plannedexercise (
	id INTEGER NOT NULL,
	group_id INTEGER NOT NULL,
	movement_id INTEGER NOT NULL,
	order_index INTEGER NOT NULL,
	scheme VARCHAR(18) NOT NULL,
	objective VARCHAR(8) NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(group_id) REFERENCES exercisegroup (id),
	FOREIGN KEY(movement_id) REFERENCES movement (id)
);
CREATE INDEX IF NOT EXISTS ix_plannedexercise_group_id ON plannedexercise (group_id);
CREATE INDEX IF NOT EXISTS ix_plannedexercise_movement_id ON plannedexercise (movement_id);
CREATE TABLE IF NOT EXISTS exercisesurvey (
	id INTEGER NOT NULL,
	session_id INTEGER NOT NULL,
	movement_id INTEGER NOT NULL,
	performed_at DATETIME NOT NULL,
	sticking_point VARCHAR,
	asymmetry_flag BOOLEAN,
	technique_flag BOOLEAN,
	PRIMARY KEY (id),
	FOREIGN KEY(session_id) REFERENCES session (id),
	FOREIGN KEY(movement_id) REFERENCES movement (id)
);
CREATE INDEX IF NOT EXISTS ix_exercisesurvey_session_id ON exercisesurvey (session_id);
CREATE INDEX IF NOT EXISTS ix_exercisesurvey_movement_id ON exercisesurvey (movement_id);
CREATE TABLE IF NOT EXISTS note (
	id INTEGER NOT NULL,
	session_id INTEGER,
	movement_id INTEGER,
	created_at DATETIME NOT NULL,
	text VARCHAR NOT NULL,
	classification VARCHAR(19),
	confirmed BOOLEAN NOT NULL,
	applied BOOLEAN NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(session_id) REFERENCES session (id),
	FOREIGN KEY(movement_id) REFERENCES movement (id)
);
CREATE TABLE IF NOT EXISTS plannedset (
	id INTEGER NOT NULL,
	planned_exercise_id INTEGER NOT NULL,
	set_index INTEGER NOT NULL,
	set_role VARCHAR(7) NOT NULL,
	is_warmup BOOLEAN NOT NULL,
	target_load FLOAT,
	target_reps_low INTEGER,
	target_reps_high INTEGER,
	target_rpe FLOAT,
	target_unassisted_reps INTEGER,
	target_assisted_reps INTEGER,
	target_plates FLOAT,
	band_pair_id INTEGER,
	target_felt_peak FLOAT,
	PRIMARY KEY (id),
	FOREIGN KEY(planned_exercise_id) REFERENCES plannedexercise (id),
	FOREIGN KEY(band_pair_id) REFERENCES bandpair (id)
);
CREATE INDEX IF NOT EXISTS ix_plannedset_planned_exercise_id ON plannedset (planned_exercise_id);
CREATE TABLE IF NOT EXISTS setlog (
	id INTEGER NOT NULL,
	planned_set_id INTEGER,
	session_id INTEGER NOT NULL,
	movement_id INTEGER NOT NULL,
	set_index INTEGER NOT NULL,
	performed_at DATETIME NOT NULL,
	actual_load FLOAT,
	actual_reps INTEGER,
	feedback_tap VARCHAR(9),
	rpe_numeric FLOAT,
	is_warmup BOOLEAN NOT NULL,
	actual_unassisted_reps INTEGER,
	actual_assisted_reps INTEGER,
	actual_plates FLOAT,
	band_pair_id INTEGER,
	felt_peak FLOAT,
	PRIMARY KEY (id),
	FOREIGN KEY(planned_set_id) REFERENCES plannedset (id),
	FOREIGN KEY(session_id) REFERENCES session (id),
	FOREIGN KEY(movement_id) REFERENCES movement (id),
	FOREIGN KEY(band_pair_id) REFERENCES bandpair (id)
);
CREATE INDEX IF NOT EXISTS ix_setlog_movement_id ON setlog (movement_id);
CREATE INDEX IF NOT EXISTS ix_setlog_session_id ON setlog (session_id);
