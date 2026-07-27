import json
import os

from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field

from app.services.room_analysis_service import (
    encode_image_as_data_url
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(
    PROJECT_ROOT / ".env"
)


TEMPLATE_ANALYSIS_MODEL = "gpt-5.6-sol"


# =========================================================
# Structured template-analysis schema
# =========================================================


class TemplateVisualAnalysis(BaseModel):
    size_category: Literal[
        "small",
        "medium",
        "large"
    ]

    primary_style: Literal[
        "modern",
        "classic",
        "minimal",
        "luxury"
    ]

    layout_type: Literal[
        "l_shape_conversation",
        "facing_sofas_conversation",
        "u_shape_conversation",
        "media_wall_focused",
        "single_sofa_minimal",
        "floating_zone",
        "functional_zoning"
    ]

    wall_color_family: Literal[
        "white",
        "off_white",
        "beige",
        "gray",
        "greige",
        "brown",
        "black",
        "blue",
        "green",
        "mixed",
        "other"
    ]

    floor_color_family: Literal[
        "white",
        "off_white",
        "beige",
        "gray",
        "greige",
        "brown",
        "black",
        "blue",
        "green",
        "natural",
        "mixed",
        "other"
    ]

    floor_material: Literal[
        "wood",
        "laminate",
        "tile",
        "stone",
        "carpet",
        "concrete",
        "other",
        "unknown"
    ]

    natural_light_level: Literal[
        "low",
        "medium",
        "high"
    ]

    room_openness: Literal[
        "closed",
        "semi_open",
        "open_plan"
    ]

    density_target: Literal[
        "minimal",
        "balanced",
        "layered"
    ]

    focal_point: Literal[
        "tv_wall",
        "fireplace",
        "window_view",
        "art_wall",
        "none"
    ]

    tags: list[str] = Field(
        default_factory=list,
        max_length=20
    )


# =========================================================
# OpenAI client
# =========================================================



def get_openai_client() -> OpenAI:
    api_key = os.getenv(
        "OPENAI_API_KEY"
    )

    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY was not found. "
            "Make sure it exists in the project .env file."
        )

    return OpenAI(
        api_key=api_key
    )


# =========================================================
# Main analysis
# =========================================================



def analyze_design_template_image(
    image_path: Path,
    title: str,
    description: str | None = None
) -> TemplateVisualAnalysis:
    if not image_path.exists():
        raise FileNotFoundError(
            f"Template image not found: {image_path}"
        )

    client = get_openai_client()

    image_data_url = (
        encode_image_as_data_url(
            image_path
        )
    )

    prompt = f"""
Analyze this READY LIVING-ROOM DESIGN IMAGE for Aqar AI template matching.

Source title:
{title}

Source description:
{description or "No source description was provided."}

Your task is to extract stable visual metadata that can later be compared
against a real user's uploaded empty room.

IMPORTANT:

- Analyze the ROOM and DESIGN only.
- Do not identify or guess exact furniture product SKUs.
- Do not invent exact room measurements.
- Ignore people, books, bowls, cushions, plants, and tiny decorative props
  unless they are useful as broad visual tags.
- Return one primary style from the allowed schema only.
- Use the most dominant visible wall and floor families.
- The tags should be short normalized lowercase descriptors useful for
  matching and search.
- Good tag examples:
  beige, natural, scandinavian, japandi, warm, neutral, family, compact,
  colorful, wood, cozy, airy, kids, guest_friendly.
- Do not repeat schema fields unnecessarily in tags.

Choose layout_type by the actual visible furniture composition:

- l_shape_conversation
- facing_sofas_conversation
- u_shape_conversation
- media_wall_focused
- single_sofa_minimal
- floating_zone
- functional_zoning

Choose focal_point from:

- tv_wall
- fireplace
- window_view
- art_wall
- none

This metadata will be cached permanently for the imported template.
""".strip()

    response = client.responses.parse(
        model=TEMPLATE_ANALYSIS_MODEL,
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompt
                    },
                    {
                        "type": "input_image",
                        "image_url": image_data_url
                    }
                ]
            }
        ],
        text_format=TemplateVisualAnalysis
    )

    analysis = response.output_parsed

    if analysis is None:
        raise ValueError(
            "Template analysis model returned no parsed result."
        )

    # Normalize tags one final time so imported rows stay stable.
    normalized_tags = []
    seen = set()

    for raw_tag in analysis.tags:
        tag = "_".join(
            str(raw_tag)
            .strip()
            .lower()
            .split()
        )

        if not tag or tag in seen:
            continue

        seen.add(tag)
        normalized_tags.append(tag)

    return analysis.model_copy(
        update={
            "tags": normalized_tags[:20]
        }
    )



def analysis_to_pretty_json(
    analysis: TemplateVisualAnalysis
) -> str:
    return json.dumps(
        analysis.model_dump(),
        ensure_ascii=False,
        indent=2
    )
