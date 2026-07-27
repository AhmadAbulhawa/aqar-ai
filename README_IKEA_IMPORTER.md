# Aqar AI — IKEA Living Room Template Bulk Importer

This package adds a bulk importer for:

`https://www.ikea.com/jo/en/rooms/living-room/`

## Files

Replace:

- `app/database.py`
- `app/models.py`
- `app/services/design_template_service.py`

Add:

- `app/services/template_analysis_service.py`
- `scripts/import_ikea_living_room_templates.py`

## Install Playwright

From the project virtual environment:

```bash
pip install playwright
python -m playwright install chromium
```

The project already needs the OpenAI SDK, Pydantic, python-dotenv, and FastAPI.

## First safe test

```bash
python scripts/import_ikea_living_room_templates.py --limit 2
```

This will:

1. Open the IKEA Jordan living-room section.
2. Click dynamic `Load more` buttons.
3. Discover gallery pages.
4. Extract title, description, hero image, and products from the `Recreate the room` section.
5. Download the hero image into `uploads/design_templates/`.
6. Analyze the template image once using `gpt-5.6-sol`.
7. Match IKEA product URLs against your current `furniture` table.
8. Save matched exact products in `design_template_products`.
9. Save every discovered source product in `design_template_source_products`.
10. Write a JSON report under `imports/reports/` with missing products.

## Import all discovered living-room templates

```bash
python scripts/import_ikea_living_room_templates.py
```

## Useful options

Skip AI analysis:

```bash
python scripts/import_ikea_living_room_templates.py --skip-ai
```

Force AI re-analysis of already imported templates:

```bash
python scripts/import_ikea_living_room_templates.py --reanalyze
```

Show the browser while importing:

```bash
python scripts/import_ikea_living_room_templates.py --headed
```

## Idempotency

The importer uses `design_templates.source_url` as the stable import key.
Running the script again updates the same imported templates instead of creating duplicates.

## Matching endpoint

Your existing endpoint continues to work:

```text
POST /designs/templates/match/{room_request_id}
```

The improved score is out of 100 and considers:

- room type
- room size
- requested style
- wall color family
- floor color family
- floor material
- natural light level
- room openness
- preferred-color overlap with template tags
