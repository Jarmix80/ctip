--
-- PostgreSQL database dump
--

\restrict lif4SmCAKfVKXajdDKZOX52JfSX0CrYF7v5ScBA2aobPuUuYvoOxEnC8bvqEl8O

-- Dumped from database version 17.6
-- Dumped by pg_dump version 17.6

-- Started on 2025-10-11 00:19:03

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
-- Name: ctip; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA ctip;


--
-- TOC entry 221 (class 1259 OID 16407)
-- Name: call_events; Type: TABLE; Schema: ctip; Owner: -
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


--
-- TOC entry 220 (class 1259 OID 16406)
-- Name: call_events_id_seq; Type: SEQUENCE; Schema: ctip; Owner: -
--

CREATE SEQUENCE ctip.call_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 4868 (class 0 OID 0)
-- Dependencies: 220
-- Name: call_events_id_seq; Type: SEQUENCE OWNED BY; Schema: ctip; Owner: -
--

ALTER SEQUENCE ctip.call_events_id_seq OWNED BY ctip.call_events.id;


--
-- TOC entry 219 (class 1259 OID 16391)
-- Name: calls; Type: TABLE; Schema: ctip; Owner: -
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


--
-- TOC entry 218 (class 1259 OID 16390)
-- Name: calls_id_seq; Type: SEQUENCE; Schema: ctip; Owner: -
--

CREATE SEQUENCE ctip.calls_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 4871 (class 0 OID 0)
-- Dependencies: 218
-- Name: calls_id_seq; Type: SEQUENCE OWNED BY; Schema: ctip; Owner: -
--

ALTER SEQUENCE ctip.calls_id_seq OWNED BY ctip.calls.id;


--
-- TOC entry 226 (class 1259 OID 16494)
-- Name: contact; Type: TABLE; Schema: ctip; Owner: -
--

CREATE TABLE ctip.contact (
    id bigint NOT NULL,
    number text NOT NULL,
    ext text,
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


--
-- TOC entry 228 (class 1259 OID 16506)
-- Name: contact_device; Type: TABLE; Schema: ctip; Owner: -
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


--
-- TOC entry 227 (class 1259 OID 16505)
-- Name: contact_device_id_seq; Type: SEQUENCE; Schema: ctip; Owner: -
--

CREATE SEQUENCE ctip.contact_device_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 4873 (class 0 OID 0)
-- Dependencies: 227
-- Name: contact_device_id_seq; Type: SEQUENCE OWNED BY; Schema: ctip; Owner: -
--

ALTER SEQUENCE ctip.contact_device_id_seq OWNED BY ctip.contact_device.id;


--
-- TOC entry 225 (class 1259 OID 16493)
-- Name: contact_id_seq; Type: SEQUENCE; Schema: ctip; Owner: -
--

CREATE SEQUENCE ctip.contact_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 4874 (class 0 OID 0)
-- Dependencies: 225
-- Name: contact_id_seq; Type: SEQUENCE OWNED BY; Schema: ctip; Owner: -
--

ALTER SEQUENCE ctip.contact_id_seq OWNED BY ctip.contact.id;


--
-- TOC entry 224 (class 1259 OID 16444)
-- Name: ivr_map; Type: TABLE; Schema: ctip; Owner: -
--

CREATE TABLE ctip.ivr_map (
    digit smallint NOT NULL,
    ext text NOT NULL,
    sms_text text NOT NULL,
    enabled boolean DEFAULT true NOT NULL
);


--
-- TOC entry 223 (class 1259 OID 16426)
-- Name: sms_out; Type: TABLE; Schema: ctip; Owner: -
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


--
-- TOC entry 222 (class 1259 OID 16425)
-- Name: sms_out_id_seq; Type: SEQUENCE; Schema: ctip; Owner: -
--

CREATE SEQUENCE ctip.sms_out_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 4877 (class 0 OID 0)
-- Dependencies: 222
-- Name: sms_out_id_seq; Type: SEQUENCE OWNED BY; Schema: ctip; Owner: -
--

ALTER SEQUENCE ctip.sms_out_id_seq OWNED BY ctip.sms_out.id;


--
-- TOC entry 4672 (class 2604 OID 16410)
-- Name: call_events id; Type: DEFAULT; Schema: ctip; Owner: -
--

ALTER TABLE ONLY ctip.call_events ALTER COLUMN id SET DEFAULT nextval('ctip.call_events_id_seq'::regclass);


--
-- TOC entry 4670 (class 2604 OID 16394)
-- Name: calls id; Type: DEFAULT; Schema: ctip; Owner: -
--

ALTER TABLE ONLY ctip.calls ALTER COLUMN id SET DEFAULT nextval('ctip.calls_id_seq'::regclass);


--
-- TOC entry 4679 (class 2604 OID 16497)
-- Name: contact id; Type: DEFAULT; Schema: ctip; Owner: -
--

ALTER TABLE ONLY ctip.contact ALTER COLUMN id SET DEFAULT nextval('ctip.contact_id_seq'::regclass);


--
-- TOC entry 4683 (class 2604 OID 16509)
-- Name: contact_device id; Type: DEFAULT; Schema: ctip; Owner: -
--

ALTER TABLE ONLY ctip.contact_device ALTER COLUMN id SET DEFAULT nextval('ctip.contact_device_id_seq'::regclass);


--
-- TOC entry 4673 (class 2604 OID 16429)
-- Name: sms_out id; Type: DEFAULT; Schema: ctip; Owner: -
--

ALTER TABLE ONLY ctip.sms_out ALTER COLUMN id SET DEFAULT nextval('ctip.sms_out_id_seq'::regclass);


--
-- TOC entry 4695 (class 2606 OID 16414)
-- Name: call_events call_events_pkey; Type: CONSTRAINT; Schema: ctip; Owner: -
--

ALTER TABLE ONLY ctip.call_events
    ADD CONSTRAINT call_events_pkey PRIMARY KEY (id);


--
-- TOC entry 4688 (class 2606 OID 16401)
-- Name: calls calls_pkey; Type: CONSTRAINT; Schema: ctip; Owner: -
--

ALTER TABLE ONLY ctip.calls
    ADD CONSTRAINT calls_pkey PRIMARY KEY (id);


--
-- TOC entry 4712 (class 2606 OID 16514)
-- Name: contact_device contact_device_pkey; Type: CONSTRAINT; Schema: ctip; Owner: -
--

ALTER TABLE ONLY ctip.contact_device
    ADD CONSTRAINT contact_device_pkey PRIMARY KEY (id);


--
-- TOC entry 4709 (class 2606 OID 16504)
-- Name: contact contact_pkey; Type: CONSTRAINT; Schema: ctip; Owner: -
--

ALTER TABLE ONLY ctip.contact
    ADD CONSTRAINT contact_pkey PRIMARY KEY (id);


--
-- TOC entry 4707 (class 2606 OID 16451)
-- Name: ivr_map ivr_map_pkey; Type: CONSTRAINT; Schema: ctip; Owner: -
--

ALTER TABLE ONLY ctip.ivr_map
    ADD CONSTRAINT ivr_map_pkey PRIMARY KEY (digit, ext);


--
-- TOC entry 4703 (class 2606 OID 16436)
-- Name: sms_out sms_out_pkey; Type: CONSTRAINT; Schema: ctip; Owner: -
--

ALTER TABLE ONLY ctip.sms_out
    ADD CONSTRAINT sms_out_pkey PRIMARY KEY (id);


--
-- TOC entry 4696 (class 1259 OID 16491)
-- Name: idx_call_events_ts; Type: INDEX; Schema: ctip; Owner: -
--

CREATE INDEX idx_call_events_ts ON ctip.call_events USING btree (ts);


--
-- TOC entry 4689 (class 1259 OID 16404)
-- Name: idx_calls_answered_by; Type: INDEX; Schema: ctip; Owner: -
--

CREATE INDEX idx_calls_answered_by ON ctip.calls USING btree (answered_by);


--
-- TOC entry 4690 (class 1259 OID 16405)
-- Name: idx_calls_direction; Type: INDEX; Schema: ctip; Owner: -
--

CREATE INDEX idx_calls_direction ON ctip.calls USING btree (direction);


--
-- TOC entry 4691 (class 1259 OID 16403)
-- Name: idx_calls_ext; Type: INDEX; Schema: ctip; Owner: -
--

CREATE INDEX idx_calls_ext ON ctip.calls USING btree (ext);


--
-- TOC entry 4692 (class 1259 OID 16489)
-- Name: idx_calls_number; Type: INDEX; Schema: ctip; Owner: -
--

CREATE INDEX idx_calls_number ON ctip.calls USING btree (number);


--
-- TOC entry 4693 (class 1259 OID 16402)
-- Name: idx_calls_started_at; Type: INDEX; Schema: ctip; Owner: -
--

CREATE INDEX idx_calls_started_at ON ctip.calls USING btree (started_at);


--
-- TOC entry 4710 (class 1259 OID 16520)
-- Name: idx_contact_number; Type: INDEX; Schema: ctip; Owner: -
--

CREATE INDEX idx_contact_number ON ctip.contact USING btree (number);


--
-- TOC entry 4697 (class 1259 OID 16421)
-- Name: idx_events_call_id; Type: INDEX; Schema: ctip; Owner: -
--

CREATE INDEX idx_events_call_id ON ctip.call_events USING btree (call_id);


--
-- TOC entry 4698 (class 1259 OID 16420)
-- Name: idx_events_ts; Type: INDEX; Schema: ctip; Owner: -
--

CREATE INDEX idx_events_ts ON ctip.call_events USING btree (ts);


--
-- TOC entry 4705 (class 1259 OID 16452)
-- Name: idx_ivr_map_ext; Type: INDEX; Schema: ctip; Owner: -
--

CREATE INDEX idx_ivr_map_ext ON ctip.ivr_map USING btree (ext);


--
-- TOC entry 4699 (class 1259 OID 16522)
-- Name: idx_sms_out_created_by; Type: INDEX; Schema: ctip; Owner: -
--

CREATE INDEX idx_sms_out_created_by ON ctip.sms_out USING btree (created_by, created_at DESC);


--
-- TOC entry 4700 (class 1259 OID 16521)
-- Name: idx_sms_out_dest_created; Type: INDEX; Schema: ctip; Owner: -
--

CREATE INDEX idx_sms_out_dest_created ON ctip.sms_out USING btree (dest, created_at DESC);


--
-- TOC entry 4701 (class 1259 OID 16442)
-- Name: idx_sms_out_status; Type: INDEX; Schema: ctip; Owner: -
--

CREATE INDEX idx_sms_out_status ON ctip.sms_out USING btree (status);


--
-- TOC entry 4704 (class 1259 OID 16443)
-- Name: uq_sms_out_callid_ivr; Type: INDEX; Schema: ctip; Owner: -
--

CREATE UNIQUE INDEX uq_sms_out_callid_ivr ON ctip.sms_out USING btree (call_id) WHERE ((source)::text = 'ivr'::text);


--
-- TOC entry 4713 (class 2606 OID 16415)
-- Name: call_events call_events_call_id_fkey; Type: FK CONSTRAINT; Schema: ctip; Owner: -
--

ALTER TABLE ONLY ctip.call_events
    ADD CONSTRAINT call_events_call_id_fkey FOREIGN KEY (call_id) REFERENCES ctip.calls(id) ON DELETE CASCADE;


--
-- TOC entry 4715 (class 2606 OID 16515)
-- Name: contact_device contact_device_contact_id_fkey; Type: FK CONSTRAINT; Schema: ctip; Owner: -
--

ALTER TABLE ONLY ctip.contact_device
    ADD CONSTRAINT contact_device_contact_id_fkey FOREIGN KEY (contact_id) REFERENCES ctip.contact(id) ON DELETE CASCADE;


--
-- TOC entry 4714 (class 2606 OID 16437)
-- Name: sms_out sms_out_call_id_fkey; Type: FK CONSTRAINT; Schema: ctip; Owner: -
--

ALTER TABLE ONLY ctip.sms_out
    ADD CONSTRAINT sms_out_call_id_fkey FOREIGN KEY (call_id) REFERENCES ctip.calls(id);


--
-- TOC entry 4866 (class 0 OID 0)
-- Dependencies: 6
-- Name: SCHEMA ctip; Type: ACL; Schema: -; Owner: -
--

GRANT ALL ON SCHEMA ctip TO postgres;


--
-- TOC entry 4867 (class 0 OID 0)
-- Dependencies: 221
-- Name: TABLE call_events; Type: ACL; Schema: ctip; Owner: -
--

GRANT ALL ON TABLE ctip.call_events TO postgres;


--
-- TOC entry 4869 (class 0 OID 0)
-- Dependencies: 220
-- Name: SEQUENCE call_events_id_seq; Type: ACL; Schema: ctip; Owner: -
--

GRANT ALL ON SEQUENCE ctip.call_events_id_seq TO postgres;


--
-- TOC entry 4870 (class 0 OID 0)
-- Dependencies: 219
-- Name: TABLE calls; Type: ACL; Schema: ctip; Owner: -
--

GRANT ALL ON TABLE ctip.calls TO postgres;


--
-- TOC entry 4872 (class 0 OID 0)
-- Dependencies: 218
-- Name: SEQUENCE calls_id_seq; Type: ACL; Schema: ctip; Owner: -
--

GRANT ALL ON SEQUENCE ctip.calls_id_seq TO postgres;


--
-- TOC entry 4875 (class 0 OID 0)
-- Dependencies: 224
-- Name: TABLE ivr_map; Type: ACL; Schema: ctip; Owner: -
--

GRANT ALL ON TABLE ctip.ivr_map TO postgres;


--
-- TOC entry 4876 (class 0 OID 0)
-- Dependencies: 223
-- Name: TABLE sms_out; Type: ACL; Schema: ctip; Owner: -
--

GRANT ALL ON TABLE ctip.sms_out TO postgres;


--
-- TOC entry 4878 (class 0 OID 0)
-- Dependencies: 222
-- Name: SEQUENCE sms_out_id_seq; Type: ACL; Schema: ctip; Owner: -
--

GRANT ALL ON SEQUENCE ctip.sms_out_id_seq TO postgres;


--
-- TOC entry 2070 (class 826 OID 16423)
-- Name: DEFAULT PRIVILEGES FOR SEQUENCES; Type: DEFAULT ACL; Schema: ctip; Owner: -
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA ctip GRANT ALL ON SEQUENCES TO appuser;


--
-- TOC entry 2072 (class 826 OID 16488)
-- Name: DEFAULT PRIVILEGES FOR SEQUENCES; Type: DEFAULT ACL; Schema: ctip; Owner: -
--

ALTER DEFAULT PRIVILEGES FOR ROLE appuser IN SCHEMA ctip GRANT ALL ON SEQUENCES TO postgres;


--
-- TOC entry 2069 (class 826 OID 16422)
-- Name: DEFAULT PRIVILEGES FOR TABLES; Type: DEFAULT ACL; Schema: ctip; Owner: -
--

ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA ctip GRANT ALL ON TABLES TO appuser;


--
-- TOC entry 2071 (class 826 OID 16487)
-- Name: DEFAULT PRIVILEGES FOR TABLES; Type: DEFAULT ACL; Schema: ctip; Owner: -
--

ALTER DEFAULT PRIVILEGES FOR ROLE appuser IN SCHEMA ctip GRANT ALL ON TABLES TO postgres;


-- Completed on 2025-10-11 00:19:03

--
-- PostgreSQL database dump complete
--

\unrestrict lif4SmCAKfVKXajdDKZOX52JfSX0CrYF7v5ScBA2aobPuUuYvoOxEnC8bvqEl8O
