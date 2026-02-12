#!/usr/bin/env python3
"""
Debug ACT PDF Parsing
Shows exactly what pdfplumber extracts from the PDF
"""

import pdfplumber
import sys

if len(sys.argv) < 2:
    print("Usage: python debug_pdf_parsing.py <path_to_pdf>")
    sys.exit(1)

pdf_path = sys.argv[1]

print("=" * 80)
print("PDF PARSING DEBUG")
print("=" * 80)

with pdfplumber.open(pdf_path) as pdf:
    print(f"\nTotal pages: {len(pdf.pages)}")
    
    for page_num, page in enumerate(pdf.pages[:2], 1):  # Just first 2 pages
        print(f"\n{'='*80}")
        print(f"PAGE {page_num}")
        print(f"{'='*80}")
        
        # Extract tables
        tables = page.extract_tables()
        
        if not tables:
            print("❌ No tables found on this page")
            print("\nRaw text extraction:")
            print(page.extract_text()[:1000])
            continue
        
        for table_num, table in enumerate(tables, 1):
            print(f"\n--- Table {table_num} ---")
            print(f"Rows: {len(table)}")
            print(f"Columns: {len(table[0]) if table else 0}")
            
            # Show first 5 rows
            print("\nFirst 5 rows:")
            for row_num, row in enumerate(table[:5], 1):
                print(f"\nRow {row_num}:")
                for col_num, cell in enumerate(row, 1):
                    cell_text = str(cell)[:50] if cell else "[EMPTY]"
                    print(f"  Col {col_num}: {cell_text}")
            
            if len(table) > 5:
                print(f"\n... and {len(table) - 5} more rows")

