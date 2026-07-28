"""Testy wspólnego katalogu tożsamości klientów botów."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.models import (
    AdminAuditLog,
    AdminUser,
    BotDisclosureGrant,
    BotIdentityBinding,
    BotIdentityCustomer,
    BotIdentityDevice,
    BotIdentityOverride,
    BotIdentityPhone,
    BotIdentityResolution,
    BotIdentitySmsChallenge,
    BotIdentitySubject,
    BotIdentitySyncRun,
)
from app.models.base import Base
from app.schemas.bot_identity import PromoteSmsBindingRequest
from app.services.bot_identity_crypto import BotIdentityCrypto
from app.services.bot_identity_directory import (
    DEVICES_SQL,
    MOBILE_ACCOUNTS_SQL,
    _deactivate_missing_snapshot_rows,
    _read_firebird_snapshot,
    _upsert_snapshot,
    authenticate_service_token,
    confirm_current,
    create_chat_sms_challenge,
    create_test_sms_challenge,
    disclose_devices,
    list_chat_masked_devices,
    list_duplicate_groups,
    promote_sms_binding,
    resolve_chat_customer,
    resolve_phone,
    set_override,
    verify_chat_sms_challenge,
    verify_resolution_nip,
)
from app.services.bot_identity_images import safe_device_image_url


class BotIdentityDirectoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            execution_options={"schema_translate_map": {"ctip": None}},
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(
                Base.metadata.create_all,
                tables=[
                    AdminUser.__table__,
                    AdminAuditLog.__table__,
                    BotIdentityCustomer.__table__,
                    BotIdentitySubject.__table__,
                    BotIdentitySyncRun.__table__,
                    BotIdentityPhone.__table__,
                    BotIdentityBinding.__table__,
                    BotIdentityDevice.__table__,
                    BotIdentityOverride.__table__,
                    BotIdentityResolution.__table__,
                    BotDisclosureGrant.__table__,
                    BotIdentitySmsChallenge.__table__,
                ],
            )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.previous = {
            "enabled": settings.bot_identity_enabled,
            "secret": settings.bot_identity_secret_key,
            "warn": settings.bot_identity_warn_after_seconds,
            "block": settings.bot_identity_block_after_seconds,
            "voice_token": settings.bot_identity_voice_token,
            "chat_token": settings.bot_identity_chat_token,
        }
        settings.bot_identity_enabled = True
        settings.bot_identity_secret_key = Fernet.generate_key().decode("ascii")
        settings.bot_identity_warn_after_seconds = 900
        settings.bot_identity_block_after_seconds = 3600
        settings.bot_identity_voice_token = "voice-test-token"
        settings.bot_identity_chat_token = "chat-test-token"

    async def asyncTearDown(self) -> None:
        settings.bot_identity_enabled = self.previous["enabled"]
        settings.bot_identity_secret_key = self.previous["secret"]
        settings.bot_identity_warn_after_seconds = self.previous["warn"]
        settings.bot_identity_block_after_seconds = self.previous["block"]
        settings.bot_identity_voice_token = self.previous["voice_token"]
        settings.bot_identity_chat_token = self.previous["chat_token"]
        await self.engine.dispose()

    async def _seed(
        self,
        *,
        phone: str = "600100200",
        external_ref: str = "5198",
        customer_ref: str = "739",
        person: str = "Jan Testowy",
        company: str = "TEST SP. Z O.O.",
    ) -> None:
        crypto = BotIdentityCrypto()
        now = datetime.now(UTC)
        async with self.sessions() as session:
            run = await session.get(BotIdentitySyncRun, "sync-ok")
            if run is None:
                run = BotIdentitySyncRun(
                    id="sync-ok",
                    source_revision=settings.bot_identity_source_revision,
                    status="completed",
                    accounts_seen=1,
                    customers_seen=1,
                    devices_seen=1,
                    ended_at=now,
                )
                session.add(run)
            customer = BotIdentityCustomer(
                customer_ref=customer_ref,
                company_name=company,
                active=True,
                last_seen_sync_id=run.id,
            )
            subject = BotIdentitySubject(
                source="firebird_mobile_account",
                external_ref=external_ref,
                display_name=person,
                active=True,
                last_seen_sync_id=run.id,
            )
            session.add_all([customer, subject])
            await session.flush()
            session.add_all(
                [
                    BotIdentityPhone(
                        subject_id=subject.id,
                        phone_enc=crypto.encrypt(phone),
                        phone_hmac=crypto.phone_hmac(phone),
                        phone_last4=phone[-4:],
                        active=True,
                        last_seen_sync_id=run.id,
                    ),
                    BotIdentityBinding(
                        subject_id=subject.id,
                        customer_id=customer.id,
                        source="firebird_mobile_account",
                        trust_state="trusted",
                        active=True,
                        last_seen_sync_id=run.id,
                    ),
                    BotIdentityDevice(
                        customer_id=customer.id,
                        external_ref="device-1",
                        device_ref=(
                            "device-ref-1"
                            if customer_ref == "739"
                            else f"device-ref-{customer_ref}"
                        ),
                        producer="Ricoh",
                        model="IM C3000",
                        serial_enc=crypto.encrypt("SERIAL-123456"),
                        serial_last4="3456",
                        image_url="https://ksero-partner.com.pl/imgdev/ran_ricoh_im_c3000.png",
                        location="Sekretariat",
                        active=True,
                        last_seen_sync_id=run.id,
                    ),
                ]
            )
            await session.commit()

    async def test_exact_identity_requires_current_confirmation_for_full_serial(
        self,
    ) -> None:
        await self._seed()
        async with self.sessions() as session:
            resolved = await resolve_phone(
                session,
                channel="voice",
                conversation_ref="call-1",
                phone="+48 600 100 200",
            )
            self.assertEqual(resolved.status, "exact")
            self.assertEqual(resolved.display_name, "Jan Testowy")
            self.assertEqual(resolved.company_name, "TEST SP. Z O.O.")

            confirmed = await confirm_current(
                session,
                resolution_ref=resolved.resolution_ref,
                conversation_ref="call-1",
                confirmed=True,
            )
            self.assertTrue(confirmed.confirmed)
            devices = await disclose_devices(
                session,
                customer_ref="739",
                disclosure_grant=confirmed.disclosure_grant or "",
                channel="voice",
                conversation_ref="call-1",
            )
            self.assertEqual(devices.devices[0].serial, "SERIAL-123456")
            self.assertEqual(devices.devices[0].serial_last4, "3456")
            self.assertEqual(devices.devices[0].producer, "Ricoh")
            self.assertEqual(
                devices.devices[0].image_url,
                "https://ksero-partner.com.pl/imgdev/ran_ricoh_im_c3000.png",
            )
            self.assertEqual(devices.disclosure_level, "full_serial")
            with self.assertRaises(PermissionError):
                await disclose_devices(
                    session,
                    customer_ref="739",
                    disclosure_grant=confirmed.disclosure_grant or "",
                    channel="voice",
                    conversation_ref="call-1",
                )

    async def test_non_mobile_phone_requires_correct_nip_before_confirmation(self) -> None:
        crypto = BotIdentityCrypto()
        now = datetime.now(UTC)
        async with self.sessions() as session:
            session.add(
                BotIdentitySyncRun(
                    id="sync-contact",
                    source_revision=settings.bot_identity_source_revision,
                    status="completed",
                    ended_at=now,
                )
            )
            customer = BotIdentityCustomer(
                customer_ref="800",
                company_name="Firma z NIP",
                nip_enc=crypto.encrypt("1234567890"),
                nip_hmac=crypto.nip_hmac("1234567890"),
                active=True,
                last_seen_sync_id="sync-contact",
            )
            subject = BotIdentitySubject(
                source="firebird_contact",
                external_ref="contact-800",
                display_name="Kontakt Testowy",
                active=True,
                last_seen_sync_id="sync-contact",
            )
            session.add_all([customer, subject])
            await session.flush()
            session.add_all(
                [
                    BotIdentityPhone(
                        subject_id=subject.id,
                        phone_enc=crypto.encrypt("600300400"),
                        phone_hmac=crypto.phone_hmac("600300400"),
                        phone_last4="0400",
                        active=True,
                        last_seen_sync_id="sync-contact",
                    ),
                    BotIdentityBinding(
                        subject_id=subject.id,
                        customer_id=customer.id,
                        source="firebird_contact",
                        trust_state="self_declared",
                        active=True,
                        last_seen_sync_id="sync-contact",
                    ),
                ]
            )
            await session.commit()

            resolved = await resolve_phone(
                session,
                channel="chat",
                conversation_ref="chat-nip",
                phone="600300400",
            )
            self.assertTrue(resolved.requires_nip_verification)
            before_nip = await confirm_current(
                session,
                resolution_ref=resolved.resolution_ref,
                conversation_ref="chat-nip",
                confirmed=True,
            )
            self.assertEqual(before_nip.status, "nip_required")
            invalid = await verify_resolution_nip(
                session,
                resolution_ref=resolved.resolution_ref,
                conversation_ref="chat-nip",
                nip="0000000000",
            )
            self.assertEqual(invalid.status, "invalid")
            verified = await verify_resolution_nip(
                session,
                resolution_ref=resolved.resolution_ref,
                conversation_ref="chat-nip",
                nip="123-456-78-90",
            )
            self.assertTrue(verified.verified)
            confirmed = await confirm_current(
                session,
                resolution_ref=resolved.resolution_ref,
                conversation_ref="chat-nip",
                confirmed=True,
            )
            self.assertTrue(confirmed.confirmed)

    async def test_duplicate_is_ambiguous_until_operator_selects_exact_record(self) -> None:
        await self._seed()
        await self._seed(
            external_ref="5199",
            customer_ref="740",
            person="Anna Testowa",
            company="INNA SP. Z O.O.",
        )
        async with self.sessions() as session:
            ambiguous = await resolve_phone(
                session,
                channel="chat",
                conversation_ref="chat-1",
                phone="600100200",
            )
            self.assertEqual(ambiguous.status, "ambiguous")
            groups = await list_duplicate_groups(session)
            self.assertEqual(len(groups), 1)
            target = groups[0].candidates[0]
            await set_override(
                session,
                phone_ref=groups[0].phone_ref,
                subject_id=target.subject_id,
                binding_id=target.binding_id,
                reason="Potwierdzone przez operatora",
                user_id=1,
            )
            exact = await resolve_phone(
                session,
                channel="chat",
                conversation_ref="chat-2",
                phone="600100200",
            )
            self.assertEqual(exact.status, "exact")
            self.assertEqual(exact.customer_ref, target.customer_ref)

    async def test_operator_accepted_sms_becomes_trusted_company_binding(self) -> None:
        async with self.sessions() as session:
            session.add(
                BotIdentitySyncRun(
                    id="sync-sms",
                    source_revision=settings.bot_identity_source_revision,
                    status="completed",
                    ended_at=datetime.now(UTC),
                )
            )
            binding = await promote_sms_binding(
                session,
                PromoteSmsBindingRequest(
                    phone="600200300",
                    customer_ref="customer-42",
                    company_name="Firma SMS",
                    case_ref="case-42",
                ),
            )
            await session.commit()
            self.assertEqual(binding.trust_state, "operator_approved")
            resolved = await resolve_phone(
                session,
                channel="voice",
                conversation_ref="call-sms",
                phone="600200300",
            )
            self.assertEqual(resolved.status, "exact")
            self.assertIsNone(resolved.display_name)
            self.assertEqual(resolved.company_name, "Firma SMS")
            customer = await session.get(BotIdentityCustomer, binding.customer_id)
            session.add(
                BotIdentityDevice(
                    customer_id=customer.id,
                    external_ref="sms-device",
                    producer="Ricoh",
                    model="M C320FW",
                    serial_enc=BotIdentityCrypto().encrypt("SMS-SERIAL-9988"),
                    serial_last4="9988",
                    active=True,
                    last_seen_sync_id="sync-sms",
                )
            )
            confirmed = await confirm_current(
                session,
                resolution_ref=resolved.resolution_ref,
                conversation_ref="call-sms",
                confirmed=True,
            )
            self.assertEqual(confirmed.disclosure_level, "masked")
            devices = await disclose_devices(
                session,
                customer_ref="customer-42",
                disclosure_grant=confirmed.disclosure_grant or "",
                channel="voice",
                conversation_ref="call-sms",
            )
            self.assertEqual(devices.disclosure_level, "masked")
            self.assertIsNone(devices.devices[0].serial)
            self.assertEqual(devices.devices[0].serial_last4, "9988")

    async def test_three_invalid_nip_attempts_block_resolution(self) -> None:
        crypto = BotIdentityCrypto()
        async with self.sessions() as session:
            session.add(
                BotIdentitySyncRun(
                    id="sync-block",
                    source_revision=settings.bot_identity_source_revision,
                    status="completed",
                    ended_at=datetime.now(UTC),
                )
            )
            customer = BotIdentityCustomer(
                customer_ref="900",
                company_name="Firma Blokada",
                nip_enc=crypto.encrypt("1234567890"),
                nip_hmac=crypto.nip_hmac("1234567890"),
                last_seen_sync_id="sync-block",
            )
            subject = BotIdentitySubject(
                source="firebird_contact",
                external_ref="contact-900",
                last_seen_sync_id="sync-block",
            )
            session.add_all([customer, subject])
            await session.flush()
            session.add_all(
                [
                    BotIdentityPhone(
                        subject_id=subject.id,
                        phone_enc=crypto.encrypt("600900900"),
                        phone_hmac=crypto.phone_hmac("600900900"),
                        phone_last4="0900",
                        last_seen_sync_id="sync-block",
                    ),
                    BotIdentityBinding(
                        subject_id=subject.id,
                        customer_id=customer.id,
                        source="firebird_contact",
                        trust_state="self_declared",
                        last_seen_sync_id="sync-block",
                    ),
                ]
            )
            await session.commit()
            resolved = await resolve_phone(
                session,
                channel="chat",
                conversation_ref="chat-block",
                phone="600900900",
            )
            statuses = []
            for _ in range(3):
                result = await verify_resolution_nip(
                    session,
                    resolution_ref=resolved.resolution_ref,
                    conversation_ref="chat-block",
                    nip="0000000000",
                )
                statuses.append(result.status)
            self.assertEqual(statuses, ["invalid", "invalid", "blocked"])
            confirmed = await confirm_current(
                session,
                resolution_ref=resolved.resolution_ref,
                conversation_ref="chat-block",
                confirmed=True,
            )
            self.assertEqual(confirmed.status, "expired")

    def test_lock_user_value_is_never_selected(self) -> None:
        select_clause = MOBILE_ACCOUNTS_SQL.upper().split("FROM KONTAKT", maxsplit=1)[0]
        self.assertNotIn("LOCK_USER", select_clause)
        self.assertIn("C.LOCK_USER IS NOT NULL", MOBILE_ACCOUNTS_SQL.upper())

    def test_firebird_snapshot_uses_driver_read_only_transaction(self) -> None:
        class ExpectedConnect(Exception):
            pass

        connect_kwargs: dict[str, object] = {}

        def connect(**kwargs):
            connect_kwargs.update(kwargs)
            raise ExpectedConnect

        fake_firebirdsql = SimpleNamespace(
            ISOLATION_LEVEL_READ_COMMITED_RO=4,
            connect=connect,
        )
        previous_allow_writes = settings.fb_allow_writes
        settings.fb_allow_writes = False
        try:
            with patch.dict("sys.modules", {"firebirdsql": fake_firebirdsql}):
                with self.assertRaises(ExpectedConnect):
                    _read_firebird_snapshot()
        finally:
            settings.fb_allow_writes = previous_allow_writes

        self.assertEqual(connect_kwargs["isolation_level"], 4)

    async def test_next_snapshot_keeps_seen_device_active(self) -> None:
        crypto = BotIdentityCrypto()
        async with self.sessions() as session:
            previous_run = BotIdentitySyncRun(
                id="sync-device-old",
                source_revision=settings.bot_identity_source_revision,
                status="completed",
                ended_at=datetime.now(UTC),
            )
            current_run = BotIdentitySyncRun(
                id="sync-device-current",
                source_revision=settings.bot_identity_source_revision,
            )
            customer = BotIdentityCustomer(
                customer_ref="1000",
                company_name="Firma urządzenia",
                active=True,
                last_seen_sync_id=previous_run.id,
            )
            session.add_all([previous_run, current_run, customer])
            await session.flush()
            session.add(
                BotIdentityDevice(
                    customer_id=customer.id,
                    external_ref="device-repeated",
                    active=True,
                    last_seen_sync_id=previous_run.id,
                )
            )
            await session.commit()

            await _upsert_snapshot(
                session,
                current_run,
                [
                    {
                        "ID_KLIENT": "1000",
                        "FIRMA": "Firma urządzenia",
                        "AKTYWNY": "TAK",
                    }
                ],
                [],
                [{"ID_KONTAKT_TABLE": "contact-device"}],
                [
                    {
                        "ID_MASZYNA": "device-repeated",
                        "ID_KLIENT": "1000",
                        "PRODUCER": "Ricoh",
                        "DEVICE_MODEL": "IM C3000",
                        "IMAGE_SOURCE": "https://ksero-partner.com.pl/imgdev/model.png",
                        "SERIAL": "SERIAL-1000",
                        "AKTYWNA": "TAK",
                    }
                ],
                crypto,
            )
            self.assertEqual(current_run.accounts_seen, 1)
            await _deactivate_missing_snapshot_rows(session, current_run.id)
            await session.commit()

            device = await session.scalar(
                select(BotIdentityDevice).where(BotIdentityDevice.external_ref == "device-repeated")
            )
            self.assertIsNotNone(device)
            self.assertTrue(device.active)
            self.assertEqual(device.last_seen_sync_id, current_run.id)
            self.assertNotEqual(device.device_ref, device.external_ref)
            UUID(device.device_ref)
            self.assertEqual(
                device.image_url,
                "https://ksero-partner.com.pl/imgdev/model.png",
            )

    async def test_chat_contract_resolves_customer_and_returns_masked_devices(
        self,
    ) -> None:
        await self._seed()
        previous_lab = settings.crm_lab_mode
        previous_database = settings.pg_database
        previous_sms_mode = settings.sms_test_mode
        previous_test_code = settings.bot_identity_test_sms_code
        settings.crm_lab_mode = True
        settings.pg_database = "ctip_test"
        settings.sms_test_mode = True
        settings.bot_identity_test_sms_code = "123456"
        try:
            async with self.sessions() as session:
                crypto = BotIdentityCrypto()
                customer = await session.scalar(
                    select(BotIdentityCustomer).where(BotIdentityCustomer.customer_ref == "739")
                )
                self.assertIsNotNone(customer)
                customer.nip_enc = crypto.encrypt("1234567890")
                customer.nip_hmac = crypto.nip_hmac("1234567890")
                await session.flush()

                by_nip = await resolve_chat_customer(
                    session,
                    nip="123-456-78-90",
                    name=None,
                )
                self.assertEqual(by_nip.status, "exact")
                self.assertEqual(by_nip.customer_ref, "739")
                self.assertEqual(by_nip.company_name, "TEST SP. Z O.O.")

                by_name = await resolve_chat_customer(
                    session,
                    nip=None,
                    name="TEST SP. Z O.O.",
                )
                self.assertEqual(by_name.status, "unique")
                self.assertEqual(by_name.customer_ref, "739")
                self.assertEqual(by_name.company_name, "TEST SP. Z O.O.")
                self.assertEqual(
                    set(by_name.model_dump()),
                    {
                        "status",
                        "candidate_count",
                        "customer_ref",
                        "company_name",
                        "matched_by",
                    },
                )

                missing = await resolve_chat_customer(
                    session,
                    nip=None,
                    name="FIRMA, KTÓREJ NIE MA",
                )
                self.assertEqual(missing.status, "not_found")
                self.assertIsNone(missing.customer_ref)
                self.assertIsNone(missing.company_name)

                other_customer = BotIdentityCustomer(
                    customer_ref="740",
                    company_name="TEST SP. Z O.O.",
                    active=True,
                    last_seen_sync_id="sync-ok",
                )
                session.add(other_customer)
                await session.flush()
                ambiguous = await resolve_chat_customer(
                    session,
                    nip=None,
                    name="TEST SP. Z O.O.",
                )
                self.assertEqual(ambiguous.status, "ambiguous")
                self.assertIsNone(ambiguous.customer_ref)
                self.assertIsNone(ambiguous.company_name)

                challenge = await create_chat_sms_challenge(
                    session,
                    phone="600100200",
                    customer_ref="739",
                    idempotency_key="sms-chat-test",
                )
                with self.assertRaises(PermissionError):
                    await list_chat_masked_devices(
                        session,
                        customer_ref="739",
                        challenge_id=challenge.challenge_id,
                    )
                invalid = await verify_chat_sms_challenge(
                    session,
                    challenge_id=challenge.challenge_id,
                    code="654321",
                )
                self.assertEqual(invalid.status, "invalid_code")
                self.assertIsNone(invalid.customer_ref)
                with self.assertRaises(PermissionError):
                    await list_chat_masked_devices(
                        session,
                        customer_ref="739",
                        challenge_id=challenge.challenge_id,
                    )
                verified = await verify_chat_sms_challenge(
                    session,
                    challenge_id=challenge.challenge_id,
                    code="123456",
                )
                self.assertEqual(verified.status, "verified")
                self.assertEqual(verified.customer_ref, "739")
                self.assertEqual(
                    verified.verification_status,
                    "sms_verified_known",
                )
                session.add_all(
                    [
                        BotIdentityDevice(
                            customer_id=customer.id,
                            external_ref="inactive-device",
                            device_ref="inactive-device-ref",
                            producer="Ricoh",
                            model="M C320FW",
                            serial_enc=crypto.encrypt("INACTIVE-9999"),
                            serial_last4="9999",
                            active=False,
                            last_seen_sync_id="sync-ok",
                        ),
                        BotIdentityDevice(
                            customer_id=other_customer.id,
                            external_ref="other-device",
                            device_ref="other-device-ref",
                            producer="Ricoh",
                            model="IM C4500",
                            serial_enc=crypto.encrypt("FOREIGN-8888"),
                            serial_last4="8888",
                            active=True,
                            last_seen_sync_id="sync-ok",
                        ),
                    ]
                )
                await session.flush()

                devices = await list_chat_masked_devices(
                    session,
                    customer_ref="739",
                    challenge_id=challenge.challenge_id,
                )
                self.assertEqual(len(devices.devices), 1)
                disclosed = devices.devices[0]
                self.assertEqual(disclosed.device_ref, "device-ref-1")
                self.assertEqual(disclosed.producer, "Ricoh")
                self.assertEqual(disclosed.model, "IM C3000")
                self.assertEqual(disclosed.serial, "SERIAL-123456")
                self.assertEqual(disclosed.serial_last4, disclosed.serial[-4:])
                self.assertEqual(
                    disclosed.image_url,
                    "https://ksero-partner.com.pl/imgdev/ran_ricoh_im_c3000.png",
                )
                self.assertEqual(disclosed.location, "Sekretariat")
                self.assertEqual(
                    set(disclosed.model_dump()),
                    {
                        "device_ref",
                        "producer",
                        "model",
                        "serial",
                        "serial_last4",
                        "image_url",
                        "location",
                        "active",
                    },
                )
        finally:
            settings.crm_lab_mode = previous_lab
            settings.pg_database = previous_database
            settings.sms_test_mode = previous_sms_mode
            settings.bot_identity_test_sms_code = previous_test_code

    async def test_expired_sms_challenge_never_discloses_devices(self) -> None:
        await self._seed()
        previous_lab = settings.crm_lab_mode
        previous_database = settings.pg_database
        previous_sms_mode = settings.sms_test_mode
        previous_test_code = settings.bot_identity_test_sms_code
        settings.crm_lab_mode = True
        settings.pg_database = "ctip_test"
        settings.sms_test_mode = True
        settings.bot_identity_test_sms_code = "123456"
        try:
            async with self.sessions() as session:
                challenge = await create_chat_sms_challenge(
                    session,
                    phone="600100200",
                    customer_ref="739",
                    idempotency_key="sms-expired-test",
                )
                stored = await session.get(BotIdentitySmsChallenge, challenge.challenge_id)
                self.assertIsNotNone(stored)
                stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                expired = await verify_chat_sms_challenge(
                    session,
                    challenge_id=challenge.challenge_id,
                    code="123456",
                )
                self.assertEqual(expired.status, "expired")
                with self.assertRaises(PermissionError):
                    await list_chat_masked_devices(
                        session,
                        customer_ref="739",
                        challenge_id=challenge.challenge_id,
                    )
        finally:
            settings.crm_lab_mode = previous_lab
            settings.pg_database = previous_database
            settings.sms_test_mode = previous_sms_mode
            settings.bot_identity_test_sms_code = previous_test_code

    async def test_production_configuration_rejects_fixed_lab_sms_code(self) -> None:
        previous_lab = settings.crm_lab_mode
        previous_database = settings.pg_database
        previous_sms_mode = settings.sms_test_mode
        previous_test_code = settings.bot_identity_test_sms_code
        settings.crm_lab_mode = False
        settings.pg_database = "ctip"
        settings.sms_test_mode = False
        settings.bot_identity_test_sms_code = "123456"
        try:
            async with self.sessions() as session:
                with self.assertRaisesRegex(RuntimeError, "wyłącznie w LAB"):
                    await create_test_sms_challenge(
                        session,
                        channel="chat",
                        conversation_ref="production-check",
                        phone="600100200",
                    )
        finally:
            settings.crm_lab_mode = previous_lab
            settings.pg_database = previous_database
            settings.sms_test_mode = previous_sms_mode
            settings.bot_identity_test_sms_code = previous_test_code

    def test_firebird_device_query_uses_model_and_image_projection(self) -> None:
        normalized = " ".join(DEVICES_SQL.upper().split())
        self.assertIn("LEFT JOIN MODEL MI ON MI.ID_MODEL = M.ID_MODEL", normalized)
        self.assertIn(
            "LEFT JOIN MODEL MN ON MN.MARKA = M.MARKA AND MN.MODEL = M.MODEL",
            normalized,
        )
        self.assertIn("COALESCE(MI.MARKA, MN.MARKA, M.MARKA) AS PRODUCER", normalized)
        self.assertIn("COALESCE(MI.PLIK, MN.PLIK) AS IMAGE_SOURCE", normalized)

    async def test_device_projection_rejects_unsafe_images_and_keeps_missing_image(
        self,
    ) -> None:
        crypto = BotIdentityCrypto()
        async with self.sessions() as session:
            run = BotIdentitySyncRun(
                id="sync-images",
                source_revision=settings.bot_identity_source_revision,
            )
            session.add(run)
            await _upsert_snapshot(
                session,
                run,
                [
                    {
                        "ID_KLIENT": "image-customer",
                        "FIRMA": "Firma obrazów",
                        "AKTYWNY": "TAK",
                    }
                ],
                [],
                [],
                [
                    {
                        "ID_MASZYNA": "safe-image",
                        "ID_KLIENT": "image-customer",
                        "PRODUCER": "Ricoh",
                        "DEVICE_MODEL": "IM C3000",
                        "IMAGE_SOURCE": (
                            "https://ksero-partner.com.pl/imgdev/ran_ricoh_im_c3000.png"
                        ),
                        "SERIAL": "SAFE-1234",
                        "AKTYWNA": "TAK",
                    },
                    {
                        "ID_MASZYNA": "missing-image",
                        "ID_KLIENT": "image-customer",
                        "PRODUCER": "Ricoh",
                        "DEVICE_MODEL": "IM C3500",
                        "IMAGE_SOURCE": None,
                        "SERIAL": "MISSING-2345",
                        "AKTYWNA": "TAK",
                    },
                    {
                        "ID_MASZYNA": "unsafe-image",
                        "ID_KLIENT": "image-customer",
                        "PRODUCER": "Ricoh",
                        "DEVICE_MODEL": "IM C4500",
                        "IMAGE_SOURCE": "file:///C:/sekret/model.png",
                        "SERIAL": "UNSAFE-3456",
                        "AKTYWNA": "TAK",
                    },
                ],
                crypto,
            )
            await session.flush()
            rows = {
                row.external_ref: row
                for row in (
                    await session.scalars(
                        select(BotIdentityDevice).where(BotIdentityDevice.customer_id.is_not(None))
                    )
                ).all()
            }
            self.assertEqual(rows["safe-image"].producer, "Ricoh")
            self.assertEqual(rows["safe-image"].model, "IM C3000")
            self.assertEqual(
                rows["safe-image"].image_url,
                "https://ksero-partner.com.pl/imgdev/ran_ricoh_im_c3000.png",
            )
            self.assertIsNone(rows["missing-image"].image_url)
            self.assertIsNone(rows["unsafe-image"].image_url)

    def test_image_url_policy_rejects_paths_smb_file_and_secrets(self) -> None:
        previous_lab = settings.crm_lab_mode
        previous_database = settings.pg_database
        previous_sms_mode = settings.sms_test_mode
        settings.crm_lab_mode = True
        settings.pg_database = "ctip_test"
        settings.sms_test_mode = True
        try:
            self.assertEqual(
                safe_device_image_url("https://ksero-partner.com.pl/imgdev/ran_ricoh_im_c3000.png"),
                "https://ksero-partner.com.pl/imgdev/ran_ricoh_im_c3000.png",
            )
            self.assertEqual(
                safe_device_image_url("http://192.168.0.9:8790/static/model.png"),
                "http://192.168.0.9:8790/static/model.png",
            )
            for value in (
                r"C:\obrazy\model.png",
                r"\\serwer\obrazy\model.png",
                "file:///var/lib/images/model.png",
                "smb://serwer/obrazy/model.png",
                "https://example.org/model.png",
                "https://ksero-partner.com.pl/imgdev/model.png?token=sekret",
                "https://user:password@ksero-partner.com.pl/imgdev/model.png",
                "https://ksero-partner.com.pl/imgdev/../sekret.png",
                "https://ksero-partner.com.pl/imgdev/model z odstępem.png",
                (
                    "https://ksero-partner.com.pl/imgdev/model.png"
                    "https://ksero-partner.com.pl/imgdev/model.png"
                ),
            ):
                self.assertIsNone(safe_device_image_url(value), value)
        finally:
            settings.crm_lab_mode = previous_lab
            settings.pg_database = previous_database
            settings.sms_test_mode = previous_sms_mode

    def test_service_tokens_are_bound_to_distinct_channels(self) -> None:
        self.assertEqual(
            authenticate_service_token("Bearer voice-test-token"),
            "voice",
        )
        self.assertEqual(
            authenticate_service_token("Bearer chat-test-token"),
            "chat",
        )
        settings.bot_identity_chat_token = "voice-test-token"
        with self.assertRaises(PermissionError):
            authenticate_service_token("Bearer voice-test-token")
