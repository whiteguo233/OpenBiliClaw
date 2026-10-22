"""Blue Fish Lab — a separate Bilibili account that lives in comment sections.

Deliberately no re-exports here: importing the package must not drag in the
LLM prompt stack. Callers import the submodule they need
(``openbiliclaw.fish.config`` / ``.account`` / ``.store`` / ``.engine``).
"""
