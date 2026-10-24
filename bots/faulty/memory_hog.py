#!/usr/bin/env python3
"""Intenta superar el presupuesto de memoria al recibir hello."""

import sys

sys.stdin.readline()
payload = bytearray(128 * 1024 * 1024)
sys.stderr.write(str(len(payload)))
