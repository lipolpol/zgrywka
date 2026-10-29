"""Decyzja na podstawie całej transkrypcji: luźne dopasowanie liter tylko tam, gdzie ma to sens."""
import pytest

from zgrywka.analyzer import Recognizer
from zgrywka.matching import FULL, Person

PEOPLE = [Person(n) for n in ("Radek Chaberko", "Michał Chaberko", "Weronika Chrabąszcz", "Eliza Macudzińska",
                                "Marcel Gawroński", "Santia Kozłowska")]
FILLER = "sprawdzamy jeszcze poziom dźwięku, dobrze, to teraz idziemy na plan i ustawiamy kamerę przy brzegu. "


@pytest.fixture(scope="module")
def rec():
    return Recognizer(PEOPLE, ["tratwy"])


@pytest.mark.parametrize("text, expected", [
    ("Tratwa, adek! " + FILLER, "Radek Chaberko"),             # zgubiona pierwsza litera
    ("Tratwa, piadek. " + FILLER, "Radek Chaberko"),           # przekręcona pierwsza litera
    ("Michałek, tratwy. " + FILLER, "Michał Chaberko"),        # zdrobnienie (więcej liter)
    ("Weroniczka, tratwy. " + FILLER, "Weronika Chrabąszcz"),
    ("Sandzia, tratwy. " + FILLER, "Santia Kozłowska"),
    ("Elizka, tratwy. " + FILLER, "Eliza Macudzińska"),
    ("Marcel Woński, tratwy. " + FILLER, "Marcel Gawroński"),   # imię + co druga litera nazwiska
    ("Arcel Gawrosk, tratwy. " + FILLER, "Marcel Gawroński"),   # obie części z brakami liter
    ("Marek Chaberko, tratwy. " + FILLER, "Radek Chaberko"),    # wspólne nazwisko, imię "prawie"
    ("Gaw roński, tratwy. " + FILLER, "Marcel Gawroński"),      # nazwisko rozbite na dwa słowa
    (FILLER + "Dobra, teraz Radek Haberko, tratwy.", "Radek Chaberko"),  # pełne nazwisko może paść później
])
def test_loose_but_logical_matches(rec, text, expected):
    person, _ = rec.match(text)
    assert person.person is not None, text
    assert person.person.name == expected


@pytest.mark.parametrize("text", [
    "On miał dużo czasu. " + FILLER,                 # "miał" to zwykłe słowo, nie Michał
    "Dam radę, tratwy. " + FILLER,                   # "radę" to zwykłe słowo, nie Radek
    "Marzec, tratwy. " + FILLER,
    "No dobra, nagrywamy. " + FILLER,
    "Adek, mikroport drugi. " + FILLER,              # część liter, ale bez nazwy odcinka obok
    FILLER + FILLER + "a potem adek",                # luźne litery w środku rozmowy się nie liczą
    "Aberko, tratwy. " + FILLER,                     # część nazwiska wspólnego dla dwóch osób
    "Rafał Chaberko, tratwy. " + FILLER,             # imię zbyt różne od Radka i Michała
    "Pani Kowalska, tratwy. " + FILLER,              # inne, popularne nazwisko - to nie Kozłowska
])
def test_no_person_from_loose_letters(rec, text):
    person, _ = rec.match(text)
    assert person.person is None, (text, person)


@pytest.mark.parametrize("text", [
    "Weronika Hrabążcz tracy, dzień drugi. " + FILLER,  # dwie litery źle, ale tuż przy nazwisku
    "Marcel Gawroński, trafy. " + FILLER,
    "Tatwy, mikroport numer 3. " + FILLER,           # jedna zgubiona litera - zawsze
])
def test_episode_with_some_letters(rec, text):
    _, episode = rec.match(text)
    assert episode.found, text


@pytest.mark.parametrize("text", [
    "Marcel, prawy brzeg. " + FILLER,                # inna pierwsza głoska
    "Dzień dobry, sprawdzamy poziom dźwięku, raz, dwa, trzy.",
    "Teatry, mikroport numer trzy. " + FILLER,       # dwie litery źle i nikogo obok - za mało
    "Trasa na jutro jest już rozpisana.",
])
def test_episode_not_from_random_words(rec, text):
    _, episode = rec.match(text)
    assert not episode.found, text


def test_full_match_is_marked_strict(rec):
    person, episode = rec.match("Eliza Macudzińska, tratwy. " + FILLER)
    assert person.kind == FULL and not person.loose and episode.found and not episode.loose


def test_loose_match_is_marked_for_review(rec):
    person, _ = rec.match("Tratwa, adek! " + FILLER)
    assert person.loose


# Zwykłe rozmowy z planu: nie mogą dawać ani osoby, ani nazwy odcinka.
CHATTER = """Dobra, to kręcimy od początku, wszyscy na miejsca.
Sprawdźcie baterie w nadajnikach, bo zaraz zaczynamy.
Radio, radio, słychać mnie? Odbiór.
Radio Zet gra w tle, wyłączcie je.
Michalina, podaj mi proszę kabel.
Marcelina idzie po kawę dla wszystkich.
Radom jest daleko stąd, jedziemy autem.
Radny przyjechał na plan zobaczyć ekipę.
Mieliśmy przerwę, teraz wracamy do pracy.
Eliminacje były wczoraj, dzisiaj finał.
Sanki, narty i łyżwy zostały w magazynie.
Maciek, Grzesiek i Bartek ustawiają światło.
Gawrony siedzą na drzewie, uważajcie na dźwięk.
Kozy, kury i owce są w zagrodzie obok.
Chaber rośnie w zbożu, ładne niebieskie kwiaty.
Hrabia przyjechał karetą, kostiumy są gotowe.
Elegancko, wszystko działa, lecimy dalej.
Santa Claus przyjdzie w grudniu, jak zawsze.
Wera z produkcji dzwoniła w sprawie umowy.
Teraz kamera B, potem zbliżenie.
Trasa na jutro jest już rozpisana.""".splitlines()


@pytest.mark.parametrize("text", CHATTER)
def test_chatter_is_not_recognized(rec, text):
    person, episode = rec.match(text)
    assert person.person is None and not episode.found, (text, person, episode)


def test_several_episodes_pick_the_one_said(rec):
    multi = Recognizer(PEOPLE, ["dom", "tratwy", "las"])
    person, episode = multi.match("Weronika Chrabąszcz, dom. " + FILLER)
    assert person.person.name == "Weronika Chrabąszcz" and episode.name == "dom"
    _, episode = multi.match("Marcel Gawroński, tratwa. " + FILLER)
    assert episode.name == "tratwy"
    _, episode = multi.match("Radek Chaberko. " + FILLER)
    assert not episode.found


def test_short_episode_name_must_be_exact():
    multi = Recognizer(PEOPLE, ["dom"])
    assert multi.match("Eliza Macudzińska, dom.")[1].found
    assert not multi.match("Eliza Macudzińska, tom drugi.")[1].found   # bez "fonetyki" dla krótkich nazw
    assert not multi.match("Eliza Macudzińska, domy.")[1].found


ALTERNATIVES = [Person("Weronika Chrabąszcz", ("Wera", "Nika", "Werka")),
                Person("Radek Chaberko", ("Radosław Chaberko",)), Person("Michał Chaberko")]


@pytest.mark.parametrize("text, expected", [
    ("Wera, tratwy. " + FILLER, "Weronika Chrabąszcz"),            # samo zdrobnienie
    ("Nika Chrabąszcz, tratwy. " + FILLER, "Weronika Chrabąszcz"),  # zdrobnienie + nazwisko
    ("Mikroport Niki, tratwy. " + FILLER, "Weronika Chrabąszcz"),   # odmiana zdrobnienia
    ("Werką. " + FILLER, "Weronika Chrabąszcz"),
    ("Radosław Chaberko, tratwy. " + FILLER, "Radek Chaberko"),     # pełne imię z nazwiskiem
])
def test_alternatives_from_person_dialog(text, expected):
    person, _ = Recognizer(ALTERNATIVES, ["tratwy"]).match(text)
    assert person.person is not None and person.person.name == expected, (text, person)


def test_alternatives_do_not_catch_common_words():
    rec = Recognizer(ALTERNATIVES, ["tratwy"])
    assert rec.match("Nikt nie przyszedł. " + FILLER)[0].person is None    # "nikt" to nie "Nika"
    assert rec.match("Werbel gra w tle. " + FILLER)[0].person is None


def test_shared_alternative_is_ambiguous():
    people = [Person("Aleksandra Nowak", ("Ola",)), Person("Olga Lis", ("Ola",))]
    person, _ = Recognizer(people, ["dom"]).match("Ola, dom. " + FILLER)
    assert person.person is None


def test_beginning_has_priority_for_episodes():
    rec = Recognizer(PEOPLE, ["las", "dom"])
    _, episode = rec.match("Weronika Chrabąszcz, dom. A w przyszłym tygodniu kręcimy las. " + FILLER)
    assert episode.name == "dom" and episode.conflict


def test_beginning_has_priority_for_people():
    person, _ = Recognizer(PEOPLE, ["dom"]).match("Marcel Woński, dom. Potem Eliza Macudzińska. " + FILLER)
    assert person.person.name == "Marcel Gawroński" and person.conflict


@pytest.mark.parametrize("text", [
    "Spędziliśmy 24h w najstraszniejszym domu strachu.",   # odmiana: "domu"
    "Straszny dom, Marcel Gawroński.",                      # inna kolejność i forma: "stra…" + "dom"
    "Dom strachu, ujęcie pierwsze.",
    "Marcel Gawroński, strachu dom.",
    "Dom pełen strachu, Eliza Macudzińska.",                # jedno słowo między
])
def test_multiword_episode_in_any_order_and_form(text):
    _, episode = Recognizer(PEOPLE, ["dom strachu"]).match(text)
    assert episode.found and episode.name == "dom strachu", text


@pytest.mark.parametrize("text", [
    "Wracam do domu, jutro też jestem.",                    # tylko jedno słowo z nazwy
    "Mój dom jest daleko, a strach ma wielkie oczy.",       # słowa za daleko od siebie
    "Strażacy przyjechali na miejsce.",
])
def test_multiword_episode_needs_all_words_close(text):
    assert not Recognizer(PEOPLE, ["dom strachu"]).match(text)[1].found, text


def test_person_only_mode_takes_episode_from_field():
    from zgrywka.analyzer import assign_episode

    assert assign_episode(None, ["dom"], person_only=True) == ("dom", True)
    assert assign_episode("las", ["dom", "las"], person_only=True) == ("las", False)   # padł inny z listy
    assert assign_episode(None, ["dom", "las"], person_only=True) == ("dom", True)    # żaden - pierwszy
    assert assign_episode(None, ["dom"], person_only=False) == (None, False)
    assert assign_episode(None, [], person_only=True) == (None, False)


class FakeEngine:
    def __init__(self, *texts):
        self.texts, self.calls = list(texts), 0

    def transcribe(self, audio, hotwords=None, vad_threshold=0.5):
        self.calls += 1
        return self.texts.pop(0) if self.texts else ""


def test_person_only_mode_skips_second_pass_when_person_found():
    import numpy as np

    from zgrywka.analyzer import Analyzer, FileAnalysis

    audio = np.zeros(16000, dtype=np.float32)
    engine = FakeEngine("Marcel Gawroński, sprawdzamy dźwięk.", "druga próba")
    Analyzer(engine, PEOPLE, ["dom"], need_episode=False)._recognize(audio, FileAnalysis("x.wav"))
    assert engine.calls == 1                       # osoba jest, odcinek niepotrzebny
    engine = FakeEngine("Marcel Gawroński, sprawdzamy dźwięk.", "druga próba")
    Analyzer(engine, PEOPLE, ["dom"], need_episode=True)._recognize(audio, FileAnalysis("x.wav"))
    assert engine.calls == 2                       # zwykły tryb szuka jeszcze odcinka
