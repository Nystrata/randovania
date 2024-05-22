import argparse
import configparser
import csv
import json
import typing
from pathlib import Path

from randovania.games.factorio.data_importer import data_parser
from randovania.lib import json_lib

locale = configparser.ConfigParser()


def template_req(name: str) -> dict:
    return {
        "type": "template",
        "data": name,
    }


def tech_req(tech_name: str) -> dict:
    return {
        "type": "resource",
        "data": {
            "type": "items",
            "name": tech_name,
            "amount": 1,
            "negate": False,
        },
    }


def and_req(entries: list, comment: str | None = None) -> dict:
    return {"type": "and", "data": {"comment": comment, "items": entries}}


def or_req(entries: list, comment: str | None = None) -> dict:
    return {"type": "or", "data": {"comment": comment, "items": entries}}


def get_localized_name(n: str) -> str:
    for k in ["item-name", "entity-name", "fluid-name", "equipment-name", "recipe-name", "technology-name"]:
        if n in locale[k]:
            return locale[k][n]
        if f"{n}-1" in locale[k]:
            return locale[k][f"{n}-1"]

    if n.startswith("fill-"):
        return f"Fill {locale['fluid-name'][n[5:-7]]} barrel"

    if n.endswith("-barrel"):
        return f"{locale['fluid-name'][n[:-7]]} barrel"

    hardcoded_names = {
        "solid-fuel-from-heavy-oil": "Solid Fuel (Heavy Oil)",
        "solid-fuel-from-light-oil": "Solid Fuel (Light Oil)",
        "solid-fuel-from-petroleum-gas": "Solid Fuel (Petroleum Gas)",
    }

    try:
        return hardcoded_names[n]
    except KeyError:
        i = n.rfind("-")
        if i != -1:
            front, number = n[:i], n[i + 1 :]
            if number.isdigit():
                return f"{get_localized_name(front)} {number}"
        raise


_k_items_for_crafting_category = {
    "crafting": [],
    "advanced-crafting": ["assembling-machine-1", "assembling-machine-2", "assembling-machine-3"],
    "smelting": ["stone-furnace", "steel-furnace", "electric-furnace"],
    "chemistry": ["chemical-plant"],
    "crafting-with-fluid": ["assembling-machine-2", "assembling-machine-3"],
    "oil-processing": ["oil-refinery"],
    "rocket-building": ["rocket-silo"],
    "centrifuging": ["centrifuge"],
}

_k_burner_entities = [
    "stone-furnace",
    "steel-furnace",
    "boiler",
    "burner-mining-drill",
]
_k_electric_entities = [
    "assembling-machine-1",
    "assembling-machine-2",
    "assembling-machine-3",
    "electric-furnace",
    "chemical-plant",
    "oil-refinery",
    "rocket-silo",
    "centrifuge",
    "electric-mining-drill",
    "pumpjack",
]

_k_fuel_production = template_req("craft-coal")

# _k_basic_mining = or_req(
#     [
#         template_req("use-burner-mining-drill"),
#         template_req("use-electric-mining-drill"),
#     ]
# )
_k_basic_mining = and_req([])

_k_miner_for_resource = {
    "raw-fish": and_req([]),
    "wood": and_req([]),
    "coal": and_req([]),  # coal is needed for power, so let's keep it simple to avoid loops
    # Rn, can always mine
    "iron-ore": _k_basic_mining,
    "copper-ore": _k_basic_mining,
    "stone": _k_basic_mining,
    "water": template_req("has-offshore-pump"),
    "steam": or_req(
        [
            and_req(
                [
                    template_req("has-boiler"),
                    _k_fuel_production,
                ],
                comment="Boiler powered Steam Engines",
            ),
            # Causes loops with coal liquefaction
            # and_req(
            #     [
            #         item_req("nuclear-reactor"),
            #         template_req("craft-uranium-fuel-cell"),
            #     ],
            #     comment="Nuclear Power",
            # ),
        ]
    ),
    "uranium-ore": and_req(
        [
            template_req("use-electric-mining-drill"),
            template_req("craft-sulfuric-acid"),
        ]
    ),
    "crude-oil": template_req("use-pumpjack"),
}

_k_tier_requirements = [
    [],  # 1
    [],
    [template_req("craft-transport-belt")],
    [
        template_req("craft-assembling-machine-1"),
        template_req("craft-inserter"),
        template_req("has-electricity"),
    ],  # TODO: electric lab, drills
    [
        template_req("craft-fast-transport-belt"),
        template_req("craft-fast-inserter"),
        tech_req("railway"),
    ],  # TODO: fast smelting
    [tech_req("construction-robotics"), template_req("craft-assembling-machine-2")],
    [template_req("craft-stack-inserter")],
    [tech_req("research-speed-6"), template_req("craft-assembling-machine-3")],
    [tech_req("logistic-system")],
    [tech_req("productivity-module-3")],
]


def requirement_for_recipe(recipes_raw: dict, recipe: str, unlocking_techs: list[str]) -> dict:
    entries = []

    if len(unlocking_techs) > 2:
        entries.append(or_req([tech_req(tech) for tech in unlocking_techs]))
    elif unlocking_techs:
        entries.append(tech_req(unlocking_techs[0]))

    category = recipes_raw[recipe].get("category", "crafting")

    if category != "crafting":  # hand-craft compatible, so assume always possible
        entries.append(template_req(f"perform-{category}"))

    for ingredient in recipes_raw[recipe]["ingredients"]:
        if isinstance(ingredient, dict):
            ing_name = ingredient["name"]
        else:
            ing_name = ingredient[0]

        if recipe == "kovarex-enrichment-process" and ing_name == "uranium-235":
            continue

        entries.append(template_req(f"craft-{ing_name}"))

    return and_req(entries)


def create_resources(header: dict, techs_for_recipe: dict) -> None:
    header["resource_database"]["items"] = {}

    for tech_name, recipes_unlocked in techs_for_recipe.items():
        header["resource_database"]["items"][tech_name] = {
            "long_name": get_localized_name(tech_name),
            "max_capacity": 1,
            "extra": {"recipes_unlocked": recipes_unlocked},
        }


def update_templates(header: dict, recipes_raw: dict, techs_for_recipe: dict[str, list[str]]):
    header["resource_database"]["requirement_template"]["has-electricity"] = {
        "display_name": "Has Electricity",
        "requirement": or_req(
            [
                and_req(
                    [
                        tech_req("steam-power"),
                        _k_fuel_production,
                    ],
                    comment="Boiler powered Steam Engines",
                ),
                and_req(
                    [tech_req("solar-energy"), tech_req("electric-energy-accumulators")],
                    comment="Solar with battery for night",
                ),  # TODO: maybe craft?
                # TODO: figure out settings later
                # and_req([item_req("solar-energy"), setting("solar-without-accumulator")]),
                # Nuclear requires electricity to work
                # and_req(
                #     [
                #         item_req("nuclear-power"),
                #         template_req("craft-uranium-fuel-cell"),
                #     ],
                #     comment="Nuclear Power",
                # ),
            ]
        ),
    }

    for tier_level, tier_req in enumerate(_k_tier_requirements):
        header["resource_database"]["requirement_template"][f"tech-tier-{tier_level + 1}"] = {
            "display_name": f"Tech Tier {tier_level + 1}",
            "requirement": and_req(tier_req),
        }

    for entity in _k_burner_entities:
        header["resource_database"]["requirement_template"][f"use-{entity}"] = {
            "display_name": f"Use {entity}",
            "requirement": and_req(
                [template_req(f"has-{entity}"), _k_fuel_production],
                comment="Fuel is considered always available.",
            ),
        }

    for entity in _k_electric_entities:
        header["resource_database"]["requirement_template"][f"use-{entity}"] = {
            "display_name": f"Use {entity}",
            "requirement": and_req(
                [
                    template_req(f"has-{entity}"),
                    template_req("has-electricity"),
                ]
            ),
        }

    # Machines needed for the non-trivial crafting recipes
    for category, items in _k_items_for_crafting_category.items():
        header["resource_database"]["requirement_template"][f"perform-{category}"] = {
            "display_name": f"Perform {category}",
            "requirement": or_req([template_req(f"use-{it}") for it in items]) if items else and_req([]),
        }

    # Add the templates for crafting all recipes
    for item_name, recipes in data_parser.get_recipes_for(recipes_raw).items():
        localized_name = get_localized_name(item_name)

        techs = set()
        for recipe in recipes:
            techs.update(techs_for_recipe.get(recipe, []))

        header["resource_database"]["requirement_template"][f"has-{item_name}"] = {
            "display_name": f"Unlocked {localized_name}",
            "requirement": or_req([tech_req(tech) for tech in sorted(techs)]) if techs else and_req([]),
        }
        header["resource_database"]["requirement_template"][f"craft-{item_name}"] = {
            "display_name": f"Craft {localized_name}",
            "requirement": or_req(
                [
                    requirement_for_recipe(recipes_raw, recipe, techs_for_recipe.get(recipe, []))
                    for recipe in sorted(recipes)
                ]
            ),
        }

    # Mining all resources
    for resource_name, requirement in _k_miner_for_resource.items():
        localized_name = get_localized_name(resource_name)
        header["resource_database"]["requirement_template"][f"craft-{resource_name}"] = {
            "display_name": f"Mine {localized_name}",
            "requirement": requirement,
        }


def read_tech_csv(csv_path: Path) -> dict:
    result = {}

    with csv_path.open() as f:
        f.readline()
        r = csv.reader(f)
        for line in r:
            if line[1] == "<>":
                continue

            tech_name, pickup_name, progressive_tier, category = line[:4]
            # print(tech_name, pickup_name, progressive_tier, category)
            result[tech_name] = {
                "pickup_name": pickup_name,
                "progressive_tier": progressive_tier,
                "category": category,
            }

    return result


def create_pickups(techs_raw: dict, tech_csv: dict) -> dict:
    result = {}

    for tech_name, data in tech_csv.items():
        pickup_name = data["pickup_name"]
        if pickup_name in result:
            result[pickup_name]["progression"].append(tech_name)
        else:
            tech = techs_raw[tech_name]
            if "icons" in tech:
                icon = tech["icons"][0]["icon"]
            else:
                icon = tech["icon"]
            result[pickup_name] = {
                "pickup_category": data["category"],
                "broad_category": data["category"],
                "model_name": icon,
                "offworld_models": {},
                "progression": [tech_name],
                "preferred_location_category": "major" if data["category"] != "enhancement" else "minor",
                "expected_case_for_describer": "shuffled",
            }

    result["Rocket Silo"]["expected_case_for_describer"] = "vanilla"
    result["Rocket Silo"]["original_location"] = 136
    result["Rocket Silo"]["hide_from_gui"] = True

    return result


def remove_unwanted_tech(tech_raw: dict[str, dict], tech_csv) -> dict:
    def filter_tech(name: str) -> bool:
        if name not in tech_csv:
            return False

        return not name.startswith("randovania-")

    return {key: value for key, value in tech_raw.items() if filter_tech(key)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--factorio", type=Path, help="Path to the Factorio root folder.", required=True)
    parser.add_argument("--tech-csv", type=Path, help="Path to the CSV with tech definitions.", required=True)
    args = parser.parse_args()

    factorio_path: Path = args.factorio
    csv_path: Path = args.tech_csv

    rdv_factorio_path = Path(__file__).parents[2].joinpath("randovania/games/factorio")
    pickup_db_path = rdv_factorio_path.joinpath("pickup_database/pickup-database.json")
    header_path = rdv_factorio_path.joinpath("logic_database/header.json")

    raw_dump_path = factorio_path.joinpath("script-output/data-raw-dump.json")

    locale.read(
        [
            factorio_path.joinpath("data/base/locale/en/base.cfg"),
            factorio_path.joinpath("mods/randovania-layout/locale/en/strings.cfg"),
        ]
    )

    tech_csv = read_tech_csv(csv_path)

    with raw_dump_path.open() as f:
        raw_dump: dict[str, dict[str, typing.Any]] = json.load(f)

    recipes_raw = raw_dump["recipe"]
    data_parser.remove_expensive(recipes_raw)

    techs_raw = remove_unwanted_tech(raw_dump["technology"], tech_csv)

    json_lib.write_path(rdv_factorio_path.joinpath("assets", "recipes-raw.json"), recipes_raw)
    json_lib.write_path(rdv_factorio_path.joinpath("assets", "techs-raw.json"), techs_raw)

    with header_path.open() as f:
        header = json.load(f)

    with pickup_db_path.open() as f:
        pickup_db = json.load(f)

    create_resources(header, data_parser.get_recipes_unlock_by_tech(techs_raw))
    update_templates(header, recipes_raw, data_parser.get_techs_for_recipe(techs_raw))

    pickup_db["standard_pickups"] = create_pickups(techs_raw, tech_csv)

    header["resource_database"]["requirement_template"] = dict(
        sorted(header["resource_database"]["requirement_template"].items(), key=lambda it: it[1]["display_name"])
    )

    with header_path.open("w") as f:
        json.dump(header, f, indent=4)

    with pickup_db_path.open("w") as f:
        json.dump(pickup_db, f, indent=4)


if __name__ == "__main__":
    main()
