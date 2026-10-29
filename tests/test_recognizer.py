"""Decyzja na podstawie całej transkrypcji: luźne dopasowanie liter tylko tam, gdzie ma to sens."""
import pytest

from zgrywka.analyzer import Recognizer
from zgrywka.matching import FULL, Person

PEOPLE = [Person(n) for n in ("Radek Chaberko", "Michał Chaberko", "Weronika Chrabąszcz", "Eliza Macudzińska",
                                "Marcel Gawroński", "Santia Kozłowska")]
FILLER = "sprawdzamy jeszcze poziom dźwięku, dobrze, to teraz idziemy na plan i ustawiamy kamerę przy brzegu. "


@pytest.fixture(scope="module")
def rec():
    return Recognizer(PEOPLE, "tratwy")


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
