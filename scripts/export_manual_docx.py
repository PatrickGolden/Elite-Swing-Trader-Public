#!/usr/bin/env python3
"""Export the user manual to docs/Elite_Trading_Manual.docx."""
import os
import sys

# Project root
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DOCS_DIR = os.path.join(ROOT, 'docs')
OUTPUT_PATH = os.path.join(DOCS_DIR, 'Elite_Trading_Manual.docx')


def main():
    os.makedirs(DOCS_DIR, exist_ok=True)
    from system_manual import ManualGenerator
    m = ManualGenerator()
    m.generate_docx(OUTPUT_PATH)
    print(f"Manual written to: {OUTPUT_PATH}")


if __name__ == '__main__':
    main()
