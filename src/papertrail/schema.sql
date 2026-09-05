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
