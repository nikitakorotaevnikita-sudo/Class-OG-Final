-- Каноническая схема БД классификатора (PostgreSQL 16 + pgvector).
-- Идемпотентна: можно применять повторно. Копия лежит в deploy/db/init/002_schema.sql
-- (побайтно совпадает, проверяется тестом).

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Классификатор. Корни уровня 1 (parent '0000.0000.0000.0000') хранятся с parent_code = NULL.
CREATE TABLE IF NOT EXISTS classifier_entries (
  code TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  level SMALLINT NOT NULL CHECK (level BETWEEN 1 AND 5),
  parent_code TEXT REFERENCES classifier_entries(code) DEFERRABLE INITIALLY DEFERRED,
  full_path TEXT NOT NULL,
  children_count INT NOT NULL DEFAULT 0,
  sort_order INT,                 -- порядок записей как в classifier_flat.json
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE classifier_entries ADD COLUMN IF NOT EXISTS sort_order INT;

-- Реестр версий эмбеддинг-модели. adapter: параметры адаптера (например, v3_blend).
CREATE TABLE IF NOT EXISTS ml_model_registry (
  version INT PRIMARY KEY,
  base_model_name TEXT NOT NULL,
  model_path TEXT NOT NULL,
  recall5_before REAL,
  recall5_after REAL,
  is_active BOOLEAN NOT NULL DEFAULT false,
  training_samples_count INT,
  trained_at TIMESTAMPTZ,
  report JSONB,
  adapter JSONB
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_model ON ml_model_registry(is_active) WHERE is_active;

CREATE TABLE IF NOT EXISTS classifier_vectors (
  id BIGSERIAL PRIMARY KEY,
  code TEXT NOT NULL REFERENCES classifier_entries(code),
  source_type TEXT NOT NULL CHECK (source_type IN ('base','annotation')),
  search_text TEXT NOT NULL,
  embedding vector(768),          -- NULL, пока не посчитан
  model_version INT NOT NULL REFERENCES ml_model_registry(version),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS cv_hnsw ON classifier_vectors USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS cv_code ON classifier_vectors(code, model_version);

CREATE TABLE IF NOT EXISTS appeal_classifications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id BIGINT,
  appeal_text TEXT NOT NULL,
  summary TEXT,
  applicant_fio TEXT,
  applicant_email TEXT,
  vid_obrascheniya TEXT,
  tip_obrascheniya TEXT,
  is_ustnoe BOOLEAN,
  overall_confidence REAL,
  needs_verification BOOLEAN,
  full_fallback_used BOOLEAN,
  candidates JSONB,
  llm_provider TEXT,
  llm_model TEXT,
  ip_address INET,
  elapsed_seconds REAL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS appeal_questions (
  id BIGSERIAL PRIMARY KEY,
  appeal_id UUID NOT NULL REFERENCES appeal_classifications(id) ON DELETE CASCADE,
  ordinal SMALLINT NOT NULL,
  question_text TEXT,
  selected_code TEXT,
  predmet_vedeniya TEXT,
  confidence REAL,
  reasoning TEXT,
  alternative_codes JSONB,
  verification_reasons JSONB,
  UNIQUE (appeal_id, ordinal)
);

CREATE TABLE IF NOT EXISTS appeal_verifications (
  appeal_id UUID PRIMARY KEY REFERENCES appeal_classifications(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','confirmed','corrected','rejected')),
  operator_codes JSONB,
  comment TEXT,
  verified_at TIMESTAMPTZ,
  verified_by TEXT
);

CREATE TABLE IF NOT EXISTS historical_appeals (
  id BIGSERIAL PRIMARY KEY,
  source_file TEXT,
  appeal_text TEXT NOT NULL,
  assigned_code TEXT NOT NULL,
  specialist TEXT,
  registration_date TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS hist_dedup ON historical_appeals
  (coalesce(source_file,''), md5(appeal_text), assigned_code, coalesce(registration_date,''));

CREATE TABLE IF NOT EXISTS jobs (
  id CHAR(32) PRIMARY KEY,                       -- uuid4().hex, контракт RX
  job_type TEXT NOT NULL CHECK (job_type IN ('classify','search','finetune','embed_annotation')),
  status TEXT NOT NULL CHECK (status IN ('queued','running','done','error')),
  appeal_id UUID REFERENCES appeal_classifications(id) ON DELETE SET NULL,
  document_id BIGINT,
  webhook_url TEXT,
  payload JSONB,
  result JSONB,
  error TEXT,
  attempts INT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(job_type, status, created_at);
-- Не больше одного активного дообучения: двойной запуск получает 409 (ActiveJobExists).
-- БД, созданная до индекса, могла накопить несколько активных дообучений: все, кроме
-- самого нового, закрываются ошибкой 'superseded', иначе индекс не создастся.
-- Идемпотентно: при одном активном дообучении (или ни одном) ничего не меняется.
UPDATE jobs SET status = 'error', error = 'superseded',
                finished_at = coalesce(finished_at, now())
  WHERE job_type = 'finetune' AND status IN ('queued','running')
    AND id <> (SELECT id FROM jobs
               WHERE job_type = 'finetune' AND status IN ('queued','running')
               ORDER BY created_at DESC, id DESC LIMIT 1);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_finetune ON jobs ((job_type))
  WHERE job_type = 'finetune' AND status IN ('queued','running');

CREATE TABLE IF NOT EXISTS llm_request_log (
  id BIGSERIAL PRIMARY KEY,
  appeal_id UUID,
  success BOOLEAN,
  attempt INT,
  elapsed_seconds REAL,
  confidence REAL,
  candidates_count INT,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Служебные отметки. files_migrated — время полного успешного переноса файлового
-- хранилища (scripts/db/migrate_from_files.py); повторный импорт только с --force.
CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
