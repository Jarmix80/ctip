#!/usr/bin/env python3
"""Smoke-test logowania web oraz dashboardu GENFORM/FLOW.

Skrypt:
1. Loguje sie przez /admin/auth/login.
2. Weryfikuje sesje przez /admin/auth/me.
3. Odczytuje dashboard GENFORM/FLOW dla scope:
   active, accepted, rejected, unfilled, ksero_partner.
4. Waliduje kluczowe reguly przyciskow:
   - summary tylko dla APPROVED_ORDER,
   - release_resources tylko dla REJECTED_GRENKE (bez zwolnienia zasobow i bez archiwizacji).
5. Opcjonalnie uruchamia mailbox-sync w trybie dry-run.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request

SCOPES = ("active", "accepted", "rejected", "unfilled", "ksero_partner")


@dataclass(slots=True)
class CheckError:
    """Pojedynczy blad walidacji smoke-testu."""

    code: str
    message: str
    context: dict[str, Any]


def _http_json(
    *,
    method: str,
    url: str,
    timeout: int,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Admin-Session"] = token
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type.lower():
                return response.status, json.loads(raw)
            return response.status, {"raw": raw}
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


def _read_env_file(path: Path) -> dict[str, str]:
    output: dict[str, str] = {}
    if not path.exists():
        return output
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = raw_line.split("=", 1)
        output[key.strip()] = value.strip()
    return output


def _resolve_password(args: argparse.Namespace) -> str | None:
    if args.password:
        return str(args.password)

    for env_name in args.password_env:
        value = os.getenv(env_name)
        if value:
            return value

    env_data = _read_env_file(Path(args.env_file))
    for env_name in args.password_env:
        if env_name in env_data and env_data[env_name]:
            return env_data[env_name]
    return None


def _resolve_email(args: argparse.Namespace) -> str | None:
    if args.email:
        return str(args.email).strip()
    for env_name in args.email_env:
        value = os.getenv(env_name)
        if value:
            return str(value).strip()
    env_data = _read_env_file(Path(args.env_file))
    for env_name in args.email_env:
        value = env_data.get(env_name)
        if value:
            return str(value).strip()
    return None


def _flow_value(item: dict[str, Any]) -> str:
    flow_status = item.get("flow_status")
    if isinstance(flow_status, dict):
        return str(flow_status.get("value") or "").strip().upper()
    return str(flow_status or "").strip().upper()


def _bool_action(item: dict[str, Any], key: str) -> bool:
    actions = item.get("available_actions")
    if not isinstance(actions, dict):
        return False
    return bool(actions.get(key))


def _validate_form_actions(item: dict[str, Any]) -> list[CheckError]:
    errors: list[CheckError] = []
    flow = _flow_value(item)
    workflow = item.get("workflow") if isinstance(item.get("workflow"), dict) else {}
    archive_state = item.get("archive_state") if isinstance(item.get("archive_state"), dict) else {}
    resources_released = bool(workflow.get("resources_released_at"))
    archive_bucket = archive_state.get("bucket")

    summary_actual = _bool_action(item, "summary")
    summary_expected = flow == "APPROVED_ORDER"
    if summary_actual != summary_expected:
        errors.append(
            CheckError(
                code="summary_rule_mismatch",
                message="Niespojna flaga przycisku summary.",
                context={
                    "form_id": item.get("id"),
                    "flow": flow,
                    "summary_actual": summary_actual,
                    "summary_expected": summary_expected,
                },
            )
        )

    release_actual = _bool_action(item, "release_resources")
    release_expected = flow == "REJECTED_GRENKE" and not resources_released and not archive_bucket
    if release_actual != release_expected:
        errors.append(
            CheckError(
                code="release_resources_rule_mismatch",
                message="Niespojna flaga przycisku release_resources.",
                context={
                    "form_id": item.get("id"),
                    "flow": flow,
                    "resources_released_at": workflow.get("resources_released_at"),
                    "archive_bucket": archive_bucket,
                    "release_actual": release_actual,
                    "release_expected": release_expected,
                },
            )
        )
    return errors


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    email = _resolve_email(args)
    password = _resolve_password(args)
    if not email:
        return 2, {
            "ok": False,
            "error": "Brak e-mail administratora (parametr --email lub zmienna).",
        }
    if not password:
        return 2, {
            "ok": False,
            "error": "Brak hasla administratora (parametr --password lub zmienna).",
        }

    base_url = args.base_url.rstrip("/")
    output: dict[str, Any] = {
        "ok": False,
        "base_url": base_url,
        "email": email,
        "scopes": {},
        "aggregate": {},
        "errors": [],
    }

    login_status, login_payload = _http_json(
        method="POST",
        url=f"{base_url}/admin/auth/login",
        payload={"email": email, "password": password, "remember_me": False},
        timeout=args.timeout,
    )
    output["login_status"] = login_status
    if login_status != 200 or not isinstance(login_payload, dict) or "token" not in login_payload:
        output["errors"].append(
            {
                "code": "login_failed",
                "message": "Logowanie do panelu administratora nie powiodlo sie.",
                "context": {"response": login_payload},
            }
        )
        return 2, output

    token = str(login_payload["token"])
    me_status, me_payload = _http_json(
        method="GET",
        url=f"{base_url}/admin/auth/me",
        timeout=args.timeout,
        token=token,
    )
    output["me_status"] = me_status
    output["me"] = me_payload
    if me_status != 200:
        output["errors"].append(
            {
                "code": "session_invalid",
                "message": "Token sesji nie dziala dla /admin/auth/me.",
                "context": {"response": me_payload},
            }
        )
        return 2, output

    forms_all: list[dict[str, Any]] = []
    for scope in SCOPES:
        query = parse.urlencode(
            {"forms_scope": "submitted", "include_devices": "false", "archive_scope": scope}
        )
        status_code, payload = _http_json(
            method="GET",
            url=f"{base_url}/admin/contracts/dashboard?{query}",
            timeout=args.timeout,
            token=token,
        )
        if status_code != 200 or not isinstance(payload, dict):
            output["scopes"][scope] = {"status": status_code, "error": payload}
            output["errors"].append(
                {
                    "code": "dashboard_failed",
                    "message": "Blad odczytu dashboardu.",
                    "context": {"scope": scope, "status": status_code, "response": payload},
                }
            )
            continue

        forms = payload.get("forms") if isinstance(payload.get("forms"), list) else []
        forms_all.extend(forms)
        output["scopes"][scope] = {
            "status": status_code,
            "forms_count": len(forms),
            "flow_values": sorted({_flow_value(item) for item in forms if _flow_value(item)}),
            "actions_true": {
                "summary": sum(1 for item in forms if _bool_action(item, "summary")),
                "release_resources": sum(
                    1 for item in forms if _bool_action(item, "release_resources")
                ),
                "archive": sum(1 for item in forms if _bool_action(item, "archive")),
                "extend_archive": sum(1 for item in forms if _bool_action(item, "extend_archive")),
            },
            "mailbox_sync": payload.get("mailbox_sync"),
        }

    if forms_all:
        for form in forms_all:
            form_errors = _validate_form_actions(form)
            output["errors"].extend(
                {
                    "code": item.code,
                    "message": item.message,
                    "context": item.context,
                }
                for item in form_errors
            )

    rejected_forms = [item for item in forms_all if _flow_value(item) == "REJECTED_GRENKE"]
    approved_forms = [item for item in forms_all if _flow_value(item) == "APPROVED_ORDER"]
    output["aggregate"] = {
        "forms_total": len(forms_all),
        "forms_rejected_grenke": len(rejected_forms),
        "forms_approved_order": len(approved_forms),
        "summary_actions": sum(1 for item in forms_all if _bool_action(item, "summary")),
        "release_resources_actions": sum(
            1 for item in forms_all if _bool_action(item, "release_resources")
        ),
    }

    if args.check_mailbox_dry_run:
        sync_status, sync_payload = _http_json(
            method="POST",
            url=f"{base_url}/admin/contracts/workflow/mailbox-sync",
            timeout=max(args.timeout, args.mailbox_timeout),
            token=token,
            payload={
                "limit": args.mailbox_limit,
                "folder": args.mailbox_folder,
                "dry_run": True,
                "reprocess": False,
                "timeout_seconds": args.mailbox_timeout,
            },
        )
        output["mailbox_dry_run"] = {"status": sync_status, "response": sync_payload}
        if sync_status not in (200, 502):
            output["errors"].append(
                {
                    "code": "mailbox_dry_run_failed",
                    "message": "Mailbox dry-run zwrocil nieoczekiwany kod HTTP.",
                    "context": {"status": sync_status},
                }
            )
        elif sync_status == 502 and args.strict_mailbox:
            output["errors"].append(
                {
                    "code": "mailbox_dry_run_502",
                    "message": "Mailbox dry-run zwrocil 502 (strict mode).",
                    "context": {"response": sync_payload},
                }
            )

    output["ok"] = len(output["errors"]) == 0
    return (0 if output["ok"] else 2), output


def build_parser() -> argparse.ArgumentParser:
    """Buduje parser argumentow CLI."""
    parser = argparse.ArgumentParser(
        description="Smoke-test logowania web i dashboardu GENFORM/FLOW."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Bazowy URL API.")
    parser.add_argument("--email", default=None, help="E-mail administratora.")
    parser.add_argument(
        "--email-env",
        nargs="+",
        default=["ADMIN_EMAIL", "WEB_ADMIN_EMAIL"],
        help="Lista nazw zmiennych srodowiskowych z adresem e-mail.",
    )
    parser.add_argument("--password", default=None, help="Haslo administratora.")
    parser.add_argument(
        "--password-env",
        nargs="+",
        default=["PASS_ADMIN_WEB", "pass_admin_web", "ADMIN_PASSWORD"],
        help="Lista nazw zmiennych srodowiskowych z haslem.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Plik .env uzywany jako fallback dla e-maila i hasla.",
    )
    parser.add_argument(
        "--check-mailbox-dry-run",
        action="store_true",
        help="Uruchom dodatkowo /admin/contracts/workflow/mailbox-sync w trybie dry-run.",
    )
    parser.add_argument(
        "--strict-mailbox",
        action="store_true",
        help="Traktuj HTTP 502 z mailbox dry-run jako blad testu.",
    )
    parser.add_argument("--mailbox-limit", type=int, default=5, help="Limit wiadomosci dry-run.")
    parser.add_argument("--mailbox-folder", default="INBOX", help="Folder IMAP do dry-run.")
    parser.add_argument(
        "--mailbox-timeout",
        type=int,
        default=120,
        help="Timeout wykonywania dry-run mailboxa (sekundy).",
    )
    parser.add_argument("--timeout", type=int, default=25, help="Timeout jednego requestu HTTP.")
    parser.add_argument("--out", default=None, help="Opcjonalny plik JSON z raportem.")
    return parser


def main() -> int:
    """Punkt startowy CLI."""
    parser = build_parser()
    args = parser.parse_args()
    exit_code, report = run(args)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
