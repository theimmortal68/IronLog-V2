-- 043_flat_2_5_increment_ladders.sql — collapse the tiered [5, 2.5] ladder to
-- a flat [2.5] for movements that should always advance in 2.5lb steps
-- (athlete feedback 2026-08-28: PureTorque Pro Rotation and Better Fly Rear
-- Delt Extension both jumped 5lb on a clean advance instead of 2.5).
UPDATE movement SET increment_ladder = '[2.5]' WHERE name IN ('PureTorque Pro Rotation', 'Better Fly Rear Delt Extension [FT]') AND increment_ladder = '[5, 2.5]';
