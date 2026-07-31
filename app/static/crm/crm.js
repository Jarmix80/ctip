const CRM_TOKEN_KEY = "admin-session-token";
const CRM_AUTO_ARCHIVE_DAYS = 30;

const CRM_VIEW_CONFIG = {
  home: {
    title: "Dzień dobry",
    lead: "Najważniejsze sprawy i kolejki w jednym miejscu.",
    breadcrumb: "Centrum Obsługi / Strona główna",
  },
  inbox: {
    title: "Skrzynka wejściowa",
    lead: "Wszystkie otwarte kontakty oczekujące w Centrum Obsługi.",
    breadcrumb: "Centrum Obsługi / Skrzynka wejściowa",
  },
  my: {
    title: "Moje sprawy",
    lead: "Sprawy przejęte przez Ciebie i oczekujące na kolejny krok.",
    breadcrumb: "Centrum Obsługi / Moje sprawy",
  },
  sales: {
    title: "Handel",
    lead: "Wspólna kolejka nowych szans oraz spraw przejętych przez Michała i Kamila.",
    breadcrumb: "Centrum Obsługi / Kolejki / Handel",
  },
  service_it: {
    title: "Serwis + IT",
    lead: "Zgłoszenia techniczne z kontrolowanym przekazaniem do Menadżera Serwisu.",
    breadcrumb: "Centrum Obsługi / Kolejki / Serwis + IT",
  },
  contracts: {
    title: "Umowy i liczniki",
    lead: "Umowy, rozliczenia umowne oraz kontrola i aktualizacja liczników.",
    breadcrumb: "Centrum Obsługi / Kolejki / Umowy i liczniki",
  },
  other: {
    title: "Inne sprawy",
    lead: "Wiadomości wymagające kwalifikacji lub przekazania do właściwego działu.",
    breadcrumb: "Centrum Obsługi / Kolejki / Inne sprawy",
  },
  forms: {
    title: "Formularze WWW",
    lead: "Plan zastąpienia formularzy Bitrix własnymi kanałami Ksero Partner.",
    breadcrumb: "Centrum Obsługi / Narzędzia / Formularze WWW",
  },
  archive: {
    title: "Archiwum",
    lead: "Sprawy zakończone lub przekazane do systemów wykonawczych.",
    breadcrumb: "Centrum Obsługi / Archiwum",
  },
};

const CRM_DEPARTMENT_LABELS = {
  sales: "Handel",
  service_it: "Serwis + IT",
  contracts: "Umowy i liczniki",
  other: "Inne sprawy",
};

const CRM_SOURCE_LABELS = {
  form: "Formularz WWW",
  configurator: "Konfigurator WWW",
  chat: "Chat WWW",
  manual: "Wpis ręczny",
  scenario: "Scenariusz LAB",
  web_form: "Formularz WWW",
  web_chat: "Chat WWW",
  voice: "Voice 998",
  email: "E-mail",
  phone: "Telefon",
};

const CRM_SOURCE_ICONS = {
  form: "▤",
  configurator: "▦",
  chat: "◇",
  manual: "✎",
  scenario: "⚙",
  web_form: "▤",
  web_chat: "◇",
  voice: "◖",
  email: "✉",
  phone: "☎",
};

const CRM_STATUS_LABELS = {
  new: "Nowa",
  waiting: "Oczekuje",
  active: "W obsłudze",
  attention: "Do weryfikacji",
  transferred: "Przekazana do MS",
  done: "Zakończona",
  archived: "Archiwalna",
};

const CRM_SALES_KANBAN_COLUMNS = [
  {
    id: "new",
    title: "Nowe",
    description: "Wszystkie nieprzejęte sprawy handlowe",
    owner: null,
  },
  {
    id: "michal",
    title: "Obsługiwane — Michał",
    description: "Sprawy przejęte przez Michała",
    owner: "Michał",
  },
  {
    id: "kamil",
    title: "Obsługiwane — Kamil",
    description: "Sprawy przejęte przez Kamila",
    owner: "Kamil",
  },
];

const CRM_DEMO_USERS = [
  { id: 1, name: "Michał", role: "Handlowiec", phone: "+48 500 100 101", email: "michal@example.test" },
  { id: 2, name: "Kamil", role: "Handlowiec", phone: "+48 500 100 102", email: "kamil@example.test" },
  { id: 3, name: "Alicja Nowak", role: "Serwis + IT", phone: "+48 500 100 103", email: "alicja@example.test" },
  { id: 4, name: "Ewa Zielińska", role: "Księgowość", phone: "", email: "ewa@example.test" },
  { id: 5, name: "Marcin Jarmuszkiewicz", role: "Administrator", phone: "+48 500 100 105", email: "marcin@example.test" },
];

const CRM_DEMO_CASES = [
  {
    id: "KP-20260725-A7K9",
    department: "sales",
    source: "web_form",
    status: "new",
    priority: "high",
    createdAt: "2026-07-25T10:18:00+02:00",
    updatedAt: "2026-07-25T10:18:00+02:00",
    owner: null,
    company: "Nova Office sp. z o.o.",
    contact: "Anna K.",
    phone: "+48 500 000 142",
    email: "a.kowalska@example.test",
    subject: "Chcę wynająć urządzenie",
    message:
      "Proszę o kontakt w sprawie wynajmu urządzenia do biura dla około 25 użytkowników.",
    sourceDetail: "Produkt: RICOH IM C3000A",
    sourceUrl: "/produkty/ricoh-im-c3000a",
    intent: "Wynajem",
    identity: "Nowy kontakt",
    timeline: [
      {
        type: "form",
        title: "Formularz został wysłany",
        text: "Źródło zapisało produkt, cel „Wynajem” oraz adres podstrony.",
        time: "dzisiaj, 10:18",
      },
      {
        type: "mail",
        title: "Potwierdzenie e-mail",
        text: "Wiadomość oczekuje na wysłanie po przyjęciu przez Centrum.",
        time: "dzisiaj, 10:18",
      },
    ],
  },
  {
    id: "KP-20260725-B3M2",
    department: "service_it",
    source: "web_form",
    status: "attention",
    priority: "high",
    createdAt: "2026-07-25T09:42:00+02:00",
    updatedAt: "2026-07-25T09:50:00+02:00",
    owner: null,
    company: "Baltic Projekt S.A.",
    companyNip: "583-31-22-741",
    companyAddress: "ul. Portowa 18, 80-601 Gdańsk",
    contact: "Marek P.",
    phone: "+48 500 000 311",
    email: "marek.p@example.test",
    subject: "Urządzenie nie drukuje",
    message:
      "Urządzenie wyświetla błąd zacięcia, ale papieru nie ma w widocznych miejscach. Drukowanie jest niemożliwe.",
    sourceDetail: "RICOH IM C2500 / S/N …0862",
    sourceUrl: "/kontakt?temat=serwis",
    intent: "Zgłoszenie serwisowe",
    identity: "Telefon potwierdzony SMS",
    device: "RICOH IM C2500",
    serial: "3381P100862",
    msCustomer: "Dopasowanie wymaga potwierdzenia",
    attachments: 2,
    timeline: [
      {
        type: "sms",
        title: "Telefon potwierdzony kodem SMS",
        text: "Kod został potwierdzony w drugiej próbie.",
        time: "dzisiaj, 09:44",
      },
      {
        type: "identity",
        title: "Znaleziono możliwe powiązanie",
        text: "Firma i urządzenie wymagają zatwierdzenia przez pracownika.",
        time: "dzisiaj, 09:45",
      },
      {
        type: "file",
        title: "Dodano 2 zdjęcia",
        text: "Pliki są widoczne tylko w prototypie sprawy.",
        time: "dzisiaj, 09:50",
      },
    ],
  },
  {
    id: "KP-20260725-C8R4",
    department: "sales",
    source: "web_chat",
    status: "active",
    priority: "medium",
    createdAt: "2026-07-25T09:17:00+02:00",
    updatedAt: "2026-07-25T09:28:00+02:00",
    owner: "Michał",
    firstClaimedAt: "2026-07-25T09:28:00+02:00",
    company: "Orion Logistyka sp. z o.o.",
    contact: "Piotr S.",
    phone: "+48 500 000 527",
    email: "piotr.s@example.test",
    subject: "Dobór urządzenia do magazynu",
    message:
      "Klient potrzebuje drukarki A3 odpornej na intensywną pracę i pył w części magazynowej.",
    sourceDetail: "Chat WWW / konfigurator",
    sourceUrl: "/konfigurator",
    intent: "Dobór urządzenia",
    identity: "Istniejący klient",
    timeline: [
      {
        type: "chat",
        title: "Rozmowa rozpoczęta przez chat",
        text: "Bot zebrał liczbę użytkowników, format i przewidywany wolumen.",
        time: "dzisiaj, 09:17",
      },
      {
        type: "person",
        title: "Sprawa przejęta",
        text: "Opiekun przejął rozmowę i zapowiedział kontakt telefoniczny.",
        time: "dzisiaj, 09:28",
      },
    ],
  },
  {
    id: "KP-20260725-D2H7",
    department: "service_it",
    source: "phone",
    status: "waiting",
    priority: "medium",
    createdAt: "2026-07-25T08:56:00+02:00",
    updatedAt: "2026-07-25T09:02:00+02:00",
    owner: "Alicja Nowak",
    company: "Centrum Edukacji Delta",
    companyNip: "525-24-18-921",
    companyAddress: "ul. Szkolna 7, 00-375 Warszawa",
    contact: "Katarzyna D.",
    phone: "+48 500 000 684",
    email: "sekretariat@example.test",
    subject: "Smugi na wydrukach",
    message: "Na wydrukach kolorowych pojawiają się pionowe smugi po lewej stronie.",
    sourceDetail: "Rozmowa telefoniczna / wew. 998",
    sourceUrl: "",
    intent: "Zgłoszenie serwisowe",
    identity: "Klient rozpoznany",
    device: "Canon iR ADV C3525i",
    serial: "X4J000271",
    msCustomer: "ID klienta 4821",
    timeline: [
      {
        type: "voice",
        title: "Rozmowa przychodząca",
        text: "Numer rozpoznany, klient potwierdził firmę i urządzenie.",
        time: "dzisiaj, 08:56",
      },
      {
        type: "person",
        title: "Sprawa przypisana",
        text: "Oczekuje na zatwierdzenie przekazania do Menadżera Serwisu.",
        time: "dzisiaj, 09:02",
      },
    ],
  },
  {
    id: "KP-20260725-E9P1",
    department: "service_it",
    source: "email",
    status: "new",
    priority: "medium",
    createdAt: "2026-07-25T08:31:00+02:00",
    updatedAt: "2026-07-25T08:31:00+02:00",
    owner: null,
    company: "Studio Forma sp. z o.o.",
    contact: "Joanna M.",
    phone: "+48 500 000 733",
    email: "joanna.m@example.test",
    subject: "Problem ze skanowaniem do folderu",
    message:
      "Po zmianie serwera urządzenie nie zapisuje skanów do dotychczasowego folderu sieciowego.",
    sourceDetail: "E-mail do działu IT",
    sourceUrl: "",
    intent: "Pomoc IT",
    identity: "Istniejący klient",
    attachments: 1,
    timeline: [
      {
        type: "mail",
        title: "Odebrano wiadomość e-mail",
        text: "System rozpoznał temat jako bieżącą pomoc IT.",
        time: "dzisiaj, 08:31",
      },
    ],
  },
  {
    id: "KP-20260724-F6T5",
    department: "other",
    category: "accounting",
    source: "web_form",
    status: "active",
    priority: "low",
    createdAt: "2026-07-24T15:47:00+02:00",
    updatedAt: "2026-07-25T08:14:00+02:00",
    owner: "Ewa Zielińska",
    company: "Mediapoint sp. z o.o.",
    companyNip: "527-28-41-332",
    companyAddress: "ul. Jasna 14, 00-041 Warszawa",
    contact: "Tomasz L.",
    phone: "+48 500 000 816",
    email: "tomasz.l@example.test",
    subject: "Korekta danych na fakturze",
    message: "Proszę o poprawienie ulicy na fakturze 1147/KP/2026.",
    sourceDetail: "Kontakt / Księgowość i umowy",
    sourceUrl: "/kontakt?temat=rozliczenia",
    intent: "Dokument księgowy",
    identity: "Istniejący klient",
    attachments: 1,
    timeline: [
      {
        type: "form",
        title: "Formularz został wysłany",
        text: "Klient wskazał numer dokumentu i dodał kopię faktury.",
        time: "wczoraj, 15:47",
      },
      {
        type: "person",
        title: "Sprawa przejęta",
        text: "Księgowość rozpoczęła weryfikację danych.",
        time: "dzisiaj, 08:14",
      },
    ],
  },
  {
    id: "KP-20260724-G4N8",
    department: "sales",
    source: "web_form",
    status: "waiting",
    priority: "medium",
    createdAt: "2026-07-24T14:22:00+02:00",
    updatedAt: "2026-07-25T08:05:00+02:00",
    owner: "Kamil",
    firstClaimedAt: "2026-07-25T08:05:00+02:00",
    company: "Green Property Group",
    contact: "Natalia R.",
    phone: "+48 500 000 905",
    email: "natalia.r@example.test",
    subject: "Bezpłatna konsultacja po case study",
    message: "Chcemy sprawdzić, czy podobne oszczędności są możliwe w naszej grupie biur.",
    sourceDetail: "Artykuł: Jak obniżyć koszty druku",
    sourceUrl: "/baza-wiedzy/jak-obnizyc-koszty-druku",
    intent: "Konsultacja",
    identity: "Nowy kontakt",
    timeline: [
      {
        type: "form",
        title: "Konsultacja z artykułu",
        text: "Źródło zachowało tytuł case study i kampanię wejściową.",
        time: "wczoraj, 14:22",
      },
      {
        type: "phone",
        title: "Próba kontaktu",
        text: "Klient poprosił o ponowny telefon po godzinie 10:00.",
        time: "dzisiaj, 08:05",
      },
    ],
  },
  {
    id: "KP-20260724-H1W3",
    department: "sales",
    source: "web_form",
    status: "active",
    priority: "low",
    createdAt: "2026-07-24T12:08:00+02:00",
    updatedAt: "2026-07-24T12:44:00+02:00",
    owner: "Michał",
    firstClaimedAt: "2026-07-24T12:44:00+02:00",
    company: "Fenix Consulting",
    contact: "Robert B.",
    phone: "+48 500 001 024",
    email: "robert.b@example.test",
    subject: "Oferta stałej obsługi IT",
    message: "Proszę o ofertę opieki nad 18 stanowiskami i dwoma serwerami.",
    sourceDetail: "Obsługa IT / Chcę ofertę",
    sourceUrl: "/obsluga-it",
    intent: "Oferta IT",
    identity: "Nowy kontakt",
    timeline: [
      {
        type: "form",
        title: "Zapytanie ze strony IT",
        text: "System skierował ofertę do kolejki handlowej z etykietą IT.",
        time: "wczoraj, 12:08",
      },
      {
        type: "person",
        title: "Konsultacja techniczna",
        text: "Do sprawy dołączono pracownika IT jako konsultanta.",
        time: "wczoraj, 12:44",
      },
    ],
  },
  {
    id: "KP-20260724-J7L6",
    department: "contracts",
    category: "contracts",
    source: "email",
    status: "new",
    priority: "low",
    createdAt: "2026-07-24T11:36:00+02:00",
    updatedAt: "2026-07-24T11:36:00+02:00",
    owner: null,
    company: "Park Technologiczny Alfa",
    companyNip: "676-24-09-115",
    companyAddress: "al. Innowacji 21, 30-001 Kraków",
    contact: "Monika W.",
    phone: "+48 500 001 163",
    email: "monika.w@example.test",
    subject: "Pytanie o datę zakończenia umowy",
    message: "Proszę o potwierdzenie okresu obowiązywania umowy wynajmu urządzenia.",
    sourceDetail: "E-mail / umowy",
    sourceUrl: "",
    intent: "Umowa",
    identity: "Istniejący klient",
    timeline: [
      {
        type: "mail",
        title: "Odebrano wiadomość",
        text: "Wiadomość została zakwalifikowana do kolejki umów.",
        time: "wczoraj, 11:36",
      },
    ],
  },
  {
    id: "KP-20260724-K5D9",
    department: "service_it",
    source: "web_form",
    status: "transferred",
    terminalAt: "2026-07-24T09:26:00+02:00",
    priority: "medium",
    createdAt: "2026-07-24T09:03:00+02:00",
    updatedAt: "2026-07-24T09:28:00+02:00",
    owner: "Alicja Nowak",
    company: "Artemis Foods sp. z o.o.",
    contact: "Paweł Z.",
    phone: "+48 500 001 248",
    email: "pawel.z@example.test",
    subject: "Błąd podajnika dokumentów",
    message: "Podajnik pobiera kilka kartek jednocześnie.",
    sourceDetail: "RICOH IM C3000 / S/N …0193",
    sourceUrl: "/kontakt?temat=serwis",
    intent: "Zgłoszenie serwisowe",
    identity: "Klient i urządzenie potwierdzone",
    device: "RICOH IM C3000",
    serial: "3920P600193",
    msCustomer: "ID klienta 3914",
    msOrder: "15488/2026",
    timeline: [
      {
        type: "sms",
        title: "Telefon potwierdzony",
        text: "Klient potwierdził aktualność firmy i urządzenia.",
        time: "wczoraj, 09:05",
      },
      {
        type: "ms",
        title: "Przekazano do Menadżera Serwisu",
        text: "Utworzono zlecenie 15488/2026 i zapisano powiązanie.",
        time: "wczoraj, 09:26",
      },
      {
        type: "sms",
        title: "Wysłano SMS do klienta",
        text: "Zlecenie zostało przyjęte i oczekuje na przydzielenie serwisanta.",
        time: "wczoraj, 09:28",
      },
    ],
  },
  {
    id: "KP-20260723-L8S2",
    department: "sales",
    source: "voice",
    status: "done",
    priority: "low",
    createdAt: "2026-06-05T13:12:00+02:00",
    updatedAt: "2026-07-06T10:17:00+02:00",
    owner: "Kamil",
    firstClaimedAt: "2026-06-06T09:12:00+02:00",
    archivedAt: "2026-07-06T09:12:00+02:00",
    archiveReason: "30 dni od pierwszego przejęcia przez handlowca",
    company: "Meridian Architekci",
    contact: "Karol C.",
    phone: "+48 500 001 384",
    email: "karol.c@example.test",
    subject: "Ploter do pracowni projektowej",
    message: "Zapytanie o zakup plotera A0 z dostawą i wdrożeniem.",
    sourceDetail: "Voice 998 / przekazanie do handlowca",
    sourceUrl: "",
    intent: "Zakup",
    identity: "Nowy kontakt",
    timeline: [
      {
        type: "voice",
        title: "Rozmowa z asystentem głosowym",
        text: "Zebrano podstawowe wymagania i zgodę na kontakt.",
        time: "23 lipca, 13:12",
      },
      {
        type: "person",
        title: "Sprawa przejęta",
        text: "Klient otrzymał SMS z numerem opiekuna.",
        time: "23 lipca, 13:16",
      },
      {
        type: "done",
        title: "Sprawa zakończona",
        text: "Oferta została wysłana klientowi.",
        time: "wczoraj, 10:17",
      },
    ],
  },
  {
    id: "KP-20260723-N6C1",
    department: "contracts",
    category: "meters",
    source: "web_form",
    status: "new",
    priority: "medium",
    createdAt: "2026-07-23T11:18:00+02:00",
    updatedAt: "2026-07-23T11:18:00+02:00",
    owner: null,
    company: "Kancelaria Północ sp. z o.o.",
    companyNip: "584-27-11-903",
    companyAddress: "ul. Długa 42, 80-831 Gdańsk",
    contact: "Magdalena R.",
    phone: "+48 500 001 512",
    email: "magdalena.r@example.test",
    subject: "Przekazanie miesięcznych liczników",
    message:
      "Proszę zaktualizować liczniki urządzenia RICOH IM C4500. Odczyt czarno-biały 184320, kolor 38740, skany 12905.",
    sourceDetail: "Formularz licznikowy / RICOH IM C4500",
    sourceUrl: "/liczniki",
    intent: "Aktualizacja liczników",
    identity: "Klient i urządzenie rozpoznane",
    device: "RICOH IM C4500",
    serial: "5381P800615",
    msCustomer: "ID klienta 5118",
    previousMeters: {
      date: "2026-06-30",
      bw: 176480,
      color: 36110,
      scan: 11784,
    },
    timeline: [
      {
        type: "form",
        title: "Odebrano formularz licznikowy",
        text: "System rozpoznał klienta i urządzenie oraz przygotował poprzednie stany do kontroli.",
        time: "23 lipca, 11:18",
      },
    ],
  },
  {
    id: "KP-20260723-M2F4",
    department: "other",
    source: "web_form",
    status: "new",
    priority: "low",
    createdAt: "2026-07-23T10:48:00+02:00",
    updatedAt: "2026-07-23T10:48:00+02:00",
    owner: null,
    company: "Osoba prywatna",
    contact: "Łukasz T.",
    phone: "+48 500 001 479",
    email: "lukasz.t@example.test",
    subject: "Pytanie o utylizację tonerów",
    message: "Czy można dostarczyć zużyte tonery bezpośrednio do siedziby firmy?",
    sourceDetail: "Kontakt / Inna sprawa",
    sourceUrl: "/kontakt?temat=inne",
    intent: "Informacja",
    identity: "Brak dopasowania",
    timeline: [
      {
        type: "form",
        title: "Formularz kontaktowy",
        text: "Sprawa oczekuje na kwalifikację.",
        time: "23 lipca, 10:48",
      },
    ],
  },
];

let crmCases = [];
let crmOperators = [];
let currentView = "home";
let currentFilter = "open";
let currentSearch = "";
let activeCaseId = null;
let currentWorkspaceMode = "primary";
let draggedCaseId = null;
let dispatcherFocusCaseId = null;
let actionCaseId = null;
let currentUser = {
  name: "Bieżący użytkownik",
  initials: "BU",
  role: "Centrum Obsługi",
};

function cloneCases(cases) {
  if (typeof structuredClone === "function") {
    return structuredClone(cases);
  }
  return JSON.parse(JSON.stringify(cases));
}

function currentOperatorId() {
  return Number(document.getElementById("crm-operator-select")?.value || 0);
}

function mapApiCase(item) {
  return {
    id: item.ref,
    externalRef: item.external_ref,
    conversationRef: item.conversation_ref,
    department: item.queue,
    category: item.category,
    source: item.source,
    status: item.status,
    priority: item.priority,
    createdAt: item.created_at,
    updatedAt: item.updated_at,
    firstClaimedAt: item.first_claimed_at,
    terminalAt: item.terminal_at,
    archivedAt: item.archived_at,
    owner: item.owner_name,
    ownerUserId: item.owner_user_id,
    company: item.company_name,
    contact: item.contact_name,
    phone: item.contact_phone || "Nie podano",
    email: item.contact_email || "Nie podano",
    subject: item.subject,
    message: item.message,
    sourceDetail: item.source_detail || "",
    sourceUrl: item.source_url || "",
    intent: CRM_DEPARTMENT_LABELS[item.queue] || "Do kwalifikacji",
    identity: item.identity_status || "Niezweryfikowany kontakt",
    device: item.device_label || "",
    serial: item.device_serial_last4 ? `••••${item.device_serial_last4}` : "",
    msOrder: item.ms_order_ref,
    isLab: item.is_lab,
    timeline: (item.events || []).map((event) => ({
      type: event.type,
      title: event.title,
      text: event.text || "",
      time: formatDateTime(event.created_at),
    })),
  };
}

function isMeterCase(caseItem) {
  return caseItem?.category === "meters";
}

async function crmApi(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body) {
    headers["Content-Type"] = "application/json";
  }
  const token = readCrmToken();
  if (token) {
    headers["X-Admin-Session"] = token;
  }
  const response = await fetch(`/api/crm/v1${path}`, { ...options, headers });
  if (!response.ok) {
    let detail = "Operacja nie powiodła się.";
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch (_error) {
      // Odpowiedź bez JSON pozostaje opisana komunikatem ogólnym.
    }
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

async function loadCrmOperators() {
  crmOperators = await crmApi("/operators");
  if (!crmOperators.length) {
    throw new Error("Brak aktywnego operatora CTIP dla laboratorium.");
  }
  const select = document.getElementById("crm-operator-select");
  const savedOperatorId = window.localStorage?.getItem("crm-declared-operator-id");
  select.innerHTML = crmOperators
    .map(
      (operator) =>
        `<option value="${operator.id}">${escapeHtml(operator.name)} · ${escapeHtml(
          operator.role
        )}</option>`
    )
    .join("");
  if (savedOperatorId && crmOperators.some((item) => String(item.id) === savedOperatorId)) {
    select.value = savedOperatorId;
  }
  applyDeclaredOperator();
}

function applyDeclaredOperator() {
  const selected = crmOperators.find((item) => item.id === currentOperatorId());
  if (!selected) {
    return;
  }
  currentUser = {
    id: selected.id,
    name: selected.name,
    initials: selected.name
      .split(/\s+/)
      .slice(0, 2)
      .map((part) => part.slice(0, 1).toUpperCase())
      .join(""),
    role: selected.role === "admin" ? "Administrator testowy" : "Operator testowy",
  };
  document.getElementById("crm-user-name").textContent = currentUser.name;
  document.getElementById("crm-user-avatar").textContent = currentUser.initials;
  document.getElementById("crm-user-role").textContent = currentUser.role;
}

async function loadCrmCases() {
  const response = await crmApi("/cases?include_archived=true&limit=500");
  crmCases = response.items.map(mapApiCase);
  dispatcherFocusCaseId = crmCases.find((item) => !item.archivedAt)?.id || null;
}

async function persistCaseAction(caseId, action, values = {}) {
  const item = await crmApi(`/cases/${encodeURIComponent(caseId)}/actions`, {
    method: "POST",
    body: JSON.stringify({
      action,
      declared_operator_id: currentOperatorId(),
      ...values,
    }),
  });
  const mapped = mapApiCase(item);
  const index = crmCases.findIndex((caseItem) => caseItem.id === caseId);
  if (index >= 0) {
    crmCases[index] = mapped;
  } else {
    crmCases.unshift(mapped);
  }
  return mapped;
}

function readCrmToken() {
  return (
    window.localStorage?.getItem(CRM_TOKEN_KEY) ||
    window.sessionStorage?.getItem(CRM_TOKEN_KEY) ||
    null
  );
}

function clearCrmToken() {
  window.localStorage?.removeItem(CRM_TOKEN_KEY);
  window.sessionStorage?.removeItem(CRM_TOKEN_KEY);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatDateTime(value) {
  if (!value) {
    return "—";
  }
  const parsedDate = new Date(value);
  if (Number.isNaN(parsedDate.getTime())) {
    return value;
  }
  return parsedDate.toLocaleString("pl-PL", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function sourceStyle(source) {
  const styles = {
    form: ["#e7f0ff", "#1769e0"],
    configurator: ["#e7f0ff", "#1769e0"],
    chat: ["#efeaff", "#6b58d9"],
    manual: ["#f0f2f6", "#5e6b81"],
    scenario: ["#fff1df", "#bd6910"],
    web_form: ["#e7f0ff", "#1769e0"],
    web_chat: ["#efeaff", "#6b58d9"],
    voice: ["#e7f7f1", "#16876f"],
    email: ["#fff1df", "#bd6910"],
    phone: ["#f0f2f6", "#5e6b81"],
  };
  const [background, color] = styles[source] || styles.phone;
  return `--source-bg:${background};--source-color:${color}`;
}

function statusMarkup(status) {
  return `<span class="crm-status ${escapeHtml(status)}">${escapeHtml(
    CRM_STATUS_LABELS[status] || status
  )}</span>`;
}

function priorityMarkup(priority) {
  const labels = {
    high: "Pilna",
    normal: "Normalna",
    medium: "Normalna",
    low: "Niska",
  };
  return `<span class="crm-priority ${escapeHtml(priority)}">${escapeHtml(
    labels[priority] || "Normalna"
  )}</span>`;
}

function caseRowMarkup(caseItem) {
  return `
    <button class="crm-case-row" type="button" data-case-id="${escapeHtml(caseItem.id)}">
      <span class="crm-case-primary">
        <span class="crm-source-icon" style="${sourceStyle(caseItem.source)}">${escapeHtml(
          CRM_SOURCE_ICONS[caseItem.source] || "•"
        )}</span>
        <span>
          <strong>${escapeHtml(caseItem.subject)}</strong>
          <small>${escapeHtml(caseItem.id)} · ${escapeHtml(caseItem.company)}</small>
        </span>
      </span>
      <span class="crm-case-meta">
        <span>${escapeHtml(CRM_SOURCE_LABELS[caseItem.source] || caseItem.source)}</span>
        <small>${escapeHtml(caseItem.sourceDetail)}</small>
      </span>
      <span class="crm-case-meta">
        ${statusMarkup(caseItem.status)}
        <small>${escapeHtml(caseItem.owner || "Nieprzejęta")}</small>
      </span>
      <span class="crm-case-meta">
        ${priorityMarkup(caseItem.priority)}
        <small>${escapeHtml(formatDateTime(caseItem.updatedAt))}</small>
      </span>
    </button>
  `;
}

function dashboardCaseMarkup(caseItem) {
  return `
    <button class="crm-dashboard-case" type="button" data-case-id="${escapeHtml(
      caseItem.id
    )}">
      <header>
        <span class="crm-source-icon" style="${sourceStyle(caseItem.source)}">${escapeHtml(
          CRM_SOURCE_ICONS[caseItem.source] || "•"
        )}</span>
        <span>
          <strong>${escapeHtml(caseItem.subject)}</strong>
          <small>${escapeHtml(caseItem.id)} · ${escapeHtml(
            CRM_DEPARTMENT_LABELS[caseItem.department]
          )}</small>
        </span>
        ${priorityMarkup(caseItem.priority)}
      </header>
      <div class="crm-dashboard-case-contact">
        <strong>${escapeHtml(caseItem.company)}</strong>
        <span>Osoba: ${escapeHtml(caseItem.contact)}</span>
        <span>Telefon: ${escapeHtml(caseItem.phone)}</span>
        <span>E-mail: ${escapeHtml(caseItem.email)}</span>
      </div>
      <p>${escapeHtml(caseItem.message)}</p>
      <footer>
        ${statusMarkup(caseItem.status)}
        <span>${escapeHtml(caseItem.owner || "Nieprzejęta")}</span>
        <small>${escapeHtml(formatDateTime(caseItem.updatedAt))}</small>
      </footer>
    </button>
  `;
}

function matchesSearch(caseItem) {
  if (!currentSearch) {
    return true;
  }
  const searchable = [
    caseItem.id,
    caseItem.company,
    caseItem.contact,
    caseItem.phone,
    caseItem.email,
    caseItem.subject,
    caseItem.message,
    caseItem.sourceDetail,
    caseItem.serial,
  ]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase("pl");
  return searchable.includes(currentSearch);
}

function openCases() {
  return crmCases.filter(
    (caseItem) =>
      !caseItem.archivedAt && !["done", "transferred"].includes(caseItem.status)
  );
}

function filteredCasesForView(view) {
  let result = crmCases.filter(matchesSearch);
  if (view === "inbox") {
    result = result.filter(
      (caseItem) => !["done", "transferred"].includes(caseItem.status)
    );
  } else if (view === "my") {
    result = result.filter(
      (caseItem) =>
        caseItem.owner === currentUser.name || caseItem.owner === "Bieżący użytkownik"
    );
  } else if (Object.hasOwn(CRM_DEPARTMENT_LABELS, view)) {
    result = result.filter((caseItem) => caseItem.department === view);
  } else if (view === "archive") {
    result = result.filter((caseItem) => Boolean(caseItem.archivedAt));
  }

  if (view !== "archive") {
    if (currentFilter === "unassigned") {
      result = result.filter((caseItem) => !caseItem.owner);
    } else if (currentFilter === "mine") {
      result = result.filter(
        (caseItem) =>
          caseItem.owner === currentUser.name || caseItem.owner === "Bieżący użytkownik"
      );
    } else if (currentFilter === "open") {
      result = result.filter((caseItem) => !["done", "transferred"].includes(caseItem.status));
    }
  }

  return result.sort((leftCase, rightCase) =>
    rightCase.updatedAt.localeCompare(leftCase.updatedAt)
  );
}

function renderStats() {
  const opened = openCases();
  const unassigned = opened.filter((caseItem) => !caseItem.owner);
  const urgent = opened.filter((caseItem) => caseItem.priority === "high");
  const serviceReview = opened.filter(
    (caseItem) =>
      caseItem.department === "service_it" &&
      ["attention", "waiting"].includes(caseItem.status)
  );
  return `
    <section class="crm-stats" aria-label="Podsumowanie kolejek">
      <article class="crm-stat-card" style="--stat-tint:#e7f0ff;--stat-color:#1769e0">
        <span>Otwarte sprawy</span>
        <strong>${opened.length}</strong>
        <small>we wszystkich kolejkach</small>
      </article>
      <article class="crm-stat-card" style="--stat-tint:#fff0db;--stat-color:#b7650c">
        <span>Nieprzejęte</span>
        <strong>${unassigned.length}</strong>
        <small>${unassigned.filter((caseItem) => caseItem.department === "sales").length} w handlu</small>
      </article>
      <article class="crm-stat-card" style="--stat-tint:#fde8ec;--stat-color:#bc3344">
        <span>Pilne</span>
        <strong>${urgent.length}</strong>
        <small>wymagają szybkiej reakcji</small>
      </article>
      <article class="crm-stat-card" style="--stat-tint:#e5f6ef;--stat-color:#177353">
        <span>Serwis do weryfikacji</span>
        <strong>${serviceReview.length}</strong>
        <small>przed przekazaniem do MS</small>
      </article>
    </section>
  `;
}

function renderHome() {
  const attentionCases = (
    currentSearch
      ? openCases()
      : openCases().filter(
          (caseItem) =>
            !caseItem.owner || caseItem.priority === "high" || caseItem.status === "attention"
        )
  )
    .filter(matchesSearch)
    .slice(0, 8);
  const departmentCounts = Object.keys(CRM_DEPARTMENT_LABELS).reduce(
    (result, department) => {
      result[department] = openCases().filter(
        (caseItem) => caseItem.department === department
      ).length;
      return result;
    },
    {}
  );
  const activity = crmCases
    .flatMap((caseItem) =>
      caseItem.timeline.map((timelineItem) => ({
        ...timelineItem,
        caseId: caseItem.id,
        company: caseItem.company,
      }))
    )
    .slice(-5)
    .reverse();

  return `
    ${renderStats()}
    <section class="crm-dashboard-grid">
      <div>
        <article class="crm-panel">
          <header class="crm-panel-header">
            <div>
              <h2>Wymagają uwagi</h2>
              <p>Nieprzejęte, pilne lub oczekujące na weryfikację.</p>
            </div>
            <button class="crm-link-button" type="button" data-view-link="my">Moje sprawy →</button>
          </header>
          <div class="crm-case-list crm-dashboard-case-list">
            ${
              attentionCases.length
                ? attentionCases.map(dashboardCaseMarkup).join("")
                : renderEmptyMarkup("Brak spraw wymagających uwagi", "Wszystkie bieżące kolejki są obsłużone.")
            }
          </div>
        </article>
      </div>
      <div>
        <article class="crm-panel">
          <header class="crm-panel-header">
            <div>
              <h3>Kanały dzisiaj</h3>
              <p>Przykładowy rozkład nowych kontaktów.</p>
            </div>
          </header>
          <div class="crm-channel-overview">
            ${channelCardMarkup("web_form", "Formularze WWW", 5)}
            ${channelCardMarkup("voice", "Voice i telefon", 4)}
            ${channelCardMarkup("web_chat", "Chat WWW", 3)}
            ${channelCardMarkup("email", "E-mail", 2)}
          </div>
        </article>
        <article class="crm-panel">
          <header class="crm-panel-header">
            <div>
              <h3>Ostatnia aktywność</h3>
              <p>Wspólna oś działań pracowników i automatów.</p>
            </div>
          </header>
          <div class="crm-activity-list">
            ${activity
              .map(
                (activityItem) => `
                  <div class="crm-activity">
                    <span>${escapeHtml(activityItem.type.slice(0, 1).toUpperCase())}</span>
                    <div>
                      <p><strong>${escapeHtml(activityItem.title)}</strong> · ${escapeHtml(
                        activityItem.company
                      )}</p>
                      <small>${escapeHtml(activityItem.time)} · ${escapeHtml(
                        activityItem.caseId
                      )}</small>
                    </div>
                  </div>
                `
              )
              .join("")}
          </div>
        </article>
        <article class="crm-panel">
          <header class="crm-panel-header">
            <div>
              <h3>Obciążenie kolejek</h3>
              <p>Otwarte sprawy według działu.</p>
            </div>
          </header>
          <div class="crm-queue-summary">
            ${Object.entries(CRM_DEPARTMENT_LABELS)
              .map(
                ([department, label]) => `
                  <button class="crm-queue-summary-card crm-queue-link" type="button" data-view-link="${department}">
                    <span>${escapeHtml(label)}</span>
                    <strong>${departmentCounts[department]}</strong>
                    <small>Przejdź do kolejki</small>
                  </button>
                `
              )
              .join("")}
          </div>
        </article>
      </div>
    </section>
  `;
}

function channelCardMarkup(source, label, count) {
  return `
    <div class="crm-channel-card">
      <span style="${sourceStyle(source)}">${escapeHtml(CRM_SOURCE_ICONS[source])}</span>
      <div>
        <strong>${escapeHtml(label)}</strong>
        <small>nowe interakcje</small>
      </div>
      <b>${count}</b>
    </div>
  `;
}

function renderQueue(view) {
  const cases = filteredCasesForView(view);
  const departmentLabel =
    view === "inbox"
      ? "Wszystkie otwarte sprawy"
      : view === "my"
        ? "Moje sprawy"
        : CRM_DEPARTMENT_LABELS[view] || "Sprawy";
  const scopedOpenCases =
    view === "inbox"
      ? openCases()
      : view === "my"
        ? openCases().filter(
            (caseItem) =>
              caseItem.owner === currentUser.name ||
              caseItem.owner === "Bieżący użytkownik"
          )
      : openCases().filter(
          (caseItem) => caseItem.department === view
        );
  const unassignedCount = scopedOpenCases.filter((caseItem) => !caseItem.owner).length;
  const highCount = cases.filter((caseItem) => caseItem.priority === "high").length;
  const oldest = cases.at(-1);

  return `
    <div class="crm-queue-toolbar">
      <div class="crm-filter-group" role="group" aria-label="Filtrowanie spraw">
        ${filterButtonMarkup("open", "Otwarte")}
        ${filterButtonMarkup("unassigned", "Nieprzejęte")}
        ${filterButtonMarkup("mine", "Moje")}
        ${filterButtonMarkup("all", "Wszystkie")}
      </div>
      <button class="crm-button secondary" type="button" data-demo-new-case>+ Nowa sprawa ręczna</button>
    </div>
    <section class="crm-queue-layout">
      <article class="crm-panel">
        <header class="crm-panel-header">
          <div>
            <h2>${escapeHtml(departmentLabel)}</h2>
            <p>${cases.length} spraw spełnia aktualne kryteria.</p>
          </div>
        </header>
        <div class="crm-case-list">
          ${
            cases.length
              ? cases.map(caseRowMarkup).join("")
              : renderEmptyMarkup(
                  "Brak spraw w tym widoku",
                  "Zmień filtr lub wyszukiwane hasło."
                )
          }
        </div>
      </article>
      <aside>
        <article class="crm-panel">
          <header class="crm-panel-header">
            <div>
              <h3>Podsumowanie kolejki</h3>
              <p>Stan zapisany w ctip_test.</p>
            </div>
          </header>
          <div class="crm-queue-summary">
            ${queueSummaryCardMarkup("Nieprzejęte", unassignedCount, "oczekują na właściciela")}
            ${queueSummaryCardMarkup("Pilne", highCount, "oznaczone wysokim priorytetem")}
            ${queueSummaryCardMarkup(
              "Najstarsza aktywność",
              oldest ? formatDateTime(oldest.updatedAt) : "—",
              oldest ? oldest.id : "brak spraw"
            )}
          </div>
        </article>
        ${
          view === "service_it"
            ? `
              <article class="crm-panel">
                <header class="crm-panel-header">
                  <div>
                    <h3>Przekazanie do MS</h3>
                    <p>Docelowy punkt końcowy etapu Centrum.</p>
                  </div>
                </header>
                <div class="crm-queue-summary">
                  <div class="crm-ms-preview">
                    <strong>Wymagana ręczna akceptacja</strong>
                    <p>Automat utworzy zlecenie dopiero po sprawdzeniu klienta i urządzenia przez uprawnioną osobę.</p>
                  </div>
                </div>
              </article>
            `
            : ""
        }
      </aside>
    </section>
  `;
}

function filterButtonMarkup(filter, label) {
  return `
    <button class="crm-filter-button ${currentFilter === filter ? "active" : ""}" type="button" data-filter="${filter}">
      ${escapeHtml(label)}
    </button>
  `;
}

function queueSummaryCardMarkup(label, value, note) {
  return `
    <div class="crm-queue-summary-card">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
      <small>${escapeHtml(note)}</small>
    </div>
  `;
}

function renderForms() {
  const forms = [
    {
      name: "Kontakt wielozadaniowy",
      source: "Bitrix 81",
      placement: "/kontakt i odnośniki stron usługowych",
      routing: "5 kafelków działów",
      state: "Prototyp do przygotowania",
      ready: false,
    },
    {
      name: "Produkt i konfigurator",
      source: "Bitrix 79",
      placement: "43 produkty i 8 konfiguracji",
      routing: "Handel / panel boczny",
      state: "Zmapowano źródła",
      ready: true,
    },
    {
      name: "Ofertownik",
      source: "CF7 5 + Bitrix",
      placement: "9 stron ofertownika",
      routing: "Handel / pytanie lub test 7 dni",
      state: "Zmapowano źródła",
      ready: true,
    },
    {
      name: "Konsultacja case study",
      source: "Bitrix 71",
      placement: "2 artykuły",
      routing: "Handel / konsultacja",
      state: "Zmapowano źródła",
      ready: true,
    },
    {
      name: "Zgłoszenie serwisowe",
      source: "Nowy formularz",
      placement: "/kontakt / Serwis + IT",
      routing: "Weryfikacja SMS → MS",
      state: "Przepływ zaplanowany",
      ready: false,
    },
    {
      name: "Serwis + IT",
      source: "Nowy wariant",
      placement: "/kontakt oraz /obsluga-it",
      routing: "Oferta → Handel, pomoc → Serwis + IT",
      state: "Przepływ zaplanowany",
      ready: false,
    },
  ];

  return `
    <section class="crm-form-catalog">
      ${forms
        .map(
          (form) => `
            <article class="crm-form-card">
              <header>
                <div>
                  <h2>${escapeHtml(form.name)}</h2>
                  <p>${escapeHtml(form.placement)}</p>
                </div>
                <span class="crm-form-state ${form.ready ? "ready" : ""}">${escapeHtml(
                  form.state
                )}</span>
              </header>
              <ul>
                <li><span>Zastępuje</span><strong>${escapeHtml(form.source)}</strong></li>
                <li><span>Routing</span><strong>${escapeHtml(form.routing)}</strong></li>
                <li><span>Tryb</span><strong>LAB bez wysyłek</strong></li>
              </ul>
            </article>
          `
        )
        .join("")}
    </section>
  `;
}

function renderArchive() {
  const archivedCases = filteredCasesForView("archive");
  return `
    <section class="crm-stats">
      <article class="crm-stat-card" style="--stat-tint:#e8f6ef;--stat-color:#177353">
        <span>Handel</span>
        <strong>${archivedCases.filter((caseItem) => caseItem.department === "sales").length}</strong>
        <small>zakończone sprawy</small>
      </article>
      <article class="crm-stat-card" style="--stat-tint:#fff0dd;--stat-color:#b7650c">
        <span>Serwis + IT</span>
        <strong>${archivedCases.filter((caseItem) => caseItem.department === "service_it").length}</strong>
        <small>przekazane do MS</small>
      </article>
      <article class="crm-stat-card" style="--stat-tint:#eeeaff;--stat-color:#6654cf">
        <span>Umowy i liczniki</span>
        <strong>${archivedCases.filter((caseItem) => caseItem.department === "contracts").length}</strong>
        <small>zakończone sprawy</small>
      </article>
      <article class="crm-stat-card" style="--stat-tint:#edf1f6;--stat-color:#647086">
        <span>Inne</span>
        <strong>${archivedCases.filter((caseItem) => caseItem.department === "other").length}</strong>
        <small>sprawy pozostałe</small>
      </article>
    </section>
    <article class="crm-panel">
      <header class="crm-panel-header">
        <div>
          <h2>Ostatnio zarchiwizowane</h2>
          <p>Sprawy trafiają tutaj po 30 dniach od pierwszego przejęcia handlowego albo zakończenia pozostałego procesu.</p>
        </div>
      </header>
      <div class="crm-case-list">
        ${
          archivedCases.length
            ? archivedCases
                .map(
                  (caseItem) => `
                    <div class="crm-archive-row">
                      ${caseRowMarkup(caseItem)}
                      <p><strong>${escapeHtml(caseItem.archiveReason || "Autoarchiwizacja po 30 dniach")}</strong> · ${escapeHtml(
                        formatDateTime(caseItem.archivedAt)
                      )}</p>
                    </div>
                  `
                )
                .join("")
            : renderEmptyMarkup("Archiwum jest puste", "Brak zakończonych spraw LAB.")
        }
      </div>
    </article>
  `;
}

function queueDisplayToolbarMarkup(primaryLabel) {
  return `
    <div class="crm-queue-display-toolbar">
      <div>
        <strong>Widok kolejki</strong>
        <span>Te same sprawy można przeglądać w widoku roboczym albo jako listę.</span>
      </div>
      <div class="crm-workspace-switcher" role="group" aria-label="Wybór widoku kolejki">
        <button class="${currentWorkspaceMode === "primary" ? "active" : ""}" type="button" data-workspace-mode="primary">
          ${escapeHtml(primaryLabel)}
        </button>
        <button class="${currentWorkspaceMode === "list" ? "active" : ""}" type="button" data-workspace-mode="list">
          Lista
        </button>
      </div>
    </div>
  `;
}

function salesKanbanCardMarkup(caseItem) {
  return `
    <article class="crm-kanban-card crm-sales-kanban-card" draggable="true" data-drag-case-id="${escapeHtml(
      caseItem.id
    )}">
      <header>
        <span class="crm-source-icon" style="${sourceStyle(caseItem.source)}">${escapeHtml(
          CRM_SOURCE_ICONS[caseItem.source] || "•"
        )}</span>
        ${priorityMarkup(caseItem.priority)}
      </header>
      <button type="button" data-case-id="${escapeHtml(caseItem.id)}">
        <strong>${escapeHtml(caseItem.subject)}</strong>
        <span class="crm-sales-company">${escapeHtml(caseItem.company)}</span>
      </button>
      <div class="crm-sales-contact">
        <strong>${escapeHtml(caseItem.contact)}</strong>
        <a href="tel:${escapeHtml(caseItem.phone)}">${escapeHtml(caseItem.phone)}</a>
        <a href="mailto:${escapeHtml(caseItem.email)}">${escapeHtml(caseItem.email)}</a>
      </div>
      <p>${escapeHtml(caseItem.message)}</p>
      <footer>
        <small>${escapeHtml(caseItem.id)} · ${escapeHtml(
          formatDateTime(caseItem.createdAt)
        )}</small>
        <span>${escapeHtml(caseItem.owner || "Nieprzejęta")}</span>
      </footer>
    </article>
  `;
}

function renderSalesKanban(cases) {
  const activeCases = cases.filter((caseItem) => !caseItem.archivedAt);
  return `
    ${queueDisplayToolbarMarkup("Kanban")}
    <section class="crm-sales-kanban" aria-label="Kanban handlowców">
      ${CRM_SALES_KANBAN_COLUMNS.map((column) => {
        const columnCases = activeCases.filter((caseItem) =>
          column.owner ? caseItem.owner === column.owner : !["Michał", "Kamil"].includes(caseItem.owner)
        );
        return `
          <article class="crm-kanban-column" data-sales-owner="${column.owner || ""}">
            <header>
              <div>
                <h3>${escapeHtml(column.title)}</h3>
                <p>${escapeHtml(column.description)}</p>
              </div>
              <span>${columnCases.length}</span>
            </header>
            <div class="crm-kanban-dropzone">
              ${
                columnCases.length
                  ? columnCases.map(salesKanbanCardMarkup).join("")
                  : '<p class="crm-kanban-empty">Przeciągnij tutaj sprawę</p>'
              }
            </div>
          </article>
        `;
      }).join("")}
    </section>
    <p class="crm-workspace-hint">Pierwsze przeciągnięcie do handlowca uruchamia termin autoarchiwizacji po 30 dniach. Zmiana handlowca nie resetuje tego terminu.</p>
  `;
}

function renderWorkspaceList(cases) {
  return `
    <article class="crm-work-list-panel">
      <header>
        <div>
          <h2>Lista operacyjna</h2>
          <p>Zwarty wariant dla użytkowników pracujących na dużej liczbie spraw.</p>
        </div>
        <span>${cases.length} pozycji</span>
      </header>
      <div class="crm-work-table-wrap">
        <table class="crm-work-table">
          <thead>
            <tr>
              <th>Sprawa</th>
              <th>Klient i temat</th>
              <th>Kanał</th>
              <th>Dział</th>
              <th>Status</th>
              <th>Priorytet</th>
              <th>Właściciel</th>
              <th>Aktualizacja</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            ${cases
              .map(
                (caseItem) => `
                  <tr data-case-id="${escapeHtml(caseItem.id)}">
                    <td><strong>${escapeHtml(caseItem.id)}</strong></td>
                    <td>
                      <strong>${escapeHtml(caseItem.company)}</strong>
                      <small>${escapeHtml(caseItem.subject)}</small>
                    </td>
                    <td>${escapeHtml(CRM_SOURCE_LABELS[caseItem.source] || caseItem.source)}</td>
                    <td>${escapeHtml(CRM_DEPARTMENT_LABELS[caseItem.department])}</td>
                    <td>${statusMarkup(caseItem.status)}</td>
                    <td>${priorityMarkup(caseItem.priority)}</td>
                    <td>${escapeHtml(caseItem.owner || "Nieprzejęta")}</td>
                    <td>${escapeHtml(formatDateTime(caseItem.updatedAt))}</td>
                    <td><button type="button" data-case-id="${escapeHtml(caseItem.id)}">Otwórz</button></td>
                  </tr>
                `
              )
              .join("")}
          </tbody>
        </table>
      </div>
    </article>
  `;
}

function dispatcherCaseMarkup(caseItem, focusedCase) {
  return `
    <button class="crm-dispatch-stream-item ${
      caseItem.id === focusedCase?.id ? "active" : ""
    }" type="button" data-workspace-focus="${escapeHtml(caseItem.id)}">
      <span class="crm-source-icon" style="${sourceStyle(caseItem.source)}">${escapeHtml(
        CRM_SOURCE_ICONS[caseItem.source] || "•"
      )}</span>
      <span>
        <strong>${escapeHtml(caseItem.subject)}</strong>
        <small>${escapeHtml(caseItem.company)} · ${escapeHtml(
          formatDateTime(caseItem.updatedAt)
        )}</small>
        <span class="crm-dispatch-stream-contact">${escapeHtml(
          caseItem.contact
        )} · ${escapeHtml(caseItem.phone)}</span>
        <span class="crm-dispatch-stream-message">${escapeHtml(caseItem.message)}</span>
      </span>
      ${caseItem.priority === "high" ? '<b title="Pilna sprawa">!</b>' : ""}
    </button>
  `;
}

function dispatcherActionsMarkup(caseItem) {
  return `
    <button class="crm-button secondary" type="button" data-dispatch-action="transfer" data-dispatch-case-id="${escapeHtml(
      caseItem.id
    )}">Przekaż innej osobie</button>
    ${
      caseItem.department === "service_it"
        ? `<button class="crm-button success" type="button" data-dispatch-action="ms" data-dispatch-case-id="${escapeHtml(
            caseItem.id
          )}">Wpisz jako zlecenie do MS</button>`
        : ""
    }
    ${
      isMeterCase(caseItem)
        ? `<button class="crm-button success" type="button" data-dispatch-action="meters" data-dispatch-case-id="${escapeHtml(
            caseItem.id
          )}">Zaktualizuj licznik w MS</button>`
        : ""
    }
    <button class="crm-button primary" type="button" data-dispatch-action="close" data-dispatch-case-id="${escapeHtml(
      caseItem.id
    )}">Zakończ sprawę</button>
  `;
}

function renderDispatcher(cases) {
  const openWorkspaceCases = cases.filter(
    (caseItem) => !caseItem.archivedAt && !["done", "transferred"].includes(caseItem.status)
  );
  let focusedCase = openWorkspaceCases.find(
    (caseItem) => caseItem.id === dispatcherFocusCaseId
  );
  if (!focusedCase) {
    focusedCase = openWorkspaceCases[0] || cases[0];
    dispatcherFocusCaseId = focusedCase?.id || null;
  }
  return `
    <section class="crm-dispatcher">
      <aside class="crm-dispatch-stream">
        <header>
          <span>STRUMIEŃ KONTAKTÓW</span>
          <strong>${openWorkspaceCases.length} aktywnych</strong>
        </header>
        <div>
          ${openWorkspaceCases
            .slice(0, 8)
            .map((caseItem) => dispatcherCaseMarkup(caseItem, focusedCase))
            .join("")}
        </div>
      </aside>
      <article class="crm-dispatch-focus">
        ${
          focusedCase
            ? `
              <header>
                <div>
                  <span>${escapeHtml(focusedCase.id)} · ${escapeHtml(
                    CRM_DEPARTMENT_LABELS[focusedCase.department]
                  )}</span>
                  <h2>${escapeHtml(focusedCase.subject)}</h2>
                  <p>${escapeHtml(formatDateTime(focusedCase.createdAt))} · ${escapeHtml(
                    CRM_SOURCE_LABELS[focusedCase.source]
                  )}</p>
                </div>
                ${priorityMarkup(focusedCase.priority)}
              </header>
              <section class="crm-dispatch-section">
                <span>DANE FIRMY</span>
                <div class="crm-dispatch-company">
                  <strong>${escapeHtml(focusedCase.company)}</strong>
                  <p>${escapeHtml(focusedCase.companyAddress || "Brak adresu w zgłoszeniu")}</p>
                  <small>NIP: ${escapeHtml(focusedCase.companyNip || "brak danych")}</small>
                </div>
              </section>
              <section class="crm-dispatch-section">
                <span>DANE OSOBY KONTAKTOWEJ</span>
                <dl class="crm-dispatch-person">
                  <div>
                    <dt>Imię i nazwisko:</dt>
                    <dd><strong>${escapeHtml(focusedCase.contact)}</strong></dd>
                  </div>
                  <div>
                    <dt>Telefon:</dt>
                    <dd><a href="tel:${escapeHtml(focusedCase.phone)}">${escapeHtml(
                      focusedCase.phone
                    )}</a></dd>
                  </div>
                  <div>
                    <dt>E-mail:</dt>
                    <dd><a href="mailto:${escapeHtml(focusedCase.email)}">${escapeHtml(
                      focusedCase.email
                    )}</a></dd>
                  </div>
                </dl>
              </section>
              <div class="crm-dispatch-message">
                <span>TREŚĆ ZGŁOSZENIA</span>
                <p>${escapeHtml(focusedCase.message)}</p>
              </div>
              <p class="crm-dispatch-owner">Właściciel: <strong>${escapeHtml(
                focusedCase.owner || "Nieprzejęta"
              )}</strong> · ${escapeHtml(CRM_STATUS_LABELS[focusedCase.status])}</p>
              <footer>
                ${dispatcherActionsMarkup(focusedCase)}
              </footer>
            `
            : renderEmptyMarkup("Brak aktywnej sprawy", "Zmień wyszukiwane hasło.")
        }
      </article>
    </section>
  `;
}

function renderDepartmentWorkspace(department) {
  const cases = crmCases
    .filter(
      (caseItem) =>
        caseItem.department === department &&
        matchesSearch(caseItem) &&
        !caseItem.archivedAt &&
        !["done", "transferred"].includes(caseItem.status)
    )
    .sort((leftCase, rightCase) => rightCase.updatedAt.localeCompare(leftCase.updatedAt));
  if (currentWorkspaceMode === "list") {
    return `${queueDisplayToolbarMarkup(
      department === "sales" ? "Kanban" : "Pulpit dyspozytora"
    )}${renderWorkspaceList(cases)}`;
  }
  if (department === "sales") {
    return renderSalesKanban(cases);
  }
  return `${queueDisplayToolbarMarkup("Pulpit dyspozytora")}${renderDispatcher(cases)}`;
}

function applyDemoAutoArchive(now = new Date()) {
  const archiveDelayMs = CRM_AUTO_ARCHIVE_DAYS * 24 * 60 * 60 * 1000;
  crmCases.forEach((caseItem) => {
    if (caseItem.archivedAt) {
      return;
    }
    const basis =
      caseItem.department === "sales" ? caseItem.firstClaimedAt : caseItem.terminalAt;
    if (!basis) {
      return;
    }
    const basisDate = new Date(basis);
    if (Number.isNaN(basisDate.getTime()) || now.getTime() - basisDate.getTime() < archiveDelayMs) {
      return;
    }
    caseItem.archivedAt = new Date(basisDate.getTime() + archiveDelayMs).toISOString();
    caseItem.archiveReason =
      caseItem.department === "sales"
        ? "30 dni od pierwszego przejęcia przez handlowca"
        : "30 dni od zakończenia sprawy";
  });
}

function renderEmptyMarkup(title, description) {
  return `
    <div class="crm-empty">
      <span>✓</span>
      <h2>${escapeHtml(title)}</h2>
      <p>${escapeHtml(description)}</p>
    </div>
  `;
}

function renderCurrentView() {
  applyDemoAutoArchive();
  const root = document.getElementById("crm-view-root");
  const config = CRM_VIEW_CONFIG[currentView] || CRM_VIEW_CONFIG.home;
  document.getElementById("crm-page-title").textContent = config.title;
  document.getElementById("crm-page-lead").textContent = config.lead;
  document.getElementById("crm-breadcrumb").textContent = config.breadcrumb;
  document.querySelectorAll(".crm-nav-item").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === currentView);
  });

  if (currentView === "home") {
    root.innerHTML = renderHome();
  } else if (currentView === "forms") {
    root.innerHTML = renderForms();
  } else if (currentView === "archive") {
    root.innerHTML = renderArchive();
  } else if (Object.hasOwn(CRM_DEPARTMENT_LABELS, currentView)) {
    root.innerHTML = renderDepartmentWorkspace(currentView);
  } else {
    root.innerHTML = renderQueue(currentView);
  }
  updateNavigationCounts();
}

function updateNavigationCounts() {
  const counts = {
    inbox: openCases().length,
    my: crmCases.filter(
      (caseItem) =>
        (caseItem.owner === currentUser.name || caseItem.owner === "Bieżący użytkownik") &&
        !["done", "transferred"].includes(caseItem.status)
    ).length,
  };
  Object.keys(CRM_DEPARTMENT_LABELS).forEach((department) => {
    counts[department] = openCases().filter(
      (caseItem) => caseItem.department === department
    ).length;
  });
  Object.entries(counts).forEach(([name, count]) => {
    const element = document.querySelector(`[data-count="${name}"]`);
    if (element) {
      element.textContent = count;
    }
  });
}

function caseDetailMarkup(caseItem) {
  const isCurrentOwner =
    caseItem.owner === currentUser.name || caseItem.owner === "Bieżący użytkownik";
  const canTransferToMs =
    caseItem.department === "service_it" &&
    !["transferred", "done"].includes(caseItem.status) &&
    Boolean(caseItem.owner);
  return `
    <section class="crm-detail-grid">
      <div>
        <article class="crm-detail-card">
          <header>
            <h3>Dane sprawy</h3>
            <p>${escapeHtml(CRM_DEPARTMENT_LABELS[caseItem.department])} · ${escapeHtml(
              CRM_SOURCE_LABELS[caseItem.source]
            )}</p>
          </header>
          <div class="crm-detail-content">
            <div class="crm-detail-fields">
              ${detailFieldMarkup("Firma", caseItem.company)}
              ${detailFieldMarkup("Osoba kontaktowa", caseItem.contact)}
              ${detailFieldMarkup("Telefon", caseItem.phone)}
              ${detailFieldMarkup("E-mail", caseItem.email)}
              ${detailFieldMarkup("Cel kontaktu", caseItem.intent)}
              ${detailFieldMarkup("Właściciel", caseItem.owner || "Nieprzejęta")}
              ${detailFieldMarkup("Źródło", caseItem.sourceDetail, true)}
              ${detailFieldMarkup("Rozpoznanie", caseItem.identity, true)}
              ${
                caseItem.device
                  ? detailFieldMarkup(
                      "Urządzenie",
                      `${caseItem.device} / ${caseItem.serial || "brak S/N"}`,
                      true
                    )
                  : ""
              }
              ${
                caseItem.msCustomer
                  ? detailFieldMarkup("Powiązanie MS", caseItem.msCustomer, true)
                  : ""
              }
              ${
                caseItem.msOrder
                  ? detailFieldMarkup("Zlecenie MS", caseItem.msOrder, true)
                  : ""
              }
            </div>
          </div>
        </article>
        <article class="crm-detail-card">
          <header>
            <h3>Treść zgłoszenia</h3>
            <p>Zachowana jako pierwsza interakcja sprawy.</p>
          </header>
          <div class="crm-detail-content">
            <p class="crm-case-message">${escapeHtml(caseItem.message)}</p>
          </div>
        </article>
        <article class="crm-detail-card">
          <header>
            <h3>Oś czasu</h3>
            <p>Kontakty, automaty i działania pracowników.</p>
          </header>
          <div class="crm-detail-content">
            <div class="crm-timeline">
              ${caseItem.timeline
                .slice()
                .reverse()
                .map(timelineMarkup)
                .join("")}
            </div>
          </div>
        </article>
      </div>
      <aside>
        <article class="crm-detail-card">
          <header>
            <h3>Status sprawy</h3>
            <p>${statusMarkup(caseItem.status)}</p>
          </header>
          <div class="crm-detail-content crm-detail-actions">
            <button class="crm-button primary" type="button" data-case-action="claim" ${
              caseItem.department === "sales" ||
              caseItem.owner ||
              ["done", "transferred"].includes(caseItem.status)
                ? "disabled"
                : ""
            }>
              ${
                caseItem.department === "sales"
                  ? caseItem.owner
                    ? `Obsługuje: ${escapeHtml(caseItem.owner)}`
                    : "Przeciągnij kartę do handlowca"
                  : caseItem.owner
                    ? `Przejęta przez: ${escapeHtml(caseItem.owner)}`
                    : "Przejmij sprawę"
              }
            </button>
            <button class="crm-button secondary" type="button" data-case-action="note">
              Dodaj notatkę
            </button>
            ${
              caseItem.department === "service_it"
                ? `
                  <div class="crm-ms-preview">
                    <strong>Menadżer Serwisu</strong>
                    <p>${
                      caseItem.msOrder
                        ? `Powiązano ze zleceniem ${escapeHtml(caseItem.msOrder)}.`
                        : "W LAB operacja nie wykonuje zapisu do Firebird."
                    }</p>
                  </div>
                  <button class="crm-button success" type="button" data-case-action="transfer" ${
                    canTransferToMs ? "" : "disabled"
                  }>
                    ${
                      caseItem.msOrder
                        ? "Przekazana do MS"
                        : "Symuluj przekazanie do MS"
                    }
                  </button>
                `
                : ""
            }
            ${
              caseItem.department !== "sales"
                ? `<button class="crm-button secondary" type="button" data-case-action="close" ${
                    ["done", "transferred"].includes(caseItem.status) ? "disabled" : ""
                  }>Zakończ sprawę</button>`
                : ""
            }
          </div>
        </article>
        <article class="crm-detail-card">
          <header>
            <h3>Następny krok</h3>
            <p>Podpowiedź operacyjna laboratorium.</p>
          </header>
          <div class="crm-detail-content">
            <div class="crm-ms-preview">
              <strong>${escapeHtml(nextStepTitle(caseItem))}</strong>
              <p>${escapeHtml(nextStepText(caseItem, isCurrentOwner))}</p>
            </div>
          </div>
        </article>
      </aside>
    </section>
  `;
}

function detailFieldMarkup(label, value, full = false) {
  return `
    <div class="crm-detail-field ${full ? "full" : ""}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value || "—")}</strong>
    </div>
  `;
}

function timelineMarkup(timelineItem) {
  return `
    <div class="crm-timeline-item">
      <span>${escapeHtml(timelineItem.type.slice(0, 1).toUpperCase())}</span>
      <div>
        <strong>${escapeHtml(timelineItem.title)}</strong>
        <p>${escapeHtml(timelineItem.text)}</p>
        <small>${escapeHtml(timelineItem.time)}</small>
      </div>
    </div>
  `;
}

function nextStepTitle(caseItem) {
  if (caseItem.status === "transferred") {
    return "Etap Centrum zakończony";
  }
  if (caseItem.status === "done") {
    return "Sprawa zakończona";
  }
  if (!caseItem.owner) {
    return "Sprawa oczekuje na przejęcie";
  }
  if (caseItem.department === "service_it") {
    return "Zweryfikuj dane przed przekazaniem";
  }
  if (caseItem.status === "waiting") {
    return "Oczekiwanie na kontakt lub dane";
  }
  return "Kontynuuj obsługę";
}

function nextStepText(caseItem, isCurrentOwner) {
  if (caseItem.status === "transferred") {
    return "Minimalny podgląd statusu będzie później odczytywany z Firebird.";
  }
  if (caseItem.status === "done") {
    return "Po 30 dniach sprawa trafi do archiwum właściwego działu.";
  }
  if (!caseItem.owner) {
    return "Pierwszy uprawniony pracownik może atomowo przejąć tę sprawę.";
  }
  if (caseItem.department === "service_it") {
    return "Potwierdź klienta i urządzenie, a następnie utwórz powiązane zlecenie MS.";
  }
  if (!isCurrentOwner) {
    return "Sprawa jest prowadzona przez innego pracownika.";
  }
  return "Dodaj notatkę, wykonaj kontakt i zapisz wynik rozmowy.";
}

function openCaseDialog(caseId) {
  const caseItem = crmCases.find((item) => item.id === caseId);
  if (!caseItem) {
    return;
  }
  activeCaseId = caseId;
  document.getElementById("crm-dialog-kicker").textContent = `${caseItem.id} · ${
    CRM_DEPARTMENT_LABELS[caseItem.department]
  }`;
  document.getElementById("crm-dialog-title").textContent = caseItem.subject;
  document.getElementById("crm-dialog-body").innerHTML = caseDetailMarkup(caseItem);
  const dialog = document.getElementById("crm-case-dialog");
  if (!dialog.open) {
    dialog.showModal();
  }
}

function refreshOpenCaseDialog() {
  if (!activeCaseId || !document.getElementById("crm-case-dialog").open) {
    return;
  }
  openCaseDialog(activeCaseId);
}

function addTimeline(caseItem, type, title, text) {
  caseItem.timeline.push({
    type,
    title,
    text,
    time: "przed chwilą",
  });
  caseItem.updatedAt = new Date().toISOString();
}

async function handleCaseAction(action) {
  const caseItem = crmCases.find((item) => item.id === activeCaseId);
  if (!caseItem) {
    return;
  }
  if (action === "transfer" && caseItem.department === "service_it") {
    closeDialog(document.getElementById("crm-case-dialog"));
    openMsDialog(caseItem.id);
    return;
  }
  if (action === "note") {
    document.getElementById("crm-note-text").value = "";
    document.getElementById("crm-note-dialog").showModal();
    return;
  }
  try {
    if (action === "claim" && !caseItem.owner && caseItem.department !== "sales") {
      await persistCaseAction(caseItem.id, "claim");
      showFeedback(`Przejęto sprawę ${caseItem.id}.`);
    } else if (action === "close") {
      await persistCaseAction(caseItem.id, "close");
      showFeedback(`Zakończono sprawę ${caseItem.id}.`);
    }
    renderCurrentView();
    refreshOpenCaseDialog();
  } catch (error) {
    showFeedback(error.message);
  }
}

function showFeedback(message) {
  const feedback = document.getElementById("crm-feedback");
  feedback.textContent = message;
  feedback.hidden = false;
  window.clearTimeout(showFeedback.timeoutId);
  showFeedback.timeoutId = window.setTimeout(() => {
    feedback.hidden = true;
  }, 5000);
}

function closeDialog(dialog) {
  if (dialog?.open) {
    dialog.close();
  }
}

function openNewCaseDialog() {
  const departmentSelect = document.getElementById("crm-new-case-department");
  if (Object.hasOwn(CRM_DEPARTMENT_LABELS, currentView)) {
    departmentSelect.value = currentView;
  }
  const dialog = document.getElementById("crm-new-case-dialog");
  if (!dialog.open) {
    dialog.showModal();
  }
}

async function createDemoCaseFromForm() {
  const source = document.getElementById("crm-new-case-source").value;
  const department = document.getElementById("crm-new-case-department").value;
  const priority = document.getElementById("crm-new-case-priority").value;
  try {
    const response = await crmApi("/cases", {
      method: "POST",
      body: JSON.stringify({
        external_ref: `manual-${Date.now()}-${crypto.randomUUID()}`,
        channel: source === "web_form" ? "form" : source === "web_chat" ? "chat" : "manual",
        queue: department,
        category: department,
        priority: priority === "medium" ? "normal" : priority,
        company_name: document.getElementById("crm-new-case-company").value.trim(),
        contact_name: document.getElementById("crm-new-case-contact").value.trim(),
        phone: document.getElementById("crm-new-case-phone").value.trim() || null,
        email: document.getElementById("crm-new-case-email").value.trim() || null,
        subject: document.getElementById("crm-new-case-subject").value.trim(),
        message: document.getElementById("crm-new-case-message").value.trim(),
        source_detail: "Sprawa utworzona ręcznie w LAB",
        is_lab: true,
        metadata: { declared_operator_id: currentOperatorId() },
      }),
    });
    const caseItem = mapApiCase(response.case);
    crmCases.unshift(caseItem);
    currentView = "inbox";
    currentFilter = "open";
    document.getElementById("crm-new-case-form").reset();
    closeDialog(document.getElementById("crm-new-case-dialog"));
    renderCurrentView();
    showFeedback(`Dodano sprawę ${caseItem.id} do ctip_test.`);
    openCaseDialog(caseItem.id);
  } catch (error) {
    showFeedback(error.message);
  }
}

function activeActionCase() {
  return crmCases.find((caseItem) => caseItem.id === actionCaseId) || null;
}

function openTransferDialog(caseId) {
  const caseItem = crmCases.find((item) => item.id === caseId);
  if (!caseItem) {
    return;
  }
  actionCaseId = caseId;
  document.getElementById("crm-transfer-users").innerHTML = crmOperators.map(
    (user, index) => `
      <label class="crm-transfer-user">
        <input type="radio" name="crm-transfer-user" value="${user.id}" ${
          index === 0 ? "checked" : ""
        }>
        <span class="crm-transfer-avatar">${escapeHtml(
          user.name
            .split(/\s+/)
            .slice(0, 2)
            .map((part) => part.slice(0, 1))
            .join("")
        )}</span>
        <span>
          <strong>${escapeHtml(user.name)}</strong>
          <small>${escapeHtml(user.role)}</small>
          <em>${user.phone_available ? "SMS dostępny" : "Brak telefonu"} · ${
            user.email ? "e-mail dostępny" : "brak e-maila"
          }</em>
        </span>
      </label>
    `
  ).join("");
  const caseLink = `${window.location.origin}/crm?case=${encodeURIComponent(caseItem.id)}`;
  document.getElementById("crm-transfer-preview").innerHTML = `
    <span>POWIADOMIENIA ZABLOKOWANE W LAB</span>
    <strong>${escapeHtml(caseItem.id)} · ${escapeHtml(caseItem.subject)}</strong>
    <p>Przypisanie zapisze się w ctip_test, ale SMS i e-mail nie zostaną wysłane:</p>
    <code>${escapeHtml(caseLink)}</code>
  `;
  document.getElementById("crm-transfer-dialog").showModal();
}

function openMsDialog(caseId) {
  const caseItem = crmCases.find((item) => item.id === caseId);
  if (!caseItem) {
    return;
  }
  actionCaseId = caseId;
  document.getElementById("crm-ms-summary").innerHTML = `
    <span>SPRAWA ${escapeHtml(caseItem.id)}</span>
    <h3>${escapeHtml(caseItem.company)}</h3>
    <p><strong>Kontakt:</strong> ${escapeHtml(caseItem.contact)} · ${escapeHtml(
      caseItem.phone
    )}</p>
    <p><strong>Urządzenie:</strong> ${escapeHtml(
      caseItem.device || "Nie wskazano"
    )} · ${escapeHtml(caseItem.serial || "brak S/N")}</p>
    <p><strong>Opis:</strong> ${escapeHtml(caseItem.message)}</p>
  `;
  document.getElementById("crm-ms-dialog").showModal();
}

function openMeterDialog(caseId) {
  const caseItem = crmCases.find((item) => item.id === caseId);
  if (!caseItem) {
    return;
  }
  actionCaseId = caseId;
  const previous = caseItem.previousMeters || {};
  document.getElementById("crm-meter-device").innerHTML = `
    <span>${escapeHtml(caseItem.id)} · POPRZEDNI ODCZYT ${escapeHtml(
      previous.date || "brak daty"
    )}</span>
    <h3>${escapeHtml(caseItem.company)}</h3>
    <p>${escapeHtml(caseItem.device || "Nie wskazano urządzenia")} · ${escapeHtml(
      caseItem.serial || "brak S/N"
    )}</p>
  `;
  document.getElementById("crm-meter-prev-bw").textContent = previous.bw ?? "brak";
  document.getElementById("crm-meter-prev-color").textContent =
    previous.color ?? "brak";
  document.getElementById("crm-meter-prev-scan").textContent = previous.scan ?? "brak";
  ["bw", "color", "scan"].forEach((meterType) => {
    document.getElementById(`crm-meter-new-${meterType}`).value = "";
  });
  document.getElementById("crm-meter-error").hidden = true;
  document.getElementById("crm-meter-dialog").showModal();
}

async function handleDispatchAction(action, caseId) {
  const caseItem = crmCases.find((item) => item.id === caseId);
  if (!caseItem) {
    return;
  }
  dispatcherFocusCaseId = caseId;
  if (action === "transfer") {
    openTransferDialog(caseId);
    return;
  }
  if (action === "ms" && caseItem.department === "service_it") {
    openMsDialog(caseId);
    return;
  }
  if (action === "meters" && isMeterCase(caseItem)) {
    openMeterDialog(caseId);
    return;
  }
  if (action === "close") {
    try {
      await persistCaseAction(caseItem.id, "close");
      renderCurrentView();
      showFeedback(`Zakończono sprawę ${caseItem.id}. Autoarchiwizacja nastąpi po 30 dniach.`);
    } catch (error) {
      showFeedback(error.message);
    }
  }
}

function switchView(view) {
  if (!Object.hasOwn(CRM_VIEW_CONFIG, view)) {
    return;
  }
  currentView = view;
  currentFilter = view === "archive" ? "all" : "open";
  if (Object.hasOwn(CRM_DEPARTMENT_LABELS, view)) {
    currentWorkspaceMode = "primary";
  }
  renderCurrentView();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function bindGlobalEvents() {
  document.addEventListener("click", (event) => {
    const viewButton = event.target.closest("[data-view]");
    if (viewButton) {
      switchView(viewButton.dataset.view);
      return;
    }
    const viewLink = event.target.closest("[data-view-link]");
    if (viewLink) {
      switchView(viewLink.dataset.viewLink);
      return;
    }
    const filterButton = event.target.closest("[data-filter]");
    if (filterButton) {
      currentFilter = filterButton.dataset.filter;
      renderCurrentView();
      return;
    }
    const workspaceModeButton = event.target.closest("[data-workspace-mode]");
    if (workspaceModeButton) {
      currentWorkspaceMode = workspaceModeButton.dataset.workspaceMode;
      renderCurrentView();
      return;
    }
    const workspaceFocusButton = event.target.closest("[data-workspace-focus]");
    if (workspaceFocusButton) {
      dispatcherFocusCaseId = workspaceFocusButton.dataset.workspaceFocus;
      renderCurrentView();
      return;
    }
    const dispatchActionButton = event.target.closest("[data-dispatch-action]");
    if (dispatchActionButton) {
      handleDispatchAction(
        dispatchActionButton.dataset.dispatchAction,
        dispatchActionButton.dataset.dispatchCaseId
      );
      return;
    }
    const caseButton = event.target.closest("[data-case-id]");
    if (caseButton) {
      openCaseDialog(caseButton.dataset.caseId);
      return;
    }
    const caseAction = event.target.closest("[data-case-action]");
    if (caseAction && !caseAction.disabled) {
      handleCaseAction(caseAction.dataset.caseAction);
      return;
    }
    if (event.target.closest("[data-close-dialog]")) {
      closeDialog(document.getElementById("crm-case-dialog"));
      return;
    }
    if (event.target.closest("[data-close-note]")) {
      closeDialog(document.getElementById("crm-note-dialog"));
      return;
    }
    if (event.target.closest("[data-close-new-case]")) {
      closeDialog(document.getElementById("crm-new-case-dialog"));
      return;
    }
    if (event.target.closest("[data-close-action-dialog]")) {
      closeDialog(event.target.closest("dialog"));
      return;
    }
    if (event.target.closest("[data-demo-new-case]")) {
      openNewCaseDialog();
    }
  });

  document.getElementById("crm-global-search").addEventListener("input", (event) => {
    currentSearch = event.target.value.trim().toLocaleLowerCase("pl");
    renderCurrentView();
  });

  document.getElementById("crm-reset-demo").addEventListener("click", async () => {
    const accepted = window.confirm(
      "Reset usunie wszystkie sprawy i zdarzenia oznaczone jako LAB. Dane produkcyjne nie zostaną naruszone. Kontynuować?"
    );
    if (!accepted) {
      return;
    }
    try {
      const result = await crmApi("/lab/reset", {
        method: "POST",
        body: JSON.stringify({
          declared_operator_id: currentOperatorId(),
          reason: "Ręczny reset izolowanego laboratorium CRM",
        }),
      });
      crmCases = [];
      currentFilter = currentView === "archive" ? "all" : "open";
      renderCurrentView();
      closeDialog(document.getElementById("crm-case-dialog"));
      showFeedback(
        `Usunięto ${result.deleted_cases} spraw LAB i ${result.deleted_events} zdarzeń.`
      );
    } catch (error) {
      showFeedback(error.message);
    }
  });

  document.getElementById("crm-note-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const noteText = document.getElementById("crm-note-text").value.trim();
    const caseItem = crmCases.find((item) => item.id === activeCaseId);
    if (!caseItem || noteText.length < 3) {
      return;
    }
    try {
      await persistCaseAction(caseItem.id, "note", { note: noteText });
      closeDialog(document.getElementById("crm-note-dialog"));
      renderCurrentView();
      refreshOpenCaseDialog();
      showFeedback(`Dodano notatkę do sprawy ${caseItem.id}.`);
    } catch (error) {
      showFeedback(error.message);
    }
  });

  document.getElementById("crm-new-case-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!event.currentTarget.reportValidity()) {
      return;
    }
    void createDemoCaseFromForm();
  });

  document.getElementById("crm-transfer-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const caseItem = activeActionCase();
    const selectedUserId = Number(
      new FormData(event.currentTarget).get("crm-transfer-user")
    );
    const selectedUser = crmOperators.find((user) => user.id === selectedUserId);
    if (!caseItem || !selectedUser) {
      return;
    }
    try {
      await persistCaseAction(caseItem.id, "assign", {
        owner_user_id: selectedUser.id,
      });
      closeDialog(document.getElementById("crm-transfer-dialog"));
      renderCurrentView();
      showFeedback(
        `Przekazano sprawę ${caseItem.id} do ${selectedUser.name}. SMS i e-mail pozostały zablokowane.`
      );
    } catch (error) {
      showFeedback(error.message);
    }
  });

  document.getElementById("crm-ms-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const caseItem = activeActionCase();
    if (!caseItem || caseItem.department !== "service_it") {
      return;
    }
    try {
      await persistCaseAction(caseItem.id, "transfer_ms");
      closeDialog(document.getElementById("crm-ms-dialog"));
      closeDialog(document.getElementById("crm-case-dialog"));
      renderCurrentView();
      showFeedback(
        `Zapisano testowe przekazanie ${caseItem.id}. Firebird nie został zmieniony.`
      );
    } catch (error) {
      showFeedback(error.message);
    }
  });

  document.getElementById("crm-meter-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const caseItem = activeActionCase();
    if (!caseItem || !isMeterCase(caseItem)) {
      return;
    }
    const previous = caseItem.previousMeters || {};
    const values = {};
    ["bw", "color", "scan"].forEach((meterType) => {
      const rawValue = document.getElementById(`crm-meter-new-${meterType}`).value;
      values[meterType] = rawValue === "" ? null : Number(rawValue);
    });
    const error = document.getElementById("crm-meter-error");
    if (Object.values(values).every((value) => value === null)) {
      error.textContent = "Wpisz co najmniej jeden nowy odczyt.";
      error.hidden = false;
      return;
    }
    const invalidType = ["bw", "color", "scan"].find(
      (meterType) =>
        values[meterType] !== null &&
        previous[meterType] !== undefined &&
        values[meterType] < previous[meterType]
    );
    if (invalidType) {
      const labels = { bw: "B/W", color: "Kolor", scan: "Skan" };
      error.textContent = `Nowy odczyt ${labels[invalidType]} jest niższy od poprzedniego stanu.`;
      error.hidden = false;
      return;
    }
    try {
      await persistCaseAction(caseItem.id, "meter_update", { meters: values });
      closeDialog(document.getElementById("crm-meter-dialog"));
      renderCurrentView();
      showFeedback(
        `Zapisano testowe liczniki sprawy ${caseItem.id}. Firebird nie został zmieniony.`
      );
    } catch (apiError) {
      error.textContent = apiError.message;
      error.hidden = false;
    }
  });

  document.getElementById("crm-operator-select").addEventListener("change", (event) => {
    window.localStorage?.setItem("crm-declared-operator-id", event.target.value);
    applyDeclaredOperator();
    renderCurrentView();
  });

  document.getElementById("crm-user-button").addEventListener("click", () => {
    const menu = document.getElementById("crm-user-menu");
    menu.hidden = !menu.hidden;
  });

  document.getElementById("crm-logout").addEventListener("click", () => {
    clearCrmToken();
    window.location.assign("/");
  });

  document.getElementById("crm-notifications").addEventListener("click", () => {
    showFeedback("Powiadomienia klienta są wyłączone w izolowanym LAB.");
  });

  document.getElementById("crm-case-dialog").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) {
      closeDialog(event.currentTarget);
    }
  });

  document.getElementById("crm-new-case-dialog").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) {
      closeDialog(event.currentTarget);
    }
  });

  ["crm-transfer-dialog", "crm-ms-dialog", "crm-meter-dialog"].forEach((dialogId) => {
    document.getElementById(dialogId).addEventListener("click", (event) => {
      if (event.target === event.currentTarget) {
        closeDialog(event.currentTarget);
      }
    });
  });

  document.addEventListener("dragstart", (event) => {
    const card = event.target.closest("[data-drag-case-id]");
    if (!card) {
      return;
    }
    draggedCaseId = card.dataset.dragCaseId;
    card.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", draggedCaseId);
  });

  document.addEventListener("dragover", (event) => {
    const column = event.target.closest("[data-sales-owner]");
    if (!column) {
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    column.classList.add("drag-over");
  });

  document.addEventListener("dragleave", (event) => {
    const column = event.target.closest("[data-sales-owner]");
    if (column && !column.contains(event.relatedTarget)) {
      column.classList.remove("drag-over");
    }
  });

  document.addEventListener("drop", async (event) => {
    const column = event.target.closest("[data-sales-owner]");
    if (!column) {
      return;
    }
    event.preventDefault();
    const caseId = event.dataTransfer.getData("text/plain") || draggedCaseId;
    const caseItem = crmCases.find((item) => item.id === caseId);
    if (!caseItem) {
      return;
    }
    const nextOwner = column.dataset.salesOwner || null;
    try {
      await persistCaseAction(
        caseItem.id,
        nextOwner ? "assign" : "unassign",
        nextOwner ? { owner_name: nextOwner } : {}
      );
      column.classList.remove("drag-over");
      draggedCaseId = null;
      renderCurrentView();
      showFeedback(
        nextOwner
          ? `Sprawę ${caseItem.id} przejął ${nextOwner}.`
          : `Sprawę ${caseItem.id} cofnięto do kolumny Nowe.`
      );
    } catch (error) {
      showFeedback(error.message);
    }
  });

  document.addEventListener("dragend", (event) => {
    event.target.closest("[data-drag-case-id]")?.classList.remove("dragging");
    document
      .querySelectorAll("[data-sales-owner].drag-over")
      .forEach((column) => column.classList.remove("drag-over"));
    draggedCaseId = null;
  });
}

async function loadCurrentUser(token) {
  const response = await fetch("/auth/me", {
    headers: { "X-Admin-Session": token },
  });
  if (!response.ok) {
    throw new Error("Sesja wygasła.");
  }
  const user = await response.json();
  const fullName = [user.first_name, user.last_name].filter(Boolean).join(" ");
  const displayName = fullName || user.email || "Bieżący użytkownik";
  const initials = fullName
    ? fullName
        .split(/\s+/)
        .slice(0, 2)
        .map((part) => part.slice(0, 1).toUpperCase())
        .join("")
    : String(user.email || "BU")
        .slice(0, 2)
        .toUpperCase();
  currentUser = {
    name: displayName,
    initials,
    role: user.role === "admin" ? "Administrator" : "Centrum Obsługi",
  };
  crmCases.forEach((caseItem) => {
    if (caseItem.owner === "Bieżący użytkownik") {
      caseItem.owner = currentUser.name;
    }
  });
  document.getElementById("crm-user-name").textContent = currentUser.name;
  document.getElementById("crm-user-avatar").textContent = currentUser.initials;
  document.getElementById("crm-user-role").textContent = currentUser.role;
}

function openCaseFromCurrentUrl() {
  const requestedCaseId = new URLSearchParams(window.location.search).get("case");
  const caseItem = crmCases.find((item) => item.id === requestedCaseId);
  if (!caseItem) {
    return;
  }
  currentView = caseItem.archivedAt ? "archive" : caseItem.department;
  dispatcherFocusCaseId = caseItem.id;
  renderCurrentView();
  openCaseDialog(caseItem.id);
}

async function initializeCrm() {
  const publicPrototypeMode = document.body.dataset.crmPublicPrototype === "true";
  if (publicPrototypeMode) {
    document.getElementById("crm-choice-link").hidden = true;
    document.getElementById("crm-menu-choice-link").hidden = true;
    document.getElementById("crm-logout").hidden = true;
    document.getElementById("crm-public-menu-info").hidden = false;
  } else {
    const token = readCrmToken();
    if (!token) {
      window.location.replace("/");
      return;
    }
    try {
      await loadCurrentUser(token);
    } catch (_error) {
      clearCrmToken();
      window.location.replace("/");
      return;
    }
  }
  try {
    await loadCrmOperators();
    await loadCrmCases();
  } catch (error) {
    document.querySelector("#crm-loading p").textContent = error.message;
    document.querySelector("#crm-loading span").hidden = true;
    return;
  }
  bindGlobalEvents();
  renderCurrentView();
  document.getElementById("crm-loading").hidden = true;
  document.getElementById("crm-app").hidden = false;
  openCaseFromCurrentUrl();
}

window.addEventListener("DOMContentLoaded", initializeCrm);
