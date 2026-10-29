import pytest

from zgrywka.matching import FIRST, FULL, LAST, EpisodeMatcher, Person, PersonMatcher, normalize

PEOPLE = [Person(n) for n in ("Radek Chaberko", "Michał Chaberko", "Weronika Chrabąszcz", "Eliza Macudzińska",
                                "Marcel Gawroński", "Santia Kozłowska")]


@pytest.fixture(scope="module")
def persons():
    return PersonMatcher(PEOPLE)


@pytest.mark.parametrize("text, expected, kind", [
    ("Marcel Gawroński, tratwy, ujęcie pierwsze", "Marcel Gawroński", FULL),
    ("Marcel Woński, tratwy", "Marcel Gawroński", FULL),             # przykład z opisu zadania
    ("Radek Haberko, tratwy.", "Radek Chaberko", FULL),
    ("Michał Hawerko. Odcinek tratwy", "Michał Chaberko", FULL),
    ("Weronika Hrabążcz tracy, dzień drugi", "Weronika Chrabąszcz", FULL),
    ("Weronika Chabążcz, Tratwy", "Weronika Chrabąszcz", FULL),
    ("Candzia Kozłowska, Teatwy", "Santia Kozłowska", FULL),
    ("Radek Haveko, tratwy, ujęcie drugie", "Radek Chaberko", FULL),
    ("Eliza Ma cudzińska, trat wy", "Eliza Macudzińska", FULL),       # słowo rozbite przez model
    ("Gawroński Marcel tratwami", "Marcel Gawroński", FULL),          # odwrotna kolejność
    ("mikroport Marcela Gawrońskiego, tratwy", "Marcel Gawroński", FULL),  # odmiana
    ("Tu Marcel, nagrywamy tratwy", "Marcel Gawroński", FIRST),
    ("Mikroport Radka.", "Radek Chaberko", FIRST),
    ("Kozłowska, tratwy.", "Santia Kozłowska", LAST),
    ("Macudzińskiej nie ma", "Eliza Macudzińska", LAST),
])
def test_person_recognized(persons, text, expected, kind):
    match = persons.match(text)
    assert match.person is not None, text
    assert match.person.name == expected
    assert match.kind == kind


@pytest.mark.parametrize("text", [
    "Dzień dobry, sprawdzamy poziom dźwięku, raz, dwa, trzy.",
    "On miał dużo czasu, ale dam radę z tą trawą.",   # "miał" ≠ Michał, "radę" ≠ Radek
    "Michalina i Marcelina poszły do teatru.",
    "Pani Kowalska i pan Gawlik",
    "",
])
def test_no_false_positives(persons, text):
    assert persons.match(text).person is None


def test_shared_surname_is_ambiguous(persons):
    match = persons.match("Haberko, tratwy.")
    assert match.person is None
    assert {p.name for p in match.ambiguous} == {"Radek Chaberko", "Michał Chaberko"}


def test_first_name_resolves_shared_surname(persons):
    assert persons.match("Michał, mikroport drugi").person.name == "Michał Chaberko"


def test_full_name_beats_first_name(persons):
    match = persons.match("Radek, a teraz Marcel Gawroński")
    assert match.person.name == "Marcel Gawroński"
    assert match.kind == FULL


def test_full_mentions_detects_prompt_echo(persons):
    echo = "Osoby: Radek Chaberko, Michał Chaberko, Weronika Chrabąszcz, Eliza Macudzińska."
    assert len(set(persons.full_mentions(echo))) >= 2


@pytest.mark.parametrize("text", [
    "tratwy", "Tratwy.", "tratwa", "tratwą", "tratwach", "dratwy", "teatwy", "Trafy", "trat wy", "tatwy",
])
def test_episode_found(text):
    assert EpisodeMatcher("tratwy").match(text).found, text


FILLER = "sprawdzamy jeszcze poziom dźwięku, dobrze, to teraz idziemy na plan i ustawiamy kamerę. "


@pytest.mark.parametrize("text", ["prawy brzeg", "dzień dobry", "", FILLER + "teatry", FILLER + "tracy"])
def test_episode_not_found_without_context(text):
    assert not EpisodeMatcher("tratwy").match(text).found, text


def test_episode_more_tolerant_next_to_name(persons):
    text = "Weronika Hrabążcz tracy, dzień drugi"
    person = persons.match(text)
    assert EpisodeMatcher("tratwy").match(text, near=person.position).found
    # ...ale nie wtedy, gdy pierwsza głoska się nie zgadza
    text = "Marcel, prawy brzeg"
    assert not EpisodeMatcher("tratwy").match(text, near=persons.match(text).position).found


def test_multiword_episode():
    matcher = EpisodeMatcher("Tratwy na Wiśle")
    assert matcher.match("Marcel, tratwy na wiśle").found
    assert matcher.match("na Wiśle robimy tratwy").found
    assert not matcher.match("tratwy").found


def test_short_episode_requires_exact():
    matcher = EpisodeMatcher("las")
    assert matcher.match("idziemy w las").found
    assert not matcher.match("idziemy na lot").found


def test_normalize_polish_spelling():
    assert normalize("Chaberko") == normalize("Haberko")
    assert normalize("Kozłowska") == "kozlowska"
    assert normalize("Gżegżółka") == normalize("Grzegrzułka")
