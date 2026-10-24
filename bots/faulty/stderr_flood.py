#!/usr/bin/env python3
"""Llena stderr antes del handshake para probar drenaje y límite de salida."""

import sys

sys.stderr.write("x" * (128 * 1024))
sys.stderr.flush()
sys.stdin.readline()
