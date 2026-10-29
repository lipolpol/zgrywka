"""Tryb tekstowy (bez okna): python -m zgrywka FOLDER --odcinek tratwy [--zastosuj]."""

from __future__ import annotations

import argparse
import os
import sys

from . import config
from .analyzer import Analyzer
from .asr import MODELS, Engine
from .naming import apply_moves, find_audio_files, make_target, merge_prior, parse_episodes, parse_prior_label, plan_moves


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="zgrywka", description="Porządkowanie nagrań z mikroportów (tryb tekstowy).")
    parser.add_argument("folder", help="folder z nagraniami")
    parser.add_argument("--odcinek", default="", help='nazwa odcinka, np. tratwy (kilka: "dom tratwy las")')
    parser.add_argument("--model", choices=list(MODELS), help="model rozpoznawania mowy")
    parser.add_argument("--urzadzenie", choices=["auto", "cpu", "cuda"], help="CPU / karta graficzna")
    parser.add_argument("--sekundy", type=int, help="ile sekund od początku nagrania analizować")
    parser.add_argument("--podfoldery", action="store_true", help="szukaj nagrań także w podfolderach")
    parser.add_argument("--zastosuj", action="store_true", help="od razu przenieś i zmień nazwy plików")
    args = parser.parse_args(argv)

    settings = config.load_settings()
    episodes = parse_episodes(args.odcinek)
    folder = os.path.abspath(args.folder)
    files = find_audio_files(folder, args.podfoldery or settings.recursive)
    if not files:
        print("Brak plików audio w folderze", folder)
        return 1
    engine = Engine(args.model or settings.model, args.urzadzenie or settings.device, config.models_dir())

    def progress(done, total):
        print(f"\rPobieranie modelu: {done / 1e6:.0f}/{total / 1e6:.0f} MB", end="", flush=True)

    engine.load(on_status=print, on_download=progress)
    print("Model:", engine.description)
    people = settings.person_objects()
    analyzer = Analyzer(engine, people, episodes, args.sekundy or settings.seconds,
                        settings.use_hints, settings.second_pass)
    items = []
    for i, path in enumerate(files, 1):
        result = analyzer.analyze(path)
        name = os.path.relpath(path, folder)
        if result.error:
            print(f"[{i}/{len(files)}] {name}: BŁĄD – {result.error} (plik zostaje bez zmian)")
            continue
        prior = parse_prior_label(path, [p.name for p in people], episodes)
        person, episode, _ = merge_prior(result.person.person.name if result.person.person else None,
                                         result.episode.name or None, prior)
        target = make_target(person, episode, settings.quote_style)
        items.append((path, target))
        print(f"[{i}/{len(files)}] {name}: {target.label}   ({result.elapsed:.1f} s) „{result.transcript[:90]}”")

    plan = plan_moves(items, folder)
    print("\nPlan:")
    for move in plan:
        if not move.unchanged:
            print(f"  {os.path.relpath(move.src, folder)}  →  {os.path.relpath(move.dst, folder)}")
    if not args.zastosuj:
        print("\n(nic nie zmieniono – dodaj --zastosuj, aby uporządkować pliki)")
        return 0
    report = apply_moves(plan, config.journal_dir(), folder)
    print(f"\nUporządkowano: {len(report.moved)}, błędy: {len(report.errors)}")
    for move, err in report.errors:
        print("  BŁĄD", move.src, "-", err)
    return 0 if not report.errors else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
