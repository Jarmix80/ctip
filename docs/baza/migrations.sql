-- Migration 2025-10-10: rozszerzenie modelu SMS oraz dodanie kartoteki kontaktów
SET search_path TO ctip;

ALTER TABLE sms_out
  ADD COLUMN created_by integer,
  ADD COLUMN template_id bigint,
  ADD COLUMN origin varchar(32) DEFAULT 'ui',
  ADD COLUMN provider_msg_id varchar(64),
  ADD COLUMN provider_status varchar(32),
  ADD COLUMN provider_error_code varchar(16),
  ADD COLUMN provider_error_desc text;

CREATE TABLE contact (
  id bigserial PRIMARY KEY,
  number text NOT NULL,
  ext text,
  first_name text,
  last_name text,
  company text,
  nip varchar(20),
  email text,
  notes text,
  source varchar(32) DEFAULT 'manual',
  created_at timestamptz DEFAULT now() NOT NULL,
  updated_at timestamptz DEFAULT now() NOT NULL
);

CREATE TABLE contact_device (
  id bigserial PRIMARY KEY,
  contact_id bigint NOT NULL REFERENCES contact(id) ON DELETE CASCADE,
  device_name text,
  serial_number text,
  location text,
  notes text,
  created_at timestamptz DEFAULT now() NOT NULL
);

CREATE INDEX idx_contact_number ON contact(number);
CREATE INDEX idx_sms_out_dest_created ON sms_out(dest, created_at DESC);
CREATE INDEX idx_sms_out_created_by ON sms_out(created_by, created_at DESC);

-- Migration 2026-04-10: przygotowanie drugiego source_type dla workflow urządzeń
SET search_path TO ctip;

ALTER TABLE form_workflow_device
  DROP CONSTRAINT IF EXISTS form_workflow_device_source_type_check;

ALTER TABLE form_workflow_device
  ADD CONSTRAINT form_workflow_device_source_type_check
  CHECK (
    source_type = ANY (
      ARRAY['google_sheet'::text, 'firebird_magazyn_28'::text, 'firebird_serial'::text]
    )
  );

-- Migration 2026-04-20: lokalny cache statusów arkusza Google dla modalu FLOW
SET search_path TO ctip;

CREATE SEQUENCE IF NOT EXISTS workflow_sheet_status_cache_id_seq
  START WITH 1
  INCREMENT BY 1
  NO MINVALUE
  NO MAXVALUE
  CACHE 1;

CREATE TABLE IF NOT EXISTS workflow_sheet_status_cache (
  id integer NOT NULL DEFAULT nextval('workflow_sheet_status_cache_id_seq'::regclass),
  source_key text,
  source_type text DEFAULT 'firebird_magazyn_28'::text NOT NULL,
  source_row integer,
  device_index text,
  device_index_normalized text,
  sheet_row integer,
  sheet_status text,
  reservation_grenke text,
  form_ctip text,
  ctip_form_id integer,
  ctip_workflow_case_id integer,
  business_status_legacy text,
  synced_at timestamptz NOT NULL,
  CONSTRAINT workflow_sheet_status_cache_pkey PRIMARY KEY (id),
  CONSTRAINT uq_workflow_sheet_status_cache_source_key UNIQUE (source_key),
  CONSTRAINT workflow_sheet_status_cache_source_type_check
    CHECK (
      source_type = ANY (
        ARRAY['google_sheet'::text, 'firebird_magazyn_28'::text, 'firebird_serial'::text]
      )
    )
);

CREATE INDEX IF NOT EXISTS idx_workflow_sheet_status_cache_index_norm
  ON workflow_sheet_status_cache(device_index_normalized);
