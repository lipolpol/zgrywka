import os

import pytest

from zgrywka.naming import (
    NO_EPISODE_TEXT, UNKNOWN_LABEL, PriorLabel, apply_moves, clean_episode, find_audio_files, journal_summary,
    latest_journal, make_target, merge_prior, parse_episodes, parse_prior_label, plan_moves, safe_filename,
    undo_journal,
)


def touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def test_labels_follow_rules():
    both = make_target("Weronika Chrabąszcz", "dom")
    assert both.label == "Weronika Chrabąszcz „dom”"
    assert both.subdir == os.path.join("dom", "Weronika Chrabąszcz „dom”") and both.move
    person_only = make_target("Marcel Gawroński", None)
    assert person_only.label == f"Marcel Gawroński „{NO_EPISODE_TEXT}”" and not person_only.move
    episode_only = make_target(None, "tratwy")
    assert episode_only.label == "NIE WIEM KTO „tratwy”" and not episode_only.move
    nothing = make_target(None, None)
    assert nothing.label == UNKNOWN_LABEL and not nothing.move


def test_episode_field_with_several_names():
    assert parse_episodes("dom tratwy las") == ["dom", "tratwy", "las"]
    assert parse_episodes("  dom,  tratwy;las  dom ") == ["dom", "tratwy", "las"]   # bez powtórzeń
    assert parse_episodes('"tratwy na Wiśle" dom') == ["tratwy na Wiśle", "dom"]
    assert parse_episodes("„stara chata” las") == ["stara chata", "las"]
    assert parse_episodes("") == []


def test_episode_quotes_and_forbidden_characters_are_cleaned():
    assert clean_episode(' "tratwy" ') == "tratwy"
    assert make_target("Marcel Gawroński", "„tratwy”").label == "Marcel Gawroński „tratwy”"
    assert "/" not in safe_filename("a/b:c*d?")
    assert safe_filename('x "y"') == "x ”y”"
    assert safe_filename("CON") == "_CON"
    assert make_target("Jan Kowalski", "a/b").subdir.split(os.sep)[0] == "a-b"


def test_plan_numbers_groups_and_continues_existing(tmp_path):
    root = str(tmp_path)
    for name in ("REC0002.WAV", "REC0010.WAV", "REC0001.WAV", "a.mp3", "b.wav"):
        touch(os.path.join(root, name))
    folder = os.path.join(root, "tratwy", "Marcel Gawroński „tratwy”")
    touch(os.path.join(folder, "Marcel Gawroński „tratwy” 1.wav"))  # z poprzedniej zgrywki
    marcel = make_target("Marcel Gawroński", "tratwy")
    unknown = make_target(None, None)
    items = [(os.path.join(root, n), marcel) for n in ("REC0010.WAV", "REC0002.WAV", "REC0001.WAV")]
    items += [(os.path.join(root, "a.mp3"), unknown), (os.path.join(root, "b.wav"), unknown)]
    plan = {os.path.basename(m.src): m for m in plan_moves(items, root)}
    # naturalna kolejność nazw: REC0001 < REC0002 < REC0010, numeracja od 2 (1 już istnieje)
    assert os.path.basename(plan["REC0001.WAV"].dst) == "Marcel Gawroński „tratwy” 2.WAV"
    assert os.path.basename(plan["REC0002.WAV"].dst) == "Marcel Gawroński „tratwy” 3.WAV"
    assert os.path.basename(plan["REC0010.WAV"].dst) == "Marcel Gawroński „tratwy” 4.WAV"
    assert os.path.dirname(plan["REC0001.WAV"].dst) == folder
    assert plan["a.mp3"].dst == os.path.join(root, "nie zidentyfikowany 1.mp3")
    assert plan["b.wav"].dst == os.path.join(root, "nie zidentyfikowany 2.wav")


def test_several_episodes_go_to_separate_folders(tmp_path):
    root = str(tmp_path)
    items = []
    for name, person, episode in (("1.wav", "Weronika Chrabąszcz", "dom"), ("2.wav", "Weronika Chrabąszcz", "dom"),
                                  ("3.wav", "Weronika Chrabąszcz", "las"), ("4.wav", "Radek Chaberko", "dom")):
        touch(os.path.join(root, name))
        items.append((os.path.join(root, name), make_target(person, episode)))
    plan = {os.path.basename(m.src): os.path.relpath(m.dst, root) for m in plan_moves(items, root)}
    assert plan["1.wav"] == os.path.join("dom", "Weronika Chrabąszcz „dom”", "Weronika Chrabąszcz „dom” 1.wav")
    assert plan["2.wav"] == os.path.join("dom", "Weronika Chrabąszcz „dom”", "Weronika Chrabąszcz „dom” 2.wav")
    assert plan["3.wav"] == os.path.join("las", "Weronika Chrabąszcz „las”", "Weronika Chrabąszcz „las” 1.wav")
    assert plan["4.wav"] == os.path.join("dom", "Radek Chaberko „dom”", "Radek Chaberko „dom” 1.wav")


def test_already_named_file_is_left_alone(tmp_path):
    root = str(tmp_path)
    path = os.path.join(root, "nie zidentyfikowany 3.wav")
    touch(path)
    plan = plan_moves([(path, make_target(None, None))], root)
    assert plan[0].unchanged


def test_apply_and_undo_roundtrip(tmp_path):
    root = str(tmp_path / "zgrywka")
    journal_dir = str(tmp_path / "historia")
    files = {name: os.path.join(root, name) for name in ("1.wav", "2.wav", "3.wav")}
    for name, path in files.items():
        touch(path, name.encode())
    items = [(files["1.wav"], make_target("Eliza Macudzińska", "tratwy")),
             (files["2.wav"], make_target("Eliza Macudzińska", None)),
             (files["3.wav"], make_target(None, "tratwy"))]
    report = apply_moves(plan_moves(items, root), journal_dir, root)
    assert len(report.moved) == 3 and not report.errors
    moved = os.path.join(root, "tratwy", "Eliza Macudzińska „tratwy”", "Eliza Macudzińska „tratwy” 1.wav")
    assert open(moved, "rb").read() == b"1.wav"
    assert os.path.exists(os.path.join(root, "NIE WIEM KTO „tratwy” 1.wav"))
    assert not os.path.exists(files["1.wav"])

    journal = latest_journal(journal_dir)
    assert journal_summary(journal) == (root, 3)
    undo = undo_journal(journal)
    assert undo.restored == 3 and not undo.errors
    for name, path in files.items():
        assert open(path, "rb").read() == name.encode()
    assert not os.path.exists(os.path.join(root, "tratwy"))  # puste foldery (odcinka i osoby) usunięte
    assert latest_journal(journal_dir) is None


def test_undo_keeps_folders_that_already_existed(tmp_path):
    root = str(tmp_path / "z")
    os.makedirs(os.path.join(root, "dom"))
    touch(os.path.join(root, "dom", "notatka.txt"))
    touch(os.path.join(root, "a.wav"))
    apply_moves(plan_moves([(os.path.join(root, "a.wav"), make_target("Jan Nowak", "dom"))], root),
                str(tmp_path / "h"), root)
    undo_journal(latest_journal(str(tmp_path / "h")))
    assert os.path.exists(os.path.join(root, "dom", "notatka.txt"))
    assert not os.path.exists(os.path.join(root, "dom", "Jan Nowak „dom”"))


def test_never_overwrites(tmp_path):
    root = str(tmp_path)
    src = os.path.join(root, "a.wav")
    touch(src, b"new")
    plan = plan_moves([(src, make_target(None, None))], root)
    touch(plan[0].dst, b"old")  # ktoś utworzył plik między planem a wykonaniem
    report = apply_moves(plan, str(tmp_path / "h"), root)
    assert report.errors and not report.moved
    assert open(plan[0].dst, "rb").read() == b"old"
    assert os.path.exists(src)


def test_find_audio_files_skips_output_dirs(tmp_path):
    root = str(tmp_path)
    touch(os.path.join(root, "a.WAV"))
    touch(os.path.join(root, "notatki.txt"))
    touch(os.path.join(root, "._a.WAV"))  # plik pomocniczy macOS
    touch(os.path.join(root, "karta", "b.mp3"))
    touch(os.path.join(root, "tratwy", "Marcel Gawroński „tratwy”", "Marcel Gawroński „tratwy” 1.wav"))
    names = lambda files: [os.path.relpath(f, root) for f in files]  # noqa: E731
    assert names(find_audio_files(root)) == ["a.WAV"]
    assert names(find_audio_files(root, recursive=True)) == ["a.WAV", os.path.join("karta", "b.mp3")]


@pytest.mark.parametrize("count", [1, 12])
def test_rerun_keeps_numbering_stable(tmp_path, count):
    root = str(tmp_path)
    paths = [os.path.join(root, f"REC{i:04d}.wav") for i in range(count)]
    for p in paths:
        touch(p)
    target = make_target(None, None)
    apply_moves(plan_moves([(p, target) for p in paths], root), str(tmp_path / "h"), root)
    renamed = find_audio_files(root)
    second = plan_moves([(p, target) for p in renamed], root)
    assert all(m.unchanged for m in second)


def test_prior_label_parsing_and_merge():
    people = ["Marcel Gawroński", "Eliza Macudzińska"]
    episodes = ["tratwy", "dom"]
    assert parse_prior_label("Marcel Gawroński „tratwy” 3.WAV", people, episodes) == \
        PriorLabel("Marcel Gawroński", "tratwy")
    assert parse_prior_label("Marcel Gawroński „!!BRAK NAZWY FILMU!!” 1.flac", people, episodes) == \
        PriorLabel("Marcel Gawroński", None)
    assert parse_prior_label("NIE WIEM KTO „dom” 2.wav", people, episodes) == PriorLabel(None, "dom")
    assert parse_prior_label("NIE WIEM KTO „las” 2.wav", people, episodes) == PriorLabel(None, None)
    assert parse_prior_label("nie zidentyfikowany 4.wav", people, episodes) == PriorLabel()
    assert parse_prior_label("REC0001.WAV", people, episodes) is None
    assert parse_prior_label("Jan Kowalski „tratwy” 1.wav", people, episodes) is None  # osoby nie ma na liście
    # nowa analiza nic nie znalazła - zostaje to, co było w nazwie
    assert merge_prior(None, None, PriorLabel(None, "dom")) == (None, "dom", True)
    # nowa analiza coś dodała - łączymy
    assert merge_prior("Eliza Macudzińska", None, PriorLabel(None, "dom")) == ("Eliza Macudzińska", "dom", True)
    # nowa analiza ma pierwszeństwo przed starą osobą
    assert merge_prior("Eliza Macudzińska", "dom", PriorLabel("Marcel Gawroński", "tratwy")) == \
        ("Eliza Macudzińska", "dom", False)
