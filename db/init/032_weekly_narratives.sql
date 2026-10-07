-- "Ross explica tu semana" (app/services/weekly_narrative.py): one written
-- summary per store per day, so opening the dashboard doesn't call the
-- Claude API every time. period_end is the day the 7-day window ends (it
-- covers the 7 full days before it), so the row is reused until tomorrow.
CREATE TABLE weekly_narratives (
    store_id UUID NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    period_end DATE NOT NULL,
    text TEXT NOT NULL,
    -- Which mascot scene goes with it: 'festeja', 'preocupada' or 'neutral'.
    mood VARCHAR(20) NOT NULL,
    -- 'ia' when Claude wrote it, 'plantilla' when it came from the fixed template.
    source VARCHAR(20) NOT NULL,
    -- The figures the text is built from, as shown beside it.
    facts JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (store_id, period_end)
);
