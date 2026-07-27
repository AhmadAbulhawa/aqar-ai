from enum import Enum


class FurnitureCategory(str, Enum):
    # Living room
    SOFA = "sofa"
    ARMCHAIR = "armchair"
    COFFEE_TABLE = "coffee_table"
    SIDE_TABLE = "side_table"
    TV_STAND = "tv_stand"
    TELEVISION = "television"
    SIDEBOARD = "sideboard"
    RUG = "rug"
    FLOOR_LAMP = "floor_lamp"
    TABLE_LAMP = "table_lamp"
    WALL_LAMP = "wall_lamp"
    CEILING_LIGHT = "ceiling_light"
    CHANDELIER = "chandelier"
    PENDANT_LIGHT = "pendant_light"
    CEILING_SPOTLIGHT = "ceiling_spotlight"

    # Dining room
    DINING_CHAIR = "dining_chair"
    DINING_TABLE = "dining_table"

    # Office
    OFFICE_CHAIR = "office_chair"
    DESK = "desk"
    BOOKSHELF = "bookshelf"

    # Bedroom
    BED = "bed"
    WARDROBE = "wardrobe"
    NIGHTSTAND = "nightstand"
    DRESSER = "dresser"

    # Shared / general
    MIRROR = "mirror"
    WALL_SHELF = "wall_shelf"
    STORAGE_CABINET = "storage_cabinet"
    WALL_ART = "wall_art"

    # Bathroom
    TOILET = "toilet"
    VANITY_SINK = "vanity_sink"
    SHOWER_BOX = "shower_box"
    BATHTUB = "bathtub"
    MEDICINE_CABINET = "medicine_cabinet"
    BATH_RUG = "bath_rug"
    TOWEL_RACK = "towel_rack"

    # Kitchen
    KITCHEN_SINK = "kitchen_sink"
    FAUCET = "faucet"
    BASE_CABINET = "base_cabinet"
    WALL_CABINET = "wall_cabinet"
    KITCHEN_ISLAND = "kitchen_island"
    REFRIGERATOR = "refrigerator"
    OVEN = "oven"
    COOKTOP = "cooktop"
    FREESTANDING_COOKER = "freestanding_cooker"
    RANGE_HOOD = "range_hood"
    DISHWASHER = "dishwasher"
    MICROWAVE = "microwave"
    WASHING_MACHINE = "washing_machine"
    DRYER = "dryer"

    # Products intentionally retained for Shop browsing when no safe,
    # renderer-aware primary category exists yet. The real source subtype is
    # preserved separately in subcategory/tags rather than being fabricated.
    SHOP_ONLY = "shop_only"


class FurnitureStyle(str, Enum):
    MODERN = "modern"
    CLASSIC = "classic"
    MINIMAL = "minimal"
    LUXURY = "luxury"


class RoomType(str, Enum):
    LIVING_ROOM = "living_room"
    BEDROOM = "bedroom"
    OFFICE = "office"
    DINING_ROOM = "dining_room"
    BATHROOM = "bathroom"
    KITCHEN = "kitchen"


class RequirementMode(str, Enum):
    AUTO = "auto"
    REQUIRED = "required"
    EXCLUDED = "excluded"


class DesignMode(str, Enum):
    AUTO = "auto"
    CUSTOM = "custom"
