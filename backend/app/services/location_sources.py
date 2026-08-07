"""Location + industry Wikipedia sources for safe local scraping."""

from __future__ import annotations

LOCATION_ALIASES: dict[str, str] = {
    "bangalore": "bangalore",
    "bengaluru": "bangalore",
    "bangaluru": "bangalore",
    "delhi": "delhi",
    "new delhi": "delhi",
    "ncr": "delhi",
    "mumbai": "mumbai",
    "bombay": "mumbai",
    "hyderabad": "hyderabad",
    "chennai": "chennai",
    "madras": "chennai",
    "pune": "pune",
    "gurgaon": "gurgaon",
    "gurugram": "gurgaon",
    "noida": "noida",
    "kolkata": "kolkata",
    "calcutta": "kolkata",
    "ahmedabad": "ahmedabad",
    "india": "india",
}

LOCATION_WIKI_CATEGORIES: dict[str, list[str]] = {
    "bangalore": [
        "Category:Companies_based_in_Bengaluru",
        "Category:Information_technology_companies_of_Bengaluru",
        "Category:Biotechnology_companies_of_Bengaluru",
        "Category:Indian_companies_established_in_Bangalore",
        "Category:Multinational_companies_headquartered_in_Bengaluru",
        "Category:Companies_of_Karnataka",
        "Category:Software_companies_of_India",
        "Category:Startups_of_India",
        "Category:Indian_unicorns",
        "Category:Online_companies_of_India",
        "Category:Financial_services_companies_of_India",
        "Category:Outsourcing_companies_of_India",
        "Category:Electronics_companies_of_India",
        "Category:Manufacturing_companies_of_India",
    ],
    "delhi": [
        "Category:Companies_based_in_Delhi",
        "Category:Companies_based_in_New_Delhi",
        "Category:Software_companies_of_India",
        "Category:Startups_of_India",
    ],
    "mumbai": [
        "Category:Companies_based_in_Mumbai",
        "Category:Companies_of_Maharashtra",
        "Category:Software_companies_of_India",
    ],
    "hyderabad": [
        "Category:Companies_based_in_Hyderabad",
        "Category:Companies_of_Telangana",
        "Category:Software_companies_of_India",
    ],
    "chennai": [
        "Category:Companies_based_in_Chennai",
        "Category:Companies_of_Tamil_Nadu",
        "Category:Software_companies_of_India",
    ],
    "pune": [
        "Category:Companies_based_in_Pune",
        "Category:Companies_of_Maharashtra",
        "Category:Software_companies_of_India",
    ],
    "gurgaon": [
        "Category:Companies_based_in_Gurgaon",
        "Category:Software_companies_of_India",
    ],
    "noida": [
        "Category:Companies_based_in_Noida",
        "Category:Software_companies_of_India",
    ],
    "kolkata": [
        "Category:Companies_based_in_Kolkata",
        "Category:Software_companies_of_India",
    ],
    "ahmedabad": [
        "Category:Companies_based_in_Ahmedabad",
        "Category:Software_companies_of_India",
    ],
    "india": [
        "Category:Software_companies_of_India",
        "Category:Companies_of_India",
        "Category:Startups_of_India",
    ],
}

# Top ~20 industry mappings
INDUSTRY_WIKI_CATEGORIES: dict[str, list[str]] = {
    "corporate": [
        "Category:Conglomerate_companies_of_India",
        "Category:Companies_listed_on_the_Bombay_Stock_Exchange",
    ],
    "software": [
        "Category:Software_companies_of_India",
        "Category:Outsourcing_companies_of_India",
    ],
    "it": [
        "Category:Software_companies_of_India",
        "Category:Outsourcing_companies_of_India",
    ],
    "startup": [
        "Category:Startups_of_India",
        "Category:Indian_unicorns",
    ],
    "fintech": ["Category:Financial_services_companies_of_India"],
    "e-commerce": ["Category:Online_companies_of_India"],
    "ecommerce": ["Category:Online_companies_of_India"],
    "healthcare": ["Category:Health_care_companies_of_India"],
    "healthtech": ["Category:Health_care_companies_of_India"],
    "edtech": ["Category:Education_companies_of_India"],
    "education": ["Category:Education_companies_of_India"],
    "real estate": ["Category:Real_estate_companies_of_India"],
    "manufacturing": ["Category:Manufacturing_companies_of_India"],
    "saas": [
        "Category:Software_companies_of_India",
        "Category:Cloud_computing_providers",
    ],
    "logistics": ["Category:Logistics_companies_of_India"],
    "supply chain": ["Category:Logistics_companies_of_India"],
    "bfsi": ["Category:Banks_of_India", "Category:Financial_services_companies_of_India"],
    "banking": ["Category:Banks_of_India"],
    "telecom": ["Category:Telecommunications_companies_of_India"],
    "media": ["Category:Mass_media_companies_of_India"],
    "entertainment": ["Category:Entertainment_companies_of_India"],
    "automobile": ["Category:Motor_vehicle_manufacturers_of_India"],
    "auto": ["Category:Motor_vehicle_manufacturers_of_India"],
    "retail": ["Category:Retail_companies_of_India"],
    "consulting": ["Category:Consulting_firms_of_India"],
    "foodtech": ["Category:Food_companies_of_India"],
    "hospitality": ["Category:Hospitality_companies_of_India"],
    "energy": ["Category:Energy_companies_of_India"],
    "cleantech": ["Category:Energy_companies_of_India"],
    "pharma": ["Category:Pharmaceutical_companies_of_India"],
    "biotech": ["Category:Biotechnology_companies_of_India"],
}


def resolve_location_key(location: str) -> str:
    return LOCATION_ALIASES.get(location.lower().strip().split(",")[0], "india")


def get_wiki_categories(location: str, industry: str) -> list[str]:
    loc_key = resolve_location_key(location)
    cats = list(LOCATION_WIKI_CATEGORIES.get(loc_key, LOCATION_WIKI_CATEGORIES["india"]))

    ind_key = industry.lower().strip()
    for key, extra in INDUSTRY_WIKI_CATEGORIES.items():
        if key in ind_key:
            cats.extend(extra)

    return list(dict.fromkeys(cats))


def get_safe_bing_queries(location: str, industry: str, role: str) -> list[str]:
    """SME-focused free discovery — names/domains come from search results, not Wikipedia."""
    loc = location.split(",")[0].strip()
    ind = industry.strip()
    return [
        f'"{loc}" {ind} companies list',
        f'"{loc}" {ind} startup companies',
        f'"{loc}" {ind} company contact us',
        f'site:.in "{loc}" {ind} "contact us"',
        f'site:.com "{loc}" {ind} "about us" email',
        f'"{loc}" {ind} {role} email OR contact',
        f'"founded in {loc}" {ind} company',
        f'"{loc}" {ind} SME OR "private limited" website',
        f'"{loc}" {ind} companies directory',
        f'"{loc}" {ind} "get in touch" OR "reach us"',
        f'"{loc}" {role} "{ind}" company website contact',
        f'site:.in "{loc}" {ind} {role} "@"',
    ]
