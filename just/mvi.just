# KAGARI MVI worker (ADR-0007). See components/kagari-mvi/README.md.
root := justfile_directory()
worker := root / "components/kagari-mvi/kagari_mvi.py"
python := env("KAGARI_MVI_PYTHON", "python3")
workdir := env("KAGARI_MVI_WORKDIR", "/srv/kagari")
allow_actor := env("KAGARI_MVI_ALLOW_ACTOR", "upiscium")

# Create the five kagari:* labels on OWNER/REPO (no-op if present).
ensure-labels repo:
    {{python}} {{quote(worker)}} ensure-labels --repo {{quote(repo)}}

# Process at most one approved Issue of OWNER/REPO (extra args go to kagari_mvi.py).
run-once repo *args:
    {{python}} {{quote(worker)}} run-once --repo {{quote(repo)}} --allow-actor {{quote(allow_actor)}} --workdir {{quote(workdir)}} {{args}}

# Review at most one implemented Issue's PR of OWNER/REPO (extra args go to kagari_mvi.py).
review-once repo *args:
    {{python}} {{quote(worker)}} review-once --repo {{quote(repo)}} --workdir {{quote(workdir)}} {{args}}

# Run the unit tests (gh/git are faked).
test:
    {{python}} {{quote(root / "components/kagari-mvi/test_kagari_mvi.py")}}
