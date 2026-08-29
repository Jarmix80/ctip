"""Odczyt i kontrolowany zapis procesu wysyłki w Menadżerze Serwisu."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.firebird_runtime import firebird_connection, firebird_writes_enabled

DELIVERY_TYPE_ID = 8
DELIVERY_TYPE_NAME = "dowóz materiałów"
QUEUE_STATUSES = ("O", "ZR")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _number(value: Any) -> float:
    return float(value or 0)


def _vat_rate(value: Any) -> Decimal:
    normalized = str(value or "0").replace("%", "").replace(",", ".").strip()
    try:
        return Decimal(normalized or "0")
    except InvalidOperation as exc:
        raise RuntimeError(f"Niepoprawna stawka VAT w kartotece magazynowej: {value!r}.") from exc


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _dict_row(cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    names = [str(item[0]).strip().lower() for item in cursor.description]
    return {name: _json_value(value) for name, value in zip(names, row, strict=True)}


def validate_shipping_dictionary() -> dict[str, Any]:
    """Potwierdza mapowanie typu usługi przed wyświetleniem kolejki."""
    connection = firebird_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT NAZWA FROM TYP_US WHERE ID_TU = ?", (DELIVERY_TYPE_ID,))
        row = cursor.fetchone()
        actual_name = _text(row[0]) if row else None
        valid = bool(actual_name and actual_name.casefold() == DELIVERY_TYPE_NAME.casefold())
        return {
            "valid": valid,
            "type_id": DELIVERY_TYPE_ID,
            "expected_name": DELIVERY_TYPE_NAME,
            "actual_name": actual_name,
        }
    finally:
        cursor.close()
        connection.close()


def load_shipping_queue(*, days: int = 30, limit: int = 200) -> list[dict[str, Any]]:
    """Pobiera otwarte i nadane zlecenia dowozu materiałów z ostatniego okresu."""
    connection = firebird_connection()
    cursor = connection.cursor()
    try:
        date_from = date.today() - timedelta(days=int(days))
        cursor.execute(
            f"""
            SELECT FIRST {int(limit)}
                z.ID_ZLECENIE_TABLE AS ORDER_TABLE_ID,
                z.ID_ZLECENIE AS ORDER_ID,
                z.ROK AS ORDER_YEAR,
                z.DATA AS ORDER_DATE,
                z.STAN AS STATUS,
                z.RODZAJ_US AS ORDER_KIND,
                z.ID_KLIENT AS CLIENT_ID,
                z.ID_MASZYNA AS MACHINE_ID,
                z.NAZWA AS COMPANY_NAME,
                z.STOI AS LOCATION,
                z.MARKA AS DEVICE_BRAND,
                z.MODEL AS DEVICE_MODEL,
                z.SERIAL AS DEVICE_SERIAL,
                z.PROBLEM AS PROBLEM,
                z.TELEFON AS PHONE,
                z.E_MAIL AS EMAIL,
                z.PRZESYLKA AS TRACKING_NUMBER,
                z.DATA_PRZES AS SHIPPED_AT,
                m.ID_MODEL AS MODEL_ID
            FROM ZLECENIE z
            LEFT JOIN MASZYNA m
              ON m.ID_FIRMA = z.ID_FIRMA AND m.ID_MASZYNA = z.ID_MASZYNA
            WHERE z.TYP_US = ?
              AND z.STAN IN (?, ?)
              AND z.DATA >= ?
            ORDER BY z.DATA ASC, z.ID_ZLECENIE_TABLE ASC
            """,
            (DELIVERY_TYPE_ID, *QUEUE_STATUSES, date_from),
        )
        return [_dict_row(cursor, row) for row in cursor.fetchall()]
    finally:
        cursor.close()
        connection.close()


def load_shipping_order(order_table_id: int) -> dict[str, Any]:
    """Pobiera pełny zestaw źródeł adresu, kontaktu i urządzenia dla zlecenia."""
    connection = firebird_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT FIRST 1
                z.ID_ZLECENIE_TABLE AS ORDER_TABLE_ID,
                z.ID_ZLECENIE AS ORDER_ID,
                z.ROK AS ORDER_YEAR,
                z.DATA AS ORDER_DATE,
                z.STAN AS STATUS,
                z.RODZAJ_US AS ORDER_KIND,
                z.ID_FIRMA AS COMPANY_ID,
                z.ID_ODDZIAL AS BRANCH_ID,
                z.ID_KLIENT AS CLIENT_ID,
                z.ID_MASZYNA AS MACHINE_ID,
                z.NAZWA AS ORDER_COMPANY_NAME,
                z.ADRES AS ORDER_STREET,
                z.KOD AS ORDER_POSTAL_CODE,
                z.POCZTA AS ORDER_CITY,
                z.STOI AS ORDER_LOCATION,
                z.TELEFON AS ORDER_PHONE,
                z.E_MAIL AS ORDER_EMAIL,
                z.ZGLASZA AS CONTACT_NAME,
                z.MARKA AS DEVICE_BRAND,
                z.MODEL AS DEVICE_MODEL,
                z.SERIAL AS DEVICE_SERIAL,
                z.EWIDENCJA AS DEVICE_ASSET_NUMBER,
                z.PROBLEM AS PROBLEM,
                z.PRZESYLKA AS TRACKING_NUMBER,
                z.ID_RW AS RW_ID,
                k.NAZWA AS CLIENT_COMPANY_NAME,
                k.ADRES AS CLIENT_STREET,
                k.KOD AS CLIENT_POSTAL_CODE,
                k.POCZTA AS CLIENT_CITY,
                k.TELEFON AS CLIENT_PHONE,
                k.E_MAIL AS CLIENT_EMAIL,
                k.KONTAKT AS CLIENT_CONTACT_NAME,
                k.KOD_KRAJU AS CLIENT_COUNTRY_CODE,
                m.ID_MODEL AS MODEL_ID,
                m.STOI AS MACHINE_LOCATION,
                m.EMAIL AS MACHINE_EMAIL,
                m.MARKA AS MACHINE_BRAND,
                m.MODEL AS MACHINE_MODEL
            FROM ZLECENIE z
            LEFT JOIN KLIENT k
              ON k.ID_FIRMA = z.ID_FIRMA AND k.ID_KLIENT = z.ID_KLIENT
            LEFT JOIN MASZYNA m
              ON m.ID_FIRMA = z.ID_FIRMA AND m.ID_MASZYNA = z.ID_MASZYNA
            WHERE z.ID_ZLECENIE_TABLE = ? AND z.TYP_US = ?
            """,
            (int(order_table_id), DELIVERY_TYPE_ID),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError("Nie znaleziono zlecenia dowozu materiałów.")
        result = _dict_row(cursor, row)
        result["suggested_address"] = {
            "company_name": result.get("order_company_name") or result.get("client_company_name"),
            "contact_name": result.get("contact_name") or result.get("client_contact_name"),
            "street": result.get("order_street") or result.get("client_street"),
            "postal_code": result.get("order_postal_code") or result.get("client_postal_code"),
            "city": result.get("order_city") or result.get("client_city"),
            "country_code": "PL",
            "phone": result.get("order_phone") or result.get("client_phone"),
            "email": result.get("order_email")
            or result.get("machine_email")
            or result.get("client_email"),
            "source": (
                "location"
                if result.get("order_location") or result.get("machine_location")
                else "order_or_client"
            ),
            "location_text": result.get("order_location") or result.get("machine_location"),
        }
        return result
    finally:
        cursor.close()
        connection.close()


def load_toner_stock(*, warehouse_id: int) -> list[dict[str, Any]]:
    """Pobiera dostępne kartoteki tonerów z wybranego magazynu."""
    connection = firebird_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT
                ID_MAGAZYN_TABLE AS WAREHOUSE_ITEM_ID,
                ID_MAGAZYN AS WAREHOUSE_ID,
                RODZAJ AS ITEM_KIND,
                INDEKS AS ITEM_INDEX,
                NAZWA AS ITEM_NAME,
                JM AS UNIT,
                ILOSC AS STOCK_QUANTITY,
                IL_REZ AS RESERVED_QUANTITY,
                CENA_NETTO AS PRICE_NET,
                CENA_Z1 AS PURCHASE_PRICE_NET,
                VAT_STAWKA AS VAT_RATE,
                IDVAT AS VAT_ID,
                MARKA AS BRAND,
                MODEL AS MODEL
            FROM MAGAZYN
            WHERE ID_MAGAZYN = ?
              AND UPPER(COALESCE(NAZWA, '')) CONTAINING 'TONER'
            ORDER BY NAZWA, INDEKS
            """,
            (int(warehouse_id),),
        )
        result = []
        for row in cursor.fetchall():
            item = _dict_row(cursor, row)
            item["vat_rate"] = float(_vat_rate(item.get("vat_rate")))
            stock = _number(item.get("stock_quantity"))
            reserved = _number(item.get("reserved_quantity"))
            item["available_quantity"] = max(0.0, stock - reserved)
            result.append(item)
        return result
    finally:
        cursor.close()
        connection.close()


def write_shipment_to_order(
    *,
    order_table_id: int,
    tracking_number: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Dodaje brakujące pozycje i oznacza zlecenie jako zrealizowane (`ZR`)."""
    enabled, reason = firebird_writes_enabled()
    if not enabled:
        raise RuntimeError(reason or "Zapis do Firebird jest zablokowany.")
    connection = firebird_connection()
    cursor = connection.cursor()
    created_position_ids: list[int] = []
    try:
        cursor.execute(
            """
            SELECT ID_KLIENT, ID_MASZYNA, ID_ZLECENIE, ROK, STAN, PRZESYLKA
            FROM ZLECENIE WHERE ID_ZLECENIE_TABLE = ? AND TYP_US = ?
            """,
            (int(order_table_id), DELIVERY_TYPE_ID),
        )
        order = cursor.fetchone()
        if order is None:
            raise ValueError("Nie znaleziono zlecenia do aktualizacji.")
        client_id, machine_id, order_id, order_year, order_status, existing_tracking = order
        if _text(existing_tracking) and _text(existing_tracking) != tracking_number:
            raise RuntimeError("Zlecenie ma już inny numer przesyłki; wymagane uzgodnienie ręczne.")

        for item in items:
            warehouse_item_id = int(item["firebird_warehouse_item_id"])
            requested_quantity = Decimal(str(item["quantity"]))
            cursor.execute(
                """
                SELECT COALESCE(SUM(ILOSC), 0)
                FROM ZPOZYCJA
                WHERE ID_ZLECENIE = ? AND ROK = ? AND ID_MAGPOZ = ?
                """,
                (order_id, order_year, warehouse_item_id),
            )
            existing_quantity = Decimal(str(cursor.fetchone()[0] or 0))
            missing_quantity = requested_quantity - existing_quantity
            if missing_quantity <= 0:
                continue
            cursor.execute(
                """
                SELECT ID_MAGAZYN, RODZAJ, INDEKS, NAZWA, JM,
                       COALESCE(CENA_NETTO, 0), COALESCE(CENA_Z1, 0),
                       COALESCE(VAT_STAWKA, 23), COALESCE(IDVAT, 1),
                       COALESCE(ILOSC, 0) - COALESCE(IL_REZ, 0)
                FROM MAGAZYN WHERE ID_MAGAZYN_TABLE = ?
                """,
                (warehouse_item_id,),
            )
            warehouse = cursor.fetchone()
            if warehouse is None:
                raise RuntimeError(f"Brak kartoteki magazynowej {warehouse_item_id}.")
            (
                _warehouse_id,
                item_kind,
                item_index,
                item_name,
                unit,
                price_net,
                purchase_price,
                vat_rate,
                vat_id,
                available,
            ) = warehouse
            if Decimal(str(available or 0)) < missing_quantity:
                raise RuntimeError(f"Brak wystarczającego stanu dla pozycji {item_name}.")
            vat_rate_value = _vat_rate(vat_rate)
            net_value = Decimal(str(price_net or 0)) * missing_quantity
            purchase_value = Decimal(str(purchase_price or 0)) * missing_quantity
            vat_value = net_value * vat_rate_value / Decimal("100")
            gross_value = net_value + vat_value
            normalized_kind = _text(item_kind) or "2. Towar inny"
            part_flag = 0 if normalized_kind.startswith("1") else 1
            cursor.execute(
                """
                INSERT INTO ZPOZYCJA (
                    ID_KLIENT, ID_MASZYNA, ID_ZLECENIE, ID_MAGPOZ,
                    RODZAJ, INDEKS, NAZWA, JM, ILOSC,
                    CENA, WARTOSC, VAT, BRUTTO, CENA_Z, WARTOSC_Z,
                    STAWKA_VAT, IDVAT, CZESC, ROK
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING ID_ZPOZYCJA_TABLE
                """,
                (
                    client_id,
                    machine_id,
                    order_id,
                    warehouse_item_id,
                    normalized_kind,
                    item_index,
                    item_name,
                    _text(unit) or "szt.",
                    missing_quantity,
                    price_net,
                    net_value,
                    vat_value,
                    gross_value,
                    purchase_price,
                    purchase_value,
                    vat_rate_value,
                    vat_id,
                    part_flag,
                    order_year,
                ),
            )
            created_position_ids.append(int(cursor.fetchone()[0]))

        cursor.execute(
            """
            UPDATE ZLECENIE
            SET PRZESYLKA = ?, DATA_PRZES = CURRENT_DATE, STAN = 'ZR'
            WHERE ID_ZLECENIE_TABLE = ?
            """,
            (tracking_number, int(order_table_id)),
        )
        connection.commit()
        return {
            "status": "written",
            "previous_order_status": _text(order_status),
            "created_position_ids": created_position_ids,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def create_rw_and_close_order(
    *,
    order_table_id: int,
    warehouse_id: int,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Tworzy dokument rozchodu `ROK`, zmniejsza stan i zamyka zlecenie umowne."""
    enabled, reason = firebird_writes_enabled()
    if not enabled:
        raise RuntimeError(reason or "Zapis do Firebird jest zablokowany.")
    connection = firebird_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT ID_FIRMA, ID_ODDZIAL, ID_KLIENT, ID_MASZYNA, ID_ZLECENIE, ROK,
                   NAZWA, ADRES, KOD, POCZTA, NIP, RODZAJ_US, ID_RW
            FROM ZLECENIE WHERE ID_ZLECENIE_TABLE = ? AND TYP_US = ?
            """,
            (int(order_table_id), DELIVERY_TYPE_ID),
        )
        order = cursor.fetchone()
        if order is None:
            raise ValueError("Nie znaleziono zlecenia do zamknięcia.")
        (
            company_id,
            branch_id,
            client_id,
            machine_id,
            order_id,
            order_year,
            company_name,
            street,
            postal_code,
            city,
            nip,
            order_kind,
            existing_rw_id,
        ) = order
        if _text(order_kind or "").casefold() != "umowa":
            return {"status": "manual_billing", "rw_id": None, "rw_number": None}
        if existing_rw_id:
            cursor.execute(
                "SELECT NUMER FROM FAKTURA WHERE ID_FAKTURA_TABLE = ?",
                (int(existing_rw_id),),
            )
            row = cursor.fetchone()
            cursor.execute(
                "UPDATE ZLECENIE SET STAN = 'Z' WHERE ID_ZLECENIE_TABLE = ?",
                (int(order_table_id),),
            )
            connection.commit()
            return {
                "status": "already_exists",
                "rw_id": int(existing_rw_id),
                "rw_number": _text(row[0]) if row else None,
            }

        if not items:
            raise RuntimeError("Brak pozycji przesyłki do utworzenia dokumentu RW.")
        item_quantities: dict[int, Decimal] = {}
        for item in items:
            item_id = int(item["firebird_warehouse_item_id"])
            quantity = Decimal(str(item["quantity"]))
            if quantity <= 0:
                raise RuntimeError(f"Niepoprawna ilość pozycji {item_id} w przesyłce.")
            item_quantities[item_id] = item_quantities.get(item_id, Decimal("0")) + quantity
        item_ids = list(item_quantities)
        placeholders = ",".join("?" for _ in item_ids)
        cursor.execute(
            f"""
            SELECT ID_MAGAZYN_TABLE, RODZAJ, INDEKS, NAZWA, JM,
                   COALESCE(CENA_NETTO, 0), COALESCE(CENA_Z1, 0),
                   COALESCE(VAT_STAWKA, 23), COALESCE(IDVAT, 1),
                   COALESCE(ILOSC, 0) - COALESCE(IL_REZ, 0)
            FROM MAGAZYN
            WHERE ID_MAGAZYN = ? AND ID_MAGAZYN_TABLE IN ({placeholders})
            """,
            (int(warehouse_id), *[int(item_id) for item_id in item_ids]),
        )
        warehouse_by_id = {int(row[0]): row for row in cursor.fetchall()}
        if len(warehouse_by_id) != len(set(item_ids)):
            raise RuntimeError("Nie wszystkie pozycje przesyłki istnieją w magazynie wydającym.")

        cursor.execute(
            """
            INSERT INTO FAKTURA (
                ID_ODDZIAL, ID_FIRMA, ID_MAGAZYN, ID_KLIENT, ID_MASZYNA,
                NAZWA, ADRES, KOD, POCZTA, NIP, RODZAJ_DOK, NUMER,
                DATA_SPRZ, DATA_WYST, DATA_PLAT, DNI_PLAT, PLATNOSC,
                WYSTAWIL, SUMA_NETTO, SUMA_VAT, SUMA_BRUTTO, ZAPLACONO,
                DO_ZAPLATY, STAN
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ROK', ?,
                      CURRENT_DATE, CURRENT_DATE, CURRENT_DATE, 0, 'Rozchód',
                      'CTIP', 0, 0, 0, 0, 0, '')
            RETURNING ID_FAKTURA_TABLE, DOKUMENT
            """,
            (
                branch_id or 1,
                company_id,
                int(warehouse_id),
                client_id,
                machine_id,
                company_name,
                street,
                postal_code,
                city,
                nip,
                f"CTIP/{order_table_id}",
            ),
        )
        rw_id, document_number = cursor.fetchone()
        rw_number = f"{document_number}/{date.today().year}"
        cursor.execute(
            "UPDATE FAKTURA SET NUMER = ? WHERE ID_FAKTURA_TABLE = ?",
            (rw_number, rw_id),
        )

        total_net = Decimal("0")
        total_vat = Decimal("0")
        total_gross = Decimal("0")
        for item_id in item_ids:
            quantity = item_quantities[item_id]
            warehouse = warehouse_by_id[int(item_id)]
            (
                _item_id,
                item_kind,
                item_index,
                item_name,
                unit,
                price_net,
                purchase_price,
                vat_rate,
                vat_id,
                available,
            ) = warehouse
            if Decimal(str(available or 0)) < quantity:
                raise RuntimeError(f"Brak stanu magazynowego dla pozycji {item_name}.")
            vat_rate_value = _vat_rate(vat_rate)
            net_value = Decimal(str(price_net or 0)) * quantity
            purchase_value = Decimal(str(purchase_price or 0)) * quantity
            vat_value = net_value * vat_rate_value / Decimal("100")
            gross_value = net_value + vat_value
            cursor.execute(
                """
                INSERT INTO FPOZYCJA (
                    ID_FAKTURA, ID_FIRMA, ID_KLIENT, ID_MASZYNA,
                    ID_ZLECENIE, ROK_ZLECENIA, ID_MAGAZYN, ID_MAGPOZ,
                    RODZAJ_DOK, NUMER, DATA_SPRZ, RODZAJ, INDEKS, NAZWA,
                    CENA_NETTO, CENA_BRUTTO, CENA_Z, ILOSC, JM,
                    WARTOSC_NETTO, WARTOSC_Z, STAWKA_VAT, VAT, IDVAT,
                    WARTOSC_BRUTTO, POBRANO
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ROK', ?, CURRENT_DATE,
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    rw_id,
                    company_id,
                    client_id,
                    machine_id,
                    order_id,
                    order_year,
                    int(warehouse_id),
                    int(item_id),
                    rw_number,
                    _text(item_kind) or "2. Towar inny",
                    item_index,
                    item_name,
                    price_net,
                    Decimal(str(price_net or 0)) * (Decimal("1") + vat_rate_value / Decimal("100")),
                    purchase_price,
                    quantity,
                    _text(unit) or "szt.",
                    net_value,
                    purchase_value,
                    f"{vat_rate_value:g} %",
                    vat_value,
                    vat_id,
                    gross_value,
                ),
            )
            total_net += net_value
            total_vat += vat_value
            total_gross += gross_value

        cursor.execute(
            """
            UPDATE FAKTURA
            SET SUMA_NETTO = ?, SUMA_VAT = ?, SUMA_BRUTTO = ?, DO_ZAPLATY = ?
            WHERE ID_FAKTURA_TABLE = ?
            """,
            (total_net, total_vat, total_gross, total_gross, rw_id),
        )
        cursor.execute(
            """
            UPDATE ZLECENIE
            SET ID_RW = ?, ID_FAKTURA = NULL, STAN = 'Z'
            WHERE ID_ZLECENIE_TABLE = ?
            """,
            (rw_id, int(order_table_id)),
        )
        connection.commit()
        return {"status": "created", "rw_id": int(rw_id), "rw_number": rw_number}
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


__all__ = [
    "DELIVERY_TYPE_ID",
    "DELIVERY_TYPE_NAME",
    "create_rw_and_close_order",
    "load_shipping_order",
    "load_shipping_queue",
    "load_toner_stock",
    "validate_shipping_dictionary",
    "write_shipment_to_order",
]
