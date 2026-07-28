"""Testy trwałych spraw Centrum Obsługi i izolacji danych LAB."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.models import (
    AdminAuditLog,
    AdminUser,
    BotIdentityBinding,
    BotIdentityCustomer,
    BotIdentityDevice,
    BotIdentityPhone,
    BotIdentitySmsChallenge,
    BotIdentitySubject,
    BotIdentitySyncRun,
    CrmCase,
    CrmCaseEvent,
)
from app.models.base import Base
from app.schemas.crm import (
    ChatCaseCreateRequest,
    CrmCaseActionRequest,
    CrmCaseCreateRequest,
)
from app.services.crm_cases import (
    apply_case_action,
    create_case,
    create_chat_case,
    get_case,
    list_cases,
    purge_expired_cases,
    reset_lab_cases,
    serialize_chat_case,
)


class CrmCaseTests(unittest.IsolatedAsyncioTestCase):
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
                    BotIdentitySmsChallenge.__table__,
                    CrmCase.__table__,
                    CrmCaseEvent.__table__,
                ],
            )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.previous_retention = settings.crm_retention_days
        self.previous_identity_enabled = settings.bot_identity_enabled
        self.previous_identity_secret = settings.bot_identity_secret_key
        settings.crm_retention_days = 90
        settings.bot_identity_enabled = True
        settings.bot_identity_secret_key = Fernet.generate_key().decode("ascii")
        async with self.sessions() as session:
            now = datetime.now(UTC)
            session.add(
                AdminUser(
                    id=1,
                    first_name="Marcin",
                    last_name="Testowy",
                    email="marcin@example.test",
                    role="admin",
                    password_hash="test",
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

    async def asyncTearDown(self) -> None:
        settings.crm_retention_days = self.previous_retention
        settings.bot_identity_enabled = self.previous_identity_enabled
        settings.bot_identity_secret_key = self.previous_identity_secret
        await self.engine.dispose()

    @staticmethod
    def _payload(*, external_ref: str = "chat-case-1") -> CrmCaseCreateRequest:
        return CrmCaseCreateRequest(
            external_ref=external_ref,
            conversation_ref="conversation-1",
            channel="chat",
            category="service",
            priority="high",
            subject="Awaria urządzenia",
            message="Urządzenie wyświetla kod błędu i nie drukuje.",
            company_name="Firma Testowa",
            contact_name="Jan Testowy",
            phone="600100200",
            email="jan@example.com",
            is_lab=True,
        )

    async def test_case_creation_is_idempotent_and_retained_for_90_days(self) -> None:
        async with self.sessions() as session:
            first, first_created = await create_case(
                session,
                self._payload(),
                idempotency_key="same-request",
                service_channel="chat",
                declared_operator=None,
                force_lab=True,
            )
            second, second_created = await create_case(
                session,
                self._payload(),
                idempotency_key="same-request",
                service_channel="chat",
                declared_operator=None,
                force_lab=True,
            )
            self.assertTrue(first_created)
            self.assertFalse(second_created)
            self.assertEqual(first.id, second.id)
            self.assertEqual(first.queue, "service_it")
            self.assertTrue(first.is_lab)
            retention = first.retained_until - first.created_at
            self.assertEqual(retention.days, 90)
            self.assertEqual(len(first.events), 1)

    async def test_actions_use_declared_operator_and_never_write_external_systems(self) -> None:
        async with self.sessions() as session:
            item, _ = await create_case(
                session,
                self._payload(),
                idempotency_key=None,
                service_channel="chat",
                declared_operator=None,
                force_lab=True,
            )
            await apply_case_action(
                session,
                item,
                CrmCaseActionRequest(action="claim", declared_operator_id=1),
            )
            self.assertEqual(item.owner_name, "Marcin Testowy")
            self.assertEqual(item.status, "active")
            await apply_case_action(
                session,
                item,
                CrmCaseActionRequest(
                    action="transfer_ms",
                    declared_operator_id=1,
                ),
            )
            self.assertEqual(item.status, "transferred")
            self.assertTrue((item.ms_order_ref or "").startswith("LAB-"))
            self.assertEqual(item.events[-1].payload, {"firebird_write": False})

    async def test_lab_reset_deletes_only_lab_cases_and_keeps_audit_summary(self) -> None:
        async with self.sessions() as session:
            lab_case, _ = await create_case(
                session,
                self._payload(external_ref="lab-1"),
                idempotency_key=None,
                service_channel="chat",
                declared_operator=None,
                force_lab=True,
            )
            regular_case, _ = await create_case(
                session,
                self._payload(external_ref="regular-1"),
                idempotency_key=None,
                service_channel="chat",
                declared_operator=None,
                force_lab=False,
            )
            regular_case.is_lab = False
            await session.commit()

            deleted_cases, deleted_events = await reset_lab_cases(
                session,
                declared_operator_id=1,
                reason="Kontrolowany reset laboratorium po teście",
            )
            await session.commit()
            self.assertEqual(deleted_cases, 1)
            self.assertEqual(deleted_events, len(lab_case.events))
            self.assertIsNotNone(await get_case(session, regular_case.ref))
            self.assertIsNone(await get_case(session, lab_case.ref))
            audit = await session.get(AdminAuditLog, 1)
            self.assertIsNotNone(audit)
            self.assertEqual(audit.action, "crm_lab_reset")

    async def test_retention_purges_expired_case_but_list_keeps_current_case(self) -> None:
        async with self.sessions() as session:
            expired, _ = await create_case(
                session,
                self._payload(external_ref="expired"),
                idempotency_key=None,
                service_channel="chat",
                declared_operator=None,
                force_lab=True,
            )
            current, _ = await create_case(
                session,
                self._payload(external_ref="current"),
                idempotency_key=None,
                service_channel="chat",
                declared_operator=None,
                force_lab=True,
            )
            expired.retained_until = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

            deleted_cases, deleted_events = await purge_expired_cases(session)
            await session.commit()
            self.assertEqual(deleted_cases, 1)
            self.assertGreaterEqual(deleted_events, 1)
            listed = await list_cases(session, lab_only=True)
            self.assertEqual([item.ref for item in listed], [current.ref])

    async def test_chat_case_contract_is_idempotent_and_does_not_store_nip(self) -> None:
        payload = ChatCaseCreateRequest(
            source_channel="chat",
            source_system="chat_kp",
            conversation_ref="conversation-chat-contract",
            category="service",
            summary="Awaria urządzenia",
            description="Urządzenie nie drukuje.",
            customer_ref="739",
            customer_match_status="exact",
            customer_nip="1234567890",
            customer_name="Firma Testowa",
            contact_phone="600100200",
            customer_confirmed=True,
            privacy_notice_accepted=True,
            privacy_notice_version="2026-07",
            privacy_notice_checksum="a" * 64,
        )
        previous_lab = settings.crm_lab_mode
        settings.crm_lab_mode = True
        try:
            async with self.sessions() as session:
                first = await create_chat_case(
                    session,
                    payload,
                    idempotency_key="chat-idempotency",
                    service_channel="chat",
                )
                second = await create_chat_case(
                    session,
                    payload,
                    idempotency_key="chat-idempotency",
                    service_channel="chat",
                )
                response = serialize_chat_case(first)

                self.assertEqual(first.id, second.id)
                self.assertEqual(first.queue, "service_it")
                self.assertNotIn("customer_nip", first.source_payload or {})
                self.assertEqual(response.status, "queued")
                self.assertEqual(response.category, "service")
        finally:
            settings.crm_lab_mode = previous_lab

    async def test_chat_case_accepts_multiple_devices_of_verified_customer(self) -> None:
        now = datetime.now(UTC)
        async with self.sessions() as session:
            run = BotIdentitySyncRun(
                id="sync-multi-device",
                source_revision=settings.bot_identity_source_revision,
                status="completed",
                ended_at=now,
            )
            customer = BotIdentityCustomer(
                customer_ref="739",
                company_name="Firma Testowa",
                active=True,
                last_seen_sync_id=run.id,
            )
            session.add_all([run, customer])
            await session.flush()
            session.add_all(
                [
                    BotIdentityDevice(
                        customer_id=customer.id,
                        external_ref="firebird-device-1",
                        device_ref="device-ref-1",
                        active=True,
                        last_seen_sync_id=run.id,
                    ),
                    BotIdentityDevice(
                        customer_id=customer.id,
                        external_ref="firebird-device-2",
                        device_ref="device-ref-2",
                        active=True,
                        last_seen_sync_id=run.id,
                    ),
                    BotIdentitySmsChallenge(
                        ref="sms-multi-device",
                        phone_hmac="phone-hmac",
                        channel="chat",
                        conversation_ref="chat-kp:739",
                        code_hash="code-hash",
                        expires_at=now + timedelta(minutes=5),
                        verified_at=now,
                    ),
                ]
            )
            await session.commit()
            payload = ChatCaseCreateRequest(
                source_channel="chat",
                source_system="chat_kp",
                conversation_ref="conversation-multi-device",
                category="service",
                summary="Awaria dwóch urządzeń",
                customer_ref="739",
                customer_match_status="exact",
                customer_name="Firma Testowa",
                device_refs=["device-ref-1", "device-ref-2", "device-ref-1"],
                sms_challenge_id="sms-multi-device",
                phone_verification_status="sms_verified_known",
                customer_confirmed=True,
                privacy_notice_accepted=True,
                privacy_notice_version="2026-07",
                privacy_notice_checksum="b" * 64,
            )

            item = await create_chat_case(
                session,
                payload,
                idempotency_key="multi-device-case",
                service_channel="chat",
            )

            self.assertEqual(item.device_label, "device-ref-1")
            self.assertEqual(item.device_refs, ["device-ref-1", "device-ref-2"])

    async def test_chat_case_rejects_device_of_another_customer(self) -> None:
        now = datetime.now(UTC)
        async with self.sessions() as session:
            run = BotIdentitySyncRun(
                id="sync-foreign-device",
                source_revision=settings.bot_identity_source_revision,
                status="completed",
                ended_at=now,
            )
            customer = BotIdentityCustomer(
                customer_ref="739",
                company_name="Firma Testowa",
                active=True,
                last_seen_sync_id=run.id,
            )
            other_customer = BotIdentityCustomer(
                customer_ref="740",
                company_name="Inna Firma",
                active=True,
                last_seen_sync_id=run.id,
            )
            session.add_all([run, customer, other_customer])
            await session.flush()
            session.add_all(
                [
                    BotIdentityDevice(
                        customer_id=other_customer.id,
                        external_ref="foreign-firebird-device",
                        device_ref="foreign-device-ref",
                        active=True,
                        last_seen_sync_id=run.id,
                    ),
                    BotIdentitySmsChallenge(
                        ref="sms-foreign-device",
                        phone_hmac="phone-hmac",
                        channel="chat",
                        conversation_ref="chat-kp:739",
                        code_hash="code-hash",
                        expires_at=now + timedelta(minutes=5),
                        verified_at=now,
                    ),
                ]
            )
            await session.commit()
            payload = ChatCaseCreateRequest(
                source_channel="chat",
                source_system="chat_kp",
                conversation_ref="conversation-foreign-device",
                category="service",
                summary="Nieprawidłowe urządzenie",
                customer_ref="739",
                customer_match_status="exact",
                customer_name="Firma Testowa",
                device_refs=["foreign-device-ref"],
                sms_challenge_id="sms-foreign-device",
                phone_verification_status="sms_verified_known",
                customer_confirmed=True,
                privacy_notice_accepted=True,
                privacy_notice_version="2026-07",
                privacy_notice_checksum="c" * 64,
            )

            with self.assertRaisesRegex(ValueError, "nie należy do klienta"):
                await create_chat_case(
                    session,
                    payload,
                    idempotency_key="foreign-device-case",
                    service_channel="chat",
                )

    def test_chat_case_device_fields_are_compatible_and_unambiguous(self) -> None:
        common = {
            "source_channel": "chat",
            "source_system": "chat_kp",
            "conversation_ref": "conversation-validation",
            "category": "service",
            "summary": "Walidacja urządzenia",
            "customer_match_status": "exact",
            "customer_confirmed": True,
            "privacy_notice_accepted": True,
            "privacy_notice_version": "2026-07",
            "privacy_notice_checksum": "d" * 64,
        }
        compatible = ChatCaseCreateRequest(
            **common,
            device_ref="device-ref-1",
            device_refs=["device-ref-1", "device-ref-1"],
        )
        self.assertEqual(compatible.selected_device_refs(), ["device-ref-1"])
        with self.assertRaisesRegex(ValueError, "lista musi zawierać wyłącznie"):
            ChatCaseCreateRequest(
                **common,
                device_ref="device-ref-1",
                device_refs=["device-ref-1", "device-ref-2"],
            )
        with self.assertRaisesRegex(ValueError, "maksymalnie 20"):
            ChatCaseCreateRequest(
                **common,
                device_refs=[f"device-ref-{number}" for number in range(21)],
            )
