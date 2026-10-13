-- Which writer produced each weekly narrative (app/services/narrative_writers/): the fixed
-- template or a model, and which one. Lets a provider be compared with the template before it is
-- trusted. Rows written before this column existed came from the template, so that is the default.
ALTER TABLE weekly_narratives ADD COLUMN IF NOT EXISTS provider VARCHAR(40) NOT NULL DEFAULT 'template';
ALTER TABLE weekly_narratives ADD COLUMN IF NOT EXISTS model VARCHAR(100);

-- A development database that did use Claude: those rows were written by it.
UPDATE weekly_narratives SET provider = 'anthropic' WHERE source = 'ia' AND provider = 'template';
