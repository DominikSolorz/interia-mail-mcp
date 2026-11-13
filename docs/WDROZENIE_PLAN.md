# Plan wdrożenia Interia Mail Cleaner

## Cel

Panel ma porządkować skrzynkę Interia podobnie do Clean Email, ale bez automatycznego kasowania ważnych wiadomości. Każda decyzja ma być widoczna, odwracalna i zatwierdzana przez użytkownika.

## Zasady bezpieczeństwa

1. Skanowanie jest tylko odczytem.
2. Kosz i Spam są wyłączone ze skanowania domyślnego.
3. Wiadomości urzędowe, prawne, medyczne, zawodowe, finansowe, rodzinne i z załącznikami trafiają do grupy chronionej.
4. Wypisanie z newslettera jest osobną akcją. Nie oznacza usunięcia wcześniejszych wiadomości.
5. Usunięcie przenosi wiadomość do Kosza. Usunięcie trwałe wymaga osobnego widoku, listy identyfikatorów i wpisania `USUŃ TRWALE`.
6. Nie wykonujemy operacji zbiorczej bez podglądu liczby wiadomości, nadawców i powodów klasyfikacji.

## Etapy

### Etap 1: stabilizacja

Kopia konfiguracji i cache, sprawdzenie połączenia IMAP/SMTP, odczyt folderów oraz test na porcie 8766. Bez kasowania wiadomości.

### Etap 2: pełny skan

Skanowanie folderów Odebrane, Wysłane, Kopie robocze i folderów użytkownika. Dla każdej wiadomości zapisujemy nadawcę, domenę, temat, datę, status przeczytania, załączniki, podpis, linki i folder źródłowy.

### Etap 3: klasyfikacja

Rozdzielamy: newsletter, reklama, spam lub phishing, sprawa urzędowa, sprawa prawna, finanse i płatności, praca, zakupy i dostawy, korespondencja prywatna oraz niepewne. Słowa „kredyt” i „pożyczka” nie wystarczają do usunięcia. Liczy się kontekst, nadawca, treść i załączniki.

### Etap 4: panel decyzji

Widok grupuje wiadomości po nadawcy i pokazuje liczbę wiadomości, datę ostatniej wiadomości, procent nieprzeczytanych, załączniki, kategorię i powód. Dostępne są osobne akcje: Podgląd, Wypisz, Przenieś do Kosza, Zablokuj nadawcę, Chroń nadawcę i Odłóż decyzję.

### Etap 5: foldery

Tworzymy strukturę: `CleanEmail/Do sprawdzenia`, `CleanEmail/Newslettery`, `CleanEmail/Reklamy`, `CleanEmail/Ważne`, `CleanEmail/Finanse`, `CleanEmail/Urzędy i prawo`, `CleanEmail/Praca`, `CleanEmail/Załączniki`. Foldery są tworzone tylko po pokazaniu podglądu.

### Etap 6: wykonanie

Najpierw tryb podglądu. Potem użytkownik zatwierdza konkretną grupę. Wypisanie wysyła żądanie do nadawcy i tworzy regułę blokującą nowe wiadomości. Usuwanie przenosi stare wiadomości do Kosza. Trwałe usuwanie jest wyłączone domyślnie.

### Etap 7: testy i publikacja

Testy na danych przykładowych, test folderów, test ochrony wiadomości z załącznikami, test wypisania bez usunięcia oraz test odwrócenia akcji. Dopiero po przejściu testów publikacja backendu i panelu.

## Kryterium gotowości

Użytkownik widzi pełną listę folderów, może podejrzeć każdą grupę, rozumie dlaczego wiadomość została zaklasyfikowana, może cofnąć przeniesienie do Kosza i nie ma automatycznego trwałego kasowania.
