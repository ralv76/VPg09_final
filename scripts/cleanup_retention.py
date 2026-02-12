#!/usr/bin/env python3
"""Скрипт удаления по срокам хранения (ТЗ 5.2). Запуск из cron, например: 0 3 * * * cd /path/to/project && python scripts/cleanup_retention.py"""
import os
import sys

# Корень проекта
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.services.cleanup import run_retention_cleanup

if __name__ == "__main__":
    stats = run_retention_cleanup()
    print("Cleanup done:", stats)
