1. Użytkownicy (wewnętrzne + kontakt)

Marcin Jarmuszkiewicz – Administrator, wew.105, marcin@ksero-partner.com.pl
, 888 565 299

Joanna Gostyńska – użytkownik standardowy, wew.101, rozliczenia@ksero-partner.com.pl
, 509 275 630

Michał Zbroszczyk – użytkownik standardowy, wew.104, michal@ksero-partner.com.pl

Agnieszka Gołęmbiewska – użytkownik standardowy, wew.103, agnieszkaf@ksero-partner.com.pl


2. Numery/rozszerzenia (skrót)

101 – CTS.IP „Serwis”

102 – CTS.IP „Umowy”

103 – SIP „Księgowość” (Agnieszka G.)

104 – SIP „Handel” (Michał Z.)

105 – SIP „Informatyk” (Marcin Jarmuszkiewicz)

107 – SIP „Agata K.”

109 – SIP „Kamil G.”

500 – „Kod SMS” (specjalny: używany do wysyłki SMS po wyborze 9 w IVR)

999 – 106-Bezprzewodowy

3, Wysyłak sms przez wybór w IVR
Drzewo IVR (wyciąg z ekranu)

Główne: Dzien_new (IVR)

[2] aplikacja_new (IVR) → akcja: odtwórz „serwis”; podmenu:

[3] instapp (IVR) → akcja: odtwórz „instalacjaapp”

[9] przejście do wew.500 (Kod SMS)

[1] (routing do wew.182 – handel_new / grupa rozdzwaniająca)

[9] ⇒ 500 jest wyzwalaczem ścieżki „SMS dla dzwoniącego”.

4. W pliku CTIP (1).pdf trzeba pobrac liste komunikatów i przerobic na łatwo czytelne wiadopmosci

Uwaga: dla naszych skryptów liczy się pierwszy RING na danym wewnętrznym – to po nim wnioskujemy, który klawisz/ścieżkę wybrał dzwoniący (mapowanie IVR robimy po docelowym wew.).
