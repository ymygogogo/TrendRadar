"""Opt-in title gate for market-focused collection."""

def blocked_titles(settings):
    config = settings.get("crawl_filter", {})
    ignored = ()
    if settings.get("enabled") and config.get("enabled"):
        ignored = tuple(term.strip().casefold() for term in config.get("ignore_title_terms", [])
                        if isinstance(term, str) and term.strip())

    def matches(title):
        return any(term in title.casefold() for term in ignored)

    return matches
