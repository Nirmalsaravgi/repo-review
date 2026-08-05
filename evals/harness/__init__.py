"""Eval harness: dataset → run the agent → score.

Scoring (`metrics`) is pure and unit-tested in CI. Running the agent live
(`runner` + `run_eval.py`) needs a real provider and costs tokens, so it is a
script, not part of the default pytest run.
"""
