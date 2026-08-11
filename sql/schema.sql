-- vertu-data-hub schema. 幂等：全部用 IF NOT EXISTS，重复执行安全。

CREATE EXTENSION IF NOT EXISTS vector;

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
