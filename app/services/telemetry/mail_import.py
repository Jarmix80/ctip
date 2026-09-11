"""Trwała klasyfikacja wiadomości i wznawialne przenoszenie przez UID MOVE."""

import copy
import hashlib
import logging
import re
from email import policy
from email.parser import BytesParser

from sqlalchemy import select, update

from app.models import telemetry as tables
from app.services.telemetry import sources
from app.services.telemetry.mail_parsing import parse_remote_message
from app.services.telemetry.store import TelemetryStore, utcnow

logger = logging.getLogger(__name__)


def mailbox_generation(mailbox, folder, *, writable):
    """Sprawdza generację UID po wybraniu konkretnego folderu."""
    status, _ = mailbox.select('"' + folder + '"', readonly=not writable)
    if status != "OK":
        raise ValueError("imap_select")
    values = mailbox.response("UIDVALIDITY")[1]
    if not values or not values[0]:
        raise ValueError("imap_uidvalidity")
    return values[0].decode()


def ensure_folders(mailbox, config):
    """Tworzy tylko dwa foldery wynikowe i wymaga atomowego przenoszenia UID."""
    status, data = mailbox.capability()
    if status != "OK" or b"MOVE" not in b" ".join(data).upper().split():
        raise ValueError("imap_move_unsupported")
    for folder in (config.mail_processed_folder, config.mail_rejected_folder):
        status, data = mailbox.list('""', '"' + folder + '"')
        if status != "OK":
            raise ValueError("imap_list")
        if not any(data):
            status, _ = mailbox.create('"' + folder + '"')
            if status != "OK":
                raise ValueError("imap_create")


def target_evidence(mailbox, delivery, config):
    """Potwierdza zaginioną odpowiedź MOVE przez identyczną treść w folderze docelowym."""
    generation = mailbox_generation(mailbox, delivery["target_folder"], writable=False)
    message_id = delivery.get("message_id") or ""
    if message_id and not any(char in message_id for char in '\r\n"\\'):
        status, data = mailbox.uid("search", None, "HEADER", "Message-ID", '"' + message_id + '"')
    else:
        status, data = mailbox.uid("search", None, "ALL")
    if status != "OK":
        raise ValueError("imap_target_search")
    uids = [int(value) for value in data[0].split()]
    for offset in range(0, len(uids), config.page_size):
        bodies = sources.mail_batch(
            mailbox, uids[offset : offset + config.page_size], config.max_file_bytes
        )
        for uid, blob in bodies.items():
            if hashlib.sha256(blob).hexdigest() == delivery["sha256"]:
                return generation, uid
    raise ValueError("imap_move_unconfirmed")


def move_delivery(mailbox, delivery, config, known_blob=None):
    """Nigdy nie usuwa innych wiadomości i nie ufa UID z innej generacji."""
    generation = mailbox_generation(mailbox, delivery["folder"], writable=True)
    if generation != delivery["uidvalidity"]:
        raise ValueError("imap_uidvalidity_changed")
    status, data = mailbox.uid("search", None, "UID", str(delivery["uid"]))
    if status != "OK":
        raise ValueError("imap_source_search")
    present = delivery["uid"] in [int(value) for value in data[0].split()]
    if not present:
        return target_evidence(mailbox, delivery, config)
    blob = (
        known_blob
        if known_blob is not None
        else sources.mail_bytes(mailbox, delivery["uid"], config.max_file_bytes)
    )
    if hashlib.sha256(blob).hexdigest() != delivery["sha256"]:
        raise ValueError("imap_uid_content_changed")
    status, data = mailbox.uid("MOVE", str(delivery["uid"]), '"' + delivery["target_folder"] + '"')
    if status != "OK":
        raise ValueError("imap_move")
    response = mailbox.response("COPYUID")[1] or []
    combined = b" ".join(value for value in [*data, *response] if isinstance(value, bytes))
    matched = re.search(rb"(?:COPYUID\s+)?(\d+)\s+(\d+)\s+(\d+)", combined)
    if matched and int(matched[2]) == delivery["uid"]:
        return matched[1].decode(), int(matched[3])
    return target_evidence(mailbox, delivery, config)


def complete_move(runner, mailbox, source, delivery, blob=None):
    """Rejestruje próbę przed operacją sieciową, a wynik po potwierdzeniu przeniesienia."""
    with runner.engine.begin() as connection:
        connection.execute(
            update(tables.mail_delivery)
            .where(tables.mail_delivery.c.id == delivery["id"])
            .values(attempts=tables.mail_delivery.c.attempts + 1, updated_at=utcnow())
        )
    try:
        generation, uid = move_delivery(mailbox, delivery, runner.config, blob)
    except Exception as error:
        with runner.engine.begin() as connection:
            connection.execute(
                update(tables.mail_delivery)
                .where(tables.mail_delivery.c.id == delivery["id"])
                .values(last_error=type(error).__name__, updated_at=utcnow())
            )
        runner.failure(source, f"imap_move/{delivery['id']}", error)
        return False
    with runner.engine.begin() as connection:
        connection.execute(
            update(tables.mail_delivery)
            .where(tables.mail_delivery.c.id == delivery["id"])
            .values(
                move_status="moved",
                target_uidvalidity=generation,
                target_uid=uid,
                last_error=None,
                updated_at=utcnow(),
            )
        )
    summary = runner.summary[source["name"]]
    summary["moved"] = summary.get("moved", 0) + 1
    return True


def run_mail(runner):
    """Zapisuje dane tylko dopasowanych urządzeń; błędy pozostawia do ponowienia."""
    mapping = runner.get_active_mapping()
    source = runner.source("remote_mail", "imap")
    checkpoint = copy.deepcopy(source["checkpoint"])
    if runner.backfill:
        checkpoint = {}
    writable = runner.config.mail_move_enabled and not runner.dry_run
    with sources.remote_mailbox(runner.config, writable=writable) as mailbox:
        if writable:
            ensure_folders(mailbox, runner.config)
            with runner.engine.connect() as connection:
                query = (
                    select(tables.mail_delivery)
                    .where(
                        tables.mail_delivery.c.source_id == source["id"],
                        tables.mail_delivery.c.move_status == "pending",
                    )
                    .order_by(tables.mail_delivery.c.created_at)
                )
                if runner.item_limit is not None:
                    query = query.limit(runner.item_limit)
                pending = connection.execute(query).mappings().all()
            for delivery in pending:
                complete_move(runner, mailbox, source, dict(delivery))
        generation = mailbox_generation(mailbox, runner.config.imap_folder, writable=writable)
        generation, new_uids = sources.mail_uids(mailbox, checkpoint, generation)
        retry = checkpoint.get("retry", []) if checkpoint.get("uidvalidity") == generation else []
        selected = sorted(set(new_uids + retry))
        limit = runner.item_limit
        if limit is None and not runner.backfill and not runner.drain:
            limit = runner.config.page_size * runner.config.max_pages
        if limit is not None:
            selected = selected[:limit]
        for offset in range(0, len(selected), runner.config.page_size):
            if (
                mailbox_generation(mailbox, runner.config.imap_folder, writable=writable)
                != generation
            ):
                raise ValueError("imap_uidvalidity_changed")
            batch_uids = selected[offset : offset + runner.config.page_size]
            try:
                batch = sources.mail_batch(mailbox, batch_uids, runner.config.max_file_bytes)
            except Exception as error:
                retry = sorted(set(retry + batch_uids))
                runner.failure(source, "imap_batch", error)
                continue
            for uid in batch_uids:
                locator = f"{runner.config.imap_folder}/{generation}/{uid}/policy2"
                try:
                    blob = batch.pop(uid, None)
                    if blob is None:
                        raise ValueError("imap_message_missing_or_oversized")
                    digest = hashlib.sha256(blob).hexdigest()
                    delivery = None
                    if not runner.dry_run:
                        with runner.engine.connect() as connection:
                            delivery = (
                                connection.execute(
                                    select(tables.mail_delivery).where(
                                        tables.mail_delivery.c.source_id == source["id"],
                                        tables.mail_delivery.c.folder == runner.config.imap_folder,
                                        tables.mail_delivery.c.uidvalidity == generation,
                                        tables.mail_delivery.c.uid == uid,
                                    )
                                )
                                .mappings()
                                .first()
                            )
                    if delivery and delivery["sha256"] != digest:
                        raise ValueError("imap_uid_content_changed")
                    if delivery is None:
                        readings = parse_remote_message(blob, runner.config.mail_timezone)
                        accepted = [
                            reading
                            for reading in readings
                            if len(mapping.get(reading.serial, [])) == 1
                        ]
                        decision = "processed" if accepted else "rejected"
                        reason = "matched_active" if accepted else "no_unique_active_match"
                        header = BytesParser(policy=policy.default).parsebytes(
                            blob, headersonly=True
                        )
                        candidate = {
                            **checkpoint,
                            "uidvalidity": generation,
                            "uid": max(uid, int(checkpoint.get("uid", 0))),
                            "retry": [value for value in retry if value != uid],
                        }
                        if runner.dry_run:
                            result = {"new": len(accepted), "duplicates": 0}
                        else:
                            with runner.engine.begin() as connection:
                                store = TelemetryStore(connection)
                                result = store.ingest(
                                    source["id"],
                                    locator,
                                    accepted,
                                    blob=blob if accepted else None,
                                    media_type="message/rfc822",
                                    embedded=True,
                                )
                                identifier = store.add(
                                    tables.mail_delivery,
                                    source_id=source["id"],
                                    folder=runner.config.imap_folder,
                                    uidvalidity=generation,
                                    uid=uid,
                                    sha256=digest,
                                    message_id=str(header.get("Message-ID") or ""),
                                    artifact_id=result["marker"].get("artifact_id"),
                                    decision=decision,
                                    reason=reason,
                                    target_folder=(
                                        runner.config.mail_processed_folder
                                        if accepted
                                        else runner.config.mail_rejected_folder
                                    ),
                                    move_status="pending",
                                    attempts=0,
                                    created_at=utcnow(),
                                    updated_at=utcnow(),
                                )
                                store.checkpoint(source["id"], candidate)
                                delivery = dict(
                                    connection.execute(
                                        select(tables.mail_delivery).where(
                                            tables.mail_delivery.c.id == identifier
                                        )
                                    )
                                    .mappings()
                                    .one()
                                )
                            checkpoint = candidate
                        summary = runner.summary[source["name"]]
                        for key in ("new", "duplicates"):
                            summary[key] = summary.get(key, 0) + result[key]
                        summary[decision] = summary.get(decision, 0) + 1
                        summary["excluded_readings"] = (
                            summary.get("excluded_readings", 0) + len(readings) - len(accepted)
                        )
                    if writable and delivery["move_status"] != "moved":
                        complete_move(runner, mailbox, source, dict(delivery), blob)
                    retry = [value for value in retry if value != uid]
                    checkpoint.update(
                        uidvalidity=generation, uid=max(uid, int(checkpoint.get("uid", 0)))
                    )
                except Exception as error:
                    retry = sorted(set(retry + [uid]))
                    runner.failure(source, locator, error)
            checkpoint["retry"] = retry
            if not runner.dry_run:
                with runner.engine.begin() as connection:
                    TelemetryStore(connection).checkpoint(source["id"], checkpoint)
            logger.info(
                "Poczta Remote: sprawdzono=%s/%s do_ponowienia=%s",
                min(offset + len(batch_uids), len(selected)),
                len(selected),
                len(retry),
            )
