# Zgrywka MXB

Program do porządkowania nagrań z mikroportów. Słucha początku każdego pliku audio,
rozpoznaje mowę po polsku, odnajduje **imię i nazwisko** osoby oraz **nazwę odcinka**,
a potem tworzy foldery, przenosi pliki i nadaje im numerowane nazwy.

Działa na **Windows, macOS i Linuksie**. Rozpoznawanie mowy odbywa się w całości na Twoim
komputerze: nagrania nigdzie nie są wysyłane. Internet jest potrzebny tylko raz, do pobrania
bibliotek i modelu mowy.

---

## Jak uruchomić

| System  | Co kliknąć dwukrotnie |
|---------|-----------------------|
| Windows | `Zgrywka-Windows.bat` |
| macOS   | `Zgrywka-Mac.command` (za pierwszym razem: prawy przycisk → **Otwórz**) |
| Linux   | `Zgrywka-Linux.sh` (albo „Zgrywka MXB” w menu aplikacji po pierwszym uruchomieniu) |

Program jest chroniony **hasłem**: pyta o nie przy pierwszym uruchomieniu i potem raz na 7 dni
(bez hasła nie działa). Hasło zna osoba, która przekazała Ci program.

**Pierwsze uruchomienie** trwa kilka minut, bo program sam instaluje potrzebne biblioteki
(ok. 700 MB, w folderze `.venv` obok programu). Przy pierwszej analizie pobiera jeszcze
model rozpoznawania mowy (ok. 1,6 GB, do folderu `modele`). Później wszystko startuje od razu
i działa bez internetu.

Potrzebny jest **Python 3.10–3.14**:
- **Windows**: pobierz z <https://www.python.org/downloads/windows/> i przy instalacji zaznacz
  **„Add python.exe to PATH”**.
- **macOS**: pobierz z <https://www.python.org/downloads/macos/>.
- **Linux**: zwykle już jest; w razie potrzeby `sudo apt install python3 python3-venv`.

---

## Jak używać

1. Wpisz **nazwę odcinka**, np. `tratwy`. **Kilka odcinków** oddziel spacją, np. `dom tratwy las`.
   Program sam rozpozna, który z nich pada w nagraniu. Nazwę z kilku słów wpisz w cudzysłowie,
   np. `"stara chata" dom`.
2. Sprawdź listę **Osoby** po prawej stronie okna:
   - **„Dodaj”** dopisuje nową osobę,
   - **dwuklik** (albo **„Edytuj”**) otwiera okno osoby: imię i nazwisko oraz **alternatywy**,
     czyli zdrobnienia i przezwiska, np. *Weronika Chrabąszcz → Wera, Nika, Werka*. Wpisz
     alternatywę, naciśnij **Enter**, wpisz kolejną, **Enter**… a na koniec kliknij **„Zapisz”**,
   - **„Usuń”** usuwa zaznaczone osoby.
3. **Przeciągnij folder** z nagraniami do okna programu (albo kliknij ramkę i wybierz folder).
   Analiza ruszy sama.

   Jeśli wszystkie nagrania w folderze są z jednego odcinka, zaznacz **„sortowanie tylko po imieniu
   i nazwisku”**: program rozpozna tylko osobę, a odcinek weźmie od razu z pola „Nazwa odcinka”
   (nie musi paść w nagraniu). Osobę rozpoznaje tak samo jak zwykle: po imieniu i nazwisku, po samym
   imieniu albo samym nazwisku, także z przekręconymi literami.
4. Sprawdź tabelę. Kolory oznaczają:
   - 🟩 **zielony**: rozpoznano osobę i odcinek, więc plik trafi do folderu odcinka i osoby;
   - 🟨 **żółty**: rozpoznano tylko osobę albo tylko odcinek, więc plik dostanie tylko nową nazwę;
   - 🟥 **czerwony**: nic nie rozpoznano;
   - ⬜ **szary**: pliku nie da się odczytać (zostanie bez zmian).

   Kolumna **„Co usłyszano”** pokazuje, co program zrozumiał z nagrania. Jeśli coś się nie zgadza,
   kliknij komórkę **„Osoba”** (albo „Rozpoznanie”) i wybierz osobę z listy **albo wpisz własną**:
   nowa osoba sama dopisze się do listy. Tak samo w kolumnie **„Odcinek”** wybierzesz jeden
   z wpisanych odcinków albo wpiszesz własny. Prawy przycisk myszy zmienia kilka zaznaczonych
   wierszy naraz.

   **Odsłuch**: kliknij **▶** przy nagraniu (albo zrób dwuklik, albo wciśnij spację). Na dole okna
   jest odtwarzacz jak w VLC: suwak do przewijania (także w godzinnych nagraniach), pauza, stop,
   **10 s** w tył i w przód, **„od początku”** oraz **poprzednie** i **następne** nagranie.
5. Kliknij **„Uporządkuj pliki”**.
6. Jeśli coś poszło nie tak, kliknij **„Cofnij ostatnie”**: wszystkie pliki wrócą na swoje miejsca.

### Zasady nazw

| Co rozpoznano          | Co się dzieje z plikiem |
|------------------------|-------------------------|
| osoba **i** odcinek    | przeniesienie do folderu odcinka, a w nim do folderu osoby: `dom/Weronika Chrabąszcz „dom”/Weronika Chrabąszcz „dom” 1.wav`, `… 2.wav`, … |
| tylko osoba            | zmiana nazwy w miejscu: `Marcel Gawroński „!!BRAK NAZWY FILMU!!” 1.wav` |
| tylko odcinek          | zmiana nazwy w miejscu: `NIE WIEM KTO „dom” 1.wav` |
| nic                    | zmiana nazwy w miejscu: `nie zidentyfikowany 1.wav` |

- Numeracja idzie według kolejności oryginalnych nazw plików i **kontynuuje istniejące numery**.
  Gdy po kolejnej zgrywce wrzucisz nowe pliki do tego samego folderu, dostaną dalsze numery.
- Program **nigdy nie nadpisuje** istniejących plików.
- Ponowna analiza tego samego folderu niczego nie psuje: program pamięta, co już jest w nazwach
  plików (także Twoje ręczne poprawki), i może tylko dopisać nowe informacje.
- **Cudzysłowy**: Windows oraz dyski exFAT/FAT32 (karty pamięci, dyski przenośne) nie pozwalają
  na znak `"` w nazwach plików, dlatego program używa polskich cudzysłowów `„tratwy”`. Na macOS
  i Linuksie można to zmienić w Ustawieniach.

### Jak program rozpoznaje osoby

Program porównuje **litery**, więc nie trzeba wpisywać typowych pomyłek ani odmiany. Wystarczy imię
i nazwisko na liście. **Alternatywy** (okno osoby) przydają się tylko na zupełnie inne formy, których
z liter nie da się zgadnąć: zdrobnienia i przezwiska (*Wera, Nika, Werka*) albo pełne imię
(*Radosław Chaberko* dla Radka). Jednowyrazowa alternatywa działa sama („Wera, dom”) i razem
z nazwiskiem („Wera Chrabąszcz”).

- **Przekręcone litery i polska wymowa**: „Radek **H**aberko” = Radek Chaberko,
  „Weronika Hrabążcz” = Weronika Chrabąszcz (ch = h, rz = ż, ó = u, w ≈ f, d ≈ t…).
- **Odmiana i zdrobnienia**: „mikroport **Marcela Gawrońskiego**”, „**Radka**”, „**Michałek**”,
  „tratwa” = „tratwy”.
- **Imię i nazwisko obok siebie**: wystarczy, że zgadza się mniej więcej **co druga litera**
  każdego z nich, np. „Marcel **Woński**” = Marcel Gawroński.
- **Samo imię albo samo nazwisko** wystarczy, jeśli nikt inny z listy go nie ma. Gdy pasuje
  tylko część liter (np. zgubiona litera: „**adek**” = Radek), program przyjmuje to tylko wtedy,
  gdy słowo pada **tuż przy nazwie odcinka** („adek, tratwy”), bo tak wygląda przedstawianie się
  na nagraniu. Takie wiersze są oznaczone dopiskiem *„część liter – sprawdź”*.
- **Nazwa odcinka** może mieć jedną pomyłkę w literach („dratwy”, „tatwy”), a tuż przy imieniu
  i nazwisku także więcej („Weronika Hrabążcz, **tracy**”).
- **Nazwa z kilku słów** pasuje też w innej kolejności i formie, jeśli słowa padną obok siebie:
  odcinek *dom strachu* rozpozna w „w najstraszniejszym **domu strachu**” i w „**straszny dom**”.
- **Początek nagrania ma pierwszeństwo**: jeśli padną dwa tytuły (np. „dom” w 5. sekundzie,
  a inny w 10.), program bierze ten wcześniejszy. Tak samo przy dwóch osobach.
- Żeby nie było przypadkowych trafień, program **nie bierze zwykłych słów ani popularnych imion
  i nazwisk za kogoś z listy**: „miał” to nie Michał, „radio” to nie Radek, „Kowalska” to nie
  Kozłowska. Samo „Chaberko” pasuje i do Radka, i do Michała, więc wtedy też nie zgaduje.

**Najlepiej działa**, gdy na początku nagrania pada wyraźnie: *„Marcel Gawroński, tratwy”*.

---

## Ustawienia

- **Osoby** (panel po prawej w głównym oknie): dokładnie tak, jak tu wpiszesz imię i nazwisko,
  tak będą nazwane foldery i pliki. Lista razem z alternatywami zapisuje się sama.
- **Zakładka „Ustawienia”**:
  - **Model**: „Dokładny (large-v3-turbo)” jest zalecany. „Szybki (small)” działa ok. 2–3 razy
    szybciej na słabszych komputerach, kosztem dokładności.
  - **Ile sekund analizować**: domyślnie 30 s od początku nagrania.
  - **Karta graficzna NVIDIA**: jeśli ją masz (Windows/Linux), przycisk **„Zainstaluj
    przyspieszenie GPU”** doinstaluje biblioteki CUDA (ok. 2,5 GB), co przyspiesza analizę
    nawet 10 razy. Bez tego program działa na procesorze.
  - **Porządkuj od razu po analizie**: tryb w pełni automatyczny, bez podglądu.
- **Przycinka mikroportów** (pole w głównym oknie): funkcja w przygotowaniu. Docelowo rozpozna,
  że plik, który mikroport zaczął sekundę po przecięciu nagrania (np. co 30 minut), jest ciągiem
  dalszym poprzedniego, i podpisze go tak samo.
  - **Raport CSV**: zapis tabeli „stara nazwa → nowa nazwa” w folderze nagrań.

---

## Tryb tekstowy (dla zaawansowanych)

```
.venv/bin/python -m zgrywka /ścieżka/do/folderu --odcinek tratwy            # tylko podgląd
.venv/bin/python -m zgrywka /ścieżka/do/folderu --odcinek tratwy --zastosuj # podgląd i porządkowanie
```
Kilka odcinków: `--odcinek "dom tratwy las"`.
Na Windows zamiast `.venv/bin/python` użyj `.venv\Scripts\python.exe`. Opcje: `--model small`,
`--urzadzenie cpu`, `--sekundy 45`, `--podfoldery`.

---

## Gdy coś nie działa

- **Okno się nie otwiera (Linux)**: zainstaluj `sudo apt install libxcb-cursor0`.
- **Błąd bibliotek po aktualizacji systemu lub Pythona**: usuń folder `.venv` obok programu
  i uruchom plik startowy ponownie.
- **macOS: „nie można otworzyć, bo pochodzi od niezidentyfikowanego dewelopera”**: prawy przycisk
  na `Zgrywka-Mac.command` → **Otwórz**. Jeśli plik nie chce się uruchomić, wpisz w Terminalu
  `chmod +x Zgrywka-Mac.command`.
- **Dziennik błędów** (warto go dołączyć przy zgłaszaniu problemu):
  - Windows: `%LOCALAPPDATA%\ZgrywkaMXB\zgrywka.log`
  - macOS: `~/Library/Application Support/ZgrywkaMXB/zgrywka.log`
  - Linux: `~/.local/share/ZgrywkaMXB/zgrywka.log`

Ustawienia, lista osób i historia zmian (do cofania) są w tym samym folderze co dziennik.
Żeby całkowicie usunąć program, usuń jego folder (razem z `.venv` i `modele`) oraz folder `ZgrywkaMXB`
z powyższej lokalizacji.

---

## Dla programistów

```
zgrywka/
  matching.py   rozmyte dopasowanie osób (z alternatywami) i odcinków (polska wymowa, odmiana, literówki)
  analyzer.py   transkrypcja początku nagrania (z podpowiedzią i drugą próbą) + decyzja
  asr.py        faster-whisper: pobieranie modelu, bezpieczny wybór CPU/GPU
  audio.py      dekodowanie tylko początku pliku (PyAV), wyrównanie poziomu
  naming.py     nazwy, foldery odcinków, numeracja, przenoszenie z dziennikiem i cofaniem
  player.py     wbudowany odtwarzacz (PyAV + miniaudio) i jego pasek
  results.py    tabela wyników i edycja osoby/odcinka
  people.py     panel osób i okno osoby z alternatywami
  lock.py       hasło raz na 7 dni (w kodzie tylko skrót PBKDF2)
  ui.py         okno główne (PySide6 / Qt)
  worker.py     analiza w osobnym wątku
  cli.py        tryb tekstowy
tests/          testy (pytest)
```
Testy: `.venv/bin/python -m pip install pytest && .venv/bin/python -m pytest tests`
