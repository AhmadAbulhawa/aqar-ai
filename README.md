# Aqar AI — IKEA room template + exact product auto-import

This update adds automatic import of supported IKEA products referenced by each room template.

## Files

Replace:

- `scripts/import_ikea_living_room_templates.py`

Add:

- `app/services/ikea_product_import_service.py`

No database schema change is required for this update.

## Install dependencies in the project venv

```cmd
D:\real-estate-ai\.venv\Scripts\python.exe -m pip install playwright pillow pillow-avif-plugin
D:\real-estate-ai\.venv\Scripts\python.exe -m playwright install chromium
```

## First test: two room templates

```cmd
D:\real-estate-ai\.venv\Scripts\python.exe scripts\import_ikea_living_room_templates.py --limit 2
```

By default, missing source products are automatically processed:

1. Existing furniture row -> reused.
2. Missing supported product -> IKEA product page is scraped.
3. Exact name, price, dimensions, color, material, image URL and product URL are extracted when available.
4. Product image is downloaded locally to `uploads/furniture/` as PNG.
5. New row is inserted into `furniture`.
6. The product is linked to the room template.
7. Unsupported categories or incomplete products are skipped and reported. Fake dimensions are never inserted.

## Optional flags

Skip product auto-import and only match existing DB products:

```cmd
D:\real-estate-ai\.venv\Scripts\python.exe scripts\import_ikea_living_room_templates.py --limit 2 --skip-product-import
```

Show Chromium while running:

```cmd
D:\real-estate-ai\.venv\Scripts\python.exe scripts\import_ikea_living_room_templates.py --limit 2 --headed
```

Skip template AI visual analysis but still import products:

```cmd
D:\real-estate-ai\.venv\Scripts\python.exe scripts\import_ikea_living_room_templates.py --limit 2 --skip-ai
```

## Supported living-room MVP categories

The importer currently auto-adds products that map confidently to your existing categories, including:

- sofa
- armchair
- coffee_table
- side_table
- tv_stand
- sideboard
- bookshelf
- storage_cabinet
- wall_shelf
- rug
- floor_lamp
- mirror
- wall_art

Other products such as cushions, duvet covers, pouffes, storage boxes, candles and unsupported accessories are skipped and listed in the JSON report.
