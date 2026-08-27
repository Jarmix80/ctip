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
    firebird_app_user_id integer,
    firebird_app_user_login text,
    can_withdraw_device_pz boolean DEFAULT false NOT NULL,
    device_theme text DEFAULT 'blue'::text NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT admin_user_pkey PRIMARY KEY (id),
    CONSTRAINT admin_user_role_check CHECK (role = ANY (ARRAY['admin'::text, 'operator'::text])),
    CONSTRAINT admin_user_device_theme_check CHECK (device_theme = ANY (ARRAY['blue'::text, 'graphite'::text, 'mint'::text]))
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
    producer text,
    model text,
    serial text,
    device_index text,
    device_index_normalized text,
    sheet_row integer,
    sheet_status text,
    sheet_notes text,
    counter_bw text,
    counter_color text,
    counter_scan text,
    reservation_status text,
    reservation_grenke text,
    reservation_until date,
    price text,
    ms_id_maszyna integer,
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

CREATE SEQUENCE ctip.device_intake_operation_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE ctip.device_intake_operation_id_seq OWNER TO postgres;

CREATE TABLE ctip.device_intake_operation (
    id integer NOT NULL DEFAULT nextval('ctip.device_intake_operation_id_seq'::regclass),
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    status text NOT NULL,
    created_by integer,
    supplier_firebird_id integer NOT NULL,
    external_document text,
    exception_reason text,
    firebird_pz_id integer,
    firebird_pz_number text,
    request_payload json NOT NULL,
    result_snapshot json,
    error_text text,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    completed_at timestamp with time zone,
    withdrawn_by integer,
    withdrawal_reason text,
    withdrawal_preview json,
    withdrawn_at timestamp with time zone,
    CONSTRAINT device_intake_operation_pkey PRIMARY KEY (id),
    CONSTRAINT uq_device_intake_operation_idempotency_key UNIQUE (idempotency_key),
    CONSTRAINT device_intake_operation_created_by_fkey FOREIGN KEY (created_by)
        REFERENCES ctip.admin_user (id) ON DELETE SET NULL,
    CONSTRAINT device_intake_operation_withdrawn_by_fkey FOREIGN KEY (withdrawn_by)
        REFERENCES ctip.admin_user (id) ON DELETE SET NULL,
    CONSTRAINT device_intake_operation_status_check CHECK (
        status = ANY (
            ARRAY[
                'processing'::text,
                'completed'::text,
                'failed'::text,
                'reconcile_required'::text,
                'withdrawn'::text
            ]
        )
    )
);
ALTER TABLE ctip.device_intake_operation OWNER TO postgres;
ALTER SEQUENCE ctip.device_intake_operation_id_seq
    OWNED BY ctip.device_intake_operation.id;

CREATE SEQUENCE ctip.device_inventory_unit_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE ctip.device_inventory_unit_id_seq OWNER TO postgres;

CREATE TABLE ctip.device_inventory_unit (
    id integer NOT NULL DEFAULT nextval('ctip.device_inventory_unit_id_seq'::regclass),
    operation_id integer,
    source_type text DEFAULT 'firebird_magazyn_28'::text NOT NULL,
    source_row integer NOT NULL,
    firebird_pz_id integer,
    firebird_zakpozycja_id integer,
    firebird_machine_id integer,
    firebird_machine_table_id integer,
    firebird_model_id integer,
    firebird_supplier_id integer,
    serial text NOT NULL,
    serial_normalized text NOT NULL,
    ewidencja text NOT NULL,
    ewidencja_normalized text NOT NULL,
    purchase_price_net numeric(18,4),
    sheet_row integer,
    sheet_sync_status text DEFAULT 'pending'::text NOT NULL,
    sheet_sync_error text,
    snapshot json,
    status text DEFAULT 'active'::text NOT NULL,
    withdrawn_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT device_inventory_unit_pkey PRIMARY KEY (id),
    CONSTRAINT uq_device_inventory_unit_source UNIQUE (source_type, source_row),
    CONSTRAINT uq_device_inventory_unit_zakpozycja UNIQUE (firebird_zakpozycja_id),
    CONSTRAINT uq_device_inventory_unit_machine_table UNIQUE (firebird_machine_table_id),
    CONSTRAINT device_inventory_unit_operation_id_fkey FOREIGN KEY (operation_id)
        REFERENCES ctip.device_intake_operation (id) ON DELETE SET NULL,
    CONSTRAINT device_inventory_unit_source_type_check CHECK (
        source_type = 'firebird_magazyn_28'::text
    ),
    CONSTRAINT device_inventory_unit_status_check CHECK (
        status = ANY (ARRAY['active'::text, 'withdrawn'::text])
    )
);
ALTER TABLE ctip.device_inventory_unit OWNER TO postgres;
ALTER SEQUENCE ctip.device_inventory_unit_id_seq
    OWNED BY ctip.device_inventory_unit.id;

CREATE UNIQUE INDEX uq_device_inventory_unit_serial_normalized
    ON ctip.device_inventory_unit USING btree (serial_normalized)
    WHERE status = 'active'::text;
CREATE UNIQUE INDEX uq_device_inventory_unit_ewidencja_normalized
    ON ctip.device_inventory_unit USING btree (ewidencja_normalized)
    WHERE status = 'active'::text;

CREATE SEQUENCE ctip.device_inventory_event_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE ctip.device_inventory_event_id_seq OWNER TO postgres;

CREATE TABLE ctip.device_inventory_event (
    id integer NOT NULL DEFAULT nextval('ctip.device_inventory_event_id_seq'::regclass),
    unit_id integer NOT NULL,
    event_type text NOT NULL,
    created_by integer,
    payload json,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT device_inventory_event_pkey PRIMARY KEY (id),
    CONSTRAINT device_inventory_event_unit_id_fkey FOREIGN KEY (unit_id)
        REFERENCES ctip.device_inventory_unit (id) ON DELETE CASCADE,
    CONSTRAINT device_inventory_event_created_by_fkey FOREIGN KEY (created_by)
        REFERENCES ctip.admin_user (id) ON DELETE SET NULL
);
ALTER TABLE ctip.device_inventory_event OWNER TO postgres;
ALTER SEQUENCE ctip.device_inventory_event_id_seq
    OWNED BY ctip.device_inventory_event.id;
CREATE INDEX idx_device_inventory_event_unit_created
    ON ctip.device_inventory_event USING btree (unit_id, created_at DESC);

CREATE SEQUENCE ctip.device_counter_reading_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE ctip.device_counter_reading_id_seq OWNER TO postgres;

CREATE TABLE ctip.device_counter_reading (
    id integer NOT NULL DEFAULT nextval('ctip.device_counter_reading_id_seq'::regclass),
    unit_id integer NOT NULL,
    source text NOT NULL,
    reading_at timestamp with time zone NOT NULL,
    counter_bw bigint,
    counter_color bigint,
    counter_scan bigint,
    applied_to_current boolean DEFAULT true NOT NULL,
    override_reason text,
    note text,
    created_by integer,
    source_snapshot json,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT device_counter_reading_pkey PRIMARY KEY (id),
    CONSTRAINT device_counter_reading_unit_id_fkey FOREIGN KEY (unit_id)
        REFERENCES ctip.device_inventory_unit (id) ON DELETE CASCADE,
    CONSTRAINT device_counter_reading_created_by_fkey FOREIGN KEY (created_by)
        REFERENCES ctip.admin_user (id) ON DELETE SET NULL,
    CONSTRAINT device_counter_reading_source_check CHECK (
        source = ANY (ARRAY['intake'::text, 'manual'::text])
    ),
    CONSTRAINT device_counter_reading_value_check CHECK (
        counter_bw IS NOT NULL OR counter_color IS NOT NULL OR counter_scan IS NOT NULL
    ),
    CONSTRAINT device_counter_reading_nonnegative_check CHECK (
        (counter_bw IS NULL OR counter_bw >= 0)
        AND (counter_color IS NULL OR counter_color >= 0)
        AND (counter_scan IS NULL OR counter_scan >= 0)
    )
);
ALTER TABLE ctip.device_counter_reading OWNER TO postgres;
ALTER SEQUENCE ctip.device_counter_reading_id_seq
    OWNED BY ctip.device_counter_reading.id;
CREATE INDEX idx_device_counter_reading_unit_date
    ON ctip.device_counter_reading USING btree (unit_id, reading_at DESC);

CREATE SEQUENCE ctip.device_manual_reservation_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE ctip.device_manual_reservation_id_seq OWNER TO postgres;

CREATE TABLE ctip.device_manual_reservation (
    id integer NOT NULL DEFAULT nextval('ctip.device_manual_reservation_id_seq'::regclass),
    unit_id integer NOT NULL,
    reserved_for text NOT NULL,
    reason text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    created_by integer,
    released_by integer,
    release_reason text,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    released_at timestamp with time zone,
    CONSTRAINT device_manual_reservation_pkey PRIMARY KEY (id),
    CONSTRAINT device_manual_reservation_unit_id_fkey FOREIGN KEY (unit_id)
        REFERENCES ctip.device_inventory_unit (id) ON DELETE CASCADE,
    CONSTRAINT device_manual_reservation_created_by_fkey FOREIGN KEY (created_by)
        REFERENCES ctip.admin_user (id) ON DELETE SET NULL,
    CONSTRAINT device_manual_reservation_released_by_fkey FOREIGN KEY (released_by)
        REFERENCES ctip.admin_user (id) ON DELETE SET NULL
);
ALTER TABLE ctip.device_manual_reservation OWNER TO postgres;
ALTER SEQUENCE ctip.device_manual_reservation_id_seq
    OWNED BY ctip.device_manual_reservation.id;
CREATE UNIQUE INDEX uq_device_manual_reservation_active
    ON ctip.device_manual_reservation USING btree (unit_id)
    WHERE released_at IS NULL;

CREATE SEQUENCE ctip.device_sheet_outbox_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE ctip.device_sheet_outbox_id_seq OWNER TO postgres;

CREATE TABLE ctip.device_sheet_outbox (
    id integer NOT NULL DEFAULT nextval('ctip.device_sheet_outbox_id_seq'::regclass),
    unit_id integer NOT NULL,
    idempotency_key text NOT NULL,
    operation_type text NOT NULL,
    status text NOT NULL,
    payload json NOT NULL,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 10 NOT NULL,
    next_attempt_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    locked_at timestamp with time zone,
    last_error text,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT device_sheet_outbox_pkey PRIMARY KEY (id),
    CONSTRAINT uq_device_sheet_outbox_idempotency_key UNIQUE (idempotency_key),
    CONSTRAINT device_sheet_outbox_unit_id_fkey FOREIGN KEY (unit_id)
        REFERENCES ctip.device_inventory_unit (id) ON DELETE CASCADE,
    CONSTRAINT device_sheet_outbox_operation_type_check CHECK (
        operation_type = ANY (
            ARRAY[
                'upsert_device'::text,
                'update_note'::text,
                'update_counters'::text,
                'delete_device'::text,
                'update_reservation'::text,
                'release_reservation'::text
            ]
        )
    ),
    CONSTRAINT device_sheet_outbox_status_check CHECK (
        status = ANY (
            ARRAY['pending'::text, 'processing'::text, 'completed'::text, 'failed'::text]
        )
    )
);
ALTER TABLE ctip.device_sheet_outbox OWNER TO postgres;
ALTER SEQUENCE ctip.device_sheet_outbox_id_seq
    OWNED BY ctip.device_sheet_outbox.id;
CREATE INDEX idx_device_sheet_outbox_pending
    ON ctip.device_sheet_outbox USING btree (status, next_attempt_at);

CREATE SEQUENCE ctip.device_audit_run_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE ctip.device_audit_run_id_seq OWNER TO postgres;

CREATE TABLE ctip.device_audit_run (
    id integer NOT NULL DEFAULT nextval('ctip.device_audit_run_id_seq'::regclass),
    status text DEFAULT 'pending'::text NOT NULL,
    requested_by integer,
    phase text,
    processed_items integer DEFAULT 0 NOT NULL,
    total_items integer DEFAULT 0 NOT NULL,
    summary json,
    source_snapshot json,
    error_text text,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    CONSTRAINT device_audit_run_pkey PRIMARY KEY (id),
    CONSTRAINT device_audit_run_requested_by_fkey FOREIGN KEY (requested_by)
        REFERENCES ctip.admin_user (id) ON DELETE SET NULL,
    CONSTRAINT device_audit_run_status_check CHECK (
        status = ANY (
            ARRAY[
                'pending'::text,
                'running'::text,
                'completed'::text,
                'failed'::text
            ]
        )
    )
);
ALTER TABLE ctip.device_audit_run OWNER TO postgres;
ALTER SEQUENCE ctip.device_audit_run_id_seq
    OWNED BY ctip.device_audit_run.id;
CREATE INDEX idx_device_audit_run_status_created
    ON ctip.device_audit_run USING btree (status, created_at DESC);

CREATE SEQUENCE ctip.device_audit_item_id_seq
    START WITH 1 INCREMENT BY 1 NO MINVALUE NO MAXVALUE CACHE 1;
ALTER SEQUENCE ctip.device_audit_item_id_seq OWNER TO postgres;

CREATE TABLE ctip.device_audit_item (
    id integer NOT NULL DEFAULT nextval('ctip.device_audit_item_id_seq'::regclass),
    run_id integer NOT NULL,
    canonical_key text NOT NULL,
    producer text,
    model text,
    serial text,
    ewidencja text,
    source_row integer,
    sheet_row integer,
    machine_id integer,
    ctip_unit_id integer,
    sheet_present boolean DEFAULT false NOT NULL,
    warehouse_present boolean DEFAULT false NOT NULL,
    machine_present boolean DEFAULT false NOT NULL,
    ctip_present boolean DEFAULT false NOT NULL,
    result_status text NOT NULL,
    issue_codes json DEFAULT '[]'::json NOT NULL,
    issue_summary text,
    source_details json DEFAULT '{}'::json NOT NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT device_audit_item_pkey PRIMARY KEY (id),
    CONSTRAINT uq_device_audit_item_run_key UNIQUE (run_id, canonical_key),
    CONSTRAINT device_audit_item_run_id_fkey FOREIGN KEY (run_id)
        REFERENCES ctip.device_audit_run (id) ON DELETE CASCADE,
    CONSTRAINT device_audit_item_result_status_check CHECK (
        result_status = ANY (
            ARRAY[
                'ok'::text,
                'missing'::text,
                'discrepancy'::text,
                'duplicate'::text
            ]
        )
    )
);
ALTER TABLE ctip.device_audit_item OWNER TO postgres;
ALTER SEQUENCE ctip.device_audit_item_id_seq
    OWNED BY ctip.device_audit_item.id;
CREATE INDEX idx_device_audit_item_run_result
    ON ctip.device_audit_item USING btree (run_id, result_status);

--
-- Sekcja: moduł realizacji wysyłek części
-- Migracja Alembic: f9a0b1c2d3e4
--

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE ctip.shipping_address (
    id integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    firebird_client_id integer NOT NULL,
    firebird_machine_id integer,
    location_key text NOT NULL,
    location_source text,
    location_text_snapshot text,
    location_fingerprint text,
    company_name text NOT NULL,
    contact_name text,
    street text NOT NULL,
    postal_code text NOT NULL,
    city text NOT NULL,
    country_code text DEFAULT 'PL'::text NOT NULL,
    phone text NOT NULL,
    email text,
    source text NOT NULL,
    verified_by integer REFERENCES ctip.admin_user(id) ON DELETE SET NULL,
    verified_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT uq_shipping_address_client_location UNIQUE (firebird_client_id, location_key)
);

CREATE INDEX idx_shipping_address_machine_location
    ON ctip.shipping_address USING btree (firebird_machine_id, location_fingerprint, updated_at);

CREATE TABLE ctip.shipping_consumable_compatibility (
    id integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    firebird_model_id integer NOT NULL,
    firebird_warehouse_item_id integer NOT NULL,
    model_label text NOT NULL,
    item_index text,
    item_name text NOT NULL,
    item_kind text,
    status text DEFAULT 'suggested'::text NOT NULL,
    confidence text,
    evidence json DEFAULT '[]'::json NOT NULL,
    source_hash text,
    first_seen_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    last_seen_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    reviewed_by integer REFERENCES ctip.admin_user(id) ON DELETE SET NULL,
    reviewed_at timestamp with time zone,
    review_note text,
    confirmed_by integer REFERENCES ctip.admin_user(id) ON DELETE SET NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT uq_shipping_compatibility_model_item
        UNIQUE (firebird_model_id, firebird_warehouse_item_id),
    CONSTRAINT shipping_compatibility_status_check
        CHECK (status = ANY (ARRAY['suggested'::text, 'confirmed'::text, 'rejected'::text, 'stale'::text])),
    CONSTRAINT shipping_compatibility_confidence_check
        CHECK (confidence IS NULL OR confidence = ANY (ARRAY['high'::text, 'medium'::text, 'low'::text]))
);

CREATE INDEX idx_shipping_compatibility_model
    ON ctip.shipping_consumable_compatibility USING btree (firebird_model_id);
CREATE INDEX idx_shipping_compatibility_status
    ON ctip.shipping_consumable_compatibility USING btree (status, confidence);

CREATE TABLE ctip.shipping_case (
    id integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    firebird_order_table_id integer NOT NULL,
    firebird_order_id integer NOT NULL,
    firebird_order_year integer NOT NULL,
    firebird_client_id integer NOT NULL,
    firebird_machine_id integer,
    firebird_model_id integer,
    order_kind text,
    invoice_required boolean DEFAULT false NOT NULL,
    status text DEFAULT 'review_pending'::text NOT NULL,
    address_id integer REFERENCES ctip.shipping_address(id) ON DELETE SET NULL,
    address_snapshot json NOT NULL,
    source_snapshot json NOT NULL,
    location_source text,
    location_text_snapshot text,
    location_fingerprint text,
    weight_kg numeric(8,3) NOT NULL,
    reviewed_by integer REFERENCES ctip.admin_user(id) ON DELETE SET NULL,
    reviewed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT uq_shipping_case_firebird_order UNIQUE (firebird_order_table_id),
    CONSTRAINT shipping_case_status_check
        CHECK (status = ANY (ARRAY['review_pending'::text, 'ready'::text, 'shipment_created'::text,
            'handed_over'::text, 'closed'::text, 'manual_billing'::text, 'reconcile_required'::text]))
);

CREATE INDEX idx_shipping_case_status_updated
    ON ctip.shipping_case USING btree (status, updated_at DESC);

CREATE TABLE ctip.shipping_item (
    id integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    shipping_case_id integer NOT NULL REFERENCES ctip.shipping_case(id) ON DELETE CASCADE,
    firebird_warehouse_item_id integer NOT NULL,
    warehouse_id integer NOT NULL,
    item_index text,
    item_name text NOT NULL,
    unit text DEFAULT 'szt.'::text NOT NULL,
    quantity numeric(12,3) NOT NULL,
    price_net numeric(18,4) NOT NULL,
    catalog_price_net numeric(18,4) DEFAULT 0 NOT NULL,
    purchase_price_net numeric(18,4) NOT NULL,
    price_source text DEFAULT 'sale'::text NOT NULL,
    vat_rate numeric(8,3) NOT NULL,
    allow_negative_stock boolean DEFAULT false NOT NULL,
    firebird_position_id integer,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT uq_shipping_item_case_warehouse
        UNIQUE (shipping_case_id, firebird_warehouse_item_id),
    CONSTRAINT shipping_item_price_source_check
        CHECK (price_source = ANY (ARRAY['sale'::text, 'purchase_fallback'::text,
            'purchase_contract'::text, 'manual'::text]))
);

CREATE TABLE ctip.shipping_day_close (
    id integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    business_date date NOT NULL,
    status text DEFAULT 'processing'::text NOT NULL,
    shipment_count integer DEFAULT 0 NOT NULL,
    closed_count integer DEFAULT 0 NOT NULL,
    error_count integer DEFAULT 0 NOT NULL,
    summary json NOT NULL,
    closed_by integer REFERENCES ctip.admin_user(id) ON DELETE SET NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT uq_shipping_day_close_date UNIQUE (business_date),
    CONSTRAINT shipping_day_close_status_check
        CHECK (status = ANY (ARRAY['processing'::text, 'completed'::text, 'partial'::text, 'failed'::text]))
);

CREATE TABLE ctip.shipping_shipment (
    id integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    shipping_case_id integer NOT NULL REFERENCES ctip.shipping_case(id) ON DELETE CASCADE,
    day_close_id integer REFERENCES ctip.shipping_day_close(id) ON DELETE SET NULL,
    idempotency_key text NOT NULL,
    provider text DEFAULT 'dpd'::text NOT NULL,
    provider_mode text NOT NULL,
    provider_shipment_id text,
    tracking_number text,
    status text DEFAULT 'processing'::text NOT NULL,
    label_content bytea,
    label_content_type text,
    label_format text,
    provider_request json NOT NULL,
    provider_response json,
    firebird_status text DEFAULT 'pending'::text NOT NULL,
    firebird_error text,
    firebird_rw_id integer,
    firebird_rw_number text,
    firebird_wz_id integer,
    firebird_wz_number text,
    firebird_invoice_id integer,
    firebird_invoice_number text,
    notification_sms_status text DEFAULT 'pending'::text NOT NULL,
    notification_email_status text DEFAULT 'pending'::text NOT NULL,
    notification_error text,
    error_text text,
    created_by integer REFERENCES ctip.admin_user(id) ON DELETE SET NULL,
    closed_by integer REFERENCES ctip.admin_user(id) ON DELETE SET NULL,
    archive_snapshot json,
    archive_search_text text,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    handed_over_at timestamp with time zone,
    closed_at timestamp with time zone,
    CONSTRAINT uq_shipping_shipment_case UNIQUE (shipping_case_id),
    CONSTRAINT uq_shipping_shipment_idempotency UNIQUE (idempotency_key),
    CONSTRAINT shipping_shipment_status_check
        CHECK (status = ANY (ARRAY['processing'::text, 'label_ready'::text, 'handed_over'::text,
            'closed'::text, 'failed'::text, 'reconcile_required'::text]))
);

CREATE INDEX idx_shipping_shipment_status_created
    ON ctip.shipping_shipment USING btree (status, created_at DESC);
CREATE INDEX idx_shipping_shipment_archive_closed
    ON ctip.shipping_shipment USING btree (status, closed_at DESC, id DESC)
    WHERE status = 'closed'::text;
CREATE INDEX idx_shipping_shipment_archive_operator
    ON ctip.shipping_shipment USING btree (closed_by, closed_at DESC)
    WHERE status = 'closed'::text;
CREATE INDEX idx_shipping_shipment_archive_search_trgm
    ON ctip.shipping_shipment USING gin (archive_search_text gin_trgm_ops)
    WHERE status = 'closed'::text;

CREATE TABLE ctip.shipping_event (
    id integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    shipping_case_id integer NOT NULL REFERENCES ctip.shipping_case(id) ON DELETE CASCADE,
    shipment_id integer REFERENCES ctip.shipping_shipment(id) ON DELETE CASCADE,
    event_type text NOT NULL,
    payload json NOT NULL,
    created_by integer REFERENCES ctip.admin_user(id) ON DELETE SET NULL,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE INDEX idx_shipping_event_case_created
    ON ctip.shipping_event USING btree (shipping_case_id, created_at DESC);

CREATE TABLE ctip.contracts_mailbox_history_case (
    id serial PRIMARY KEY,
    application_no_raw text NOT NULL,
    application_no_normalized text NOT NULL UNIQUE,
    title text NOT NULL,
    status text DEFAULT 'historical_closed'::text NOT NULL,
    source text DEFAULT 'mailbox_backfill'::text NOT NULL,
    message_count integer DEFAULT 0 NOT NULL,
    first_message_at timestamp with time zone,
    last_message_at timestamp with time zone,
    case_metadata json,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    archived_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT contracts_mailbox_history_case_status_check CHECK (status = 'historical_closed'::text)
);

CREATE INDEX idx_contracts_mailbox_history_case_archived
    ON ctip.contracts_mailbox_history_case USING btree (archived_at, id);

CREATE TABLE ctip.contracts_mailbox_message (
    id serial PRIMARY KEY,
    message_id text NOT NULL UNIQUE,
    mailbox_folder text DEFAULT 'INBOX'::text NOT NULL,
    imap_id text,
    processing_status text DEFAULT 'pending'::text NOT NULL,
    classification text,
    event_type text,
    application_no_raw text,
    application_no_normalized text,
    proforma_no_raw text,
    proforma_no_normalized text,
    subject text DEFAULT ''::text NOT NULL,
    sender text DEFAULT ''::text NOT NULL,
    body_text text DEFAULT ''::text NOT NULL,
    received_at timestamp with time zone NOT NULL,
    form_request_id integer REFERENCES ctip.form_request(id) ON DELETE SET NULL,
    history_case_id integer REFERENCES ctip.contracts_mailbox_history_case(id) ON DELETE CASCADE,
    attachment_manifest json DEFAULT '[]'::json NOT NULL,
    details text,
    attempts integer DEFAULT 0 NOT NULL,
    last_error text,
    first_seen_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    last_seen_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    processed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at timestamp with time zone DEFAULT timezone('utc'::text, now()) NOT NULL,
    CONSTRAINT contracts_mailbox_message_status_check CHECK (
        processing_status = ANY (ARRAY[
            'pending'::text,
            'linked_form'::text,
            'historical_archived'::text,
            'ignored'::text,
            'manual_hold'::text,
            'error'::text
        ])
    ),
    CONSTRAINT contracts_mailbox_message_single_target_check CHECK (
        form_request_id IS NULL OR history_case_id IS NULL
    )
);

CREATE INDEX idx_contracts_mailbox_message_status_received
    ON ctip.contracts_mailbox_message USING btree (processing_status, received_at);
CREATE INDEX idx_contracts_mailbox_message_application
    ON ctip.contracts_mailbox_message USING btree (application_no_normalized);
CREATE INDEX idx_contracts_mailbox_message_form
    ON ctip.contracts_mailbox_message USING btree (form_request_id, received_at);
CREATE INDEX idx_contracts_mailbox_message_history_case
    ON ctip.contracts_mailbox_message USING btree (history_case_id, received_at);

GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.admin_user TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.admin_session TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.admin_setting TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.admin_audit_log TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.form_request TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.form_workflow_case TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.form_workflow_device TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.workflow_sheet_status_cache TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.device_intake_operation TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.device_inventory_unit TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.device_inventory_event TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.device_counter_reading TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.device_manual_reservation TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.device_sheet_outbox TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.device_audit_run TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.device_audit_item TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.shipping_address TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.shipping_consumable_compatibility TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.shipping_case TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.shipping_item TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.shipping_day_close TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.shipping_shipment TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.shipping_event TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.contracts_mailbox_history_case TO appuser;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE ctip.contracts_mailbox_message TO appuser;
GRANT ALL ON SEQUENCE ctip.admin_user_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.admin_session_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.admin_audit_log_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.form_request_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.form_workflow_case_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.form_workflow_device_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.workflow_sheet_status_cache_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.device_intake_operation_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.device_inventory_unit_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.device_inventory_event_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.device_counter_reading_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.device_manual_reservation_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.device_sheet_outbox_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.device_audit_run_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.device_audit_item_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.contracts_mailbox_history_case_id_seq TO appuser;
GRANT ALL ON SEQUENCE ctip.contracts_mailbox_message_id_seq TO appuser;


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
