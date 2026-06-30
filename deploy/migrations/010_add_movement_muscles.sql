ALTER TABLE movement ADD COLUMN primary_muscle VARCHAR(15);
ALTER TABLE movement ADD COLUMN secondary_muscles JSON;
