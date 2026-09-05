CREATE TABLE IF NOT EXISTS schema_version (
    version integer PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS papers (
    id uuid PRIMARY KEY,
    filename text NOT NULL,
    sha256 char(64) NOT NULL UNIQUE,
    size_bytes bigint NOT NULL CHECK (size_bytes > 0),
    page_count integer NOT NULL CHECK (page_count > 0),
    parser_version text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS pages (
    paper_id uuid NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    page_index integer NOT NULL CHECK (page_index >= 0),
    text text NOT NULL,
    PRIMARY KEY (paper_id, page_index)
);
INSERT INTO schema_version(version) VALUES (1) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS questions (
    id uuid PRIMARY KEY,
    paper_id uuid NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    request_id uuid NOT NULL UNIQUE,
    question text NOT NULL,
    status text NOT NULL CHECK (status IN
      ('pending', 'running', 'answered', 'insufficient_evidence', 'failed')),
    stage text NOT NULL DEFAULT 'pending',
    claims jsonb NOT NULL DEFAULT '[]',
    message text NOT NULL DEFAULT '',
    error_code text,
    support_status text,
    human_review jsonb,
    trace jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);
CREATE INDEX IF NOT EXISTS questions_paper_created ON questions(paper_id, created_at DESC);

CREATE TABLE IF NOT EXISTS model_calls (
    id uuid PRIMARY KEY,
    question_id uuid NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    budget_scope text NOT NULL,
    currency text NOT NULL,
    stage text NOT NULL,
    reserved_cost numeric NOT NULL CHECK (reserved_cost >= 0),
    actual_cost numeric CHECK (actual_cost >= 0),
    details jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);
INSERT INTO schema_version(version) VALUES (2) ON CONFLICT DO NOTHING;

ALTER TABLE questions ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'qa'
    CHECK (kind IN ('qa', 'introduction'));
ALTER TABLE questions ADD COLUMN IF NOT EXISTS introduction jsonb;
CREATE TABLE IF NOT EXISTS question_request_aliases (
    request_id uuid PRIMARY KEY,
    question_id uuid NOT NULL REFERENCES questions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS questions_introduction_paper_created
    ON questions(paper_id, created_at DESC, id DESC) WHERE kind = 'introduction';
INSERT INTO schema_version(version) VALUES (3) ON CONFLICT DO NOTHING;
