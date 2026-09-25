Independent test suite. Authored by agents that were given ONLY docs/SPEC.md and
docs/API.md -- never the implementation in herdr_usage/ and never tests/.

Run: python3 -m unittest discover -s tests_indep -t .
