#!/bin/bash
# Abre a janela do MacScaler.
cd "$(dirname "$0")" && exec .venv/bin/python -W ignore -m app "$@"
