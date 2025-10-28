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
