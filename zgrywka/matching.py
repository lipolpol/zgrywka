"""Rozpoznawanie osób i nazwy odcinka w transkrypcji.

Nie ma tu żadnych ręcznie wpisanych "innych form" - wszystko robi algorytm porównujący litery:
- polska wymowa: ch = h, rz = ż, ó = u, głoski dźwięczne jak bezdźwięczne (w/f, d/t, ...),
- przekręcone, zgubione albo dodane litery ("Haberko", "adek", "Marcel Woński"),
- odmiana i zdrobnienia ("Marcela Gawrońskiego", "Radka", "Michałek"),
- słowa sklejone lub rozbite przez rozpoznawanie mowy ("Ma cudzińska").

Zasada: im luźniejsze dopasowanie liter, tym więcej musi się zgadzać poza nimi:
- imię i nazwisko obok siebie: wystarczy mniej więcej co druga litera każdego z nich,
- samo imię albo samo nazwisko z częścią liter: tylko tuż przy nazwie odcinka ("adek, tratwy"),
- nazwa odcinka z częścią liter: tylko tuż przy rozpoznanej osobie,
a zwykłe słowa i popularne imiona/nazwiska nigdy nie udają kogoś z listy
("miał" to nie Michał, "radio" to nie Radek, "Kowalska" to nie Kozłowska).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

from rapidfuzz.distance import LCSseq, Levenshtein

# Rodzaje dopasowania osoby.
FULL = "full"    # imię i nazwisko
FIRST = "first"  # samo imię
LAST = "last"    # samo nazwisko

NEAR_BEFORE, NEAR_AFTER = 4, 6  # okolica znalezionej nazwy odcinka/osoby, gdzie szukamy luźniej
PAIR_THRESHOLD = 0.65        # "imię nazwisko" obok siebie: wystarczy mniej więcej co druga litera
LOOSE_MARGIN = 0.1           # luźne dopasowanie musi wyraźnie wygrać z drugą osobą

_PL_TABLE = str.maketrans({
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
    "ó": "u", "ś": "s", "ź": "z", "ż": "z",
})
_DEVOICE = str.maketrans("bdgwz", "ptkfs")
_WORD_RE = re.compile(r"[^\W_]+")
_VOWELS = frozenset("aeiouy")
# Typowe polskie końcówki fleksyjne (Marcela, Gawrońskiego, Kozłowskiej, Weroniką...).
_RAW_INFLECTIONS = ("a", "ą", "e", "ę", "i", "o", "u", "y", "em", "om", "ie", "ów", "owi", "iem",
                    "iego", "iemu", "im", "ich", "imi", "ej", "iej", "ami", "ach")
# Końcówki obcinane przy porównywaniu nazwy odcinka ("tratwy" = "tratwa" = "tratwach").
_RAW_EPISODE_SUFFIXES = ("ami", "ach", "owi", "iem", "iego", "iemu", "iej", "ich", "om", "ów",
                         "em", "ie", "a", "ą", "e", "ę", "i", "o", "u", "y")
# Częste polskie słowa. Luźne dopasowanie nigdy nie bierze ich za imię ani nazwę odcinka:
# skoro rozpoznawanie mowy zapisało "miał" albo "radę", to właśnie to usłyszało.
_RAW_COMMON_WORDS = """
jest są był była było byli były będzie będą jestem jesteś jesteśmy jesteście być
mam masz ma mamy macie mają miał miała miało mieli miały mieć mieliśmy
mogę możesz może możemy możecie mogą mógł mogła mogło mogli móc
chcę chcesz chce chcemy chcecie chcą chciał chciała chcieli chcieć
wiem wiesz wie wiemy wiecie wiedzą wiedział wiedziała wiedzieć
robię robisz robi robimy robicie robią robił robiła robić zrobić zrobię zrobimy zrobił zrobiła
mówię mówisz mówi mówimy mówią mówił mówiła mówić powiedz powiedział powiedziała powiedzieć
idę idziesz idzie idziemy idziecie idą szedł szła iść chodź chodźmy chodzi chodzić
dam dasz daj dajcie damy dają dał dała dać radę rada rady radzie radą
widzę widzisz widzi widzimy widzą widział widziała widzieć
słyszę słyszysz słyszy słychać słucham słuchaj słuchajcie słuchać
patrz patrzę patrzy patrzcie czekaj czekam czekamy czekajcie
nagrywamy nagrywam nagrywa nagrywać nagranie nagrania nagraniu nagraj
sprawdzamy sprawdzam sprawdza sprawdzić sprawdzenie jedziemy jedzie jadę jechać
zaczynamy zaczynam zaczyna zacząć kończymy kończę koniec
dobrze dobra dobry dobre dobrego dobrej świetnie super okej okay
teraz potem zaraz jeszcze już tutaj tam gdzie kiedy jak tak nie no
dzień cześć hej siema proszę dziękuję dzięki przepraszam
wszystko wszyscy wszystkie ktoś coś nic nikt każdy każda
tylko bardzo chyba właśnie jakby czyli więc żeby ale albo oraz także też
który która które których tego tej tym temu ten ta to te tamten tamta tamto
mój moja moje twój twoja twoje nasz nasza nasze wasz wasza jego jej ich
on ona ono oni one ja ty my wy mnie mi ciebie tobie sobie się siebie nas was im
raz dwa trzy cztery pięć sześć siedem osiem dziewięć dziesięć
pierwszy pierwsza pierwsze drugi druga drugie trzeci trzecia trzecie
czas czasu rok roku dni dzisiaj dziś jutro wczoraj rano wieczór wieczorem
plan planie scena sceny scenę film filmu odcinek odcinka odcinku program programu
materiał materiału ujęcie ujęcia ujęciu dubel kadr kadru akcja akcji cięcie stop start
kamera kamerę kamery mikrofon mikroport mikroportu mikroporty dźwięk dźwięku poziom poziomu
próba próbę próby test testu bateria baterie baterii
woda wody wodę wodzie brzeg brzegu rzeka rzeki rzekę las lasu lesie dom domu domie
człowiek ludzie pan pani panie panu państwo chłopaki dziewczyny
mama tata brat siostra kolega koleżanka szef szefie praca pracy
gotowe gotowy gotowa gotowi światło światła telefon zdjęcie zdjęcia
szybko wolno głośno cicho bliżej dalej wyżej niżej prawo lewo prawy lewy prawa lewa
miejsce miejscu rzecz rzeczy sprawa problem pytanie dlaczego przez przed nad pod
trochę dużo mało więcej mniej razem chwila chwilę moment sekunda sekundę minuta minutę
marzec kwiecień maj czerwiec lipiec sierpień wrzesień październik listopad grudzień
poniedziałek wtorek środa czwartek piątek sobota niedziela
takie taki taka tego temat tematu sami sama samo
w z ze o u na do od za po bo i a że czy dla bez przy oraz lub
radio radia radiu radom radny radna santa sanki sanie hrabia hrabina gawron gawrony chaber kozy koza
"""
# Popularne polskie imiona i nazwiska. To po prostu inne osoby, więc luźne dopasowanie
# nie zamienia ich na kogoś z listy ("Kowalska" to nie "Kozłowska"). Dokładne trafienie działa zawsze.
_RAW_COMMON_NAMES = """
anna maria katarzyna małgorzata agnieszka barbara ewa krystyna elżbieta magdalena joanna aleksandra
monika zofia teresa danuta natalia julia karolina marta beata dorota halina jadwiga janina irena iwona
jolanta grażyna alicja justyna paulina ewelina agata patrycja kinga kamila martyna wiktoria zuzanna
oliwia maja lena amelia hanna emilia gabriela dominika sylwia renata urszula wanda marianna klaudia
ola kasia basia gosia asia ania magda ela iza zosia ula jola bożena lucyna marcelina michalina
jan piotr krzysztof andrzej tomasz paweł michał marcin stanisław jakub adam marek łukasz grzegorz
mateusz wojciech mariusz dariusz zbigniew ryszard kamil maciej rafał robert jacek józef kazimierz
jerzy tadeusz mirosław sebastian szymon bartosz daniel dawid damian artur filip kacper antoni wiktor
igor oskar dominik patryk hubert przemysław sławomir wiesław waldemar leszek roman henryk janusz
zdzisław bogdan edward kuba bartek tomek wojtek krzysiek darek mirek zbyszek staszek franek antek
nowak kowalski kowalska wiśniewski wiśniewska wójcik kowalczyk kamiński kamińska lewandowski
lewandowska zieliński zielińska szymański szymańska woźniak dąbrowski dąbrowska kozłowski kozłowska
jankowski jankowska mazur wojciechowski wojciechowska kwiatkowski kwiatkowska krawczyk kaczmarek
piotrowski piotrowska grabowski grabowska zając pawłowski pawłowska michalski michalska król
wieczorek jabłoński jabłońska wróbel nowakowski nowakowska majewski majewska olszewski olszewska
stępień malinowski malinowska jaworski jaworska adamczyk dudek nowicki nowicka pawlak górski
górska witkowski witkowska walczak sikora baran rutkowski rutkowska michalak szewczyk ostrowski
ostrowska tomaszewski tomaszewska pietrzak zalewski zalewska wróblewski wróblewska marciniak
jasiński jasińska zawadzki zawadzka bąk jakubowski jakubowska sadowski sadowska duda włodarczyk
wilk chmielewski chmielewska borkowski borkowska sokołowski sokołowska szczepański szczepańska
sawicki sawicka lis kucharski kucharska kalinowski kalinowska wysocki wysocka mazurek kubiak
maciejewski maciejewska czarnecki czarnecka kołodziej urbański urbańska kaźmierczak sobczak
konieczny głowacki głowacka zakrzewski zakrzewska krajewski krajewska krupa gawlik laskowski laskowska
"""


def normalize(text: str) -> str:
    """Uproszczona, "fonetyczna" postać słowa: małe litery, bez polskich znaków,
    ch=h, rz=ż, ó=u, v=w, j=i, bez podwójnych liter i znaków innych niż litery/cyfry."""
    t = unicodedata.normalize("NFKC", text).lower()
    t = t.replace("ch", "h").replace("rz", "z")
    t = t.translate(_PL_TABLE)
    t = "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))
    t = t.replace("qu", "kw").replace("q", "k").replace("x", "ks").replace("v", "w").replace("j", "i")
    t = re.sub(r"[^a-z0-9]+", "", t)
    return re.sub(r"(.)\1+", r"\1", t)


# wszystkie listy w tej samej postaci znormalizowanej co porównywane słowa ("ach" → "ah")
_INFLECTION_SUFFIXES = frozenset(normalize(s) for s in _RAW_INFLECTIONS)
_EPISODE_SUFFIXES = sorted({normalize(s) for s in _RAW_EPISODE_SUFFIXES}, key=len, reverse=True)
COMMON_WORDS = frozenset(normalize(w) for w in _RAW_COMMON_WORDS.split())
COMMON_NAMES = frozenset(normalize(w) for w in _RAW_COMMON_NAMES.split())


@lru_cache(maxsize=8192)
def phonetic(norm: str) -> str:
    """Grubsze uproszczenie postaci znormalizowanej: głoski dźwięczne jak bezdźwięczne
    (w=f, d=t, b=p, g=k, z=s, dz=c) - tak się je wymawia, więc i tak bywają zapisywane
    ("tratwy" ≈ "trafy", "Chrabąszcz" ≈ "Hrabążcz")."""
    return re.sub(r"(.)\1+", r"\1", norm.replace("dz", "c").translate(_DEVOICE))


@dataclass(frozen=True)
class Token:
    text: str  # słowo tak, jak je zapisało rozpoznawanie mowy
    norm: str  # postać znormalizowana


def tokenize(text: str) -> list[Token]:
    tokens = []
    for word in _WORD_RE.findall(text or ""):
        norm = normalize(word)
        if norm:
            tokens.append(Token(word, norm))
    return tokens


def _windows(tokens: list[Token], start: int, max_len: int):
    """Sklejone okna 1..max_len kolejnych słów od pozycji start: (liczba_słów, tekst).
    Okno kończy się na słowie ukrytym (np. nazwie odcinka)."""
    joined = ""
    for n in range(1, max_len + 1):
        if start + n > len(tokens) or not tokens[start + n - 1].norm:
            return
        joined += tokens[start + n - 1].norm
        yield n, joined


def _heard(tokens: list[Token], start: int, count: int) -> str:
    return " ".join(t.text for t in tokens[start:start + count])


def _known_word(token: Token) -> bool:
    return token.norm in COMMON_WORDS or token.norm in COMMON_NAMES


def _only_common_words(tokens: list[Token], start: int, count: int) -> bool:
    return all(t.norm in COMMON_WORDS for t in tokens[start:start + count])


def _only_known_words(tokens: list[Token], start: int, count: int) -> bool:
    return all(_known_word(t) for t in tokens[start:start + count])


def _has_known_word(tokens: list[Token], start: int, count: int) -> bool:
    """We fragmencie jest zwykłe słowo albo popularne imię/nazwisko - wtedy nie zgadujemy
    na podstawie części liter, że to ktoś z listy."""
    return any(_known_word(t) for t in tokens[start:start + count])


def mask_tokens(tokens: list[Token], positions) -> list[Token]:
    """Kopia transkrypcji, w której wskazane słowa (np. nazwa odcinka) nie mogą niczego udawać."""
    hidden = set(positions)
    return [Token(t.text, "") if i in hidden else t for i, t in enumerate(tokens)]


def near_positions(position: int | None, length: int) -> set[int]:
    if position is None:
        return set()
    return set(range(max(0, position - NEAR_BEFORE), min(length, position + NEAR_AFTER)))


def max_edits_first(n: int) -> int:
    """Ile pomyłek wolno w samym imieniu bez dodatkowych warunków
    (krótkie imiona muszą się zgadzać, bo np. "miał" ≈ "Michał")."""
    if n <= 5:
        return 0
    return 1 if n <= 10 else 2


def max_edits_last(n: int) -> int:
    if n <= 5:
        return 0
    if n <= 8:
        return 1
    return 2 if n <= 11 else 3


def max_edits_episode(n: int) -> int:
    if n <= 3:
        return 0
    if n <= 8:
        return 1
    return 2 if n <= 12 else 3


def _stems(word: str) -> list[str]:
    """Rdzenie do rozpoznawania form odmienionych: Weronika → weronik(i), Radek → radk(a)."""
    stems = [word]
    if len(word) >= 4 and word[-1] in _VOWELS:
        stems.append(word[:-1])
    if len(word) >= 5 and (word.endswith("ek") or word.endswith("ec")):
        stems.append(word[:-2] + word[-1])
    return stems


def part_similarity(name: str, cand: str) -> float:
    """Ścisłe podobieństwo (0..1) części imienia/nazwiska do fragmentu: pomyłki w literach,
    wymowa i polskie końcówki."""
    if not name or not cand:
        return 0.0
    best = Levenshtein.normalized_similarity(name, cand)
    if best < 1.0:
        best = max(best, Levenshtein.normalized_similarity(phonetic(name), phonetic(cand)) - 0.03)
        for stem in _stems(name):
            if len(stem) >= 3 and cand.startswith(stem):
                suffix = cand[len(stem):]
                # "Santii" po normalizacji daje "santi", czyli sam rdzeń
                if suffix in _INFLECTION_SUFFIXES or (not suffix and stem != name):
                    best = max(best, 0.95)
    return best


def letter_overlap(name: str, heard: str) -> tuple[float, float]:
    """(pokrycie, precyzja): jaka część liter nazwy występuje w usłyszanym fragmencie w tej samej
    kolejności oraz jaka część usłyszanego fragmentu to litery nazwy."""
    best = (0.0, 0.0)
    for a, b in ((name, heard), (phonetic(name), phonetic(heard))):
        if a and b:
            common = LCSseq.similarity(a, b)
            pair = (common / len(a), common / len(b))
            if sum(pair) > sum(best):
                best = pair
    return best


def _f1(coverage: float, precision: float) -> float:
    return 2 * coverage * precision / (coverage + precision) if coverage + precision else 0.0


def loose_similarity(name: str, heard: str) -> float:
    """Luźne podobieństwo na samych literach (0, gdy warunki nie są spełnione).

    W usłyszanym fragmencie musi być co najmniej 60% liter nazwy w tej samej kolejności
    (75%, gdy nie zgadza się pierwsza głoska - np. "adek" zamiast "Radek"), a sam fragment
    nie może się składać głównie z innych liter ("za dużo liter, ale nie tych")."""
    if len(name) < 4 or not heard:
        return 0.0
    coverage, precision = letter_overlap(name, heard)
    same_start = phonetic(name)[:1] == phonetic(heard)[:1]
    if precision < 0.6 or coverage < (0.6 if same_start else 0.75):
        return 0.0
    return _f1(coverage, precision)


def pair_part_similarity(name: str, heard: str) -> float:
    """Ocena imienia albo nazwiska, gdy oba padły obok siebie: wystarczy około co druga litera."""
    strict = part_similarity(name, heard)
    coverage, precision = letter_overlap(name, heard)
    loose = _f1(coverage, precision) if coverage >= 0.5 and precision >= 0.6 else 0.0
    best = max(strict, loose)
    return best if best >= 0.5 else 0.0


def _part_threshold(name: str, max_edits) -> float:
    n = len(name)
    return min(0.95, 1.0 - max_edits(n) / n) - 1e-9 if n else 1.0


def _episode_stem(word: str) -> str:
    for suffix in _EPISODE_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


# --------------------------------------------------------------------------------------
# Osoby
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Person:
    name: str                           # tak trafi do nazwy folderu/pliku, np. "Weronika Chrabąszcz"
    alternatives: tuple[str, ...] = ()  # wpisane przez użytkownika, np. ("Wera", "Nika", "Radosław Chaberko")


def parse_alternatives(text: str) -> list[str]:
    """'Wera, Nika; Werka' -> ['Wera', 'Nika', 'Werka'] (bez pustych i powtórzeń)."""
    result, seen = [], set()
    for part in re.split(r"[,;\n]", text or ""):
        part = " ".join(part.split())
        if part and part.casefold() not in seen:
            seen.add(part.casefold())
            result.append(part)
    return result


@dataclass
class PersonMatch:
    person: Person | None = None
    kind: str = ""              # FULL / FIRST / LAST albo "" gdy brak
    score: float = 0.0
    heard: str = ""             # fragment transkrypcji, który pasował
    position: int = 1 << 30     # numer słowa w transkrypcji
    loose: bool = False         # dopasowanie luźne (tylko część liter)
    ambiguous: list[Person] = field(default_factory=list)  # np. wspólne nazwisko kilku osób
    conflict: bool = False      # w nagraniu padło kilka różnych osób

    @property
    def strength(self) -> int:
        return _strength(self.kind, self.loose)

    @property
    def rank(self) -> tuple:
        return (self.strength, round(self.score, 3), -self.position)


def _strength(kind: str, loose: bool) -> int:
    if kind == FULL:
        return 3
    if kind in (FIRST, LAST):
        return 1 if loose else 2
    return 0


@dataclass
class _NameForm:
    first: str        # znormalizowane imię (lub imiona)
    first_words: int
    last: str         # znormalizowane nazwisko - puste, gdy wpisano jedno słowo
    full: str


def _name_form(text: str) -> _NameForm | None:
    words = [normalize(w) for w in _WORD_RE.findall(text)]
    words = [w for w in words if w]
    if not words:
        return None
    if len(words) == 1:
        return _NameForm(words[0], 1, "", words[0])
    first = "".join(words[:-1])
    return _NameForm(first, len(words) - 1, words[-1], first + words[-1])


def _person_forms(person: Person) -> list[_NameForm]:
    """Imię i nazwisko oraz alternatywy. Jednowyrazowa alternatywa (zdrobnienie, np. "Wera")
    działa jak inne imię tej osoby - sama i razem z jej nazwiskiem ("Wera Chrabąszcz")."""
    main = _name_form(person.name)
    forms = [main] if main else []
    for alternative in person.alternatives:
        form = _name_form(alternative)
        if form is None or form in forms:
            continue
        if not form.last and main and main.last:
            form = _NameForm(form.first, form.first_words, main.last, form.first + main.last)
        forms.append(form)
    return forms


@dataclass
class _Candidate:
    kind: str
    score: float
    position: int
    heard: str
    loose: bool = False

    @property
    def rank(self) -> tuple:
        return (_strength(self.kind, self.loose), self.score, -self.position)


class PersonMatcher:
    def __init__(self, people: list[Person]):
        self.people = list(people)
        self._forms: list[list[_NameForm]] = [_person_forms(p) for p in self.people]
        first_owners: dict[str, set[int]] = {}
        last_owners: dict[str, set[int]] = {}
        for idx, forms in enumerate(self._forms):
            for form in forms:
                first_owners.setdefault(form.first, set()).add(idx)
                if form.last:
                    last_owners.setdefault(form.last, set()).add(idx)
        self._first_owners = first_owners
        self._last_owners = last_owners

    def _unique_first(self, form: _NameForm) -> bool:
        return len(self._first_owners.get(form.first, ())) == 1

    def _unique_last(self, form: _NameForm) -> bool:
        return len(self._last_owners.get(form.last, ())) == 1

    def _best_part(self, tokens, part: str, max_words: int, threshold: float):
        best = None
        for i in range(len(tokens)):
            for n, joined in _windows(tokens, i, max_words):
                s = part_similarity(part, joined)
                if s < threshold or (best is not None and s <= best[0] + 1e-9):
                    continue
                if s < 0.95 and _has_known_word(tokens, i, n):
                    continue  # "Santa" to nie "Santia" - inne, znane słowo, a nie przekręcone imię
                best = (s, i, n)
        return best

    def _best_loose_part(self, tokens, part: str, max_words: int, slots: set[int]):
        best = None
        for i in sorted(slots):
            for n, joined in _windows(tokens, i, max_words):
                if _has_known_word(tokens, i, n):
                    continue
                s = loose_similarity(part, joined)
                if s > 0 and (best is None or s > best[0] + 1e-9):
                    best = (s, i, n)
        return best

    def _full_candidate(self, tokens, form: _NameForm) -> _Candidate | None:
        """Imię i nazwisko obok siebie (w dowolnej kolejności) albo sklejone/rozbite inaczej."""
        best = None
        lf, ll = len(form.first), len(form.last)
        for i in range(len(tokens)):
            for a_part, b_part, a_words, a_len, b_len in (
                (form.first, form.last, form.first_words + 1, lf, ll),
                (form.last, form.first, 3, ll, lf),
            ):
                for na, wa in _windows(tokens, i, a_words):
                    sa = pair_part_similarity(a_part, wa)
                    if not sa:
                        continue
                    for nb, wb in _windows(tokens, i + na, 3):
                        sb = pair_part_similarity(b_part, wb)
                        if not sb or _only_known_words(tokens, i, na + nb):
                            continue
                        score = (a_len * sa + b_len * sb) / (a_len + b_len)
                        if score >= PAIR_THRESHOLD and (best is None or score > best.score + 1e-9):
                            best = _Candidate(FULL, score, i, _heard(tokens, i, na + nb))
            for n, joined in _windows(tokens, i, form.first_words + 3):
                score = Levenshtein.normalized_similarity(form.full, joined)
                if score >= 0.75 and (best is None or score > best.score + 1e-9):
                    best = _Candidate(FULL, score, i, _heard(tokens, i, n))
        return best

    def candidates(self, tokens: list[Token], slots: set[int] | None = None) -> list[tuple[int, _Candidate]]:
        """Najlepszy dowód dla każdej osoby. slots: numery słów, w których wolno dopasować luźno
        samo imię lub samo nazwisko (tuż przy nazwie odcinka)."""
        slots = slots or set()
        found = []
        for idx, forms in enumerate(self._forms):
            options = [c for form in forms for c in self._strict_options(tokens, form)]
            if not options and slots:
                # luźno (tylko część liter) - wyłącznie tam, gdzie zwykle pada nazwisko
                options = [c for form in forms for c in self._loose_options(tokens, form, slots)]
            if options:
                found.append((idx, max(options, key=lambda c: c.rank)))
        return found

    def _strict_options(self, tokens: list[Token], form: _NameForm) -> list[_Candidate]:
        options: list[_Candidate] = []
        first = self._best_part(tokens, form.first, form.first_words + 1,
                                _part_threshold(form.first, max_edits_first))
        last = None
        if form.last:
            last = self._best_part(tokens, form.last, 3, _part_threshold(form.last, max_edits_last))
            full = self._full_candidate(tokens, form)
            if full:
                options.append(full)
            if first and last:
                # imię i nazwisko padły, ale nie obok siebie - słabszy dowód niż para obok siebie
                lf, ll = len(form.first), len(form.last)
                score = (lf * first[0] + ll * last[0]) / (lf + ll) - 0.02
                parts = sorted([first[1:], last[1:]])
                heard = " … ".join(_heard(tokens, i, n) for i, n in parts)
                options.append(_Candidate(FULL, score, parts[0][0], heard))
        if first and (self._unique_first(form) or not form.last):
            options.append(_Candidate(FIRST, first[0], first[1], _heard(tokens, first[1], first[2])))
        if last and self._unique_last(form):
            options.append(_Candidate(LAST, last[0], last[1], _heard(tokens, last[1], last[2])))
        return options

    def _loose_options(self, tokens: list[Token], form: _NameForm, slots: set[int]) -> list[_Candidate]:
        options: list[_Candidate] = []
        if self._unique_first(form) or not form.last:
            loose_first = self._best_loose_part(tokens, form.first, form.first_words + 1, slots)
            if loose_first:
                options.append(_Candidate(FIRST, loose_first[0], loose_first[1],
                                          _heard(tokens, loose_first[1], loose_first[2]), loose=True))
        if form.last and self._unique_last(form):
            loose_last = self._best_loose_part(tokens, form.last, 2, slots)
            if loose_last:
                options.append(_Candidate(LAST, loose_last[0], loose_last[1],
                                          _heard(tokens, loose_last[1], loose_last[2]), loose=True))
        return options

    def ambiguous_surnames(self, tokens: list[Token]) -> list[Person]:
        """Osoby, których wspólne nazwisko padło w nagraniu (np. "Chaberko" = Radek lub Michał)."""
        result: list[Person] = []
        for surname, owners in self._last_owners.items():
            if len(owners) < 2:
                continue
            if self._best_part(tokens, surname, 3, _part_threshold(surname, max_edits_last)):
                result.extend(self.people[i] for i in sorted(owners) if self.people[i] not in result)
        return result

    def match(self, text_or_tokens, slots: set[int] | None = None) -> PersonMatch:
        tokens = tokenize(text_or_tokens) if isinstance(text_or_tokens, str) else text_or_tokens
        found = self.candidates(tokens, slots)
        if not found:
            return PersonMatch(ambiguous=self.ambiguous_surnames(tokens))
        found.sort(key=lambda it: it[1].rank, reverse=True)
        idx, best = found[0]
        result = PersonMatch(person=self.people[idx], kind=best.kind, score=best.score, heard=best.heard,
                             position=best.position, loose=best.loose)
        best_strength = _strength(best.kind, best.loose)
        for idx2, other in found[1:]:
            if _strength(other.kind, other.loose) != best_strength:
                continue
            close = abs(best.score - other.score) < (LOOSE_MARGIN if best.loose else 0.02)
            if close and (best.loose or other.position == best.position):
                # ten sam fragment (albo luźne litery) pasuje podobnie do dwóch osób - nie zgadujemy
                return PersonMatch(ambiguous=[self.people[idx], self.people[idx2]])
            result.conflict = True  # padła też inna osoba - warto sprawdzić
        return result

    def full_mentions(self, text: str) -> list[int]:
        """Indeksy osób, których imię i nazwisko (razem) padło w tekście, w kolejności wystąpienia."""
        found = [(c.position, i) for i, c in self.candidates(tokenize(text), set()) if c.kind == FULL]
        return [i for _, i in sorted(found)]


# --------------------------------------------------------------------------------------
# Nazwa odcinka
# --------------------------------------------------------------------------------------

@dataclass
class EpisodeMatch:
    found: bool = False
    score: float = 0.0
    heard: str = ""
    position: int | None = None
    length: int = 0             # liczba słów, które pasowały
    loose: bool = False
    name: str = ""              # który z wpisanych odcinków
    conflict: bool = False      # w nagraniu padło kilka różnych odcinków


class EpisodeMatcher:
    def __init__(self, episode: str):
        self.episode = (episode or "").strip()
        self.words = [t.norm for t in tokenize(self.episode)]
        self.joined = "".join(self.words)
        self.stem = "".join(_episode_stem(w) for w in self.words)
        self.forms = {self.joined, self.stem, phonetic(self.joined), phonetic(self.stem)}
        if len(self.joined) <= 3:
            self.forms = {self.joined}  # krótkie nazwy ("dom") tylko dokładnie, bez "fonetyki" ("tom")
        self.allowed = max_edits_episode(len(self.joined))

    @staticmethod
    def _window_forms(window: list[Token]) -> set[str]:
        joined = "".join(t.norm for t in window)
        stem = "".join(_episode_stem(t.norm) for t in window)
        return {joined, stem, phonetic(joined), phonetic(stem)}

    def _distance(self, window: list[Token], cutoff: int) -> int:
        # porównujemy postać pełną i rdzeń ("tratwy"/"tratwach"), zwykłą i "fonetyczną"
        return min((
            Levenshtein.distance(a, b, score_cutoff=cutoff + 1)
            for a in self.forms for b in self._window_forms(window)
            if abs(len(a) - len(b)) <= cutoff
        ), default=cutoff + 1)

    def _window_sizes(self):
        k = len(self.words)
        return range(max(1, k - 1), k + 2)

    def _search(self, tokens: list[Token], allowed: int):
        best: tuple[int, int, int] | None = None  # (odległość, start, liczba słów)
        for i in range(len(tokens)):
            for n in self._window_sizes():
                if i + n > len(tokens):
                    break
                d = self._distance(tokens[i:i + n], allowed)
                if d <= allowed and (best is None or d < best[0]):
                    best = (d, i, n)
        return best

    def _search_loose(self, tokens: list[Token], slots: set[int]):
        """Część liter w tej samej kolejności, ale tylko w miejscach, gdzie zwykle pada nazwa
        odcinka, z tą samą pierwszą głoską ("prawy" ≠ "tratwy") i bez zwykłych słów."""
        if len(self.joined) < 5:
            return None
        first_sound = phonetic(self.joined)[:1]
        best: tuple[float, int, int] | None = None  # (wynik, start, liczba słów)
        for i in sorted(slots):
            for n in self._window_sizes():
                if i + n > len(tokens) or _only_common_words(tokens, i, n):
                    continue
                window = tokens[i:i + n]
                joined = "".join(t.norm for t in window)
                if phonetic(joined)[:1] != first_sound:
                    continue
                stem = "".join(_episode_stem(t.norm) for t in window)
                score = 0.0
                for target, heard in ((self.joined, joined), (self.stem, stem)):
                    coverage, precision = letter_overlap(target, heard)
                    if coverage >= 0.6 and precision >= 0.6:
                        score = max(score, _f1(coverage, precision))
                if score and (best is None or score > best[0]):
                    best = (score, i, n)
        return best

    def match(self, text_or_tokens, near: int | None = None) -> EpisodeMatch:
        """near: numer słowa, przy którym padło imię/nazwisko - tylko tam wystarczy część liter."""
        if not self.joined:
            return EpisodeMatch()
        tokens = tokenize(text_or_tokens) if isinstance(text_or_tokens, str) else text_or_tokens
        best = self._search(tokens, self.allowed)
        if best:
            d, i, n = best
            return EpisodeMatch(True, 1.0 - d / max(len(self.joined), 1), _heard(tokens, i, n), i, n)
        loose = self._search_loose(tokens, near_positions(near, len(tokens)))
        if loose:
            score, i, n = loose
            return EpisodeMatch(True, score, _heard(tokens, i, n), i, n, loose=True)
        # Dłuższe nazwy: wystarczy, że padły wszystkie istotne słowa (w dowolnej kolejności).
        significant = [w for w in self.words if len(w) >= 4]
        if len(significant) >= 2:
            heard, first = [], None
            for word in significant:
                part = EpisodeMatcher(word)
                hit = part._search(tokens, part.allowed)
                if hit is None:
                    return EpisodeMatch()
                heard.append(_heard(tokens, hit[1], hit[2]))
                first = hit[1] if first is None else min(first, hit[1])
            return EpisodeMatch(True, 0.8, " … ".join(heard), first, 1)
        return EpisodeMatch()


class MultiEpisodeMatcher:
    """Kilka wpisanych odcinków naraz ("dom tratwy las") - który z nich padł w nagraniu."""

    def __init__(self, episodes: list[str]):
        self.matchers = [EpisodeMatcher(e) for e in episodes if e.strip()]
        self.matchers = [m for m in self.matchers if m.joined]

    def find_all(self, tokens: list[Token], near: int | None = None) -> list[EpisodeMatch]:
        found = []
        for matcher in self.matchers:
            result = matcher.match(tokens, near)
            if result.found:
                result.name = matcher.episode
                found.append(result)
        return found

    @staticmethod
    def choose(found: list[EpisodeMatch], near: int | None = None) -> EpisodeMatch:
        """Najpewniejszy odcinek: dokładny przed luźnym, najbliżej imienia, najlepszy wynik, najwcześniej."""
        if not found:
            return EpisodeMatch()

        def key(r: EpisodeMatch):
            distance = abs(r.position - near) if near is not None and r.position is not None else 0
            return (not r.loose, -distance, round(r.score, 3), -(r.position or 0))

        best = max(found, key=key)
        best.conflict = len({r.name.casefold() for r in found}) > 1
        return best
