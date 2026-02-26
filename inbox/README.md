# Katalog wymiany plikow

Katalog `inbox/` sluzy do szybkiego wrzucania plikow z Windows na serwer Linux.

Zasady:
- pliki wrzucane do `inbox/` nie sa commitowane do Git (sa ignorowane przez `.gitignore`),
- skrypt Windows `scripts/windows/send_to_inbox.ps1` tworzy podkatalog z timestampem i kopiuje pliki przez `scp`,
- skrypt `scripts/inbox_contract_watcher.py` moze automatycznie odczytywac z PDF pola `NIP` i `NR UMOWY` (wynik: `*.pdf.parsed.json`),
- po przetworzeniu plikow warto je przeniesc lub usunac, aby utrzymac porzadek.

Przyklad struktury po wysylce:
- `inbox/2026-02-25_11-05-12/plik_a.pdf`
- `inbox/2026-02-25_11-05-12/zrzut_ekranu.png`
