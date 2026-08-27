--
-- PostgreSQL database dump
--

\restrict ZhoCtUc30GzkxBj7Yh0bzFBbOdWa7nQwYCLFFyI6FrXZAs0p8C43i7qFe78uujG

-- Dumped from database version 17.6
-- Dumped by pg_dump version 17.6

-- Started on 2025-10-09 17:43:36

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- TOC entry 6 (class 2615 OID 16389)
-- Name: ctip; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA ctip;


ALTER SCHEMA ctip OWNER TO postgres;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- TOC entry 221 (class 1259 OID 16407)
-- Name: call_events; Type: TABLE; Schema: ctip; Owner: postgres
--

CREATE TABLE ctip.call_events (
    id bigint NOT NULL,
    call_id bigint,
    ts timestamp with time zone NOT NULL,
    typ text NOT NULL,
    ext text,
    number text,
    payload text
);


ALTER TABLE ctip.call_events OWNER TO postgres;

--
-- TOC entry 220 (class 1259 OID 16406)
-- Name: call_events_id_seq; Type: SEQUENCE; Schema: ctip; Owner: postgres
--

CREATE SEQUENCE ctip.call_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE ctip.call_events_id_seq OWNER TO postgres;

--
-- TOC entry 4839 (class 0 OID 0)
-- Dependencies: 220
-- Name: call_events_id_seq; Type: SEQUENCE OWNED BY; Schema: ctip; Owner: postgres
--

ALTER SEQUENCE ctip.call_events_id_seq OWNED BY ctip.call_events.id;


--
-- TOC entry 219 (class 1259 OID 16391)
-- Name: calls; Type: TABLE; Schema: ctip; Owner: postgres
--

CREATE TABLE ctip.calls (
    id bigint NOT NULL,
    ext text NOT NULL,
    number text,
    direction text NOT NULL,
    answered_by text,
    started_at timestamp with time zone NOT NULL,
    connected_at timestamp with time zone,
    ended_at timestamp with time zone,
    duration_s integer,
    disposition text DEFAULT 'UNKNOWN'::text NOT NULL,
    last_state text,
    notes text,
    CONSTRAINT calls_direction_check CHECK ((direction = ANY (ARRAY['OUT'::text, 'IN'::text]))),
    CONSTRAINT calls_disposition_check CHECK ((disposition = ANY (ARRAY['ANSWERED'::text, 'NO_ANSWER'::text, 'BUSY'::text, 'FAILED'::text, 'UNKNOWN'::text])))
);


ALTER TABLE ctip.calls OWNER TO postgres;

--
-- TOC entry 218 (class 1259 OID 16390)
-- Name: calls_id_seq; Type: SEQUENCE; Schema: ctip; Owner: postgres
--

CREATE SEQUENCE ctip.calls_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE ctip.calls_id_seq OWNER TO postgres;

--
-- TOC entry 4842 (class 0 OID 0)
-- Dependencies: 218
-- Name: calls_id_seq; Type: SEQUENCE OWNED BY; Schema: ctip; Owner: postgres
--

ALTER SEQUENCE ctip.calls_id_seq OWNED BY ctip.calls.id;


--
-- TOC entry 224 (class 1259 OID 16444)
-- Name: ivr_map; Type: TABLE; Schema: ctip; Owner: postgres
--

CREATE TABLE ctip.ivr_map (
    digit smallint NOT NULL,
    ext text NOT NULL,
    sms_text text NOT NULL,
    enabled boolean DEFAULT true NOT NULL
);


ALTER TABLE ctip.ivr_map OWNER TO postgres;

--
-- TOC entry XXX
-- Name: contact; Type: TABLE; Schema: ctip; Owner: postgres
--

CREATE TABLE ctip.contact (
    id bigint NOT NULL,
    number text NOT NULL,
    ext text,
    firebird_id text,
    first_name text,
    last_name text,
    company text,
    nip character varying(20),
    email text,
    notes text,
    source character varying(32) DEFAULT 'manual'::character varying,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE ctip.contact OWNER TO postgres;

--
-- TOC entry XXX
-- Name: contact_id_seq; Type: SEQUENCE; Schema: ctip; Owner: postgres
--

CREATE SEQUENCE ctip.contact_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE ctip.contact_id_seq OWNER TO postgres;

--
-- TOC entry XXX
-- Name: contact_device; Type: TABLE; Schema: ctip; Owner: postgres
--

CREATE TABLE ctip.contact_device (
    id bigint NOT NULL,
    contact_id bigint NOT NULL,
    device_name text,
    serial_number text,
    location text,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE ctip.contact_device OWNER TO postgres;

--
-- TOC entry XXX
-- Name: contact_device_id_seq; Type: SEQUENCE; Schema: ctip; Owner: postgres
--

CREATE SEQUENCE ctip.contact_device_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE ctip.contact_device_id_seq OWNER TO postgres;

--
-- TOC entry 223 (class 1259 OID 16426)
-- Name: sms_out; Type: TABLE; Schema: ctip; Owner: postgres
--

CREATE TABLE ctip.sms_out (
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    dest text NOT NULL,
    text text NOT NULL,
    source character varying(32) DEFAULT 'ivr'::character varying,
    status character varying(16) DEFAULT 'NEW'::character varying NOT NULL,
    error_msg text,
    call_id bigint,
    meta jsonb,
    created_by integer,
    template_id bigint,
    origin character varying(32) DEFAULT 'ui'::character varying,
    provider_msg_id character varying(64),
    provider_status character varying(32),
    provider_error_code character varying(16),
    provider_error_desc text
);


ALTER TABLE ctip.sms_out OWNER TO postgres;

--
-- TOC entry 222 (class 1259 OID 16425)
-- Name: sms_out_id_seq; Type: SEQUENCE; Schema: ctip; Owner: postgres
--

CREATE SEQUENCE ctip.sms_out_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE ctip.sms_out_id_seq OWNER TO postgres;

--
-- TOC entry 4846 (class 0 OID 0)
-- Dependencies: 222
-- Name: sms_out_id_seq; Type: SEQUENCE OWNED BY; Schema: ctip; Owner: postgres
--

ALTER SEQUENCE ctip.sms_out_id_seq OWNED BY ctip.sms_out.id;


--
-- TOC entry 4660 (class 2604 OID 16410)
-- Name: call_events id; Type: DEFAULT; Schema: ctip; Owner: postgres
--

ALTER TABLE ONLY ctip.call_events ALTER COLUMN id SET DEFAULT nextval('ctip.call_events_id_seq'::regclass);


--
-- TOC entry 4658 (class 2604 OID 16394)
-- Name: calls id; Type: DEFAULT; Schema: ctip; Owner: postgres
--

ALTER TABLE ONLY ctip.calls ALTER COLUMN id SET DEFAULT nextval('ctip.calls_id_seq'::regclass);


--
-- TOC entry 4661 (class 2604 OID 16429)
-- Name: sms_out id; Type: DEFAULT; Schema: ctip; Owner: postgres
--

ALTER TABLE ONLY ctip.sms_out ALTER COLUMN id SET DEFAULT nextval('ctip.sms_out_id_seq'::regclass);


--
-- TOC entry 4675 (class 2606 OID 16414)
-- Name: call_events call_events_pkey; Type: CONSTRAINT; Schema: ctip; Owner: postgres
--

ALTER TABLE ONLY ctip.call_events
    ADD CONSTRAINT call_events_pkey PRIMARY KEY (id);


--
-- TOC entry 4669 (class 2606 OID 16401)
-- Name: calls calls_pkey; Type: CONSTRAINT; Schema: ctip; Owner: postgres
--

ALTER TABLE ONLY ctip.calls
    ADD CONSTRAINT calls_pkey PRIMARY KEY (id);


--
-- TOC entry 4684 (class 2606 OID 16451)
-- Name: ivr_map ivr_map_pkey; Type: CONSTRAINT; Schema: ctip; Owner: postgres
--

ALTER TABLE ONLY ctip.ivr_map
    ADD CONSTRAINT ivr_map_pkey PRIMARY KEY (digit, ext);

ALTER TABLE ONLY ctip.ivr_map
    ADD CONSTRAINT uq_ivr_map_ext UNIQUE (ext);


--
-- TOC entry 4680 (class 2606 OID 16436)
-- Name: sms_out sms_out_pkey; Type: CONSTRAINT; Schema: ctip; Owner: postgres
--

ALTER TABLE ONLY ctip.sms_out
    ADD CONSTRAINT sms_out_pkey PRIMARY KEY (id);


--
-- TOC entry 4670 (class 1259 OID 16404)
-- Name: idx_calls_answered_by; Type: INDEX; Schema: ctip; Owner: postgres
--

CREATE INDEX idx_calls_answered_by ON ctip.calls USING btree (answered_by);


--
-- TOC entry 4671 (class 1259 OID 16405)
-- Name: idx_calls_direction; Type: INDEX; Schema: ctip; Owner: postgres
--

CREATE INDEX idx_calls_direction ON ctip.calls USING btree (direction);


--
-- TOC entry 4672 (class 1259 OID 16403)
-- Name: idx_calls_ext; Type: INDEX; Schema: ctip; Owner: postgres
--

CREATE INDEX idx_calls_ext ON ctip.calls USING btree (ext);


--
-- TOC entry 4673 (class 1259 OID 16402)
-- Name: idx_calls_started_at; Type: INDEX; Schema: ctip; Owner: postgres
--

CREATE INDEX idx_calls_started_at ON ctip.calls USING btree (started_at);


--
-- TOC entry 4676 (class 1259 OID 16421)
-- Name: idx_events_call_id; Type: INDEX; Schema: ctip; Owner: postgres
--

CREATE INDEX idx_events_call_id ON ctip.call_events USING btree (call_id);


--
-- TOC entry 4677 (class 1259 OID 16420)
-- Name: idx_events_ts; Type: INDEX; Schema: ctip; Owner: postgres
--

CREATE INDEX idx_events_ts ON ctip.call_events USING btree (ts);


--
-- TOC entry 4682 (class 1259 OID 16452)
-- Name: idx_ivr_map_ext; Type: INDEX; Schema: ctip; Owner: postgres
--

CREATE INDEX idx_ivr_map_ext ON ctip.ivr_map USING btree (ext);


--
-- TOC entry 4678 (class 1259 OID 16442)
-- Name: idx_sms_out_status; Type: INDEX; Schema: ctip; Owner: postgres
--

CREATE INDEX idx_sms_out_status ON ctip.sms_out USING btree (status);


--
-- TOC entry 4681 (class 1259 OID 16443)
-- Name: uq_sms_out_callid_ivr; Type: INDEX; Schema: ctip; Owner: postgres
--

CREATE UNIQUE INDEX uq_sms_out_callid_ivr ON ctip.sms_out USING btree (call_id) WHERE ((source)::text = 'ivr'::text);

CREATE INDEX idx_contact_number ON ctip.contact USING btree (number);
CREATE INDEX idx_contact_ext ON ctip.contact USING btree (ext);
CREATE INDEX idx_contact_firebird_id ON ctip.contact USING btree (firebird_id);
CREATE INDEX idx_sms_out_dest_created ON ctip.sms_out USING btree (dest, created_at DESC);
CREATE INDEX idx_sms_out_created_by ON ctip.sms_out USING btree (created_by, created_at DESC);


--
-- TOC entry 4685 (class 2606 OID 16415)
-- Name: call_events call_events_call_id_fkey; Type: FK CONSTRAINT; Schema: ctip; Owner: postgres
--

ALTER TABLE ONLY ctip.call_events
    ADD CONSTRAINT call_events_call_id_fkey FOREIGN KEY (call_id) REFERENCES ctip.calls(id) ON DELETE CASCADE;


--
-- TOC entry 4686 (class 2606 OID 16437)
-- Name: sms_out sms_out_call_id_fkey; Type: FK CONSTRAINT; Schema: ctip; Owner: postgres
--

ALTER TABLE ONLY ctip.sms_out
    ADD CONSTRAINT sms_out_call_id_fkey FOREIGN KEY (call_id) REFERENCES ctip.calls(id);

ALTER TABLE ONLY ctip.contact
    ALTER COLUMN id SET DEFAULT nextval('ctip.contact_id_seq'::regclass);

ALTER TABLE ONLY ctip.contact_device
    ALTER COLUMN id SET DEFAULT nextval('ctip.contact_device_id_seq'::regclass);

ALTER TABLE ONLY ctip.contact_device
    ADD CONSTRAINT contact_device_contact_id_fkey FOREIGN KEY (contact_id) REFERENCES ctip.contact(id) ON DELETE CASCADE;


--
-- TOC entry 4837 (class 0 OID 0)
-- Dependencies: 6
-- Name: SCHEMA ctip; Type: ACL; Schema: -; Owner: postgres
--

GRANT USAGE ON SCHEMA ctip TO appuser;


--
-- TOC entry 4838 (class 0 OID 0)
-- Dependencies: 221
-- Name: TABLE call_events; Type: ACL; Schema: ctip; Owner: postgres
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.call_events TO appuser;


--
-- TOC entry 4840 (class 0 OID 0)
-- Dependencies: 220
-- Name: SEQUENCE call_events_id_seq; Type: ACL; Schema: ctip; Owner: postgres
--

GRANT ALL ON SEQUENCE ctip.call_events_id_seq TO appuser;


--
-- TOC entry 4841 (class 0 OID 0)
-- Dependencies: 219
-- Name: TABLE calls; Type: ACL; Schema: ctip; Owner: postgres
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.calls TO appuser;


--
-- TOC entry 4843 (class 0 OID 0)
-- Dependencies: 218
-- Name: SEQUENCE calls_id_seq; Type: ACL; Schema: ctip; Owner: postgres
--

GRANT ALL ON SEQUENCE ctip.calls_id_seq TO appuser;


--
-- TOC entry 4844 (class 0 OID 0)
-- Dependencies: 224
-- Name: TABLE ivr_map; Type: ACL; Schema: ctip; Owner: postgres
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.ivr_map TO appuser;


--
-- TOC entry 4845 (class 0 OID 0)
-- Dependencies: 223
-- Name: TABLE sms_out; Type: ACL; Schema: ctip; Owner: postgres
--

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.sms_out TO appuser;

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.contact TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.contact_device TO appuser;
GRANT ALL ON SEQUENCE ctip.contact_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.contact_device_id_seq TO appuser;

--
-- Sekcja: tabele administracyjne panelu CTIP
--

CREATE SEQUENCE ctip.admin_user_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE ctip.admin_user_id_seq OWNER TO postgres;
ALTER SEQUENCE ctip.admin_user_id_seq OWNED BY ctip.admin_user.id;

CREATE TABLE ctip.admin_user (
    id integer NOT NULL DEFAULT nextval('ctip.admin_user_id_seq'::regclass),
    first_name text,
    last_name text,
    email text NOT NULL,
    internal_ext text,
    mobile_phone text,
    role text DEFAULT 'admin'::text NOT NULL,
    password_hash text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    is_salesperson boolean DEFAULT false NOT NULL,
    crm_sales_sms_enabled boolean DEFAULT false NOT NULL,
    crm_sales_email_enabled boolean DEFAULT false NOT NULL,
    crm_operations_sms_enabled boolean DEFAULT false NOT NULL,
    crm_operations_email_enabled boolean DEFAULT false NOT NULL,
    firebird_app_user_id integer,
    firebird_app_user_login text,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT admin_user_pkey PRIMARY KEY (id),
    CONSTRAINT admin_user_role_check CHECK (role = ANY (ARRAY['admin'::text, 'operator'::text, 'serwisant'::text]))
);

ALTER TABLE ctip.admin_user OWNER TO postgres;

CREATE UNIQUE INDEX ix_admin_user_email ON ctip.admin_user USING btree (email);

CREATE SEQUENCE ctip.admin_session_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE ctip.admin_session_id_seq OWNER TO postgres;
ALTER SEQUENCE ctip.admin_session_id_seq OWNED BY ctip.admin_session.id;

CREATE TABLE ctip.admin_session (
    id integer NOT NULL DEFAULT nextval('ctip.admin_session_id_seq'::regclass),
    user_id integer NOT NULL,
    token text NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    revoked_at timestamp with time zone,
    client_ip text,
    user_agent text,
    CONSTRAINT admin_session_pkey PRIMARY KEY (id),
    CONSTRAINT admin_session_user_id_fkey FOREIGN KEY (user_id)
        REFERENCES ctip.admin_user (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE CASCADE
);

ALTER TABLE ctip.admin_session OWNER TO postgres;

CREATE UNIQUE INDEX ix_admin_session_token ON ctip.admin_session USING btree (token);

CREATE TABLE ctip.admin_setting (
    key text NOT NULL,
    value text NOT NULL,
    is_secret boolean DEFAULT false NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_by integer,
    CONSTRAINT admin_setting_pkey PRIMARY KEY (key),
    CONSTRAINT admin_setting_updated_by_fkey FOREIGN KEY (updated_by)
        REFERENCES ctip.admin_user (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE SET NULL
);

ALTER TABLE ctip.admin_setting OWNER TO postgres;

CREATE SEQUENCE ctip.admin_audit_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE ctip.admin_audit_log_id_seq OWNER TO postgres;
ALTER SEQUENCE ctip.admin_audit_log_id_seq OWNED BY ctip.admin_audit_log.id;

CREATE TABLE ctip.admin_audit_log (
    id integer NOT NULL DEFAULT nextval('ctip.admin_audit_log_id_seq'::regclass),
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    user_id integer,
    action text NOT NULL,
    payload json,
    client_ip text,
    CONSTRAINT admin_audit_log_pkey PRIMARY KEY (id),
    CONSTRAINT admin_audit_log_user_id_fkey FOREIGN KEY (user_id)
        REFERENCES ctip.admin_user (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE SET NULL
);

ALTER TABLE ctip.admin_audit_log OWNER TO postgres;

CREATE INDEX ix_admin_audit_log_created_at ON ctip.admin_audit_log USING btree (created_at);

CREATE SEQUENCE ctip.form_request_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE ctip.form_request_id_seq OWNER TO postgres;
ALTER SEQUENCE ctip.form_request_id_seq OWNED BY ctip.form_request.id;

CREATE TABLE ctip.form_request (
    id integer NOT NULL DEFAULT nextval('ctip.form_request_id_seq'::regclass),
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    created_by integer,
    customer_name text NOT NULL,
    customer_email text NOT NULL,
    customer_phone text NOT NULL,
    status text DEFAULT 'GENERATED'::text NOT NULL,
    token_hash text NOT NULL,
    token_expires_at timestamp with time zone NOT NULL,
    token_used_at timestamp with time zone,
    sms_status text,
    email_status text,
    ms_status text,
    notification_error text,
    submitted_payload text,
    submitted_at timestamp with time zone,
    archive_bucket text,
    archived_at timestamp with time zone,
    archive_due_at timestamp with time zone,
    CONSTRAINT form_request_pkey PRIMARY KEY (id),
    CONSTRAINT form_request_created_by_fkey FOREIGN KEY (created_by)
        REFERENCES ctip.admin_user (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE SET NULL,
    CONSTRAINT form_request_status_check CHECK (status = ANY (ARRAY['GENERATED'::text, 'DISPATCHED'::text, 'SUBMITTED'::text, 'EXPIRED'::text])),
    CONSTRAINT form_request_archive_bucket_check CHECK ((archive_bucket IS NULL) OR (archive_bucket = ANY (ARRAY['accepted'::text, 'rejected'::text, 'unfilled'::text, 'ksero_partner'::text, 'closed_other'::text]))),
    CONSTRAINT uq_form_request_token_hash UNIQUE (token_hash)
);

ALTER TABLE ctip.form_request OWNER TO postgres;

CREATE INDEX idx_form_request_status_created ON ctip.form_request USING btree (status, created_at);
CREATE INDEX idx_form_request_created_by ON ctip.form_request USING btree (created_by, created_at);
CREATE INDEX ix_form_request_archive_bucket ON ctip.form_request USING btree (archive_bucket);
CREATE INDEX ix_form_request_archive_due_at ON ctip.form_request USING btree (archive_due_at);

CREATE SEQUENCE ctip.form_workflow_case_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE ctip.form_workflow_case_id_seq OWNER TO postgres;
ALTER SEQUENCE ctip.form_workflow_case_id_seq OWNED BY ctip.form_workflow_case.id;

CREATE TABLE ctip.form_workflow_case (
    id integer NOT NULL DEFAULT nextval('ctip.form_workflow_case_id_seq'::regclass),
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    form_request_id integer NOT NULL,
    created_by integer,
    updated_by integer,
    stage text DEFAULT 'FORM_SUBMITTED'::text NOT NULL,
    business_status text DEFAULT 'DRAFT'::text NOT NULL,
    client_mode text,
    firebird_client_id integer,
    firebird_client_status text,
    client_payload_snapshot json,
    proforma_firebird_id integer,
    proforma_number text,
    proforma_pdf_path text,
    signature_deadline_at timestamp with time zone,
    resources_release_due_at timestamp with time zone,
    resources_released_at timestamp with time zone,
    grenke_contract_start_date date,
    kp_contract_start_date date,
    status_changed_at timestamp with time zone,
    status_source text,
    status_history json,
    delivery_date date,
    delivery_time_window text,
    delivery_contact_name text,
    delivery_contact_phone text,
    delivery_notes text,
    CONSTRAINT form_workflow_case_pkey PRIMARY KEY (id),
    CONSTRAINT uq_form_workflow_case_form_request_id UNIQUE (form_request_id),
    CONSTRAINT form_workflow_case_form_request_id_fkey FOREIGN KEY (form_request_id)
        REFERENCES ctip.form_request (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE CASCADE,
    CONSTRAINT form_workflow_case_created_by_fkey FOREIGN KEY (created_by)
        REFERENCES ctip.admin_user (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE SET NULL,
    CONSTRAINT form_workflow_case_updated_by_fkey FOREIGN KEY (updated_by)
        REFERENCES ctip.admin_user (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE SET NULL,
    CONSTRAINT form_workflow_case_stage_check CHECK (stage = ANY (ARRAY['FORM_SUBMITTED'::text, 'CLIENT_READY'::text, 'DEVICES_SELECTED'::text, 'PROFORMA_CREATED'::text])),
    CONSTRAINT form_workflow_case_business_status_check CHECK (business_status = ANY (ARRAY['DRAFT'::text, 'PENDING_APPROVAL'::text, 'APPROVED'::text, 'ZEROWKA'::text, 'REJECTED'::text, 'WAITING_SIGNATURE'::text, 'APPROVED_ORDER'::text, 'REJECTED_GRENKE'::text, 'RENTAL_WITHOUT_GRENKE'::text, 'CLOSED_NOT_REALIZED'::text]))
);

ALTER TABLE ctip.form_workflow_case OWNER TO postgres;

CREATE INDEX idx_form_workflow_case_form_request ON ctip.form_workflow_case USING btree (form_request_id);
CREATE INDEX ix_form_workflow_case_resources_release_due_at ON ctip.form_workflow_case USING btree (resources_release_due_at);

CREATE SEQUENCE ctip.form_workflow_device_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE ctip.form_workflow_device_id_seq OWNER TO postgres;
ALTER SEQUENCE ctip.form_workflow_device_id_seq OWNED BY ctip.form_workflow_device.id;

CREATE TABLE ctip.form_workflow_device (
    id integer NOT NULL DEFAULT nextval('ctip.form_workflow_device_id_seq'::regclass),
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    workflow_case_id integer NOT NULL,
    source_type text DEFAULT 'google_sheet'::text NOT NULL,
    source_row integer,
    producer text,
    model text,
    serial text,
    ewidencja text,
    device_status text,
    reservation_status text,
    price text,
    price_net text,
    price_gross text,
    firebird_machine_id integer,
    firebird_client_id integer,
    snapshot json,
    CONSTRAINT form_workflow_device_pkey PRIMARY KEY (id),
    CONSTRAINT uq_form_workflow_device_source_row UNIQUE (workflow_case_id, source_type, source_row),
    CONSTRAINT form_workflow_device_workflow_case_id_fkey FOREIGN KEY (workflow_case_id)
        REFERENCES ctip.form_workflow_case (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE CASCADE,
    CONSTRAINT form_workflow_device_source_type_check CHECK (source_type = ANY (ARRAY['google_sheet'::text, 'firebird_magazyn_28'::text, 'firebird_serial'::text]))
);

ALTER TABLE ctip.form_workflow_device OWNER TO postgres;

CREATE INDEX idx_form_workflow_device_case ON ctip.form_workflow_device USING btree (workflow_case_id);

CREATE SEQUENCE ctip.workflow_sheet_status_cache_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE ctip.workflow_sheet_status_cache_id_seq OWNER TO postgres;
ALTER SEQUENCE ctip.workflow_sheet_status_cache_id_seq OWNED BY ctip.workflow_sheet_status_cache.id;

CREATE TABLE ctip.workflow_sheet_status_cache (
    id integer NOT NULL DEFAULT nextval('ctip.workflow_sheet_status_cache_id_seq'::regclass),
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
    synced_at timestamp with time zone NOT NULL,
    CONSTRAINT workflow_sheet_status_cache_pkey PRIMARY KEY (id),
    CONSTRAINT uq_workflow_sheet_status_cache_source_key UNIQUE (source_key),
    CONSTRAINT workflow_sheet_status_cache_source_type_check CHECK (
        source_type = ANY (
            ARRAY['google_sheet'::text, 'firebird_magazyn_28'::text, 'firebird_serial'::text]
        )
    )
);

ALTER TABLE ctip.workflow_sheet_status_cache OWNER TO postgres;

CREATE INDEX idx_workflow_sheet_status_cache_index_norm
    ON ctip.workflow_sheet_status_cache USING btree (device_index_normalized);

CREATE SEQUENCE ctip.delivery_case_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE ctip.delivery_case_id_seq OWNER TO postgres;
ALTER SEQUENCE ctip.delivery_case_id_seq OWNED BY ctip.delivery_case.id;

CREATE TABLE ctip.delivery_case (
    id integer NOT NULL DEFAULT nextval('ctip.delivery_case_id_seq'::regclass),
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    created_by integer,
    updated_by integer,
    source text DEFAULT 'manual'::text NOT NULL,
    case_type text DEFAULT 'delivery'::text NOT NULL,
    status text DEFAULT 'new'::text NOT NULL,
    title text NOT NULL,
    form_request_id integer,
    workflow_case_id integer,
    firebird_client_id integer,
    customer_name text,
    customer_nip text,
    customer_email text,
    customer_phone text,
    delivery_date date,
    delivery_time_window text,
    delivery_contact_name text,
    delivery_contact_phone text,
    delivery_notes text,
    service_notes text,
    snapshot json,
    CONSTRAINT delivery_case_pkey PRIMARY KEY (id),
    CONSTRAINT uq_delivery_case_workflow_case_id UNIQUE (workflow_case_id),
    CONSTRAINT delivery_case_created_by_fkey FOREIGN KEY (created_by)
        REFERENCES ctip.admin_user (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE SET NULL,
    CONSTRAINT delivery_case_updated_by_fkey FOREIGN KEY (updated_by)
        REFERENCES ctip.admin_user (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE SET NULL,
    CONSTRAINT delivery_case_form_request_id_fkey FOREIGN KEY (form_request_id)
        REFERENCES ctip.form_request (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE SET NULL,
    CONSTRAINT delivery_case_workflow_case_id_fkey FOREIGN KEY (workflow_case_id)
        REFERENCES ctip.form_workflow_case (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE SET NULL,
    CONSTRAINT delivery_case_source_check CHECK (source = ANY (ARRAY['grenke'::text, 'manual'::text])),
    CONSTRAINT delivery_case_type_check CHECK (case_type = ANY (ARRAY['delivery'::text, 'pickup'::text])),
    CONSTRAINT delivery_case_status_check CHECK (
        status = ANY (
            ARRAY['new'::text, 'planned'::text, 'in_progress'::text, 'done'::text, 'cancelled'::text]
        )
    )
);

ALTER TABLE ctip.delivery_case OWNER TO postgres;

CREATE INDEX idx_delivery_case_source_status
    ON ctip.delivery_case USING btree (source, status);

CREATE INDEX idx_delivery_case_type_status
    ON ctip.delivery_case USING btree (case_type, status);

CREATE INDEX idx_delivery_case_delivery_date
    ON ctip.delivery_case USING btree (delivery_date);

CREATE INDEX idx_delivery_case_firebird_client
    ON ctip.delivery_case USING btree (firebird_client_id);

CREATE SEQUENCE ctip.delivery_case_device_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE ctip.delivery_case_device_id_seq OWNER TO postgres;
ALTER SEQUENCE ctip.delivery_case_device_id_seq OWNED BY ctip.delivery_case_device.id;

CREATE TABLE ctip.delivery_case_device (
    id integer NOT NULL DEFAULT nextval('ctip.delivery_case_device_id_seq'::regclass),
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    delivery_case_id integer NOT NULL,
    workflow_device_id integer,
    producer text,
    model text,
    serial text,
    ewidencja text,
    firebird_machine_id integer,
    device_role text DEFAULT 'delivery'::text NOT NULL,
    source_type text,
    source_row integer,
    snapshot json,
    CONSTRAINT delivery_case_device_pkey PRIMARY KEY (id),
    CONSTRAINT uq_delivery_case_device_workflow_device_id UNIQUE (delivery_case_id, workflow_device_id),
    CONSTRAINT delivery_case_device_delivery_case_id_fkey FOREIGN KEY (delivery_case_id)
        REFERENCES ctip.delivery_case (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE CASCADE,
    CONSTRAINT delivery_case_device_workflow_device_id_fkey FOREIGN KEY (workflow_device_id)
        REFERENCES ctip.form_workflow_device (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE SET NULL
);

ALTER TABLE ctip.delivery_case_device OWNER TO postgres;

CREATE INDEX idx_delivery_case_device_case
    ON ctip.delivery_case_device USING btree (delivery_case_id);

CREATE INDEX idx_delivery_case_device_machine
    ON ctip.delivery_case_device USING btree (firebird_machine_id);

CREATE SEQUENCE ctip.delivery_case_task_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE ctip.delivery_case_task_id_seq OWNER TO postgres;
ALTER SEQUENCE ctip.delivery_case_task_id_seq OWNED BY ctip.delivery_case_task.id;

CREATE TABLE ctip.delivery_case_task (
    id integer NOT NULL DEFAULT nextval('ctip.delivery_case_task_id_seq'::regclass),
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    delivery_case_id integer NOT NULL,
    task_type text DEFAULT 'other'::text NOT NULL,
    status text DEFAULT 'todo'::text NOT NULL,
    title text NOT NULL,
    due_date date,
    due_time_window text,
    assignee_user_id integer,
    notes text,
    snapshot json,
    CONSTRAINT delivery_case_task_pkey PRIMARY KEY (id),
    CONSTRAINT delivery_case_task_delivery_case_id_fkey FOREIGN KEY (delivery_case_id)
        REFERENCES ctip.delivery_case (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE CASCADE,
    CONSTRAINT delivery_case_task_assignee_user_id_fkey FOREIGN KEY (assignee_user_id)
        REFERENCES ctip.admin_user (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE SET NULL,
    CONSTRAINT delivery_case_task_type_check CHECK (
        task_type = ANY (
            ARRAY[
                'delivery'::text,
                'preparation'::text,
                'pickup'::text,
                'zerowka'::text,
                'customer_contact'::text,
                'service_order'::text,
                'document'::text,
                'other'::text
            ]
        )
    ),
    CONSTRAINT delivery_case_task_status_check CHECK (
        status = ANY (ARRAY['todo'::text, 'planned'::text, 'done'::text, 'cancelled'::text])
    )
);

ALTER TABLE ctip.delivery_case_task OWNER TO postgres;

CREATE INDEX idx_delivery_case_task_case
    ON ctip.delivery_case_task USING btree (delivery_case_id);

CREATE INDEX idx_delivery_case_task_due
    ON ctip.delivery_case_task USING btree (status, due_date);

CREATE SEQUENCE ctip.delivery_case_file_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE ctip.delivery_case_file_id_seq OWNER TO postgres;
ALTER SEQUENCE ctip.delivery_case_file_id_seq OWNED BY ctip.delivery_case_file.id;

CREATE TABLE ctip.delivery_case_file (
    id integer NOT NULL DEFAULT nextval('ctip.delivery_case_file_id_seq'::regclass),
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    delivery_case_id integer NOT NULL,
    file_type text DEFAULT 'other'::text NOT NULL,
    source text DEFAULT 'upload'::text NOT NULL,
    file_name text NOT NULL,
    original_name text,
    path text NOT NULL,
    content_type text,
    size_bytes integer,
    uploaded_by integer,
    snapshot json,
    CONSTRAINT delivery_case_file_pkey PRIMARY KEY (id),
    CONSTRAINT delivery_case_file_delivery_case_id_fkey FOREIGN KEY (delivery_case_id)
        REFERENCES ctip.delivery_case (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE CASCADE,
    CONSTRAINT delivery_case_file_uploaded_by_fkey FOREIGN KEY (uploaded_by)
        REFERENCES ctip.admin_user (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE SET NULL,
    CONSTRAINT delivery_case_file_source_check CHECK (
        source = ANY (ARRAY['upload'::text, 'mailbox'::text, 'generated'::text])
    )
);

ALTER TABLE ctip.delivery_case_file OWNER TO postgres;

CREATE INDEX idx_delivery_case_file_case
    ON ctip.delivery_case_file USING btree (delivery_case_id);

CREATE SEQUENCE ctip.delivery_document_template_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE ctip.delivery_document_template_id_seq OWNER TO postgres;
ALTER SEQUENCE ctip.delivery_document_template_id_seq OWNED BY ctip.delivery_document_template.id;

CREATE TABLE ctip.delivery_document_template (
    id integer NOT NULL DEFAULT nextval('ctip.delivery_document_template_id_seq'::regclass),
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    template_key text NOT NULL,
    label text NOT NULL,
    document_type text DEFAULT 'other'::text NOT NULL,
    template_path text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    required_fields json,
    snapshot json,
    CONSTRAINT delivery_document_template_pkey PRIMARY KEY (id),
    CONSTRAINT uq_delivery_document_template_key UNIQUE (template_key)
);

ALTER TABLE ctip.delivery_document_template OWNER TO postgres;

CREATE INDEX idx_delivery_document_template_active
    ON ctip.delivery_document_template USING btree (active);

CREATE SEQUENCE ctip.grenke_contract_end_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER SEQUENCE ctip.grenke_contract_end_id_seq OWNER TO postgres;
ALTER SEQUENCE ctip.grenke_contract_end_id_seq OWNED BY ctip.grenke_contract_end.id;

CREATE TABLE ctip.grenke_contract_end (
    id integer NOT NULL DEFAULT nextval('ctip.grenke_contract_end_id_seq'::regclass),
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    delivery_case_id integer,
    form_request_id integer,
    workflow_case_id integer,
    status text DEFAULT 'pending_confirmation'::text NOT NULL,
    grenke_contract_start_date date,
    prefilled_end_date date,
    confirmed_end_date date,
    confirmed_at timestamp with time zone,
    confirmed_by integer,
    customer_name text,
    contract_number text,
    source_note text,
    notification_history json,
    snapshot json,
    CONSTRAINT grenke_contract_end_pkey PRIMARY KEY (id),
    CONSTRAINT uq_grenke_contract_end_workflow_case_id UNIQUE (workflow_case_id),
    CONSTRAINT grenke_contract_end_delivery_case_id_fkey FOREIGN KEY (delivery_case_id)
        REFERENCES ctip.delivery_case (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE CASCADE,
    CONSTRAINT grenke_contract_end_form_request_id_fkey FOREIGN KEY (form_request_id)
        REFERENCES ctip.form_request (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE SET NULL,
    CONSTRAINT grenke_contract_end_workflow_case_id_fkey FOREIGN KEY (workflow_case_id)
        REFERENCES ctip.form_workflow_case (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE SET NULL,
    CONSTRAINT grenke_contract_end_confirmed_by_fkey FOREIGN KEY (confirmed_by)
        REFERENCES ctip.admin_user (id) MATCH SIMPLE
        ON UPDATE NO ACTION
        ON DELETE SET NULL,
    CONSTRAINT grenke_contract_end_status_check CHECK (
        status = ANY (
            ARRAY['pending_confirmation'::text, 'confirmed'::text, 'cancelled'::text]
        )
    )
);

ALTER TABLE ctip.grenke_contract_end OWNER TO postgres;

CREATE INDEX idx_grenke_contract_end_status_date
    ON ctip.grenke_contract_end USING btree (status, confirmed_end_date);

CREATE INDEX idx_grenke_contract_end_pending_prefill
    ON ctip.grenke_contract_end USING btree (status, prefilled_end_date);

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.admin_user TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.admin_session TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.admin_setting TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.admin_audit_log TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.form_request TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.form_workflow_case TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.form_workflow_device TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.workflow_sheet_status_cache TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.delivery_case TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.delivery_case_device TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.delivery_case_task TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.delivery_case_file TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.delivery_document_template TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.grenke_contract_end TO appuser;
GRANT ALL ON SEQUENCE ctip.admin_user_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.admin_session_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.admin_audit_log_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.form_request_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.form_workflow_case_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.form_workflow_device_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.workflow_sheet_status_cache_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.delivery_case_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.delivery_case_device_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.delivery_case_task_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.delivery_case_file_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.delivery_document_template_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.grenke_contract_end_id_seq TO appuser;


--
-- TOC entry 4847 (class 0 OID 0)
-- Dependencies: 222
-- Name: SEQUENCE sms_out_id_seq; Type: ACL; Schema: ctip; Owner: postgres
--

GRANT ALL ON SEQUENCE ctip.sms_out_id_seq TO appuser;


--
-- TOC entry 2059 (class 826 OID 16423)
-- Name: DEFAULT PRIVILEGES FOR SEQUENCES; Type: DEFAULT ACL; Schema: ctip; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA ctip GRANT ALL ON SEQUENCES TO appuser;


--
-- TOC entry 2060 (class 826 OID 16422)
-- Name: DEFAULT PRIVILEGES FOR TABLES; Type: DEFAULT ACL; Schema: ctip; Owner: postgres
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA ctip GRANT SELECT,INSERT,DELETE,UPDATE ON TABLES TO appuser;


-- Completed on 2025-10-09 17:43:37

--
-- PostgreSQL database dump complete
--

\unrestrict ZhoCtUc30GzkxBj7Yh0bzFBbOdWa7nQwYCLFFyI6FrXZAs0p8C43i7qFe78uujG
-- Katalog tożsamości botów (migracja 71c4e8a2d9f0, 2026-07-24):
-- ctip.bot_identity_customer
-- ctip.bot_identity_subject
-- ctip.bot_identity_phone
-- ctip.bot_identity_binding
-- ctip.bot_identity_device
-- ctip.bot_identity_override
-- ctip.bot_identity_sync_run
-- ctip.bot_identity_resolution
-- ctip.bot_disclosure_grant
-- Pełna definicja, klucze i ograniczenia są utrzymywane w:
-- alembic/versions/71c4e8a2d9f0_add_bot_identity_directory.py
-- Rozszerzenie katalogu i Centrum Obsługi (migracja e2b7c4d9a610, 2026-07-27):
-- ctip.bot_identity_customer.nip_enc
-- ctip.bot_identity_customer.nip_hmac
-- ctip.bot_identity_resolution.nip_failure_count
-- ctip.bot_identity_resolution.nip_verified_at
-- ctip.bot_identity_sms_challenge
-- ctip.crm_case
-- ctip.crm_case_event
-- Pełna definicja, klucze i ograniczenia są utrzymywane w:
-- alembic/versions/e2b7c4d9a610_add_crm_cases_and_nip_verification.py
-- Bezpieczne referencje i rozszerzony kontrakt urządzeń (migracja a6f3c8d2e910, 2026-07-28):
-- ctip.bot_identity_device.device_ref
-- ctip.bot_identity_device.image_url
-- ctip.crm_case.device_refs
-- Pełna definicja, backfill UUID i ograniczenia są utrzymywane w:
-- alembic/versions/a6f3c8d2e910_expand_bot_device_disclosure.py
-- Uproszczenie kolejek CRM i powiadomienia użytkowników (migracja f3a7c9e2d610, 2026-07-30):
-- ctip.admin_user.crm_sales_sms_enabled
-- ctip.admin_user.crm_sales_email_enabled
-- ctip.admin_user.crm_operations_sms_enabled
-- ctip.admin_user.crm_operations_email_enabled
-- ctip.crm_case.category
-- Kolejki crm_case: sales, service_it, contracts, other.
-- Historyczne meters są mapowane do contracts, a accounting do other.
-- Pełna definicja, mapowanie danych i ograniczenia są utrzymywane w:
-- alembic/versions/f3a7c9e2d610_simplify_crm_queues_and_user_notifications.py
-- Moduł wysyłek i katalog zgodności części (migracja f1a2b3c4d5e6, 2026-08-05):
-- ctip.shipping_address
-- ctip.shipping_consumable_compatibility
-- ctip.shipping_case
-- ctip.shipping_item
-- ctip.shipping_shipment
-- ctip.shipping_day_close
-- ctip.shipping_event
-- shipping_consumable_compatibility.status: suggested, confirmed, rejected, stale.
-- shipping_consumable_compatibility.confidence: high, medium, low albo NULL.
-- Dowody są przechowywane w kolumnie evidence JSON; ręczna decyzja w reviewed_by,
-- reviewed_at i review_note. Relacja ma unikalność po firebird_model_id oraz
-- firebird_warehouse_item_id i nie modyfikuje MAGAZYN.ID_MODEL w Firebird.
-- Pełna definicja, indeksy, klucze i procedura adopcji prototypu są utrzymywane w:
-- alembic/versions/f1a2b3c4d5e6_add_shipping_module.py
-- Ochrona adresu po zmianie lokalizacji urządzenia (migracja a4b5c6d7e8f9, 2026-08-05):
-- ctip.shipping_address.location_source
-- ctip.shipping_address.location_text_snapshot
-- ctip.shipping_address.location_fingerprint
-- ctip.shipping_case.location_source
-- ctip.shipping_case.location_text_snapshot
-- ctip.shipping_case.location_fingerprint
-- Indeks idx_shipping_address_machine_location obejmuje firebird_machine_id,
-- location_fingerprint i updated_at. Pełna definicja jest utrzymywana w:
-- alembic/versions/a4b5c6d7e8f9_add_shipping_location_guard.py
-- Wybór rozliczenia wysyłki przez fakturę (migracja b5c6d7e8f901, 2026-08-24):
-- ctip.shipping_case.invoice_required BOOLEAN NOT NULL DEFAULT false
-- Wartość true wybiera automatyczne utworzenie FV i WZ; false wybiera RW dla umowy
-- albo WZ dla zlecenia poza umową. Pełna definicja jest utrzymywana w:
-- alembic/versions/b5c6d7e8f901_add_shipping_invoice_required.py
-- Jawna zgoda na czasowy ujemny stan (migracja c6d7e8f901a2, 2026-08-25):
-- ctip.shipping_item.allow_negative_stock BOOLEAN NOT NULL DEFAULT false
-- Pole jest ustawiane wyłącznie dla pozycji, która podczas akceptacji miała rzeczywisty
-- stan zerowy, a operator potwierdził oczekiwaną dostawę. Zgoda jest przekazywana do
-- kontrolowanego zapisu ZPOZYCJA oraz dokumentu RW albo WZ. Pełna definicja jest utrzymywana w:
-- alembic/versions/c6d7e8f901a2_add_shipping_negative_stock.py
-- Ceny rozliczeniowe i dokumenty końcowe wysyłki (migracja d7e8f901a2b3, 2026-08-26):
-- ctip.shipping_item.catalog_price_net NUMERIC(18,4) NOT NULL DEFAULT 0
-- ctip.shipping_item.price_source TEXT NOT NULL DEFAULT 'sale'
-- price_source: sale, purchase_fallback, purchase_contract albo manual.
-- ctip.shipping_shipment.firebird_rw_id / firebird_rw_number
-- ctip.shipping_shipment.firebird_wz_id / firebird_wz_number
-- ctip.shipping_shipment.firebird_invoice_id / firebird_invoice_number
-- Pole shipping_item.price_net przechowuje cenę zaakceptowaną przez operatora.
-- Pełna definicja i backfill ceny katalogowej są utrzymywane w:
-- alembic/versions/d7e8f901a2b3_add_shipping_prices_and_documents.py
-- Archiwum zakończonych wysyłek (migracja e8f901a2b3c4, 2026-08-27):
-- ctip.shipping_shipment.closed_by INTEGER NULL
-- ctip.shipping_shipment.archive_snapshot JSON NULL
-- ctip.shipping_shipment.archive_search_text TEXT NULL
-- closed_by wskazuje operatora finalizacji, a archive_snapshot przechowuje niezmienny
-- stan zlecenia, odbiorcy, urządzenia, części, cen, DPD i dokumentów RW/WZ/FV.
-- Indeksy idx_shipping_shipment_archive_closed i idx_shipping_shipment_archive_operator
-- obsługują filtry rejestru. Indeks GIN idx_shipping_shipment_archive_search_trgm
-- korzysta z pg_trgm i przyspiesza wyszukiwanie wielowyrazowe oraz przybliżone.
-- Pełna definicja i backfill zamkniętych spraw są utrzymywane w:
-- alembic/versions/e8f901a2b3c4_add_shipping_archive.py
-- Wspólny graf migracji urządzeń, dostaw i umów:
-- alembic/versions/4e2a9c7d1b60_add_device_inventory_registry.py
-- alembic/versions/7c91e2f4a6b8_add_workflow_sheet_counters.py
-- alembic/versions/9d4b6f2a1c80_add_device_audit.py
-- alembic/versions/b5c7d9e1f302_add_device_counters_and_pz_withdrawal.py
-- alembic/versions/c4d8e2f6a1b3_add_device_theme_to_admin_user.py
-- alembic/versions/d6f1a8c3e740_merge_device_and_contract_heads.py
