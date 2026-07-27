from app.enums import (
    FurnitureCategory,
    RoomType
)


ROOM_TEMPLATES = {
    RoomType.LIVING_ROOM.value: {
        "required": [
            FurnitureCategory.SOFA.value,
            FurnitureCategory.COFFEE_TABLE.value
        ],
        "one_of": [
            [
                FurnitureCategory.TV_STAND.value,
                FurnitureCategory.SIDEBOARD.value
            ]
        ],
        "optional": [
            FurnitureCategory.TELEVISION.value,
            FurnitureCategory.ARMCHAIR.value,
            FurnitureCategory.SIDE_TABLE.value,
            FurnitureCategory.RUG.value,
            FurnitureCategory.FLOOR_LAMP.value,
            FurnitureCategory.WALL_ART.value
        ],
        "max_unique_products": 15
    },

    RoomType.BEDROOM.value: {
        "required": [
            FurnitureCategory.BED.value,
            FurnitureCategory.WARDROBE.value
        ],
        "one_of": [],
        "optional": [
            FurnitureCategory.NIGHTSTAND.value,
            FurnitureCategory.DRESSER.value,
            FurnitureCategory.MIRROR.value,
            FurnitureCategory.RUG.value,
            FurnitureCategory.WALL_ART.value
        ],
        "max_unique_products": 15
    },

    RoomType.OFFICE.value: {
        "required": [
            FurnitureCategory.DESK.value,
            FurnitureCategory.OFFICE_CHAIR.value
        ],
        "one_of": [],
        "optional": [
            FurnitureCategory.BOOKSHELF.value,
            FurnitureCategory.STORAGE_CABINET.value,
            FurnitureCategory.WALL_SHELF.value,
            FurnitureCategory.WALL_ART.value
        ],
        "max_unique_products": 15
    },

    RoomType.DINING_ROOM.value: {
        "required": [
            FurnitureCategory.DINING_TABLE.value,
            FurnitureCategory.DINING_CHAIR.value
        ],
        "one_of": [],
        "optional": [
            FurnitureCategory.SIDEBOARD.value,
            FurnitureCategory.RUG.value,
            FurnitureCategory.WALL_ART.value
        ],
        "max_unique_products": 15
    },

    RoomType.BATHROOM.value: {
        "required": [
            FurnitureCategory.TOILET.value,
            FurnitureCategory.VANITY_SINK.value,
            FurnitureCategory.MIRROR.value
        ],
        "one_of": [
            [
                FurnitureCategory.SHOWER_BOX.value,
                FurnitureCategory.BATHTUB.value
            ]
        ],
        "optional": [
            FurnitureCategory.MEDICINE_CABINET.value,
            FurnitureCategory.BATH_RUG.value,
            FurnitureCategory.TOWEL_RACK.value,
            FurnitureCategory.STORAGE_CABINET.value,
            FurnitureCategory.WALL_SHELF.value
        ],
        "max_unique_products": 15
    },

    RoomType.KITCHEN.value: {
        "required": [
            FurnitureCategory.KITCHEN_SINK.value,
            FurnitureCategory.FAUCET.value,
            FurnitureCategory.REFRIGERATOR.value,
            FurnitureCategory.BASE_CABINET.value,
            FurnitureCategory.WALL_CABINET.value
        ],
        "one_of": [
            [
                FurnitureCategory.COOKTOP.value,
                FurnitureCategory.FREESTANDING_COOKER.value
            ]
        ],
        "optional": [
            FurnitureCategory.KITCHEN_ISLAND.value,
            FurnitureCategory.OVEN.value,
            FurnitureCategory.RANGE_HOOD.value,
            FurnitureCategory.DISHWASHER.value,
            FurnitureCategory.MICROWAVE.value,
            FurnitureCategory.WALL_ART.value
        ],
        "max_unique_products": 15
    }
}
