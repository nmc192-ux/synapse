ALTER TABLE synapse_tasks
    ADD COLUMN IF NOT EXISTS project_id TEXT;

ALTER TABLE synapse_memory
    ADD COLUMN IF NOT EXISTS project_id TEXT;

CREATE INDEX IF NOT EXISTS idx_synapse_tasks_project_id
    ON synapse_tasks (project_id);

CREATE INDEX IF NOT EXISTS idx_synapse_memory_project_id
    ON synapse_memory (project_id);
