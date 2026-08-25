-- vertu-data-hub schema. 幂等：全部用 IF NOT EXISTS，重复执行安全。

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ========== 经销商知识主表 ==========

CREATE TABLE IF NOT EXISTS dealer (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    official_name   VARCHAR(240) NOT NULL,
    normalized_name VARCHAR(240) NOT NULL,
    country_code    VARCHAR(2) NOT NULL,
    city            VARCHAR(120),
    language_codes  TEXT[] NOT NULL DEFAULT '{}',
    status          VARCHAR(20) NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','active','inactive','merged')),
    proposed_by     VARCHAR(160) NOT NULL,
    confirmed_by    VARCHAR(160),
    confirmed_at    TIMESTAMPTZ,
    merged_into_id  UUID REFERENCES dealer(id),
    version         INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (status <> 'merged' OR merged_into_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_dealer_normalized_name_trgm
    ON dealer USING gin (normalized_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_dealer_country_status
    ON dealer (country_code, status);

CREATE TABLE IF NOT EXISTS dealer_alias (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dealer_id        UUID NOT NULL REFERENCES dealer(id) ON DELETE CASCADE,
    alias             VARCHAR(240) NOT NULL,
    normalized_alias  VARCHAR(240) NOT NULL,
    source            VARCHAR(40) NOT NULL DEFAULT 'manual',
    active            BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (dealer_id, normalized_alias)
);
CREATE INDEX IF NOT EXISTS idx_dealer_alias_normalized_trgm
    ON dealer_alias USING gin (normalized_alias gin_trgm_ops);

CREATE TABLE IF NOT EXISTS dealer_store (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dealer_id   UUID NOT NULL REFERENCES dealer(id) ON DELETE CASCADE,
    external_id VARCHAR(120),
    name        VARCHAR(240) NOT NULL,
    city        VARCHAR(120),
    address     TEXT,
    status      VARCHAR(20) NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','inactive')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (dealer_id, external_id)
);

CREATE TABLE IF NOT EXISTS dealer_owner (
    dealer_id    UUID NOT NULL REFERENCES dealer(id) ON DELETE CASCADE,
    principal_id VARCHAR(160) NOT NULL,
    team_key      VARCHAR(160),
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    assigned_by   VARCHAR(160) NOT NULL,
    assigned_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (dealer_id, principal_id)
);
CREATE INDEX IF NOT EXISTS idx_dealer_owner_principal
    ON dealer_owner (principal_id) WHERE active;

-- ========== 不可变源对象、资产版本和权威任务状态 ==========

CREATE TABLE IF NOT EXISTS source_object (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dealer_id     UUID REFERENCES dealer(id),
    scope_type    VARCHAR(20) NOT NULL DEFAULT 'dealer',
    scope_key     VARCHAR(160),
    bucket        VARCHAR(120) NOT NULL,
    object_key    VARCHAR(900) NOT NULL,
    content_hash  VARCHAR(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    original_name VARCHAR(500) NOT NULL,
    content_type  VARCHAR(160) NOT NULL,
    byte_size     BIGINT NOT NULL CHECK (byte_size > 0),
    uploaded_by   VARCHAR(160) NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (bucket, object_key),
    UNIQUE (dealer_id, content_hash)
);

CREATE TABLE IF NOT EXISTS knowledge_asset (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dealer_id     UUID REFERENCES dealer(id),
    scope_type    VARCHAR(20) NOT NULL DEFAULT 'dealer',
    scope_key     VARCHAR(160),
    store_id      UUID REFERENCES dealer_store(id),
    logical_key   VARCHAR(300) NOT NULL,
    title         VARCHAR(500) NOT NULL,
    category      VARCHAR(40) NOT NULL,
    sensitivity   VARCHAR(20) NOT NULL DEFAULT 'internal'
                  CHECK (sensitivity IN ('internal','confidential','restricted')),
    status        VARCHAR(30) NOT NULL DEFAULT 'received'
                  CHECK (status IN ('received','identifying','awaiting_review','processing','searchable','failed','deleted')),
    created_by    VARCHAR(160) NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (dealer_id, logical_key)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_asset_filter
    ON knowledge_asset (dealer_id, status, category, updated_at DESC);

CREATE TABLE IF NOT EXISTS asset_version (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id          UUID NOT NULL REFERENCES knowledge_asset(id),
    source_object_id  UUID NOT NULL REFERENCES source_object(id),
    previous_version_id UUID REFERENCES asset_version(id),
    version_number    INTEGER NOT NULL CHECK (version_number > 0),
    is_current        BOOLEAN NOT NULL DEFAULT TRUE,
    language_code     VARCHAR(16),
    pipeline_version  VARCHAR(80) NOT NULL DEFAULT 'pending',
    created_by        VARCHAR(160) NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (asset_id, version_number)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_current_version
    ON asset_version (asset_id) WHERE is_current;

CREATE TABLE IF NOT EXISTS processing_job (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dealer_id         UUID REFERENCES dealer(id),
    asset_version_id  UUID NOT NULL REFERENCES asset_version(id),
    job_type          VARCHAR(40) NOT NULL DEFAULT 'ingestion',
    queue_name        VARCHAR(40) NOT NULL,
    status            VARCHAR(20) NOT NULL DEFAULT 'queued'
                      CHECK (status IN ('queued','running','succeeded','failed')),
    progress          SMALLINT NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    idempotency_key   VARCHAR(200) NOT NULL UNIQUE,
    dispatch_status   VARCHAR(20) NOT NULL DEFAULT 'pending'
                      CHECK (dispatch_status IN ('pending','sent','failed')),
    dispatch_error    TEXT,
    dispatched_at     TIMESTAMPTZ,
    attempt_count     INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts      INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    error_code        VARCHAR(80),
    error_message     TEXT,
    input_data        JSONB NOT NULL DEFAULT '{}',
    output_data       JSONB NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_processing_job_queue
    ON processing_job (queue_name, status, created_at);
ALTER TABLE processing_job
    ADD COLUMN IF NOT EXISTS dispatch_status VARCHAR(20) NOT NULL DEFAULT 'pending';
ALTER TABLE processing_job
    ADD COLUMN IF NOT EXISTS dispatch_error TEXT;
ALTER TABLE processing_job
    ADD COLUMN IF NOT EXISTS dispatched_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_processing_job_dispatch
    ON processing_job (dispatch_status, created_at) WHERE status = 'queued';

CREATE TABLE IF NOT EXISTS derived_artifact (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dealer_id        UUID REFERENCES dealer(id),
    asset_version_id UUID NOT NULL REFERENCES asset_version(id),
    artifact_type    VARCHAR(40) NOT NULL,
    bucket           VARCHAR(120) NOT NULL,
    object_key       VARCHAR(900) NOT NULL,
    content_hash     VARCHAR(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    content_type     VARCHAR(160) NOT NULL,
    byte_size        BIGINT NOT NULL CHECK (byte_size > 0),
    pipeline_version VARCHAR(80) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (asset_version_id, artifact_type, pipeline_version),
    UNIQUE (bucket, object_key)
);

CREATE TABLE IF NOT EXISTS content_chunk (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dealer_id           UUID REFERENCES dealer(id),
    asset_version_id    UUID NOT NULL REFERENCES asset_version(id),
    chunk_index         INTEGER NOT NULL CHECK (chunk_index >= 0),
    text                TEXT NOT NULL,
    section             VARCHAR(300),
    page_start          INTEGER CHECK (page_start IS NULL OR page_start > 0),
    page_end            INTEGER CHECK (page_end IS NULL OR page_end >= page_start),
    language_code       VARCHAR(16),
    citation            JSONB NOT NULL DEFAULT '{}',
    embedding           vector(1024) NOT NULL,
    embedding_provider  VARCHAR(40) NOT NULL,
    embedding_model     VARCHAR(120) NOT NULL,
    embedding_dimension INTEGER NOT NULL CHECK (embedding_dimension > 0),
    pipeline_version    VARCHAR(80) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (asset_version_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_content_chunk_dealer
    ON content_chunk (dealer_id, asset_version_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_content_chunk_embedding
    ON content_chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_content_chunk_text
    ON content_chunk USING gin (to_tsvector('simple', text));

CREATE TABLE IF NOT EXISTS image_embedding (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dealer_id           UUID REFERENCES dealer(id),
    asset_version_id    UUID NOT NULL REFERENCES asset_version(id),
    embedding           vector(1024) NOT NULL,
    embedding_provider  VARCHAR(40) NOT NULL,
    embedding_model     VARCHAR(120) NOT NULL,
    embedding_dimension INTEGER NOT NULL CHECK (embedding_dimension > 0),
    width               INTEGER NOT NULL CHECK (width > 0),
    height              INTEGER NOT NULL CHECK (height > 0),
    image_format        VARCHAR(20) NOT NULL,
    ocr_language        VARCHAR(20) NOT NULL,
    ocr_line_count      INTEGER NOT NULL DEFAULT 0 CHECK (ocr_line_count >= 0),
    ocr_mean_confidence REAL CHECK (ocr_mean_confidence BETWEEN 0 AND 1),
    pipeline_version    VARCHAR(80) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (asset_version_id, pipeline_version)
);
CREATE INDEX IF NOT EXISTS idx_image_embedding_dealer
    ON image_embedding (dealer_id, asset_version_id);
CREATE INDEX IF NOT EXISTS idx_image_embedding_vector
    ON image_embedding USING hnsw (embedding vector_cosine_ops);

-- Legacy local semantic fields remain for backward-compatible migrations.
ALTER TABLE image_embedding ADD COLUMN IF NOT EXISTS semantic_embedding vector(512);
ALTER TABLE image_embedding ADD COLUMN IF NOT EXISTS semantic_provider VARCHAR(40);
ALTER TABLE image_embedding ADD COLUMN IF NOT EXISTS semantic_model VARCHAR(160);
ALTER TABLE image_embedding ADD COLUMN IF NOT EXISTS quality_score REAL
    CHECK (quality_score BETWEEN 0 AND 1);
ALTER TABLE image_embedding ADD COLUMN IF NOT EXISTS semantic_labels JSONB NOT NULL DEFAULT '[]';
ALTER TABLE image_embedding ADD COLUMN IF NOT EXISTS semantic_indexed_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_image_embedding_semantic_vector
    ON image_embedding USING hnsw (semantic_embedding vector_cosine_ops);

-- Existing dealer-only installations are upgraded in place. Shared child rows keep
-- dealer_id NULL; authorization always joins through knowledge_asset.
ALTER TABLE source_object ADD COLUMN IF NOT EXISTS scope_type VARCHAR(20) NOT NULL DEFAULT 'dealer';
ALTER TABLE source_object ADD COLUMN IF NOT EXISTS scope_key VARCHAR(160);
ALTER TABLE knowledge_asset ADD COLUMN IF NOT EXISTS scope_type VARCHAR(20) NOT NULL DEFAULT 'dealer';
ALTER TABLE knowledge_asset ADD COLUMN IF NOT EXISTS scope_key VARCHAR(160);
UPDATE source_object SET scope_key = dealer_id::text WHERE scope_key IS NULL;
UPDATE knowledge_asset SET scope_key = dealer_id::text WHERE scope_key IS NULL;
ALTER TABLE source_object ALTER COLUMN scope_key SET NOT NULL;
ALTER TABLE knowledge_asset ALTER COLUMN scope_key SET NOT NULL;
ALTER TABLE source_object ALTER COLUMN dealer_id DROP NOT NULL;
ALTER TABLE knowledge_asset ALTER COLUMN dealer_id DROP NOT NULL;
ALTER TABLE processing_job ALTER COLUMN dealer_id DROP NOT NULL;
ALTER TABLE derived_artifact ALTER COLUMN dealer_id DROP NOT NULL;
ALTER TABLE content_chunk ALTER COLUMN dealer_id DROP NOT NULL;
ALTER TABLE image_embedding ALTER COLUMN dealer_id DROP NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_source_object_scope_hash
    ON source_object (scope_type, scope_key, content_hash);
CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_asset_scope_key
    ON knowledge_asset (scope_type, scope_key, logical_key);
CREATE INDEX IF NOT EXISTS idx_knowledge_asset_scope_filter
    ON knowledge_asset (scope_type, scope_key, status, category, updated_at DESC);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_source_object_scope'
    ) THEN
        ALTER TABLE source_object ADD CONSTRAINT ck_source_object_scope CHECK (
            (scope_type = 'dealer' AND dealer_id IS NOT NULL AND scope_key = dealer_id::text)
            OR (scope_type IN ('department','company') AND dealer_id IS NULL)
        );
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_knowledge_asset_scope'
    ) THEN
        ALTER TABLE knowledge_asset ADD CONSTRAINT ck_knowledge_asset_scope CHECK (
            (scope_type = 'dealer' AND dealer_id IS NOT NULL AND scope_key = dealer_id::text)
            OR (
                scope_type IN ('department','company')
                AND dealer_id IS NULL
                AND store_id IS NULL
            )
        );
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS audit_event (
    id          BIGSERIAL PRIMARY KEY,
    actor_id    VARCHAR(160) NOT NULL,
    action      VARCHAR(100) NOT NULL,
    object_type VARCHAR(80) NOT NULL,
    object_id   UUID,
    request_id  VARCHAR(200),
    payload     JSONB NOT NULL DEFAULT '{}',
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_event_object
    ON audit_event (object_type, object_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS original_export_grant (
    id                 UUID PRIMARY KEY,
    asset_id           UUID NOT NULL REFERENCES knowledge_asset(id),
    asset_version_id   UUID NOT NULL REFERENCES asset_version(id),
    initiated_by       VARCHAR(160) NOT NULL,
    idempotency_key    VARCHAR(200) NOT NULL,
    reason             VARCHAR(500) NOT NULL,
    reason_sha256      VARCHAR(64) NOT NULL CHECK (reason_sha256 ~ '^[0-9a-f]{64}$'),
    request_id         VARCHAR(200),
    reauthenticated_at TIMESTAMPTZ NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL,
    expires_at         TIMESTAMPTZ NOT NULL,
    consumed_at        TIMESTAMPTZ,
    UNIQUE (initiated_by, idempotency_key),
    CHECK (expires_at > created_at)
);
CREATE INDEX IF NOT EXISTS idx_original_export_grant_expiry
    ON original_export_grant (expires_at) WHERE consumed_at IS NULL;

CREATE OR REPLACE FUNCTION prevent_audit_event_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_event is append-only';
END;
$$;
DROP TRIGGER IF EXISTS trg_audit_event_append_only ON audit_event;
CREATE TRIGGER trg_audit_event_append_only
BEFORE UPDATE OR DELETE ON audit_event
FOR EACH ROW EXECUTE FUNCTION prevent_audit_event_mutation();

-- ========== 数据源目录：新增来源只 INSERT，不改表结构 ==========

CREATE TABLE IF NOT EXISTS data_source (
    id             BIGSERIAL PRIMARY KEY,
    code           VARCHAR(60) UNIQUE NOT NULL,        -- 'policy_product_docs' / 'vps_daily_sales' / 'odoo_sale_view'
    source_type    VARCHAR(20) NOT NULL CHECK (source_type IN ('file','skill','db','mcp')),
    display_name   VARCHAR(200) NOT NULL,
    description    TEXT,
    config         JSONB NOT NULL DEFAULT '{}',        -- 各 connector 自定义配置（密钥仍走 .env，这里只存引用）
    enabled        BOOLEAN NOT NULL DEFAULT TRUE,
    last_synced_at TIMESTAMP,
    created_at     TIMESTAMP DEFAULT now(),
    updated_at     TIMESTAMP DEFAULT now()
);

-- ========== 同步幂等台账 ==========
-- 文件哈希 / skill 拉取参数 / db 表名 统一记录，替代每种来源各写一套去重逻辑。

CREATE TABLE IF NOT EXISTS source_item (
    id               BIGSERIAL PRIMARY KEY,
    data_source_id   BIGINT NOT NULL REFERENCES data_source(id),
    external_key     VARCHAR(500) NOT NULL,            -- 文件相对路径 / script_key+参数哈希 / schema.table
    content_hash     VARCHAR(64),                       -- sha256，未变化则跳过重新入库
    status           VARCHAR(20) NOT NULL DEFAULT 'ingested',  -- ingested / failed / skipped
    last_ingested_at TIMESTAMP DEFAULT now(),
    error            TEXT,
    UNIQUE (data_source_id, external_key)
);

-- ========== 文本 RAG 分片 ==========

CREATE TABLE IF NOT EXISTS doc_chunk (
    id             BIGSERIAL PRIMARY KEY,
    data_source_id BIGINT NOT NULL REFERENCES data_source(id),
    source_item_id BIGINT REFERENCES source_item(id),
    text           TEXT NOT NULL,
    embedding      vector(1024),
    source_file    VARCHAR(500) NOT NULL,               -- 幂等键：同一 source_file 先删后插
    section        VARCHAR(200),
    tags           JSONB NOT NULL DEFAULT '{}',          -- {"doc_type":"policy","country":"Russia",...}
    effective_date DATE,
    expiry_date    DATE,
    created_at     TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_doc_chunk_embedding ON doc_chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_doc_chunk_tags      ON doc_chunk USING gin (tags);
CREATE INDEX IF NOT EXISTS idx_doc_chunk_source    ON doc_chunk (data_source_id, source_file);

-- ========== 图片/多模态资产 ==========

CREATE TABLE IF NOT EXISTS media_asset (
    id             BIGSERIAL PRIMARY KEY,
    data_source_id BIGINT NOT NULL REFERENCES data_source(id),
    source_item_id BIGINT REFERENCES source_item(id),
    url            VARCHAR(500) NOT NULL,
    media_type     VARCHAR(20) NOT NULL DEFAULT 'image',
    tags           JSONB NOT NULL DEFAULT '{}',          -- {"image_type":"display","store_id":"RU-MOW-01",...}
    shot_date      DATE,
    description    TEXT,
    embedding      vector(1024),
    created_at     TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_media_asset_embedding ON media_asset USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_media_asset_tags      ON media_asset USING gin (tags);

-- ========== 结构化数据：精确取数，不走向量相似 ==========
-- record_kind='row'      : 文件/未来批量导入的原始行（如历史销售 Excel）
-- record_kind='snapshot' : skill 拉取（vertu-cli ...）某时间窗口的聚合结果快照

CREATE TABLE IF NOT EXISTS structured_record (
    id             BIGSERIAL PRIMARY KEY,
    data_source_id BIGINT NOT NULL REFERENCES data_source(id),
    source_item_id BIGINT REFERENCES source_item(id),
    dataset_code   VARCHAR(100) NOT NULL,                -- 'historical_sales_2023' / 'headline_kpi'
    record_kind    VARCHAR(20) NOT NULL DEFAULT 'row' CHECK (record_kind IN ('row','snapshot')),
    natural_key    VARCHAR(300) NOT NULL,                 -- 原始行主键 / script_key+参数哈希+周期
    period_start   DATE,
    period_end     DATE,
    row_date       DATE,
    data           JSONB NOT NULL,                        -- 原始字段（含中文列名）或 vertu-cli 的 JSON 结果
    created_at     TIMESTAMP DEFAULT now(),
    UNIQUE (data_source_id, dataset_code, natural_key)
);
CREATE INDEX IF NOT EXISTS idx_structured_record_date   ON structured_record (data_source_id, dataset_code, row_date);
CREATE INDEX IF NOT EXISTS idx_structured_record_period ON structured_record (data_source_id, dataset_code, period_start, period_end);
CREATE INDEX IF NOT EXISTS idx_structured_record_data   ON structured_record USING gin (data);

-- ========== 结构化数据字典（给未来 agent 做取数定位/口径说明） ==========

CREATE TABLE IF NOT EXISTS structured_dataset (
    id             BIGSERIAL PRIMARY KEY,
    data_source_id BIGINT NOT NULL REFERENCES data_source(id),
    dataset_code   VARCHAR(100) NOT NULL,
    display_name   VARCHAR(200),
    description    TEXT,
    columns_doc    JSONB,                                 -- [{"name":"销售日期","type":"date","desc":"..."}]
    query_hint     TEXT,
    refresh_mode   VARCHAR(20) NOT NULL DEFAULT 'snapshot' CHECK (refresh_mode IN ('snapshot','live')),
    created_at     TIMESTAMP DEFAULT now(),
    UNIQUE (data_source_id, dataset_code)
);

-- ========== 同步运行日志 ==========

CREATE TABLE IF NOT EXISTS ingestion_run (
    id              BIGSERIAL PRIMARY KEY,
    data_source_id  BIGINT NOT NULL REFERENCES data_source(id),
    started_at      TIMESTAMP DEFAULT now(),
    finished_at     TIMESTAMP,
    status          VARCHAR(20) NOT NULL DEFAULT 'running',   -- running/success/failed
    items_processed INT DEFAULT 0,
    error           TEXT
);
