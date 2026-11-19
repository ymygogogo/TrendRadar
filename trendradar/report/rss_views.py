"""Shared RSS selection for the report and public news pages."""


def rss_groups(current, new, mode):
    def identity(item):
        return item.get("url") or (item.get("source_name"), item.get("title"))

    def unique(groups, only_new=False):
        seen, output = set(), []
        for group in groups:
            items = []
            for item in group.get("titles", []):
                key = identity(item)
                marked = key in newly_seen or item.get("is_new", False)
                if key in seen or (only_new and not marked):
                    continue
                seen.add(key)
                items.append({**item, "is_new": marked})
            if items:
                output.append({**group, "count": len(items), "titles": items})
        return output

    current, new = current or [], new or []
    newly_seen = {identity(item) for group in new for item in group.get("titles", [])}
    if mode == "incremental":
        return unique(current + new), []
    all_groups = unique(current + new)
    return unique(new + current, only_new=True), all_groups
