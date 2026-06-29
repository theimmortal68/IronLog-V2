ALTER TABLE enginestate ADD COLUMN active_program_id INTEGER REFERENCES program(id);
