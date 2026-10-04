from next.seo import RobotsRule


rules = [
    RobotsRule(disallow="/search/"),
    RobotsRule(user_agent=("GPTBot", "CCBot"), disallow="/"),
]
